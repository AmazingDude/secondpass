"""Unit tests for deterministic cross-file context gathering."""

from __future__ import annotations

from pathlib import Path

from app.context import gather_cross_file_context


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(
        tmp_path / "pkg" / "a.py",
        "from pkg.b import helper\n\n\ndef use():\n    return helper()\n",
    )
    _write(tmp_path / "pkg" / "b.py", "def helper():\n    return 1\n")
    _write(tmp_path / "pkg" / "c.py", "def other():\n    return 2\n")
    _write(tmp_path / "pkg" / "sub" / "__init__.py", "")
    _write(
        tmp_path / "pkg" / "sub" / "caller.py",
        "from pkg.a import use\n\n\ndef run():\n    return use()\n",
    )
    return tmp_path


def test_gathers_imports_siblings_and_reverse_callers(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    target = root / "pkg" / "a.py"

    context = gather_cross_file_context(target, project_root=root, max_files=10)
    by_path = {item.path.replace("\\", "/"): item for item in context}

    assert by_path["pkg/b.py"].relation == "imported_by_target"
    assert by_path["pkg/c.py"].relation == "same_package"
    assert by_path["pkg/sub/caller.py"].relation == "imports_target"


def test_priority_order_is_imports_then_siblings_then_callers(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    target = root / "pkg" / "a.py"

    context = gather_cross_file_context(target, project_root=root, max_files=10)
    relations = [item.relation for item in context]

    assert relations.index("imported_by_target") < relations.index("same_package")
    assert relations.index("same_package") < relations.index("imports_target")


def test_max_files_caps_and_respects_priority(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    target = root / "pkg" / "a.py"

    context = gather_cross_file_context(target, project_root=root, max_files=2)

    assert len(context) == 2
    assert [item.relation for item in context] == ["imported_by_target", "same_package"]


def test_max_total_chars_truncates_and_stops(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    big_content = "x = 1\n" * 5000
    _write(root / "pkg" / "b.py", big_content)
    target = root / "pkg" / "a.py"

    context = gather_cross_file_context(
        target,
        project_root=root,
        max_files=10,
        max_file_chars=100,
        max_total_chars=150,
    )

    assert context
    assert all(len(item.content) <= 120 for item in context)
    total_chars = sum(len(item.content) for item in context)
    assert total_chars <= 200


def test_target_with_no_local_imports_returns_only_siblings_and_callers(
    tmp_path: Path,
) -> None:
    root = _make_project(tmp_path)
    _write(root / "pkg" / "a.py", "def use():\n    return 1\n")

    context = gather_cross_file_context(root / "pkg" / "a.py", project_root=root)
    relations = {item.relation for item in context}

    assert "imported_by_target" not in relations
    assert "same_package" in relations


def test_loose_directory_does_not_create_same_package_context(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    loose = tmp_path / "loose"
    _write(loose / "target.py", "import json\n\nVALUE = json.dumps({})\n")
    _write(loose / "unrelated.py", "def unrelated():\n    return 1\n")

    context = gather_cross_file_context(
        loose / "target.py", project_root=tmp_path, max_files=10
    )

    assert all(item.relation != "same_package" for item in context)
    assert all(not item.path.replace("\\", "/").endswith("unrelated.py") for item in context)


def test_architecture_fixture_package_keeps_sibling_context() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / "benchmark" / "fixtures" / "architecture" / "checkout_handler.py"

    context = gather_cross_file_context(target, project_root=root, max_files=10)
    by_path = {item.path.replace("\\", "/"): item for item in context}

    sibling = "benchmark/fixtures/architecture/low_level_persistence_client.py"
    assert by_path[sibling].relation == "same_package"


def test_project_root_auto_detected_via_git(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    target = root / "pkg" / "a.py"

    context = gather_cross_file_context(target)

    paths = {item.path.replace("\\", "/") for item in context}
    assert "pkg/b.py" in paths


def test_syntax_error_in_target_does_not_crash(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    _write(root / "pkg" / "a.py", "def broken(:\n")

    context = gather_cross_file_context(root / "pkg" / "a.py", project_root=root)

    assert any(item.relation == "same_package" for item in context)
