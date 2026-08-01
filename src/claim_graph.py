"""Claim–Evidence Graph 與 deterministic confidence 引擎（T4 函式庫層）。

本模組把「分析結論」變成可稽核的物件：每個 Claim 由 Fact → Inference → Conclusion 三層
分離組成，正反證據都保留，信心分數與 verdict 一律由這裡的 deterministic Python 程式計算。

四條邊界（對應 `.kiro/steering/evidence-confidence-standards.md`）：

1. **LLM 只能提案**。模型可以提出 Claim 候選、拆 Fact、分類 supporting／contradicting、
   提出 limitations／invalidation／watchpoints。模型**不能**決定 confidence、不能決定 verdict、
   不能引用不存在或已被 reject 的 Evidence ID、不能把 fallback 當主要確認。
   模型輸出中的 `confidence`／`verdict` 欄位一律被丟棄（見 `_claim_from_proposal`）。
2. **未知 ID 一律拒絕**。`EvidencePool.require()` 對未知或 rejected 的 ID 拋 `ValueError`；
   LLM 路徑失敗時整批提案作廢，改走 deterministic fallback，不做靜默略過。
3. **信心是 heuristic evidence score**，不是校準過的市場正確機率；`type` 固定為
   `CONFIDENCE_TYPE_HEURISTIC`。
4. **Critic 只能降低分數**。正值調整會被拒絕並記在 `limiters`，分數不動。

強度一律用 `quality × claim_relevance × independence_factor`，並先做 source lineage 收斂：
同一條來源鏈（同源轉載）只取該鏈最強的一筆，**20 篇同源低品質轉載不會贏過 1 篇高品質反證**。

本模組不 import `src/credibility.py`（T3 由另一條線實作），只讀取 Evidence 上
`schemas.EVIDENCE_CREDIBILITY_FIELDS` 定義的欄位；欄位缺失時使用保守預設值，不拋例外。
接線到 Orchestrator 由 T4 整合負責，這裡只提供純函式與資料結構。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from types import MappingProxyType
from urllib.parse import urlparse

from src.schemas import (
    CONFIDENCE_COMPONENT_KEYS,
    CONFIDENCE_LEVELS,
    CONFIDENCE_TYPE_HEURISTIC,
    CONFIDENCE_WEIGHTS,
    DEFAULT_CLAIM_TYPE,
    HARD_CAPS,
    HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP,
    MIN_DOMAIN_COVERAGE,
    SINGLE_DOMAIN_CONFIDENCE_CAP,
    SOURCE_TYPE_FALLBACK_FIXTURE,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICTS,
    Claim,
    ClaimConfidence,
    Fact,
)

# 計分版本與 credibility 的 SCORING_VERSION 分開遞增：改 claim 計分不必動 evidence 計分版本。
CLAIM_SCORING_VERSION = "claim-confidence-v1"

CLAIM_TYPE_HYPOTHESIS = "hypothesis_test"
CLAIM_TYPE_COMPARISON = "comparison"

GRAPH_SOURCE_LLM = "llm"
GRAPH_SOURCE_FALLBACK = "deterministic_fallback"

# data_type → domain。domain 是「研究領域覆蓋度」的單位，不是資料來源數量。
DOMAIN_BY_DATA_TYPE = MappingProxyType({
    "market": "market",
    "price_history": "market",
    "ohlcv": "market",
    "vegas_channel": "market",
    "derivatives": "derivatives",
    "long_short_ratio": "derivatives",
    "news": "news",
    "announcement": "news",
    "social": "social",
    "onchain": "onchain",
    "whale": "onchain",
    "tvl": "onchain",
    "macro": "macro",
})
DEFAULT_DOMAIN = "other"

# 沒有 research plan 時的必要領域；domain_coverage 一律以「必要領域」為分母，
# 這樣缺少 on-chain 或社群面時覆蓋度會誠實地掉下來。
DEFAULT_REQUIRED_DOMAINS = ("market", "news", "social", "onchain")
# domain coverage 分母的下限。plan 可以縮減研究範圍，但分母低於這個值時 coverage 會被
# 少數領域輕易填滿，等於讓 plan（可能來自模型）間接抬高 confidence。取 3 是因為
# 1 / 3 = 0.333 已低於 MIN_DOMAIN_COVERAGE (0.4)：任何單一領域的 Claim 都無法
# 靠改寫 plan 通過覆蓋率門檻。
MIN_REQUIRED_DOMAIN_COUNT = 3

# 缺欄位時的保守預設：品質不明就當偏低，相關性不明就當一半，獨立性不明才給 1.0
# （獨立性由 lineage 收斂另外處理，這裡不重複懲罰）。
DEFAULT_QUALITY = 0.20
DEFAULT_CLAIM_RELEVANCE = 0.50
DEFAULT_INDEPENDENCE = 1.00

# 需要幾條互相獨立的來源鏈才算「來源多元」。
SOURCE_DIVERSITY_TARGET = 3

# 「高品質」門檻：品質高、獨立性足夠、且不是 fallback fixture。
HIGH_QUALITY_SCORE_THRESHOLD = 0.70
HIGH_QUALITY_INDEPENDENCE_THRESHOLD = 0.50

# verdict 由支持／反對強度比決定，不由模型決定。
VERDICT_SUPPORTED_RATIO = 0.85
VERDICT_PARTIAL_RATIO = 0.65
VERDICT_MIXED_RATIO = 0.35

CONFIDENCE_LEVEL_HIGH_MIN = 0.70
CONFIDENCE_LEVEL_MEDIUM_MIN = 0.45

# 資料不足時仍要有分數欄位，但不得看起來像可用的判斷。
INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP = 0.35

# limiters 只會出現這些名稱；每個名稱代表一條實際生效的規則。
LIMITER_SINGLE_DOMAIN = "single_supporting_domain"
LIMITER_HIGH_QUALITY_CONFLICT = "high_quality_conflict"
LIMITER_SINGLE_SECONDARY_NEWS = "single_secondary_news_source"
LIMITER_FALLBACK_ONLY = "fallback_only_primary_support"
LIMITER_NO_SUPPORT = "no_supporting_evidence"
LIMITER_LOW_DOMAIN_COVERAGE = "domain_coverage_below_minimum"
LIMITER_CRITIC_REDUCTION = "critic_confidence_reduction"
LIMITER_CRITIC_POSITIVE_REJECTED = "critic_positive_adjustment_rejected"
# plan 要求的領域數低於 MIN_REQUIRED_DOMAIN_COUNT，coverage 分母已被補到下限。
LIMITER_PLAN_SCOPE_FLOOR = "plan_scope_denominator_floor"

KNOWN_LIMITERS = (
    LIMITER_SINGLE_DOMAIN,
    LIMITER_HIGH_QUALITY_CONFLICT,
    LIMITER_SINGLE_SECONDARY_NEWS,
    LIMITER_FALLBACK_ONLY,
    LIMITER_NO_SUPPORT,
    LIMITER_LOW_DOMAIN_COVERAGE,
    LIMITER_CRITIC_REDUCTION,
    LIMITER_CRITIC_POSITIVE_REJECTED,
    LIMITER_PLAN_SCOPE_FLOOR,
)

CLAIM_TIMEOUT_SECONDS = 60.0
CLAIM_SCHEMA_NAME = "claim_graph"

# Fact 欄位不得混寫推論或預測。這些詞出現在 Fact 裡就代表分層失敗，整批提案作廢。
_INFERENCE_MARKERS = (
    "因此", "所以", "推測", "預期", "將會", "料將", "意味著", "代表未來", "因而", "顯示未來",
    "therefore", "will likely", "suggests that", "implies", "expected to", "because of this",
)

CLAIM_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "claim_type": {"type": "string"},
                    "hypothesis_id": {"type": "string"},
                    "facts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "statement": {"type": "string"},
                                "evidence_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["statement", "evidence_ids"],
                            "additionalProperties": False,
                        },
                    },
                    "inference": {"type": "string"},
                    "conclusion": {"type": "string"},
                    "supporting_evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "contradicting_evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "limitations": {"type": "array", "items": {"type": "string"}},
                    "invalidation_conditions": {"type": "array", "items": {"type": "string"}},
                    "watchpoints": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "statement", "facts", "inference", "conclusion",
                    "supporting_evidence_ids", "contradicting_evidence_ids",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}

_CLAIM_PROMPT = (
    "你是加密貨幣研究分析師。只能使用下面提供的 Evidence，請用繁體中文回覆。\n\n"
    "每個 Claim 必須把三層分開，不得混寫：\n"
    "1. facts：資料直接顯示的觀察，每一條都要附 evidence_ids。**不得**出現「因此」「預期」"
    "「將會」等推論或預測用語。\n"
    "2. inference：由 facts 推導出的解釋，寫在 inference 欄位。\n"
    "3. conclusion：對研究問題的回答，寫在 conclusion 欄位。\n\n"
    "supporting_evidence_ids 與 contradicting_evidence_ids 只能填提供清單中的 evidence_id；"
    "捏造或猜測 ID 會使整批提案作廢。有高品質反方證據時必須列進 contradicting_evidence_ids，"
    "不得隱藏。不要輸出 confidence 或 verdict —— 這兩項由程式計算。\n\n"
)


# --------------------------------------------------------------------------------------
# Evidence 正規化與查詢
# --------------------------------------------------------------------------------------

def _as_dict(item) -> dict:
    """把 Evidence dataclass、dict 或任意物件轉成純 dict，不改寫原始資料。"""
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    return {key: getattr(item, key) for key in dir(item) if not key.startswith("_")}


def _clamp(value, default: float = 0.0) -> float:
    """把任意輸入夾到 0–1；非數字或 NaN 一律退回 default，不拋例外。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(0.0, min(1.0, number))


def normalize_evidence(items) -> list:
    """把一批 Evidence 轉成計分用的正規化 record，缺欄位時填保守預設值。"""
    records = []
    for item in items or []:
        raw = _as_dict(item)
        evidence_id = str(raw.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        data_type = str(raw.get("data_type") or "").strip()
        breakdown = raw.get("score_breakdown") or {}
        if not isinstance(breakdown, dict):
            breakdown = {}
        source_type = str(raw.get("source_type") or "unknown").strip() or "unknown"
        limiters = raw.get("score_limiters") or []
        if not isinstance(limiters, (list, tuple)):
            limiters = []
        records.append({
            "evidence_id": evidence_id,
            "source": str(raw.get("source") or ""),
            "source_url": str(raw.get("source_url") or ""),
            "data_type": data_type,
            "domain": DOMAIN_BY_DATA_TYPE.get(data_type, DEFAULT_DOMAIN),
            "source_type": source_type,
            "verification_status": str(raw.get("verification_status") or "unverified"),
            "quality": _quality(raw, breakdown, source_type, list(limiters)),
            "claim_relevance": _relevance(raw),
            "independence_factor": _clamp(raw.get("independence_factor"), DEFAULT_INDEPENDENCE),
            "lineage_id": _lineage_id(raw),
            "is_fallback": _is_fallback(source_type, list(limiters), raw),
            "score_limiters": [str(name) for name in limiters],
        })
    return records


def _quality(raw: dict, breakdown: dict, source_type: str, limiters: list) -> float:
    """取 T3 算好的最終分數；沒有 T3 欄位時退回既有 reliability_score。"""
    for key in ("final_score", "score", "raw_score"):
        if key in breakdown:
            quality = _clamp(breakdown.get(key), DEFAULT_QUALITY)
            break
    else:
        quality = _clamp(raw.get("reliability_score"), DEFAULT_QUALITY)
    # fallback fixture 的上限由 hard cap 決定，即使上游忘了套用也不得突破。
    if source_type == SOURCE_TYPE_FALLBACK_FIXTURE or SOURCE_TYPE_FALLBACK_FIXTURE in limiters:
        quality = min(quality, HARD_CAPS[SOURCE_TYPE_FALLBACK_FIXTURE])
    return round(quality, 4)


def _relevance(raw: dict) -> float:
    """claim_relevance 預設 0.0 代表「還沒有人填」，這時給保守的 0.50。"""
    value = raw.get("claim_relevance")
    relevance = _clamp(value, DEFAULT_CLAIM_RELEVANCE)
    if relevance <= 0.0:
        return DEFAULT_CLAIM_RELEVANCE
    return round(relevance, 4)


def _lineage_id(raw: dict) -> str:
    """來源鏈識別：優先用 T3 的 source_lineage_id，其次 URL host，最後 source 名稱。"""
    lineage = str(raw.get("source_lineage_id") or "").strip()
    if lineage:
        return lineage
    reference = raw.get("content_reference") or {}
    if isinstance(reference, dict):
        for key in ("canonical_url", "original_source_url"):
            candidate = str(reference.get(key) or "").strip()
            if candidate:
                return _host(candidate)
    url = str(raw.get("source_url") or "").strip()
    if url:
        return _host(url)
    source = str(raw.get("source") or "").strip()
    return source or str(raw.get("evidence_id") or "")


def _host(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or parsed.path).lower()
    return host[4:] if host.startswith("www.") else host


def _is_fallback(source_type: str, limiters: list, raw: dict) -> bool:
    if source_type == SOURCE_TYPE_FALLBACK_FIXTURE or SOURCE_TYPE_FALLBACK_FIXTURE in limiters:
        return True
    return bool(raw.get("is_fallback"))


class EvidencePool:
    """本次 run 的 Evidence 索引；rejected 的證據一律不得進入 Claim。"""

    def __init__(self, evidence) -> None:
        self._records = {}
        self.rejected_ids = []
        for record in normalize_evidence(evidence):
            if record["verification_status"] == "rejected":
                self.rejected_ids.append(record["evidence_id"])
                continue
            self._records.setdefault(record["evidence_id"], record)
        self.rejected_ids = sorted(set(self.rejected_ids))

    def __len__(self) -> int:
        return len(self._records)

    @property
    def active_ids(self) -> list:
        return list(self._records)

    def get(self, evidence_id: str):
        return self._records.get(str(evidence_id))

    def require(self, evidence_ids) -> list:
        """回傳去重後的有效 ID（保留輸入順序）。未知或 rejected 的 ID 一律拋錯。"""
        resolved, seen = [], set()
        unknown, rejected = [], []
        for raw_id in evidence_ids or []:
            evidence_id = str(raw_id).strip()
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            if evidence_id in self._records:
                resolved.append(evidence_id)
            elif evidence_id in self.rejected_ids:
                rejected.append(evidence_id)
            else:
                unknown.append(evidence_id)
        if unknown:
            raise ValueError("unknown evidence ids: " + ", ".join(sorted(unknown)))
        if rejected:
            raise ValueError("rejected evidence cannot be cited: " + ", ".join(sorted(rejected)))
        return resolved

    def domain(self, evidence_id: str) -> str:
        record = self.get(evidence_id)
        return record["domain"] if record else DEFAULT_DOMAIN

    def quality(self, evidence_id: str) -> float:
        record = self.get(evidence_id)
        return record["quality"] if record else 0.0

    def strength(self, evidence_id: str) -> float:
        """quality × claim_relevance × independence_factor，不是證據數量。"""
        record = self.get(evidence_id)
        if not record:
            return 0.0
        return round(record["quality"] * record["claim_relevance"] * record["independence_factor"], 6)

    def lineage(self, evidence_id: str) -> str:
        record = self.get(evidence_id)
        return record["lineage_id"] if record else str(evidence_id)

    def is_fallback(self, evidence_id: str) -> bool:
        record = self.get(evidence_id)
        return bool(record and record["is_fallback"])

    def is_high_quality(self, evidence_id: str) -> bool:
        record = self.get(evidence_id)
        if not record or record["is_fallback"]:
            return False
        return (record["quality"] >= HIGH_QUALITY_SCORE_THRESHOLD
                and record["independence_factor"] >= HIGH_QUALITY_INDEPENDENCE_THRESHOLD)

    def lineage_groups(self, evidence_ids) -> dict:
        """把 ID 依來源鏈分組；同源轉載只會落在同一組。"""
        groups = {}
        for evidence_id in evidence_ids:
            groups.setdefault(self.lineage(evidence_id), []).append(evidence_id)
        return groups

    def collapsed_strength(self, evidence_ids):
        """每條來源鏈只採計最強的一筆，回傳 (總強度, 代表 ID 清單)。"""
        groups = self.lineage_groups(evidence_ids)
        representatives = []
        for lineage in sorted(groups):
            best = sorted(groups[lineage], key=lambda item: (-self.strength(item), item))[0]
            representatives.append(best)
        total = round(sum(self.strength(item) for item in representatives), 6)
        return total, representatives


# --------------------------------------------------------------------------------------
# Deterministic confidence
# --------------------------------------------------------------------------------------

def _confidence_level(score: float) -> str:
    if score >= CONFIDENCE_LEVEL_HIGH_MIN:
        return CONFIDENCE_LEVELS[2]
    if score >= CONFIDENCE_LEVEL_MEDIUM_MIN:
        return CONFIDENCE_LEVELS[1]
    return CONFIDENCE_LEVELS[0]


def _verdict_from_ratio(ratio: float) -> str:
    if ratio >= VERDICT_SUPPORTED_RATIO:
        return "supported"
    if ratio >= VERDICT_PARTIAL_RATIO:
        return "partially_supported"
    if ratio >= VERDICT_MIXED_RATIO:
        return "mixed"
    return "contradicted"


def evaluate_claim(
    pool: EvidencePool,
    *,
    supporting_ids=(),
    contradicting_ids=(),
    required_domains=None,
    invalidation_conditions=(),
    critic_adjustment: float = 0.0,
) -> dict:
    """計算一個 Claim 的 verdict 與 confidence。

    這是本模組唯一的計分入口：相同輸入永遠得到相同輸出，LLM 不參與。
    未知或 rejected 的 Evidence ID 會直接拋 `ValueError`。
    """
    supporting = pool.require(supporting_ids)
    contradicting = pool.require(contradicting_ids)
    overlap = sorted(set(supporting) & set(contradicting))
    if overlap:
        raise ValueError("evidence cannot support and contradict the same claim: " + ", ".join(overlap))

    # `required_domains` 來自 research plan，而 plan 可能由模型產生。直接把它當成 coverage 的
    # 分母，等於讓 plan 只要少要求幾個領域就能推高 confidence —— 那是 LLM 間接決定分數，
    # 違反「confidence 一律由 deterministic 程式計算」這條邊界。
    #
    # 修法刻意不是忽略 plan：題目不相關的領域本來就不該扣分（見
    # `test_plan_hypotheses_produce_separate_hypothesis_claims`）。改成給分母一個下限——
    # plan 可以縮減研究範圍，但不能把分母縮到讓單一領域就構成「全覆蓋」。
    # 下限取 MIN_REQUIRED_DOMAIN_COUNT 而非預設的四領域，是為了讓
    # `len(covered) == 1` 的 coverage 必定低於 MIN_DOMAIN_COVERAGE，也就是任何
    # 單一領域的 Claim 都無法靠改寫 plan 通過覆蓋率門檻。
    requested = tuple(dict.fromkeys(
        str(name) for name in (required_domains or ()) if str(name).strip()))
    required = requested or DEFAULT_REQUIRED_DOMAINS
    scope_floor_applied = len(required) < MIN_REQUIRED_DOMAIN_COUNT
    if scope_floor_applied:
        padded = tuple(dict.fromkeys(required + DEFAULT_REQUIRED_DOMAINS))
        required = padded[:MIN_REQUIRED_DOMAIN_COUNT]

    support_strength, support_reps = pool.collapsed_strength(supporting)
    contradiction_strength, _ = pool.collapsed_strength(contradicting)
    support_groups = pool.lineage_groups(supporting)

    considered = supporting + contradicting
    covered = sorted({pool.domain(item) for item in considered} & set(required))
    domain_coverage = round(len(covered) / len(required), 4) if required else 0.0

    denominator = sum(pool.strength(item) for item in support_reps)
    if denominator > 0:
        weighted_quality = sum(pool.strength(item) * pool.quality(item) for item in support_reps) / denominator
    elif support_reps:
        weighted_quality = sum(pool.quality(item) for item in support_reps) / len(support_reps)
    else:
        weighted_quality = 0.0

    total_strength = support_strength + contradiction_strength
    components = {
        "weighted_evidence_quality": round(weighted_quality, 4),
        "domain_coverage": domain_coverage,
        "source_diversity": round(min(1.0, len(support_groups) / SOURCE_DIVERSITY_TARGET), 4),
        "signal_consistency": round(support_strength / total_strength, 4) if total_strength else 0.0,
        "counter_evidence_coverage": 1.0 if contradicting else (0.5 if list(invalidation_conditions) else 0.0),
    }
    score = round(sum(CONFIDENCE_WEIGHTS[key] * components[key] for key in CONFIDENCE_COMPONENT_KEYS), 4)

    limiters = []
    supporting_domains = sorted({pool.domain(item) for item in supporting})

    if scope_floor_applied:
        # 不影響分數（分母已經在上面補到下限），只留痕跡：讀者要能看出這個 coverage
        # 是對照補足後的分母算出來的，而不是 plan 原本要求的範圍。
        limiters.append(LIMITER_PLAN_SCOPE_FLOOR)

    if supporting and len(supporting_domains) == 1:
        limiters.append(LIMITER_SINGLE_DOMAIN)
        score = min(score, SINGLE_DOMAIN_CONFIDENCE_CAP)

    high_quality_support = any(pool.is_high_quality(item) for item in supporting)
    high_quality_contradiction = any(pool.is_high_quality(item) for item in contradicting)
    if high_quality_support and high_quality_contradiction:
        limiters.append(LIMITER_HIGH_QUALITY_CONFLICT)
        score = min(score, HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP)

    if len(support_groups) == 1 and supporting and all(
        (pool.get(item) or {}).get("source_type") == "secondary_media" for item in supporting
    ):
        limiters.append(LIMITER_SINGLE_SECONDARY_NEWS)
        score = min(score, HARD_CAPS["single_secondary_news_source"])

    insufficient = False
    if not supporting:
        limiters.append(LIMITER_NO_SUPPORT)
        insufficient = True
    elif all(pool.is_fallback(item) for item in supporting):
        limiters.append(LIMITER_FALLBACK_ONLY)
        insufficient = True
    if domain_coverage < MIN_DOMAIN_COVERAGE:
        limiters.append(LIMITER_LOW_DOMAIN_COVERAGE)
        insufficient = True

    if insufficient:
        verdict = VERDICT_INSUFFICIENT_EVIDENCE
        score = min(score, INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP)
    else:
        verdict = _verdict_from_ratio(components["signal_consistency"])
        if verdict == "supported" and LIMITER_HIGH_QUALITY_CONFLICT in limiters:
            verdict = "partially_supported"

    adjustment = _to_float(critic_adjustment)
    if adjustment > 0:
        # Critic 只能下調信心。正值一律拒絕，並留下痕跡讓讀者知道有人試圖加分。
        limiters.append(LIMITER_CRITIC_POSITIVE_REJECTED)
    elif adjustment < 0:
        score = round(max(0.0, score + adjustment), 4)
        limiters.append(LIMITER_CRITIC_REDUCTION)

    score = round(max(0.0, min(1.0, score)), 4)
    confidence = ClaimConfidence(
        score=score,
        level=_confidence_level(score),
        type=CONFIDENCE_TYPE_HEURISTIC,
        components=components,
        limiters=limiters,
    )
    if verdict not in VERDICTS:  # 防止未來新增規則時漏掉合法 verdict 集合
        raise ValueError("computed verdict is not allowed: %r" % verdict)
    return {
        "verdict": verdict,
        "confidence": asdict(confidence),
        "supporting_evidence_ids": supporting,
        "contradicting_evidence_ids": contradicting,
        "support_strength": round(support_strength, 4),
        "contradiction_strength": round(contradiction_strength, 4),
        "supporting_domains": supporting_domains,
        "covered_domains": covered,
        # `required_domains` 是實際用來算 coverage 的分母；`plan_requested_domains` 是 plan
        # 原本要求的範圍。兩者不同時代表分母下限生效，讀者可據此還原計算過程。
        "required_domains": list(required),
        "plan_requested_domains": list(requested),
        "independent_support_chains": len(support_groups),
        "scoring_version": CLAIM_SCORING_VERSION,
    }


def _to_float(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number != number else number


# --------------------------------------------------------------------------------------
# Claim 組裝
# --------------------------------------------------------------------------------------

def _claim_id(index: int) -> str:
    return "CL-%03d" % index


def _clean_texts(values) -> list:
    if isinstance(values, str):
        values = [values]
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def _mixed_layer_marker(statement: str):
    lowered = statement.lower()
    for marker in _INFERENCE_MARKERS:
        if marker in statement or marker in lowered:
            return marker
    return None


def build_claim(
    pool: EvidencePool,
    claim_id: str,
    *,
    statement: str,
    facts,
    inference: str,
    conclusion: str,
    supporting_ids=(),
    contradicting_ids=(),
    claim_type: str = DEFAULT_CLAIM_TYPE,
    limitations=(),
    invalidation_conditions=(),
    watchpoints=(),
    required_domains=None,
    critic_adjustment: float = 0.0,
) -> tuple:
    """組出一個 Claim dict（含 deterministic verdict／confidence）與其評估細節。"""
    invalidation = _clean_texts(invalidation_conditions)
    assessment = evaluate_claim(
        pool,
        supporting_ids=supporting_ids,
        contradicting_ids=contradicting_ids,
        required_domains=required_domains,
        invalidation_conditions=invalidation,
        critic_adjustment=critic_adjustment,
    )
    fact_objects = []
    for entry in facts or []:
        fact_ids = pool.require(entry.get("evidence_ids"))
        fact_objects.append(asdict(Fact(statement=str(entry.get("statement") or "").strip(),
                                        evidence_ids=fact_ids)))
    claim = Claim(
        claim_id=claim_id,
        statement=str(statement).strip(),
        claim_type=str(claim_type or DEFAULT_CLAIM_TYPE),
        verdict=assessment["verdict"],
        facts=fact_objects,
        inference=str(inference or "").strip(),
        conclusion=str(conclusion or "").strip(),
        supporting_evidence_ids=assessment["supporting_evidence_ids"],
        contradicting_evidence_ids=assessment["contradicting_evidence_ids"],
        confidence=ClaimConfidence(**assessment["confidence"]),
        limitations=_clean_texts(limitations),
        invalidation_conditions=invalidation,
        watchpoints=_clean_texts(watchpoints),
    )
    return asdict(claim), assessment


# --------------------------------------------------------------------------------------
# LLM 提案路徑（模型只提案，不計分）
# --------------------------------------------------------------------------------------

_MAX_SUMMARY_ITEMS = 6


def _summarise_content(content) -> dict:
    """把 Evidence content 壓成 prompt 用摘要：移除長數列，保留文字與純量。"""
    if not isinstance(content, dict):
        return {"value": str(content)[:200]}
    summary = {}
    for key, value in content.items():
        if isinstance(value, (int, float, bool)) or value is None:
            summary[key] = value
        elif isinstance(value, str):
            summary[key] = value[:200]
        elif isinstance(value, (list, tuple)):
            if len(value) > _MAX_SUMMARY_ITEMS and all(isinstance(item, (int, float)) for item in value):
                summary[key] = {"omitted_series_length": len(value)}
            else:
                summary[key] = [str(item)[:120] for item in list(value)[:_MAX_SUMMARY_ITEMS]]
        elif isinstance(value, dict):
            summary[key] = {inner: str(item)[:120] for inner, item in list(value.items())[:_MAX_SUMMARY_ITEMS]}
    return summary


def llm_evidence_payload(pool: EvidencePool, evidence) -> list:
    """給模型看的 Evidence 清單：帶 ID、領域與品質，內容摘要後不含長數列。"""
    raw_by_id = {}
    for item in evidence or []:
        raw = _as_dict(item)
        raw_by_id[str(raw.get("evidence_id") or "")] = raw
    payload = []
    for evidence_id in pool.active_ids:
        record = pool.get(evidence_id)
        raw = raw_by_id.get(evidence_id, {})
        payload.append({
            "evidence_id": evidence_id,
            "source": record["source"],
            "source_url": record["source_url"],
            "data_type": record["data_type"],
            "domain": record["domain"],
            "source_type": record["source_type"],
            "verification_status": record["verification_status"],
            "quality": record["quality"],
            "is_fallback": record["is_fallback"],
            "content": _summarise_content(raw.get("content")),
        })
    return payload


def build_claim_prompt(coins, question: str, pool: EvidencePool, evidence, hypotheses=()) -> str:
    payload = {
        "coins": list(coins),
        "question": question,
        "hypotheses": [
            {"hypothesis_id": item.get("hypothesis_id", ""), "statement": item.get("statement", "")}
            for item in hypotheses or []
        ],
        "evidence": llm_evidence_payload(pool, evidence),
    }
    return _CLAIM_PROMPT + json.dumps(payload, ensure_ascii=False)


def _validate_proposal(pool: EvidencePool, proposal) -> dict:
    """驗證單一 Claim 提案；任何違規都拋 `ValueError`，由呼叫端整批降級。"""
    if not isinstance(proposal, dict):
        raise ValueError("claim proposal must be an object")
    statement = str(proposal.get("statement") or "").strip()
    if not statement:
        raise ValueError("claim proposal is missing statement")
    inference = str(proposal.get("inference") or "").strip()
    conclusion = str(proposal.get("conclusion") or "").strip()
    if not inference or not conclusion:
        raise ValueError("claim proposal must separate inference and conclusion")

    raw_facts = proposal.get("facts")
    if not isinstance(raw_facts, list) or not raw_facts:
        raise ValueError("claim proposal must contain at least one traceable fact")
    facts = []
    for entry in raw_facts:
        if not isinstance(entry, dict):
            raise ValueError("fact must be an object with statement and evidence_ids")
        fact_statement = str(entry.get("statement") or "").strip()
        if not fact_statement:
            raise ValueError("fact is missing statement")
        marker = _mixed_layer_marker(fact_statement)
        if marker:
            raise ValueError("fact mixes inference or forecast language: %r" % marker)
        if fact_statement in (inference, conclusion):
            raise ValueError("fact duplicates the inference or conclusion layer")
        fact_ids = pool.require(entry.get("evidence_ids"))
        if not fact_ids:
            raise ValueError("fact must cite at least one evidence id")
        facts.append({"statement": fact_statement, "evidence_ids": fact_ids})

    supporting = pool.require(proposal.get("supporting_evidence_ids"))
    contradicting = pool.require(proposal.get("contradicting_evidence_ids"))
    return {
        "statement": statement,
        "claim_type": str(proposal.get("claim_type") or DEFAULT_CLAIM_TYPE),
        "hypothesis_id": str(proposal.get("hypothesis_id") or ""),
        "facts": facts,
        "inference": inference,
        "conclusion": conclusion,
        "supporting_ids": supporting,
        "contradicting_ids": contradicting,
        "limitations": _clean_texts(proposal.get("limitations")),
        "invalidation_conditions": _clean_texts(proposal.get("invalidation_conditions")),
        "watchpoints": _clean_texts(proposal.get("watchpoints")),
    }


def propose_claims(client, coins, question: str, pool: EvidencePool, evidence, hypotheses=(),
                   timeout_seconds: float = CLAIM_TIMEOUT_SECONDS) -> list:
    """呼叫注入的 `LLMClient` 取得 Claim 提案並驗證；不合法就拋 `ValueError`。"""
    response = client.generate_json(
        prompt=build_claim_prompt(coins, question, pool, evidence, hypotheses),
        schema=CLAIM_PROPOSAL_SCHEMA,
        schema_name=CLAIM_SCHEMA_NAME,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(response, dict):
        raise ValueError("claim proposal response must be a JSON object")
    proposals = response.get("claims")
    if not isinstance(proposals, list) or not proposals:
        raise ValueError("claim proposal response contains no claims")
    return [_validate_proposal(pool, proposal) for proposal in proposals]


# --------------------------------------------------------------------------------------
# Deterministic fallback
# --------------------------------------------------------------------------------------

def _active_signals(pool: EvidencePool, signals) -> list:
    cleaned = []
    for signal in signals or []:
        record = _as_dict(signal)
        evidence_id = str(record.get("evidence_id") or "").strip()
        if not pool.get(evidence_id):
            continue
        side = str(record.get("side") or "neutral")
        cleaned.append({
            "side": side if side in ("bull", "bear", "neutral") else "neutral",
            "text": str(record.get("text") or "").strip(),
            "evidence_id": evidence_id,
            "weight": _to_float(record.get("weight"), 0.0),
        })
    return cleaned


def _insufficient_claim(pool: EvidencePool, claim_id: str, coin_label: str, reason: str,
                        required_domains, critic_adjustment: float) -> tuple:
    return build_claim(
        pool,
        claim_id,
        statement="%s：本次證據不足以形成方向判斷" % coin_label,
        facts=[],
        inference="沒有足夠的有效證據可供推論，因此不產生方向性解釋。",
        conclusion="輸出 insufficient_evidence，不給方向。",
        supporting_ids=(),
        contradicting_ids=(),
        limitations=[reason, "信心分數為 heuristic evidence score，不是市場正確機率。"],
        invalidation_conditions=["補齊缺失領域的證據後需重新評估。"],
        watchpoints=["重新蒐集缺失領域的證據。"],
        required_domains=required_domains,
        critic_adjustment=critic_adjustment,
    )


def fallback_claims(pool: EvidencePool, coins, question: str, signals=None, stance=None,
                    hypotheses=(), required_domains=None, critic_adjustment: float = 0.0) -> tuple:
    """LLM 不可用或提案不合法時的保守 Claim：只用既有訊號盤點與有效 Evidence。"""
    coin_label = "／".join(coins) if coins else "標的"
    claims, assessments = [], []
    active = _active_signals(pool, signals)
    bull = [item for item in active if item["side"] == "bull"]
    bear = [item for item in active if item["side"] == "bear"]

    if not pool.active_ids:
        claim, assessment = _insufficient_claim(
            pool, _claim_id(1), coin_label, "本次執行沒有任何有效證據。", required_domains, critic_adjustment)
        return [claim], [assessment]
    if not bull and not bear:
        claim, assessment = _insufficient_claim(
            pool, _claim_id(1), coin_label,
            "本次證據未產生任何方向性訊號（%d 筆有效證據）。" % len(pool.active_ids),
            required_domains, critic_adjustment)
        return [claim], [assessment]

    bull_weight = round(sum(item["weight"] for item in bull), 2)
    bear_weight = round(sum(item["weight"] for item in bear), 2)
    if bear_weight > bull_weight:
        support_side, oppose_side = bear, bull
        side_text, side_weights = "偏空", (bear_weight, bull_weight)
    else:
        support_side, oppose_side = bull, bear
        side_text, side_weights = "偏多", (bull_weight, bear_weight)

    stance_basis = ""
    if isinstance(stance, dict):
        stance_basis = str(stance.get("basis") or "")

    facts = [{"statement": item["text"], "evidence_ids": [item["evidence_id"]]} for item in support_side]
    limitations = ["本 Claim 由 deterministic fallback 產生，未經模型敘事分層。"]
    if bull_weight == bear_weight:
        limitations.append("多空權重相同（各 %.2f），支持側以多方為預設，方向不具決定性。" % bull_weight)
    if stance_basis:
        limitations.append("立場判定依據：%s" % stance_basis)

    claim, assessment = build_claim(
        pool,
        _claim_id(1),
        statement="%s 目前訊號%s（支持側權重 %.2f，反向側 %.2f）" % (coin_label, side_text, side_weights[0], side_weights[1]),
        facts=facts,
        inference="支持側與反向側的權重差距構成本次方向判讀；權重來自既有訊號盤點，非模型自由生成。",
        conclusion="針對「%s」，本次證據%s，但仍保留反向側證據供讀者檢視。" % (question or "研究問題", side_text),
        supporting_ids=[item["evidence_id"] for item in support_side],
        contradicting_ids=[item["evidence_id"] for item in oppose_side],
        limitations=limitations,
        invalidation_conditions=["反向側權重超過支持側時，本判斷即被推翻。"],
        watchpoints=[item["text"] for item in oppose_side] or ["持續追蹤反向訊號是否出現。"],
        required_domains=required_domains,
        critic_adjustment=critic_adjustment,
    )
    claims.append(claim)
    assessments.append(assessment)

    for index, hypothesis in enumerate(hypotheses or (), start=2):
        record = _as_dict(hypothesis)
        statement = str(record.get("statement") or "").strip()
        if not statement:
            continue
        hypothesis_claim, hypothesis_assessment = build_claim(
            pool,
            _claim_id(index),
            statement=statement,
            claim_type=CLAIM_TYPE_HYPOTHESIS,
            facts=facts,
            inference="以本次訊號盤點的淨方向近似假設方向，支持與反對強度分別計算。",
            conclusion="假設的檢驗結果由支持／反對強度比決定，未由模型指定。",
            supporting_ids=[item["evidence_id"] for item in support_side],
            contradicting_ids=[item["evidence_id"] for item in oppose_side],
            limitations=["deterministic fallback 以整體訊號淨方向近似假設方向，未做語意分類。"],
            invalidation_conditions=_clean_texts(record.get("falsification_conditions"))
            or ["反向側強度超過支持側時，假設即被推翻。"],
            watchpoints=[item["text"] for item in oppose_side] or ["持續追蹤反向訊號是否出現。"],
            required_domains=required_domains,
            critic_adjustment=critic_adjustment,
        )
        hypothesis_assessment = dict(hypothesis_assessment, hypothesis_id=str(record.get("hypothesis_id") or ""))
        claims.append(hypothesis_claim)
        assessments.append(hypothesis_assessment)

    return claims, assessments


# --------------------------------------------------------------------------------------
# 對外入口
# --------------------------------------------------------------------------------------

def _plan_dict(plan) -> dict:
    if plan is None:
        return {}
    if isinstance(plan, dict):
        return plan
    if is_dataclass(plan) and not isinstance(plan, type):
        return asdict(plan)
    return {}


def _coin_list(coin) -> list:
    if coin is None:
        return []
    if isinstance(coin, str):
        return [coin.upper()] if coin.strip() else []
    return [str(item).upper() for item in coin if str(item).strip()]


def build_claim_graph(coin, question: str, evidence, *, signals=None, stance=None, plan=None,
                      client=None, critique=None, timeout_seconds: float = CLAIM_TIMEOUT_SECONDS) -> dict:
    """建立完整 Claim Graph。

    `client` 為注入的 `src.ports.LLMClient`（測試用 MockLLMClient）。模型失敗、JSON 不合法、
    沒有 claims、引用未知或 rejected 的 Evidence ID、或 Fact 混寫推論時，一律整批作廢並改走
    deterministic fallback；confidence 與 verdict 永遠由 `evaluate_claim()` 計算。
    """
    pool = EvidencePool(evidence)
    plan_data = _plan_dict(plan)
    coins = _coin_list(coin) or _coin_list(plan_data.get("coins"))
    required_domains = plan_data.get("required_domains") or None
    hypotheses = [_as_dict(item) for item in (plan_data.get("hypotheses") or ())]

    critic_adjustment = 0.0
    if isinstance(critique, dict):
        critic_adjustment = _to_float(critique.get("confidence_adjustment"), 0.0)

    source = GRAPH_SOURCE_FALLBACK
    fallback_reason = ""
    claims, assessments = [], []

    if client is None:
        fallback_reason = "no llm client supplied"
    else:
        try:
            proposals = propose_claims(client, coins, question, pool, evidence, hypotheses, timeout_seconds)
            for index, proposal in enumerate(proposals, start=1):
                claim, assessment = build_claim(
                    pool,
                    _claim_id(index),
                    statement=proposal["statement"],
                    claim_type=proposal["claim_type"],
                    facts=proposal["facts"],
                    inference=proposal["inference"],
                    conclusion=proposal["conclusion"],
                    supporting_ids=proposal["supporting_ids"],
                    contradicting_ids=proposal["contradicting_ids"],
                    limitations=proposal["limitations"],
                    invalidation_conditions=proposal["invalidation_conditions"],
                    watchpoints=proposal["watchpoints"],
                    required_domains=required_domains,
                    critic_adjustment=critic_adjustment,
                )
                if proposal["hypothesis_id"] or proposal["claim_type"] == CLAIM_TYPE_HYPOTHESIS:
                    assessment = dict(assessment, hypothesis_id=proposal["hypothesis_id"])
                claims.append(claim)
                assessments.append(assessment)
            source = GRAPH_SOURCE_LLM
        except Exception as error:  # noqa: BLE001 - 任何模型端問題都必須降級而不是中斷流程
            claims, assessments = [], []
            fallback_reason = "%s: %s" % (type(error).__name__, error)

    if not claims:
        claims, assessments = fallback_claims(
            pool, coins, question, signals=signals, stance=stance, hypotheses=hypotheses,
            required_domains=required_domains, critic_adjustment=critic_adjustment)
        source = GRAPH_SOURCE_FALLBACK

    related = {}
    for claim in claims:
        for evidence_id in claim["supporting_evidence_ids"] + claim["contradicting_evidence_ids"]:
            related.setdefault(evidence_id, [])
            if claim["claim_id"] not in related[evidence_id]:
                related[evidence_id].append(claim["claim_id"])

    hypothesis_assessments = []
    for claim, assessment in zip(claims, assessments):
        if claim["claim_type"] != CLAIM_TYPE_HYPOTHESIS and not assessment.get("hypothesis_id"):
            continue
        hypothesis_assessments.append({
            "hypothesis_id": assessment.get("hypothesis_id", ""),
            "claim_id": claim["claim_id"],
            "statement": claim["statement"],
            "support_strength": assessment["support_strength"],
            "contradiction_strength": assessment["contradiction_strength"],
            "verdict": claim["verdict"],
            "confidence": claim["confidence"]["score"],
            "independent_support_chains": assessment["independent_support_chains"],
        })

    return {
        "claims": claims,
        "assessments": assessments,
        "hypothesis_assessments": hypothesis_assessments,
        "source": source,
        "fallback_reason": fallback_reason,
        "related_claim_ids": {key: related[key] for key in sorted(related)},
        "rejected_evidence_ids": list(pool.rejected_ids),
        "evidence_count": len(pool),
        "scoring_version": CLAIM_SCORING_VERSION,
    }


def claims_document(graph: dict) -> dict:
    """`claims.json` 的內容；純 dict／list／純量，可直接 `json.dumps`。"""
    return {
        "scoring_version": graph.get("scoring_version", CLAIM_SCORING_VERSION),
        "confidence_type": CONFIDENCE_TYPE_HEURISTIC,
        "confidence_note": "heuristic evidence score，不是校準過的市場正確機率。",
        "claim_source": graph.get("source", GRAPH_SOURCE_FALLBACK),
        "fallback_reason": graph.get("fallback_reason", ""),
        "claims": graph.get("claims", []),
        "hypothesis_assessments": graph.get("hypothesis_assessments", []),
        "rejected_evidence_ids": graph.get("rejected_evidence_ids", []),
    }


def apply_related_claim_ids(evidence, graph: dict) -> int:
    """把 claim ID 寫回 Evidence 的 `related_claim_ids`；回傳被更新的筆數。

    這個 helper 由整合端（Orchestrator）自行決定要不要呼叫，本模組不主動改寫任何輸入。
    """
    mapping = graph.get("related_claim_ids") or {}
    updated = 0
    for item in evidence or []:
        if isinstance(item, dict):
            evidence_id = str(item.get("evidence_id") or "")
            claim_ids = mapping.get(evidence_id)
            if claim_ids:
                item["related_claim_ids"] = list(claim_ids)
                updated += 1
            continue
        evidence_id = str(getattr(item, "evidence_id", "") or "")
        claim_ids = mapping.get(evidence_id)
        if claim_ids and hasattr(item, "related_claim_ids"):
            item.related_claim_ids = list(claim_ids)
            updated += 1
    return updated
