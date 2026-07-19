"""Core security-review agent loop."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from app.llm import chat
from app.memory import save_finding, search_memory, seed_memory
from app.scanner import Finding, run_static_scan
from app.websearch import search_web

try:
    from openai import BadRequestError
except ImportError:  # pragma: no cover
    BadRequestError = Exception  # type: ignore[misc, assignment]

MAX_TOOL_ITERATIONS = 6

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_static_scan",
            "description": (
                "Run Semgrep static analysis on one or more file/directory paths "
                "and return normalized security findings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File or directory paths to scan.",
                    }
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "Search past security lessons for issues similar to the current "
                "finding. Useful for recalling personal patterns and fixes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language description of the issue.",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "How many lessons to return (default 3).",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web (via Tavily) for security guidance, OWASP notes, "
                "or remediation context related to a finding."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Web search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (default 3).",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_finding",
            "description": (
                "Persist a confirmed lesson into long-term memory so future "
                "reviews can recall it. Pass type, pattern, bad_example, fix, "
                "and optional source/id fields."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finding": {
                        "type": "object",
                        "description": "Lesson object to store.",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {"type": "string"},
                            "pattern": {"type": "string"},
                            "bad_example": {"type": "string"},
                            "fix": {"type": "string"},
                            "source": {"type": "string"},
                        },
                        "required": ["type", "pattern", "fix"],
                    }
                },
                "required": ["finding"],
            },
        },
    },
]

_TOOL_HANDLERS: dict[str, Callable[..., Any]] = {
    "run_static_scan": run_static_scan,
    "search_memory": search_memory,
    "search_web": search_web,
    "save_finding": save_finding,
}

_SYSTEM_PROMPT = """\
You are secondpass, a careful personal security review agent.

You are given one static-analysis finding. Gather useful context before concluding:
- Prefer calling search_memory at least once for similar past lessons.
- Call search_web when public guidance (for example OWASP) would strengthen the advice.
- Call save_finding only if this is a confirmed reusable lesson worth remembering.
- You choose which tools to call and in what order; do not invent tool results.

When finished, respond with ONLY a JSON object (no markdown fences):
{
  "explanation": "clear explanation of the risk",
  "suggested_fix": "concrete remediation advice"
}
"""


def _assistant_message_dict(message: Any) -> dict[str, Any]:
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


def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")
    return handler(**arguments)


def _serialize_tool_result(result: Any) -> str:
    try:
        return json.dumps(result, default=str, ensure_ascii=False)
    except TypeError:
        return json.dumps({"result": str(result)}, ensure_ascii=False)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
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


def _best_memory_match(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not matches:
        return None
    ranked = sorted(
        matches,
        key=lambda item: (
            item.get("distance")
            if isinstance(item.get("distance"), (int, float))
            else float("inf")
        ),
    )
    best = ranked[0]
    return {
        "id": best.get("id"),
        "type": best.get("type"),
        "pattern": best.get("pattern"),
        "fix": best.get("fix"),
        "source": best.get("source"),
        "distance": best.get("distance"),
        "confidence": best.get("confidence"),
    }


def _review_finding(finding: Finding, max_iterations: int = MAX_TOOL_ITERATIONS) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Review this static-analysis finding. Use tools only if helpful, "
                "then return the final JSON report.\n\n"
                f"{json.dumps(finding, indent=2)}"
            ),
        },
    ]

    memory_matches: list[dict[str, Any]] = []
    web_context: list[dict[str, Any]] = []
    saved_lesson_id: str | None = None
    explanation = ""
    suggested_fix = ""

    for iteration in range(max_iterations):
        try:
            response = chat(messages, tools=TOOL_DEFINITIONS)
        except BadRequestError as exc:
            # Some providers (notably Groq) occasionally emit malformed tool calls.
            # Nudge the model to either retry properly or finish with JSON.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous tool call was invalid for the API. "
                        "Either call a tool using the provided function-calling "
                        "interface, or finish now with ONLY the final JSON object "
                        '{"explanation": "...", "suggested_fix": "..."}.'
                        f"\nProvider detail: {exc}"
                    ),
                }
            )
            if iteration == max_iterations - 1:
                explanation = (
                    "Provider rejected tool calls before a final answer was produced."
                )
            continue

        message = response.choices[0].message
        messages.append(_assistant_message_dict(message))

        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            content = message.content or ""
            parsed = _extract_json_object(content) or {}
            explanation = str(parsed.get("explanation") or content).strip()
            suggested_fix = str(parsed.get("suggested_fix") or "").strip()
            break

        for tool_call in tool_calls:
            name = tool_call.function.name
            arguments = _parse_tool_arguments(tool_call.function.arguments)
            try:
                result = _execute_tool(name, arguments)
                if name == "search_memory" and isinstance(result, list):
                    memory_matches.extend(result)
                elif name == "search_web" and isinstance(result, list):
                    web_context.extend(result)
                elif name == "save_finding":
                    saved_lesson_id = str(result)
                payload = _serialize_tool_result(result)
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
        explanation = explanation or (
            "Reached max tool iterations without a final model answer."
        )

    return {
        "finding": finding,
        "memory_match": _best_memory_match(memory_matches),
        "memory_matches": memory_matches,
        "web_context": web_context,
        "saved_lesson_id": saved_lesson_id,
        "explanation": explanation,
        "suggested_fix": suggested_fix,
    }


def review_code(path: str, max_iterations: int = MAX_TOOL_ITERATIONS) -> dict[str, Any]:
    """Run the planner loop over a path and return a structured review report."""
    target = str(Path(path).resolve())
    seed_memory()

    findings = run_static_scan([target])
    reviewed = [_review_finding(finding, max_iterations=max_iterations) for finding in findings]

    return {
        "path": target,
        "finding_count": len(findings),
        "findings": reviewed,
    }
