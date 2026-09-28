"""Lightweight tool-call logging hook."""

from __future__ import annotations

import functools
import inspect
import json
import re
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from rich.console import Console
from rich.text import Text

F = TypeVar("F", bound=Callable[..., Any])

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_PATH = _ROOT / "tool_calls.log"

# Live stderr only (file logs keep full ISO timestamps / plain text).
_stderr_console = Console(
    file=sys.stderr,
    highlight=False,
    soft_wrap=True,
    legacy_windows=False,
)
_AGENT_FIELD_WIDTH = 28  # "agent=architecture_worker"
_TOOL_FIELD_WIDTH = 24  # "tool=run_static_scan"
_KIND_FIELD_WIDTH = 12  # "agent_event"

_SAFE_AGENT_EVENTS = frozenset({
    "supervisor -> security worker",
    "supervisor -> architecture worker",
    "supervisor -> memory_worker",
    "supervisor -> web_worker",
    "supervisor running honest logic-review assessment",
    "logic-review inconclusive — rate limited",
    "logic-review inconclusive — invalid model response",
    "architecture_worker inconclusive — rate limited",
    "architecture_worker inconclusive — invalid model response",
    "supervisor received finding; deciding worker routing",
    "supervisor skip save_finding — verified outcomes require human accept/reject",
})
_AGENT_EVENT_PREFIXES = (
    ("supervisor starting review of ", "supervisor starting review"),
    ("supervisor path review starting for ", "supervisor path review starting"),
    ("supervisor routing: ", "supervisor routing: selected workers"),
    ("supervisor routing failed (", "supervisor routing failed"),
    ("supervisor scan error: ", "supervisor scan error"),
    ("memory_worker -> supervisor ", "memory_worker -> supervisor"),
    ("web_worker -> supervisor ", "web_worker -> supervisor"),
    ("logic-review assessment failed (", "logic-review inconclusive"),
    ("logic-review dropped architecture-category bleed ", "logic-review filtered issue"),
    ("logic-review: clean —", "logic-review: clean"),
    ("logic-review: inconclusive —", "logic-review: inconclusive"),
    ("logic-review: unverified —", "logic-review: unverified"),
    ("architecture_worker reviewing ", "architecture_worker reviewing"),
    ("architecture_worker failed (", "architecture_worker inconclusive"),
    ("architecture_worker: clean —", "architecture_worker: clean"),
    ("architecture_worker: inconclusive —", "architecture_worker: inconclusive"),
    ("architecture_worker: claim_unverified —", "architecture_worker: claim_unverified"),
    ("architecture_worker claimed issues but none met the evidence bar;", "architecture_worker: claim_unverified"),
    ("supervisor aggregated report: ", "supervisor aggregated report"),
)

# Which multi-agent role is currently executing (supervisor / memory_worker / …).
_current_agent: ContextVar[str] = ContextVar("secondpass_agent", default="system")

# Live stderr agent/tool traces (multi-file quiet mode turns these off).
_stderr_live_enabled: ContextVar[bool] = ContextVar(
    "secondpass_stderr_live", default=True
)
_stderr_file_label: ContextVar[str | None] = ContextVar(
    "secondpass_stderr_file", default=None
)


def get_current_agent() -> str:
    return _current_agent.get()


@contextmanager
def agent_scope(agent_name: str) -> Iterator[None]:
    """Mark tool calls made inside this block as belonging to ``agent_name``."""
    token = _current_agent.set(agent_name)
    try:
        yield
    finally:
        _current_agent.reset(token)


@contextmanager
def live_stderr_scope(
    *,
    enabled: bool = True,
    file_label: str | None = None,
) -> Iterator[None]:
    """Control live stderr traces; optional short filename prefix for parallel runs."""
    enabled_token = _stderr_live_enabled.set(enabled)
    label_token = _stderr_file_label.set(file_label)
    try:
        yield
    finally:
        _stderr_live_enabled.reset(enabled_token)
        _stderr_file_label.reset(label_token)


def _live_clock(now: datetime) -> str:
    return now.strftime("%H:%M:%S")


def _prepend_file_label(line: Text) -> None:
    label = _stderr_file_label.get()
    if label:
        line.append(f"[{label}] ", style="dim")


def _print_agent_stderr(now: datetime, message: str) -> None:
    if not _stderr_live_enabled.get():
        return
    line = Text()
    _prepend_file_label(line)
    line.append("[agent]", style="bold cyan")
    line.append(" ")
    line.append(_live_clock(now), style="dim")
    line.append(" | ", style="dim")
    line.append(f"{'agent_event':<{_KIND_FIELD_WIDTH}}", style="cyan")
    line.append(" | ", style="dim")
    line.append(message, style="cyan")
    _stderr_console.print(line)


def _print_tool_stderr(
    now: datetime,
    *,
    agent_name: str,
    tool_name: str,
    status: str,
    duration_ms: float,
    args_text: str,
) -> None:
    if not _stderr_live_enabled.get():
        return
    agent_field = f"agent={agent_name}"
    tool_field = f"tool={tool_name}"
    line = Text()
    _prepend_file_label(line)
    line.append("[tool] ", style="bold magenta")  # pad to same width as [agent]
    line.append(_live_clock(now), style="dim")
    line.append(" | ", style="dim")
    line.append(f"{agent_field:<{_AGENT_FIELD_WIDTH}}", style="magenta")
    line.append(" | ", style="dim")
    line.append(f"{tool_field:<{_TOOL_FIELD_WIDTH}}", style="magenta")
    line.append(" | ", style="dim")
    status_style = "red" if status.startswith("error=") else "green"
    line.append(f"{status:<18}", style=status_style)
    line.append(" | ", style="dim")
    line.append(f"duration_ms={duration_ms:.1f}", style="dim")
    line.append(" | ", style="dim")
    line.append(f"args={args_text}", style="dim")
    _stderr_console.print(line)


def _persist_hook_event(
    stage: str,
    *,
    worker_name: str,
    detail: dict[str, Any],
) -> None:
    """When a review job_id is in audit_scope, mirror this hook into SQLite.

    Failures never break the live review — stderr / file logging already ran.
    """
    try:
        from app.audit import get_current_job_id, log_audit_stage

        job_id = get_current_job_id()
        if not job_id:
            return
        log_audit_stage(
            stage,
            worker_name=worker_name,
            detail=detail,
            job_id=job_id,
        )
    except Exception:  # noqa: BLE001 — hooks must not fail the review
        pass


def log_agent_event(message: str, *, log_file: str | Path | None = _DEFAULT_LOG_PATH) -> None:
    """Log a fixed progress label, never caller-supplied free-form content."""
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    agent_name = get_current_agent()
    if message in _SAFE_AGENT_EVENTS:
        label = message
    elif match := re.match(r"^logic-review: \d+ concrete issue\(s\) — (.*)", message):
        label = (
            "logic-review: inconclusive with issues"
            if match.group(1).startswith("inconclusive")
            else "logic-review: concrete issue(s)"
        )
    elif match := re.match(r"^architecture_worker: \d+ issue\(s\) — (.*)", message):
        label = (
            "architecture_worker: inconclusive with issues"
            if match.group(1).startswith("inconclusive")
            else "architecture_worker: claimed issues"
        )
    else:
        label = next(
            (safe for prefix, safe in _AGENT_EVENT_PREFIXES if message.startswith(prefix)),
            "Agent activity",
        )
    line = f"{timestamp} | agent_event | {label}"
    _print_agent_stderr(now, label)
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    from app.audit import STAGE_AGENT_EVENT

    _persist_hook_event(
        STAGE_AGENT_EVENT,
        worker_name=agent_name,
        detail={
            "kind": "agent_event",
            "agent": agent_name,
            "message": label,
        },
    )


def _format_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    # Omit values and keyword names before any sink; truncation is not redaction.
    return json.dumps({
        "positional_count": len(args),
        "keyword_count": len(kwargs),
        "storage": "metadata_only",
    })


def log_tool_call(
    func: F | None = None,
    *,
    log_file: str | Path | None = _DEFAULT_LOG_PATH,
) -> F | Callable[[F], F]:
    """Log tool identity, status, duration and argument counts, not argument content.

    Free-form agent events and optional file labels have separate log behavior.
    """

    def decorator(inner: F) -> F:
        @functools.wraps(inner)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tool_name = inner.__name__
            agent_name = get_current_agent()
            started = time.perf_counter()
            error: BaseException | None = None
            try:
                return inner(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 — re-raised below
                error = exc
                raise
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000
                now = datetime.now(timezone.utc)
                timestamp = now.isoformat()
                status = f"error={type(error).__name__}" if error else "ok"
                args_text = _format_args(args, kwargs)
                line = (
                    f"{timestamp} | agent={agent_name} | tool={tool_name} | "
                    f"{status} | duration_ms={elapsed_ms:.1f} | "
                    f"args={args_text}"
                )
                _print_tool_stderr(
                    now,
                    agent_name=agent_name,
                    tool_name=tool_name,
                    status=status,
                    duration_ms=elapsed_ms,
                    args_text=args_text,
                )
                if log_file is not None:
                    path = Path(log_file)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as handle:
                        handle.write(line + "\n")
                from app.audit import STAGE_TOOL_CALL

                _persist_hook_event(
                    STAGE_TOOL_CALL,
                    worker_name=agent_name,
                    detail={
                        "kind": "tool",
                        "agent": agent_name,
                        "tool": tool_name,
                        "ok": error is None,
                        "status": status,
                        "duration_ms": round(elapsed_ms, 1),
                        "args": args_text,
                    },
                )

        # Preserve signature for introspection / tooling.
        wrapper.__signature__ = inspect.signature(inner)  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    if func is not None:
        return decorator(func)
    return decorator
