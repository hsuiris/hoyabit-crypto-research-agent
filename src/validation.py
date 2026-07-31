"""Evidence and execution-log validation."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime


REQUIRED_FIELDS = {"evidence_id", "source", "source_url", "fetched_at", "data_type", "coin", "time_range", "content", "content_reference", "related_claim", "reliability_score"}


def validate_evidence(evidence: list[object], referenced_ids: list[str]) -> list[str]:
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
    missing_refs = set(referenced_ids) - seen
    errors.extend(f"report references unknown evidence_id: {item}" for item in sorted(missing_refs))
    return errors
