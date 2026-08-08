"""Offline tests for confidence-bucket precision analysis (no live LLM)."""

from __future__ import annotations

import json
from pathlib import Path

from app.benchmark_confidence_buckets import (
    SMALL_SAMPLE_THRESHOLD,
    analyze_result_file,
    bucket_table,
    confidence_bucket,
    confidence_records_with_hits,
    render_markdown_table,
)


def test_confidence_bucket_edges() -> None:
    assert confidence_bucket(0) == "<70"
    assert confidence_bucket(69) == "<70"
    assert confidence_bucket(70) == "70-79"
    assert confidence_bucket(79) == "70-79"
    assert confidence_bucket(80) == "80-89"
    assert confidence_bucket(89) == "80-89"
    assert confidence_bucket(90) == "90-100"
    assert confidence_bucket(100) == "90-100"


def _write_results(tmp_path: Path) -> tuple[Path, Path]:
    ground_truth_path = tmp_path / "gt.json"
    ground_truth_path.write_text(
        json.dumps(
            {
                "fixtures": {
                    "benchmark/fixtures/notes_idor.py": [
                        {"finding_type": "missing_ownership_check"}
                    ],
                    "benchmark/fixtures/clean_ownership.py": [],
                }
            }
        ),
        encoding="utf-8",
    )

    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "ground_truth": str(ground_truth_path),
                "per_file": [
                    {
                        "file_path": "benchmark/fixtures/notes_idor.py",
                        "confidence_records": [
                            {
                                "finding_type": "missing_ownership_check",
                                "raw_finding_type": "missing_ownership_check",
                                "confidence": 95,
                                "detection_method": "llm_reasoning",
                                "verdict": "accepted",
                            },
                            {
                                "finding_type": "missing_ownership_check",
                                "raw_finding_type": "missing_ownership_check",
                                "confidence": 60,
                                "detection_method": "llm_reasoning",
                                "verdict": "needs_review",
                            },
                        ],
                    },
                    {
                        "file_path": "benchmark/fixtures/clean_ownership.py",
                        "confidence_records": [
                            {
                                "finding_type": "missing_ownership_check",
                                "raw_finding_type": "missing_ownership_check",
                                "confidence": 82,
                                "detection_method": "llm_reasoning",
                                "verdict": "accepted",
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return results_path, ground_truth_path


def test_confidence_records_with_hits_matches_ground_truth(tmp_path: Path) -> None:
    results_path, _ = _write_results(tmp_path)
    payload = json.loads(results_path.read_text(encoding="utf-8"))

    rows = confidence_records_with_hits(payload)

    by_confidence = {row["confidence"]: row for row in rows}
    assert by_confidence[95]["is_hit"] is True  # notes_idor expects this type
    assert by_confidence[60]["is_hit"] is True  # same fixture, still a hit
    assert by_confidence[82]["is_hit"] is False  # clean_ownership expects nothing


def test_bucket_table_precision_and_small_sample_flag(tmp_path: Path) -> None:
    results_path, _ = _write_results(tmp_path)
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    rows = confidence_records_with_hits(payload)

    table = bucket_table(rows)

    assert table["90-100"]["n"] == 1
    assert table["90-100"]["hits"] == 1
    assert table["90-100"]["precision"] == 1.0
    assert table["90-100"]["small_sample"] is True  # n=1 < threshold

    assert table["80-89"]["n"] == 1
    assert table["80-89"]["hits"] == 0
    assert table["80-89"]["precision"] == 0.0

    assert table["70-79"]["n"] == 0
    assert table["70-79"]["precision"] is None
    assert table["70-79"]["small_sample"] is False  # empty bucket is not "small", it's empty

    assert table["<70"]["n"] == 1
    assert table["<70"]["hits"] == 1


def test_analyze_result_file_labels_suite_and_provider(tmp_path: Path) -> None:
    results_path, _ = _write_results(tmp_path)

    analysis = analyze_result_file(results_path, suite_label="security")

    assert analysis["suite"] == "security"
    assert analysis["provider"] == "openai"
    assert analysis["model"] == "gpt-4o-mini"
    assert len(analysis["rows"]) == 3


def test_render_markdown_table_labels_every_row(tmp_path: Path) -> None:
    results_path, _ = _write_results(tmp_path)
    analysis = analyze_result_file(results_path, suite_label="security")

    markdown = render_markdown_table([analysis])

    assert "| security | openai |" in markdown
    assert "too small to read as a rate" in markdown  # n=1 buckets flagged inline
    assert SMALL_SAMPLE_THRESHOLD == 3
