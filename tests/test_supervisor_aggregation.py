"""Offline tests for Supervisor path-level aggregation."""

from __future__ import annotations

from pathlib import Path

from app.supervisor import aggregate_worker_reports, supervise_review


def _worker_report(
    *,
    path: str,
    worker_name: str,
    accepted: int,
    needs_review: int,
    skipped: bool = False,
    tool_call_failures: int = 0,
) -> dict:
    return {
        "path": path,
        "worker_name": worker_name,
        "finding_count": accepted + needs_review,
        "accepted_count": accepted,
        "needs_review_count": needs_review,
        "accepted": [{"id": i} for i in range(accepted)],
        "needs_review": [{"id": i} for i in range(needs_review)],
        "gate_threshold": 80,
        "skipped": skipped,
        "tool_call_failures": tool_call_failures,
        "no_issues": accepted == 0 and needs_review == 0,
    }


def test_aggregate_merges_counts_and_summary() -> None:
    security = _worker_report(
        path="/tmp/a.py",
        worker_name="security",
        accepted=2,
        needs_review=1,
        tool_call_failures=1,
    )
    architecture = _worker_report(
        path="/tmp/a.py",
        worker_name="architecture",
        accepted=1,
        needs_review=2,
        tool_call_failures=3,
    )

    combined = aggregate_worker_reports(security, architecture)

    assert combined["path"] == "/tmp/a.py"
    assert combined["security"] is security or combined["security"]["accepted_count"] == 2
    assert combined["architecture"]["accepted_count"] == 1
    summary = combined["summary"]
    assert summary["security_accepted"] == 2
    assert summary["security_needs_review"] == 1
    assert summary["architecture_accepted"] == 1
    assert summary["architecture_needs_review"] == 2
    assert summary["accepted_count"] == 3
    assert summary["needs_review_count"] == 3
    assert summary["finding_count"] == 6
    assert summary["no_issues"] is False
    assert summary["architecture_skipped"] is False
    assert summary["workers_run"] == ["security", "architecture"]
    assert summary["tool_call_failures"] == 4
    assert summary["gate_threshold"] == 80


def test_aggregate_security_only_marks_architecture_skipped() -> None:
    security = _worker_report(
        path="/tmp/a.py",
        worker_name="security",
        accepted=0,
        needs_review=0,
    )
    combined = aggregate_worker_reports(security, None)

    assert combined["architecture"] is None
    assert combined["summary"]["workers_run"] == ["security"]
    assert combined["summary"]["architecture_skipped"] is True
    assert combined["summary"]["no_issues"] is True
    assert combined["summary"]["accepted_count"] == 0


def test_aggregate_respects_architecture_skipped_flag() -> None:
    security = _worker_report(
        path="/tmp/pkg",
        worker_name="security",
        accepted=1,
        needs_review=0,
    )
    architecture = _worker_report(
        path="/tmp/pkg",
        worker_name="architecture",
        accepted=0,
        needs_review=0,
        skipped=True,
    )
    combined = aggregate_worker_reports(security, architecture)
    assert combined["summary"]["architecture_skipped"] is True
    assert combined["summary"]["accepted_count"] == 1
    assert combined["summary"]["workers_run"] == ["security", "architecture"]


def test_supervise_review_aggregates_stubbed_workers(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "notes.py"
    target.write_text("def get_note():\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.agent.review_code",
        lambda path, max_iterations=6, on_stage=None: _worker_report(
            path=path,
            worker_name="security",
            accepted=1,
            needs_review=0,
        ),
    )
    monkeypatch.setattr(
        "app.agent.review_architecture",
        lambda path, project_root=None, on_stage=None: _worker_report(
            path=path,
            worker_name="architecture",
            accepted=0,
            needs_review=1,
        ),
    )

    report = supervise_review(str(target))

    assert set(report.keys()) == {"path", "security", "architecture", "summary"}
    assert report["security"]["worker_name"] == "security"
    assert report["architecture"]["worker_name"] == "architecture"
    assert report["summary"]["accepted_count"] == 1
    assert report["summary"]["needs_review_count"] == 1
    assert report["summary"]["finding_count"] == 2
    assert report["summary"]["workers_run"] == ["security", "architecture"]


def test_supervise_review_can_skip_architecture(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "notes.py"
    target.write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.agent.review_code",
        lambda path, max_iterations=6, on_stage=None: _worker_report(
            path=path,
            worker_name="security",
            accepted=0,
            needs_review=0,
        ),
    )

    def _fail_arch(*args, **kwargs):
        raise AssertionError("architecture should not run")

    monkeypatch.setattr("app.agent.review_architecture", _fail_arch)

    report = supervise_review(str(target), run_architecture=False)
    assert report["architecture"] is None
    assert report["summary"]["workers_run"] == ["security"]
    assert report["summary"]["architecture_skipped"] is True
