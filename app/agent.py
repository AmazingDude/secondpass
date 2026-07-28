"""Core security-review entry points (scan + multi-agent supervisor)."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.confidence_gate import GateResult, apply_confidence_gate
from app.gitdiff import ChangedFile, finding_in_changed_lines
from app.hooks import agent_scope, log_agent_event
from app.llm import chat
from app.memory import seed_memory
from app.scanner import Finding as ScannerFinding
from app.scanner import ScanError, run_static_scan
from app.schema import Finding as StructuredFinding
from app.schema import ReviewResult
from app.supervisor import supervise_finding
from app.workers.common import extract_json_object

StageCallback = Callable[[str], None]

MAX_TOOL_ITERATIONS = 6
_MAX_LOGIC_SOURCE_CHARS = 12000
_STATIC_RULE_CONFIDENCE = 90

_LOGIC_ASSESS_SYSTEM = """\
You are a careful security logic reviewer for secondpass. Semgrep reported no
(or unavailable) static findings for this source. Your job is a second look for
real logic / authorization bugs that scanners miss.

CRITICAL RULES:
- If the code genuinely has no security concerns, say so plainly.
- Do NOT invent a finding just to have something to report.
- Only report an issue when there is a real, specific, explainable concern —
  name the function or lines and what is wrong.
- Vague or generic concerns (e.g. "might need RBAC", "consider access control",
  "add more validation") without a concrete bug in THIS code are NOT findings.
  Treat those as clean (has_issues=false).
- Do not invent secrets, endpoints, or auth flows that are not in the source.

Respond with ONLY JSON (no markdown):
{
  "has_issues": true/false,
  "summary": "one sentence: 'No security issues found.' OR a brief real-issue summary",
  "issues": [
    {
      "line": 1,
      "severity": "WARNING",
      "finding_type": "missing_ownership_check",
      "confidence": 85,
      "message": "specific description of the concrete bug",
      "snippet": "short relevant code excerpt",
      "suggested_fix": "specific remediation"
    }
  ]
}
If has_issues is false, issues MUST be an empty list.
"""


def _logic_finding_from_issue(
    path: str,
    issue: dict[str, Any],
    *,
    fallback_snippet: str,
) -> ScannerFinding:
    """Convert one assessed logic issue into a Finding for the supervisor."""
    snippet = str(issue.get("snippet") or "").strip() or fallback_snippet
    if len(snippet) > 2000:
        snippet = snippet[:2000] + "\n..."
    line = issue.get("line")
    try:
        line_no = max(1, int(line))
    except (TypeError, ValueError):
        line_no = 1
    severity = str(issue.get("severity") or "WARNING").upper()
    if severity not in {"INFO", "WARNING", "ERROR"}:
        severity = "WARNING"
    message = str(issue.get("message") or "").strip() or "Logic / authorization concern"
    return {
        "rule_id": "secondpass.logic-review",
        "severity": severity,
        "path": path,
        "line": line_no,
        "message": message,
        "snippet": snippet,
    }


def _bounded_confidence(value: Any, *, default: int) -> int:
    try:
        confidence = int(value)
    except (TypeError, ValueError):
        confidence = default
    return max(0, min(100, confidence))


def _finding_evidence(finding: ScannerFinding) -> str:
    location = f"{finding.get('path', '')}:{finding.get('line', 0)}"
    message = str(finding.get("message") or "").strip()
    snippet = str(finding.get("snippet") or "").strip()
    parts = [location]
    if message:
        parts.append(message)
    if snippet:
        parts.append(snippet)
    return "\n".join(parts)


def map_semgrep_finding(finding: ScannerFinding) -> StructuredFinding:
    """Map a scanner-internal Semgrep finding to the shared Phase 3 schema."""
    rule_id = str(finding.get("rule_id") or "").strip()
    return StructuredFinding(
        finding_type=rule_id or "static_rule_match",
        evidence=_finding_evidence(finding),
        confidence=_STATIC_RULE_CONFIDENCE,
        suggested_fix="Review and remediate the matched static-analysis rule.",
        detection_method="static_rule",
    )


def map_logic_issue(
    path: str,
    issue: dict[str, Any],
    *,
    fallback_snippet: str,
) -> StructuredFinding:
    """Map one logic-review issue to the shared Phase 3 schema."""
    scanner_finding = _logic_finding_from_issue(
        path,
        issue,
        fallback_snippet=fallback_snippet,
    )
    finding_type = str(issue.get("finding_type") or "").strip()
    suggested_fix = str(issue.get("suggested_fix") or "").strip()
    return StructuredFinding(
        finding_type=finding_type or "logic_security_issue",
        evidence=_finding_evidence(scanner_finding),
        confidence=_bounded_confidence(issue.get("confidence"), default=70),
        suggested_fix=(
            suggested_fix
            or "Add the missing security control described by the logic finding."
        ),
        detection_method="llm_reasoning",
    )


def _source_is_reviewable(path: str) -> bool:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return False
    return bool(text)


def _read_source_for_logic(path: str) -> str:
    source = Path(path).read_text(encoding="utf-8")
    if len(source) > _MAX_LOGIC_SOURCE_CHARS:
        return source[:_MAX_LOGIC_SOURCE_CHARS] + "\n... [truncated]"
    return source


def assess_logic_review(
    path: str,
    *,
    scan_note: str | None = None,
) -> dict[str, Any]:
    """LLM gate: real logic issues, or an honest clean result.

    Returns:
      {
        "has_issues": bool,
        "summary": str,
        "findings": list[Finding],  # empty when clean
        "failures": int,
      }
    """
    source = _read_source_for_logic(path)
    note = scan_note or "Semgrep reported no issues for this path."
    failures = 0

    with agent_scope("supervisor"):
        log_agent_event("supervisor running honest logic-review assessment")
        try:
            response = chat(
                [
                    {"role": "system", "content": _LOGIC_ASSESS_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"{note}\n\n"
                            f"Path: {path}\n\n"
                            "Source:\n"
                            f"```\n{source}\n```\n\n"
                            "Decide whether there is a real, specific security "
                            "concern. If not, set has_issues=false."
                        ),
                    },
                ],
                tools=None,
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            log_agent_event(f"logic-review assessment failed ({exc}); treating as clean")
            return {
                "has_issues": False,
                "summary": (
                    "Logic review could not complete; no concrete issue confirmed."
                ),
                "findings": [],
                "structured_findings": [],
                "failures": failures,
            }

        content = response.choices[0].message.content or ""
        parsed = extract_json_object(content) or {}
        has_issues = bool(parsed.get("has_issues"))
        summary = str(parsed.get("summary") or "").strip()
        raw_issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else []

        findings: list[ScannerFinding] = []
        structured_findings: list[StructuredFinding] = []
        if has_issues:
            for issue in raw_issues:
                if not isinstance(issue, dict):
                    continue
                message = str(issue.get("message") or "").strip()
                if not message:
                    continue
                fallback_snippet = source.strip()[:2000]
                findings.append(
                    _logic_finding_from_issue(
                        path, issue, fallback_snippet=fallback_snippet
                    )
                )
                structured_findings.append(
                    map_logic_issue(
                        path, issue, fallback_snippet=fallback_snippet
                    )
                )

        # Guard: model claimed issues but gave nothing specific → clean.
        if has_issues and not findings:
            has_issues = False
            summary = summary or "No security issues found."
            log_agent_event(
                "logic-review claimed issues but produced none specific; treating as clean"
            )

        if not has_issues:
            summary = summary or "No security issues found."
            log_agent_event(f"logic-review: clean — {summary}")
            return {
                "has_issues": False,
                "summary": summary,
                "findings": [],
                "structured_findings": [],
                "failures": failures,
            }

        log_agent_event(f"logic-review: {len(findings)} concrete issue(s) — {summary}")
        return {
            "has_issues": True,
            "summary": summary or f"{len(findings)} logic issue(s) identified",
            "findings": findings,
            "structured_findings": structured_findings,
            "failures": failures,
        }


def _review_finding(
    finding: ScannerFinding,
    max_iterations: int = MAX_TOOL_ITERATIONS,
) -> dict[str, Any]:
    """Hand one finding to the supervisor → workers pipeline."""
    return supervise_finding(dict(finding), max_iterations=max_iterations)


def build_security_review_output(
    target: str,
    reviewed: list[dict[str, Any]],
    structured_findings: list[StructuredFinding],
    *,
    timestamp: datetime | None = None,
) -> tuple[ReviewResult, GateResult, list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach structured findings and gate the worker-enriched review items."""
    if len(reviewed) != len(structured_findings):
        raise ValueError("Reviewed and structured finding counts must match.")

    enriched: list[dict[str, Any]] = []
    finalized_findings: list[StructuredFinding] = []
    for item, structured in zip(reviewed, structured_findings):
        supervisor_fix = str(item.get("suggested_fix") or "").strip()
        finalized = (
            structured.model_copy(update={"suggested_fix": supervisor_fix})
            if supervisor_fix
            else structured
        )
        finalized_findings.append(finalized)
        enriched_item = dict(item)
        enriched_item["structured_finding"] = finalized.model_dump(mode="json")
        enriched.append(enriched_item)

    review_result = ReviewResult(
        findings=finalized_findings,
        file_path=target,
        timestamp=timestamp or datetime.now(timezone.utc),
        worker_name="security",
    )
    gate_result = apply_confidence_gate(review_result)
    accepted: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    for item, finding in zip(enriched, finalized_findings):
        bucket = (
            accepted
            if finding.confidence >= gate_result.threshold
            else needs_review
        )
        bucket.append(item)

    return review_result, gate_result, accepted, needs_review


def _structured_report_fields(
    review_result: ReviewResult,
    gate_result: GateResult,
    accepted: list[dict[str, Any]],
    needs_review: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "review_result": review_result.model_dump(mode="json"),
        "gate_result": gate_result.model_dump(mode="json"),
        "gate_threshold": gate_result.threshold,
        "accepted_count": len(accepted),
        "needs_review_count": len(needs_review),
        "accepted": accepted,
        "needs_review": needs_review,
        "findings": accepted,
        "all_findings": [*accepted, *needs_review],
    }


def _empty_report(
    target: str,
    *,
    scan_error: str | None,
    scan_empty: bool,
    used_logic_fallback: bool,
    message: str,
    tool_call_failures: int = 0,
) -> dict[str, Any]:
    review_result, gate_result, accepted, needs_review = build_security_review_output(
        target,
        [],
        [],
    )
    return {
        "path": target,
        "provider": os.getenv("LLM_PROVIDER", "groq"),
        "model": os.getenv("LLM_MODEL") or None,
        "finding_count": 0,
        "static_scan_empty": scan_empty and scan_error is None,
        "static_scan_error": scan_error,
        "used_logic_fallback": used_logic_fallback,
        "no_issues": True,
        "message": message,
        "tool_call_failures": tool_call_failures,
        **_structured_report_fields(
            review_result,
            gate_result,
            accepted,
            needs_review,
        ),
    }


def _emit_stage(on_stage: StageCallback | None, stage: str) -> None:
    if on_stage is not None:
        on_stage(stage)


def review_code(
    path: str,
    max_iterations: int = MAX_TOOL_ITERATIONS,
    *,
    on_stage: StageCallback | None = None,
) -> dict[str, Any]:
    """Run scan + multi-agent review over a path; return a structured report."""
    target = str(Path(path).resolve())
    seed_memory()

    scan_error: str | None = None
    findings: list[ScannerFinding] = []
    _emit_stage(on_stage, "scanning")
    with agent_scope("supervisor"):
        log_agent_event(f"supervisor starting review of {target}")
        try:
            findings = run_static_scan([target])
        except ScanError as exc:
            scan_error = str(exc)
            findings = []
            log_agent_event(f"supervisor scan error: {scan_error}")

    scan_empty = not findings
    structured_findings = [map_semgrep_finding(finding) for finding in findings]
    used_logic_fallback = False
    logic_failures = 0
    logic_summary: str | None = None

    if scan_empty and _source_is_reviewable(target):
        used_logic_fallback = True
        _emit_stage(on_stage, "logic_review")
        scan_note = (
            f"Static scan unavailable ({scan_error}). Review the source carefully."
            if scan_error
            else "Semgrep reported no issues for this path."
        )
        assessment = assess_logic_review(target, scan_note=scan_note)
        logic_failures = int(assessment.get("failures") or 0)
        logic_summary = str(assessment.get("summary") or "")
        if not assessment.get("has_issues"):
            _emit_stage(on_stage, "building_report")
            return _empty_report(
                target,
                scan_error=scan_error,
                scan_empty=scan_empty,
                used_logic_fallback=True,
                message=logic_summary or "No security issues found.",
                tool_call_failures=logic_failures,
            )
        findings = list(assessment.get("findings") or [])
        structured_findings = list(assessment.get("structured_findings") or [])

    if findings:
        _emit_stage(on_stage, "workers")
    reviewed = [
        _review_finding(finding, max_iterations=max_iterations) for finding in findings
    ]

    _emit_stage(on_stage, "building_report")
    review_result, gate_result, accepted, needs_review = build_security_review_output(
        target,
        reviewed,
        structured_findings,
    )
    return {
        "path": target,
        "provider": os.getenv("LLM_PROVIDER", "groq"),
        "model": os.getenv("LLM_MODEL") or None,
        "finding_count": len(reviewed),
        "static_scan_empty": scan_empty and scan_error is None,
        "static_scan_error": scan_error,
        "used_logic_fallback": used_logic_fallback,
        "no_issues": len(reviewed) == 0,
        "message": (
            logic_summary
            if used_logic_fallback and not reviewed
            else (None if reviewed else "No security issues found.")
        ),
        "tool_call_failures": logic_failures
        + sum(int(item.get("tool_call_failures") or 0) for item in reviewed),
        **_structured_report_fields(
            review_result,
            gate_result,
            accepted,
            needs_review,
        ),
    }


def review_changed_files(
    changed_files: list[ChangedFile],
    *,
    max_iterations: int = MAX_TOOL_ITERATIONS,
    mode: str = "staged",
    on_stage: StageCallback | None = None,
) -> dict[str, Any]:
    """Review whole changed files, then keep findings that fall in diff hunks."""
    files = list(changed_files)
    seed_memory()

    combined: list[dict[str, Any]] = []
    scan_errors: list[str] = []
    used_logic_fallback = False
    static_scan_empty = True
    filtered_out = 0
    clean_messages: list[str] = []
    failures = 0

    for changed in files:
        report = review_code(
            str(changed.path),
            max_iterations=max_iterations,
            on_stage=on_stage,
        )
        failures += int(report.get("tool_call_failures") or 0)
        if report.get("static_scan_error"):
            scan_errors.append(f"{changed.path}: {report['static_scan_error']}")
        if not report.get("static_scan_empty"):
            static_scan_empty = False
        if report.get("used_logic_fallback"):
            used_logic_fallback = True
        if report.get("no_issues") and report.get("message"):
            clean_messages.append(f"{changed.path}: {report['message']}")

        for item in report.get("all_findings") or []:
            finding = item.get("finding") or {}
            line = int(finding.get("line") or 0)
            rule_id = str(finding.get("rule_id") or "")
            if finding_in_changed_lines(line, changed, rule_id=rule_id):
                enriched = dict(item)
                enriched["diff_ranges"] = list(changed.ranges)
                combined.append(enriched)
            else:
                filtered_out += 1

    combined_structured = [
        StructuredFinding.model_validate(item["structured_finding"])
        for item in combined
    ]
    review_result, gate_result, accepted, needs_review = build_security_review_output(
        f"git diff ({mode})",
        combined,
        combined_structured,
    )

    _emit_stage(on_stage, "building_report")
    no_issues = len(combined) == 0
    message = None
    if no_issues:
        if filtered_out:
            message = (
                "No findings on the changed lines "
                f"({filtered_out} finding(s) outside changed ranges)."
            )
        elif clean_messages:
            message = "No security issues found."
        else:
            message = "No security issues found."

    return {
        "path": f"git diff ({mode})",
        "provider": os.getenv("LLM_PROVIDER", "groq"),
        "model": os.getenv("LLM_MODEL") or None,
        "finding_count": len(combined),
        "static_scan_empty": static_scan_empty and not scan_errors,
        "static_scan_error": "; ".join(scan_errors) if scan_errors else None,
        "used_logic_fallback": used_logic_fallback,
        "no_issues": no_issues,
        "message": message,
        "tool_call_failures": failures,
        "diff_mode": mode,
        "changed_files": [str(item.path) for item in files],
        "filtered_out_findings": filtered_out,
        **_structured_report_fields(
            review_result,
            gate_result,
            accepted,
            needs_review,
        ),
    }
