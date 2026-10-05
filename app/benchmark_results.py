"""Persist benchmark runs without replacing an earlier result."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4


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
