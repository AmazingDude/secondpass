"""Unit tests for the detection-quality benchmark evaluator."""

from __future__ import annotations

from app.benchmark import PredictedFinding, evaluate, load_ground_truth


def _gt() -> dict:
    return {
        "version": "1",
        "fixtures": {
            "benchmark/fixtures/notes_idor.py": [
                {"finding_type": "missing_ownership_check", "symbol": "get_note"},
            ],
            "benchmark/fixtures/ops_shell.py": [
                {"finding_type": "command_injection", "symbol": "run_backup"},
            ],
            "benchmark/fixtures/clean_ownership.py": [],
        },
    }


def test_perfect_predictions_score_one() -> None:
    predictions = [
        PredictedFinding(
            file_path="benchmark/fixtures/notes_idor.py",
            finding_type="missing_ownership_check",
        ),
        {
            "file_path": "benchmark/fixtures/ops_shell.py",
            "finding_type": "command_injection",
        },
    ]
    report = evaluate(predictions, _gt())

    assert report.true_positives == 2
    assert report.false_positives == 0
    assert report.false_negatives == 0
    assert report.precision == 1.0
    assert report.recall == 1.0


def test_false_positive_on_clean_file_lowers_precision() -> None:
    predictions = [
        PredictedFinding(
            file_path="benchmark/fixtures/notes_idor.py",
            finding_type="missing_ownership_check",
        ),
        PredictedFinding(
            file_path="benchmark/fixtures/ops_shell.py",
            finding_type="command_injection",
        ),
        PredictedFinding(
            file_path="benchmark/fixtures/clean_ownership.py",
            finding_type="missing_ownership_check",
        ),
    ]
    report = evaluate(predictions, _gt())

    assert report.true_positives == 2
    assert report.false_positives == 1
    assert report.false_negatives == 0
    assert report.precision == 2 / 3
    assert report.recall == 1.0


def test_missed_finding_lowers_recall() -> None:
    predictions = [
        PredictedFinding(
            file_path="benchmark/fixtures/notes_idor.py",
            finding_type="missing_ownership_check",
        ),
    ]
    report = evaluate(predictions, _gt())

    assert report.true_positives == 1
    assert report.false_positives == 0
    assert report.false_negatives == 1
    assert report.precision == 1.0
    assert report.recall == 0.5


def test_wrong_finding_type_is_not_a_hit() -> None:
    predictions = [
        PredictedFinding(
            file_path="benchmark/fixtures/notes_idor.py",
            finding_type="command_injection",
        ),
    ]
    report = evaluate(predictions, _gt())

    assert report.true_positives == 0
    assert report.false_positives == 1
    assert report.false_negatives == 2


def test_empty_predictions_against_bugs_is_zero_recall() -> None:
    report = evaluate([], _gt())

    assert report.true_positives == 0
    assert report.false_positives == 0
    assert report.false_negatives == 2
    assert report.precision == 1.0
    assert report.recall == 0.0


def test_load_ground_truth_matches_repo_fixtures() -> None:
    ground_truth = load_ground_truth()
    fixtures = ground_truth["fixtures"]

    assert fixtures["benchmark/fixtures/clean_ownership.py"] == []
    assert fixtures["benchmark/fixtures/notes_idor.py"][0]["finding_type"] == (
        "missing_ownership_check"
    )
    assert fixtures["benchmark/fixtures/ops_shell.py"][0]["finding_type"] == (
        "command_injection"
    )
