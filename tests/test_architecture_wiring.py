"""Offline tests for Architecture Worker wiring into the review pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.agent import build_architecture_review_output, review_architecture, review_path
from app.schema import Finding, ReviewResult


def test_review_architecture_skips_directories(tmp_path: Path, monkeypatch) -> None:
    def _fail(*args, **kwargs):
        raise AssertionError("run_architecture_worker should not run for directories")

    monkeypatch.setattr("app.agent.run_architecture_worker", _fail)

    report = review_architecture(str(tmp_path))

    assert report["skipped"] is True
    assert report["finding_count"] == 0
    assert report["accepted"] == []
    assert report["needs_review"] == []


def test_review_architecture_clean_shape(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "service.py"
    target.write_text("def do_thing():\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.agent.run_architecture_worker",
        lambda *args, **kwargs: {
            "has_issues": False,
            "summary": "No architecture issues found.",
            "structured_findings": [],
            "context_files": [],
            "failures": 0,
        },
    )

    report = review_architecture(str(target))

    assert report["worker_name"] == "architecture"
    assert report["finding_count"] == 0
    assert report["no_issues"] is True
    assert report["accepted"] == []
    assert report["needs_review"] == []
    assert report["review_result"]["worker_name"] == "architecture"


def test_review_architecture_splits_by_confidence(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "service.py"
    target.write_text("def do_thing():\n    pass\n", encoding="utf-8")

    high = Finding(
        finding_type="layering_violation",
        evidence="service.py:1\nimports the UI layer directly",
        confidence=90,
        suggested_fix="Invert the dependency",
        detection_method="llm_reasoning",
    )
    low = Finding(
        finding_type="duplicated_logic",
        evidence="service.py:20\nsame retry loop as worker.py",
        confidence=60,
        suggested_fix="Extract a shared retry helper",
        detection_method="llm_reasoning",
    )
    monkeypatch.setattr(
        "app.agent.run_architecture_worker",
        lambda *args, **kwargs: {
            "has_issues": True,
            "summary": "2 issues",
            "structured_findings": [high, low],
            "context_files": [{"path": "service_helper.py", "relation": "same_package"}],
            "failures": 0,
        },
    )

    report = review_architecture(str(target))

    assert report["finding_count"] == 2
    assert report["accepted_count"] == 1
    assert report["needs_review_count"] == 1
    assert (
        report["accepted"][0]["structured_finding"]["finding_type"]
        == "layering_violation"
    )
    assert (
        report["needs_review"][0]["structured_finding"]["finding_type"]
        == "duplicated_logic"
    )
    assert report["context_files"] == [
        {"path": "service_helper.py", "relation": "same_package"}
    ]


def test_build_architecture_review_output_worker_name_and_gate() -> None:
    finding = Finding(
        finding_type="naming_convention",
        evidence="module.py:5\nfunction uses camelCase in a snake_case codebase",
        confidence=95,
        suggested_fix="Rename to snake_case",
        detection_method="llm_reasoning",
    )
    result, gate = build_architecture_review_output(
        "module.py",
        [finding],
        timestamp=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )

    assert isinstance(result, ReviewResult)
    assert result.worker_name == "architecture"
    assert gate.accepted == [finding]
    assert gate.needs_review == []


def test_review_path_combines_security_and_architecture(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "service.py"
    target.write_text("import subprocess\n", encoding="utf-8")

    monkeypatch.setattr("app.agent.seed_memory", lambda: 0)
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    monkeypatch.setattr(
        "app.agent.assess_logic_review",
        lambda path, **kwargs: {
            "has_issues": False,
            "summary": "No security issues found.",
            "findings": [],
            "structured_findings": [],
            "failures": 0,
        },
    )
    monkeypatch.setattr(
        "app.agent.run_architecture_worker",
        lambda *args, **kwargs: {
            "has_issues": False,
            "summary": "No architecture issues found.",
            "structured_findings": [],
            "context_files": [],
            "failures": 0,
        },
    )

    report = review_path(str(target))

    assert {"path", "security", "architecture", "summary"}.issubset(report.keys())
    assert report["security"]["review_result"]["worker_name"] == "security"
    assert report["architecture"]["worker_name"] == "architecture"
    assert report["summary"]["workers_run"] == ["security", "architecture"]
    assert report["summary"]["no_issues"] is True
    assert report["summary"]["accepted_count"] == 0
    assert report["summary"]["needs_review_count"] == 0
