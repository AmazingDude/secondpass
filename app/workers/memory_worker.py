"""MemoryWorker — search personal lessons and interpret matches."""

from __future__ import annotations

import json
from typing import Any

from app.memory import search_memory
from app.workers.common import run_tool_loop

MEMORY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "Search past security lessons for issues similar to the current finding."
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
]

_SYSTEM = """\
You are MemoryWorker for secondpass. Your ONLY job is personal lesson memory.

- Decide whether search_memory is useful for this finding.
- If yes, call search_memory with a focused query.
- Interpret the results: is there a real match, how confident, worth reporting?
- Do NOT invent lessons. If nothing relevant, say so.

When finished, respond with ONLY JSON (no markdown):
{
  "searched": true/false,
  "worth_reporting": true/false,
  "best_match": null or {
    "id": "...", "type": "...", "pattern": "...", "fix": "...",
    "source": "...", "distance": 0.0, "confidence": 0.0
  },
  "matches": [],
  "rationale": "short reason"
}
"""


def run_memory_worker(
    finding: dict[str, Any],
    *,
    query_hint: str | None = None,
    max_iterations: int = 4,
) -> tuple[dict[str, Any], int]:
    """Return (interpretation dict, tool_call_failures)."""
    matches_collected: list[dict[str, Any]] = []

    def _on_tool(name: str, result: Any) -> None:
        if name == "search_memory" and isinstance(result, list):
            matches_collected.extend(result)

    hint = f"\nSuggested query hint from supervisor: {query_hint}" if query_hint else ""
    user = (
        "Evaluate this finding against personal security lesson memory.\n\n"
        f"{json.dumps(finding, indent=2)}{hint}"
    )

    final, failures = run_tool_loop(
        agent_name="memory_worker",
        system_prompt=_SYSTEM,
        user_content=user,
        tools=MEMORY_TOOLS,
        handlers={"search_memory": search_memory},
        max_iterations=max_iterations,
        on_tool_result=_on_tool,
        temperature=0,
    )

    # Prefer structured worker output; fall back to raw matches if JSON incomplete.
    if matches_collected and not final.get("matches"):
        final["matches"] = matches_collected
    if matches_collected and final.get("best_match") is None:
        ranked = sorted(
            matches_collected,
            key=lambda item: (
                item.get("distance")
                if isinstance(item.get("distance"), (int, float))
                else float("inf")
            ),
        )
        best = ranked[0]
        final.setdefault("best_match", {
            "id": best.get("id"),
            "type": best.get("type"),
            "pattern": best.get("pattern"),
            "fix": best.get("fix"),
            "source": best.get("source"),
            "distance": best.get("distance"),
            "confidence": best.get("confidence"),
        })
        final.setdefault("searched", True)
        confidence = best.get("confidence")
        if final.get("worth_reporting") is None and isinstance(confidence, (int, float)):
            final["worth_reporting"] = confidence >= 0.4

    final.setdefault("searched", bool(matches_collected))
    final.setdefault("worth_reporting", bool(final.get("best_match")))
    final.setdefault("matches", matches_collected)
    final.setdefault("rationale", "")
    return final, failures
