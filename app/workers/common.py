"""Shared helpers for multi-agent tool-calling loops."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from app.hooks import agent_scope
from app.llm import LLMRateLimitedError, chat

try:
    from openai import BadRequestError
except ImportError:  # pragma: no cover
    BadRequestError = Exception  # type: ignore[misc, assignment]


def assistant_message_dict(message: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": message.content,
    }
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        payload["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
            for tool_call in tool_calls
        ]
    return payload


def parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def serialize_tool_result(result: Any) -> str:
    try:
        return json.dumps(result, default=str, ensure_ascii=False)
    except TypeError:
        return json.dumps({"result": str(result)}, ensure_ascii=False)


def extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def run_tool_loop(
    *,
    agent_name: str,
    system_prompt: str,
    user_content: str,
    tools: list[dict[str, Any]],
    handlers: dict[str, Callable[..., Any]],
    max_iterations: int = 4,
    on_tool_result: Callable[[str, Any], None] | None = None,
    temperature: float | None = None,
) -> tuple[dict[str, Any], int]:
    """Run a small agent tool loop scoped to ``agent_name``.

    Returns (final_json_dict, tool_call_failures).
    ``temperature`` defaults to 0 via chat() when None (review determinism).
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    failures = 0
    final: dict[str, Any] = {}

    with agent_scope(agent_name):
        for iteration in range(max_iterations):
            try:
                response = chat(messages, tools=tools, temperature=temperature)
            except LLMRateLimitedError:
                failures += 1
                final = {
                    "error": "skipped — rate limited",
                    "rate_limited": True,
                }
                break
            except BadRequestError as exc:
                failures += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous tool call was invalid for the API. "
                            "Either call a tool correctly, or finish with ONLY "
                            "a JSON object as instructed.\n"
                            f"Provider detail: {exc}"
                        ),
                    }
                )
                if iteration == max_iterations - 1:
                    final = {"error": "Provider rejected tool calls before finishing."}
                continue

            message = response.choices[0].message
            messages.append(assistant_message_dict(message))
            tool_calls = getattr(message, "tool_calls", None) or []

            if not tool_calls:
                content = message.content or ""
                final = extract_json_object(content) or {"raw": content}
                break

            for tool_call in tool_calls:
                name = tool_call.function.name
                arguments = parse_tool_arguments(tool_call.function.arguments)
                handler = handlers.get(name)
                try:
                    if handler is None:
                        raise ValueError(f"Unknown tool for {agent_name}: {name}")
                    result = handler(**arguments)
                    if on_tool_result is not None:
                        on_tool_result(name, result)
                    payload = serialize_tool_result(result)
                except Exception as exc:  # noqa: BLE001 — feed errors back to the model
                    payload = json.dumps({"error": str(exc)}, ensure_ascii=False)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": payload,
                    }
                )
        else:
            if not final:
                final = {"error": "Reached max tool iterations without a final answer."}

    return final, failures
