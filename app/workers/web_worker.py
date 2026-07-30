"""WebResearchWorker — search public guidance and interpret relevance."""

from __future__ import annotations

import json
from typing import Any

from app.websearch import search_web
from app.workers.common import run_tool_loop

WEB_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web (via Tavily) for OWASP/CWE or remediation guidance."
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
]

_SYSTEM = """\
You are WebResearchWorker for secondpass. Your ONLY job is public web guidance.

- Decide whether search_web is useful for this finding.
- If yes, call search_web with a focused OWASP/CWE/remediation query.
- Interpret results: keep only relevant guidance; drop noise.
- Do NOT invent URLs or titles.

When finished, respond with ONLY JSON (no markdown):
{
  "searched": true/false,
  "relevant": true/false,
  "results": [{"title": "...", "url": "...", "snippet": "..."}],
  "rationale": "short reason"
}
"""


def run_web_worker(
    finding: dict[str, Any],
    *,
    query_hint: str | None = None,
    max_iterations: int = 4,
) -> tuple[dict[str, Any], int]:
    """Return (interpretation dict, tool_call_failures)."""
    results_collected: list[dict[str, Any]] = []

    def _on_tool(name: str, result: Any) -> None:
        if name == "search_web" and isinstance(result, list):
            results_collected.extend(result)

    hint = f"\nSuggested query hint from supervisor: {query_hint}" if query_hint else ""
    user = (
        "Evaluate whether public web guidance would help this finding.\n\n"
        f"{json.dumps(finding, indent=2)}{hint}"
    )

    final, failures = run_tool_loop(
        agent_name="web_worker",
        system_prompt=_SYSTEM,
        user_content=user,
        tools=WEB_TOOLS,
        handlers={"search_web": search_web},
        max_iterations=max_iterations,
        on_tool_result=_on_tool,
        temperature=0,
    )

    if results_collected and not final.get("results"):
        final["results"] = results_collected
    final.setdefault("searched", bool(results_collected))
    final.setdefault("relevant", bool(final.get("results")))
    final.setdefault("results", results_collected if final.get("relevant") else final.get("results") or [])
    if final.get("relevant") is False:
        final["results"] = []
    final.setdefault("rationale", "")
    return final, failures
