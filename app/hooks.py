"""Lightweight tool-call logging hook."""

from __future__ import annotations

import functools
import inspect
import json
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_PATH = _ROOT / "tool_calls.log"
_MAX_ARG_CHARS = 400

# Which multi-agent role is currently executing (supervisor / memory_worker / …).
_current_agent: ContextVar[str] = ContextVar("secondpass_agent", default="system")


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


def log_agent_event(message: str, *, log_file: str | Path | None = _DEFAULT_LOG_PATH) -> None:
    """Log a multi-agent hand-off or decision (not a tool call)."""
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"{timestamp} | agent_event | {message}"
    print(f"[agent] {line}", file=sys.stderr, flush=True)
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _truncate(value: str, limit: int = _MAX_ARG_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _format_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    if args:
        payload["args"] = list(args)
    if kwargs:
        payload["kwargs"] = kwargs
    try:
        rendered = json.dumps(payload, default=str, ensure_ascii=False)
    except TypeError:
        rendered = repr(payload)
    return _truncate(rendered)


def log_tool_call(
    func: F | None = None,
    *,
    log_file: str | Path | None = _DEFAULT_LOG_PATH,
) -> F | Callable[[F], F]:
    """Wrap a tool function and log timestamp, agent, name, args, and duration."""

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
                timestamp = datetime.now(timezone.utc).isoformat()
                status = f"error={type(error).__name__}" if error else "ok"
                line = (
                    f"{timestamp} | agent={agent_name} | tool={tool_name} | "
                    f"{status} | duration_ms={elapsed_ms:.1f} | "
                    f"args={_format_args(args, kwargs)}"
                )
                # stderr keeps stdout clean for MCP stdio JSON-RPC; CLIs still show logs.
                print(f"[tool] {line}", file=sys.stderr, flush=True)
                if log_file is not None:
                    path = Path(log_file)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as handle:
                        handle.write(line + "\n")

        # Preserve signature for introspection / tooling.
        wrapper.__signature__ = inspect.signature(inner)  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    if func is not None:
        return decorator(func)
    return decorator
