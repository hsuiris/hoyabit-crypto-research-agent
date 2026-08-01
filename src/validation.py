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
    CREDIBILITY_COMPONENT_KEYS,
    HARD_CAPS,
    NON_SUBSTANTIVE_VERIFICATION_STATUSES,
    SOURCE_TYPE_FALLBACK_FIXTURE,
    SOURCE_TYPES,
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
