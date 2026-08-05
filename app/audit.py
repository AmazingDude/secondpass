"""Queryable pipeline audit trail (SQLite), separate from hooks.py live logs.

Storage choice for prompt I/O: redacted summaries only — per-message role,
character lengths, and a short preview. Full prompts can exceed practical
SQLite/row size and are noisy for "why trust this" inspection; lengths +
preview are enough to reconstruct which stage ran and roughly what was sent.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from app.persistence import StoredAuditEvent, list_audit_events, save_audit_event

# Pipeline stage names (stable for readers / tests).
STAGE_REVIEW_START = "review_start"
STAGE_PROMPT_IO = "prompt_io"
STAGE_SCHEMA_VALIDATION = "schema_validation"
STAGE_CONFIDENCE_GATE = "confidence_gate"
STAGE_CHROMA_SAVE_SKIP = "chroma_save_skip"
STAGE_CHROMA_PROMOTE = "chroma_promote"
STAGE_REVIEW_PERSISTED = "review_persisted"
STAGE_VERIFIED_OUTCOME = "verified_outcome_write"
STAGE_REVIEW_COMPLETE = "review_complete"

_PREVIEW_CHARS = 240

_current_job_id: ContextVar[str | None] = ContextVar(
    "secondpass_audit_job_id", default=None
)
_current_worker: ContextVar[str | None] = ContextVar(
    "secondpass_audit_worker", default=None
)


def get_current_job_id() -> str | None:
    return _current_job_id.get()


def get_current_worker_name() -> str | None:
    return _current_worker.get()


@contextmanager
def audit_scope(job_id: str) -> Iterator[None]:
    """Bind all audit writes in this block to ``job_id``."""
    token = _current_job_id.set(job_id)
    try:
        yield
    finally:
        _current_job_id.reset(token)


@contextmanager
def audit_worker_scope(worker_name: str) -> Iterator[None]:
    """Mark stages as belonging to security / architecture / supervisor."""
    token = _current_worker.set(worker_name)
    try:
        yield
    finally:
        _current_worker.reset(token)


def summarize_messages(messages: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Redacted prompt summary: roles, lengths, short previews — not full text."""
    items: list[dict[str, Any]] = []
    total_chars = 0
    for message in messages or []:
        content = message.get("content")
        if content is None:
            text = ""
        elif isinstance(content, str):
            text = content
        else:
            text = json.dumps(content, default=str)
        total_chars += len(text)
        items.append(
            {
                "role": message.get("role"),
                "chars": len(text),
                "preview": text[:_PREVIEW_CHARS]
                + ("..." if len(text) > _PREVIEW_CHARS else ""),
            }
        )
    return {
        "message_count": len(items),
        "total_chars": total_chars,
        "messages": items,
        "storage": "redacted_summary",
    }


def summarize_model_out(content: str | None) -> dict[str, Any]:
    text = content or ""
    return {
        "chars": len(text),
        "preview": text[:_PREVIEW_CHARS] + ("..." if len(text) > _PREVIEW_CHARS else ""),
        "storage": "redacted_summary",
    }


def log_audit_stage(
    stage: str,
    *,
    detail: dict[str, Any] | None = None,
    worker_name: str | None = None,
    job_id: str | None = None,
    db_path: Any = None,
) -> StoredAuditEvent | None:
    """Persist one stage event when a job_id is in scope (or passed explicitly)."""
    resolved_job = job_id if job_id is not None else get_current_job_id()
    if not resolved_job:
        return None
    resolved_worker = (
        worker_name if worker_name is not None else get_current_worker_name()
    )
    return save_audit_event(
        resolved_job,
        stage,
        worker_name=resolved_worker,
        detail=detail or {},
        db_path=db_path,
    )


def get_audit_trail(
    job_id: str,
    *,
    db_path: Any = None,
) -> list[StoredAuditEvent]:
    """One ordered trail spanning all workers for this submission."""
    return list_audit_events(job_id, db_path=db_path)
