"""Semgrep static-analysis wrapper."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TypedDict

from app.hooks import log_tool_call


class Finding(TypedDict):
    rule_id: str
    severity: str
    path: str
    line: int
    message: str
    snippet: str


class ScanError(RuntimeError):
    """Raised when Semgrep cannot complete a scan."""


def _source_snippet(result: dict) -> str:
    path = result.get("path", "")
    start_line = result.get("start", {}).get("line", 0)
    end_line = result.get("end", {}).get("line", start_line)

    if path and start_line:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            return "\n".join(lines[start_line - 1 : end_line]).strip()
        except (OSError, UnicodeError):
            pass

    return result.get("extra", {}).get("lines", "").strip()


@log_tool_call
def run_static_scan(paths: list[str]) -> list[Finding]:
    """Run Semgrep against paths and return normalized findings."""
    if not paths:
        raise ValueError("At least one path is required.")

    command = [
        "semgrep",
        "scan",
        "--config",
        "p/python",
        "--config",
        "p/javascript",
        "--json",
        *paths,
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:
        raise ScanError(
            "Semgrep is not installed or is not on PATH. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ScanError(f"Semgrep scan failed: {detail}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ScanError("Semgrep returned invalid JSON output.") from exc

    findings: list[Finding] = []
    for result in payload.get("results", []):
        extra = result.get("extra", {})
        findings.append(
            {
                "rule_id": result.get("check_id", ""),
                "severity": extra.get("severity", ""),
                "path": result.get("path", ""),
                "line": result.get("start", {}).get("line", 0),
                "message": extra.get("message", ""),
                "snippet": _source_snippet(result),
            }
        )

    return findings
