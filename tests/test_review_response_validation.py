"""Provider failures must remain visible through the public review interfaces."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import review_architecture, review_code
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


def test_security_invalid_json_is_inconclusive(target: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: _response("not JSON"))

    report = review_code(str(target))

    assert report["inconclusive"] is True
    assert report["no_issues"] is False
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["tool_call_failures"] == 1
    assert "invalid model response" in report["message"]


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
