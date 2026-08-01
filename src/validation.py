"""Evidence, claim, and citation validation.

Four layers of checks:

* the original metadata contract -- every record must be traceable to a source, a fetch time and a
  claim, or the run stops;
* the T3 credibility contract -- a scored record must be able to explain its own number. The score
  has to match the breakdown, no hard cap may be exceeded, and a degraded record must be labelled as
  degraded. This is what stops a downstream module (or a model) from quietly writing a flattering
  score onto an unverifiable record;
* the T4 claim contract (`validate_claims`) -- the graph that came out must still be auditable;
* the T5 structural citation gate (`run_citation_gate`) -- the last check before anything is
  published. It answers one question: *can every material judgment in this report be traced back to
  evidence that actually belongs to this run?* Unknown IDs, cross-run IDs, rejected evidence,
  fallback-only support, and confidence that exceeds a hard cap all fail the gate, and a failing gate
  means the report is not fit to publish.

The gate is deliberately independent of the engines that produced the numbers. `src/credibility.py`
and `src/claim_graph.py` compute scores correctly by construction; the gate re-checks the *file that
came out*, because a claims graph can also arrive from a model proposal, a hand edit, or an older
version of the pipeline.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from types import MappingProxyType

from .claim_graph import (
    INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP,
    LIMITER_CONTEXT_ONLY,
    LIMITER_FALLBACK_ONLY,
    LIMITER_HIGH_QUALITY_CONFLICT,
    LIMITER_LOW_DOMAIN_COVERAGE,
    LIMITER_NO_SUPPORT,
    LIMITER_SINGLE_DOMAIN,
    LIMITER_SINGLE_SECONDARY_NEWS,
)
from .schemas import (
    ASSESSMENT_SOURCES,
    CITATION_GATE_CHECKS,
    CITATION_GATE_VERSION,
    CONFIDENCE_COMPONENT_KEYS,
    CONFIDENCE_LEVELS,
    CONFIDENCE_TYPE_HEURISTIC,
    CREDIBILITY_COMPONENT_KEYS,
    GATE_SEVERITY_ERROR,
    GATE_SEVERITY_WARNING,
    GATE_STATUS_FAIL,
    GATE_STATUS_PASS,
    GATE_STATUS_PASS_WITH_WARNINGS,
    HARD_CAPS,
    HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP,
    IMPACT_DIRECTIONS,
    IMPACT_HORIZONS,
    MAX_ASSESSMENT_RATIONALE_CHARS,
    MAX_ASSESSMENT_SOURCE_ITEM_IDS,
    NON_SUBSTANTIVE_VERIFICATION_STATUSES,
    QUESTION_RELATIONSHIPS,
    RELEVANCE_LABEL_IRRELEVANT,
    RELEVANCE_LABEL_SCORES,
    RELEVANCE_LABELS,
    SEMANTIC_ASSESSMENT_KEYS,
    SEMANTIC_CRITIC_CATEGORIES,
    SEMANTIC_CRITIC_CATEGORY_ALIASES,
    SEMANTIC_CRITIC_CATEGORY_OTHER,
    SINGLE_DOMAIN_CONFIDENCE_CAP,
    SOURCE_ITEM_KEYS,
    SOURCE_TYPE_FALLBACK_FIXTURE,
    SOURCE_TYPES,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICTS,
    VERIFICATION_STATUS_REJECTED,
    VERIFICATION_STATUSES,
    effective_weight,
    source_item_parent_id,
)


REQUIRED_FIELDS = {"evidence_id", "source", "source_url", "fetched_at", "data_type", "coin", "time_range", "content", "content_reference", "related_claim", "reliability_score"}

# What `score_breakdown` has to carry for the number to be auditable.
REQUIRED_BREAKDOWN_FIELDS = ("raw_score", "final_score", "components", "hard_cap", "scoring_version")

# Floating-point slack: scores are rounded to 4 decimals by the credibility engine.
_TOLERANCE = 1e-6


def _credibility_errors(record: dict, require_scored: bool) -> list[str]:
    """T3 checks for one record. Applied whenever the record has been scored."""
    errors = []
    evidence_id = record.get("evidence_id", "?")
    source_type = record.get("source_type", "unknown")
    status = record.get("verification_status", "unverified")
    breakdown = record.get("score_breakdown")
    score = record.get("reliability_score")

    if source_type not in SOURCE_TYPES:
        errors.append(f"{evidence_id}: unknown source_type {source_type!r}")
    if status not in VERIFICATION_STATUSES:
        errors.append(f"{evidence_id}: unknown verification_status {status!r}")

    if not isinstance(breakdown, dict) or not breakdown:
        if require_scored:
            errors.append(f"{evidence_id}: missing score_breakdown (evidence was never scored)")
        return errors

    missing = [field for field in REQUIRED_BREAKDOWN_FIELDS if field not in breakdown]
    if missing:
        errors.append(f"{evidence_id}: score_breakdown missing {missing}")
        return errors
    components = breakdown.get("components")
    if not isinstance(components, dict) or set(components) != set(CREDIBILITY_COMPONENT_KEYS):
        errors.append(f"{evidence_id}: score_breakdown components must be exactly {sorted(CREDIBILITY_COMPONENT_KEYS)}")

    # The engine's final_score is the only authority; a caller may not keep a different number in
    # the field the report actually prints.
    final_score = breakdown.get("final_score")
    if not isinstance(final_score, (int, float)) or abs(float(final_score) - float(score or 0)) > _TOLERANCE:
        errors.append(f"{evidence_id}: reliability_score {score} does not match score_breakdown final_score {final_score}")
        return errors

    limiters = breakdown.get("score_limiters") or record.get("score_limiters") or []
    unknown_limiters = [name for name in limiters if name not in HARD_CAPS]
    if unknown_limiters:
        errors.append(f"{evidence_id}: unknown score limiters {sorted(unknown_limiters)}")
    for name in limiters:
        if name in HARD_CAPS and float(final_score) > HARD_CAPS[name] + _TOLERANCE:
            errors.append(f"{evidence_id}: hard cap {name} ({HARD_CAPS[name]}) breached by final score {final_score}")
    cap = breakdown.get("hard_cap")
    if isinstance(cap, (int, float)) and float(final_score) > float(cap) + _TOLERANCE:
        errors.append(f"{evidence_id}: hard cap {cap} breached by final score {final_score}")

    if not record.get("source_lineage_id"):
        errors.append(f"{evidence_id}: scored evidence must carry a source_lineage_id")
    if not record.get("scoring_version"):
        errors.append(f"{evidence_id}: scored evidence must carry a scoring_version")

    # Degraded evidence stays in the list, but it may not look substantive.
    if status == VERIFICATION_STATUS_REJECTED and abs(float(score or 0)) > _TOLERANCE:
        errors.append(f"{evidence_id}: rejected evidence must score 0, got {score}")
    if source_type == SOURCE_TYPE_FALLBACK_FIXTURE:
        cap_value = HARD_CAPS[SOURCE_TYPE_FALLBACK_FIXTURE]
        if float(score or 0) > cap_value + _TOLERANCE:
            errors.append(f"{evidence_id}: fallback evidence must not exceed {cap_value}, got {score}")
        if status not in NON_SUBSTANTIVE_VERIFICATION_STATUSES:
            errors.append(f"{evidence_id}: fallback evidence must be labelled unavailable/fallback/rejected, got {status!r}")
    elif status in {"unavailable", "fallback"}:
        errors.append(f"{evidence_id}: verification_status {status!r} requires source_type {SOURCE_TYPE_FALLBACK_FIXTURE!r}, got {source_type!r}")
    return errors


# --------------------------------------------------------------------------------------
# E2 — 問題導向語意評估的契約
#
# 這一層回答的是「這筆評估自己解釋得通嗎」：標籤合法、分數等於標籤的固定映射、
# effective_weight 等於公式算出來的值、引用的子項真的屬於這筆證據。
#
# 為什麼要獨立驗一次：`semantic_assessment` 的標籤可以來自模型。模型不得決定數值，因此
# 「分數是不是照映射算的」必須被檢查，而不是相信寫入者。這與 T3 的 score_breakdown 同理。
# --------------------------------------------------------------------------------------

# 「一筆證據裝很多子項」的資料型別。它們被引用時，光有母證據 ID 無法回答「引用的是哪一則」。
AGGREGATED_DATA_TYPES = frozenset({
    "news", "announcement", "social", "social_bluesky", "social_hackernews",
})


def _source_item_errors(record: dict) -> list[str]:
    """子項清單的形狀：ID 必須屬於這筆證據、不重複、欄位不得多出來。"""
    evidence_id = str(record.get("evidence_id") or "?")
    items = record.get("source_items")
    if items in (None, []):
        return []
    if not isinstance(items, list):
        return [f"{evidence_id}: source_items must be a list"]
    errors, seen = [], set()
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"{evidence_id}: source_items[{position}] must be an object")
            continue
        item_id = str(item.get("source_item_id") or "").strip()
        if not item_id:
            errors.append(f"{evidence_id}: source_items[{position}] is missing source_item_id")
            continue
        if item_id in seen:
            errors.append(f"{evidence_id}: duplicate source_item_id {item_id!r}")
        seen.add(item_id)
        parent = source_item_parent_id(item_id)
        if parent != str(record.get("evidence_id") or ""):
            errors.append(f"{evidence_id}: source_item_id {item_id!r} does not belong to this evidence")
        unknown = sorted(set(item) - set(SOURCE_ITEM_KEYS))
        if unknown:
            errors.append(f"{evidence_id}: source_items[{position}] has unknown keys {unknown}")
    return errors


def _assessment_errors(record: dict) -> list[str]:
    """`semantic_assessment` 的契約。空 dict 代表「本次未評估」，合法。"""
    evidence_id = str(record.get("evidence_id") or "?")
    assessment = record.get("semantic_assessment")
    if assessment in (None, {}):
        return []
    if not isinstance(assessment, dict):
        return [f"{evidence_id}: semantic_assessment must be an object"]

    errors = []
    missing = [key for key in SEMANTIC_ASSESSMENT_KEYS if key not in assessment]
    if missing:
        return [f"{evidence_id}: semantic_assessment missing {missing}"]
    unknown = sorted(set(assessment) - set(SEMANTIC_ASSESSMENT_KEYS))
    if unknown:
        errors.append(f"{evidence_id}: semantic_assessment has unknown keys {unknown}")

    label = assessment.get("relevance_label")
    if label not in RELEVANCE_LABELS:
        errors.append(f"{evidence_id}: unknown relevance_label {label!r}")
    else:
        expected = RELEVANCE_LABEL_SCORES[label]
        score = assessment.get("relevance_score")
        if not isinstance(score, (int, float)) or abs(float(score) - expected) > _TOLERANCE:
            # 這是本層最重要的一條：分數只能是標籤的固定映射。放寬它等於允許模型自評分數。
            errors.append(f"{evidence_id}: relevance_score {score!r} does not match "
                          f"relevance_label {label!r} (expected {expected})")
        elif label == RELEVANCE_LABEL_IRRELEVANT and not str(assessment.get("excluded_reason") or "").strip():
            errors.append(f"{evidence_id}: irrelevant evidence must record an excluded_reason")
        elif label != RELEVANCE_LABEL_IRRELEVANT and str(assessment.get("excluded_reason") or "").strip():
            errors.append(f"{evidence_id}: excluded_reason is only allowed when relevance_label is "
                          f"{RELEVANCE_LABEL_IRRELEVANT!r}")

    for key, allowed in (("relationship_to_question", QUESTION_RELATIONSHIPS),
                         ("impact_direction", IMPACT_DIRECTIONS),
                         ("impact_horizon", IMPACT_HORIZONS),
                         ("assessment_source", ASSESSMENT_SOURCES)):
        if assessment.get(key) not in allowed:
            errors.append(f"{evidence_id}: unknown {key} {assessment.get(key)!r}")

    rationale = assessment.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        errors.append(f"{evidence_id}: semantic_assessment requires a rationale")
    elif len(rationale) > MAX_ASSESSMENT_RATIONALE_CHARS:
        errors.append(f"{evidence_id}: rationale is longer than "
                      f"{MAX_ASSESSMENT_RATIONALE_CHARS} characters")

    item_ids = assessment.get("source_item_ids")
    if not isinstance(item_ids, list):
        errors.append(f"{evidence_id}: source_item_ids must be a list")
    else:
        if len(item_ids) > MAX_ASSESSMENT_SOURCE_ITEM_IDS:
            errors.append(f"{evidence_id}: source_item_ids may name at most "
                          f"{MAX_ASSESSMENT_SOURCE_ITEM_IDS} items")
        if len(set(item_ids)) != len(item_ids):
            errors.append(f"{evidence_id}: source_item_ids contains duplicates")
        known = {str(item.get("source_item_id")) for item in (record.get("source_items") or [])
                 if isinstance(item, dict)}
        unknown_items = sorted(str(item) for item in item_ids if str(item) not in known)
        if unknown_items:
            errors.append(f"{evidence_id}: source_item_ids {unknown_items} are not items of this evidence")

    if not str(assessment.get("assessment_version") or "").strip():
        errors.append(f"{evidence_id}: semantic_assessment must carry an assessment_version")

    weight = assessment.get("effective_weight")
    expected_weight = effective_weight(record.get("reliability_score"),
                                       assessment.get("relevance_score"),
                                       record.get("independence_factor"))
    if not isinstance(weight, (int, float)) or abs(float(weight) - expected_weight) > _TOLERANCE:
        errors.append(f"{evidence_id}: effective_weight {weight!r} does not match "
                      f"reliability × relevance × independence ({expected_weight})")
    return errors


def validate_evidence_assessments(proposals, evidence) -> list[str]:
    """驗證**模型提出**的整批評估提案；回傳全部問題，呼叫端據此決定整批作廢。

    刻意要求「恰好一筆對一個本次 Evidence ID」：少一筆代表模型沒有讀完證據清單，多一筆或
    重複代表它在編 ID。部分接受會讓報告出現「有些證據被評估過、有些沒有」的混合狀態，
    而讀者無從分辨哪一種 —— 所以這裡是全有或全無，`src/orchestrator.py` 隨即改走
    deterministic fallback。
    """
    index = _evidence_index(evidence)
    if not isinstance(proposals, list) or not proposals:
        return ["evidence assessment payload contains no entries"]

    errors, seen = [], []
    for position, proposal in enumerate(proposals, start=1):
        if not isinstance(proposal, dict):
            errors.append(f"assessment[{position}] must be an object")
            continue
        evidence_id = str(proposal.get("evidence_id") or "").strip()
        if not evidence_id:
            errors.append(f"assessment[{position}] is missing evidence_id")
            continue
        if evidence_id not in index:
            errors.append(f"assessment cites unknown evidence id {evidence_id!r}")
            continue
        if evidence_id in seen:
            errors.append(f"assessment repeats evidence id {evidence_id!r}")
        seen.append(evidence_id)

        for key, allowed in (("relevance_label", RELEVANCE_LABELS),
                             ("relationship_to_question", QUESTION_RELATIONSHIPS),
                             ("impact_direction", IMPACT_DIRECTIONS),
                             ("impact_horizon", IMPACT_HORIZONS)):
            if proposal.get(key) not in allowed:
                errors.append(f"{evidence_id}: unknown {key} {proposal.get(key)!r}")
        for numeric in ("relevance_score", "effective_weight", "reliability_score", "confidence"):
            if numeric in proposal:
                # 模型給了數字就代表它在試著自評分數。不靜默忽略：整批作廢並留下原因。
                errors.append(f"{evidence_id}: assessment may not carry a model-supplied {numeric}")

        item_ids = proposal.get("source_item_ids")
        if item_ids is None:
            item_ids = []
        if not isinstance(item_ids, list):
            errors.append(f"{evidence_id}: source_item_ids must be a list")
        else:
            if len(item_ids) > MAX_ASSESSMENT_SOURCE_ITEM_IDS:
                errors.append(f"{evidence_id}: source_item_ids may name at most "
                              f"{MAX_ASSESSMENT_SOURCE_ITEM_IDS} items")
            if len(set(str(item) for item in item_ids)) != len(item_ids):
                errors.append(f"{evidence_id}: source_item_ids contains duplicates")
            known = {str(item.get("source_item_id"))
                     for item in (index[evidence_id].get("source_items") or [])
                     if isinstance(item, dict)}
            for item in item_ids:
                if str(item) not in known:
                    errors.append(f"{evidence_id}: source_item_id {str(item)!r} does not belong to it")

    uncovered = sorted(set(index) - set(seen))
    if uncovered:
        errors.append(f"assessment does not cover every evidence id: {uncovered}")
    return errors


def validate_evidence(evidence: list[object], referenced_ids: list[str],
                      require_scored: bool = False) -> list[str]:
    """Return every problem found, rather than raising on the first one.

    `require_scored` is set by the orchestrator: inside the pipeline an unscored record is itself a
    bug, while callers that inspect raw adapter output (tests, the source-summary page) legitimately
    see records whose `reliability_score` is still the adapter's legacy hint.
    """
    errors = []
    seen = set()
    for item in evidence:
        record = asdict(item) if hasattr(item, "__dataclass_fields__") else item
        missing = REQUIRED_FIELDS - set(record)
        if missing:
            errors.append(f"{record.get('evidence_id', '?')}: missing {sorted(missing)}")
        if record.get("evidence_id") in seen:
            errors.append(f"duplicate evidence_id: {record['evidence_id']}")
        seen.add(record.get("evidence_id"))
        if not record.get("source_url") or not record.get("source"):
            errors.append(f"{record.get('evidence_id', '?')}: source is empty")
        if not record.get("content_reference") or not record.get("related_claim"):
            errors.append(f"{record.get('evidence_id', '?')}: missing traceability fields")
        if not 0 <= record.get("reliability_score", -1) <= 1:
            errors.append(f"{record.get('evidence_id', '?')}: invalid reliability_score")
        try:
            datetime.fromisoformat(record.get("fetched_at", "" ).replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"{record.get('evidence_id', '?')}: invalid fetched_at")
        errors.extend(_credibility_errors(record, require_scored))
        errors.extend(_source_item_errors(record))
        errors.extend(_assessment_errors(record))
    missing_refs = set(referenced_ids) - seen
    errors.extend(f"report references unknown evidence_id: {item}" for item in sorted(missing_refs))
    return errors


# --------------------------------------------------------------------------------------
# T4 — Claim graph 的結構檢查
# --------------------------------------------------------------------------------------

def _discloses_mixed_evidence(claim: dict, overlap: list) -> bool:
    """Claim 是否已明確承認這些證據兩面都指：verdict 為 mixed，或列在 mixed／context 清單裡。"""
    if claim.get("verdict") == GATE_MIXED_VERDICT:
        return True
    declared = set()
    for key in GATE_MIXED_EVIDENCE_KEYS:
        declared |= {str(item) for item in (claim.get(key) or [])}
    return bool(declared) and set(str(item) for item in overlap) <= declared


def _claim_evidence_index(evidence: list) -> tuple:
    """從 Evidence 清單取出 claim 檢查需要的三組資訊：有效 ID、fallback ID、rejected ID。"""
    active, fallback, rejected = set(), set(), set()
    for item in evidence or []:
        record = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
        evidence_id = str(record.get("evidence_id") or "")
        if not evidence_id:
            continue
        status = record.get("verification_status") or "unverified"
        if status == VERIFICATION_STATUS_REJECTED:
            rejected.add(evidence_id)
            continue
        active.add(evidence_id)
        limiters = record.get("score_limiters") or []
        if record.get("source_type") == SOURCE_TYPE_FALLBACK_FIXTURE or (
                SOURCE_TYPE_FALLBACK_FIXTURE in limiters):
            fallback.add(evidence_id)
    return active, fallback, rejected


def _claim_confidence_errors(claim_id: str, confidence: object) -> list[str]:
    """信心欄位必須自我解釋：分數、等級、type、五個分量鍵都要在。"""
    if not isinstance(confidence, dict):
        return [f"{claim_id}: confidence must be an object"]
    errors = []
    score = confidence.get("score")
    if not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
        errors.append(f"{claim_id}: confidence score must be between 0 and 1, got {score!r}")
    if confidence.get("level") not in CONFIDENCE_LEVELS:
        errors.append(f"{claim_id}: unknown confidence level {confidence.get('level')!r}")
    # 信心是 heuristic evidence score，不是校準過的市場正確機率；型別欄位不得被改寫成別的說法。
    if confidence.get("type") != CONFIDENCE_TYPE_HEURISTIC:
        errors.append(f"{claim_id}: confidence type must be {CONFIDENCE_TYPE_HEURISTIC!r}, "
                      f"got {confidence.get('type')!r}")
    components = confidence.get("components")
    if not isinstance(components, dict) or set(components) != set(CONFIDENCE_COMPONENT_KEYS):
        errors.append(f"{claim_id}: confidence components must be exactly "
                      f"{sorted(CONFIDENCE_COMPONENT_KEYS)}")
    if not isinstance(confidence.get("limiters"), list):
        errors.append(f"{claim_id}: confidence limiters must be a list")
    return errors


def validate_claims(claims: list, evidence: list | None = None) -> list[str]:
    """Return every structural problem in the Claim graph, rather than raising on the first one.

    This is the counterpart to `validate_evidence`: `src/claim_graph.py` computes the numbers, and
    this function checks that what came out is still auditable -- every cited ID exists, rejected
    evidence is not cited, the three layers are actually in three fields, and a Claim without
    supporting evidence (or supported only by fallback fixtures) is labelled
    `insufficient_evidence` instead of presenting a direction.

    `evidence` is optional so callers holding only the graph can still run the shape checks; when it
    is supplied the ID, fallback and rejected checks are enabled as well.
    """
    if not isinstance(claims, list) or not claims:
        return ["claim graph must contain at least one claim"]

    active, fallback, rejected = _claim_evidence_index(evidence)
    check_ids = bool(evidence)
    errors, seen = [], set()

    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claim #{index} must be an object")
            continue
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id:
            errors.append(f"claim #{index}: missing claim_id")
            claim_id = f"#{index}"
        if claim_id in seen:
            errors.append(f"duplicate claim_id: {claim_id}")
        seen.add(claim_id)

        if not str(claim.get("statement") or "").strip():
            errors.append(f"{claim_id}: missing statement")
        verdict = claim.get("verdict")
        if verdict not in VERDICTS:
            errors.append(f"{claim_id}: unknown verdict {verdict!r}")
        # Fact / Inference / Conclusion 必須分開存放，否則讀者無法分辨哪一句是資料、哪一句是解讀。
        if not str(claim.get("inference") or "").strip():
            errors.append(f"{claim_id}: missing inference layer")
        if not str(claim.get("conclusion") or "").strip():
            errors.append(f"{claim_id}: missing conclusion layer")

        supporting = claim.get("supporting_evidence_ids") or []
        contradicting = claim.get("contradicting_evidence_ids") or []
        if not isinstance(supporting, list) or not isinstance(contradicting, list):
            errors.append(f"{claim_id}: supporting/contradicting evidence must be lists")
            supporting = supporting if isinstance(supporting, list) else []
            contradicting = contradicting if isinstance(contradicting, list) else []
        overlap = sorted(set(supporting) & set(contradicting))
        # T5 規則 6：同一筆證據不得同時支持與反對，除非 Claim 已明確標示它是混合／背景性質。
        # `src/claim_graph.py` 產生的圖永遠不會有重疊（引擎直接拒絕），這個例外只給外部提供的
        # claims 檔使用 —— 一筆真正兩面都指的證據，誠實的做法是揭露，而不是挑一邊放。
        if overlap and not _discloses_mixed_evidence(claim, overlap):
            errors.append(f"{claim_id}: evidence both supports and contradicts the claim: {overlap}")

        facts = claim.get("facts")
        if not isinstance(facts, list):
            errors.append(f"{claim_id}: facts must be a list")
            facts = []
        fact_ids = []
        for fact_index, fact in enumerate(facts):
            if not isinstance(fact, dict):
                errors.append(f"{claim_id}: fact #{fact_index} must be an object")
                continue
            if not str(fact.get("statement") or "").strip():
                errors.append(f"{claim_id}: fact #{fact_index} is missing statement")
            ids = fact.get("evidence_ids")
            if not isinstance(ids, list) or not ids:
                errors.append(f"{claim_id}: fact #{fact_index} must cite at least one evidence id")
                continue
            fact_ids.extend(str(item) for item in ids)

        if check_ids:
            cited = [str(item) for item in supporting] + [str(item) for item in contradicting] + fact_ids
            unknown = sorted({item for item in cited if item not in active and item not in rejected})
            if unknown:
                errors.append(f"{claim_id}: cites unknown evidence ids {unknown}")
            cited_rejected = sorted({item for item in cited if item in rejected})
            if cited_rejected:
                errors.append(f"{claim_id}: cites rejected evidence {cited_rejected}")

        # 「至少一筆支持證據，否則必須是 insufficient_evidence」與「主要支持全為 fallback 時同樣
        # 必須是 insufficient_evidence」是 T4 的兩條硬規則，不接受用文字補救。
        if not supporting and verdict != VERDICT_INSUFFICIENT_EVIDENCE:
            errors.append(f"{claim_id}: claim without supporting evidence must be "
                          f"{VERDICT_INSUFFICIENT_EVIDENCE}, got {verdict!r}")
        if (check_ids and supporting and verdict != VERDICT_INSUFFICIENT_EVIDENCE
                and all(str(item) in fallback for item in supporting)):
            errors.append(f"{claim_id}: fallback evidence cannot be the sole support of "
                          f"verdict {verdict!r}")

        errors.extend(_claim_confidence_errors(claim_id, claim.get("confidence")))
    return errors


# --------------------------------------------------------------------------------------
# T5 — Structural Citation Gate
# --------------------------------------------------------------------------------------

# 每一筆被引用的 Evidence 至少要有這四個欄位，否則讀者無法自行重現它：
# 來源是誰、什麼時候取得、內容指到哪裡、以及它被哪些 Claim 使用。
GATE_REQUIRED_EVIDENCE_FIELDS = ("source", "fetched_at", "content_reference", "related_claim_ids")

# Claim confidence 的 limiter → 生效上限。數值與 `src/claim_graph.py` 的計分規則同源，
# 由 gate 再獨立驗一次：`claims.json` 可能來自模型提案、手改，或舊版管線，
# 「引擎當時算對」不等於「現在檔案裡的數字仍然合法」。
CLAIM_CONFIDENCE_LIMITER_CAPS = MappingProxyType({
    LIMITER_SINGLE_DOMAIN: SINGLE_DOMAIN_CONFIDENCE_CAP,
    LIMITER_HIGH_QUALITY_CONFLICT: HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP,
    LIMITER_SINGLE_SECONDARY_NEWS: HARD_CAPS["single_secondary_news_source"],
    LIMITER_NO_SUPPORT: INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP,
    LIMITER_FALLBACK_ONLY: INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP,
    LIMITER_LOW_DOMAIN_COVERAGE: INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP,
    LIMITER_CONTEXT_ONLY: INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP,
})

# 允許「同一筆 Evidence 同時出現在支持與反對」的唯一情況：Claim 自己已經說明這筆證據是混合／
# 背景性質。verdict 為 mixed，或 Claim 明確列出 `mixed_evidence_ids`／`context_evidence_ids`。
GATE_MIXED_EVIDENCE_KEYS = ("mixed_evidence_ids", "context_evidence_ids")
GATE_MIXED_VERDICT = "mixed"

# 低於這個可靠度的證據不足以支撐「確證」語氣；用於 stale_or_weak 與 confidence_too_high。
WEAK_EVIDENCE_SCORE = 0.50

# 語意偵測用的詞表。這是啟發式的：命中只產生 warning，永遠不會讓 gate FAIL，
# 因為「這句話太強」是判斷問題，不是結構問題，不該由字串比對決定能不能發佈。
_OVER_CLAIM_MARKERS = ("必然", "保證", "肯定會", "一定會", "無疑", "確定會", "毫無疑問", "穩賺")
_CAUSAL_MARKERS = ("導致", "造成", "使得", "推升", "引發", "驅動了", "帶動了", "因為", "是因為")
_OUTCOME_MARKERS = ("商業成果", "營收", "獲利", "已帶來", "已產生收益", "實際採用", "用戶成長",
                    "落地成效", "已提升業績")
_INTENT_MARKERS = ("準備拋售", "準備賣出", "打算賣", "打算買", "意圖", "計畫賣出", "計畫買入",
                   "即將出貨", "即將買入", "拋售意圖", "準備買入")
_OFFICIAL_SOURCE_TYPES = ("official_announcement",)
_OFFICIAL_DATA_TYPES = ("announcement",)
_ONCHAIN_SOURCE_TYPES = ("blockchain_raw",)
_ONCHAIN_DATA_TYPES = ("onchain", "whale")


def _as_record(item) -> dict:
    """Evidence／Claim 一律轉成 dict；dataclass 與純 dict 都能進來。"""
    if hasattr(item, "__dataclass_fields__"):
        return asdict(item)
    return dict(item) if isinstance(item, dict) else {}


def _evidence_index(evidence) -> dict:
    """evidence_id → 正規化後的 record。重複 ID 由 `validate_evidence` 負責回報。"""
    index = {}
    for item in evidence or []:
        record = _as_record(item)
        evidence_id = str(record.get("evidence_id") or "")
        if evidence_id:
            index.setdefault(evidence_id, record)
    return index


def _text_of(claim: dict, *keys) -> str:
    return " ".join(str(claim.get(key) or "") for key in keys)


def _claim_cited_ids(claim: dict) -> dict:
    """把一個 Claim 的引用拆成三類，讓 finding 能指出問題出在哪一種引用上。"""
    supporting = [str(item) for item in (claim.get("supporting_evidence_ids") or [])]
    contradicting = [str(item) for item in (claim.get("contradicting_evidence_ids") or [])]
    fact_ids = []
    for fact in claim.get("facts") or []:
        record = fact if isinstance(fact, dict) else {}
        fact_ids.extend(str(item) for item in (record.get("evidence_ids") or []))
    return {"supporting": supporting, "contradicting": contradicting, "facts": fact_ids}


def normalise_semantic_category(category) -> str:
    """把 Critic 回傳的類別收斂到八個語意類別之一，認不出來的一律標成 `other`。

    模型用舊名稱（例如 `confidence`）或近似名稱時，正規化比整份稽核作廢更有用：
    稽核內容本身仍然可讀，只是被歸到正確的類別裡。
    """
    name = str(category or "").strip().lower().replace(" ", "_").replace("-", "_")
    if name in SEMANTIC_CRITIC_CATEGORIES:
        return name
    return SEMANTIC_CRITIC_CATEGORY_ALIASES.get(name, SEMANTIC_CRITIC_CATEGORY_OTHER)


def _is_fallback(record: dict) -> bool:
    limiters = record.get("score_limiters") or []
    return (record.get("source_type") == SOURCE_TYPE_FALLBACK_FIXTURE
            or SOURCE_TYPE_FALLBACK_FIXTURE in limiters)


def _score_of(record: dict) -> float:
    try:
        return float(record.get("reliability_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _confidence_caps(claim: dict, supporting_records: list) -> dict:
    """回傳這個 Claim 生效的信心上限，鍵是規則名稱、值是上限。

    兩種來源：
    * Claim 自己的 limiters 與 verdict —— 引擎保證會套用，因此違反屬於**錯誤**；
    * 全部支持證據共有的 credibility limiter —— 例如整組支持證據都被
      `unverifiable_intent_attribution` 壓到 0.55。這一類只回報為警告（見
      `run_citation_gate`），因為 claim confidence 與 evidence reliability 是兩個不同尺度，
      硬性等同會讓合法的 live 執行被擋住。
    """
    caps, evidence_caps = {}, {}
    confidence = claim.get("confidence") if isinstance(claim.get("confidence"), dict) else {}
    for name in confidence.get("limiters") or []:
        cap = CLAIM_CONFIDENCE_LIMITER_CAPS.get(str(name))
        if cap is not None:
            caps[str(name)] = cap
    if claim.get("verdict") == VERDICT_INSUFFICIENT_EVIDENCE:
        caps[VERDICT_INSUFFICIENT_EVIDENCE] = INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP

    if supporting_records:
        shared = set(supporting_records[0].get("score_limiters") or [])
        for record in supporting_records[1:]:
            shared &= set(record.get("score_limiters") or [])
        for name in sorted(shared):
            # fallback_fixture 交給 verdict 規則處理：引擎刻意用 insufficient_evidence 的 0.35
            # 作為上限，而不是把 evidence 的 0.20 直接當成 claim 上限。
            if name == SOURCE_TYPE_FALLBACK_FIXTURE or name not in HARD_CAPS:
                continue
            evidence_caps["evidence:%s" % name] = HARD_CAPS[name]
    return {"claim": caps, "evidence": evidence_caps}


def detect_semantic_risks(claims, evidence=None) -> list[dict]:
    """Deterministic 語意風險偵測：涵蓋八個語意類別，全部只產生 warning。

    這是「離線也要有語意檢查」的那一半。LLM Critic 能讀懂句子，但它可能不可用、可能逾時、
    也可能在正式執行當天完全沒有配額；這裡用可重現的規則把最容易出錯的八種過度推論標出來，
    讓報告即使在沒有模型的情況下也會自己承認風險。

    刻意保守：命中只是提醒，不改分數、不改結論、不阻止發佈。
    """
    index = _evidence_index(evidence)
    findings = []

    def add(category: str, claim_id: str, message: str, evidence_ids=()):
        findings.append({
            "category": category,
            "severity": GATE_SEVERITY_WARNING,
            "claim_id": claim_id,
            "evidence_ids": sorted(set(str(item) for item in evidence_ids)),
            "issue": message,
            "detected_by": "deterministic_rules",
        })

    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "?")
        verdict = claim.get("verdict")
        confidence = claim.get("confidence") if isinstance(claim.get("confidence"), dict) else {}
        cited = _claim_cited_ids(claim)
        supporting = [index[item] for item in cited["supporting"] if item in index]
        reasoning_text = _text_of(claim, "inference", "conclusion")
        all_text = _text_of(claim, "statement", "inference", "conclusion")

        if any(marker in all_text for marker in _OVER_CLAIM_MARKERS):
            add("over_claim", claim_id, "結論使用了確定性用語，強度超過證據能支撐的程度。")

        if not cited["supporting"] and verdict != VERDICT_INSUFFICIENT_EVIDENCE:
            add("unsupported", claim_id, "沒有任何支持證據，卻給出了方向性判定。")

        if cited["contradicting"] and not (claim.get("limitations") or []):
            add("ignored_counter", claim_id,
                "存在反方證據，但未在限制中說明它如何影響結論。", cited["contradicting"])

        if (supporting and verdict != VERDICT_INSUFFICIENT_EVIDENCE
                and all(_score_of(record) < WEAK_EVIDENCE_SCORE for record in supporting)):
            add("stale_or_weak", claim_id,
                "全部支持證據的可靠度都低於 %.2f，不足以作為確證。" % WEAK_EVIDENCE_SCORE,
                cited["supporting"])

        if any(marker in reasoning_text for marker in _CAUSAL_MARKERS):
            add("correlation_as_causation", claim_id,
                "推論或結論使用了因果用語；本管線只能建立同時性關聯。")

        if supporting and any(marker in reasoning_text for marker in _OUTCOME_MARKERS) and all(
                record.get("source_type") in _OFFICIAL_SOURCE_TYPES
                or record.get("data_type") in _OFFICIAL_DATA_TYPES for record in supporting):
            add("official_statement_as_outcome", claim_id,
                "官方公告只能證明「已發布」，不能證明商業成果已經發生。", cited["supporting"])

        if supporting and any(marker in all_text for marker in _INTENT_MARKERS) and all(
                record.get("source_type") in _ONCHAIN_SOURCE_TYPES
                or record.get("data_type") in _ONCHAIN_DATA_TYPES for record in supporting):
            add("onchain_transfer_as_intent", claim_id,
                "鏈上轉帳能證明轉帳發生，不能證明地址擁有者的意圖。", cited["supporting"])

        if (confidence.get("level") == CONFIDENCE_LEVELS[2] and supporting
                and all(_score_of(record) < WEAK_EVIDENCE_SCORE for record in supporting)):
            add("confidence_too_high", claim_id,
                "信心等級為 high，但支持證據全部是低可靠度來源。", cited["supporting"])

    return findings


def critique_semantic_findings(critique) -> list[dict]:
    """把 LLM Critic 的 findings 正規化成與 `detect_semantic_risks` 相同的形狀。

    Critic 只能標註與下調信心：這裡不讀取它的任何分數，只保留類別、指向的句子與 evidence ID，
    確保它無法藉由 findings 新增證據或改寫既有 Evidence。
    """
    if not isinstance(critique, dict):
        return []
    normalised = []
    for finding in critique.get("findings") or []:
        record = finding if isinstance(finding, dict) else {}
        evidence_id = str(record.get("evidence_id") or "").strip()
        normalised.append({
            "category": normalise_semantic_category(record.get("category")),
            "severity": GATE_SEVERITY_WARNING,
            "claim_id": "",
            "evidence_ids": [evidence_id] if evidence_id and evidence_id != "N/A" else [],
            "issue": str(record.get("issue") or "").strip(),
            "claim_text": str(record.get("claim") or "").strip(),
            "reported_severity": str(record.get("severity") or "").strip(),
            "detected_by": "llm_critic",
        })
    return normalised


def _resolvable_item_url(item: dict) -> bool:
    url = str(item.get("url") or "").strip().lower()
    return url.startswith("http://") or url.startswith("https://")


def _cited_item_locator_findings(record: dict, citing: str) -> list[tuple]:
    """規則 11：被引用的聚合證據要指出「實際是哪一則」。

    只在證據**真的有子項**時檢查。沒有子項的紀錄有兩種：採集失敗的降級 fixture（本來就沒有
    內容可指），以及非聚合型的單一觀測（市場、鏈上）—— 對這兩種要求子項 locator 沒有意義。

    嚴重度分成兩級，依「這是結構漏洞還是資料品質問題」：

    * **完全沒指出任何子項** → error。這是結構漏洞：沒有人說過引用的是哪一則。本管線的寫入者
      永遠會填（見 `orchestrator._citable_source_item_ids`），因此這條規則實際攔的是外部產生
      或手改過的 `claims.json`／`evidence.json`。降級來源例外，只給 warning —— 它的狀態已經
      標成 unavailable／fallback，沒有子項是採集失敗的結果。
    * **指出了子項，但該子項自己沒有 http(s) URL** → warning。子項已經被指名、母證據仍有
      locator 與 fetched_at，因此引用仍可回溯；缺的是那一則自己的網址，屬資料品質問題。
      把它升為 error 會讓「某個平台這次沒給貼文網址」直接讓整份報告發不出去。

    author／published_at 缺值同樣只警告、不阻擋，且**不得**用 fetched_at 代填：來源沒署名就是
    沒署名，偽造一個作者比留 null 糟得多。
    """
    items = [item for item in (record.get("source_items") or []) if isinstance(item, dict)]
    if str(record.get("data_type") or "") not in AGGREGATED_DATA_TYPES or not items:
        return []
    evidence_id = str(record.get("evidence_id") or "?")
    is_fallback = _is_fallback(record)

    assessment = record.get("semantic_assessment")
    named = {str(item) for item in ((assessment or {}).get("source_item_ids") or [])}
    chosen = [item for item in items if str(item.get("source_item_id")) in named]
    if not chosen:
        severity = GATE_SEVERITY_WARNING if is_fallback else GATE_SEVERITY_ERROR
        return [(severity, f"{evidence_id}: 被 {citing} 引用的聚合證據沒有指出實際依據的子項，"
                           f"無法回答引用的是哪一則")]
    try:
        datetime.fromisoformat(str(record.get("fetched_at") or "").replace("Z", "+00:00"))
    except ValueError:
        severity = GATE_SEVERITY_WARNING if is_fallback else GATE_SEVERITY_ERROR
        return [(severity, f"{evidence_id}: 被 {citing} 引用，但 fetched_at 無法解析，"
                           f"子項 locator 不可重現")]
    if is_fallback:
        return []
    if not any(_resolvable_item_url(item) for item in chosen):
        return [(GATE_SEVERITY_WARNING,
                 f"{evidence_id}: 被 {citing} 引用的子項都沒有自己的 http(s) URL，"
                 f"只能回溯到母來源 {record.get('source_url') or '(無)'}")]
    def unattributed(item: dict) -> bool:
        no_author = not (item.get("author") or item.get("author_handle"))
        no_time = not (item.get("published_at") or item.get("created_at") or item.get("indexed_at"))
        return no_author or no_time

    incomplete = sorted(str(item.get("source_item_id")) for item in chosen if unattributed(item))
    if incomplete:
        return [(GATE_SEVERITY_WARNING,
                 f"{evidence_id}: 子項 {incomplete} 缺少 author 或發布時間（已存 null，未以 "
                 f"fetched_at 代填），引用仍可追溯但署名與時序無法確認")]
    return []


def run_citation_gate(run_id: str, evidence, claims, *, cited_evidence_ids=(), critique=None,
                      check_related_claim_ids: bool = True) -> dict:
    """T5 的最後一道關卡：報告裡的每個主要判斷是否都能追溯回本次 run 的 Evidence。

    十條結構規則（見 `schemas.CITATION_GATE_CHECKS`）全部在這裡執行，任何一條的錯誤都會讓
    `status` 變成 `FAIL`。呼叫端必須把 FAIL 當成「這份報告不可發佈」，先降級重建，而不是照樣輸出。

    `check_related_claim_ids` 供「還沒把 Claim ID 寫回 Evidence」的中間階段呼叫；正式輸出前
    一律用預設的 `True`，否則第 9 條規則等於沒有執行。

    回傳純 dict／list／純量，可直接寫進 `execution_log.json`；相同輸入永遠得到相同輸出。
    """
    index = _evidence_index(evidence)
    claim_list = [claim for claim in (claims or []) if isinstance(claim, dict)]
    findings = []

    def add(check: str, severity: str, message: str, claim_id: str = "", evidence_ids=()):
        findings.append({
            "check": check,
            "severity": severity,
            "claim_id": claim_id,
            "evidence_ids": sorted(set(str(item) for item in evidence_ids)),
            "message": message,
        })

    # 規則 0：claims.json 本身的形狀。gate 自己重跑一次而不是相信呼叫端已經驗過，
    # 因為 gate 也會被用來檢查別處產生的 claims 檔。
    for error in validate_claims(claims, evidence):
        add(CITATION_GATE_CHECKS[0], GATE_SEVERITY_ERROR, error)

    # 規則 2（Evidence 屬於本次 run）：先檢查整個證據池，而不是只檢查被引用的部分。
    # 一筆屬於別次 run 的證據出現在本次 evidence.json 裡，本身就是可追溯性問題。
    for evidence_id in sorted(index):
        record_run_id = str(index[evidence_id].get("run_id") or "")
        if not record_run_id:
            add("evidence_belongs_to_run", GATE_SEVERITY_WARNING,
                f"{evidence_id}: 沒有 run_id，無法確認它屬於本次執行", evidence_ids=[evidence_id])
        elif run_id and record_run_id != str(run_id):
            add("evidence_belongs_to_run", GATE_SEVERITY_ERROR,
                f"{evidence_id}: run_id 為 {record_run_id!r}，不屬於本次執行 {str(run_id)!r}",
                evidence_ids=[evidence_id])

    # 被引用的 ID：Claim 的支持／反對／Fact，加上報告層級（LLM 或離線推理）宣稱引用的 ID。
    citations: dict = {}
    for claim in claim_list:
        claim_id = str(claim.get("claim_id") or "?")
        for kind, ids in _claim_cited_ids(claim).items():
            for evidence_id in ids:
                citations.setdefault(evidence_id, []).append((claim_id, kind))
    for evidence_id in cited_evidence_ids or ():
        citations.setdefault(str(evidence_id), []).append(("report", "report"))

    for evidence_id in sorted(citations):
        citing = ", ".join(sorted({claim_id for claim_id, _ in citations[evidence_id]}))
        record = index.get(evidence_id)
        if record is None:
            # 規則 1：引用了不存在的 Evidence。這是最嚴重的一種：讀者永遠追不到來源。
            add("evidence_id_exists", GATE_SEVERITY_ERROR,
                f"{evidence_id}: 被 {citing} 引用，但本次 run 沒有這筆 Evidence",
                evidence_ids=[evidence_id])
            continue
        record_run_id = str(record.get("run_id") or "")
        if run_id and record_run_id and record_run_id != str(run_id):
            # 規則 10：跨 run 引用。與規則 2 分開回報，因為兩者的處理方式不同：
            # 前者是「這份 evidence.json 混進了外來紀錄」，這裡是「報告真的引用了它」。
            add("no_cross_run_citation", GATE_SEVERITY_ERROR,
                f"{evidence_id}: 被 {citing} 引用，但它屬於另一次執行 {record_run_id!r}",
                evidence_ids=[evidence_id])
        if record.get("verification_status") == VERIFICATION_STATUS_REJECTED:
            # 規則 4：rejected 的 Evidence 不得進入主要報告。
            add("evidence_not_rejected", GATE_SEVERITY_ERROR,
                f"{evidence_id}: 狀態為 rejected，不得被 {citing} 引用", evidence_ids=[evidence_id])
        # 規則 3：被引用的 Evidence 必須自我可追溯。
        for field_name in GATE_REQUIRED_EVIDENCE_FIELDS:
            if field_name == "related_claim_ids" and not check_related_claim_ids:
                continue
            if field_name == "related_claim_ids" and all(
                    kind == "report" for _, kind in citations[evidence_id]):
                # 只出現在報告層級引用清單、沒有任何 Claim 使用的證據不必有 claim 連結。
                continue
            if not record.get(field_name):
                add("evidence_required_fields", GATE_SEVERITY_ERROR,
                    f"{evidence_id}: 被引用的 Evidence 缺少 {field_name}", evidence_ids=[evidence_id])
        # 規則 11（E2）：聚合證據被引用時要指出實際依據的子項。
        for severity, message in _cited_item_locator_findings(record, citing):
            add("cited_item_locator", severity, message, evidence_ids=[evidence_id])

    for claim in claim_list:
        claim_id = str(claim.get("claim_id") or "?")
        verdict = claim.get("verdict")
        cited = _claim_cited_ids(claim)
        supporting_records = [index[item] for item in cited["supporting"] if item in index]
        confidence = claim.get("confidence") if isinstance(claim.get("confidence"), dict) else {}

        # 規則 6：同一筆 Evidence 不得同時被當成支持與反對，除非 Claim 已明確標示混合／背景。
        overlap = sorted(set(cited["supporting"]) & set(cited["contradicting"]))
        if overlap:
            disclosed = _discloses_mixed_evidence(claim, overlap)
            add("support_contradiction_exclusive",
                GATE_SEVERITY_WARNING if disclosed else GATE_SEVERITY_ERROR,
                (f"{claim_id}: {overlap} 同時支持與反對本判斷，已標示為混合／背景證據" if disclosed
                 else f"{claim_id}: {overlap} 同時被當成支持與反對，且未標示為 mixed／context"),
                claim_id=claim_id, evidence_ids=overlap)

        # 規則 7：沒有支持證據就必須誠實輸出 insufficient_evidence。
        if not cited["supporting"] and verdict != VERDICT_INSUFFICIENT_EVIDENCE:
            add("claim_has_supporting_evidence", GATE_SEVERITY_ERROR,
                f"{claim_id}: 沒有支持證據，verdict 必須是 {VERDICT_INSUFFICIENT_EVIDENCE}，"
                f"實際為 {verdict!r}", claim_id=claim_id)

        # 規則 5：fallback 證據不得成為主要 Claim 的唯一支持。
        if (supporting_records and verdict != VERDICT_INSUFFICIENT_EVIDENCE
                and all(_is_fallback(record) for record in supporting_records)):
            add("fallback_not_sole_support", GATE_SEVERITY_ERROR,
                f"{claim_id}: 支持證據全部是 fallback／降級來源，不得據此給出 {verdict!r}",
                claim_id=claim_id, evidence_ids=cited["supporting"])

        # 規則 8：信心不得突破 hard cap。
        caps = _confidence_caps(claim, supporting_records)
        try:
            score = float(confidence.get("score"))
        except (TypeError, ValueError):
            score = None
        if score is not None:
            for name, cap in sorted(caps["claim"].items()):
                if score > float(cap) + _TOLERANCE:
                    add("confidence_within_cap", GATE_SEVERITY_ERROR,
                        f"{claim_id}: 信心 {score} 超過 {name} 的上限 {cap}", claim_id=claim_id)
            for name, cap in sorted(caps["evidence"].items()):
                if score > float(cap) + _TOLERANCE:
                    add("confidence_within_cap", GATE_SEVERITY_WARNING,
                        f"{claim_id}: 信心 {score} 高於全部支持證據共有的 {name} 上限 {cap}",
                        claim_id=claim_id, evidence_ids=cited["supporting"])

        # 規則 9：related_claim_ids 必須包含實際引用它的 Claim，雙向都要對得上。
        if check_related_claim_ids:
            claim_cited = set(cited["supporting"]) | set(cited["contradicting"])
            for evidence_id in sorted(claim_cited):
                record = index.get(evidence_id)
                if record is None:
                    continue  # 已由規則 1 回報
                linked = {str(item) for item in (record.get("related_claim_ids") or [])}
                if claim_id not in linked:
                    add("related_claim_ids_consistent", GATE_SEVERITY_ERROR,
                        f"{evidence_id}: 被 {claim_id} 引用，但 related_claim_ids 只有 "
                        f"{sorted(linked) or '空清單'}", claim_id=claim_id, evidence_ids=[evidence_id])

    if check_related_claim_ids:
        known_claim_ids = {str(claim.get("claim_id") or "") for claim in claim_list}
        for evidence_id in sorted(index):
            for claim_id in {str(item) for item in (index[evidence_id].get("related_claim_ids") or [])}:
                if claim_id not in known_claim_ids:
                    add("related_claim_ids_consistent", GATE_SEVERITY_ERROR,
                        f"{evidence_id}: related_claim_ids 指向不存在的 Claim {claim_id!r}",
                        evidence_ids=[evidence_id])
                    continue
                claim = next(item for item in claim_list if str(item.get("claim_id")) == claim_id)
                cited = _claim_cited_ids(claim)
                if evidence_id not in set(cited["supporting"]) | set(cited["contradicting"]):
                    add("related_claim_ids_consistent", GATE_SEVERITY_ERROR,
                        f"{evidence_id}: related_claim_ids 宣稱被 {claim_id} 使用，"
                        f"但該 Claim 沒有引用它", claim_id=claim_id, evidence_ids=[evidence_id])

    semantic = detect_semantic_risks(claim_list, evidence) + critique_semantic_findings(critique)

    errors = [item for item in findings if item["severity"] == GATE_SEVERITY_ERROR]
    warnings = [item for item in findings if item["severity"] == GATE_SEVERITY_WARNING]
    if errors:
        status = GATE_STATUS_FAIL
    elif warnings or semantic:
        status = GATE_STATUS_PASS_WITH_WARNINGS
    else:
        status = GATE_STATUS_PASS

    checks = {}
    for name in CITATION_GATE_CHECKS:
        hit = [item["severity"] for item in findings if item["check"] == name]
        checks[name] = ("fail" if GATE_SEVERITY_ERROR in hit
                        else "warn" if GATE_SEVERITY_WARNING in hit else "pass")

    return {
        "gate_version": CITATION_GATE_VERSION,
        "status": status,
        "run_id": str(run_id or ""),
        "claim_count": len(claim_list),
        "evidence_count": len(index),
        "cited_evidence_count": len([item for item in citations if item in index]),
        "uncited_evidence_ids": sorted(item for item in index if item not in citations),
        "checks": checks,
        "related_claim_ids_checked": bool(check_related_claim_ids),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": [item["message"] for item in errors],
        "warnings": [item["message"] for item in warnings],
        "findings": findings,
        "semantic_finding_count": len(semantic),
        "semantic_categories": sorted({item["category"] for item in semantic}),
        "semantic_findings": semantic,
    }
