"""Minimal provider-agnostic LLM client."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_PROVIDERS = {
    "groq": {
        "api_key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
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


def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Any:
    """Send a chat completion request using the configured provider."""
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    config = _PROVIDERS.get(provider)
    if config is None:
        supported = ", ".join(_PROVIDERS)
        raise ValueError(
            f"Unsupported LLM_PROVIDER {provider!r}. Choose one of: {supported}."
        )

    api_key_env = config["api_key_env"]
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"{api_key_env} is required when LLM_PROVIDER={provider}.")

    client = OpenAI(api_key=api_key, base_url=config["base_url"])
    request: dict[str, Any] = {
        "model": os.getenv("LLM_MODEL") or config["default_model"],
        "messages": messages,
    }
    if tools is not None:
        request["tools"] = tools

    return client.chat.completions.create(**request)
