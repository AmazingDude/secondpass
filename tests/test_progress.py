"""Unit tests for CLI review progress helpers."""

from __future__ import annotations

from app.progress import STAGE_LABELS, stage_label


def test_stage_label_known_stages() -> None:
    assert "Semgrep" in stage_label("scanning")
    assert "logic review" in stage_label("logic_review").lower()
    assert "workers" in stage_label("workers").lower()
    assert "report" in stage_label("building_report").lower()
    for stage in STAGE_LABELS:
        assert stage_label(stage) == STAGE_LABELS[stage]


def test_stage_label_fallback_for_unknown() -> None:
    label = stage_label("custom_stage")
    assert "Custom stage" in label
    assert label.startswith("[blue]")
