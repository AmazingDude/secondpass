"""Deterministic cross-file context gathering for the Architecture Worker."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_MAX_FILES = 6
_DEFAULT_MAX_FILE_CHARS = 2000
_DEFAULT_MAX_TOTAL_CHARS = 8000
_MAX_REVERSE_SCAN_FILES = 40

_STDLIB_NAMES = frozenset(getattr(sys, "stdlib_module_names", ()) or ())


@dataclass
class ContextFile:
    path: str
    relation: str
    content: str


@dataclass(frozen=True)
class ImportFact:
    """One import from a target file, classified against the project tree."""

    module: str
    kind: str  # stdlib | resolved_project | unresolved_external
    resolved_path: str | None = None


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


def _is_stdlib_module(module: str) -> bool:
    top = (module or "").split(".", 1)[0]
    if not top:
        return False
    if _STDLIB_NAMES:
        return top in _STDLIB_NAMES
    return False


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path)


def _resolve_relative_import(
    target: Path,
    *,
    module: str | None,
    level: int,
    project_root: Path,
) -> tuple[str, Path | None]:
    """Return (dotted module name, resolved path or None) for a relative import."""
    package_dir = target.parent
    for _ in range(max(level - 1, 0)):
        package_dir = package_dir.parent

    parts = list(module.split(".")) if module else []
    if parts:
        candidate_base = package_dir / Path(*parts)
        file_candidate = candidate_base.with_suffix(".py")
        init_candidate = candidate_base / "__init__.py"
        if file_candidate.is_file():
            resolved = file_candidate
        elif init_candidate.is_file():
            resolved = init_candidate
        else:
            resolved = None
    else:
        init_candidate = package_dir / "__init__.py"
        resolved = init_candidate if init_candidate.is_file() else None

    dotted = _module_name_for(resolved, project_root) if resolved is not None else None
    if dotted is None:
        try:
            rel_pkg = package_dir.resolve().relative_to(project_root.resolve())
            pkg_parts = list(rel_pkg.parts) + parts
            dotted = ".".join(pkg_parts) if pkg_parts else (module or "")
        except ValueError:
            dotted = module or package_dir.name
    return dotted, resolved


def classify_imports(
    source: str,
    *,
    target_path: str | Path,
    project_root: str | Path | None = None,
) -> list[ImportFact]:
    """Classify target imports as stdlib, resolved project, or unresolved external.

    Uses ``sys.stdlib_module_names`` for the stdlib set. A module is
    ``resolved_project`` only when a real ``.py`` / package ``__init__.py``
    exists under the project root (absolute) or via a resolvable relative import.
    Framework imports that are not present in the tree (Django, Werkzeug's
    missing ``.repr`` sibling when isolated, etc.) stay ``unresolved_external``.
    """
    target = Path(target_path)
    try:
        target = target.resolve()
    except OSError:
        pass
    root = Path(project_root).resolve() if project_root else _find_project_root(target)

    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return []

    facts: list[ImportFact] = []
    seen: set[tuple[str, str]] = set()

    def _add(module: str, kind: str, resolved: Path | None) -> None:
        key = (module, kind)
        if not module or key in seen:
            return
        seen.add(key)
        facts.append(
            ImportFact(
                module=module,
                kind=kind,
                resolved_path=_display_path(resolved, root) if resolved else None,
            )
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name or ""
                if _is_stdlib_module(module):
                    _add(module, "stdlib", None)
                    continue
                resolved = _path_for_module(module, root)
                if resolved is not None:
                    _add(module, "resolved_project", resolved)
                else:
                    _add(module, "unresolved_external", None)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                dotted, resolved = _resolve_relative_import(
                    target,
                    module=node.module,
                    level=node.level,
                    project_root=root,
                )
                if resolved is not None:
                    _add(dotted, "resolved_project", resolved)
                else:
                    _add(dotted or (node.module or ""), "unresolved_external", None)
                continue
            module = node.module or ""
            if not module:
                continue
            if _is_stdlib_module(module):
                _add(module, "stdlib", None)
                continue
            resolved = _path_for_module(module, root)
            if resolved is not None:
                _add(module, "resolved_project", resolved)
            else:
                _add(module, "unresolved_external", None)

    return facts


def resolved_project_modules(facts: list[ImportFact]) -> list[ImportFact]:
    return [fact for fact in facts if fact.kind == "resolved_project"]


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
    same explicit package, then files elsewhere in the target's top-level
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

    if target.parent.is_dir() and (target.parent / "__init__.py").is_file():
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
