"""Security coverage semantics: additive static+logic, inconclusive, truncation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.agent import (
    _MAX_LOGIC_SOURCE_CHARS,
    assess_logic_review,
    map_logic_issue,
    review_code,
)
from app.llm import LLMRateLimitedError


def _fake_chat(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _static_finding(path: str) -> dict[str, Any]:
    return {
        "rule_id": "python.lang.security.audit.subprocess-shell-true",
        "severity": "ERROR",
        "path": path,
        "line": 2,
        "message": "subprocess call with shell=True",
        "snippet": "subprocess.call(cmd, shell=True)",
    }


def _reviewed(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding": finding,
        "explanation": "static or logic finding",
        "suggested_fix": "fix it",
        "memory_match": None,
        "web_context": [],
        "saved_lesson_id": None,
        "tool_call_failures": 0,
    }


def test_static_finding_does_not_suppress_logic_review(monkeypatch, tmp_path: Path) -> None:
    """(a) Semgrep hit + planted IDOR-like logic → logic-review still runs."""
    target = tmp_path / "mixed.py"
    target.write_text(
        "import subprocess\n"
        "NOTES = {}\n"
        "def get_note(note_id, user_id):\n"
        "    return NOTES.get(note_id)\n"
        "subprocess.call('ls', shell=True)\n",
        encoding="utf-8",
    )
    static = _static_finding(str(target))
    logic_calls: list[str] = []

    def _assess(path: str, scan_note: str | None = None) -> dict[str, Any]:
        logic_calls.append(scan_note or "")
        issue = {
            "line": 3,
            "severity": "ERROR",
            "finding_type": "missing_ownership_check",
            "confidence": 90,
            "message": "get_note returns NOTES.get(note_id) without ownership check",
            "snippet": "return NOTES.get(note_id)",
            "suggested_fix": "Compare owner_id to current user before return",
        }
        structured = map_logic_issue(
            path, issue, fallback_snippet=issue["snippet"]
        )
        from app.agent import _logic_finding_from_issue

        return {
            "has_issues": True,
            "summary": "1 ownership issue",
            "findings": [
                _logic_finding_from_issue(
                    path, issue, fallback_snippet=issue["snippet"]
                )
            ],
            "structured_findings": [structured],
            "failures": 0,
            "inconclusive": False,
            "source_truncated": False,
            "status": "issues",
        }

    monkeypatch.setattr("app.agent.seed_memory", lambda: None)
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [static])
    monkeypatch.setattr("app.agent.assess_logic_review", _assess)
    monkeypatch.setattr(
        "app.agent._review_finding",
        lambda finding, max_iterations=6: _reviewed(finding),
    )

    report = review_code(str(target))

    assert logic_calls, "logic-review must run even when Semgrep found something"
    assert "ADDITIONAL" in logic_calls[0] or "additional" in logic_calls[0].lower()
    assert report["finding_count"] == 2
    types = [
        item["structured_finding"]["finding_type"]
        for item in report["all_findings"]
    ]
    assert "python.lang.security.audit.subprocess-shell-true" in types
    assert "missing_ownership_check" in types
    assert report.get("used_logic_review") is True
    assert report.get("inconclusive") is False


def test_rate_limited_logic_review_is_not_clean_success(
    monkeypatch, tmp_path: Path
) -> None:
    """(b) Rate-limited logic-review must not present as successful clean."""
    target = tmp_path / "notes.py"
    target.write_text("def get_note():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr("app.agent.seed_memory", lambda: None)
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    monkeypatch.setattr(
        "app.agent.chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LLMRateLimitedError("skipped — rate limited")
        ),
    )

    report = review_code(str(target))

    assert report["finding_count"] == 0
    assert report.get("inconclusive") is True
    assert report.get("no_issues") is False
    assert report.get("logic_review_status") == "inconclusive"
    assert "inconclusive" in (report.get("message") or "").lower()
    assert "rate limited" in (report.get("message") or "").lower()
    assert (report.get("review_result") or {}).get("coverage_status") == "inconclusive"


def test_assess_logic_review_rate_limit_marks_inconclusive(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "notes.py"
    target.write_text("def get_note():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.agent.chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LLMRateLimitedError("skipped — rate limited")
        ),
    )

    result = assess_logic_review(str(target))
    assert result["has_issues"] is False
    assert result["inconclusive"] is True
    assert result["status"] == "inconclusive"
    assert result["summary"] == "inconclusive — rate limited"
    assert result["failures"] == 1


def test_truncated_source_visible_on_report(monkeypatch, tmp_path: Path) -> None:
    """(c) Truncated logic-review input is visible in the report dict."""
    target = tmp_path / "huge.py"
    # Past the logic-review window so truncation trips.
    target.write_text("x = 1\n" * (_MAX_LOGIC_SOURCE_CHARS // 2), encoding="utf-8")

    monkeypatch.setattr("app.agent.seed_memory", lambda: None)
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    monkeypatch.setattr(
        "app.agent.chat",
        lambda *args, **kwargs: _fake_chat(
            '{"has_issues": false, "summary": "No security issues found.", "issues": []}'
        ),
    )

    report = review_code(str(target))

    assert report.get("source_truncated") is True
    assert report.get("source_truncated_note")
    assert str(_MAX_LOGIC_SOURCE_CHARS) in (report["source_truncated_note"] or "")
    assert report.get("inconclusive") is False
    assert report.get("no_issues") is True


def test_hard_chat_failure_is_inconclusive_not_clean(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "notes.py"
    target.write_text("def get_note():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr("app.agent.seed_memory", lambda: None)
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    monkeypatch.setattr(
        "app.agent.chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )

    report = review_code(str(target))
    assert report.get("inconclusive") is True
    assert report.get("no_issues") is False
    assert "inconclusive" in (report.get("message") or "").lower()
