"""Question-driven Research Planner（T2）。

把現場題目轉換成結構化的 ``ResearchPlan``（欄位定義見 ``src/schemas.py``，本模組不得
自行改名或新增欄位語意）。Planner **只規劃要查什麼，不得產生任何市場結論或方向判斷**
——那是 T4 Claim 的責任，這裡完全不做多空判斷、不預測漲跌。

公開入口是 :func:`build_research_plan`：

    plan, planning_log = build_research_plan(question, coins, client=some_llm_client)

行為：

1. 若提供了 ``LLMClient``（見 ``src/ports.py``），先嘗試呼叫模型產生結構化 plan。
2. 模型呼叫失敗、回傳非 JSON 物件、缺必要欄位，或 ``task_modes`` 出現不在
   ``TASK_MODES`` 中的值時，**不中止流程**，改用純關鍵字比對的 deterministic fallback。
3. 未提供 ``client`` 時，直接走 deterministic fallback（沒有模型可呼叫）。
4. 回傳的第二個值 ``planning_log`` 一律含 ``path``（``"llm"`` 或 ``"fallback"``）、
   ``fallback_used``、``fallback_reason``、``duration_seconds``，方便呼叫端（Orchestrator）
   寫進 Execution Log，本模組不負責寫任何檔案。

Deterministic fallback 對同一題目永遠產生同一份 plan（不含任何時間戳或隨機性），
方便測試與稽核重現。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict

from src.ports import LLMClient
from src.schemas import (
    CLAIM_DOMAINS,
    DEFAULT_TASK_MODE,
    DEFAULT_TIME_WINDOW_DAYS,
    Hypothesis,
    ResearchPlan,
    TASK_MODES,
    TIME_WINDOW_SOURCE_EXPLICIT,
    TIME_WINDOW_SOURCES,
    default_stop_conditions,
    default_time_window,
    normalise_domains,
)

PLANNER_SCHEMA_NAME = "research_plan"
PLANNER_TIMEOUT_SECONDS = 20.0

PLANNING_PATH_LLM = "llm"
PLANNING_PATH_FALLBACK = "fallback"


# --------------------------------------------------------------------------------------
# Fallback 詞彙表（T2-planner.md「Planner 失敗時使用 deterministic fallback」）
# --------------------------------------------------------------------------------------

# 每個 task mode 對應的觸發關鍵字；同一題可同時命中多個 mode（多選）。
# ``DEFAULT_TASK_MODE`` 永遠是基底 mode，其餘命中的 mode 會附加在後面，dedupe 後保序。
_TASK_MODE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("compare_assets", ("比較", "比较", "相比", "相較", "相较", "vs", "VS", "Vs")),
    ("test_hypothesis", ("認為", "认为", "是否", "會不會", "会不会")),
    ("explain_driver", ("原因", "為什麼", "为什么", "驅動", "驱动")),
    ("assess_consistency", ("一致", "矛盾", "整合")),
    ("identify_risks", ("風險", "风险")),
    ("identify_attention_conditions", ("值得關注", "值得关注", "條件", "条件", "觀察", "观察")),
)

# 每個 domain 對應的觸發關鍵字，用於從題目文字直接偵測需要哪些資料 domain。
# 這裡的標籤比 ``CLAIM_DOMAINS`` 細（``announcement``、``whale``），因為關鍵字表要表達的是
# 「題目提到了公告／巨鯨」。輸出前一律經 ``normalise_domains()`` 收斂成 canonical domain，
# 落地的 ``required_domains`` 才會與 T4 算 coverage 用的詞彙表一致。
_DOMAIN_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("market", ("價格", "价格", "市場", "市场", "行情")),
    ("news", ("新聞", "新闻")),
    ("macro", ("宏觀", "宏观")),
    ("announcement", ("公告", "官方宣布", "官方公佈", "官方公告")),
    ("onchain", ("鏈上", "链上", "on-chain", "onchain")),
    ("social", ("社群", "社交", "社群媒體", "社群媒体")),
    ("derivatives", ("衍生", "資金費率", "资金费率", "合約", "合约", "期貨", "期货")),
    ("whale", ("巨鯨", "巨鲸")),
)
# 供 required_domains 排序用的固定順序，避免同一份 plan 每次執行順序不同。
_DOMAIN_ORDER: tuple[str, ...] = tuple(domain for domain, _ in _DOMAIN_KEYWORDS)

# task mode 沒有從題目文字命中任何 domain 關鍵字時的基底 domain。
_BASE_DOMAINS_BY_MODE: dict[str, tuple[str, ...]] = {
    "describe_market_state": ("market",),
    "test_hypothesis": ("market",),
    "compare_assets": ("market",),
    "explain_driver": ("market", "news"),
    "assess_consistency": ("market", "news"),
    "identify_risks": ("market", "derivatives"),
    "identify_attention_conditions": ("market",),
}

# 比較題共用的維度；`comparison_dimensions` 只在 task_modes 含 compare_assets 時填入。
_DEFAULT_COMPARISON_DIMENSIONS: tuple[str, ...] = (
    "price_performance", "volatility", "market_cap_liquidity", "risk_signals",
)

# 明確天數字面（詞彙比對優先於數字 regex 之外的補充）；比對時取第一個命中者。
_TIME_WINDOW_WORD_DAYS: tuple[tuple[str, int], ...] = (
    ("兩週", 14), ("两周", 14), ("兩周", 14),
    ("三週", 21), ("三周", 21),
    ("一週", 7), ("一周", 7),
    ("一個月", 30), ("一个月", 30),
    ("半年", 182),
    ("一年", 365),
)
_NUM_DAY_RE = re.compile(r"(\d+)\s*天")
_NUM_WEEK_RE = re.compile(r"(\d+)\s*[週周]")
_NUM_MONTH_RE = re.compile(r"(\d+)\s*個?个?月")


def _detect_explicit_days(question: str) -> int | None:
    """從題目文字偵測明確天數；偵測不到回傳 ``None``（交由呼叫端套用產品預設值）。"""
    day_match = _NUM_DAY_RE.search(question)
    if day_match:
        return int(day_match.group(1))
    week_match = _NUM_WEEK_RE.search(question)
    if week_match:
        return int(week_match.group(1)) * 7
    month_match = _NUM_MONTH_RE.search(question)
    if month_match:
        return int(month_match.group(1)) * 30
    for word, days in _TIME_WINDOW_WORD_DAYS:
        if word in question:
            return days
    return None


def _fallback_task_modes(question: str) -> list[str]:
    matched = [mode for mode, keywords in _TASK_MODE_KEYWORDS if any(kw in question for kw in keywords)]
    ordered = [DEFAULT_TASK_MODE]
    for mode in matched:
        if mode not in ordered:
            ordered.append(mode)
    return ordered


def _fallback_domains(question: str, task_modes: list[str]) -> list[str]:
    detected = {domain for domain, keywords in _DOMAIN_KEYWORDS if any(kw in question for kw in keywords)}
    for mode in task_modes:
        detected.update(_BASE_DOMAINS_BY_MODE.get(mode, ()))
    ordered = [domain for domain in _DOMAIN_ORDER if domain in detected]
    return normalise_domains(ordered) or ["market"]


def _fallback_comparison_dimensions(task_modes: list[str]) -> list[str]:
    return list(_DEFAULT_COMPARISON_DIMENSIONS) if "compare_assets" in task_modes else []


def _fallback_time_window(question: str) -> dict:
    days = _detect_explicit_days(question)
    if days:
        return {"days": days, "source": TIME_WINDOW_SOURCE_EXPLICIT}
    return default_time_window()


def _fallback_assumptions(time_window: dict) -> list[str]:
    if time_window.get("source") != "default":
        return []
    return [
        f"題目未明確指定時間範圍，沿用產品預設的 {time_window.get('days', DEFAULT_TIME_WINDOW_DAYS)} 天"
        "作為研究窗口；這個天數不是題目要求的，只是預設假設。"
    ]


def _fallback_hypotheses(question: str, coins: list[str], task_modes: list[str]) -> list[Hypothesis]:
    """假設題必須對稱：support / contradiction / falsification 三組都非空。"""
    if "test_hypothesis" not in task_modes:
        return []
    statement = question.strip()
    coin_label = "/".join(coins) if coins else "標的"
    return [
        Hypothesis(
            hypothesis_id="H1",
            statement=statement,
            support_questions=[
                f"是否有市場數據支持「{statement}」？",
                f"{coin_label} 近期價格與成交量是否與此判斷一致？",
                "鏈上或衍生品訊號是否印證這個判斷？",
            ],
            contradiction_questions=[
                f"是否有市場數據反對「{statement}」？",
                f"{coin_label} 是否出現與此判斷相反的資金流向？",
                "新聞或社群情緒是否釋出相反訊號？",
            ],
            falsification_conditions=[
                "若價格出現顯著突破或跌破既有區間，此假設應視為被推翻。",
                "若成交量、資金費率或鏈上訊號持續且一致地指向相反方向，應重新評估此假設。",
            ],
        )
    ]


def _build_fallback_plan(question: str, coins: list[str]) -> ResearchPlan:
    task_modes = _fallback_task_modes(question)
    time_window = _fallback_time_window(question)
    return ResearchPlan(
        coins=list(coins),
        task_modes=task_modes,
        primary_question=question,
        time_window=time_window,
        hypotheses=_fallback_hypotheses(question, coins, task_modes),
        required_domains=_fallback_domains(question, task_modes),
        comparison_dimensions=_fallback_comparison_dimensions(task_modes),
        assumptions=_fallback_assumptions(time_window),
        stop_conditions=default_stop_conditions(),
    )


# --------------------------------------------------------------------------------------
# LLM 路徑：prompt、schema 與嚴格結構驗證
# --------------------------------------------------------------------------------------

RESEARCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "coins": {"type": "array", "items": {"type": "string"}},
        "task_modes": {"type": "array", "items": {"type": "string", "enum": list(TASK_MODES)}},
        "primary_question": {"type": "string"},
        "time_window": {
            "type": "object",
            "properties": {
                "days": {"type": "integer"},
                "source": {"type": "string", "enum": list(TIME_WINDOW_SOURCES)},
            },
            "required": ["days", "source"],
            "additionalProperties": False,
        },
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis_id": {"type": "string"},
                    "statement": {"type": "string"},
                    "support_questions": {"type": "array", "items": {"type": "string"}},
                    "contradiction_questions": {"type": "array", "items": {"type": "string"}},
                    "falsification_conditions": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "hypothesis_id", "statement", "support_questions",
                    "contradiction_questions", "falsification_conditions",
                ],
                "additionalProperties": False,
            },
        },
        # enum 只是把模型往 canonical 詞彙推；真正的保證在 ``_plan_from_llm_payload()`` 的
        # ``normalise_domains()``。不在這裡驗證失敗就作廢整份 plan：模型只是用了近義詞
        # （market_data 之類），plan 的其餘部分仍然可用，沒有理由整份丟掉。
        "required_domains": {"type": "array", "items": {"type": "string", "enum": list(CLAIM_DOMAINS)}},
        "comparison_dimensions": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "stop_conditions": {
            "type": "object",
            "properties": {
                "max_evidence": {"type": "integer"},
                "max_followup_rounds": {"type": "integer"},
            },
            "required": ["max_evidence", "max_followup_rounds"],
            "additionalProperties": False,
        },
    },
    "required": [
        "coins", "task_modes", "primary_question", "time_window", "hypotheses",
        "required_domains", "comparison_dimensions", "assumptions", "stop_conditions",
    ],
    "additionalProperties": False,
}


def _build_prompt(question: str, coins: list[str]) -> str:
    payload = {
        "question": question,
        "coins": coins,
        "allowed_task_modes": list(TASK_MODES),
        "allowed_domains": list(CLAIM_DOMAINS),
        "time_window_sources": list(TIME_WINDOW_SOURCES),
        "default_time_window_days": DEFAULT_TIME_WINDOW_DAYS,
    }
    return (
        "你是加密貨幣研究任務的規劃者（Planner）。你的工作只是把題目拆解成研究計畫，"
        "絕對不要對市場方向做出任何判斷、預測或結論——那是另一個角色（分析師）的責任。\n"
        "請規劃：\n"
        "1. task_modes：可多選，只能使用 allowed_task_modes 中的值。\n"
        "2. time_window：題目明確提到天數/週數/月數時 source 填 explicit，否則沿用"
        "default_time_window_days 並填 default。\n"
        "3. hypotheses：只有在題目要求驗證假設時才填，且 support_questions、"
        "contradiction_questions、falsification_conditions 三者都必須非空且對稱"
        "（同時找支持面與反對面，不能只找想看到的證據）。\n"
        "4. required_domains：需要蒐集的資料領域，只能使用 allowed_domains 中的值，"
        "不要自創近義詞（例如不要寫 market_data、technical_analysis，價格與技術面都屬 market）。\n"
        "5. comparison_dimensions：比較題才填，且兩個標的必須共用同一組維度。\n"
        "6. assumptions：任何你替題目補上的預設值都要在這裡明說。\n"
        "只回傳一個 JSON 物件，欄位需完全符合上述規則。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _validate_plan_payload(raw: object) -> dict:
    """驗證 LLM 回傳的 plan 結構；任何不合法都拋 ``ValueError`` 交由呼叫端降級。"""
    if not isinstance(raw, dict):
        raise ValueError("research_plan response must be a JSON object")
    missing = set(RESEARCH_PLAN_SCHEMA["required"]) - set(raw)
    if missing:
        raise ValueError(f"research_plan response missing required fields: {sorted(missing)}")

    task_modes = raw["task_modes"]
    if not isinstance(task_modes, list) or not task_modes or any(mode not in TASK_MODES for mode in task_modes):
        raise ValueError(f"research_plan response has invalid task_modes: {task_modes!r}")

    time_window = raw["time_window"]
    if (
        not isinstance(time_window, dict)
        or not isinstance(time_window.get("days"), int)
        or isinstance(time_window.get("days"), bool)
        or time_window.get("days") <= 0
        or time_window.get("source") not in TIME_WINDOW_SOURCES
    ):
        raise ValueError(f"research_plan response has invalid time_window: {time_window!r}")

    hypotheses = raw["hypotheses"]
    if not isinstance(hypotheses, list):
        raise ValueError("research_plan response hypotheses must be a list")
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            raise ValueError("research_plan response hypothesis entries must be objects")
        for key in ("support_questions", "contradiction_questions", "falsification_conditions"):
            if not isinstance(hypothesis.get(key), list) or not hypothesis[key]:
                raise ValueError(f"research_plan hypothesis is missing a non-empty {key}")
    if "test_hypothesis" in task_modes and not hypotheses:
        raise ValueError("research_plan declares test_hypothesis but supplies no hypotheses")

    stop_conditions = raw["stop_conditions"]
    if (
        not isinstance(stop_conditions, dict)
        or not isinstance(stop_conditions.get("max_evidence"), int)
        or stop_conditions.get("max_evidence") <= 0
        or not isinstance(stop_conditions.get("max_followup_rounds"), int)
        or stop_conditions.get("max_followup_rounds") < 0
    ):
        raise ValueError(f"research_plan response has invalid stop_conditions: {stop_conditions!r}")

    for key in ("required_domains", "comparison_dimensions", "assumptions", "coins"):
        if not isinstance(raw[key], list):
            raise ValueError(f"research_plan response field {key!r} must be a list")

    return raw


def _plan_from_llm_payload(question: str, coins: list[str], raw: dict) -> ResearchPlan:
    """把已驗證過的 LLM payload 轉成 ``ResearchPlan``。

    ``coins`` 與 ``primary_question`` 一律採用呼叫端傳入的正規化值，不採信模型自己
    複述的版本，避免模型改寫題目或幣種造成的漂移。
    """
    hypotheses = [
        Hypothesis(
            hypothesis_id=str(item.get("hypothesis_id", "")),
            statement=str(item.get("statement", "")),
            support_questions=list(item.get("support_questions", [])),
            contradiction_questions=list(item.get("contradiction_questions", [])),
            falsification_conditions=list(item.get("falsification_conditions", [])),
        )
        for item in raw["hypotheses"]
    ]
    # required_domains 一律收斂成 canonical domain。模型即使無視 schema 的 enum 回了
    # market_data／news_events 這類近義詞，落地的 plan 仍與 T4 算 domain_coverage 用的
    # 詞彙表一致；全部無法對應時留空，由計分端退回預設分母（見 claim_graph）。
    return ResearchPlan(
        coins=list(coins),
        task_modes=list(raw["task_modes"]),
        primary_question=question,
        time_window=dict(raw["time_window"]),
        hypotheses=hypotheses,
        required_domains=normalise_domains(raw["required_domains"]),
        comparison_dimensions=list(raw["comparison_dimensions"]),
        assumptions=list(raw["assumptions"]),
        stop_conditions=dict(raw["stop_conditions"]),
    )


# --------------------------------------------------------------------------------------
# 公開入口
# --------------------------------------------------------------------------------------

def _normalize_coins(coins: object) -> list[str]:
    if isinstance(coins, str):
        coins = [coins]
    normalized: list[str] = []
    seen: set[str] = set()
    for coin in coins:
        token = str(coin).strip().upper()
        if token and token not in seen:
            seen.add(token)
            normalized.append(token)
    if not normalized:
        raise ValueError("build_research_plan requires at least one coin")
    return normalized


def plan_to_dict(plan: ResearchPlan) -> dict:
    """把 ``ResearchPlan``（含巢狀 ``Hypothesis``）轉成可 ``json.dumps`` 的 dict。"""
    return asdict(plan)


def build_research_plan(
    question: str,
    coins: object,
    *,
    client: LLMClient | None = None,
    timeout_seconds: float = PLANNER_TIMEOUT_SECONDS,
) -> tuple[ResearchPlan, dict]:
    """把題目與幣種轉成 ``ResearchPlan``。

    - 未提供 ``client`` 時直接走 deterministic keyword fallback。
    - 提供 ``client`` 時先嘗試模型呼叫；模型呼叫例外、JSON 不合法、缺必要欄位或
      ``task_modes`` 含未知值，都會被攔下並改用 fallback（不中止流程）。
    - 回傳 ``(plan, planning_log)``；``planning_log["path"]`` 是 ``"llm"`` 或
      ``"fallback"``，呼叫端可直接寫入 Execution Log。

    單幣執行呼叫一次即得到一份 plan；比較執行把兩個幣種放進同一次呼叫的 ``coins``，
    回傳的仍是同一份 plan，因此 time_window／comparison_dimensions／stop_conditions
    天然共用，不需要額外同步。
    """
    normalized_coins = _normalize_coins(coins)
    normalized_question = (question or "").strip()
    if not normalized_question:
        raise ValueError("build_research_plan requires a non-empty question")

    started = time.monotonic()

    if client is None:
        plan = _build_fallback_plan(normalized_question, normalized_coins)
        return plan, {
            "path": PLANNING_PATH_FALLBACK,
            "fallback_used": True,
            "fallback_reason": "no_client_injected",
            "duration_seconds": round(time.monotonic() - started, 4),
        }

    try:
        raw = client.generate_json(
            prompt=_build_prompt(normalized_question, normalized_coins),
            schema=RESEARCH_PLAN_SCHEMA,
            schema_name=PLANNER_SCHEMA_NAME,
            timeout_seconds=timeout_seconds,
        )
        validated = _validate_plan_payload(raw)
        plan = _plan_from_llm_payload(normalized_question, normalized_coins, validated)
        return plan, {
            "path": PLANNING_PATH_LLM,
            "fallback_used": False,
            "fallback_reason": None,
            "duration_seconds": round(time.monotonic() - started, 4),
        }
    except Exception as error:  # noqa: BLE001 - 任何模型/驗證失敗都必須降級，不得中止流程
        plan = _build_fallback_plan(normalized_question, normalized_coins)
        return plan, {
            "path": PLANNING_PATH_FALLBACK,
            "fallback_used": True,
            "fallback_reason": f"{type(error).__name__}: {error}",
            "duration_seconds": round(time.monotonic() - started, 4),
        }
