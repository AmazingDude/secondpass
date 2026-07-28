"""Offline tests for Security review schema and confidence-gate wiring."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.agent import (
    build_security_review_output,
    map_logic_issue,
    map_semgrep_finding,
    review_code,
)
from app.schema import Finding, ReviewResult


def _scanner_finding(**overrides: object) -> dict:
    finding: dict[str, object] = {
        "rule_id": "python.lang.security.audit.subprocess-shell-true",
        "severity": "ERROR",
        "path": "benchmark/fixtures/ops_shell.py",
        "line": 10,
        "message": "Avoid shell=True with user-controlled input",
        "snippet": "subprocess.run(command, shell=True)",
    }
    finding.update(overrides)
    return finding


def _reviewed(raw_finding: dict, *, suggested_fix: str) -> dict:
    return {
        "finding": raw_finding,
        "memory_match": None,
        "memory_matches": [],
        "web_context": [],
        "saved_lesson_id": None,
        "explanation": "Concrete explanation",
        "suggested_fix": suggested_fix,
        "tool_call_failures": 0,
        "routing": {},
        "memory_worker": None,
        "web_worker": None,
    }


def test_semgrep_mapping_produces_static_rule_finding() -> None:
    mapped = map_semgrep_finding(_scanner_finding())

    assert mapped.finding_type == "python.lang.security.audit.subprocess-shell-true"
    assert mapped.detection_method == "static_rule"
    assert mapped.confidence == 90
    assert "ops_shell.py:10" in mapped.evidence
    assert "shell=True" in mapped.evidence


def test_logic_mapping_produces_llm_reasoning_finding() -> None:
    mapped = map_logic_issue(
        "benchmark/fixtures/notes_idor.py",
        {
            "line": 12,
            "severity": "WARNING",
            "finding_type": "missing_ownership_check",
            "confidence": 79,
            "message": "get_note returns records without checking owner_id",
            "snippet": "return NOTES.get(note_id)",
            "suggested_fix": "Compare owner_id with current_user_id.",
        },
        fallback_snippet="return NOTES.get(note_id)",
    )

    assert mapped.finding_type == "missing_ownership_check"
    assert mapped.detection_method == "llm_reasoning"
    assert mapped.confidence == 79
    assert mapped.suggested_fix == "Compare owner_id with current_user_id."
    assert "notes_idor.py:12" in mapped.evidence


def test_build_security_output_applies_gate_and_preserves_enrichment() -> None:
    high = Finding(
        finding_type="command_injection",
        evidence="ops.py:10\nshell=True",
        confidence=90,
        suggested_fix="Initial static fix",
        detection_method="static_rule",
    )
    low = Finding(
        finding_type="missing_ownership_check",
        evidence="notes.py:12\nowner_id is not checked",
        confidence=79,
        suggested_fix="Initial logic fix",
        detection_method="llm_reasoning",
    )
    high_item = _reviewed(
        _scanner_finding(),
        suggested_fix="Use subprocess with shell=False.",
    )
    low_item = _reviewed(
        _scanner_finding(
            rule_id="secondpass.logic-review",
            path="benchmark/fixtures/notes_idor.py",
            line=12,
        ),
        suggested_fix="Check owner_id before returning the note.",
    )

    result, gate, accepted, needs_review = build_security_review_output(
        "benchmark/fixtures",
        [high_item, low_item],
        [high, low],
        timestamp=datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
    )

    assert isinstance(result, ReviewResult)
    assert [item.finding_type for item in gate.accepted] == ["command_injection"]
    assert [item.finding_type for item in gate.needs_review] == [
        "missing_ownership_check"
    ]
    assert accepted[0]["explanation"] == "Concrete explanation"
    assert accepted[0]["structured_finding"]["suggested_fix"] == (
        "Use subprocess with shell=False."
    )
    assert needs_review[0]["structured_finding"]["confidence"] == 79


def test_review_code_returns_schema_and_gate_json_offline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "ops.py"
    target.write_text("import subprocess\n", encoding="utf-8")
    raw = _scanner_finding(path=str(target))

    monkeypatch.setattr("app.agent.seed_memory", lambda: 0)
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [raw])
    monkeypatch.setattr(
        "app.agent._review_finding",
        lambda finding, max_iterations: _reviewed(
            finding,
            suggested_fix="Use shell=False and pass an argument list.",
        ),
    )

    report = review_code(str(target))

    assert report["finding_count"] == 1
    assert report["accepted_count"] == 1
    assert report["needs_review_count"] == 0
    assert report["review_result"]["worker_name"] == "security"
    assert report["review_result"]["findings"][0]["detection_method"] == "static_rule"
    assert report["gate_result"]["threshold"] == 80
    assert report["accepted"][0]["structured_finding"]["finding_type"] == (
        "python.lang.security.audit.subprocess-shell-true"
    )
