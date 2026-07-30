"""Helpers for human verified-outcome decisions (SQLite), not Chroma lessons."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.confidence_gate import GateResult
from app.persistence import (
    _ROOT,
    StoredReview,
    StoredVerifiedOutcome,
    get_review,
    save_review,
    save_verified_outcome,
)
from app.schema import Finding, ReviewResult


def _repo_relative_path(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(_ROOT.resolve()).as_posix()
    except ValueError:
        return path.replace("\\", "/")


def persist_worker_report(
    report: dict[str, Any] | None,
    *,
    db_path: Path | str | None = None,
) -> StoredReview | None:
    """Persist one worker report's ReviewResult + GateResult if present."""
    if not report:
        return None
    raw_result = report.get("review_result")
    raw_gate = report.get("gate_result")
    if not raw_result or not raw_gate:
        return None
    review_result = ReviewResult.model_validate(raw_result)
    review_result = review_result.model_copy(
        update={"file_path": _repo_relative_path(review_result.file_path)}
    )
    gate_result = GateResult.model_validate(raw_gate)
    return save_review(review_result, gate_result, db_path=db_path)


def persist_combined_review(
    combined: dict[str, Any],
    *,
    db_path: Path | str | None = None,
) -> dict[str, int | None]:
    """Save Security (+ Architecture) review rows; return {worker: review_id}."""
    ids: dict[str, int | None] = {"security": None, "architecture": None}
    security = persist_worker_report(combined.get("security"), db_path=db_path)
    if security is not None:
        ids["security"] = security.id
    architecture = persist_worker_report(combined.get("architecture"), db_path=db_path)
    if architecture is not None:
        ids["architecture"] = architecture.id
    return ids


def record_finding_decision(
    review_id: int,
    finding_index: int,
    *,
    accepted: bool,
    reason: str,
    linked_fix_commit: str | None = None,
    db_path: Path | str | None = None,
) -> StoredVerifiedOutcome:
    """Accept/reject one finding from a stored review by index (0-based)."""
    reason_text = reason.strip()
    if not reason_text:
        raise ValueError("reason is required and must be non-empty")

    stored = get_review(review_id, db_path=db_path)
    if stored is None:
        raise ValueError(f"No review with id={review_id}")

    findings = stored.review_result.findings
    if finding_index < 0 or finding_index >= len(findings):
        raise ValueError(
            f"finding index {finding_index} out of range "
            f"(review {review_id} has {len(findings)} finding(s))"
        )

    finding: Finding = findings[finding_index]
    return save_verified_outcome(
        finding,
        accepted=accepted,
        reason=reason_text,
        file_path=stored.file_path,
        review_id=stored.id,
        linked_fix_commit=linked_fix_commit,
        db_path=db_path,
    )
