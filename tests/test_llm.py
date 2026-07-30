"""Unit tests for LLM temperature helpers (no live provider calls)."""

from __future__ import annotations

from app.llm import (
    DEFAULT_TEMPERATURE,
    TEMPERATURE_ZERO_FALLBACK,
    resolve_chat_temperature,
    temperature_attempt_values,
)


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
