"""Deterministic cross-file context gathering for the Architecture Worker."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_MAX_FILES = 6
_DEFAULT_MAX_FILE_CHARS = 2000
_DEFAULT_MAX_TOTAL_CHARS = 8000
_MAX_REVERSE_SCAN_FILES = 40


@dataclass
class ContextFile:
    path: str
    relation: str
    content: str


def _find_project_root(start: Path) -> Path:
    current = start if start.is_dir() else start.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def _module_name_for(path: Path, project_root: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return None
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _path_for_module(module: str, project_root: Path) -> Path | None:
    candidate = project_root / Path(*module.split("."))
    file_candidate = candidate.with_suffix(".py")
    if file_candidate.is_file():
        return file_candidate
    init_candidate = candidate / "__init__.py"
    if init_candidate.is_file():
        return init_candidate
    return None


def _imported_modules(source: str) -> list[str]:
    """First-party-looking absolute imports, in source order (may repeat)."""
    modules: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return modules
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _read_truncated(path: Path, *, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + "\n... [truncated]"
    return text


def gather_cross_file_context(
    target_path: str | Path,
    *,
    project_root: str | Path | None = None,
    max_files: int = _DEFAULT_MAX_FILES,
    max_file_chars: int = _DEFAULT_MAX_FILE_CHARS,
    max_total_chars: int = _DEFAULT_MAX_TOTAL_CHARS,
) -> list[ContextFile]:
    """Gather a small, deterministic set of files related to ``target_path``.

    Priority order: first-party imports of the target, sibling files in the
    same package/directory, then files elsewhere in the target's top-level
    package that import the target back ("callers"). This is a text/AST
    heuristic, not a full dependency graph — deterministic and bounded so a
    layering call has real cross-file evidence without unbounded LLM input.
    """
    target = Path(target_path).resolve()
    root = Path(project_root).resolve() if project_root else _find_project_root(target)

    try:
        source = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        source = ""

    ordered: list[tuple[Path, str]] = []
    seen: set[Path] = {target}

    for module in _imported_modules(source):
        resolved = _path_for_module(module, root)
        if resolved is not None and resolved not in seen:
            ordered.append((resolved, "imported_by_target"))
            seen.add(resolved)

    if target.parent.is_dir():
        for sibling in sorted(target.parent.glob("*.py")):
            if sibling not in seen and sibling.name != "__init__.py":
                ordered.append((sibling, "same_package"))
                seen.add(sibling)

    target_module = _module_name_for(target, root)
    if target_module:
        try:
            relative_parts = target.relative_to(root).parts
        except ValueError:
            relative_parts = ()
        top_level_dir = root / relative_parts[0] if relative_parts else target.parent
        if top_level_dir.is_file():
            top_level_dir = root
        if top_level_dir.is_dir():
            candidates = sorted(top_level_dir.rglob("*.py"))[:_MAX_REVERSE_SCAN_FILES]
            for candidate in candidates:
                if candidate in seen:
                    continue
                try:
                    candidate_source = candidate.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if target_module in candidate_source:
                    ordered.append((candidate, "imports_target"))
                    seen.add(candidate)

    context_files: list[ContextFile] = []
    total_chars = 0
    for path, relation in ordered:
        if len(context_files) >= max_files:
            break
        content = _read_truncated(path, max_chars=max_file_chars)
        if not content:
            continue
        if total_chars + len(content) > max_total_chars:
            remaining = max_total_chars - total_chars
            if remaining <= 0:
                break
            content = content[:remaining] + "\n... [truncated]"
        total_chars += len(content)
        try:
            display_path = str(path.relative_to(root))
        except ValueError:
            display_path = str(path)
        context_files.append(
            ContextFile(path=display_path, relation=relation, content=content)
        )

    return context_files
