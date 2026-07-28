"""ArchitectureWorker — naming, layering, dependency-direction, duplication review."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.context import ContextFile, gather_cross_file_context
from app.hooks import agent_scope, log_agent_event
from app.llm import chat
from app.schema import Finding as StructuredFinding
from app.workers.common import extract_json_object

_MAX_SOURCE_CHARS = 8000

_SYSTEM = """\
You are ArchitectureWorker for secondpass, a careful reviewer of code
structure and conventions — NOT security. Your ONLY job: naming conventions,
layering violations, dependency-direction problems, and duplicated logic.

You are given the target file plus a small set of related files (imports,
same-package siblings, reverse callers) for cross-file context. A layering
violation cannot be judged from one file alone — use the related files.

CRITICAL RULES:
- Only report an issue when there is a real, specific, explainable problem —
  name the file, function, or lines involved.
- Do NOT invent conventions that are not evident from the actual code shown.
- Vague concerns ("could be cleaner", "consider refactoring") without a
  concrete instance are NOT findings. Treat those as clean (has_issues=false).
- Do not flag security issues — those are handled by the Security Worker.

Respond with ONLY JSON (no markdown):
{
  "has_issues": true/false,
  "summary": "one sentence: 'No architecture issues found.' OR a brief summary",
  "issues": [
    {
      "finding_type": "layering_violation | naming_convention | dependency_direction | duplicated_logic",
      "confidence": 75,
      "location": "path:line or symbol name",
      "evidence": "specific quote or description grounding the issue",
      "message": "what is wrong and why",
      "suggested_fix": "concrete remediation"
    }
  ]
}
If has_issues is false, issues MUST be an empty list.
"""


def _read_truncated(path: str, *, max_chars: int = _MAX_SOURCE_CHARS) -> str:
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    if len(source) > max_chars:
        return source[:max_chars] + "\n... [truncated]"
    return source


def _bounded_confidence(value: Any, *, default: int) -> int:
    try:
        confidence = int(value)
    except (TypeError, ValueError):
        confidence = default
    return max(0, min(100, confidence))


def _format_context_block(
    target_path: str,
    target_source: str,
    context_files: list[ContextFile],
) -> str:
    blocks = [f"Target file: {target_path}\n```\n{target_source}\n```"]
    for item in context_files:
        blocks.append(
            f"Related file ({item.relation}): {item.path}\n```\n{item.content}\n```"
        )
    return "\n\n".join(blocks)


def _issue_to_finding(
    target_path: str,
    issue: dict[str, Any],
) -> StructuredFinding | None:
    """Map one raw architecture issue to a schema-valid Finding, or None if ungrounded."""
    message = str(issue.get("message") or "").strip()
    evidence_text = str(issue.get("evidence") or "").strip()
    if not message and not evidence_text:
        return None

    location = str(issue.get("location") or target_path).strip()
    evidence = "\n".join(part for part in [location, evidence_text, message] if part)
    finding_type = str(issue.get("finding_type") or "").strip() or "architecture_issue"
    suggested_fix = str(issue.get("suggested_fix") or "").strip() or (
        "Address the structural issue described in the evidence."
    )
    return StructuredFinding(
        finding_type=finding_type,
        evidence=evidence,
        confidence=_bounded_confidence(issue.get("confidence"), default=70),
        suggested_fix=suggested_fix,
        detection_method="llm_reasoning",
    )


def run_architecture_worker(
    target_path: str,
    *,
    project_root: str | None = None,
    max_context_files: int = 6,
) -> dict[str, Any]:
    """Cross-file architecture/conventions review for one target file.

    Returns:
      {
        "has_issues": bool,
        "summary": str,
        "structured_findings": list[Finding],
        "context_files": [{"path": ..., "relation": ...}, ...],
        "failures": int,
      }
    """
    target_source = _read_truncated(target_path)
    if not target_source.strip():
        return {
            "has_issues": False,
            "summary": "No source content to review.",
            "structured_findings": [],
            "context_files": [],
            "failures": 0,
        }

    context_files = gather_cross_file_context(
        target_path,
        project_root=project_root,
        max_files=max_context_files,
    )
    context_summary = [
        {"path": item.path, "relation": item.relation} for item in context_files
    ]
    failures = 0

    with agent_scope("architecture_worker"):
        log_agent_event(
            f"architecture_worker reviewing {target_path} "
            f"with {len(context_files)} related file(s)"
        )
        try:
            response = chat(
                [
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": _format_context_block(
                            target_path, target_source, context_files
                        ),
                    },
                ],
                tools=None,
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            log_agent_event(f"architecture_worker failed ({exc}); treating as clean")
            return {
                "has_issues": False,
                "summary": (
                    "Architecture review could not complete; no concrete issue confirmed."
                ),
                "structured_findings": [],
                "context_files": context_summary,
                "failures": failures,
            }

        content = response.choices[0].message.content or ""
        parsed = extract_json_object(content) or {}
        has_issues = bool(parsed.get("has_issues"))
        summary = str(parsed.get("summary") or "").strip()
        raw_issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else []

        structured_findings: list[StructuredFinding] = []
        if has_issues:
            for issue in raw_issues:
                if not isinstance(issue, dict):
                    continue
                finding = _issue_to_finding(target_path, issue)
                if finding is not None:
                    structured_findings.append(finding)

        # Guard: model claimed issues but gave nothing specific → clean.
        if has_issues and not structured_findings:
            has_issues = False
            summary = summary or "No architecture issues found."
            log_agent_event(
                "architecture_worker claimed issues but produced none specific; "
                "treating as clean"
            )

        if not has_issues:
            summary = summary or "No architecture issues found."
            log_agent_event(f"architecture_worker: clean — {summary}")
        else:
            log_agent_event(
                f"architecture_worker: {len(structured_findings)} issue(s) — {summary}"
            )

        return {
            "has_issues": has_issues,
            "summary": summary,
            "structured_findings": structured_findings,
            "context_files": context_summary,
            "failures": failures,
        }
