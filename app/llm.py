"""Minimal provider-agnostic LLM client."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

try:
    from openai import BadRequestError
except ImportError:  # pragma: no cover
    BadRequestError = Exception  # type: ignore[misc, assignment]

try:
    from openai import RateLimitError
except ImportError:  # pragma: no cover
    RateLimitError = type("RateLimitError", (Exception,), {})  # type: ignore[misc, assignment]

from app.hooks import log_agent_event

load_dotenv()


class LLMRateLimitedError(RuntimeError):
    """Provider returned HTTP 429 / rate limit. Callers should skip this step."""

_PROVIDERS = {
    "groq": {
        "api_key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
    },
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
    },
    "openrouter": {
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/auto",
    },
}

# Review/analysis default: pin sampling as low as the provider allows.
DEFAULT_TEMPERATURE = 0.0
# Some OpenAI-compatible endpoints reject exactly 0; retry with a tiny epsilon.
TEMPERATURE_ZERO_FALLBACK = 0.01


def resolve_chat_temperature(temperature: float | None) -> float:
    """None → DEFAULT_TEMPERATURE (0.0) for review/analysis determinism."""
    if temperature is None:
        return DEFAULT_TEMPERATURE
    return float(temperature)


def temperature_attempt_values(temperature: float) -> list[float | None]:
    """Ordered temperature tries: requested, then fallbacks that omit on failure.

    ``None`` in the list means "omit the temperature field" for providers that
    reject the parameter entirely.
    """
    attempts: list[float | None] = [temperature]
    if temperature == 0.0:
        attempts.append(TEMPERATURE_ZERO_FALLBACK)
        attempts.append(None)
    return attempts


def format_temperature_attempt(attempt: float | None) -> str:
    """Human-readable label for logs / tests."""
    if attempt is None:
        return "omitted"
    return str(attempt)


def _is_temperature_reject(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "temperature" in text


def _is_rate_limit(exc: BaseException) -> bool:
    """True for OpenAI-shaped 429s and the Groq TPD messages we hit in benchmarks."""
    if isinstance(exc, RateLimitError):
        return True
    if type(exc).__name__ in {"RateLimitError", "RateLimitExceeded"}:
        return True
    if getattr(exc, "status_code", None) == 429:
        return True
    text = str(exc).lower()
    return (
        "rate_limit" in text
        or "rate limit" in text
        or "tokens per day" in text
        or "tpd" in text
        or "429" in text
    )


def _log_rate_limited(exc: BaseException) -> None:
    log_agent_event(
        f"llm: skipped — rate limited ({type(exc).__name__})"
    )
    try:
        from app.audit import log_audit_stage

        log_audit_stage(
            "llm_rate_limited",
            detail={
                "status": "skipped — rate limited",
                "error_type": type(exc).__name__,
            },
        )
    except Exception:  # noqa: BLE001 — audit must never break chat()
        pass


def _log_temperature_outcome(
    *,
    attempt: float | None,
    requested: float,
    rejected_prior: list[float | None],
) -> None:
    label = format_temperature_attempt(attempt)
    if not rejected_prior:
        log_agent_event(f"llm temperature: using {label} (requested {requested})")
        return
    prior = ", ".join(format_temperature_attempt(item) for item in rejected_prior)
    log_agent_event(
        f"llm temperature: provider rejected [{prior}]; "
        f"fell back to {label} (requested {requested})"
    )


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    temperature: float | None = None,
) -> Any:
    """Send a chat completion request using the configured provider.

    ``temperature`` defaults to 0.0 (via None → DEFAULT_TEMPERATURE) so review
    and analysis calls sample as deterministically as the provider allows.
    If the provider rejects temperature=0 (or temperature at all), retries with
    a tiny epsilon and finally omits the field rather than failing the review.
    Each successful attempt (including first-try 0.0) is logged via log_agent_event.
    """
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    config = _PROVIDERS.get(provider)
    if config is None:
        supported = ", ".join(_PROVIDERS)
        raise ValueError(
            f"Unsupported LLM_PROVIDER {provider!r}. Choose one of: {supported}."
        )

    api_key_env = config["api_key_env"]
    api_key = (os.getenv(api_key_env) or "").strip()
    if not api_key and provider == "gemini":
        api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(f"{api_key_env} is required when LLM_PROVIDER={provider}.")

    client = OpenAI(api_key=api_key, base_url=config["base_url"])
    request: dict[str, Any] = {
        "model": os.getenv("LLM_MODEL") or config["default_model"],
        "messages": messages,
    }
    if tools is not None:
        request["tools"] = tools
        request["tool_choice"] = "auto"

    resolved = resolve_chat_temperature(temperature)
    last_error: BaseException | None = None
    rejected_prior: list[float | None] = []
    for attempt in temperature_attempt_values(resolved):
        payload = dict(request)
        if attempt is not None:
            payload["temperature"] = attempt
        try:
            response = client.chat.completions.create(**payload)
        except Exception as exc:
            if _is_rate_limit(exc):
                _log_rate_limited(exc)
                raise LLMRateLimitedError("skipped — rate limited") from exc
            if isinstance(exc, BadRequestError) and _is_temperature_reject(exc):
                last_error = exc
                rejected_prior.append(attempt)
                log_agent_event(
                    "llm temperature: provider rejected "
                    f"{format_temperature_attempt(attempt)} ({exc})"
                )
                continue
            raise

        _log_temperature_outcome(
            attempt=attempt,
            requested=resolved,
            rejected_prior=rejected_prior,
        )
        _audit_prompt_io(messages, response)
        return response

    if last_error is not None:
        raise last_error
    raise RuntimeError("chat() failed without a provider response")


def _audit_prompt_io(messages: list[dict[str, Any]], response: Any) -> None:
    """Record redacted prompt/response summary when an audit job is in scope."""
    try:
        from app.audit import (
            STAGE_PROMPT_IO,
            log_audit_stage,
            summarize_messages,
            summarize_model_out,
        )
    except ImportError:  # pragma: no cover
        return

    content = None
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        content = None
    log_audit_stage(
        STAGE_PROMPT_IO,
        detail={
            "prompt": summarize_messages(messages),
            "model_out": summarize_model_out(content if isinstance(content, str) else None),
        },
    )
