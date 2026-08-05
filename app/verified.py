"""Helpers for human verified-outcome decisions (SQLite) + optional Chroma promote.

Human ACCEPT writes SQLite always, then may promote a concise lesson into
Chroma via ``save_finding`` (near-duplicate / id-safe). REJECT is SQLite-only.
Supervisor never calls this path — it must not auto-save to Chroma.
"""

from __future__ import annotations

from dataclasses import dataclass
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

# Keep Chroma lesson text short — evidence/fix only, never raw files/prompts.
_MAX_LESSON_FIELD_CHARS = 500


@dataclass(frozen=True)
class FindingDecisionResult:
    """SQLite verified outcome plus optional Chroma promotion status."""

    outcome: StoredVerifiedOutcome
    memory_promotion: dict[str, Any] | None = None


def _repo_relative_path(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(_ROOT.resolve()).as_posix()
    except ValueError:
        return path.replace("\\", "/")


def _truncate(text: str, limit: int = _MAX_LESSON_FIELD_CHARS) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def lesson_from_accepted_finding(
    finding: Finding,
    *,
    file_path: str,
    review_id: int,
    finding_index: int,
) -> dict[str, Any]:
    """Build a concise reusable Chroma lesson from a structured finding.

    Uses finding type, evidence, suggested fix, and normalized file context.
    Does not include human reason text, raw source files, or prompts.
    Stable ``id`` makes CLI/API retries of the same accept idempotent.
    """
    evidence = _truncate(finding.evidence)
    fix = _truncate(finding.suggested_fix) or "(see review evidence)"
    normalized = _repo_relative_path(file_path)
    return {
        "id": f"human-accept-r{review_id}-i{finding_index}",
        "type": finding.finding_type,
        "pattern": evidence or finding.finding_type,
        "bad_example": _truncate(evidence, 240) if evidence else "",
        "fix": fix,
        "source": f"human-accepted:{normalized}",
    }


def persist_worker_report(
    report: dict[str, Any] | None,
    *,
    db_path: Path | str | None = None,
    job_id: str | None = None,
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
    stored = save_review(
        review_result,
        gate_result,
        db_path=db_path,
        job_id=job_id,
    )
    try:
        from app.audit import STAGE_REVIEW_PERSISTED, log_audit_stage

        log_audit_stage(
            STAGE_REVIEW_PERSISTED,
            worker_name=stored.worker_name,
            job_id=job_id,
            detail={
                "review_id": stored.id,
                "accepted_count": stored.accepted_count,
                "needs_review_count": stored.needs_review_count,
                "file_path": stored.file_path,
            },
            db_path=db_path,
        )
    except Exception:  # noqa: BLE001
        pass
    return stored


def persist_combined_review(
    combined: dict[str, Any],
    *,
    db_path: Path | str | None = None,
    job_id: str | None = None,
) -> dict[str, int | None]:
    """Save Security (+ Architecture) review rows; return {worker: review_id}."""
    ids: dict[str, int | None] = {"security": None, "architecture": None}
    security = persist_worker_report(
        combined.get("security"), db_path=db_path, job_id=job_id
    )
    if security is not None:
        ids["security"] = security.id
    architecture = persist_worker_report(
        combined.get("architecture"), db_path=db_path, job_id=job_id
    )
    if architecture is not None:
        ids["architecture"] = architecture.id
    return ids


def _promote_accepted_lesson(
    finding: Finding,
    *,
    file_path: str,
    review_id: int,
    finding_index: int,
    outcome_id: int,
    worker_name: str,
    job_id: str | None,
    db_path: Path | str | None,
    chroma_persist_directory: Path | str | None,
) -> dict[str, Any]:
    """Write Chroma lesson after SQLite succeed; never raises to callers."""
    from app.audit import STAGE_CHROMA_PROMOTE, log_audit_stage
    from app.memory import save_finding

    lesson = lesson_from_accepted_finding(
        finding,
        file_path=file_path,
        review_id=review_id,
        finding_index=finding_index,
    )
    try:
        result = save_finding(lesson, persist_directory=chroma_persist_directory)
        status = str(result.get("status") or "unknown")
        detail = {
            "outcome_id": outcome_id,
            "review_id": review_id,
            "finding_index": finding_index,
            "finding_type": finding.finding_type,
            "status": status,
            "lesson_id": result.get("id") or result.get("matched_id"),
            "reason": result.get("reason"),
            "distance": result.get("distance"),
        }
        try:
            log_audit_stage(
                STAGE_CHROMA_PROMOTE,
                worker_name=worker_name,
                job_id=job_id,
                detail=detail,
                db_path=db_path,
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": status,
            "lesson_id": result.get("id"),
            "matched_id": result.get("matched_id"),
            "reason": result.get("reason"),
            "distance": result.get("distance"),
        }
    except Exception as exc:  # noqa: BLE001 — decision already in SQLite
        failure = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "lesson_id": None,
            "reason": "chroma promotion failed after verified outcome was saved",
        }
        try:
            log_audit_stage(
                STAGE_CHROMA_PROMOTE,
                worker_name=worker_name,
                job_id=job_id,
                detail={
                    "outcome_id": outcome_id,
                    "review_id": review_id,
                    "finding_index": finding_index,
                    "finding_type": finding.finding_type,
                    "status": "error",
                    "error": failure["error"],
                },
                db_path=db_path,
            )
        except Exception:  # noqa: BLE001
            pass
        return failure


def record_finding_decision(
    review_id: int,
    finding_index: int,
    *,
    accepted: bool,
    reason: str,
    linked_fix_commit: str | None = None,
    db_path: Path | str | None = None,
    chroma_persist_directory: Path | str | None = None,
) -> FindingDecisionResult:
    """Accept/reject one finding from a stored review by index (0-based).

    Always writes the verified outcome to SQLite. On human ACCEPT only, also
    attempts Chroma promotion via ``save_finding``. Chroma failures do not
    roll back the SQLite decision.
    """
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
    outcome = save_verified_outcome(
        finding,
        accepted=accepted,
        reason=reason_text,
        file_path=stored.file_path,
        review_id=stored.id,
        linked_fix_commit=linked_fix_commit,
        db_path=db_path,
    )
    try:
        from app.audit import STAGE_VERIFIED_OUTCOME, log_audit_stage

        log_audit_stage(
            STAGE_VERIFIED_OUTCOME,
            worker_name=stored.worker_name,
            job_id=stored.job_id,
            detail={
                "outcome_id": outcome.id,
                "review_id": stored.id,
                "finding_index": finding_index,
                "accepted": accepted,
                "reason": reason_text,
                "finding_type": finding.finding_type,
            },
            db_path=db_path,
        )
    except Exception:  # noqa: BLE001
        pass

    memory_promotion: dict[str, Any] | None = None
    if accepted:
        memory_promotion = _promote_accepted_lesson(
            finding,
            file_path=stored.file_path,
            review_id=stored.id,
            finding_index=finding_index,
            outcome_id=outcome.id,
            worker_name=stored.worker_name,
            job_id=stored.job_id,
            db_path=db_path,
            chroma_persist_directory=chroma_persist_directory,
        )

    return FindingDecisionResult(outcome=outcome, memory_promotion=memory_promotion)
