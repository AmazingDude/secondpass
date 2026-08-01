"""Two-sided tests: Security ↔ Architecture category-bleed filters."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import (
    _LOGIC_ASSESS_SYSTEM,
    assess_logic_review,
    is_architecture_category_bleed,
)
from app.benchmark_cross_worker import (
    ARCHITECTURE_FIXTURES_EXPECT_SECURITY_CLEAN,
    SECURITY_FIXTURES_EXPECT_ARCHITECTURE_NO_AUTHZ_BLEED,
    architecture_findings_have_security_bleed,
    run_cross_worker_bleed_checks,
    security_items_have_architecture_bleed,
)
from app.workers.architecture_worker import (
    _issue_to_finding,
    is_security_category_bleed,
)

_REPO = Path(__file__).resolve().parents[1]


def _msg(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_logic_prompt_forbids_architecture_bleed() -> None:
    assert "Architecture Worker" in _LOGIC_ASSESS_SYSTEM
    assert "bypass of" in _LOGIC_ASSESS_SYSTEM
    assert "business rules" in _LOGIC_ASSESS_SYSTEM
    assert "dependency direction" in _LOGIC_ASSESS_SYSTEM
    assert "has_issues=false" in _LOGIC_ASSESS_SYSTEM


@pytest.mark.parametrize(
    "finding_type,message",
    [
        (
            "bypass_of_business_rules",
            "checkout bypasses the service layer business rules",
        ),
        (
            "dependency_direction_violation",
            "low-level module depends upward on high-level workflow",
        ),
        (
            "layering_violation",
            "handler reaches into the data layer directly",
        ),
        (
            "architecture_concern",
            "this is really an architectural layering issue",
        ),
    ],
)
def test_architecture_bleed_markers_are_detected(finding_type: str, message: str) -> None:
    assert is_architecture_category_bleed(
        finding_type=finding_type,
        message=message,
        evidence=message,
        suggested_fix="refactor layers",
    )


@pytest.mark.parametrize(
    "finding_type,message",
    [
        (
            "missing_ownership_check",
            "get_note returns NOTES.get(note_id) without comparing owner_id",
        ),
        (
            "hardcoded_secret",
            "API_KEY is hardcoded in source instead of env/secret manager",
        ),
        (
            "path_traversal",
            "filename is joined to EXPORT_DIR with no sanitization",
        ),
        (
            "command_injection",
            "user-controlled archive_name interpolated into shell=True",
        ),
    ],
)
def test_real_security_findings_are_not_architecture_bleed(
    finding_type: str, message: str
) -> None:
    assert not is_architecture_category_bleed(
        finding_type=finding_type,
        message=message,
        evidence=message,
        suggested_fix="fix the security bug",
    )


def test_assess_logic_review_drops_architecture_bleed(monkeypatch) -> None:
    path = str(_REPO / "benchmark/fixtures/architecture/checkout_handler.py")

    monkeypatch.setattr(
        "app.agent.chat",
        lambda *a, **k: _msg(
            json.dumps(
                {
                    "has_issues": True,
                    "summary": "bypasses service layer",
                    "issues": [
                        {
                            "line": 15,
                            "severity": "WARNING",
                            "finding_type": "bypass_of_business_rules",
                            "confidence": 90,
                            "message": (
                                "checkout bypasses inventory_service business rules "
                                "by mutating the data store directly"
                            ),
                            "snippet": "STOCK[sku] -= quantity",
                            "suggested_fix": "call reserve_stock instead",
                        }
                    ],
                }
            )
        ),
    )
    result = assess_logic_review(path)
    assert result["has_issues"] is False
    assert result["findings"] == []
    assert result["structured_findings"] == []


def test_assess_logic_review_keeps_real_idor(monkeypatch) -> None:
    path = str(_REPO / "benchmark/fixtures/notes_idor.py")

    monkeypatch.setattr(
        "app.agent.chat",
        lambda *a, **k: _msg(
            json.dumps(
                {
                    "has_issues": True,
                    "summary": "IDOR on get_note",
                    "issues": [
                        {
                            "line": 12,
                            "severity": "ERROR",
                            "finding_type": "missing_ownership_check",
                            "confidence": 95,
                            "message": (
                                "get_note returns any note without comparing "
                                "owner_id to current_user_id"
                            ),
                            "snippet": "return NOTES.get(note_id)",
                            "suggested_fix": "Require note['owner_id'] == current_user_id",
                        }
                    ],
                }
            )
        ),
    )
    result = assess_logic_review(path)
    assert result["has_issues"] is True
    assert len(result["structured_findings"]) == 1
    assert result["structured_findings"][0].finding_type == "missing_ownership_check"


@pytest.mark.parametrize("rel", ARCHITECTURE_FIXTURES_EXPECT_SECURITY_CLEAN)
def test_architecture_fixture_paths_exist(rel: str) -> None:
    assert (_REPO / rel).is_file()


@pytest.mark.parametrize("rel", SECURITY_FIXTURES_EXPECT_ARCHITECTURE_NO_AUTHZ_BLEED)
def test_security_fixture_paths_exist(rel: str) -> None:
    assert (_REPO / rel).is_file()


def test_cross_worker_helpers_flag_bleed_payloads() -> None:
    assert security_items_have_architecture_bleed(
        [
            {
                "structured_finding": {
                    "finding_type": "dependency_direction_violation",
                    "evidence": "depends upward",
                    "suggested_fix": "invert",
                }
            }
        ]
    )
    assert architecture_findings_have_security_bleed(
        [
            {
                "finding_type": "layering_violation",
                "evidence": "missing ownership check / IDOR",
                "suggested_fix": "add owner_id check",
            }
        ]
    )


def test_architecture_still_drops_security_bleed_on_security_fixtures() -> None:
    """Standing reverse check: Architecture filter still rejects authz on IDOR file."""
    finding = _issue_to_finding(
        "benchmark/fixtures/notes_idor.py",
        {
            "finding_type": "layering_violation",
            "confidence": 90,
            "location": "get_note",
            "evidence": "get_note skips owner_id vs current_user_id",
            "message": "Missing ownership / IDOR",
            "suggested_fix": "Add ownership check",
        },
    )
    assert finding is None
    assert is_security_category_bleed(
        finding_type="layering_violation",
        evidence="owner_id never compared",
        message="IDOR",
    )


def test_cross_worker_fixture_presence_check() -> None:
    result = run_cross_worker_bleed_checks(live=False)
    assert result["status"] == "fixtures_present"
