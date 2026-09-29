"""Queryable pipeline audit trail (SQLite).

Includes pipeline stages (schema_validation, confidence_gate, …) and, when a
job_id is in audit_scope, CLI-equivalent hook rows from hooks.py
(agent_event / tool_call) so one poll returns a mixed chronological feed.

Prompt I/O summaries retain roles and character counts, never text previews.
Pipeline stage details retain only known progress metadata; review start/finish
paths remain for directory progress. Hook logs have a separate content policy.
This does not rewrite historical records or sanitize stored review results.
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
# Live CLI-equivalent hook events (hooks.py), stored in the same audit_events
# table so one job_id-keyed poll returns stages + agent/tool lines in order.
STAGE_AGENT_EVENT = "agent_event"
STAGE_TOOL_CALL = "tool_call"

HOOK_STAGES = frozenset({STAGE_AGENT_EVENT, STAGE_TOOL_CALL})
_PROMPT_ROLES = frozenset({"system", "developer", "user", "assistant", "tool", "function"})


def event_kind(stage: str) -> str:
    """Classify an audit row for API/UI: stage | agent_event | tool."""
    if stage == STAGE_AGENT_EVENT:
        return "agent_event"
    if stage == STAGE_TOOL_CALL:
        return "tool"
    return "stage"

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
    """Prompt metadata only: roles and lengths, with no content retained."""
    items: list[dict[str, Any]] = []
    total_chars = 0
    for message in messages or []:
        role = message.get("role")
        if not isinstance(role, str) or role not in _PROMPT_ROLES:
            role = "unknown"
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
                "role": role,
                "chars": len(text),
            }
        )
    return {
        "message_count": len(items),
        "total_chars": total_chars,
        "messages": items,
        "storage": "metadata_only",
    }


def summarize_model_out(content: str | None) -> dict[str, Any]:
    """Response metadata only; even short content can contain a secret."""
    text = content or ""
    return {
        "chars": len(text),
        "storage": "metadata_only",
    }


_STAGE_INT_FIELDS = {
    STAGE_SCHEMA_VALIDATION: ("finding_count",),
    STAGE_CONFIDENCE_GATE: (
        "threshold", "accepted_count", "needs_review_count", "finding_count"
    ),
    STAGE_REVIEW_PERSISTED: ("review_id", "accepted_count", "needs_review_count"),
    STAGE_REVIEW_COMPLETE: ("accepted_count", "needs_review_count"),
    STAGE_VERIFIED_OUTCOME: ("outcome_id", "review_id", "finding_index"),
    STAGE_CHROMA_PROMOTE: ("outcome_id", "review_id", "finding_index"),
}
_STAGE_BOOL_FIELDS = {
    STAGE_SCHEMA_VALIDATION: ("ok",),
    STAGE_REVIEW_START: ("run_architecture",),
    STAGE_VERIFIED_OUTCOME: ("accepted",),
}


def _metadata_only_stage_detail(stage: str, detail: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only known, typed progress fields; unknown stages store no detail."""
    source = detail or {}
    if stage in HOOK_STAGES:
        # Hook writers normalize their own message/argument content before calling us.
        return source

    safe: dict[str, Any] = {}
    for key in _STAGE_INT_FIELDS.get(stage, ()):
        value = source.get(key)
        if type(value) is int and value >= 0:
            safe[key] = value
    for key in _STAGE_BOOL_FIELDS.get(stage, ()):
        value = source.get(key)
        if type(value) is bool:
            safe[key] = value

    if stage in (STAGE_REVIEW_START, STAGE_REVIEW_COMPLETE):
        # Directory progress currently correlates per-file events by path.
        path = source.get("path")
        if type(path) is str:
            safe["path"] = path
    if stage == STAGE_REVIEW_COMPLETE:
        workers = source.get("workers_run")
        if type(workers) is list and all(
            type(worker) is str and worker in ("security", "architecture")
            for worker in workers
        ):
            safe["workers_run"] = workers
        review_ids = source.get("persisted_review_ids")
        if type(review_ids) is dict:
            safe["persisted_review_ids"] = {
                worker: review_ids[worker]
                for worker in ("security", "architecture")
                if type(review_ids.get(worker)) is int and review_ids[worker] >= 0
            }
    elif stage == STAGE_CHROMA_PROMOTE:
        status = source.get("status")
        if type(status) is str:
            safe["status"] = (
                status if status in ("saved", "skipped", "error") else "unknown"
            )
    elif stage == STAGE_PROMPT_IO:
        prompt = source.get("prompt")
        if type(prompt) is dict:
            items = []
            messages = prompt.get("messages")
            if type(messages) is list:
                for item in messages:
                    if type(item) is not dict:
                        continue
                    role = item.get("role")
                    if type(role) is not str or role not in _PROMPT_ROLES:
                        role = "unknown"
                    chars = item.get("chars")
                    items.append({
                        "role": role,
                        "chars": chars if type(chars) is int and chars >= 0 else 0,
                    })
            safe["prompt"] = {
                "message_count": len(items),
                "total_chars": sum(item["chars"] for item in items),
                "messages": items,
                "storage": "metadata_only",
            }
        model_out = source.get("model_out")
        if type(model_out) is dict:
            chars = model_out.get("chars")
            safe["model_out"] = {
                "chars": chars if type(chars) is int and chars >= 0 else 0,
                "storage": "metadata_only",
            }
    return safe


def log_audit_stage(
    stage: str,
    *,
    detail: dict[str, Any] | None = None,
    worker_name: str | None = None,
    job_id: str | None = None,
    db_path: Any = None,
) -> StoredAuditEvent | None:
    """Persist metadata-only stage detail when a job_id is in scope."""
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
        detail=_metadata_only_stage_detail(stage, detail),
        db_path=db_path,
    )


def get_audit_trail(
    job_id: str,
    *,
    db_path: Any = None,
) -> list[StoredAuditEvent]:
    """One ordered trail spanning all workers for this submission."""
    return list_audit_events(job_id, db_path=db_path)
