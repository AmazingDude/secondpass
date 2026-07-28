"""Unit tests for SQLite review / verified-outcome persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.confidence_gate import GateResult, apply_confidence_gate
from app.persistence import (
    get_review,
    init_db,
    list_outcomes_for_file,
    list_reviews,
    save_review,
    save_verified_outcome,
)
from app.schema import Finding, ReviewResult


def _finding(**overrides: object) -> Finding:
    payload: dict[str, object] = {
        "finding_type": "missing_ownership_check",
        "evidence": "get_note returns a note without comparing owner_id",
        "confidence": 85,
        "suggested_fix": "Compare note.owner_id to current_user.id",
        "detection_method": "llm_reasoning",
    }
    payload.update(overrides)
    return Finding.model_validate(payload)


def _review(*findings: Finding, file_path: str = "benchmark/fixtures/notes_idor.py") -> ReviewResult:
    return ReviewResult(
        findings=list(findings),
        file_path=file_path,
        timestamp=datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
        worker_name="security",
    )


def test_init_db_creates_file(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "test.db"
    created = init_db(db_path)

    assert created == db_path
    assert db_path.is_file()


def test_save_and_get_review(tmp_path: Path) -> None:
    db_path = tmp_path / "secondpass.db"
    high = _finding(confidence=90)
    low = _finding(confidence=40, finding_type="command_injection")
    result = _review(high, low)
    gate = apply_confidence_gate(result)

    stored = save_review(result, gate, db_path=db_path)
    loaded = get_review(stored.id, db_path=db_path)

    assert loaded is not None
    assert loaded.id == stored.id
    assert loaded.file_path == "benchmark/fixtures/notes_idor.py"
    assert loaded.worker_name == "security"
    assert loaded.gate_threshold == 80
    assert loaded.accepted_count == 1
    assert loaded.needs_review_count == 1
    assert loaded.review_result.findings[0].confidence == 90
    assert loaded.gate_result.accepted[0].finding_type == "missing_ownership_check"


def test_list_reviews_newest_first_with_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "secondpass.db"
    first = save_review(
        _review(_finding(), file_path="a.py"),
        GateResult(accepted=[_finding()], needs_review=[], threshold=80),
        db_path=db_path,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    second = save_review(
        _review(_finding(confidence=50), file_path="b.py"),
        GateResult(accepted=[], needs_review=[_finding(confidence=50)], threshold=80),
        db_path=db_path,
        created_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    listed = list_reviews(limit=1, db_path=db_path)
    assert len(listed) == 1
    assert listed[0].id == second.id
    assert listed[0].file_path == "b.py"

    all_rows = list_reviews(limit=10, db_path=db_path)
    assert [row.id for row in all_rows] == [second.id, first.id]


def test_get_review_missing_returns_none(tmp_path: Path) -> None:
    db_path = tmp_path / "secondpass.db"
    init_db(db_path)
    assert get_review(999, db_path=db_path) is None


def test_save_and_list_verified_outcomes_by_file(tmp_path: Path) -> None:
    db_path = tmp_path / "secondpass.db"
    finding = _finding()
    review = save_review(
        _review(finding),
        apply_confidence_gate(_review(finding)),
        db_path=db_path,
    )

    accepted = save_verified_outcome(
        finding,
        accepted=True,
        reason="Confirmed IDOR in get_note",
        file_path="benchmark/fixtures/notes_idor.py",
        review_id=review.id,
        linked_fix_commit="abc123",
        db_path=db_path,
    )
    rejected = save_verified_outcome(
        _finding(confidence=70, finding_type="missing_ownership_check"),
        accepted=False,
        reason="False positive — ownership is checked upstream",
        file_path=r"benchmark\fixtures\notes_idor.py",
        review_id=review.id,
        db_path=db_path,
    )
    save_verified_outcome(
        _finding(finding_type="command_injection", confidence=95),
        accepted=True,
        reason="shell=True is real",
        file_path="benchmark/fixtures/ops_shell.py",
        db_path=db_path,
    )

    for_notes = list_outcomes_for_file(
        "benchmark/fixtures/notes_idor.py",
        db_path=db_path,
    )
    assert len(for_notes) == 2
    assert for_notes[0].id == rejected.id
    assert for_notes[0].accepted is False
    assert for_notes[1].id == accepted.id
    assert for_notes[1].linked_fix_commit == "abc123"
    assert for_notes[1].review_id == review.id

    other = list_outcomes_for_file(
        "benchmark/fixtures/ops_shell.py",
        db_path=db_path,
    )
    assert len(other) == 1
    assert other[0].finding.finding_type == "command_injection"
