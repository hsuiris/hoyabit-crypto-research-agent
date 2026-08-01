"""正式執行（formal run）的生命週期管理。

負責三件事，且都是純模組（不呼叫 LLM、不碰網路）：

1. Run 狀態機：`CREATED -> RUNNING -> COMPLETED / COMPLETED_DEGRADED / FAILED`，
   非法轉換一律拋出 `InvalidRunTransitionError`。
2. 輸出防覆寫與 formal lock：同一 run_id 的既有輸出不可被第二次執行蓋掉；
   formal run 意外重跑會被拒絕，只有明確授權（`authorized_rerun=True` 且指定
   `rerun_of`）才能重跑，且會保留完整 lineage 歷史（第一次失敗紀錄不會被刪除）。
3. Deadline 決策：提供「還剩多少時間、是否該收尾、要不要跳過哪個階段」的純函式，
   不放寬 `src/orchestrator.py` 既有的 720 秒三階段預算（`DEFAULT_TIME_BUDGET_SECONDS`）。

本模組建立在 T0.5 的 `RunContext`（run_id、output_prefix、deadline）與
`LocalArtifactStore`（run 前綴隔離、manifest）之上，不重新發明一套平行的
run id 或 manifest 機制；所有檔案系統操作都透過 `LocalArtifactStore` 或建構子
明確傳入的 `root` 路徑，不硬編 `/tmp/outputs` 或 `runs/` 之類的字面路徑。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .artifact_store import LocalArtifactStore
from .orchestrator import DEFAULT_TIME_BUDGET_SECONDS
from .run_context import RunContext

# ---------------------------------------------------------------------------
# Run 狀態機
# ---------------------------------------------------------------------------

STATUS_CREATED = "CREATED"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_COMPLETED_DEGRADED = "COMPLETED_DEGRADED"
STATUS_FAILED = "FAILED"

VALID_STATUSES = (
    STATUS_CREATED,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_COMPLETED_DEGRADED,
    STATUS_FAILED,
)

# 終止狀態不允許再轉換；RUNNING 可以走向三種終止狀態之一。
_ALLOWED_TRANSITIONS = {
    STATUS_CREATED: {STATUS_RUNNING, STATUS_FAILED},
    STATUS_RUNNING: {STATUS_COMPLETED, STATUS_COMPLETED_DEGRADED, STATUS_FAILED},
    STATUS_COMPLETED: set(),
    STATUS_COMPLETED_DEGRADED: set(),
    STATUS_FAILED: set(),
}

# ---------------------------------------------------------------------------
# Run 模式
# ---------------------------------------------------------------------------

RUN_MODE_TEST = "test"
RUN_MODE_FORMAL = "formal"
VALID_RUN_MODES = (RUN_MODE_TEST, RUN_MODE_FORMAL)

# ---------------------------------------------------------------------------
# Checkpoint 階段（T7 規格：至少在這五個階段保存可恢復狀態）
# ---------------------------------------------------------------------------

CHECKPOINT_STAGES = (
    "plan",
    "collected_evidence",
    "scored_evidence",
    "claims",
    "final_artifacts",
)

# ---------------------------------------------------------------------------
# Deadline 常數
# ---------------------------------------------------------------------------

# 比賽的絕對上限；本模組的預設值必須更保守，不得把預設拉到這個上限。
HARD_DEADLINE_SECONDS_MAX = 900.0
FINALIZATION_DEADLINE_SECONDS_MAX = 840.0

# 預設沿用 repo 既有三階段預算（420 + 180 + 120 = 720 秒），只讀對照，不重新定義。
DEFAULT_HARD_DEADLINE_SECONDS = DEFAULT_TIME_BUDGET_SECONDS
# 收尾窗口與 hard deadline 維持跟比賽上限相同的比例（840 / 900），套用在既有預算上
# 得到 672 秒，仍嚴格低於比賽的 840 秒收尾門檻。
DEFAULT_FINALIZATION_DEADLINE_SECONDS = DEFAULT_HARD_DEADLINE_SECONDS * (
    FINALIZATION_DEADLINE_SECONDS_MAX / HARD_DEADLINE_SECONDS_MAX
)

if DEFAULT_HARD_DEADLINE_SECONDS > HARD_DEADLINE_SECONDS_MAX:  # pragma: no cover - 純防呆
    raise AssertionError("既有三階段預算已超過比賽的 900 秒硬上限，需先確認 orchestrator 設定")


# ---------------------------------------------------------------------------
# 例外
# ---------------------------------------------------------------------------


class RunManagerError(Exception):
    """本模組所有例外的基底類別。"""


class InvalidRunTransitionError(RunManagerError):
    """狀態轉換不合法（例如從終止狀態再轉換，或跳過 RUNNING 直接 COMPLETED）。"""


class FormalRunAlreadyExistsError(RunManagerError):
    """formal run 對同一 question_hash 已存在，且沒有明確授權重跑。"""


class RunDirectoryExistsError(RunManagerError):
    """同一 run_id 的輸出目錄已有內容，不可被第二次執行覆寫。"""


# ---------------------------------------------------------------------------
# 問題雜湊：formal lock 的 key
# ---------------------------------------------------------------------------


def compute_question_hash(question: str, coins) -> str:
    """把問題與幣種正規化後取 SHA-256，作為 formal lock 的 key。

    正規化順序不影響結果：幣種一律轉大寫並排序，問題只去除頭尾空白。
    """
    coin_list = [coins] if isinstance(coins, str) else list(coins)
    normalised_coins = sorted({coin.strip().upper() for coin in coin_list if coin and coin.strip()})
    payload = json.dumps(
        {"question": question.strip(), "coins": normalised_coins},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# RunRecord：一次執行的生命週期狀態
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    """一次 run 的可變生命週期狀態；不可變的識別與時間欄位仍由 `RunContext` 提供。"""

    context: RunContext
    mode: str
    question_hash: str
    status: str = STATUS_CREATED
    rerun_of: str | None = None
    rerun_reason: str | None = None
    authorized_rerun: bool = False
    checkpoints: dict = field(default_factory=dict)

    @property
    def run_id(self) -> str:
        return self.context.run_id

    def transition(self, new_status: str) -> None:
        """套用狀態轉換；非法轉換一律拋出 `InvalidRunTransitionError`，狀態維持不變。"""
        if new_status not in VALID_STATUSES:
            raise ValueError(f"unknown run status: {new_status!r}")
        allowed = _ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidRunTransitionError(
                f"run {self.run_id!r} cannot transition from {self.status!r} to {new_status!r} "
                f"(allowed targets: {sorted(allowed) or 'none, terminal state'})"
            )
        self.status = new_status

    def save_checkpoint(self, stage: str, data: object) -> None:
        """保存單一階段的可恢復狀態；不會清除其他階段已保存的 checkpoint。"""
        if stage not in CHECKPOINT_STAGES:
            raise ValueError(f"unknown checkpoint stage: {stage!r}, expected one of {CHECKPOINT_STAGES}")
        self.checkpoints[stage] = data

    def lock_payload(self) -> dict:
        """formal lock 歷史紀錄裡的單筆條目。"""
        return {
            "run_id": self.run_id,
            "question_hash": self.question_hash,
            "mode": self.mode,
            "status": self.status,
            "started_at": self.context.started_at,
            "rerun_of": self.rerun_of,
            "rerun_reason": self.rerun_reason,
            "authorized_rerun": self.authorized_rerun,
        }

    def as_dict(self) -> dict:
        payload = self.context.as_dict()
        payload.update({
            "mode": self.mode,
            "question_hash": self.question_hash,
            "status": self.status,
            "rerun_of": self.rerun_of,
            "rerun_reason": self.rerun_reason,
            "authorized_rerun": self.authorized_rerun,
            "checkpoints": sorted(self.checkpoints.keys()),
        })
        return payload


# ---------------------------------------------------------------------------
# Deadline 決策：純函式，相同輸入永遠得到相同輸出
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeadlineDecision:
    """某個時間點的 deadline 決策；只回傳結構化結果，不直接執行任何降級動作。"""

    elapsed_seconds: float
    remaining_seconds: float
    hard_deadline_seconds: float
    finalization_deadline_seconds: float
    should_finalize: bool
    should_skip_follow_up_collection: bool
    should_skip_critic: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "remaining_seconds": self.remaining_seconds,
            "hard_deadline_seconds": self.hard_deadline_seconds,
            "finalization_deadline_seconds": self.finalization_deadline_seconds,
            "should_finalize": self.should_finalize,
            "should_skip_follow_up_collection": self.should_skip_follow_up_collection,
            "should_skip_critic": self.should_skip_critic,
            "reason": self.reason,
        }


def evaluate_deadline(
    elapsed_seconds: float,
    hard_deadline_seconds: float = DEFAULT_HARD_DEADLINE_SECONDS,
    finalization_deadline_seconds: float | None = None,
) -> DeadlineDecision:
    """回答「還剩多少時間、該不該收尾、該跳過哪個階段」，不執行任何降級動作。

    - `hard_deadline_seconds` 預設沿用既有 720 秒三階段預算，最多不得超過比賽的 900 秒。
    - `finalization_deadline_seconds` 預設是 `hard_deadline_seconds` 依 840/900 等比例換算，
      最多不得超過比賽的 840 秒，且不可大於 `hard_deadline_seconds` 本身。
    - 相同輸入永遠得到相同輸出（deterministic），不讀取任何系統時間。
    """
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must be >= 0")
    if hard_deadline_seconds <= 0:
        raise ValueError("hard_deadline_seconds must be > 0")
    if hard_deadline_seconds > HARD_DEADLINE_SECONDS_MAX:
        raise ValueError(f"hard_deadline_seconds must be <= {HARD_DEADLINE_SECONDS_MAX} seconds")

    if finalization_deadline_seconds is None:
        finalization_deadline_seconds = hard_deadline_seconds * (
            FINALIZATION_DEADLINE_SECONDS_MAX / HARD_DEADLINE_SECONDS_MAX
        )
    if finalization_deadline_seconds <= 0:
        raise ValueError("finalization_deadline_seconds must be > 0")
    if finalization_deadline_seconds > FINALIZATION_DEADLINE_SECONDS_MAX:
        raise ValueError(
            f"finalization_deadline_seconds must be <= {FINALIZATION_DEADLINE_SECONDS_MAX} seconds"
        )
    if finalization_deadline_seconds > hard_deadline_seconds:
        raise ValueError("finalization_deadline_seconds must be <= hard_deadline_seconds")

    remaining = hard_deadline_seconds - elapsed_seconds
    should_finalize = elapsed_seconds >= finalization_deadline_seconds

    if remaining <= 0:
        reason = "hard_deadline_exceeded"
    elif should_finalize:
        reason = "finalization_window_reached"
    else:
        reason = "within_budget"

    return DeadlineDecision(
        elapsed_seconds=elapsed_seconds,
        remaining_seconds=max(remaining, 0.0),
        hard_deadline_seconds=hard_deadline_seconds,
        finalization_deadline_seconds=finalization_deadline_seconds,
        should_finalize=should_finalize,
        should_skip_follow_up_collection=should_finalize,
        should_skip_critic=should_finalize,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# RunManager：建立 run、防覆寫、formal lock、狀態轉換
# ---------------------------------------------------------------------------


class RunManager:
    """管理 run 的建立與生命週期；輸出仍經由 `LocalArtifactStore` 寫入 `root` 之下。

    `root` 是呼叫端明確傳入的路徑（例如測試用的 tempdir，或正式環境的產物根目錄），
    本類別不會自行決定或硬編任何絕對路徑。
    """

    #: formal lock 的存放位置：`root/_formal_locks/{question_hash}.json`。
    #: 這不是提交物（不是 report.md / evidence.json 等六項檔案），所以獨立於
    #: 各 run 的 `output_prefix` 之外，但仍完全在呼叫端提供的 `root` 之下。
    LOCK_SUBDIR = "_formal_locks"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # -- formal lock 存取 -----------------------------------------------

    def _lock_path(self, question_hash: str) -> Path:
        return self.root / self.LOCK_SUBDIR / f"{question_hash}.json"

    def _read_lock(self, question_hash: str) -> dict | None:
        path = self._lock_path(question_hash)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_lock(self, question_hash: str, payload: dict) -> None:
        path = self._lock_path(question_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _append_lock_history(self, question_hash: str, entry: dict) -> None:
        lock = self._read_lock(question_hash) or {"question_hash": question_hash, "history": []}
        lock["history"].append(entry)
        self._write_lock(question_hash, lock)

    def _update_lock_status(self, question_hash: str, run_id: str, status: str) -> None:
        lock = self._read_lock(question_hash)
        if not lock:
            return
        for entry in lock["history"]:
            if entry["run_id"] == run_id:
                entry["status"] = status
        self._write_lock(question_hash, lock)

    def formal_lock_history(self, question_hash: str) -> list[dict]:
        """回傳某個 question_hash 的完整 formal lock 歷史（含第一次失敗紀錄）。"""
        lock = self._read_lock(question_hash)
        return list(lock["history"]) if lock else []

    # -- run 建立 ----------------------------------------------------------

    def create_run(
        self,
        question: str,
        coins,
        mode: str = RUN_MODE_TEST,
        run_id: str | None = None,
        rerun_of: str | None = None,
        rerun_reason: str | None = None,
        authorized_rerun: bool = False,
        as_of=None,
    ) -> RunRecord:
        """建立一個新的 `RunRecord`（狀態為 `CREATED`）。

        - `mode="formal"` 時，若同一 `question_hash` 已有 formal run 紀錄，且沒有帶
          `authorized_rerun=True` 並指向最新一筆的 `run_id`，會拋出
          `FormalRunAlreadyExistsError`。
        - 若目標輸出目錄（依 run_id 推導）已有內容，會拋出 `RunDirectoryExistsError`，
          確保既有輸出不被第二次執行覆寫。
        """
        if mode not in VALID_RUN_MODES:
            raise ValueError(f"mode must be one of {VALID_RUN_MODES}, got {mode!r}")
        if authorized_rerun and not rerun_of:
            raise ValueError("authorized_rerun=True requires rerun_of to be set")
        if rerun_of and not rerun_reason:
            raise ValueError("a rerun requires rerun_reason to be set")

        context = RunContext.create(question, coins, run_id=run_id, as_of=as_of)
        question_hash = compute_question_hash(question, context.coins)

        if mode == RUN_MODE_FORMAL:
            history = self.formal_lock_history(question_hash)
            if history:
                latest = history[-1]
                is_authorized_for_latest = authorized_rerun and rerun_of == latest["run_id"]
                if not is_authorized_for_latest:
                    raise FormalRunAlreadyExistsError(
                        f"formal run already exists for question_hash={question_hash!r} "
                        f"(latest run_id={latest['run_id']!r}, status={latest['status']!r}); "
                        f"pass authorized_rerun=True with rerun_of={latest['run_id']!r} to rerun"
                    )

        output_dir = self.root / context.output_prefix
        if output_dir.exists() and any(output_dir.iterdir()):
            raise RunDirectoryExistsError(f"run output directory already has content: {output_dir}")

        record = RunRecord(
            context=context,
            mode=mode,
            question_hash=question_hash,
            status=STATUS_CREATED,
            rerun_of=rerun_of,
            rerun_reason=rerun_reason,
            authorized_rerun=bool(rerun_of and authorized_rerun),
        )

        if mode == RUN_MODE_FORMAL:
            self._append_lock_history(question_hash, record.lock_payload())

        return record

    # -- 狀態轉換 ------------------------------------------------------

    def transition(self, record: RunRecord, new_status: str) -> RunRecord:
        """套用狀態轉換並同步 formal lock（若適用）。非法轉換拋出例外，record 維持原狀態。"""
        record.transition(new_status)
        if record.mode == RUN_MODE_FORMAL:
            self._update_lock_status(record.question_hash, record.run_id, new_status)
        return record

    # -- 產物輸出 ------------------------------------------------------

    def artifact_store(self, record: RunRecord) -> LocalArtifactStore:
        """取得這個 run 對應的 `LocalArtifactStore`，一律建立在 `self.root` 之下。"""
        return LocalArtifactStore.for_run(record.context, self.root)

    def write_checkpoints(self, record: RunRecord) -> str:
        """把目前累積的 checkpoint 狀態落地，供逾時後仍能重建最小可用產物。"""
        store = self.artifact_store(record)
        return store.write_json("_checkpoint.json", {
            "run_id": record.run_id,
            "status": record.status,
            "stages": record.checkpoints,
        })
