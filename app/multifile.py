"""Deterministic multi-file review selection and aggregation."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

_SKIP_DIRECTORIES = frozenset({".venv", "node_modules", ".git", "__pycache__"})


def discover_python_files(directory: Path) -> list[Path]:
    """Find reviewable Python files in deterministic relative-path order."""
    root = directory.resolve()
    discovered: list[Path] = []
    for current, directories, files in os.walk(root):
        directories[:] = sorted(
            name for name in directories if name not in _SKIP_DIRECTORIES
        )
        discovered.extend(
            Path(current, name).resolve()
            for name in files
            if name.endswith(".py")
        )

    discovered.sort(key=lambda path: path.relative_to(root).as_posix())
    return discovered


def select_python_files(directory: Path, *, max_files: int) -> list[Path]:
    """Return the first N Python files after deterministic relative-path sorting."""
    if max_files < 1:
        raise ValueError("max_files must be at least 1")
    return discover_python_files(directory)[:max_files]


ReviewOne = Callable[[str], dict[str, Any]]
FileStartCallback = Callable[[Path, int, int], None]


def review_python_files(
    paths: Sequence[Path],
    *,
    workers: int,
    review_one: ReviewOne,
    on_file_start: FileStartCallback | None = None,
) -> dict[str, Any]:
    """Run one existing full review per file and combine counts in input order."""
    if workers < 1:
        raise ValueError("workers must be at least 1")

    ordered_paths = list(paths)
    if workers == 1:
        reports: list[dict[str, Any]] = []
        for index, path in enumerate(ordered_paths, start=1):
            if on_file_start is not None:
                on_file_start(path, index, len(ordered_paths))
            reports.append(review_one(str(path)))
    else:
        if on_file_start is not None:
            for index, path in enumerate(ordered_paths, start=1):
                on_file_start(path, index, len(ordered_paths))
        with ThreadPoolExecutor(
            max_workers=min(workers, len(ordered_paths) or 1),
            thread_name_prefix="secondpass-file",
        ) as executor:
            # executor.map returns in input order even when reviews finish out of order.
            reports = list(executor.map(lambda path: review_one(str(path)), ordered_paths))

    files: list[dict[str, Any]] = []
    total_accepted = 0
    total_needs_review = 0
    for path, report in zip(ordered_paths, reports, strict=True):
        summary = report.get("summary") or {}
        accepted = int(summary.get("accepted_count") or 0)
        needs_review = int(summary.get("needs_review_count") or 0)
        total_accepted += accepted
        total_needs_review += needs_review
        files.append(
            {
                "path": str(path),
                "accepted_count": accepted,
                "needs_review_count": needs_review,
                "report": report,
            }
        )

    return {
        "file_count": len(files),
        "accepted_count": total_accepted,
        "needs_review_count": total_needs_review,
        "files": files,
    }
