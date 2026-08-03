"""ArchitectureWorker — naming, layering, dependency-direction, duplication review."""

from __future__ import annotations

import ast
import re
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

_TARGET_SYMBOL_RE = re.compile(
    r"^(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|^([A-Z_][A-Z0-9_]*)\s*=",
    re.MULTILINE,
)

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

# Ownership / authz / IDOR — Security Worker territory; drop if Architecture emits these.
_SECURITY_BLEED_MARKERS = (
    "ownership",
    "owner_id",
    "current_user",
    "idor",
    "insecure direct object",
    "authorization",
    "authentication",
    "access control",
    "broken access",
    "permission check",
    "permissions check",
    "authz",
    "unauthorized access",
    "without checking owner",
    "missing ownership",
    "ownership check",
)

# Layering / dependency claims need a real multi-module surface.
_STRUCTURE_CLAIM_TYPES = frozenset({"layering_violation", "dependency_direction"})
_STDLIB_TOP_LEVEL = frozenset(
    {
        "__future__",
        "abc",
        "argparse",
        "asyncio",
        "base64",
        "builtins",
        "collections",
        "concurrent",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "hashlib",
        "hmac",
        "http",
        "importlib",
        "inspect",
        "io",
        "itertools",
        "json",
        "logging",
        "math",
        "os",
        "pathlib",
        "pickle",
        "platform",
        "random",
        "re",
        "secrets",
        "shutil",
        "socket",
        "ssl",
        "string",
        "subprocess",
        "sys",
        "tempfile",
        "threading",
        "time",
        "traceback",
        "typing",
        "urllib",
        "uuid",
        "warnings",
    }
)

_SYSTEM = """\
You are ArchitectureWorker for secondpass, a careful reviewer of code
structure and conventions — NOT security. Your ONLY job: naming conventions,
layering violations, dependency-direction problems, and duplicated logic.

You are given the target file plus a small set of related files (imports,
same-package siblings, reverse callers) for cross-file context. A layering
violation cannot be judged from one file alone — use the related files.

TARGET vs CONTEXT (attribution — critical):
- Related/sibling files are for understanding relationships ONLY. They are
  NOT additional subjects to review or to file findings against.
- Every finding you return MUST concern the target file being reviewed —
  the problem lives in the target (e.g. the target imports the wrong layer,
  or the target depends upward). Cite the target path or a symbol defined
  in the target in location/evidence.
- Do NOT report a finding whose primary subject is a related/context file,
  even if that related file has a real architecture bug. Someone else will
  review that file as its own target. If the only issue you notice is in a
  sibling, set has_issues=false (or omit that issue).

INSUFFICIENT STRUCTURE (do not invent layers):
- A small single-purpose script that only uses the standard library often has
  no architectural layers to assess. If there is no identifiable project layer
  boundary (e.g. handler vs service vs data store, or high-level workflow vs
  low-level persistence), set has_issues=false for layering / dependency claims.
- Importing a standard library module (subprocess, pathlib, os, sys, json, …)
  is NOT, by itself, evidence of a layering_violation or dependency_direction
  problem. Stdlib is not a "lower" or "higher" application layer.
- A real layering / dependency-direction finding must name an actual
  project-defined boundary being crossed (which first-party module/layer
  should be used instead, or which upward dependency is wrong) — not "this
  file imports a lower/higher-level-sounding module."

CRITICAL RULES:
- Only report an issue when there is a real, specific, explainable problem —
  name the file, function, or lines involved.
- Vague concerns ("could be cleaner", "consider refactoring") without a
  concrete instance are NOT findings. Treat those as clean (has_issues=false).
- Do NOT flag security issues — those are handled ONLY by the Security Worker.
- FORBIDDEN (Security Worker only — never report, never reclassify under
  architecture type names like layering_violation / naming_convention /
  dependency_direction / duplicated_logic):
  authentication, authorization, ownership checks, owner_id / current_user
  comparisons, IDOR / Insecure Direct Object Reference, access control,
  Broken Access Control, or permission checks. If the only real issue is
  missing ownership or unauthorized access, set has_issues=false.
- Do NOT invent architecture findings whose evidence is really "this function
  returns data without an ownership check" — that is security, not layering.

NAMING (naming_convention):
- Only flag naming when it contradicts patterns visible in the target and/or
  related files. Cite the conflicting symbol(s) in evidence.
- Do NOT invent external style guides (PEP 8, "public constants shouldn't
  have a leading underscore", etc.) that are not demonstrated by this code.
- Leading underscore + SCREAMING_SNAKE for module-private constants is often
  intentional. Do NOT suggest stripping the leading _ unless related files
  show the same class of symbol without it.
- Do NOT suggest renaming symbols to encode security concerns (e.g.
  NOTES_WITHOUT_OWNERSHIP_CHECK) — that is Security Worker territory.

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


def is_security_category_bleed(
    *,
    message: str = "",
    evidence: str = "",
    suggested_fix: str = "",
    finding_type: str = "",
) -> bool:
    """True when Architecture is re-labeling a Security (authz/IDOR) issue."""
    text = f"{finding_type}\n{message}\n{evidence}\n{suggested_fix}".lower()
    return any(marker in text for marker in _SECURITY_BLEED_MARKERS)


def _source_has_first_party_import(source: str) -> bool:
    """True when source imports a non-stdlib / relative (first-party) module."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
            return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = (alias.name or "").split(".", 1)[0]
                if top and top not in _STDLIB_TOP_LEVEL:
                    return True
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            top = node.module.split(".", 1)[0]
            if top and top not in _STDLIB_TOP_LEVEL:
                return True
    return False


def is_insufficient_structure_claim(
    *,
    finding_type: str = "",
    target_source: str = "",
    message: str = "",
    evidence: str = "",
    suggested_fix: str = "",
) -> bool:
    """True when layering/dependency is claimed on a file with no layer surface.

    A real layering or dependency-direction issue requires a first-party module
    boundary. Stdlib-only scripts have nothing to violate — drop those claims
    regardless of invented "service layer" wording in the finding text.
    """
    _ = (message, evidence, suggested_fix)
    if finding_type not in _STRUCTURE_CLAIM_TYPES:
        return False
    if not (target_source or "").strip():
        return False
    return not _source_has_first_party_import(target_source)


def _mentions_path(text: str, path: str | Path) -> bool:
    candidate = Path(str(path).replace("\\", "/"))
    name = candidate.name.lower()
    stem = candidate.stem.lower()
    posix = candidate.as_posix().lower()
    if name and name in text:
        return True
    if posix and posix in text:
        return True
    if stem and re.search(rf"\b{re.escape(stem)}\b", text):
        return True
    return False


def _target_defined_symbols(source: str) -> set[str]:
    symbols: set[str] = set()
    for match in _TARGET_SYMBOL_RE.finditer(source or ""):
        name = match.group(1) or match.group(2)
        if name:
            symbols.add(name.lower())
    return symbols


def _mentions_symbol(text: str, symbols: set[str]) -> bool:
    for symbol in symbols:
        if len(symbol) < 2:
            continue
        if re.search(rf"\b{re.escape(symbol)}\b", text):
            return True
    return False


def is_off_target_finding(
    target_path: str,
    *,
    location: str = "",
    evidence: str = "",
    message: str = "",
    suggested_fix: str = "",
    context_files: list[ContextFile] | None = None,
    target_source: str = "",
) -> bool:
    """True when a finding is about a context sibling, not the file under review.

    Keeps cross-file context for detection, but drops findings whose evidence
    only names a related file (or never references the target at all).
    """
    text = f"{location}\n{evidence}\n{message}\n{suggested_fix}".lower()
    mentions_target_path = _mentions_path(text, target_path)
    mentions_context_path = False
    for item in context_files or []:
        if _mentions_path(text, item.path):
            # Ignore when the "context" path is the target itself.
            if Path(str(item.path).replace("\\", "/")).name.lower() == Path(
                str(target_path).replace("\\", "/")
            ).name.lower():
                continue
            mentions_context_path = True
            break

    # Explicit sibling subject with no target path → misattribution (the §30 bug),
    # unless a symbol defined in the target clearly anchors the finding there
    # (e.g. "checkout mutates inventory_data_store" while reviewing checkout).
    if mentions_context_path and not mentions_target_path:
        if _mentions_symbol(text, _target_defined_symbols(target_source)):
            return False
        return True
    if mentions_target_path:
        return False

    # No file path cited — require a clear content match to a target symbol.
    if _mentions_symbol(text, _target_defined_symbols(target_source)):
        return False
    return True


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
    *,
    context_files: list[ContextFile] | None = None,
    target_source: str = "",
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
    location_raw = str(issue.get("location") or "").strip()
    if is_security_category_bleed(
        finding_type=finding_type,
        message=message,
        evidence=evidence_text,
        suggested_fix=suggested_fix,
    ):
        return None
    if is_insufficient_structure_claim(
        finding_type=finding_type,
        target_source=target_source,
        message=message,
        evidence=evidence_text,
        suggested_fix=suggested_fix,
    ):
        return None
    if is_soft_only_smell(
        finding_type,
        message=message,
        evidence=evidence_text,
        suggested_fix=suggested_fix,
    ):
        return None
    if is_off_target_finding(
        target_path,
        location=location_raw,
        evidence=evidence_text,
        message=message,
        suggested_fix=suggested_fix,
        context_files=context_files,
        target_source=target_source,
    ):
        return None

    location = location_raw or target_path
    evidence = "\n".join(part for part in [location, evidence_text, message] if part)
    confidence = adjust_architecture_confidence(
        finding_type,
        _bounded_confidence(issue.get("confidence"), default=70),
        message=message,
        evidence=evidence_text,
        suggested_fix=suggested_fix,
    )
    try:
        return StructuredFinding(
            finding_type=finding_type,
            evidence=evidence,
            confidence=confidence,
            suggested_fix=suggested_fix,
            detection_method="llm_reasoning",
        )
    except Exception as exc:  # noqa: BLE001 — schema reject → drop + audit
        try:
            from app.audit import STAGE_SCHEMA_VALIDATION, log_audit_stage

            log_audit_stage(
                STAGE_SCHEMA_VALIDATION,
                worker_name="architecture",
                detail={"ok": False, "error": str(exc), "finding_type": finding_type},
            )
        except Exception:  # noqa: BLE001
            pass
        return None


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
                temperature=0,
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
                finding = _issue_to_finding(
                    target_path,
                    issue,
                    context_files=context_files,
                    target_source=target_source,
                )
                if finding is not None:
                    structured_findings.append(finding)

        # Guard: model claimed issues but gave nothing specific → clean.
        # Filters (authz bleed, soft smell, off-target, insufficient-structure)
        # may drop every issue while the LLM summary still describes the claim.
        # Keep that claim in the agent_event log; neutralize the user-facing summary.
        if has_issues and not structured_findings:
            has_issues = False
            log_agent_event(
                "architecture_worker claimed issues but produced none specific; "
                "treating as clean"
            )
            log_agent_event(
                f"architecture_worker: clean — "
                f"{summary or 'No architecture issues found.'}"
            )
            summary = "No architecture issues found."
        elif not has_issues:
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
