"""Detection-quality benchmark evaluator (precision / recall stub)."""

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
    """Minimal prediction record for v1 matching (file_path + finding_type)."""

    file_path: str
    finding_type: str


class ScoreReport(BaseModel):
    """Precision/recall summary for a set of predictions vs ground truth."""

    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)


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
