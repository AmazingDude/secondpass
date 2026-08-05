"""Tests for verified-outcome wiring, human-gated Chroma promote, Supervisor hard-gate."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.audit import STAGE_CHROMA_PROMOTE, get_audit_trail
from app.confidence_gate import GateResult, apply_confidence_gate
from app.persistence import get_review, list_outcomes_for_file, save_review
from app.schema import Finding, ReviewResult
from app.supervisor import supervise_finding
from app.verified import (
    FindingDecisionResult,
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
    chroma = tmp_path / "chromadb"
    finding = _finding()
    report = _report_for(finding, path="benchmark/fixtures/notes_idor.py")
    stored = persist_worker_report(report, db_path=db, job_id="job-accept-reject")
    assert stored is not None

    accepted = record_finding_decision(
        stored.id,
        0,
        accepted=True,
        reason="Confirmed IDOR — no owner_id check",
        db_path=db,
        chroma_persist_directory=chroma,
    )
    rejected = record_finding_decision(
        stored.id,
        0,
        accepted=False,
        reason="Would reject a duplicate false positive",
        db_path=db,
        chroma_persist_directory=chroma,
    )

    assert isinstance(accepted, FindingDecisionResult)
    assert accepted.outcome.accepted is True
    assert accepted.outcome.reason.startswith("Confirmed IDOR")
    assert accepted.outcome.review_id == stored.id
    assert accepted.memory_promotion is not None
    assert accepted.memory_promotion["status"] == "saved"
    assert accepted.memory_promotion.get("lesson_id")

    assert rejected.outcome.accepted is False
    assert rejected.memory_promotion is None

    outcomes = list_outcomes_for_file(
        "benchmark/fixtures/notes_idor.py", db_path=db
    )
    assert len(outcomes) == 2
    assert {item.accepted for item in outcomes} == {True, False}


def test_human_accept_writes_sqlite_and_promotes_chroma(tmp_path: Path) -> None:
    db = tmp_path / "secondpass.db"
    chroma = tmp_path / "chromadb"
    finding = _finding()
    stored = persist_worker_report(
        _report_for(finding, path="benchmark/fixtures/notes_idor.py"),
        db_path=db,
        job_id="job-promote",
    )
    assert stored is not None

    decision = record_finding_decision(
        stored.id,
        0,
        accepted=True,
        reason="Human confirmed ownership bug",
        db_path=db,
        chroma_persist_directory=chroma,
    )

    outcomes = list_outcomes_for_file(
        "benchmark/fixtures/notes_idor.py", db_path=db
    )
    assert len(outcomes) == 1
    assert outcomes[0].accepted is True
    assert decision.memory_promotion is not None
    assert decision.memory_promotion["status"] == "saved"

    from app.memory import search_memory

    matches = search_memory(
        "missing ownership check get_note",
        n_results=3,
        persist_directory=chroma,
    )
    assert any(
        m.get("id") == decision.memory_promotion["lesson_id"] for m in matches
    )


def test_human_reject_never_calls_chroma(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "secondpass.db"
    calls: list[Any] = []

    def _boom(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise AssertionError("save_finding must not run on reject")

    monkeypatch.setattr("app.memory.save_finding", _boom)

    stored = persist_worker_report(
        _report_for(_finding(), path="benchmark/fixtures/notes_idor.py"),
        db_path=db,
    )
    assert stored is not None

    decision = record_finding_decision(
        stored.id,
        0,
        accepted=False,
        reason="False positive — ownership is checked upstream",
        db_path=db,
    )

    assert decision.outcome.accepted is False
    assert decision.memory_promotion is None
    assert calls == []
    assert len(list_outcomes_for_file("benchmark/fixtures/notes_idor.py", db_path=db)) == 1


def test_chroma_duplicate_accept_skips_second_lesson(tmp_path: Path) -> None:
    db = tmp_path / "secondpass.db"
    chroma = tmp_path / "chromadb"
    stored = persist_worker_report(
        _report_for(_finding(), path="benchmark/fixtures/notes_idor.py"),
        db_path=db,
        job_id="job-dup",
    )
    assert stored is not None

    first = record_finding_decision(
        stored.id,
        0,
        accepted=True,
        reason="Confirmed once",
        db_path=db,
        chroma_persist_directory=chroma,
    )
    second = record_finding_decision(
        stored.id,
        0,
        accepted=True,
        reason="Confirmed again (retry)",
        db_path=db,
        chroma_persist_directory=chroma,
    )

    assert first.memory_promotion is not None
    assert first.memory_promotion["status"] == "saved"
    assert second.memory_promotion is not None
    assert second.memory_promotion["status"] == "skipped"
    assert second.memory_promotion.get("matched_id") == first.memory_promotion.get(
        "lesson_id"
    )

    from app.memory import init_memory

    assert init_memory(chroma).count() == 1
    assert len(list_outcomes_for_file("benchmark/fixtures/notes_idor.py", db_path=db)) == 2


def test_chroma_failure_keeps_sqlite_outcome_and_audits(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "secondpass.db"
    chroma = tmp_path / "chromadb"

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("chroma disk full")

    monkeypatch.setattr("app.memory.save_finding", _fail)

    stored = persist_worker_report(
        _report_for(_finding(), path="benchmark/fixtures/notes_idor.py"),
        db_path=db,
        job_id="job-chroma-fail",
    )
    assert stored is not None

    decision = record_finding_decision(
        stored.id,
        0,
        accepted=True,
        reason="Still accept even if memory write fails",
        db_path=db,
        chroma_persist_directory=chroma,
    )

    assert decision.outcome.accepted is True
    assert decision.memory_promotion is not None
    assert decision.memory_promotion["status"] == "error"
    assert "chroma disk full" in (decision.memory_promotion.get("error") or "")

    outcomes = list_outcomes_for_file(
        "benchmark/fixtures/notes_idor.py", db_path=db
    )
    assert len(outcomes) == 1

    trail = get_audit_trail("job-chroma-fail", db_path=db)
    promote_events = [e for e in trail if e.stage == STAGE_CHROMA_PROMOTE]
    assert promote_events
    assert promote_events[-1].detail.get("status") == "error"


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
