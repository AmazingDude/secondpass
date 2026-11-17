"""Persist benchmark runs without replacing an earlier result."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run as subprocess_run
from typing import Any
from uuid import uuid4


def capture_input_manifest(
    repo_root: Path, inputs: list[tuple[str, Path]],
) -> dict[str, Any]:
    """Record preflight input bytes and available Git identity, not a pinned snapshot."""
    revision: str | None = None
    dirty: bool | None = None
    if (repo_root / ".git").exists():
        try:
            revision_result = subprocess_run(
                ["git", "rev-parse", "HEAD"], cwd=repo_root,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=True, timeout=5,
            )
            revision = revision_result.stdout.decode("ascii").strip()
            dirty = bool(subprocess_run(
                ["git", "status", "--porcelain"], cwd=repo_root,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=True, timeout=5,
            ).stdout.strip())
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            revision = None
            dirty = None

    rows: list[dict[str, str | None]] = []
    for role, path in inputs:
        target = path if path.is_absolute() else repo_root / path
        digest = sha256()
        try:
            with target.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            status, fingerprint = "present", digest.hexdigest()
        except FileNotFoundError:
            status, fingerprint = "missing", None
        except OSError:
            status, fingerprint = "unreadable", None
        rows.append({
            "role": role,
            "path": str(path) if path.is_absolute() else path.as_posix(),
            "status": status,
            "sha256": fingerprint,
        })

    return {
        "schema": "inputs-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": revision,
        "git_dirty": dirty,
        "inputs": rows,
    }


def validate_benchmark_label(label: str) -> None:
    """Keep a user-chosen label inside the result directory on every platform."""
    if not isinstance(label, str) or not label.strip() or re.search(r'[\x00-\x1f\x7f<>:"/\\|?*]', label):
        raise ValueError("benchmark label must not be blank or contain path or filename characters")


def write_benchmark_result(
    results_dir: Path, payload: dict[str, Any], *, default: Any = None,
) -> Path:
    """Save one result under its label and date with a unique run ID."""
    label = payload["label"]
    validate_benchmark_label(label)
    run_id = uuid4().hex
    payload["run_id"] = run_id
    contents = json.dumps(payload, indent=2, default=default) + "\n"
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{label}_{payload['date']}_{run_id}.json"
    with path.open("x", encoding="utf-8") as result_file:
        result_file.write(contents)
    return path
