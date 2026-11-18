"""File-only static review shared by local CLI and MCP callers."""

from __future__ import annotations

import re
from importlib.resources import as_file, files
from pathlib import Path
from typing import Literal, TypedDict

from app.scanner import Finding, ScanError, run_static_scan


class StaticReviewReport(TypedDict):
    mode: Literal["static"]
    path: str
    rules_path: str | None
    coverage_status: Literal["ok", "inconclusive"]
    inconclusive: bool
    finding_count: int
    findings: list[Finding]
    analysis_scope: Literal["single_python_file_static_patterns"]
    persisted: Literal[False]


def review_static_file(
    path: str | Path, *, rules_path: str | Path | None = None,
) -> StaticReviewReport:
    """Inspect local rules without models; coverage applies only to this scan."""
    inputs = [path] if rules_path is None else [path, rules_path]
    for item in inputs:
        normalized = str(item).replace("\\", "/")
        if normalized.startswith("//") and not re.match(r"^//\?/[A-Za-z]:/", normalized):
            raise ValueError("Static review requires local paths.")
    target = Path(path).expanduser().resolve()
    if not target.is_file() or target.suffix != ".py":
        raise ValueError("Static review requires one Python file.")
    selected_rules = Path(rules_path).expanduser().resolve() if rules_path is not None else None
    if selected_rules is not None and not selected_rules.is_file():
        raise ValueError("Static rules must be a local file.")
    incomplete = False
    try:
        source = selected_rules if selected_rules is not None else files("app").joinpath("static_rules.yml")
        with as_file(source) as rule_file:
            matches = run_static_scan([str(target)], config_path=rule_file)
    except ScanError as exc:
        incomplete = True
        matches = exc.findings
    return {
        "mode": "static", "path": str(target),
        "rules_path": str(selected_rules) if selected_rules is not None else None,
        "coverage_status": "inconclusive" if incomplete else "ok",
        "inconclusive": incomplete, "finding_count": len(matches), "findings": matches,
        "analysis_scope": "single_python_file_static_patterns", "persisted": False,
    }
