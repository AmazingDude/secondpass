"""Lesson-store failures must not discard detector results."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import review_code
from app.persistence import list_reviews
from app.scanner import ScanError
from app.supervisor import supervise_review


def _chat(messages, tools=None, temperature=None, *, route_memory=False, logic_issue=False):
    system = messages[0]["content"]
    if "careful security logic reviewer" in system:
        result = {"has_issues": False, "summary": "No additional issues.", "issues": []}
        if logic_issue:
            result = {
                "has_issues": True,
                "summary": "get_note lacks an ownership check.",
                "issues": [{
                    "line": 4,
                    "severity": "ERROR",
                    "finding_type": "missing_ownership_check",
                    "confidence": 95,
                    "message": "get_note returns NOTES.get(note_id) without checking owner_id.",
                    "snippet": "return NOTES.get(note_id)",
                    "suggested_fix": "Check note['owner_id'] against the caller.",
                }],
            }
    elif "Decide which specialist workers" in system:
        result = {"use_memory": route_memory, "use_web": False}
    elif "Synthesize a final security review" in system:
        result = {
            "explanation": "Shell execution accepts untrusted input.",
            "suggested_fix": "Pass an argument list without a shell.",
        }
    else:
        raise AssertionError("Unexpected model request")
    message = SimpleNamespace(content=json.dumps(result), tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def unavailable_memory(monkeypatch, tmp_path: Path):
    canary = "private-memory-connection-canary"

    def unavailable_client(**kwargs):
        raise RuntimeError(canary)

    monkeypatch.setattr("app.memory._DEFAULT_DB_PATH", tmp_path / "memory")
    monkeypatch.setattr("chromadb.PersistentClient", unavailable_client)
    return canary


@pytest.mark.parametrize("route_memory", [False, True])
def test_memory_startup_failure_preserves_detector_findings(
    monkeypatch, tmp_path: Path, capsys, route_memory: bool, unavailable_memory
) -> None:
    target = tmp_path / "shell.py"
    target.write_text(
        "import subprocess\nNOTES = {}\ndef get_note(note_id, user_id):\n"
        "    return NOTES.get(note_id)\nsubprocess.run(command, shell=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [{
        "rule_id": "python.lang.security.audit.subprocess-shell-true",
        "severity": "ERROR",
        "path": str(target),
        "line": 5,
        "message": "Shell execution accepts untrusted input.",
        "snippet": "subprocess.run(command, shell=True)",
    }])
    chat = partial(_chat, route_memory=route_memory, logic_issue=True)
    monkeypatch.setattr("app.agent.chat", chat)
    monkeypatch.setattr("app.supervisor.chat", chat)
    monkeypatch.setattr("app.workers.common.chat", chat)

    report = review_code(str(target))

    assert report["finding_count"] == 2
    assert report["all_findings"][0]["finding"]["rule_id"] == "python.lang.security.audit.subprocess-shell-true"
    assert report["all_findings"][1]["structured_finding"]["finding_type"] == "missing_ownership_check"
    assert report["inconclusive"] is True
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["no_issues"] is False
    assert "lesson memory" in report["message"].lower()
    for finding in report["all_findings"]:
        assert finding["routing"]["use_memory"] is False
        assert finding["memory_worker"]["unavailable"] is True
    assert unavailable_memory not in json.dumps(report)
    assert unavailable_memory not in capsys.readouterr().err


def test_memory_startup_failure_is_inconclusive_in_saved_empty_review(
    monkeypatch, tmp_path: Path, unavailable_memory
) -> None:
    target = tmp_path / "clean.py"
    target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", tmp_path / "reviews.db")
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    monkeypatch.setattr("app.agent.chat", _chat)

    combined = supervise_review(str(target), run_architecture=False)

    assert combined["summary"]["finding_count"] == 0
    assert combined["summary"]["inconclusive"] is True
    assert combined["summary"]["no_issues"] is False
    reviews = list_reviews()
    assert len(reviews) == 1
    assert reviews[0].review_result.coverage_status == "inconclusive"
    assert unavailable_memory not in json.dumps(combined)


def test_disabled_memory_preserves_findings_without_initializing_store(
    monkeypatch, tmp_path: Path, unavailable_memory,
) -> None:
    target = tmp_path / "notes.py"
    target.write_text(
        "NOTES = {}\n\ndef get_note(note_id, user_id):\n    return NOTES.get(note_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    chat = partial(_chat, route_memory=True, logic_issue=True)
    monkeypatch.setattr("app.agent.chat", chat)
    monkeypatch.setattr("app.supervisor.chat", chat)

    report = review_code(str(target), memory_enabled=False)

    assert report["finding_count"] == 1
    assert report["inconclusive"] is False
    assert report["review_result"]["coverage_status"] == "ok"
    finding = report["all_findings"][0]
    assert finding["structured_finding"]["finding_type"] == "missing_ownership_check"
    assert finding["routing"]["use_memory"] is False
    assert finding["memory_worker"]["disabled"] is True
    assert not finding["memory_worker"].get("unavailable")
    assert not (tmp_path / "memory").exists()


def test_disabled_memory_allows_complete_saved_empty_review(
    monkeypatch, tmp_path: Path, unavailable_memory,
) -> None:
    target = tmp_path / "clean.py"
    target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", tmp_path / "reviews.db")
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    monkeypatch.setattr("app.agent.chat", _chat)

    combined = supervise_review(str(target), run_architecture=False, memory_enabled=False)

    assert combined["summary"]["finding_count"] == 0
    assert combined["summary"]["inconclusive"] is False
    assert combined["summary"]["no_issues"] is True
    assert list_reviews()[0].review_result.coverage_status == "ok"
    assert not (tmp_path / "memory").exists()


@pytest.mark.parametrize("failure", ["scanner", "model"])
def test_disabled_memory_does_not_hide_incomplete_detectors(
    monkeypatch, tmp_path: Path, unavailable_memory, failure: str,
) -> None:
    target = tmp_path / "clean.py"
    target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", tmp_path / "reviews.db")

    def scan(paths):
        if failure == "scanner":
            raise ScanError("Static scan unavailable")
        return []

    monkeypatch.setattr("app.agent.run_static_scan", scan)
    monkeypatch.setattr(
        "app.agent.chat",
        _chat if failure == "scanner" else lambda *args, **kwargs: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not valid JSON"))],
        ),
    )

    combined = supervise_review(str(target), run_architecture=False, memory_enabled=False)

    assert combined["summary"]["inconclusive"] is True
    assert combined["summary"]["no_issues"] is False
    assert list_reviews()[0].review_result.coverage_status == "inconclusive"
    assert not (tmp_path / "memory").exists()
