"""Git diff helpers for reviewing only changed code."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class GitDiffError(RuntimeError):
    """Raised when git diff cannot be collected."""


_HUNK_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")
_DIFF_GIT_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")


@dataclass
class ChangedFile:
    """A file touched by the selected diff, with new-side line ranges."""

    path: Path
    ranges: list[tuple[int, int]] = field(default_factory=list)
    is_new: bool = False

    def covers_line(self, line: int) -> bool:
        if line <= 0:
            return False
        if self.is_new and not self.ranges:
            # Brand-new file with no parseable hunks: treat all lines as in-scope.
            return True
        return any(start <= line <= end for start, end in self.ranges)


@dataclass
class DiffSelection:
    mode: str  # "staged" | "unstaged"
    files: list[ChangedFile]
    repo_root: Path


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitDiffError(
            "git is not installed or is not on PATH."
        ) from exc


def _find_repo_root(start: Path | None = None) -> Path:
    cwd = (start or Path.cwd()).resolve()
    completed = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise GitDiffError(
            detail or "Not inside a git repository. Run this from a repo checkout."
        )
    return Path(completed.stdout.strip()).resolve()


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def parse_unified_diff(diff_text: str, *, repo_root: Path) -> list[ChangedFile]:
    """Parse a unified diff into changed files and new-side line ranges."""
    files: dict[str, ChangedFile] = {}
    current: ChangedFile | None = None
    new_line = 0

    for raw_line in diff_text.splitlines():
        git_match = _DIFF_GIT_RE.match(raw_line)
        if git_match:
            rel = git_match.group(2).strip()
            path = (repo_root / rel).resolve()
            current = files.get(rel)
            if current is None:
                current = ChangedFile(path=path)
                files[rel] = current
            continue

        if raw_line.startswith("+++ "):
            target = raw_line[4:].strip()
            if target == "/dev/null":
                # File deleted in this diff — drop it from review targets.
                if current is not None:
                    for key, value in list(files.items()):
                        if value is current:
                            del files[key]
                    current = None
                continue
            if target.startswith("b/"):
                rel = target[2:]
                path = (repo_root / rel).resolve()
                current = files.get(rel) or ChangedFile(path=path)
                files[rel] = current
            continue

        if raw_line.startswith("--- "):
            source = raw_line[4:].strip()
            if source == "/dev/null" and current is not None:
                current.is_new = True
            continue

        if current is None:
            continue

        if raw_line.startswith("Binary files ") or raw_line.startswith("GIT binary patch"):
            # Skip binaries.
            for key, value in list(files.items()):
                if value is current:
                    del files[key]
            current = None
            continue

        hunk = _HUNK_RE.match(raw_line)
        if hunk:
            start = int(hunk.group(3))
            new_line = start
            # Do not mark the entire hunk window as changed — only added lines
            # below count, so pre-existing issues in context lines are ignored.
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            current.ranges.append((new_line, new_line))
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            # Removed line exists only on the old side.
            continue
        elif raw_line.startswith("\\"):
            # "\ No newline at end of file"
            continue
        elif raw_line.startswith(" "):
            new_line += 1

    result: list[ChangedFile] = []
    for changed in files.values():
        if not changed.path.exists() or not changed.path.is_file():
            continue
        changed.ranges = _merge_ranges(changed.ranges)
        result.append(changed)
    return sorted(result, key=lambda item: str(item.path))


def collect_diff_selection(start: Path | None = None) -> DiffSelection:
    """Prefer staged changes; fall back to unstaged working-tree changes.

    Staged-first matches a pre-commit workflow: review what you're about to
    commit. If the index is empty, fall back to unstaged edits so `review --diff`
    is still useful during active coding.
    """
    repo_root = _find_repo_root(start)

    staged = _run_git(["diff", "--staged", "--no-color", "--unified=3"], cwd=repo_root)
    if staged.returncode != 0:
        detail = staged.stderr.strip() or staged.stdout.strip()
        raise GitDiffError(f"git diff --staged failed: {detail}")

    if staged.stdout.strip():
        files = parse_unified_diff(staged.stdout, repo_root=repo_root)
        return DiffSelection(mode="staged", files=files, repo_root=repo_root)

    unstaged = _run_git(["diff", "--no-color", "--unified=3"], cwd=repo_root)
    if unstaged.returncode != 0:
        detail = unstaged.stderr.strip() or unstaged.stdout.strip()
        raise GitDiffError(f"git diff failed: {detail}")

    files = parse_unified_diff(unstaged.stdout, repo_root=repo_root)
    return DiffSelection(mode="unstaged", files=files, repo_root=repo_root)


def finding_in_changed_lines(
    finding_line: int,
    changed: ChangedFile,
    *,
    rule_id: str = "",
) -> bool:
    """Return True when a finding should be reported for this changed file."""
    # File-level logic reviews apply to any touched file.
    if rule_id == "secondpass.logic-review":
        return bool(changed.ranges) or changed.is_new
    return changed.covers_line(finding_line)
