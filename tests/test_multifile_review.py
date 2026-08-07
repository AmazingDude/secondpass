"""CLI multi-file selection and deterministic aggregation tests."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.confidence_gate import GateResult
from app.multifile import (
    is_trivial_python_source,
    review_python_files,
    select_python_files,
)
from app.persistence import (
    list_audit_events,
    list_reviews,
    save_audit_event,
    save_review,
)
from app.schema import ReviewResult


def _write(root: Path, relative: str, content: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_is_trivial_python_source_rules() -> None:
    assert is_trivial_python_source("")
    assert is_trivial_python_source("   \n# comment\n")
    assert is_trivial_python_source('"""module doc"""\n')
    assert is_trivial_python_source("pass\n")
    assert is_trivial_python_source('"""doc"""\npass\n')
    assert not is_trivial_python_source("x = 1\n")
    assert not is_trivial_python_source("from . import foo\n")
    assert not is_trivial_python_source("def f():\n    return 1\n")
    # Unparsable source is not classified as trivial.
    assert not is_trivial_python_source("def broken(\n")


def test_select_python_files_sorts_before_cap_and_skips_junk(tmp_path: Path) -> None:
    for relative in (
        "z_last.py",
        "pkg/c_mid.py",
        "a_first.py",
        "pkg/b_second.py",
        "README.md",
        ".venv/ignored.py",
        "venv/ignored.py",
        "env/ignored.py",
        "node_modules/ignored.py",
        ".git/ignored.py",
        "pkg/__pycache__/ignored.py",
        "dist/ignored.py",
        "build/ignored.py",
        ".pytest_cache/ignored.py",
    ):
        _write(tmp_path, relative, "value = 1\n")

    selection = select_python_files(tmp_path, max_files=3)

    assert selection.relative_selected() == [
        "a_first.py",
        "pkg/b_second.py",
        "pkg/c_mid.py",
    ]
    assert selection.discovered_count == 4
    assert selection.eligible_count == 4
    assert selection.capped is True
    assert selection.junk_dirs_pruned >= 1


def test_default_excludes_init_even_when_nontrivial(tmp_path: Path) -> None:
    _write(tmp_path, "__init__.py", "from .mod import x\n")
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "mod.py", "x = 1\n")

    selection = select_python_files(tmp_path, max_files=10)

    assert selection.relative_selected() == ["mod.py"]
    assert selection.skipped_init_count == 2
    assert selection.skipped_trivial_count == 0


def test_include_init_keeps_nontrivial_and_skips_trivial_init(tmp_path: Path) -> None:
    _write(tmp_path, "__init__.py", '"""package"""\n')
    _write(tmp_path, "pkg/__init__.py", "from .mod import helper\n")
    _write(tmp_path, "pkg/mod.py", "def helper():\n    return 1\n")

    selection = select_python_files(tmp_path, max_files=10, include_init=True)

    assert selection.relative_selected() == [
        "pkg/__init__.py",
        "pkg/mod.py",
    ]
    assert selection.skipped_init_count == 0
    assert selection.skipped_trivial_count == 1


def test_skips_comment_and_docstring_only_non_init(tmp_path: Path) -> None:
    _write(tmp_path, "comments_only.py", "# just a note\n")
    _write(tmp_path, "doc_only.py", '"""docs"""\n')
    _write(tmp_path, "real.py", "SECRET = 'x'\n")

    selection = select_python_files(tmp_path, max_files=10)

    assert selection.relative_selected() == ["real.py"]
    assert selection.skipped_trivial_count == 2


def test_syntax_invalid_file_stays_eligible(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "def broken(\n")
    _write(tmp_path, "ok.py", "x = 1\n")

    selection = select_python_files(tmp_path, max_files=10)

    assert selection.relative_selected() == ["broken.py", "ok.py"]
    assert selection.skipped_trivial_count == 0


def test_include_and_exclude_filters_before_cap(tmp_path: Path) -> None:
    _write(tmp_path, "architecture/a.py", "x = 1\n")
    _write(tmp_path, "architecture/b.py", "y = 2\n")
    _write(tmp_path, "tests/nested/t.py", "z = 3\n")
    _write(tmp_path, "other.py", "w = 4\n")

    selection = select_python_files(
        tmp_path,
        max_files=1,
        include=["architecture/*.py", "tests/**"],
        exclude=["tests/**", "architecture/b.py"],
    )

    assert selection.relative_selected() == ["architecture/a.py"]
    assert selection.skipped_include_count == 1  # other.py
    assert selection.skipped_exclude_count == 2  # tests/nested/t.py + architecture/b.py
    assert selection.eligible_count == 1
    assert selection.capped is False


def test_include_accepts_click_windows_expanded_paths(tmp_path: Path) -> None:
    """Click on Windows may turn ``**/a.py`` into a cwd-relative real path."""
    target = _write(tmp_path, "architecture/a.py", "x = 1\n")
    _write(tmp_path, "architecture/b.py", "y = 2\n")

    selection = select_python_files(
        tmp_path,
        max_files=10,
        # Simulate Click's Windows argv expansion (absolute or cwd-relative).
        include=[str(target), str(target).replace("/", "\\")],
    )

    assert selection.relative_selected() == ["architecture/a.py"]
    assert selection.skipped_include_count == 1


def test_empty_directory_is_clear_non_crash(tmp_path: Path) -> None:
    selection = select_python_files(tmp_path, max_files=4)
    assert selection.selected == ()
    assert selection.discovered_count == 0
    assert selection.eligible_count == 0


def test_no_eligible_after_filters(tmp_path: Path) -> None:
    _write(tmp_path, "__init__.py", "")
    _write(tmp_path, "emptyish.py", "# noop\n")
    selection = select_python_files(tmp_path, max_files=4)
    assert selection.selected == ()
    assert selection.skipped_init_count == 1
    assert selection.skipped_trivial_count == 1


def test_symlink_directories_are_not_traversed(tmp_path: Path) -> None:
    real = tmp_path / "real_pkg"
    real.mkdir()
    (real / "hidden.py").write_text("x = 1\n", encoding="utf-8")
    link = tmp_path / "linked_pkg"
    try:
        os.symlink(real, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted on this host")

    _write(tmp_path, "visible.py", "y = 2\n")
    selection = select_python_files(tmp_path, max_files=10)
    assert selection.relative_selected() == ["visible.py"]
    assert "linked_pkg/hidden.py" not in selection.relative_selected()


def test_review_python_files_sequentially_aggregates_per_file_counts(
    tmp_path: Path,
) -> None:
    paths = [tmp_path / "a.py", tmp_path / "b.py"]
    calls: list[str] = []

    def fake_supervise(path: str) -> dict:
        calls.append(Path(path).name)
        accepted, needs = (2, 1) if path.endswith("a.py") else (1, 3)
        return {
            "path": path,
            "summary": {
                "accepted_count": accepted,
                "needs_review_count": needs,
            },
        }

    aggregate = review_python_files(paths, workers=1, review_one=fake_supervise)

    assert calls == ["a.py", "b.py"]
    assert aggregate["accepted_count"] == 3
    assert aggregate["needs_review_count"] == 4
    assert [
        (Path(item["path"]).name, item["accepted_count"], item["needs_review_count"])
        for item in aggregate["files"]
    ] == [("a.py", 2, 1), ("b.py", 1, 3)]


def test_parallel_reviews_keep_deterministic_results_and_sqlite_writes(
    tmp_path: Path,
) -> None:
    paths = [tmp_path / name for name in ("a.py", "b.py", "c.py", "d.py")]
    db_path = tmp_path / "parallel.db"
    expected = {
        "a.py": (1, 0),
        "b.py": (2, 1),
        "c.py": (0, 3),
        "d.py": (4, 2),
    }

    def fake_supervise(path: str) -> dict:
        name = Path(path).name
        # Force completion order to differ from selected/input order.
        time.sleep({"a.py": 0.04, "b.py": 0.03, "c.py": 0.02, "d.py": 0.01}[name])
        accepted, needs = expected[name]
        review = ReviewResult(
            findings=[],
            file_path=path,
            timestamp=datetime.now(timezone.utc),
            worker_name="security",
        )
        save_review(
            review,
            GateResult(accepted=[], needs_review=[], threshold=80),
            db_path=db_path,
            job_id=f"job-{name}",
        )
        save_audit_event(
            f"job-{name}",
            "review_complete",
            worker_name="supervisor",
            detail={"accepted_count": accepted, "needs_review_count": needs},
            db_path=db_path,
        )
        return {
            "path": path,
            "summary": {
                "accepted_count": accepted,
                "needs_review_count": needs,
            },
        }

    aggregate = review_python_files(paths, workers=4, review_one=fake_supervise)

    assert [Path(item["path"]).name for item in aggregate["files"]] == [
        "a.py",
        "b.py",
        "c.py",
        "d.py",
    ]
    assert aggregate["accepted_count"] == 7
    assert aggregate["needs_review_count"] == 6
    assert len(list_reviews(limit=10, db_path=db_path)) == 4
    for path in paths:
        events = list_audit_events(f"job-{path.name}", db_path=db_path)
        assert len(events) == 1
        assert events[0].stage == "review_complete"


def test_parallel_memory_seed_initializes_once(
    tmp_path: Path, monkeypatch
) -> None:
    """Concurrent supervise_review calls must not race first-use Chroma seeding."""
    from app import memory

    lessons_path = tmp_path / "lessons.json"
    lessons_path.write_text(
        json.dumps(
            [
                {
                    "id": "lesson-1",
                    "type": "idor",
                    "pattern": "missing owner check",
                    "bad_example": "get(id)",
                    "fix": "check owner",
                    "source": "test",
                }
            ]
        ),
        encoding="utf-8",
    )

    class RacingCollection:
        def __init__(self) -> None:
            self.ids: list[str] = []
            self.second_counter_entered = threading.Event()
            self.count_calls = 0
            self.add_calls = 0
            self.lock = threading.Lock()

        def count(self) -> int:
            with self.lock:
                self.count_calls += 1
                call = self.count_calls
            if call == 1:
                self.second_counter_entered.wait(timeout=0.1)
            else:
                self.second_counter_entered.set()
            return len(self.ids)

        def add(self, *, ids, documents, metadatas) -> None:
            del documents, metadatas
            with self.lock:
                self.add_calls += 1
                self.ids.extend(ids)

    collection = RacingCollection()
    monkeypatch.setattr(memory, "init_memory", lambda persist_directory=None: collection)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: memory.seed_memory(lessons_path=lessons_path),
                range(2),
            )
        )

    assert sorted(results) == [0, 1]
    assert collection.add_calls == 1


def test_single_file_cli_still_calls_supervise_review_once(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from app import cli

    target = tmp_path / "single.py"
    target.write_text("value = 1\n", encoding="utf-8")
    calls: list[str] = []

    def fake_supervise(path: str, *, on_stage=None) -> dict:
        del on_stage
        calls.append(path)
        security = {
            "path": path,
            "accepted": [],
            "needs_review": [],
            "accepted_count": 0,
            "needs_review_count": 0,
        }
        return {
            "path": path,
            "security": security,
            "architecture": None,
            "summary": {
                "workers_run": ["security"],
                "accepted_count": 0,
                "needs_review_count": 0,
                "security_accepted": 0,
                "security_needs_review": 0,
                "architecture_skipped": True,
            },
        }

    monkeypatch.setattr(cli, "supervise_review", fake_supervise)

    result = CliRunner().invoke(cli.app, ["review", str(target)])

    assert result.exit_code == 0, result.output
    assert calls == [str(target.resolve())]
    assert "Starting review of" in result.output


def test_directory_cli_prints_selection_and_skips_init(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from app import cli

    _write(tmp_path, "__init__.py", "")
    _write(tmp_path, "a_mod.py", "x = 1\n")
    _write(tmp_path, "b_mod.py", "y = 2\n")
    reviewed: list[str] = []

    def fake_supervise(path: str) -> dict:
        reviewed.append(Path(path).name)
        return {
            "path": path,
            "summary": {"accepted_count": 0, "needs_review_count": 0},
        }

    monkeypatch.setattr(cli, "supervise_review", fake_supervise)
    result = CliRunner().invoke(
        cli.app,
        ["review", str(tmp_path), "--max-files", "4", "--workers", "1"],
    )

    assert result.exit_code == 0, result.output
    assert "skipped_init=1" in result.output
    assert "a_mod.py" in result.output
    assert "b_mod.py" in result.output
    assert "__init__.py" not in reviewed
    assert reviewed == ["a_mod.py", "b_mod.py"]
    assert "Quiet mode" in result.output
    assert "running" in result.output
    assert "done" in result.output
    assert "accepted=0" in result.output


def test_directory_cli_verbose_flag_is_accepted(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from app import cli

    _write(tmp_path, "only.py", "x = 1\n")

    def fake_supervise(path: str) -> dict:
        return {
            "path": path,
            "summary": {"accepted_count": 1, "needs_review_count": 0},
        }

    monkeypatch.setattr(cli, "supervise_review", fake_supervise)
    result = CliRunner().invoke(
        cli.app,
        [
            "review",
            str(tmp_path),
            "--max-files",
            "2",
            "--workers",
            "2",
            "--verbose",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Quiet mode" not in result.output
    assert "done" in result.output
    assert "accepted=1" in result.output


def test_review_python_files_invokes_start_and_done_callbacks(
    tmp_path: Path,
) -> None:
    paths = [tmp_path / "a.py", tmp_path / "b.py"]
    started: list[str] = []
    done: list[str] = []

    def fake_supervise(path: str) -> dict:
        return {
            "path": path,
            "summary": {"accepted_count": 0, "needs_review_count": 0},
        }

    aggregate = review_python_files(
        paths,
        workers=2,
        review_one=fake_supervise,
        on_file_start=lambda path, index, total: started.append(
            f"{index}/{total}:{path.name}"
        ),
        on_file_done=lambda path, report: done.append(path.name),
    )
    assert sorted(started) == ["1/2:a.py", "2/2:b.py"]
    assert sorted(done) == ["a.py", "b.py"]
    assert aggregate["file_count"] == 2
