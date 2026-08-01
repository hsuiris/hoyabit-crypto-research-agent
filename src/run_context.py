"""單次執行的識別與環境設定：run_id、時間邊界、產物前綴、模型資訊。

T0.5 的用途是先把「一次執行」需要攜帶的欄位與環境變數名稱固定下來，讓各模組可以平行開發，
之後接 Bedrock 與 S3 時不需要再改欄位名稱。本模組不呼叫任何外部服務。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .llm import configured_provider, llm_runtime_info

# 環境變數名稱在 T0.5 凍結；新增變數可以，改名不行（部署範本與其他 Agent 都依賴這些字面值）。
ENV_LLM_PROVIDER = "LLM_PROVIDER"
ENV_BEDROCK_MODEL_ID = "BEDROCK_MODEL_ID"
ENV_AWS_REGION = "AWS_REGION"
ENV_ARTIFACT_BUCKET = "ARTIFACT_BUCKET"
ENV_ARTIFACT_PREFIX = "ARTIFACT_PREFIX"
ENV_RUN_MODE = "RUN_MODE"
ENV_INTERNAL_DEADLINE_SECONDS = "INTERNAL_DEADLINE_SECONDS"

FROZEN_ENV_NAMES = (
    ENV_LLM_PROVIDER,
    ENV_BEDROCK_MODEL_ID,
    ENV_AWS_REGION,
    ENV_ARTIFACT_BUCKET,
    ENV_ARTIFACT_PREFIX,
    ENV_RUN_MODE,
    ENV_INTERNAL_DEADLINE_SECONDS,
)

# 與 orchestrator 的三階段預算總和一致（7 + 3 + 2 分鐘），仍低於比賽的 15 分鐘上限。
DEFAULT_INTERNAL_DEADLINE_SECONDS = 720.0
DEFAULT_ARTIFACT_PREFIX = "runs"
DEFAULT_RUN_MODE = "offline"
VALID_RUN_MODES = ("offline", "live", "demo")
CONFIG_VERSION = "t0.5"

# T7：run_id 的字面格式。`RUN-` 前綴讓 run 目錄在任何檔案清單裡都一眼可辨，時間戳讓排序等於
# 時序，幣種讓人不必打開 manifest 就知道這次分析的標的，尾碼則保證同一秒內建立的兩個 run 不撞名。
RUN_ID_PREFIX = "RUN"
RUN_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
# 比較題會有兩個幣種；再多就只取前幾個，避免 run 目錄名稱長到不好用。
RUN_ID_MAX_COINS = 3


def normalise_coins(coins: object) -> tuple[str, ...]:
    """把單一字串或可迭代的幣種正規化為大寫 tuple，順序保留呼叫端給的順序。"""
    raw = [coins] if isinstance(coins, str) else list(coins or [])
    return tuple(coin.strip().upper() for coin in raw if coin and str(coin).strip())


def build_run_id(coins: object, now: datetime | None = None, suffix: str | None = None) -> str:
    """組出 `RUN-20260801T012345Z-BTC-1a2b3c4d` 形式的 run_id。

    `suffix` 只在測試需要固定輸出時才傳入；正式執行一律使用隨機尾碼，因為同一秒內建立兩個 run
    是完全可能的（例如比較題的兩腳），而 run_id 一旦相撞就會有輸出互相覆寫的風險。
    """
    stamp = (now or datetime.now(timezone.utc)).strftime(RUN_ID_TIMESTAMP_FORMAT)
    normalised = normalise_coins(coins)[:RUN_ID_MAX_COINS] or ("NA",)
    tail = (suffix or uuid.uuid4().hex[:8]).strip()
    return "-".join([RUN_ID_PREFIX, stamp, *normalised, tail])


def _code_commit() -> str:
    """從 .git 讀出目前 commit；讀不到就回 unknown，不使用 subprocess。"""
    override = os.getenv("CODE_COMMIT", "").strip()
    if override:
        return override
    git_dir = Path(__file__).resolve().parents[1] / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            return (git_dir / head.split(" ", 1)[1].strip()).read_text(encoding="utf-8").strip()
        return head
    except OSError:
        return "unknown"


def _internal_deadline_seconds() -> float:
    raw = os.getenv(ENV_INTERNAL_DEADLINE_SECONDS, "").strip()
    if not raw:
        return DEFAULT_INTERNAL_DEADLINE_SECONDS
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{ENV_INTERNAL_DEADLINE_SECONDS} must be a number") from error
    if value <= 0:
        raise ValueError(f"{ENV_INTERNAL_DEADLINE_SECONDS} must be greater than zero")
    return value


def _run_mode(explicit: str | None) -> str:
    mode = (explicit or os.getenv(ENV_RUN_MODE, "") or DEFAULT_RUN_MODE).strip().lower()
    if mode not in VALID_RUN_MODES:
        raise ValueError(f"{ENV_RUN_MODE} must be one of {list(VALID_RUN_MODES)}")
    return mode


@dataclass(frozen=True)
class RunContext:
    """一次分析執行的不可變描述；產物路徑與 manifest 都由此推導。"""

    run_id: str
    run_mode: str
    question: str
    coins: tuple[str, ...]
    started_at: str
    as_of: str
    deadline_at: str
    output_prefix: str
    code_commit: str
    config_version: str
    model_provider: str
    model_id: str | None
    artifact_bucket: str | None = None
    deadline_seconds: float = DEFAULT_INTERNAL_DEADLINE_SECONDS
    region: str | None = field(default=None)

    @classmethod
    def create(
        cls,
        question: str,
        coins: object,
        run_mode: str | None = None,
        run_id: str | None = None,
        as_of: datetime | None = None,
    ) -> "RunContext":
        """由參數與已凍結的環境變數組出 RunContext；不讀取任何遠端資源。"""
        now = datetime.now(timezone.utc)
        normalised_coins = normalise_coins(coins)
        # T7：沒有指定 run_id 時一律使用 `RUN-<時間>-<幣種>-<尾碼>`，讓每次執行在檔案系統上就能
        # 唯一識別；呼叫端仍可傳入自己的 run_id（測試與既有輸出目錄依賴這個能力）。
        resolved_run_id = run_id or build_run_id(normalised_coins, now=now)
        if not normalised_coins:
            raise ValueError("RunContext requires at least one coin")
        deadline_seconds = _internal_deadline_seconds()
        runtime = llm_runtime_info()
        prefix_root = (os.getenv(ENV_ARTIFACT_PREFIX, "").strip() or DEFAULT_ARTIFACT_PREFIX).strip("/")
        return cls(
            run_id=resolved_run_id,
            run_mode=_run_mode(run_mode),
            question=question,
            coins=normalised_coins,
            started_at=now.isoformat(),
            as_of=(as_of or now).isoformat(),
            deadline_at=(now + timedelta(seconds=deadline_seconds)).isoformat(),
            output_prefix=f"{prefix_root}/{resolved_run_id}",
            code_commit=_code_commit(),
            config_version=os.getenv("CONFIG_VERSION", "").strip() or CONFIG_VERSION,
            model_provider=runtime.get("provider") or configured_provider(),
            model_id=runtime.get("model"),
            artifact_bucket=os.getenv(ENV_ARTIFACT_BUCKET, "").strip() or None,
            deadline_seconds=deadline_seconds,
            region=os.getenv(ENV_AWS_REGION, "").strip() or None,
        )

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["coins"] = list(self.coins)
        return payload
