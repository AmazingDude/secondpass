"""Offline tests for benchmark runner helpers (no live LLM / Semgrep)."""

from __future__ import annotations

from pathlib import Path

from app.benchmark import evaluate, load_ground_truth
from app.benchmark_run import (
    fixture_relative_path,
    normalize_benchmark_finding_type,
    predictions_from_report_items,
    run_benchmark,
)


def test_normalize_maps_semgrep_shell_rule_to_command_injection() -> None:
    assert (
        normalize_benchmark_finding_type(
            "python.lang.security.audit.subprocess-shell-true.subprocess-shell-true"
        )
        == "command_injection"
    )


def test_normalize_keeps_logic_label() -> None:
    assert (
        normalize_benchmark_finding_type("missing_ownership_check")
        == "missing_ownership_check"
    )


def test_predictions_from_accepted_items_dedupe_and_remap(
    tmp_path: Path,
) -> None:
    items = [
        {
            "structured_finding": {
                "finding_type": (
                    "python.lang.security.audit.subprocess-shell-true"
                    ".subprocess-shell-true"
                )
            }
        },
        {
            "structured_finding": {
                "finding_type": (
                    "python.lang.security.audit.subprocess-shell-true"
                    ".subprocess-shell-true"
                )
            }
        },
    ]
    predictions = predictions_from_report_items(
        items, fixture_path="benchmark/fixtures/ops_shell.py"
    )
    assert len(predictions) == 1
    assert predictions[0].finding_type == "command_injection"


def test_fixture_relative_path_from_absolute(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = repo / "benchmark" / "fixtures" / "ops_shell.py"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    assert (
        fixture_relative_path(target, repo_root=repo)
        == "benchmark/fixtures/ops_shell.py"
    )


def test_run_benchmark_offline_writes_results(tmp_path: Path, monkeypatch) -> None:
    results_dir = tmp_path / "results"

    def fake_offline(fixture_abs: Path) -> dict:
        name = fixture_abs.name
        if name == "ops_shell.py":
            return {
                "path": str(fixture_abs),
                "accepted": [
                    {
                        "structured_finding": {
                            "finding_type": (
                                "python.lang.security.audit.subprocess-shell-true"
                                ".subprocess-shell-true"
                            )
                        }
                    }
                ],
                "needs_review": [],
                "static_scan_error": None,
                "used_logic_fallback": False,
                "message": None,
            }
        if name == "notes_idor.py":
            return {
                "path": str(fixture_abs),
                "accepted": [],
                "needs_review": [],
                "static_scan_error": None,
                "used_logic_fallback": False,
                "message": "No static findings (offline).",
            }
        return {
            "path": str(fixture_abs),
            "accepted": [],
            "needs_review": [],
            "static_scan_error": None,
            "used_logic_fallback": False,
            "message": "clean",
        }

    monkeypatch.setattr("app.benchmark_run._offline_review", fake_offline)

    payload = run_benchmark(
        offline=True,
        results_dir=results_dir,
        label="unit",
    )

    score = payload["score"]
    # offline Semgrep hits ops_shell only → 1 TP, 1 FN (notes_idor), 0 FP
    assert score["true_positives"] == 1
    assert score["false_positives"] == 0
    assert score["false_negatives"] == 1
    assert score["precision"] == 1.0
    assert score["recall"] == 0.5
    assert payload["scored_bucket"] == "accepted"
    assert payload["mode"] == "offline_semgrep"
    written = list(results_dir.glob("unit_*.json"))
    assert len(written) == 1


def test_mapped_predictions_score_against_real_ground_truth() -> None:
    predictions = [
        {
            "file_path": "benchmark/fixtures/ops_shell.py",
            "finding_type": normalize_benchmark_finding_type(
                "python.lang.security.audit.subprocess-shell-true"
            ),
        },
        {
            "file_path": "benchmark/fixtures/notes_idor.py",
            "finding_type": "missing_ownership_check",
        },
    ]
    report = evaluate(predictions, load_ground_truth())
    assert report.true_positives == 2
    assert report.false_positives == 0
    assert report.false_negatives == 0
