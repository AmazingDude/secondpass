"""Supervisor — routes findings to workers and synthesizes the final report."""

from __future__ import annotations

import json
from typing import Any

from app.hooks import agent_scope, log_agent_event
from app.llm import chat
from app.memory import save_finding
from app.workers.common import extract_json_object, run_tool_loop
from app.workers.memory_worker import run_memory_worker
from app.workers.web_worker import run_web_worker

SAVE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "save_finding",
            "description": (
                "Persist a NEW confirmed lesson into long-term memory. "
                "ONLY call when the issue is meaningfully new or a distinct "
                "variant. Do NOT save if memory_worker already reported a close "
                "match for the same pattern."
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

_ROUTE_SYSTEM = """\
You are the Supervisor for secondpass. You receive one static-analysis finding.
Decide which specialist workers to invoke (one, both, or neither):

- memory_worker: personal past lessons (IDOR, ownership, session bugs, etc.)
- web_worker: public OWASP/CWE/remediation guidance

Respond with ONLY JSON (no markdown):
{
  "use_memory": true/false,
  "use_web": true/false,
  "memory_query_hint": "optional short query or empty string",
  "web_query_hint": "optional short query or empty string",
  "routing_rationale": "one sentence"
}
"""

_SYNTH_SYSTEM = """\
You are the Supervisor for secondpass. Synthesize a final security review from:
- the original finding
- MemoryWorker output (may be empty if not routed)
- WebResearchWorker output (may be empty if not routed)

Write a clear explanation and concrete fix. Prefer the memory lesson when it
clearly matches; use web context to strengthen advice when relevant.

Do NOT invent risks that are not supported by the finding and worker outputs.
Stay specific to the code under review.

When finished, respond with ONLY JSON (no markdown):
{
  "explanation": "clear explanation of the risk",
  "suggested_fix": "concrete remediation advice",
  "should_save_lesson": true/false,
  "lesson_to_save": null or {
    "type": "...", "pattern": "...", "bad_example": "...",
    "fix": "...", "source": "secondpass supervisor"
  }
}

Set should_save_lesson true ONLY if this is meaningfully new / distinct from
any close memory match. If memory_worker already found a strong match, set
should_save_lesson false and lesson_to_save null.
"""


def _route_workers(finding: dict[str, Any]) -> tuple[dict[str, Any], int]:
    failures = 0
    with agent_scope("supervisor"):
        try:
            response = chat(
                [
                    {"role": "system", "content": _ROUTE_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            "Route this finding to the right worker(s):\n\n"
                            f"{json.dumps(finding, indent=2)}"
                        ),
                    },
                ],
                tools=None,
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            log_agent_event(f"supervisor routing failed ({exc}); defaulting to both workers")
            return (
                {
                    "use_memory": True,
                    "use_web": True,
                    "memory_query_hint": "",
                    "web_query_hint": "",
                    "routing_rationale": "fallback: route to both workers",
                },
                failures,
            )

        content = response.choices[0].message.content or ""
        parsed = extract_json_object(content) or {}
        route = {
            "use_memory": bool(parsed.get("use_memory", True)),
            "use_web": bool(parsed.get("use_web", True)),
            "memory_query_hint": str(parsed.get("memory_query_hint") or ""),
            "web_query_hint": str(parsed.get("web_query_hint") or ""),
            "routing_rationale": str(parsed.get("routing_rationale") or ""),
        }
        return route, failures


def _synthesize(
    finding: dict[str, Any],
    memory_result: dict[str, Any] | None,
    web_result: dict[str, Any] | None,
) -> tuple[dict[str, Any], int]:
    failures = 0
    with agent_scope("supervisor"):
        try:
            response = chat(
                [
                    {"role": "system", "content": _SYNTH_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "finding": finding,
                                "memory_worker": memory_result,
                                "web_worker": web_result,
                            },
                            indent=2,
                            default=str,
                        ),
                    },
                ],
                tools=None,
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            return (
                {
                    "explanation": f"Supervisor synthesis failed: {exc}",
                    "suggested_fix": "",
                    "should_save_lesson": False,
                    "lesson_to_save": None,
                },
                failures,
            )

        content = response.choices[0].message.content or ""
        parsed = extract_json_object(content) or {"explanation": content, "suggested_fix": ""}
        parsed.setdefault("should_save_lesson", False)
        parsed.setdefault("lesson_to_save", None)
        return parsed, failures


def _maybe_save_lesson(
    synth: dict[str, Any],
    memory_result: dict[str, Any] | None,
) -> tuple[str | None, int]:
    """Call save_finding via supervisor tool loop when warranted."""
    if not synth.get("should_save_lesson"):
        return None, 0
    if memory_result and memory_result.get("worth_reporting") and memory_result.get("best_match"):
        confidence = (memory_result.get("best_match") or {}).get("confidence")
        if isinstance(confidence, (int, float)) and confidence >= 0.45:
            log_agent_event(
                "supervisor skip save_finding — memory_worker already has a close match"
            )
            return None, 0

    lesson = synth.get("lesson_to_save")
    if not isinstance(lesson, dict):
        return None, 0

    saved_id: str | None = None

    def _on_tool(name: str, result: Any) -> None:
        nonlocal saved_id
        if name != "save_finding":
            return
        if isinstance(result, dict) and result.get("status") == "saved":
            saved_id = str(result.get("id"))
        elif isinstance(result, dict) and result.get("status") == "skipped":
            saved_id = None
        else:
            saved_id = str(result)

    log_agent_event("supervisor -> save_finding (evaluating new lesson)")
    _, failures = run_tool_loop(
        agent_name="supervisor",
        system_prompt=(
            "You are the Supervisor. Call save_finding exactly once with the "
            "provided lesson object, then reply with JSON "
            '{"status": "done"}.'
        ),
        user_content=json.dumps({"lesson": lesson}, indent=2),
        tools=SAVE_TOOLS,
        handlers={"save_finding": save_finding},
        max_iterations=3,
        on_tool_result=_on_tool,
    )
    return saved_id, failures


def supervise_finding(
    finding: dict[str, Any],
    *,
    max_iterations: int = 4,
) -> dict[str, Any]:
    """Run supervisor → workers → synthesis for one finding."""
    failures = 0

    log_agent_event("supervisor received finding; deciding worker routing")
    route, route_failures = _route_workers(finding)
    failures += route_failures
    log_agent_event(
        "supervisor routing: "
        f"memory={route['use_memory']} web={route['use_web']} "
        f"({route.get('routing_rationale') or 'no rationale'})"
    )

    memory_result: dict[str, Any] | None = None
    web_result: dict[str, Any] | None = None

    if route["use_memory"]:
        log_agent_event("supervisor -> memory_worker")
        memory_result, mem_failures = run_memory_worker(
            finding,
            query_hint=route.get("memory_query_hint") or None,
            max_iterations=max_iterations,
        )
        failures += mem_failures
        log_agent_event(
            "memory_worker -> supervisor "
            f"(worth_reporting={memory_result.get('worth_reporting')})"
        )

    if route["use_web"]:
        log_agent_event("supervisor -> web_worker")
        web_result, web_failures = run_web_worker(
            finding,
            query_hint=route.get("web_query_hint") or None,
            max_iterations=max_iterations,
        )
        failures += web_failures
        log_agent_event(
            "web_worker -> supervisor "
            f"(relevant={web_result.get('relevant')})"
        )

    if not route["use_memory"] and not route["use_web"]:
        log_agent_event("supervisor routed to neither worker; synthesizing from finding alone")

    synth, synth_failures = _synthesize(finding, memory_result, web_result)
    failures += synth_failures

    saved_lesson_id, save_failures = _maybe_save_lesson(synth, memory_result)
    failures += save_failures

    memory_matches = list((memory_result or {}).get("matches") or [])
    best_match = (memory_result or {}).get("best_match")
    if best_match and not (memory_result or {}).get("worth_reporting"):
        best_match = None

    web_context = list((web_result or {}).get("results") or [])
    if web_result and not web_result.get("relevant"):
        web_context = []

    return {
        "finding": finding,
        "memory_match": best_match,
        "memory_matches": memory_matches,
        "web_context": web_context,
        "saved_lesson_id": saved_lesson_id,
        "explanation": str(synth.get("explanation") or "").strip(),
        "suggested_fix": str(synth.get("suggested_fix") or "").strip(),
        "tool_call_failures": failures,
        "routing": route,
        "memory_worker": memory_result,
        "web_worker": web_result,
    }
