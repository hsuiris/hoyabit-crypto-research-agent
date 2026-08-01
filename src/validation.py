"""Evidence and execution-log validation.

Two layers of checks:

* the original metadata contract -- every record must be traceable to a source, a fetch time and a
  claim, or the run stops;
* the T3 credibility contract -- a scored record must be able to explain its own number. The score
  has to match the breakdown, no hard cap may be exceeded, and a degraded record must be labelled as
  degraded. This is what stops a downstream module (or a model) from quietly writing a flattering
  score onto an unverifiable record.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from .schemas import (
    CONFIDENCE_COMPONENT_KEYS,
    CONFIDENCE_LEVELS,
    CONFIDENCE_TYPE_HEURISTIC,
    CREDIBILITY_COMPONENT_KEYS,
    HARD_CAPS,
    NON_SUBSTANTIVE_VERIFICATION_STATUSES,
    SOURCE_TYPE_FALLBACK_FIXTURE,
    SOURCE_TYPES,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICTS,
    VERIFICATION_STATUS_REJECTED,
    VERIFICATION_STATUSES,
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
    missing_refs = set(referenced_ids) - seen
    errors.extend(f"report references unknown evidence_id: {item}" for item in sorted(missing_refs))
    return errors


# --------------------------------------------------------------------------------------
# T4 — Claim graph 的結構檢查
# --------------------------------------------------------------------------------------

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
        if overlap:
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
