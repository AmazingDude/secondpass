"""Unit tests for Phase 3 review finding schemas."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schema import Finding, ReviewResult


def _valid_finding(**overrides: object) -> Finding:
    payload: dict[str, object] = {
        "finding_type": "missing_ownership_check",
        "evidence": "get_note(note_id) returns a note without comparing owner_id",
        "confidence": 85,
        "suggested_fix": "Verify note.owner_id == current_user.id before returning",
        "detection_method": "llm_reasoning",
    }
    payload.update(overrides)
    return Finding.model_validate(payload)


def test_valid_finding_and_review_result() -> None:
    finding = _valid_finding()
    result = ReviewResult(
        findings=[finding],
        file_path="demo_notes/notes.py",
        timestamp=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        worker_name="security",
    )

    assert finding.finding_type == "missing_ownership_check"
    assert finding.confidence == 85
    assert finding.detection_method == "llm_reasoning"
    assert len(result.findings) == 1
    assert result.file_path == "demo_notes/notes.py"
    assert result.worker_name == "security"

    empty = ReviewResult(
        findings=[],
        file_path="demo_notes/clean.py",
        timestamp=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        worker_name="architecture",
    )
    assert empty.findings == []


def test_empty_evidence_fails_validation() -> None:
    with pytest.raises(ValidationError):
        _valid_finding(evidence="")

    with pytest.raises(ValidationError):
        _valid_finding(evidence="   ")


@pytest.mark.parametrize("confidence", [-1, 101])
def test_confidence_outside_range_fails_validation(confidence: int) -> None:
    with pytest.raises(ValidationError):
        _valid_finding(confidence=confidence)
