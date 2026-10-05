"""Legacy detection scoring and coverage-aware benchmark reporting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Repo-relative default; override via load_ground_truth(path=...).
DEFAULT_GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parent.parent / "benchmark" / "ground_truth.json"
)


class PredictedFinding(BaseModel):
    """Prediction record for v1 matching (file_path + finding_type).

    ``confidence`` and ``detection_method`` are optional and purely
    informational — scoring/matching in ``evaluate`` below still keys only on
    (file_path, finding_type). They exist so confidence-bucket precision
    analysis (app/benchmark_confidence_buckets.py) can read per-finding
    confidence straight out of the persisted results JSON instead of only
    aggregate precision/recall.
    """

    file_path: str
    finding_type: str
    confidence: int | None = Field(default=None, ge=0, le=100)
    detection_method: str | None = None


class ScoreReport(BaseModel):
    """Precision/recall summary for a set of predictions vs ground truth."""

    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)

    def metrics(self) -> dict[str, dict[str, int | float | None]]:
        """Present explicit denominators; retain legacy serialized numeric scores."""
        return {
            "precision": _metric(
                self.true_positives, self.true_positives + self.false_positives,
            ),
            "recall": _metric(
                self.true_positives, self.true_positives + self.false_negatives,
            ),
        }


def _metric(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _expected_keys(ground_truth: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    fixtures = ground_truth.get("fixtures", {})
    for file_path, issues in fixtures.items():
        norm = _normalize_path(file_path)
        for issue in issues:
            keys.add((norm, issue["finding_type"]))
    return keys


def _predicted_keys(predictions: list[PredictedFinding]) -> set[tuple[str, str]]:
    return {
        (_normalize_path(item.file_path), item.finding_type) for item in predictions
    }


def load_ground_truth(path: Path | str | None = None) -> dict[str, Any]:
    """Load the versioned ground-truth JSON."""
    target = Path(path) if path is not None else DEFAULT_GROUND_TRUTH_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def evaluate(
    predictions: list[PredictedFinding] | list[dict[str, Any]],
    ground_truth: dict[str, Any],
) -> ScoreReport:
    """Score predictions against ground truth.

    Matching rule (v1): same normalized file_path + same finding_type = hit.
    Extra fields on predictions are ignored.
    """
    predicted = [
        item
        if isinstance(item, PredictedFinding)
        else PredictedFinding.model_validate(item)
        for item in predictions
    ]

    expected = _expected_keys(ground_truth)
    actual = _predicted_keys(predicted)

    true_positives = len(expected & actual)
    false_positives = len(actual - expected)
    false_negatives = len(expected - actual)

    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives)
        else 1.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives)
        else 1.0
    )

    return ScoreReport(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
    )


def fixture_evaluation_status(report: dict[str, Any]) -> str:
    """Require explicit coverage evidence; failure signals take precedence."""
    coverage = (report.get("review_result") or {}).get("coverage_status")
    if (
        report.get("inconclusive")
        or report.get("static_scan_error")
        or coverage == "inconclusive"
    ):
        return "inconclusive"
    return "completed" if coverage == "ok" else "unknown"


def summarize_evaluation(
    predictions: list[PredictedFinding],
    ground_truth: dict[str, Any],
    per_file: list[dict[str, Any]],
) -> dict[str, Any]:
    """Coverage-aware reporting, without changing legacy file/type matching.

    Conditional metrics use completed fixtures only. Detection yield retains
    valid findings even with incomplete/unknown coverage, against all requested expectations.
    Missing coverage is unknown, not evidence of a successful analysis.
    """
    fixtures = ground_truth.get("fixtures") or {}
    rows = {_normalize_path(row["file_path"]): row for row in per_file}
    statuses: dict[str, str] = {}
    for path in fixtures:
        row = rows.get(_normalize_path(path), {})
        statuses[_normalize_path(path)] = (
            "errored" if row.get("error") else row.get("evaluation_status", "unknown")
        )
    completed = {path for path, status in statuses.items() if status == "completed"}
    observed = {
        path for path, status in statuses.items()
        if status in {"completed", "inconclusive", "unknown"}
    }
    conditional = evaluate(
        [item for item in predictions if _normalize_path(item.file_path) in completed],
        {"fixtures": {
            path: issues for path, issues in fixtures.items()
            if _normalize_path(path) in completed
        }},
    )
    expected = _expected_keys(ground_truth)
    actual = _predicted_keys([
        item for item in predictions if _normalize_path(item.file_path) in observed
    ])
    counts = {"requested": len(fixtures)}
    counts.update({
        status: sum(value == status for value in statuses.values())
        for status in ("completed", "inconclusive", "errored", "unknown")
    })
    if not fixtures:
        status = "empty"
    elif not completed:
        status = "invalid"
    elif len(completed) == len(fixtures):
        status = "complete"
    else:
        status = "incomplete"
    return {
        "version": "coverage-v1",
        "matching": "legacy-v1-file-type",
        "status": status,
        "fixtures": counts,
        "completion": _metric(len(completed), len(fixtures)),
        "conditional": conditional.metrics(),
        "detection_yield": _metric(len(expected & actual), len(expected)),
    }


def format_evaluation(summary: dict[str, Any]) -> str:
    """CLI headline with validity, coverage and visible metric denominators."""
    def display(metric: dict[str, Any]) -> str:
        value = "n/a" if metric["value"] is None else f'{metric["value"]:.3f}'
        return f'{value} ({metric["numerator"]}/{metric["denominator"]})'

    counts = summary["fixtures"]
    metrics = summary["conditional"]
    return (
        f'Evaluation: {summary["status"]} [coverage-v1; legacy-v1 file/type matching]\n'
        f'Completed fixtures: {counts["completed"]}/{counts["requested"]}; '
        f'inconclusive={counts["inconclusive"]} errored={counts["errored"]} '
        f'unknown={counts["unknown"]}\n'
        f'Conditional precision={display(metrics["precision"])}; '
        f'recall={display(metrics["recall"])}\n'
        f'Detection yield={display(summary["detection_yield"])}'
    )


def evaluation_exit_code(summary: dict[str, Any]) -> int:
    """Fail a benchmark command unless every requested fixture completed."""
    return 0 if summary["status"] == "complete" else 1
