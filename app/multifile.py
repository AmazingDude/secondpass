"""Deterministic multi-file review selection and aggregation.

Selection prefers modules that are likely to hold Security/Architecture
signal under a hard ``--max-files`` cap. Package markers (``__init__.py``)
are excluded by default because ownership bugs, injections, secrets, and
layering issues almost never live there — not because they can never be
buggy. Opt in with ``--include-init``; trivial/empty markers still drop.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)


@dataclass(frozen=True)
class FileSelection:
    """Selected paths plus concise skip counters for CLI transparency."""

    root: Path
    selected: tuple[Path, ...]
    discovered_count: int
    skipped_init_count: int
    skipped_trivial_count: int
    skipped_include_count: int
    skipped_exclude_count: int
    junk_dirs_pruned: int
    eligible_count: int
    include_init: bool
    capped: bool

    @property
    def selected_count(self) -> int:
        return len(self.selected)

    def relative_selected(self) -> list[str]:
        return [path.relative_to(self.root).as_posix() for path in self.selected]


def relative_posix(path: Path, root: Path) -> str:
    """Normalize a path as a POSIX-style relative string for match/display."""
    return path.resolve().relative_to(root.resolve()).as_posix()


def discover_python_files(directory: Path) -> tuple[list[Path], int]:
    """Find regular ``.py`` files; skip junk dirs; do not follow dir symlinks.

    Returns ``(paths_in_walk_order_unsorted, junk_dirs_pruned)``.
    """
    root = directory.resolve()
    discovered: list[Path] = []
    junk_dirs_pruned = 0
    # followlinks=False (default): never descend into directory symlinks.
    for current, directories, files in os.walk(root, followlinks=False):
        kept: list[str] = []
        for name in sorted(directories):
            child = Path(current, name)
            if name in _SKIP_DIRECTORIES:
                junk_dirs_pruned += 1
                continue
            if child.is_symlink():
                # Extra guard: treat symlink dirs as non-traversable.
                junk_dirs_pruned += 1
                continue
            kept.append(name)
        directories[:] = kept
        for name in files:
            if not name.endswith(".py"):
                continue
            candidate = Path(current, name)
            if candidate.is_symlink() or not candidate.is_file():
                continue
            discovered.append(candidate.resolve())
    return discovered, junk_dirs_pruned


def is_trivial_python_source(source: str) -> bool:
    """Return True when the module has no review-relevant structure.

    Trivial means empty / comments / shebang / encoding / one module docstring /
    a bare ``pass`` only. Decode and parse failures are the caller's problem —
    this helper assumes ``source`` is already decoded text.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Keep ineligible classification out of this helper: callers treat
        # unparsable files as non-trivial so the review pipeline can surface them.
        return False

    body = list(tree.body)
    if not body:
        return True

    # Strip a single leading module docstring.
    if isinstance(body[0], ast.Expr) and isinstance(
        body[0].value, ast.Constant
    ) and isinstance(body[0].value.value, str):
        body = body[1:]

    if not body:
        return True
    if len(body) == 1 and isinstance(body[0], ast.Pass):
        return True
    return False


def is_trivial_python_file(path: Path) -> bool:
    """True only when the file decodes and is structurally trivial.

    Undecodable or unparsable files return False (remain eligible).
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return is_trivial_python_source(source)


def _match_relative(relative: str, pattern: str) -> bool:
    """Match a POSIX relative path against a user glob (supports ``**``)."""
    # PurePosixPath.match only gained recursive ``**`` in 3.13; keep a
    # small stdlib helper so ``tests/**`` works on 3.12+.
    if "**" not in pattern:
        return fnmatch.fnmatch(relative, pattern)

    regex_parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            regex_parts.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            regex_parts.append(".*")
            index += 2
            continue
        char = pattern[index]
        if char == "*":
            regex_parts.append("[^/]*")
        elif char == "?":
            regex_parts.append("[^/]")
        else:
            regex_parts.append(re.escape(char))
        index += 1
    return re.fullmatch("".join(regex_parts), relative) is not None


def _matches_any(relative: str, patterns: Sequence[str]) -> bool:
    return any(_match_relative(relative, pattern) for pattern in patterns)


def normalize_path_pattern(pattern: str, root: Path) -> str:
    """Keep intentional globs; rewrite shell/Click-expanded paths to root-relative POSIX.

    On Windows, Click expands ``**/foo.py`` against the process cwd before our
    CLI sees the value. Matching then fails because selection compares
    root-relative paths like ``architecture/foo.py``. If ``pattern`` resolves
    to a real file under ``root``, rewrite it to that relative path so include /
    exclude still work. Globs (any ``*`` / ``?`` / ``[``) are left alone aside
    from backslash → slash normalization.
    """
    normalized = pattern.replace("\\", "/")
    if any(char in normalized for char in "*?["):
        return normalized

    candidate = Path(pattern)
    try:
        resolved = candidate.resolve()
    except OSError:
        return normalized
    if not resolved.is_file():
        return normalized
    try:
        return relative_posix(resolved, root.resolve())
    except ValueError:
        return normalized


def select_python_files(
    directory: Path,
    *,
    max_files: int,
    include_init: bool = False,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> FileSelection:
    """Discover, filter, sort, then cap — no hidden ranking."""
    if max_files < 1:
        raise ValueError("max_files must be at least 1")

    root = directory.resolve()
    discovered, junk_dirs_pruned = discover_python_files(root)
    discovered_count = len(discovered)

    include_patterns = tuple(
        normalize_path_pattern(pattern, root) for pattern in include
    )
    exclude_patterns = tuple(
        normalize_path_pattern(pattern, root) for pattern in exclude
    )

    skipped_include = 0
    skipped_exclude = 0
    skipped_init = 0
    skipped_trivial = 0
    eligible: list[Path] = []

    for path in discovered:
        relative = relative_posix(path, root)

        if include_patterns and not _matches_any(relative, include_patterns):
            skipped_include += 1
            continue
        if exclude_patterns and _matches_any(relative, exclude_patterns):
            skipped_exclude += 1
            continue

        if path.name == "__init__.py" and not include_init:
            skipped_init += 1
            continue

        if is_trivial_python_file(path):
            skipped_trivial += 1
            continue

        eligible.append(path)

    eligible.sort(key=lambda item: relative_posix(item, root))
    selected = tuple(eligible[:max_files])
    return FileSelection(
        root=root,
        selected=selected,
        discovered_count=discovered_count,
        skipped_init_count=skipped_init,
        skipped_trivial_count=skipped_trivial,
        skipped_include_count=skipped_include,
        skipped_exclude_count=skipped_exclude,
        junk_dirs_pruned=junk_dirs_pruned,
        eligible_count=len(eligible),
        include_init=include_init,
        capped=len(eligible) > len(selected),
    )


ReviewOne = Callable[[str], dict[str, Any]]
FileStartCallback = Callable[[Path, int, int], None]
FileDoneCallback = Callable[[Path, dict[str, Any]], None]


def review_python_files(
    paths: Sequence[Path],
    *,
    workers: int,
    review_one: ReviewOne,
    on_file_start: FileStartCallback | None = None,
    on_file_done: FileDoneCallback | None = None,
) -> dict[str, Any]:
    """Run one existing full review per file and combine counts in input order."""
    if workers < 1:
        raise ValueError("workers must be at least 1")

    ordered_paths = list(paths)
    total = len(ordered_paths)

    def _run_one(index_path: tuple[int, Path]) -> dict[str, Any]:
        index, path = index_path
        if on_file_start is not None:
            on_file_start(path, index, total)
        report = review_one(str(path))
        if on_file_done is not None:
            on_file_done(path, report)
        return report

    indexed = list(enumerate(ordered_paths, start=1))
    if workers == 1:
        reports = [_run_one(item) for item in indexed]
    else:
        with ThreadPoolExecutor(
            max_workers=min(workers, len(ordered_paths) or 1),
            thread_name_prefix="secondpass-file",
        ) as executor:
            # map preserves input order in the returned list.
            reports = list(executor.map(_run_one, indexed))

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
