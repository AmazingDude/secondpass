"""Unit tests for LLM temperature helpers (no live provider calls)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.llm import (
    DEFAULT_TEMPERATURE,
    TEMPERATURE_ZERO_FALLBACK,
    LLMRateLimitedError,
    chat,
    format_temperature_attempt,
    resolve_chat_temperature,
    temperature_attempt_values,
)
from app.workers.common import run_tool_loop


def test_resolve_none_defaults_to_zero() -> None:
    assert resolve_chat_temperature(None) == DEFAULT_TEMPERATURE
    assert resolve_chat_temperature(None) == 0.0


def test_resolve_keeps_explicit_temperature() -> None:
    assert resolve_chat_temperature(0.7) == 0.7
    assert resolve_chat_temperature(0) == 0.0


def test_temperature_attempts_for_zero_include_fallback_then_omit() -> None:
    assert temperature_attempt_values(0.0) == [
        0.0,
        TEMPERATURE_ZERO_FALLBACK,
        None,
    ]


def test_temperature_attempts_for_nonzero_are_single() -> None:
    assert temperature_attempt_values(0.5) == [0.5]


def test_format_temperature_attempt_labels() -> None:
    assert format_temperature_attempt(0.0) == "0.0"
    assert format_temperature_attempt(0.01) == "0.01"
    assert format_temperature_attempt(None) == "omitted"


def test_chat_logs_fallback_when_zero_rejected(monkeypatch) -> None:
    events: list[str] = []
    temperatures_seen: list[float | None] = []

    class _TempReject(Exception):
        pass

    class _FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            temp = kwargs.get("temperature", "__omitted__")
            temperatures_seen.append(None if temp == "__omitted__" else temp)
            if "temperature" in kwargs and kwargs["temperature"] == 0.0:
                raise _TempReject("Invalid temperature: must be > 0")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("app.llm.OpenAI", _FakeClient)
    monkeypatch.setattr("app.llm.BadRequestError", _TempReject)
    monkeypatch.setattr(
        "app.llm.log_agent_event",
        lambda message, **kwargs: events.append(message),
    )

    chat([{"role": "user", "content": "hi"}], temperature=0)

    assert temperatures_seen[0] == 0.0
    assert temperatures_seen[1] == TEMPERATURE_ZERO_FALLBACK
    assert any("provider rejected 0.0" in event for event in events)
    assert any(
        "fell back to 0.01" in event and "rejected [0.0]" in event for event in events
    )


def test_chat_logs_when_zero_accepted_first_try(monkeypatch) -> None:
    events: list[str] = []

    class _FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            assert kwargs.get("temperature") == 0.0
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("app.llm.OpenAI", _FakeClient)
    monkeypatch.setattr(
        "app.llm.log_agent_event",
        lambda message, **kwargs: events.append(message),
    )

    chat([{"role": "user", "content": "hi"}], temperature=0)

    assert any("using 0.0" in event for event in events)
    assert not any("fell back" in event for event in events)


def test_chat_rate_limit_raises_llm_rate_limited_and_logs(monkeypatch) -> None:
    events: list[str] = []

    class _RateLimit(Exception):
        status_code = 429

    class _FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            raise _RateLimit(
                "Error code: 429 - Rate limit reached for model "
                "llama-3.3-70b-versatile on tokens per day (TPD)"
            )

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("app.llm.OpenAI", _FakeClient)
    monkeypatch.setattr("app.llm.RateLimitError", _RateLimit)
    monkeypatch.setattr(
        "app.llm.log_agent_event",
        lambda message, **kwargs: events.append(message),
    )

    with pytest.raises(LLMRateLimitedError, match="skipped — rate limited"):
        chat([{"role": "user", "content": "hi"}], temperature=0)

    assert any("skipped — rate limited" in event for event in events)
    assert not any("Traceback" in event for event in events)


def test_run_tool_loop_degrades_on_rate_limit(monkeypatch) -> None:
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise LLMRateLimitedError("skipped — rate limited")

    monkeypatch.setattr("app.workers.common.chat", _boom)

    final, failures = run_tool_loop(
        agent_name="memory_worker",
        system_prompt="sys",
        user_content="user",
        tools=None,
        handlers={},
        max_iterations=2,
    )

    assert failures == 1
    assert final.get("rate_limited") is True
    assert final.get("error") == "skipped — rate limited"


def test_logic_review_degrades_on_rate_limit(monkeypatch, tmp_path) -> None:
    target = tmp_path / "notes.py"
    target.write_text("def get_note():\n    return 1\n", encoding="utf-8")

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise LLMRateLimitedError("skipped — rate limited")

    monkeypatch.setattr("app.agent.chat", _boom)

    from app.agent import assess_logic_review

    result = assess_logic_review(str(target))
    assert result["has_issues"] is False
    assert result["inconclusive"] is True
    assert result["status"] == "inconclusive"
    assert result["summary"] == "inconclusive — rate limited"
    assert result["failures"] == 1
