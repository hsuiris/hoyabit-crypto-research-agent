"""競賽資料形狀契約（T0.6 凍結）。

T2（Research Plan）、T3（Credibility）、T4（Claim Graph）、T5（Citation Gate 與提交物）
會平行開發。如果每條線各自命名欄位、各自寫權重，整合時必然對不起來。本模組把四條線共用的
資料形狀與常數一次固定下來：**只有結構與常數，沒有任何業務邏輯**。

四條規則：

1. 這裡不計分、不規劃、不建圖、不驗證引用。credibility 與 claim confidence 的實際計算一律由
   T3／T4 的 deterministic Python 程式負責；LLM 不得決定最終分數，也不得突破 hard cap。
2. 常數集合使用 ``tuple``／``frozenset``／``MappingProxyType``，呼叫端無法就地改寫共用預設值。
3. 欄位名稱與數值取自 ``docs/competition-tasks/T2-planner.md``、``T3-credibility.md``、
   ``T4-claim-graph.md``、``T5-citation-output.md`` 與
   ``.kiro/steering/evidence-confidence-standards.md``。**改名等於破壞契約，新增欄位才是相容做法。**
4. 本模組不 import 專案內其他模組（與 ``src/ports.py`` 相同原則），任何一層都能安全引用。

Evidence 本身仍定義在 ``src/day1_mvp.py``，因為既有 positional constructor 必須保持相容；
本模組只提供它新增欄位的名稱清單 ``EVIDENCE_CREDIBILITY_FIELDS`` 與對應預設值常數，供
validator、credibility engine 與 Web UI 共用同一份字面值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType

# ``SCHEMA_VERSION`` 描述本檔凍結的資料形狀；``SCORING_VERSION`` 只描述 credibility 計分版本
# （字面值取自 design.md 5.5 的輸出範例）。兩者分開遞增：改欄位不必動計分版本，反之亦然。
SCHEMA_VERSION = "competition-schema-v1"
SCORING_VERSION = "credibility-v1"


# --------------------------------------------------------------------------------------
# T2 — Research Plan
# --------------------------------------------------------------------------------------

# 七種 task mode（T2-planner.md「Allowed task modes」）。一題可同時具有多個 mode。
TASK_MODES = (
    "describe_market_state",
    "test_hypothesis",
    "compare_assets",
    "explain_driver",
    "assess_consistency",
    "identify_risks",
    "identify_attention_conditions",
)
DEFAULT_TASK_MODE = "describe_market_state"

# 時間窗來源：題目寫明用 explicit，沿用產品預設值時必須是 default，且要在 assumptions 明示。
TIME_WINDOW_SOURCE_EXPLICIT = "explicit"
TIME_WINDOW_SOURCE_DEFAULT = "default"
TIME_WINDOW_SOURCES = (TIME_WINDOW_SOURCE_EXPLICIT, TIME_WINDOW_SOURCE_DEFAULT)

DEFAULT_TIME_WINDOW_DAYS = 14
DEFAULT_MAX_EVIDENCE = 36
DEFAULT_MAX_FOLLOWUP_ROUNDS = 1


# --------------------------------------------------------------------------------------
# T2 產出／T4 消費 — 研究領域（domain）詞彙表
# --------------------------------------------------------------------------------------

# domain 是「研究領域覆蓋度」的單位，不是資料來源數量。它有兩端：
#   producer：``ResearchPlan.required_domains``（src/planner.py）
#   consumer：domain_coverage 的分母（src/claim_graph.py），會與每筆證據的 domain 取交集
#
# 兩端必須共用同一組字面值。詞彙表一旦分岔，交集會是空集合，coverage 靜靜地變成 0.0，
# 於是每個 Claim 都被判成 insufficient_evidence —— 沒有任何錯誤訊息，報告只會說「資料不足」。
# 因此這裡是 domain 字面值的唯一定義來源。
#
# 這組值必須等於 ``claim_graph.DOMAIN_BY_DATA_TYPE`` 的相異值集合（由測試釘住，防止再次分岔）。
CLAIM_DOMAINS = (
    "market",
    "news",
    "social",
    "onchain",
    "derivatives",
    "macro",
)

# 非 canonical 寫法 → canonical domain。需要它的原因有兩個：
#   1. LLM 產生的 plan 用自由字串填 required_domains。實測 Bedrock（nova-lite）會回
#      market_data／news_events／social_sentiment／technical_analysis 這類近義詞。
#   2. Planner 的關鍵字表有比 domain 更細的標籤：``announcement`` 歸 news、``whale`` 歸
#      onchain —— 與 ``DOMAIN_BY_DATA_TYPE`` 對證據的歸類方式一致。
#
# 比對前一律先過 ``_domain_key()``（小寫、非英數字元收斂成單一底線），因此 ``on-chain``、
# ``On Chain``、``ONCHAIN`` 命中同一個鍵，不需要列舉大小寫與分隔符的變體。
DOMAIN_ALIASES = MappingProxyType({
    # market：價格、成交量與技術面都由市場資料推導，歸同一個領域。
    "price": "market",
    "prices": "market",
    "price_data": "market",
    "price_action": "market",
    "price_history": "market",
    "market_data": "market",
    "market_price": "market",
    "ohlcv": "market",
    "volume": "market",
    "volumes": "market",
    "trading_volume": "market",
    "liquidity": "market",
    "technical": "market",
    "technicals": "market",
    "technical_analysis": "market",
    "technical_indicators": "market",
    "indicators": "market",
    "ta": "market",
    "chart": "market",
    "charts": "market",
    "vegas_channel": "market",
    # news：官方公告是第一手新聞，與二手報導同屬 news 領域（可信度差異由 credibility 處理）。
    "news_events": "news",
    "news_event": "news",
    "news_media": "news",
    "media": "news",
    "headlines": "news",
    "press": "news",
    "press_release": "news",
    "announcement": "news",
    "announcements": "news",
    "official": "news",
    "official_announcement": "news",
    "official_announcements": "news",
    "project_updates": "news",
    # social
    "sentiment": "social",
    "social_media": "social",
    "social_sentiment": "social",
    "social_discussion": "social",
    "community": "social",
    "community_sentiment": "social",
    "discussion": "social",
    # onchain：巨鯨餘額與 TVL 都是鏈上觀測，與 DOMAIN_BY_DATA_TYPE 的 whale／tvl 一致。
    "on_chain": "onchain",
    "onchain_data": "onchain",
    "on_chain_data": "onchain",
    "chain": "onchain",
    "chain_data": "onchain",
    "blockchain": "onchain",
    "blockchain_data": "onchain",
    "whale": "onchain",
    "whales": "onchain",
    "whale_activity": "onchain",
    "whale_wallets": "onchain",
    "tvl": "onchain",
    "total_value_locked": "onchain",
    # derivatives
    "derivative": "derivatives",
    "derivatives_data": "derivatives",
    "funding_rate": "derivatives",
    "funding_rates": "derivatives",
    "futures": "derivatives",
    "options": "derivatives",
    "open_interest": "derivatives",
    "long_short_ratio": "derivatives",
    "leverage": "derivatives",
    "perpetuals": "derivatives",
    # macro：政策與監管事件由 macro collector 取得（Fed press releases），歸 macro。
    "macroeconomic": "macro",
    "macroeconomics": "macro",
    "macro_economy": "macro",
    "macro_economic": "macro",
    "economy": "macro",
    "economic": "macro",
    "monetary_policy": "macro",
    "policy": "macro",
    "regulation": "macro",
    "regulations": "macro",
    "regulatory": "macro",
    "risk_appetite": "macro",
    "fear_greed": "macro",
})

_DOMAIN_KEY_RE = re.compile(r"[^a-z0-9]+")


def _domain_key(name: object) -> str:
    """把 domain 寫法收斂成比對用的鍵：小寫，非英數字元一律變成單一底線。"""
    return _DOMAIN_KEY_RE.sub("_", str(name).strip().lower()).strip("_")


def normalise_domains(names) -> list[str]:
    """把任意 domain 寫法對應成 ``CLAIM_DOMAINS`` 的字面值。

    保序去重（輸入順序是 deterministic 的，因此輸出也是）。無法對應的名稱一律丟棄，
    **不**在這裡替換成預設值：分母該不該退回預設是計分端的決定，這裡只做字面值對應。
    """
    canonical: list[str] = []
    for name in names or ():
        key = _domain_key(name)
        if not key:
            continue
        domain = key if key in CLAIM_DOMAINS else DOMAIN_ALIASES.get(key)
        if domain and domain not in canonical:
            canonical.append(domain)
    return canonical


def default_time_window() -> dict:
    """預設時間窗。標成 ``default`` 是為了讓報告能誠實說明「這 14 天不是題目要求的」。"""
    return {"days": DEFAULT_TIME_WINDOW_DAYS, "source": TIME_WINDOW_SOURCE_DEFAULT}


def default_stop_conditions() -> dict:
    """預設停止條件，避免蒐集無上限地吃掉時間預算。"""
    return {"max_evidence": DEFAULT_MAX_EVIDENCE, "max_followup_rounds": DEFAULT_MAX_FOLLOWUP_ROUNDS}


@dataclass
class Hypothesis:
    """假設題的對稱結構。

    支持與反對問題都必須存在，否則只會找到想看的證據；``falsification_conditions``
    則是「什麼情況下這個假設算被推翻」，讓假設可檢驗而不是口號。
    """

    hypothesis_id: str = ""
    statement: str = ""
    support_questions: list = field(default_factory=list)
    contradiction_questions: list = field(default_factory=list)
    falsification_conditions: list = field(default_factory=list)


@dataclass
class ResearchPlan:
    """Planner 輸出形狀（落地為 ``research_plan.json``）。

    欄位順序刻意與 T2-planner.md 的 JSON 範例一致。Plan 只描述「要查什麼」，
    不得含市場結論 —— 方向性判斷是 T4 Claim 的責任。
    """

    coins: list = field(default_factory=list)
    task_modes: list = field(default_factory=list)
    primary_question: str = ""
    time_window: dict = field(default_factory=default_time_window)
    hypotheses: list = field(default_factory=list)
    required_domains: list = field(default_factory=list)
    comparison_dimensions: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    stop_conditions: dict = field(default_factory=default_stop_conditions)


# --------------------------------------------------------------------------------------
# T4 — Claim / Fact / Confidence
# --------------------------------------------------------------------------------------

# 五種 verdict（T4-claim-graph.md「Allowed verdicts」）。
VERDICTS = (
    "supported",
    "partially_supported",
    "mixed",
    "contradicted",
    "insufficient_evidence",
)
VERDICT_INSUFFICIENT_EVIDENCE = "insufficient_evidence"

CONFIDENCE_LEVELS = ("low", "medium", "high")

# 信心是 heuristic evidence score，不是校準過的市場正確機率；``type`` 欄位固定寫這個字。
CONFIDENCE_TYPE_HEURISTIC = "heuristic"

DEFAULT_CLAIM_TYPE = "market_judgment"


@dataclass
class Fact:
    """可直接追溯的觀察。``evidence_ids`` 必須指向本次 run 內存在且未被 reject 的 Evidence。"""

    statement: str = ""
    evidence_ids: list = field(default_factory=list)


@dataclass
class ClaimConfidence:
    """Claim 的信心分數與其展開。

    ``components`` 使用 ``CONFIDENCE_COMPONENT_KEYS`` 的鍵，``limiters`` 記錄套用過的
    cap 名稱（例如 ``single_supporting_domain``），讓讀者知道分數為什麼被壓住。
    Critic 只能降低 ``score``，不能提高。
    """

    score: float = 0.0
    level: str = "low"
    type: str = CONFIDENCE_TYPE_HEURISTIC
    components: dict = field(default_factory=dict)
    limiters: list = field(default_factory=list)


@dataclass
class Claim:
    """一個主要判斷，含 Fact → Inference → Conclusion 三層與正反證據。

    ``verdict`` 預設 ``insufficient_evidence``：資料不足時必須誠實輸出資料不足，
    不能因為預設值好看就先給方向。
    """

    claim_id: str = ""
    statement: str = ""
    claim_type: str = DEFAULT_CLAIM_TYPE
    verdict: str = VERDICT_INSUFFICIENT_EVIDENCE
    facts: list = field(default_factory=list)
    inference: str = ""
    conclusion: str = ""
    supporting_evidence_ids: list = field(default_factory=list)
    contradicting_evidence_ids: list = field(default_factory=list)
    confidence: ClaimConfidence = field(default_factory=ClaimConfidence)
    limitations: list = field(default_factory=list)
    invalidation_conditions: list = field(default_factory=list)
    watchpoints: list = field(default_factory=list)


# --------------------------------------------------------------------------------------
# T3 — Evidence credibility 的可解釋計分
# --------------------------------------------------------------------------------------

CREDIBILITY_COMPONENT_KEYS = (
    "source_quality",
    "traceability",
    "freshness",
    "method_transparency",
    "independence",
)

# base_score = 0.30×source_quality + 0.25×traceability + 0.20×freshness
#            + 0.15×method_transparency + 0.10×independence
CREDIBILITY_WEIGHTS = MappingProxyType({
    "source_quality": 0.30,
    "traceability": 0.25,
    "freshness": 0.20,
    "method_transparency": 0.15,
    "independence": 0.10,
})

# T3 Source Registry 的類別；Evidence.source_type 只能是其中之一。
SOURCE_TYPES = (
    "market_api",
    "derivatives_api",
    "blockchain_raw",
    "official_announcement",
    "major_media",
    "secondary_media",
    "social_public",
    "macro_api",
    "local_csv",
    "fallback_fixture",
    "unknown",
)
SOURCE_TYPE_UNKNOWN = "unknown"
SOURCE_TYPE_FALLBACK_FIXTURE = "fallback_fixture"

# ``unverified`` 是預設值；``partially_confirmed`` 取自 design.md 5.5 的輸出範例；
# ``verified`` 取自 evidence-confidence-standards（LLM 不得自行把 unverified 改成 verified）；
# ``rejected`` 由 T5 citation gate 使用，被 reject 的 Evidence 不得進入主要報告。
# ``unavailable``／``fallback`` 由 T3 追加（T3-credibility.md 規則 11）：降級證據必須留在
# Evidence List 供稽核，但狀態要一眼看出它不是實證。兩者的差別是「原本預期取得但這次取不到」
# 與「本來就是離線 fixture」。追加在尾端，既有值的字面與順序不變。
VERIFICATION_STATUSES = (
    "unverified", "partially_confirmed", "verified", "rejected", "unavailable", "fallback",
)
VERIFICATION_STATUS_UNVERIFIED = "unverified"
VERIFICATION_STATUS_REJECTED = "rejected"
VERIFICATION_STATUS_UNAVAILABLE = "unavailable"
VERIFICATION_STATUS_FALLBACK = "fallback"
# 這兩個狀態代表「不得作為主要實證」；validator 與 T4／T5 用同一份集合判斷。
NON_SUBSTANTIVE_VERIFICATION_STATUSES = frozenset({
    VERIFICATION_STATUS_REJECTED, VERIFICATION_STATUS_UNAVAILABLE, VERIFICATION_STATUS_FALLBACK,
})

# hard cap 是上限，必須以 min(raw_score, cap) 套用，不可被加權平均突破。
# ``missing_fetched_at`` 在 T3 與 steering 中寫的是 ``rejected``（不是一個數字）：
# 這裡以 0.0 表示該語意，並用 REJECTING_HARD_CAP_KEYS 標明「命中即 reject，不是降分」，
# 讓整個字典維持同一種型別（float），呼叫端不必為單一鍵做特例判斷。
HARD_CAP_REJECTED = 0.0
REJECTING_HARD_CAP_KEYS = frozenset({"missing_fetched_at"})

HARD_CAPS = MappingProxyType({
    "missing_source_locator": 0.30,
    "missing_fetched_at": HARD_CAP_REJECTED,
    "anonymous_or_low_trace_social": 0.35,
    "single_secondary_news_source": 0.60,
    "fallback_fixture": 0.20,
    "unverifiable_entity_attribution": 0.60,
    "unverifiable_intent_attribution": 0.55,
    "high_quality_conflict_claim_cap": 0.70,
})

# T3 附加在 Evidence 尾端的欄位（全部有 default，既有 positional constructor 不受影響）。
# ``scoring_version`` 不在 T3「New Evidence Fields」的程式碼區塊內，而是同一份文件
# Deterministic Score 的輸出清單要求保存的欄位，因此一併凍結在這裡。
EVIDENCE_CREDIBILITY_FIELDS = (
    "source_type",
    "published_at",
    "event_time",
    "verification_status",
    "source_lineage_id",
    "claim_relevance",
    "independence_factor",
    "score_breakdown",
    "score_limiters",
    "related_claim_ids",
    "scoring_version",
)

# 同源轉載的 lineage 判斷可用的鍵；20 篇引用同一原始消息只算一條主要來源鏈。
SOURCE_LINEAGE_KEYS = (
    "canonical_url",
    "original_source_url",
    "normalized_title",
    "content_hash",
    "quote_hash",
    "source_domain",
    "event_id",
    "transaction_hash",
)


# --------------------------------------------------------------------------------------
# T4 — Claim confidence 的可解釋計分
# --------------------------------------------------------------------------------------

CONFIDENCE_COMPONENT_KEYS = (
    "weighted_evidence_quality",
    "domain_coverage",
    "source_diversity",
    "signal_consistency",
    "counter_evidence_coverage",
)

# confidence = 0.30×weighted_evidence_quality + 0.25×domain_coverage + 0.20×source_diversity
#            + 0.15×signal_consistency + 0.10×counter_evidence_coverage
CONFIDENCE_WEIGHTS = MappingProxyType({
    "weighted_evidence_quality": 0.30,
    "domain_coverage": 0.25,
    "source_diversity": 0.20,
    "signal_consistency": 0.15,
    "counter_evidence_coverage": 0.10,
})

# 只有一個 domain 支持時的上限；高品質支持與反對同時存在時的上限（與 HARD_CAPS 同一數值）；
# domain coverage 低於門檻時必須輸出 insufficient_evidence。
SINGLE_DOMAIN_CONFIDENCE_CAP = 0.60
HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP = HARD_CAPS["high_quality_conflict_claim_cap"]
MIN_DOMAIN_COVERAGE = 0.40


# --------------------------------------------------------------------------------------
# T5 — 提交物檔名
# --------------------------------------------------------------------------------------

# 六項提交物；所有檔案使用同一個 run ID，路徑一律經由 ArtifactStore（見 src/ports.py）。
ARTIFACT_FILENAMES = MappingProxyType({
    "report": "report.md",
    "evidence": "evidence.json",
    "execution_log": "execution_log.json",
    "research_plan": "research_plan.json",
    "claims": "claims.json",
    "manifest": "manifest.json",
})
REQUIRED_ARTIFACT_KEYS = tuple(ARTIFACT_FILENAMES)

MANIFEST_VERSION = "competition-manifest-v1"


# --------------------------------------------------------------------------------------
# E2 — 問題導向的證據語意評估（question-aware evidence assessment）
# --------------------------------------------------------------------------------------

# 為什麼需要這一層：credibility（T3）衡量的是「這個來源有多可信」，那是來源屬性，**不是**
# 「這筆證據對使用者問的那個問題有多相關」。一則可信度 0.9 的總經新聞，對「ETH 下行風險」
# 這個題目可能完全不相關；一筆可信度 0.45 的社群貼文可能直接命中題目。兩者混為一談時，
# 報告會用來源品質冒充問題相關性。
#
# 三條邊界：
#   1. 標籤（離散）可以由模型提出；**分數一律由 Python 依固定映射得出**，模型不得輸出數值。
#   2. `relationship_to_question` 與 `impact_direction` 是解釋欄位，不參與任何加權公式。
#   3. Claim confidence 仍由 `src/claim_graph.py` 的既有公式與 hard caps 計算；本層只提供
#      `claim_relevance` 這個既有輸入的值，不新增第二套決策邏輯。
#
# 另外：市場立場（`_market_stance()` 的固定權重）與 Claim confidence 是**兩條不同的訊號層**。
# E2 只讓 Claim／Evidence 層的問題相關性可稽核，刻意不動 stance 的權重公式。

ASSESSMENT_VERSION = "evidence-assessment-v1"

# 五種相關性標籤與 Python 固定映射。映射表是唯一的數值來源：模型只能給標籤。
RELEVANCE_LABEL_DIRECT = "direct"
RELEVANCE_LABEL_INDIRECT = "indirect"
RELEVANCE_LABEL_CONTEXT = "context"
RELEVANCE_LABEL_IRRELEVANT = "irrelevant"
RELEVANCE_LABEL_UNCLEAR = "unclear"
RELEVANCE_LABELS = (
    RELEVANCE_LABEL_DIRECT,
    RELEVANCE_LABEL_INDIRECT,
    RELEVANCE_LABEL_CONTEXT,
    RELEVANCE_LABEL_IRRELEVANT,
    RELEVANCE_LABEL_UNCLEAR,
)
RELEVANCE_LABEL_SCORES = MappingProxyType({
    RELEVANCE_LABEL_DIRECT: 1.0,
    RELEVANCE_LABEL_INDIRECT: 0.65,
    RELEVANCE_LABEL_CONTEXT: 0.35,
    # 明確不相關就是 0，不是「未知」。這個 0 必須能一路保持到 claim confidence，
    # 不可在下游被當成缺值而還原成保守的 0.50（見 `claim_graph._relevance`）。
    RELEVANCE_LABEL_IRRELEVANT: 0.0,
    # 「看不懂」不等於「不相關」，但也不能假裝知道：給一個低到不足以撐起結論的值。
    RELEVANCE_LABEL_UNCLEAR: 0.20,
})

# 這筆證據相對題目站在哪一邊。純解釋欄位。
QUESTION_RELATIONSHIP_SUPPORT = "support"
QUESTION_RELATIONSHIP_CONTRADICT = "contradict"
QUESTION_RELATIONSHIP_CONTEXT = "context"
QUESTION_RELATIONSHIP_IRRELEVANT = "irrelevant"
QUESTION_RELATIONSHIP_UNCLEAR = "unclear"
QUESTION_RELATIONSHIPS = (
    QUESTION_RELATIONSHIP_SUPPORT,
    QUESTION_RELATIONSHIP_CONTRADICT,
    QUESTION_RELATIONSHIP_CONTEXT,
    QUESTION_RELATIONSHIP_IRRELEVANT,
    QUESTION_RELATIONSHIP_UNCLEAR,
)

IMPACT_DIRECTIONS = ("bullish", "bearish", "neutral", "mixed", "not_applicable", "unclear")
IMPACT_DIRECTION_UNCLEAR = "unclear"
IMPACT_DIRECTION_NOT_APPLICABLE = "not_applicable"

IMPACT_HORIZONS = ("immediate", "short_term", "medium_term", "long_term", "unclear")
IMPACT_HORIZON_UNCLEAR = "unclear"

ASSESSMENT_SOURCE_LLM = "llm"
ASSESSMENT_SOURCE_FALLBACK = "deterministic_fallback"
ASSESSMENT_SOURCES = (ASSESSMENT_SOURCE_LLM, ASSESSMENT_SOURCE_FALLBACK)

# rationale 是給人讀的一句話（繁體中文），不是敘事段落；上限存在的目的是避免它變成第二份報告。
MAX_ASSESSMENT_RATIONALE_CHARS = 120
# 一筆聚合證據最多指出三個子項。超過三個就不再是「實際引用了哪幾則」，而是整份清單。
MAX_ASSESSMENT_SOURCE_ITEM_IDS = 3

# `semantic_assessment` 的完整鍵集合。`excluded_reason` 只有在 irrelevant 時才有內容，
# 但鍵一律存在，讀者不必分辨「沒有這個鍵」與「這個鍵是空的」。
SEMANTIC_ASSESSMENT_KEYS = (
    "relevance_label",
    "relevance_score",
    "relationship_to_question",
    "impact_direction",
    "impact_horizon",
    "rationale",
    "source_item_ids",
    "assessment_source",
    "effective_weight",
    "excluded_reason",
    "assessment_version",
)

# 子項 locator 可保存的鍵。來源沒有提供時一律存 `None`：
# **禁止**以 `fetched_at` 冒充 `published_at`，也禁止猜作者。
SOURCE_ITEM_KEYS = (
    "source_item_id",
    "title",
    "text",
    "url",
    "publisher",
    "platform",
    "author",
    "author_handle",
    "published_at",
    "created_at",
    "indexed_at",
)

# `source_item_id` = 母證據 ID + 兩位序號。刻意不用隨機值或 hash：
# 同一份輸入必須產生同一組 ID，否則「報告引用了哪一則」在每次執行間會漂移。
SOURCE_ITEM_ID_SEPARATOR = "-ITEM-"
_SOURCE_ITEM_ID_RE = re.compile(r"^(?P<parent>.+)%s(?P<index>\d{2})$" % re.escape(SOURCE_ITEM_ID_SEPARATOR))

# Evidence 追加的兩個向後相容欄位（E2）。與 T3／T5 的欄位分開列：
# 這既不是「來源多可信」也不是「屬於哪次執行」，而是「相對這次的問題有多相關」。
EVIDENCE_ASSESSMENT_FIELDS = ("source_items", "semantic_assessment")


def source_item_id(parent_evidence_id: str, index: int) -> str:
    """子項的穩定 ID。``index`` 由 1 起算，兩位補零。"""
    return "%s%s%02d" % (parent_evidence_id, SOURCE_ITEM_ID_SEPARATOR, int(index))


def source_item_parent_id(item_id) -> str:
    """從 ``source_item_id`` 反推母證據 ID；格式不符回空字串。"""
    match = _SOURCE_ITEM_ID_RE.match(str(item_id or "").strip())
    return match.group("parent") if match else ""


def relevance_score_for(label) -> float:
    """標籤 → 分數。這是唯一的映射入口，模型給的任何數值都不會經過這裡。"""
    return RELEVANCE_LABEL_SCORES[str(label)]


def effective_weight(reliability_score, relevance_score, independence_factor) -> float:
    """``reliability_score × relevance_score × independence_factor``，四捨五入並夾在 0–1。

    公式放在契約層而不是計分層，是為了讓 Orchestrator（寫入者）與 validation（稽核者）
    共用同一份算式。兩邊各寫一次的話，稽核只會確認「兩個 bug 一致」。

    `relationship_to_question` 與 `impact_direction` 刻意不在公式裡：它們是解釋欄位，
    一旦參與加權，模型就能透過選標籤間接調整權重。
    """
    def number(value) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if result != result else max(0.0, min(1.0, result))

    product = number(reliability_score) * number(relevance_score) * number(independence_factor)
    return round(max(0.0, min(1.0, product)), 4)


# --------------------------------------------------------------------------------------
# T5 — Citation Gate
# --------------------------------------------------------------------------------------

# Evidence 的 run 歸屬欄位，由 T5 追加在 EVIDENCE_CREDIBILITY_FIELDS 之後。
# 刻意與計分欄位分開列：這不是「這筆證據多可信」，而是「這筆證據屬於哪一次執行」。
# 沒有這個欄位，「Evidence 屬於本次 run」與「跨 run Evidence 不得被引用」只能靠假設，
# 無法真正檢查 —— 一筆從別次執行（或快取 fixture）流進來的證據看起來會完全正常。
EVIDENCE_RUN_FIELDS = ("run_id",)

CITATION_GATE_VERSION = "citation-gate-v1"

# Gate 只有三種結果。FAIL 代表報告會含不可追溯的引用，不得發佈；
# PASS_WITH_WARNINGS 代表可發佈但必須把警告印在報告裡。
GATE_STATUS_PASS = "PASS"
GATE_STATUS_PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
GATE_STATUS_FAIL = "FAIL"
GATE_STATUSES = (GATE_STATUS_PASS, GATE_STATUS_PASS_WITH_WARNINGS, GATE_STATUS_FAIL)

GATE_SEVERITY_ERROR = "error"
GATE_SEVERITY_WARNING = "warning"
GATE_SEVERITIES = (GATE_SEVERITY_ERROR, GATE_SEVERITY_WARNING)

# T5「Structural Citation Gate」的十條規則，順序與文件一致。每個名稱都是 finding 的 `check`
# 欄位值，讓執行記錄可以逐條回答「這條規則本次通過了嗎」。
# T5「Structural Citation Gate」的十條規則，順序與文件一致；E2 在尾端追加第 11 條
# （聚合證據的子項 locator）。每個名稱都是 finding 的 `check` 欄位值，讓執行記錄可以逐條
# 回答「這條規則本次通過了嗎」。
CITATION_GATE_CHECKS = (
    "claim_structure",              # claims.json 本身的形狀（沿用 validate_claims）
    "evidence_id_exists",           # 1. 引用的 Evidence ID 存在
    "evidence_belongs_to_run",      # 2. Evidence 屬於本次 run
    "evidence_required_fields",     # 3. source／fetched_at／content_reference／related_claim_ids
    "evidence_not_rejected",        # 4. 狀態不是 rejected
    "fallback_not_sole_support",    # 5. fallback 不得為主要 Claim 的唯一支持
    "support_contradiction_exclusive",  # 6. 同一 Evidence 不得同時支持與反對
    "claim_has_supporting_evidence",    # 7. 沒有支持證據就必須 insufficient_evidence
    "confidence_within_cap",        # 8. 信心不得突破 credibility／conflict hard cap
    "related_claim_ids_consistent",  # 9. related_claim_ids 與實際引用一致
    "no_cross_run_citation",        # 10. 跨 run Evidence 不得被引用
    # 11（E2）. 被引用的聚合證據（news／announcement／social）必須指出實際依據的子項，
    # 且該子項要有可解析的 http(s) locator。少了它，「引用了 EV-NEWS-001」無法回答
    # 「引用的是五則裡的哪一則」。
    "cited_item_locator",
)

# 語意稽核的八個類別（T5「Semantic Critic」）。同一份清單同時給 LLM Critic 的 prompt／schema
# 與 deterministic 語意偵測使用，因此離線執行也有這八類的覆蓋，不會因為沒有模型就完全沒有語意檢查。
SEMANTIC_CRITIC_CATEGORIES = (
    "over_claim",
    "unsupported",
    "ignored_counter",
    "stale_or_weak",
    "correlation_as_causation",
    "official_statement_as_outcome",
    "onchain_transfer_as_intent",
    "confidence_too_high",
)

# 舊版 Critic 只認得五個類別（其中 `confidence` 是 `confidence_too_high` 的舊名）。
# 模型回傳舊名或近似名稱時正規化成上面的八類，而不是整份稽核作廢。
SEMANTIC_CRITIC_CATEGORY_ALIASES = MappingProxyType({
    "confidence": "confidence_too_high",
    "overclaim": "over_claim",
    "over_claiming": "over_claim",
    "unsupported_claim": "unsupported",
    "ignored_counter_evidence": "ignored_counter",
    "stale": "stale_or_weak",
    "weak_evidence": "stale_or_weak",
    "causation": "correlation_as_causation",
    "correlation_causation": "correlation_as_causation",
    "official_announcement_as_outcome": "official_statement_as_outcome",
    "onchain_intent": "onchain_transfer_as_intent",
})
SEMANTIC_CRITIC_CATEGORY_OTHER = "other"
