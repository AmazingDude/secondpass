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
_SOFT_SMELL_TYPES = frozenset({"naming_convention", "duplicated_logic"})
_SOFT_CONFIDENCE_CAP = 79

_SOFT_DUPLICATION_DROP_MARKERS = (
    "could extract",
    "could be shared",
    "shared helper",
    "similar",
    "resemble",
)

_HARD_EVIDENCE_MARKERS = (
    "near-identical",
    "identical block",
    "copy-pasted",
    "copy pasted",
    "verbatim",
    "contradicts",
    "contradict",
    "related files use",
    "same package uses",
    "visible pattern",
    "same pattern in",
)

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
- Vague concerns ("could be cleaner", "consider refactoring") without a
  concrete instance are NOT findings. Treat those as clean (has_issues=false).
- Do not flag security issues — those are handled by the Security Worker.

NAMING (naming_convention):
- Only flag naming when it contradicts patterns visible in the target and/or
  related files. Cite the conflicting symbol(s) in evidence.
- Do NOT invent external style guides (PEP 8, "public constants shouldn't
  have a leading underscore", etc.) that are not demonstrated by this code.
- Leading underscore + SCREAMING_SNAKE for module-private constants is often
  intentional. Do NOT suggest stripping the leading _ unless related files
  show the same class of symbol without it.

DUPLICATION (duplicated_logic):
- Mild similarity / "these look alike" / "could extract a shared helper" is
  CLEAN unless there are near-identical duplicated blocks with a concrete
  cost (e.g. same bug-prone logic copy-pasted, or edits must be kept in sync).
- Shared helpers already existing for the common parts is a reason to leave
  remaining parallel structure alone — do not invent a further merge.

CONFIDENCE:
- Soft smells, judgment calls, and weak similarity MUST use confidence < 80
  (so they land in needs_review if reported at all). Prefer not reporting them.
- Reserve confidence >= 80 only for clear, evidenced issues (hard naming
  contradiction of visible patterns, near-identical harmful duplication,
  clear layering / dependency-direction breaks).

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


def adjust_architecture_confidence(
    finding_type: str,
    confidence: int,
    *,
    message: str = "",
    evidence: str = "",
    suggested_fix: str = "",
) -> int:
    """Bias soft smell types below the gate unless hard evidence markers appear.

    naming_convention / duplicated_logic need hard markers (near-identical,
    contradicts visible pattern, etc.) to keep confidence >= 80; otherwise
    soft language or a bare overconfident score is capped to 79.
    """
    confidence = max(0, min(100, confidence))
    if finding_type not in _SOFT_SMELL_TYPES:
        return confidence

    text = f"{message}\n{evidence}\n{suggested_fix}".lower()
    has_hard = any(marker in text for marker in _HARD_EVIDENCE_MARKERS)
    if has_hard:
        return confidence
    if confidence >= 80:
        return min(confidence, _SOFT_CONFIDENCE_CAP)
    return confidence


def is_soft_only_smell(
    finding_type: str,
    *,
    message: str = "",
    evidence: str = "",
    suggested_fix: str = "",
) -> bool:
    """True when a smell should be dropped as clean rather than reported."""
    if finding_type not in _SOFT_SMELL_TYPES:
        return False

    text = f"{message}\n{evidence}\n{suggested_fix}".lower()
    has_hard = any(marker in text for marker in _HARD_EVIDENCE_MARKERS)
    if has_hard:
        return False

    if finding_type == "duplicated_logic":
        return any(marker in text for marker in _SOFT_DUPLICATION_DROP_MARKERS)

    if finding_type == "naming_convention":
        invents_external = any(
            marker in text
            for marker in (
                "style guide",
                "pep 8",
                "pep8",
                "conventionally",
                "typically named",
                "usually named",
            )
        )
        strips_private_underscore = (
            "leading" in text
            and "_" in text
            and any(word in text for word in ("rename", "remove", "strip", "public"))
        )
        return invents_external or strips_private_underscore

    return False


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

    finding_type = str(issue.get("finding_type") or "").strip() or "architecture_issue"
    suggested_fix = str(issue.get("suggested_fix") or "").strip() or (
        "Address the structural issue described in the evidence."
    )
    if is_soft_only_smell(
        finding_type,
        message=message,
        evidence=evidence_text,
        suggested_fix=suggested_fix,
    ):
        return None

    location = str(issue.get("location") or target_path).strip()
    evidence = "\n".join(part for part in [location, evidence_text, message] if part)
    confidence = adjust_architecture_confidence(
        finding_type,
        _bounded_confidence(issue.get("confidence"), default=70),
        message=message,
        evidence=evidence_text,
        suggested_fix=suggested_fix,
    )
    return StructuredFinding(
        finding_type=finding_type,
        evidence=evidence,
        confidence=confidence,
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
