"""CLI multi-file selection and deterministic aggregation tests."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app.confidence_gate import GateResult
from app.multifile import review_python_files, select_python_files
from app.persistence import (
    list_audit_events,
    list_reviews,
    save_audit_event,
    save_review,
)
from app.schema import ReviewResult


def test_select_python_files_sorts_before_cap_and_skips_junk(tmp_path: Path) -> None:
    for relative in (
        "z_last.py",
        "pkg/c_mid.py",
        "a_first.py",
        "pkg/b_second.py",
        "README.md",
        ".venv/ignored.py",
        "node_modules/ignored.py",
        ".git/ignored.py",
        "pkg/__pycache__/ignored.py",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n", encoding="utf-8")

    selected = select_python_files(tmp_path, max_files=3)

    assert [path.relative_to(tmp_path).as_posix() for path in selected] == [
        "a_first.py",
        "pkg/b_second.py",
        "pkg/c_mid.py",
    ]


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
