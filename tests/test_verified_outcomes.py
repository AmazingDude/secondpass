"""Tests for verified-outcome wiring and Supervisor save_finding hard-gate."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.confidence_gate import GateResult, apply_confidence_gate
from app.persistence import get_review, list_outcomes_for_file, save_review
from app.schema import Finding, ReviewResult
from app.supervisor import supervise_finding
from app.verified import (
    persist_combined_review,
    persist_worker_report,
    record_finding_decision,
)


def _finding(**overrides: object) -> Finding:
    payload: dict[str, object] = {
        "finding_type": "missing_ownership_check",
        "evidence": "get_note returns a note without comparing owner_id",
        "confidence": 90,
        "suggested_fix": "Compare note.owner_id to current_user.id",
        "detection_method": "llm_reasoning",
    }
    payload.update(overrides)
    return Finding.model_validate(payload)


def _report_for(finding: Finding, *, path: str, worker: str = "security") -> dict:
    result = ReviewResult(
        findings=[finding],
        file_path=path,
        timestamp=datetime(2026, 7, 30, tzinfo=timezone.utc),
        worker_name=worker,
    )
    gate = apply_confidence_gate(result)
    return {
        "path": path,
        "worker_name": worker,
        "review_result": result.model_dump(mode="json"),
        "gate_result": gate.model_dump(mode="json"),
        "accepted_count": len(gate.accepted),
        "needs_review_count": len(gate.needs_review),
    }


def test_persist_and_record_accept_reject(tmp_path: Path) -> None:
    db = tmp_path / "secondpass.db"
    finding = _finding()
    report = _report_for(finding, path="benchmark/fixtures/notes_idor.py")
    stored = persist_worker_report(report, db_path=db)
    assert stored is not None

    accepted = record_finding_decision(
        stored.id,
        0,
        accepted=True,
        reason="Confirmed IDOR — no owner_id check",
        db_path=db,
    )
    rejected = record_finding_decision(
        stored.id,
        0,
        accepted=False,
        reason="Would reject a duplicate false positive",
        db_path=db,
    )

    assert accepted.accepted is True
    assert accepted.reason.startswith("Confirmed IDOR")
    assert accepted.review_id == stored.id
    assert rejected.accepted is False

    outcomes = list_outcomes_for_file(
        "benchmark/fixtures/notes_idor.py", db_path=db
    )
    assert len(outcomes) == 2
    assert {item.accepted for item in outcomes} == {True, False}


def test_record_decision_requires_reason(tmp_path: Path) -> None:
    db = tmp_path / "secondpass.db"
    finding = _finding()
    result = ReviewResult(
        findings=[finding],
        file_path="a.py",
        timestamp=datetime(2026, 7, 30, tzinfo=timezone.utc),
        worker_name="security",
    )
    stored = save_review(
        result,
        GateResult(accepted=[finding], needs_review=[], threshold=80),
        db_path=db,
    )
    try:
        record_finding_decision(stored.id, 0, accepted=True, reason="  ", db_path=db)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "reason" in str(exc).lower()


def test_persist_combined_review_writes_both_workers(tmp_path: Path) -> None:
    db = tmp_path / "secondpass.db"
    combined = {
        "security": _report_for(
            _finding(), path="benchmark/fixtures/notes_idor.py", worker="security"
        ),
        "architecture": _report_for(
            _finding(
                finding_type="dependency_direction",
                evidence="service imports cli",
                confidence=85,
            ),
            path="benchmark/fixtures/notes_idor.py",
            worker="architecture",
        ),
    }
    ids = persist_combined_review(combined, db_path=db)
    assert ids["security"] is not None
    assert ids["architecture"] is not None
    assert get_review(ids["security"], db_path=db).worker_name == "security"
    assert get_review(ids["architecture"], db_path=db).worker_name == "architecture"


def test_supervise_finding_never_calls_save_finding(monkeypatch) -> None:
    calls: list[Any] = []

    def _fake_route(finding: dict) -> tuple[dict, int]:
        return (
            {
                "use_memory": False,
                "use_web": False,
                "memory_query_hint": "",
                "web_query_hint": "",
                "routing_rationale": "test",
            },
            0,
        )

    def _fake_synth(finding, memory_result, web_result) -> tuple[dict, int]:
        return (
            {
                "explanation": "test",
                "suggested_fix": "fix it",
                "should_save_lesson": True,
                "lesson_to_save": {
                    "type": "idor",
                    "pattern": "x",
                    "fix": "y",
                },
            },
            0,
        )

    def _boom(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise AssertionError("save_finding must not be called")

    monkeypatch.setattr("app.supervisor._route_workers", _fake_route)
    monkeypatch.setattr("app.supervisor._synthesize", _fake_synth)
    monkeypatch.setattr("app.memory.save_finding", _boom)

    result = supervise_finding(
        {
            "rule_id": "secondpass.logic-review",
            "path": "notes.py",
            "line": 1,
            "message": "missing ownership",
            "snippet": "return NOTES.get(id)",
            "severity": "WARNING",
        }
    )

    assert result["saved_lesson_id"] is None
    assert calls == []
    assert "save_finding" not in str(result)
