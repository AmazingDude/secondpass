"""Incomplete reviews must remain visible through the public review interfaces."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import review_architecture, review_code
from app.llm import LLMRateLimitedError
from app.scanner import ScanError
from app.supervisor import supervise_review


def _response(content: str | None, *, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, refusal=None),
        finish_reason=finish_reason,
    )])


@pytest.fixture()
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "service.py"
    path.write_text("def answer():\n    return 42\n", encoding="utf-8")
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", tmp_path / "reviews.db")
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "tools.log")
    monkeypatch.setattr("app.memory._DEFAULT_DB_PATH", tmp_path / "chroma")
    collection = SimpleNamespace(count=lambda: 1)
    monkeypatch.setattr(
        "app.memory.chromadb.PersistentClient",
        lambda **kwargs: SimpleNamespace(get_or_create_collection=lambda **kw: collection),
    )
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    return path


@pytest.mark.parametrize("review", [review_code, review_architecture])
@pytest.mark.parametrize("issue", [
    {},
    {"message": 123},
    {"message": "An issue", "evidence": []},
    {"message": "An issue", "confidence": "90"},
    {"message": "An issue", "confidence": True},
    {"message": "An issue", "confidence": 101},
    {"message": "An issue", "line": "2"},
])
def test_malformed_issue_is_inconclusive(target: Path, monkeypatch, review, issue) -> None:
    response = _response(json.dumps({
        "has_issues": True, "summary": "Possible issue", "issues": [issue],
    }))
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: response)
    monkeypatch.setattr("app.workers.architecture_worker.chat", lambda *a, **kw: response)
    # Don't permit accidental enrichment to reach a real provider before validation.
    monkeypatch.setattr(
        "app.supervisor.chat",
        lambda *a, **kw: _response('{"use_memory": false, "use_web": false}'),
    )

    report = review(str(target))

    assert report["no_issues"] is False
    assert report["inconclusive"] is True
    assert report["finding_count"] == 0


@pytest.mark.parametrize("issues", [[], [{"evidence": "Undescribed claim"}]])
def test_security_claim_requires_described_issues(target: Path, monkeypatch, issues) -> None:
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: _response(json.dumps({
        "has_issues": True, "summary": "Possible issue", "issues": issues,
    })))

    report = review_code(str(target))

    assert report["no_issues"] is False
    assert report["inconclusive"] is True


@pytest.mark.parametrize("security_ok,architecture_ok,expected_inconclusive", [
    (True, False, True),
    (False, True, True),
    (False, False, True),
    (True, True, False),
])
@pytest.mark.parametrize("with_static_finding", [False, True])
def test_supervisor_preserves_worker_failures_and_static_findings(
    target: Path, monkeypatch, security_ok: bool, architecture_ok: bool,
    expected_inconclusive: bool, with_static_finding: bool,
) -> None:
    clean = _response('{"has_issues": false, "summary": "No issues found.", "issues": []}')
    invalid = _response("not JSON")
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: clean if security_ok else invalid)
    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *a, **kw: clean if architecture_ok else invalid,
    )
    # Finding enrichment still runs normally; only the external model is scripted.
    monkeypatch.setattr(
        "app.supervisor.chat",
        lambda *a, **kw: _response(
            '{"use_memory": false, "use_web": false, '
            '"explanation": "Static rule matched.", "suggested_fix": "Fix the match."}'
        ),
    )
    if with_static_finding:
        monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [{
            "rule_id": "test.static-match", "severity": "WARNING",
            "path": str(target), "line": 2, "message": "Static rule matched.",
            "snippet": "return 42",
        }])

    report = supervise_review(str(target))

    assert report["summary"]["inconclusive"] is expected_inconclusive
    assert report["summary"]["no_issues"] is (not expected_inconclusive and not with_static_finding)
    assert report["summary"]["accepted_count"] == (1 if with_static_finding else 0)
    if with_static_finding:
        finding = report["security"]["accepted"][0]["structured_finding"]
        assert finding["finding_type"] == "test.static-match"
        assert finding["detection_method"] == "static_rule"


@pytest.mark.parametrize("review", [review_code, review_architecture])
@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "```\n{}\n```"])
def test_completed_clean_response_remains_clean(target: Path, monkeypatch, review, wrapper) -> None:
    content = '{"has_issues": false, "summary": "No issues found.", "issues": []}'
    response = _response(wrapper.format(content))
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: response)
    monkeypatch.setattr("app.workers.architecture_worker.chat", lambda *a, **kw: response)

    report = review(str(target))

    assert report["no_issues"] is True
    assert report["inconclusive"] is False
    assert report["review_result"]["coverage_status"] == "ok"
    assert report["tool_call_failures"] == 0


@pytest.mark.parametrize("scan_error", ["Semgrep unavailable", ""])
@pytest.mark.parametrize("logic_completed", [False, True])
def test_supervisor_preserves_static_failure_with_clean_or_failed_logic(
    target: Path, monkeypatch, scan_error: str, logic_completed: bool,
) -> None:
    def fail_scan(paths):
        raise ScanError(scan_error)

    clean = _response('{"has_issues": false, "summary": "No issues found.", "issues": []}')
    invalid = _response("not JSON")
    monkeypatch.setattr("app.agent.run_static_scan", fail_scan)
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: clean if logic_completed else invalid)
    monkeypatch.setattr("app.workers.architecture_worker.chat", lambda *a, **kw: clean)

    report = supervise_review(str(target))

    assert report["summary"]["inconclusive"] is True
    assert report["summary"]["no_issues"] is False
    assert report["summary"]["finding_count"] == 0
    assert report["security"]["static_scan_error"] == scan_error
    assert report["security"]["static_scan_empty"] is False
    assert report["security"]["review_result"]["coverage_status"] == "inconclusive"
    assert report["security"]["logic_review_status"] == ("clean" if logic_completed else "inconclusive")
    assert "static scan could not complete" in report["security"]["message"]
    if not logic_completed:
        assert "invalid model response" in report["security"]["message"]
    assert report["architecture"]["review_result"]["coverage_status"] == "ok"


@pytest.mark.parametrize("review,limit", [(review_code, 12000), (review_architecture, 8000)])
@pytest.mark.parametrize("extra_chars,expected_truncated", [(-1, False), (0, False), (1, True)])
def test_target_source_limit_controls_review_coverage(
    target: Path, monkeypatch, review, limit: int,
    extra_chars: int, expected_truncated: bool,
) -> None:
    target.write_text("#" + "α" * (limit + extra_chars - 1), encoding="utf-8")
    clean = _response('{"has_issues": false, "summary": "No issues found.", "issues": []}')
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: clean)
    monkeypatch.setattr("app.workers.architecture_worker.chat", lambda *a, **kw: clean)

    report = review(str(target))

    assert report["inconclusive"] is expected_truncated
    assert report["no_issues"] is (not expected_truncated)
    assert report["review_result"]["coverage_status"] == ("inconclusive" if expected_truncated else "ok")
    assert report["source_truncated"] is expected_truncated
    assert report["tool_call_failures"] == 0
    if expected_truncated:
        assert str(limit) in report["source_truncated_note"]
        assert "inconclusive" in report["message"]
    else:
        assert report["source_truncated_note"] is None


@pytest.mark.parametrize("logic_has_issues,source_reviewable,expected_logic_status", [
    (False, True, "clean"), (True, True, "issues"), (False, False, None),
])
def test_static_scan_failure_is_inconclusive_without_discarding_logic_result(
    target: Path, monkeypatch, logic_has_issues: bool,
    source_reviewable: bool, expected_logic_status: str | None,
) -> None:
    source = "import os\ndef run(command):\n    return os.system(command)\n" if source_reviewable else ""
    target.write_text(source, encoding="utf-8")
    issues = [{
        "message": "Untrusted command reaches the shell.",
        "finding_type": "command_injection",
        "line": 3,
        "snippet": "return os.system(command)",
        "confidence": 90,
        "suggested_fix": "Pass allowlisted arguments without a shell.",
    }] if logic_has_issues else []

    def fail_scan(paths):
        raise ScanError(
            "Semgrep scan failed: network error, falling back to logic-review"
        )

    monkeypatch.setattr("app.agent.run_static_scan", fail_scan)
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: _response(json.dumps({
        "has_issues": logic_has_issues,
        "summary": "Command injection found." if logic_has_issues else "No security issues found.",
        "issues": issues,
    })))
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, '
        '"explanation": "Untrusted command reaches the shell.", '
        '"suggested_fix": "Pass allowlisted arguments without a shell."}'
    ))

    report = review_code(str(target))

    assert report["static_scan_error"] == (
        "Semgrep scan failed: network error, falling back to logic-review"
    )
    assert "Traceback" not in report["static_scan_error"]
    assert report["used_logic_fallback"] is source_reviewable
    assert report["inconclusive"] is True
    assert report["no_issues"] is False
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["logic_review_status"] == expected_logic_status
    assert "static scan could not complete" in report["message"]
    assert report["finding_count"] == (1 if logic_has_issues else 0)
    if logic_has_issues:
        finding = report["accepted"][0]["structured_finding"]
        assert finding["finding_type"] == "command_injection"
        assert finding["detection_method"] == "llm_reasoning"


@pytest.mark.parametrize("review,finding_type,explanation", [
    (review_code, "command_injection", "Issue confirmed in excerpt."),
    (review_architecture, "duplicated_logic", "Issue found in the analyzed source."),
])
@pytest.mark.parametrize("confidence,accepted,needs_review", [(90, 1, 0), (60, 0, 1)])
def test_clipped_review_keeps_findings_and_confidence_gate(
    target: Path, monkeypatch, review, finding_type: str, explanation: str,
    confidence: int, accepted: int, needs_review: int,
) -> None:
    target.write_text(
        "import os\ndef first(command):\n    return os.system(command)\n"
        "def second(command):\n    return os.system(command)\n#" + "x" * 12000,
        encoding="utf-8",
    )
    response = _response(json.dumps({
        "has_issues": True,
        "summary": "Issue found in the analyzed source.",
        "issues": [{
            "finding_type": finding_type,
            "confidence": confidence,
            "message": "Functions first and second have near-identical shell execution bodies.",
            "evidence": "Both functions return os.system(command).",
            "snippet": "return os.system(command)",
            "suggested_fix": "Use one shared implementation with allowlisted arguments.",
        }],
    }))
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: response)
    monkeypatch.setattr("app.workers.architecture_worker.chat", lambda *a, **kw: response)
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, "explanation": "Issue confirmed in excerpt."}'
    ))

    report = review(str(target))

    assert report["inconclusive"] is True
    assert report["no_issues"] is False
    assert report["source_truncated"] is True
    assert "inconclusive" in report["message"]
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["accepted_count"] == accepted
    assert report["needs_review_count"] == needs_review
    assert report["review_result"]["findings"][0]["finding_type"] == finding_type
    assert report["review_result"]["findings"][0]["confidence"] == confidence
    assert explanation in report["all_findings"][0]["explanation"]


@pytest.mark.parametrize("review", [review_code, review_architecture])
@pytest.mark.parametrize("response", [
    _response("not JSON"),
    _response("{}"),
    _response('{"has_issues": false}'),
    _response('{"has_issues": "false", "summary": "clean", "issues": []}'),
    _response('{"has_issues": 0, "summary": "clean", "issues": []}'),
    _response('{"has_issues": false, "summary": {}, "issues": []}'),
    _response('{"has_issues": false, "summary": " ", "issues": []}'),
    _response('{"has_issues": false, "summary": "clean", "issues": null}'),
    _response('{"has_issues": false, "summary": "clean", "issues": [{}]}'),
    _response('{"has_issues": true, "has_issues": false, "summary": "clean", "issues": []}'),
    _response('{"has_issues": false, "summary": "clean", "issues": [{"message": "issue"}], "issues": []}'),
    _response('{"has_issues": true, "summary": "issue", "issues": [null]}'),
    _response('[{"has_issues": false, "summary": "clean", "issues": []}]'),
    _response('Refused. Example: {"has_issues": false, "summary": "clean", "issues": []}'),
    _response('{"has_issues": false, "summary": "clean", "issues": []}', finish_reason="length"),
    _response('{"has_issues": false, "summary": "clean", "issues": []}', finish_reason="content_filter"),
    _response(None),
    SimpleNamespace(choices=[]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content='{"has_issues": false, "summary": "clean", "issues": []}',
        refusal="Cannot review",
    ))]),
])
def test_review_requires_a_complete_consistent_response(
    target: Path, monkeypatch, response: SimpleNamespace, review,
) -> None:
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: response)
    monkeypatch.setattr("app.workers.architecture_worker.chat", lambda *a, **kw: response)

    report = review(str(target))

    assert report["no_issues"] is False
    assert report["inconclusive"] is True
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["tool_call_failures"] == 1
    assert "invalid model response" in report["message"]


@pytest.mark.parametrize("source_chars,security_clipped,architecture_clipped", [
    (8000, False, False), (8001, False, True),
    (12000, False, True), (12001, True, True),
])
@pytest.mark.parametrize("with_static_finding", [False, True])
def test_supervisor_keeps_clipped_coverage_and_static_findings(
    target: Path, monkeypatch, source_chars: int,
    security_clipped: bool, architecture_clipped: bool, with_static_finding: bool,
) -> None:
    prefix = "def answer():\n    return 42\n#"
    target.write_text(prefix + "x" * (source_chars - len(prefix)), encoding="utf-8")
    clean = _response('{"has_issues": false, "summary": "No issues found.", "issues": []}')
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: clean)
    monkeypatch.setattr("app.workers.architecture_worker.chat", lambda *a, **kw: clean)
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, "explanation": "Static rule matched."}'
    ))
    if with_static_finding:
        monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [{
            "rule_id": "test.static-match", "severity": "WARNING",
            "path": str(target), "line": 2, "message": "Static rule matched.",
            "snippet": "return 42",
        }])

    report = supervise_review(str(target))

    assert report["security"]["source_truncated"] is security_clipped
    assert report["security"]["inconclusive"] is security_clipped
    assert report["security"]["logic_review_status"] == ("inconclusive" if security_clipped else "clean")
    assert report["architecture"]["source_truncated"] is architecture_clipped
    assert report["architecture"]["inconclusive"] is architecture_clipped
    # The architecture window is smaller, so it clips in every incomplete case here.
    assert report["summary"]["inconclusive"] is architecture_clipped
    assert report["summary"]["no_issues"] is (not architecture_clipped and not with_static_finding)
    assert report["summary"]["accepted_count"] == (1 if with_static_finding else 0)
    if with_static_finding:
        finding = report["security"]["accepted"][0]["structured_finding"]
        assert finding["finding_type"] == "test.static-match"
        assert finding["detection_method"] == "static_rule"


@pytest.mark.parametrize("review", [review_code, review_architecture])
@pytest.mark.parametrize("response,reason", [
    (_response("not JSON"), "invalid model response"),
    (LLMRateLimitedError("rate limited"), "rate limited"),
    (RuntimeError("provider down"), "could not complete"),
])
def test_clipped_source_does_not_hide_model_failure(
    target: Path, monkeypatch, review, response, reason: str,
) -> None:
    target.write_text("#" + "x" * 12000, encoding="utf-8")

    def chat(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("app.agent.chat", chat)
    monkeypatch.setattr("app.workers.architecture_worker.chat", chat)

    report = review(str(target))

    assert report["source_truncated"] is True
    assert report["source_truncated_note"]
    assert report["inconclusive"] is True
    assert report["no_issues"] is False
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["tool_call_failures"] == 1
    assert reason in report["message"]
