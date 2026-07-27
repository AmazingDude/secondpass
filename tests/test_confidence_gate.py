"""Unit tests for the confidence gate helper."""

from __future__ import annotations

from datetime import datetime, timezone

from app.confidence_gate import DEFAULT_THRESHOLD, apply_confidence_gate
from app.schema import Finding, ReviewResult


def _finding(*, confidence: int, finding_type: str = "test") -> Finding:
    return Finding(
        finding_type=finding_type,
        evidence=f"evidence for {finding_type}",
        confidence=confidence,
        suggested_fix="apply a targeted fix",
        detection_method="llm_reasoning",
    )


def _review(*findings: Finding) -> ReviewResult:
    return ReviewResult(
        findings=list(findings),
        file_path="demo_notes/notes.py",
        timestamp=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        worker_name="security",
    )


def test_finding_at_threshold_goes_to_accepted() -> None:
    finding = _finding(confidence=DEFAULT_THRESHOLD)
    gated = apply_confidence_gate(_review(finding))

    assert gated.threshold == DEFAULT_THRESHOLD
    assert gated.accepted == [finding]
    assert gated.needs_review == []


def test_finding_below_threshold_goes_to_needs_review() -> None:
    finding = _finding(confidence=79)
    gated = apply_confidence_gate(_review(finding))

    assert gated.accepted == []
    assert gated.needs_review == [finding]


def test_mixed_review_result_splits_correctly() -> None:
    low = _finding(confidence=40, finding_type="low")
    at_threshold = _finding(confidence=80, finding_type="at")
    high = _finding(confidence=95, finding_type="high")
    mid_low = _finding(confidence=79, finding_type="mid_low")

    gated = apply_confidence_gate(_review(low, at_threshold, high, mid_low))

    assert gated.accepted == [at_threshold, high]
    assert gated.needs_review == [low, mid_low]


def test_empty_review_result_yields_empty_lists() -> None:
    gated = apply_confidence_gate(_review())

    assert gated.accepted == []
    assert gated.needs_review == []
    assert gated.threshold == DEFAULT_THRESHOLD
