"""Supervisor — finding-level Memory/Web routing + path-level worker aggregation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.hooks import agent_scope, log_agent_event
from app.llm import chat
from app.workers.common import extract_json_object
from app.workers.memory_worker import run_memory_worker
from app.workers.web_worker import run_web_worker

StageCallback = Callable[[str], None]

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

Do NOT propose saving new lessons to memory. Verified-outcome memory is written
only after an explicit human accept/reject with a reason (CLI decide command).

When finished, respond with ONLY JSON (no markdown):
{
  "explanation": "clear explanation of the risk",
  "suggested_fix": "concrete remediation advice"
}
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
                temperature=0,
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
                temperature=0,
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            return (
                {
                    "explanation": f"Supervisor synthesis failed: {exc}",
                    "suggested_fix": "",
                },
                failures,
            )

        content = response.choices[0].message.content or ""
        parsed = extract_json_object(content) or {
            "explanation": content,
            "suggested_fix": "",
        }
        return parsed, failures


def supervise_finding(
    finding: dict[str, Any],
    *,
    max_iterations: int = 4,
) -> dict[str, Any]:
    """Run supervisor → workers → synthesis for one finding.

    Does not auto-write Chroma lessons via save_finding. Seeded lessons in
    security_lessons.json remain available for MemoryWorker retrieval; new
    durable outcomes go through human accept/reject → SQLite verified_outcomes.
    """
    from app.audit import STAGE_CHROMA_SAVE_SKIP, audit_worker_scope, log_audit_stage

    failures = 0

    with audit_worker_scope("supervisor"):
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
            log_agent_event(
                "supervisor routed to neither worker; synthesizing from finding alone"
            )

        synth, synth_failures = _synthesize(finding, memory_result, web_result)
        failures += synth_failures
        log_agent_event(
            "supervisor skip save_finding — verified outcomes require human accept/reject"
        )
        log_audit_stage(
            STAGE_CHROMA_SAVE_SKIP,
            detail={
                "reason": "verified outcomes require human accept/reject",
                "saved_lesson_id": None,
            },
        )

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
        "saved_lesson_id": None,
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
    security_inconclusive = bool(security_report.get("inconclusive"))

    summary = {
        "security_accepted": sec_accepted,
        "security_needs_review": sec_needs,
        "architecture_accepted": arch_accepted,
        "architecture_needs_review": arch_needs,
        "accepted_count": overall_accepted,
        "needs_review_count": overall_needs,
        "finding_count": overall_accepted + overall_needs,
        "architecture_skipped": arch_skipped,
        "no_issues": overall_accepted == 0
        and overall_needs == 0
        and not security_inconclusive,
        "inconclusive": security_inconclusive,
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
    job_id: str | None = None,
) -> dict[str, Any]:
    """Top-level Supervisor: run Security (+ Architecture) and aggregate.

    Finding-level Memory/Web routing stays inside the Security path via
    supervise_finding. This coordinates the two engineering workers only.
    Architecture is skipped for directories when run_architecture is True (same
    policy as review_architecture). Diff mode stays Security-only at the CLI.

    ``job_id`` keys the persistent audit trail. API jobs pass their job UUID;
    CLI/MCP callers omit it and get a synthetic id so both share one model.
    """
    import uuid

    from app.agent import review_architecture, review_code
    from app.audit import (
        STAGE_REVIEW_COMPLETE,
        STAGE_REVIEW_START,
        audit_scope,
        audit_worker_scope,
        log_audit_stage,
    )

    correlation_id = job_id or str(uuid.uuid4())
    target = str(Path(path).resolve())

    with audit_scope(correlation_id):
        log_audit_stage(
            STAGE_REVIEW_START,
            worker_name="supervisor",
            detail={"path": target, "run_architecture": run_architecture},
        )
        with agent_scope("supervisor"):
            log_agent_event(f"supervisor path review starting for {target}")
            log_agent_event("supervisor -> security worker")

        with audit_worker_scope("security"):
            security_report = review_code(
                target,
                max_iterations=max_iterations,
                on_stage=on_stage,
            )

        architecture_report: dict[str, Any] | None = None
        if run_architecture:
            with agent_scope("supervisor"):
                log_agent_event("supervisor -> architecture worker")
            with audit_worker_scope("architecture"):
                architecture_report = review_architecture(target, on_stage=on_stage)

        combined = aggregate_worker_reports(
            security_report,
            architecture_report,
            path=target,
        )

        from app.verified import persist_combined_review

        persisted = persist_combined_review(combined, job_id=correlation_id)
        combined["persisted_review_ids"] = persisted
        combined["summary"]["persisted_review_ids"] = persisted
        combined["job_id"] = correlation_id
        combined["summary"]["job_id"] = correlation_id

        log_audit_stage(
            STAGE_REVIEW_COMPLETE,
            worker_name="supervisor",
            detail={
                "accepted_count": combined["summary"]["accepted_count"],
                "needs_review_count": combined["summary"]["needs_review_count"],
                "workers_run": combined["summary"]["workers_run"],
                "persisted_review_ids": persisted,
            },
        )

        with agent_scope("supervisor"):
            summary = combined["summary"]
            log_agent_event(
                "supervisor aggregated report: "
                f"accepted={summary['accepted_count']} "
                f"needs_review={summary['needs_review_count']} "
                f"workers={summary['workers_run']} "
                f"persisted={persisted} "
                f"job_id={correlation_id}"
            )
        return combined
