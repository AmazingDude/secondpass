"""Core security-review entry points (scan + multi-agent supervisor)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.gitdiff import ChangedFile, finding_in_changed_lines
from app.hooks import agent_scope, log_agent_event
from app.memory import seed_memory
from app.scanner import Finding, ScanError, run_static_scan
from app.supervisor import supervise_finding

MAX_TOOL_ITERATIONS = 6


def _logic_review_finding(path: str, *, reason: str | None = None) -> Finding:
    """Build a fallback finding so logic bugs Semgrep misses can still be reviewed."""
    source = Path(path).read_text(encoding="utf-8")
    snippet = source.strip()
    if len(snippet) > 2000:
        snippet = snippet[:2000] + "\n..."
    message = (
        reason
        or (
            "No Semgrep findings. Review this source for access-control and "
            "authorization logic flaws (for example missing ownership checks)."
        )
    )
    return {
        "rule_id": "secondpass.logic-review",
        "severity": "INFO",
        "path": path,
        "line": 1,
        "message": message,
        "snippet": snippet,
    }


def _source_is_reviewable(path: str) -> bool:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return False
    return bool(text)


def _review_finding(finding: Finding, max_iterations: int = MAX_TOOL_ITERATIONS) -> dict[str, Any]:
    """Hand one finding to the supervisor → workers pipeline."""
    return supervise_finding(dict(finding), max_iterations=max_iterations)


def review_code(path: str, max_iterations: int = MAX_TOOL_ITERATIONS) -> dict[str, Any]:
    """Run scan + multi-agent review over a path; return a structured report."""
    target = str(Path(path).resolve())
    seed_memory()

    scan_error: str | None = None
    findings: list[Finding] = []
    with agent_scope("supervisor"):
        log_agent_event(f"supervisor starting review of {target}")
        try:
            findings = run_static_scan([target])
        except ScanError as exc:
            scan_error = str(exc)
            findings = []
            log_agent_event(f"supervisor scan error: {scan_error}")

    scan_empty = not findings
    used_logic_fallback = False
    if scan_empty and _source_is_reviewable(target):
        used_logic_fallback = True
        reason = None
        if scan_error:
            reason = (
                f"Static scan unavailable ({scan_error}). Falling back to a "
                "logic/authorization review of the source."
            )
        findings = [_logic_review_finding(target, reason=reason)]
        log_agent_event("supervisor using logic-review fallback finding")

    reviewed = [
        _review_finding(finding, max_iterations=max_iterations) for finding in findings
    ]

    return {
        "path": target,
        "provider": os.getenv("LLM_PROVIDER", "groq"),
        "model": os.getenv("LLM_MODEL") or None,
        "finding_count": len(reviewed),
        "static_scan_empty": scan_empty and scan_error is None,
        "static_scan_error": scan_error,
        "used_logic_fallback": used_logic_fallback,
        "tool_call_failures": sum(
            int(item.get("tool_call_failures") or 0) for item in reviewed
        ),
        "findings": reviewed,
    }


def review_changed_files(
    changed_files: list[ChangedFile],
    *,
    max_iterations: int = MAX_TOOL_ITERATIONS,
    mode: str = "staged",
) -> dict[str, Any]:
    """Review whole changed files, then keep findings that fall in diff hunks."""
    files = list(changed_files)
    seed_memory()

    combined: list[dict[str, Any]] = []
    scan_errors: list[str] = []
    used_logic_fallback = False
    static_scan_empty = True
    filtered_out = 0

    for changed in files:
        report = review_code(str(changed.path), max_iterations=max_iterations)
        if report.get("static_scan_error"):
            scan_errors.append(f"{changed.path}: {report['static_scan_error']}")
        if not report.get("static_scan_empty"):
            static_scan_empty = False
        if report.get("used_logic_fallback"):
            used_logic_fallback = True

        for item in report.get("findings") or []:
            finding = item.get("finding") or {}
            line = int(finding.get("line") or 0)
            rule_id = str(finding.get("rule_id") or "")
            if finding_in_changed_lines(line, changed, rule_id=rule_id):
                enriched = dict(item)
                enriched["diff_ranges"] = list(changed.ranges)
                combined.append(enriched)
            else:
                filtered_out += 1

    return {
        "path": f"git diff ({mode})",
        "provider": os.getenv("LLM_PROVIDER", "groq"),
        "model": os.getenv("LLM_MODEL") or None,
        "finding_count": len(combined),
        "static_scan_empty": static_scan_empty and not scan_errors,
        "static_scan_error": "; ".join(scan_errors) if scan_errors else None,
        "used_logic_fallback": used_logic_fallback,
        "tool_call_failures": sum(
            int(item.get("tool_call_failures") or 0) for item in combined
        ),
        "diff_mode": mode,
        "changed_files": [str(item.path) for item in files],
        "filtered_out_findings": filtered_out,
        "findings": combined,
    }
