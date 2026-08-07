"""Formatting-only checks for live stderr trace lines."""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO

from rich.console import Console

from app import hooks
from app.hooks import _live_clock, _print_agent_stderr, _print_tool_stderr


def test_live_clock_is_hhmmss_only() -> None:
    now = datetime(2026, 8, 3, 13, 35, 39, 75297, tzinfo=timezone.utc)
    assert _live_clock(now) == "13:35:39"


def test_agent_and_tool_stderr_lines_align_and_color(monkeypatch) -> None:
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="standard",
        legacy_windows=False,
        highlight=False,
        soft_wrap=True,
        width=140,
    )
    monkeypatch.setattr(hooks, "_stderr_console", console)
    now = datetime(2026, 8, 3, 13, 35, 39, tzinfo=timezone.utc)

    _print_agent_stderr(now, "supervisor starting review of sample.py")
    _print_tool_stderr(
        now,
        agent_name="supervisor",
        tool_name="run_static_scan",
        status="ok",
        duration_ms=12.5,
        args_text='{"args": [["sample.py"]]}',
    )

    rendered = buf.getvalue()
    assert "[agent]" in rendered
    assert "[tool]" in rendered
    assert "13:35:39" in rendered
    assert "2026-08-03" not in rendered
    assert "agent_event" in rendered
    assert "agent=supervisor" in rendered
    assert "tool=run_static_scan" in rendered
    assert "duration_ms=12.5" in rendered
    assert 'args={"args": [["sample.py"]]}' in rendered
    # Color codes present when force_terminal + non-legacy Windows
    assert "\x1b[" in rendered


def test_live_stderr_scope_can_suppress_and_prefix(monkeypatch) -> None:
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        legacy_windows=False,
        highlight=False,
        soft_wrap=True,
        width=140,
    )
    monkeypatch.setattr(hooks, "_stderr_console", console)
    now = datetime(2026, 8, 3, 13, 35, 39, tzinfo=timezone.utc)

    with hooks.live_stderr_scope(enabled=False):
        _print_agent_stderr(now, "should not appear")
    assert buf.getvalue() == ""

    with hooks.live_stderr_scope(enabled=True, file_label="checkout_handler.py"):
        _print_agent_stderr(now, "architecture_worker reviewing")
    rendered = buf.getvalue()
    assert "[checkout_handler.py]" in rendered
    assert "[agent]" in rendered
    assert "architecture_worker reviewing" in rendered
