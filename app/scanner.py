"""Semgrep static-analysis wrapper."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

from app.hooks import log_tool_call

# Substrings seen in real Semgrep stderr when rule packs fail to download (§21 DNS).
_NETWORK_MARKERS = (
    "getaddrinfo",
    "name or service not known",
    "nodename nor servname",
    "failed to resolve",
    "max retries exceeded",
    "max retries",
    "connection refused",
    "connection aborted",
    "connection reset",
    "timed out",
    "timeout",
    "temporary failure in name resolution",
    "network is unreachable",
    "semgrep.dev",
    "httpsconnectionpool",
    "nameresolutionerror",
    "newconnectionerror",
)


class Finding(TypedDict):
    rule_id: str
    severity: str
    path: str
    line: int
    message: str
    snippet: str


class ScanError(RuntimeError):
    """Raised when Semgrep cannot complete a scan."""

    def __init__(self, message: str, *, findings: list[Finding] | None = None) -> None:
        super().__init__(message)
        self.findings = findings or []


def format_semgrep_failure_message(*, stderr: str = "", stdout: str = "") -> str:
    """Map Semgrep failure text to a short CLI-safe message (no raw traceback dump)."""
    detail = (stderr or stdout or "").strip()
    lowered = detail.lower()
    if any(marker in lowered for marker in _NETWORK_MARKERS):
        return "Semgrep scan failed: network error, falling back to logic-review"
    if not detail:
        return "Semgrep scan failed"
    first_line = next(
        (line.strip() for line in detail.splitlines() if line.strip()),
        detail,
    )
    if len(first_line) > 160:
        first_line = first_line[:157] + "..."
    return f"Semgrep scan failed: {first_line}"


def _resolve_semgrep() -> str:
    """Prefer the Semgrep next to this Python (venv), then PATH."""
    scripts_dir = Path(sys.executable).resolve().parent
    for name in ("semgrep.exe", "semgrep"):
        candidate = scripts_dir / name
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("semgrep")
    if found:
        return found
    raise FileNotFoundError(
        "Semgrep is not installed or is not on PATH. "
        "Install dependencies with: pip install -r requirements.txt"
    )


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
def run_static_scan(
    paths: list[str], *, config_path: str | Path | None = None
) -> list[Finding]:
    """Run Semgrep against paths and return normalized findings."""
    if not paths:
        raise ValueError("At least one path is required.")

    try:
        semgrep_bin = _resolve_semgrep()
    except FileNotFoundError as exc:
        raise ScanError(str(exc)) from exc

    configs = [str(config_path)] if config_path is not None else ["p/python", "p/javascript"]
    command = [semgrep_bin, "scan"]
    for config in configs:
        command.extend(["--config", config])
    if config_path is not None:
        command.extend(["--metrics=off", "--disable-version-check", "--no-rewrite-rule-ids"])
    command.extend(["--json", *paths])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60 if config_path is not None else None,
        )
    except FileNotFoundError as exc:
        raise ScanError(
            "Semgrep is not installed or is not on PATH. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ScanError(
            "Semgrep scan failed: timed out, falling back to logic-review"
        ) from exc
    except OSError as exc:
        raise ScanError(
            format_semgrep_failure_message(stderr=str(exc))
        ) from exc

    if completed.returncode != 0 and config_path is None:
        raise ScanError(
            format_semgrep_failure_message(
                stderr=completed.stderr or "",
                stdout=completed.stdout or "",
            )
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ScanError("Semgrep returned invalid JSON output.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ScanError("Semgrep returned invalid result data.")

    findings: list[Finding] = []
    invalid_results = False
    for result in payload.get("results", []):
        if not isinstance(result, dict) or any(
            not isinstance(result.get(field, {}), dict)
            for field in ("extra", "start", "end")
        ):
            invalid_results = True
            continue
        extra = result.get("extra", {})
        start_line = result.get("start", {}).get("line")
        end_line = result.get("end", {}).get("line", start_line)
        if (
            not all(
                isinstance(result.get(field), str) and result[field] and "\x00" not in result[field]
                for field in ("check_id", "path")
            )
            or type(start_line) is not int or start_line < 1
            or type(end_line) is not int or end_line < start_line
            or not all(isinstance(extra.get(field, ""), str) for field in ("severity", "message", "lines"))
        ):
            invalid_results = True
            continue
        findings.append(
            {
                "rule_id": result.get("check_id", ""),
                "severity": extra.get("severity", ""),
                "path": result.get("path", ""),
                "line": start_line,
                "message": extra.get("message", ""),
                "snippet": _source_snippet(result),
            }
        )

    if invalid_results:
        raise ScanError("Semgrep returned invalid finding data.", findings=findings)
    if config_path is not None:
        path_info = payload.get("paths")
        scanned_paths = path_info.get("scanned") if isinstance(path_info, dict) else None
        if not isinstance(scanned_paths, list) or not all(isinstance(path, str) for path in scanned_paths):
            raise ScanError("Semgrep returned invalid coverage data.", findings=findings)
        if not isinstance(payload.get("errors"), list):
            raise ScanError("Semgrep returned invalid error data.", findings=findings)
        try:
            scanned = {Path(path).resolve() for path in scanned_paths}
            requested = {Path(path).resolve() for path in paths}
        except (OSError, ValueError) as exc:
            raise ScanError("Semgrep returned invalid coverage paths.", findings=findings) from exc
        if (
            completed.returncode != 0
            or payload.get("errors")
            or not requested.issubset(scanned)
        ):
            raise ScanError("Semgrep reported incomplete analysis.", findings=findings)
    return findings
