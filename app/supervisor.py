"""Supervisor — finding-level Memory/Web routing + path-level worker aggregation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.hooks import agent_scope, log_agent_event
from app.llm import chat
from app.memory import save_finding
from app.workers.common import extract_json_object, run_tool_loop

StageCallback = Callable[[str], None]
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


def _count(report: dict[str, Any] | None, key: str) -> int:
    if not report:
        return 0
    if key in report and report[key] is not None:
        try:
            return int(report[key])
        except (TypeError, ValueError):
            pass
    bucket = report.get(key.replace("_count", "")) if key.endswith("_count") else None
    if isinstance(bucket, list):
        return len(bucket)
    return 0


def aggregate_worker_reports(
    security: dict[str, Any],
    architecture: dict[str, Any] | None = None,
    *,
    path: str | None = None,
) -> dict[str, Any]:
    """Merge Security + Architecture worker reports into one Supervisor result.

    Preserves each worker's schema/gate fields intact. Summary counts are
    derived from accepted / needs_review buckets (or their *_count fields).
    """
    security_report = dict(security or {})
    architecture_report = dict(architecture) if architecture is not None else None

    sec_accepted = _count(security_report, "accepted_count")
    sec_needs = _count(security_report, "needs_review_count")
    if architecture_report is None:
        arch_accepted = 0
        arch_needs = 0
        arch_skipped = True
        workers_run = ["security"]
    else:
        arch_accepted = _count(architecture_report, "accepted_count")
        arch_needs = _count(architecture_report, "needs_review_count")
        arch_skipped = bool(architecture_report.get("skipped"))
        workers_run = ["security", "architecture"]

    overall_accepted = sec_accepted + arch_accepted
    overall_needs = sec_needs + arch_needs
    tool_failures = int(security_report.get("tool_call_failures") or 0) + int(
        (architecture_report or {}).get("tool_call_failures") or 0
    )

    summary = {
        "security_accepted": sec_accepted,
        "security_needs_review": sec_needs,
        "architecture_accepted": arch_accepted,
        "architecture_needs_review": arch_needs,
        "accepted_count": overall_accepted,
        "needs_review_count": overall_needs,
        "finding_count": overall_accepted + overall_needs,
        "architecture_skipped": arch_skipped,
        "no_issues": overall_accepted == 0 and overall_needs == 0,
        "workers_run": workers_run,
        "tool_call_failures": tool_failures,
        "gate_threshold": security_report.get("gate_threshold")
        or (architecture_report or {}).get("gate_threshold"),
    }

    resolved_path = (
        path
        or security_report.get("path")
        or (architecture_report or {}).get("path")
        or ""
    )
    return {
        "path": resolved_path,
        "security": security_report,
        "architecture": architecture_report,
        "summary": summary,
    }


def supervise_review(
    path: str,
    *,
    max_iterations: int = 6,
    run_architecture: bool = True,
    on_stage: StageCallback | None = None,
) -> dict[str, Any]:
    """Top-level Supervisor: run Security (+ Architecture) and aggregate.

    Finding-level Memory/Web routing stays inside the Security path via
    supervise_finding. This coordinates the two engineering workers only.
    Architecture is skipped for directories when run_architecture is True (same
    policy as review_architecture). Diff mode stays Security-only at the CLI.
    """
    # Lazy imports avoid the agent ↔ supervisor cycle (agent uses supervise_finding).
    from app.agent import review_architecture, review_code

    target = str(Path(path).resolve())
    with agent_scope("supervisor"):
        log_agent_event(f"supervisor path review starting for {target}")
        log_agent_event("supervisor -> security worker")

    security_report = review_code(
        target,
        max_iterations=max_iterations,
        on_stage=on_stage,
    )

    architecture_report: dict[str, Any] | None = None
    if run_architecture:
        with agent_scope("supervisor"):
            log_agent_event("supervisor -> architecture worker")
        architecture_report = review_architecture(target, on_stage=on_stage)

    combined = aggregate_worker_reports(
        security_report,
        architecture_report,
        path=target,
    )
    with agent_scope("supervisor"):
        summary = combined["summary"]
        log_agent_event(
            "supervisor aggregated report: "
            f"accepted={summary['accepted_count']} "
            f"needs_review={summary['needs_review_count']} "
            f"workers={summary['workers_run']}"
        )
    return combined
