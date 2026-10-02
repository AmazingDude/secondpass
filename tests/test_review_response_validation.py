"""Incomplete reviews must remain visible through the public review interfaces."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import review_architecture, review_changed_files, review_code
from app.gitdiff import ChangedFile
from app.llm import LLMRateLimitedError
from app.scanner import ScanError
from app.supervisor import supervise_review


def _response(content: str | None, *, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, refusal=None),
        finish_reason=finish_reason,
    )])


def test_assisted_review_keeps_valid_static_match_when_scan_data_is_incomplete(
    target: Path, monkeypatch,
) -> None:
    from app.scanner import run_static_scan

    monkeypatch.setattr("app.agent.run_static_scan", run_static_scan)
    monkeypatch.setattr("app.scanner.subprocess.run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stderr="", stdout=json.dumps({"results": [{
            "check_id": "test.static-match", "path": str(target),
            "start": {"line": 2}, "end": {"line": 2},
            "extra": {"severity": "WARNING", "message": "Static rule matched."},
        }, None]}),
    ))
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: _response(
        '{"has_issues": false, "summary": "No additional issues.", "issues": []}'
    ))
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, '
        '"explanation": "Inspect this static match.", "suggested_fix": "Review in context."}'
    ))

    report = review_code(str(target), memory_enabled=False)

    assert report["finding_count"] == 1
    assert report["all_findings"][0]["finding"]["rule_id"] == "test.static-match"
    assert report["inconclusive"] is True
    assert report["no_issues"] is False
    assert report["review_result"]["coverage_status"] == "inconclusive"


@pytest.fixture()
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "service.py"
    path.write_text("def answer():\n    return 42\n", encoding="utf-8")
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", tmp_path / "reviews.db")
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "tools.log")
    monkeypatch.setattr("app.memory._DEFAULT_DB_PATH", tmp_path / "chroma")
    collection = SimpleNamespace(count=lambda: 1)
    monkeypatch.setattr(
        "chromadb.PersistentClient",
        lambda **kwargs: SimpleNamespace(get_or_create_collection=lambda **kw: collection),
    )
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    return path


@pytest.fixture()
def model_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr("app.workers.architecture_worker.chat", lambda *a, **kw: calls.append(a))
    return calls


def _deny_target_read(target: Path, monkeypatch) -> None:
    original_read = Path.read_text

    def denied_read(path, *args, **kwargs):
        if path == target:
            raise PermissionError("permission denied")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied_read)


@pytest.mark.parametrize("review", [review_code, review_architecture])
@pytest.mark.parametrize("read_error", ["missing", "permission", "encoding"])
def test_unreadable_source_is_incomplete(target: Path, monkeypatch, model_calls, review, read_error) -> None:
    if read_error == "missing":
        target = target.with_name("missing.py")
    elif read_error == "encoding":
        target.write_bytes(b"\xff\xfe\x00")
    else:
        _deny_target_read(target, monkeypatch)

    report = review(str(target))

    assert report["no_issues"] is False
    assert report["inconclusive"] is True
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["finding_count"] == 0
    if review is review_code:
        assert report["used_logic_review"] is False
    assert report["tool_call_failures"] == 0
    assert "source" in report["message"].lower()
    assert model_calls == []


@pytest.mark.parametrize("review", [review_code, review_architecture])
@pytest.mark.parametrize("source", ["", " \n\t", " " * 12001], ids=["empty", "whitespace", "long-whitespace"])
def test_readable_empty_source_needs_no_model(target: Path, model_calls, review, source) -> None:
    target.write_text(source, encoding="utf-8")

    report = review(str(target))

    assert report["no_issues"] is True
    assert report["inconclusive"] is False
    assert report["review_result"]["coverage_status"] == "ok"
    assert report["source_truncated"] is False
    assert report["tool_call_failures"] == 0
    assert report["message"] == "No source content to review."
    assert model_calls == []


@pytest.mark.parametrize("diff", [False, True])
def test_cli_displays_unreadable_source_as_incomplete(target: Path, monkeypatch, model_calls, diff: bool) -> None:
    from typer.testing import CliRunner

    from app import cli

    if diff:
        _deny_target_read(target, monkeypatch)
    else:
        target.write_bytes(b"\xff\xfe\x00")
    if diff:
        monkeypatch.chdir(target.parent)
        subprocess.run(["git", "init", "--quiet"], check=True)
        subprocess.run(["git", "add", "--", target.name], check=True)
    args = ["review", "--diff"] if diff else ["review", str(target)]

    result = CliRunner().invoke(cli.app, args)

    assert result.exit_code == 0, result.output
    assert "Review incomplete" in result.output
    assert "No issues found" not in result.output
    rendered_text = " ".join(result.output.replace("│", " ").split())
    assert "could not read target source" in rendered_text
    assert model_calls == []


@pytest.mark.parametrize("filename", ["asset.bin", "asset with spaces.bin", "café.bin"])
@pytest.mark.parametrize("remove_after_staging", [False, True])
def test_cli_reports_binary_diff_as_unreviewed(
    target: Path, monkeypatch, model_calls, filename, remove_after_staging: bool,
) -> None:
    from typer.testing import CliRunner

    from app import cli

    target = target.with_name(filename)
    target.write_bytes(b"\x00binary content")
    monkeypatch.chdir(target.parent)
    subprocess.run(["git", "init", "--quiet"], check=True)
    subprocess.run(["git", "add", "--", target.name], check=True)
    if remove_after_staging:
        target.unlink()
    scan_calls = []
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: scan_calls.append(paths) or [])

    result = CliRunner().invoke(cli.app, ["review", "--diff"])

    assert result.exit_code == 0, result.output
    assert "Review incomplete" in result.output
    assert "binary" in result.output.lower()
    assert "Clean result" not in result.output
    assert "No issues found" not in result.output
    assert scan_calls == []
    assert model_calls == []


@pytest.mark.parametrize("with_finding", [False, True])
def test_mixed_diff_preserves_text_results_and_binary_coverage(
    target: Path, monkeypatch, static_match, with_finding: bool,
) -> None:
    binary = target.with_name("asset.bin")
    binary.write_bytes(b"\x00binary content")
    scanned = []

    def scan(paths):
        scanned.extend(paths)
        return static_match if with_finding else []

    monkeypatch.setattr("app.agent.run_static_scan", scan)
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: _response(
        '{"has_issues": false, "summary": "No issues.", "issues": []}'
    ))
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, "explanation": "Static rule matched."}'
    ))

    report = review_changed_files([
        ChangedFile(binary, is_binary=True), ChangedFile(target, [(2, 2)])
    ])

    assert report["no_issues"] is False
    assert report["inconclusive"] is True
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["accepted_count"] == int(with_finding)
    assert report["tool_call_failures"] == 0
    assert str(binary) in report["message"]
    assert "binary file not reviewed" in report["message"]
    assert scanned == [str(target)]


@pytest.mark.parametrize("with_finding", [False, True])
def test_cli_preserves_text_result_alongside_binary_warning(
    target: Path, monkeypatch, static_match, with_finding: bool,
) -> None:
    from typer.testing import CliRunner

    from app import cli

    binary = target.with_name("café.bin")
    binary.write_bytes(b"\x00binary content")
    monkeypatch.chdir(target.parent)
    subprocess.run(["git", "init", "--quiet"], check=True)
    subprocess.run(["git", "add", "--", target.name, binary.name], check=True)
    scanned = []

    def scan(paths):
        scanned.extend(paths)
        return static_match if with_finding else []

    monkeypatch.setattr("app.agent.run_static_scan", scan)
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: _response(
        '{"has_issues": false, "summary": "No issues.", "issues": []}'
    ))
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, "explanation": "Static rule matched."}'
    ))

    result = CliRunner().invoke(cli.app, ["review", "--diff"])

    assert result.exit_code == 0, result.output
    assert ("test.static-match" in result.output) == with_finding
    assert "Coverage: inconclusive" in result.output
    assert "binary" in result.output.lower()
    assert "logic review inconclusive" not in result.output
    assert "No issues found" not in result.output
    assert scanned == [str(target)]


@pytest.mark.parametrize("selection", ["empty", "deleted", "deleted_binary"])
def test_cli_does_not_call_an_empty_review_clean(target: Path, monkeypatch, model_calls, selection) -> None:
    from typer.testing import CliRunner

    from app import cli

    monkeypatch.chdir(target.parent)
    subprocess.run(["git", "init", "--quiet"], check=True)
    if selection != "empty":
        if selection == "deleted_binary":
            target.write_bytes(b"\x00binary content")
        subprocess.run(["git", "add", "--", target.name], check=True)
        subprocess.run([
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "--quiet", "--no-gpg-sign", "-m", "Initial fixture",
        ], check=True)
        target.unlink()
        subprocess.run(["git", "add", "--", target.name], check=True)

    result = CliRunner().invoke(cli.app, ["review", "--diff"])

    assert result.exit_code == 0, result.output
    assert "Nothing reviewed" in result.output
    assert "Clean result" not in result.output
    assert "No issues found" not in result.output
    assert model_calls == []


@pytest.mark.parametrize("with_static_finding", [False, True])
@pytest.mark.parametrize("static_error", [False, True])
def test_supervisor_keeps_read_failure_and_static_results(
    target: Path, monkeypatch, static_match, model_calls,
    with_static_finding: bool, static_error: bool,
) -> None:
    target.write_bytes(b"\xff")
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, "explanation": "Static rule matched."}'
    ))

    def scan(paths):
        if static_error:
            raise ScanError("Semgrep unavailable")
        return static_match if with_static_finding else []

    monkeypatch.setattr("app.agent.run_static_scan", scan)

    report = supervise_review(str(target))

    assert report["summary"]["no_issues"] is False
    assert report["summary"]["inconclusive"] is True
    assert report["summary"]["accepted_count"] == int(with_static_finding and not static_error)
    for worker in ["security", "architecture"]:
        assert report[worker]["review_result"]["coverage_status"] == "inconclusive"
        assert "could not read target source" in report[worker]["message"]
    if static_error:
        assert "static scan could not complete" in report["security"]["message"]
    assert model_calls == []


def test_changed_files_keep_read_failure_with_static_findings(target: Path, monkeypatch, static_match) -> None:
    target.write_bytes(b"\xff")
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: static_match)
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, "explanation": "Static rule matched."}'
    ))

    report = review_changed_files([ChangedFile(target, [(2, 2)])])

    assert report["no_issues"] is False
    assert report["inconclusive"] is True
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["accepted_count"] == 1
    assert report["all_findings"][0]["structured_finding"]["finding_type"] == "test.static-match"
    assert "could not read target source" in report["message"]


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


@pytest.fixture()
def filtered_security_target(target: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: _response(json.dumps({
        "has_issues": True,
        "summary": "Handler bypasses the service layer.",
        "issues": [{
            "line": 2,
            "finding_type": "layering_violation",
            "message": "Handler bypasses the service layer.",
            "snippet": "return 42",
            "confidence": 90,
            "suggested_fix": "Move business rules into the service layer.",
        }],
    })))
    monkeypatch.setattr("app.workers.architecture_worker.chat", lambda *a, **kw: _response(
        '{"has_issues": false, "summary": "No architecture issues.", "issues": []}'
    ))
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, "explanation": "Static rule matched."}'
    ))
    return target


def test_filtered_security_claim_is_not_clean(filtered_security_target: Path) -> None:
    report = review_code(str(filtered_security_target))

    assert report["no_issues"] is False
    assert report["claim_unverified"] is True
    assert report["logic_review_status"] == "unverified"
    assert report["inconclusive"] is False
    assert report["review_result"]["claim_status"] == "unverified"
    assert report["review_result"]["coverage_status"] == "ok"
    assert report["all_findings"] == []
    assert report["tool_call_failures"] == 0
    assert "unverified" in report["message"]


@pytest.fixture()
def static_match(target: Path) -> list[dict]:
    return [{
        "rule_id": "test.static-match", "severity": "WARNING",
        "path": str(target), "line": 2,
        "message": "Static rule matched.", "snippet": "return 42",
    }]


def test_filtered_security_claim_preserves_static_findings(
    filtered_security_target: Path, monkeypatch, static_match,
) -> None:
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: static_match)

    report = review_code(str(filtered_security_target))

    assert report["claim_unverified"] is True
    assert report["review_result"]["claim_status"] == "unverified"
    assert report["accepted_count"] == 1
    assert report["needs_review_count"] == 0
    assert report["all_findings"][0]["structured_finding"]["finding_type"] == "test.static-match"
    assert "unverified" in report["message"]


def test_supervisor_preserves_filtered_security_claim(filtered_security_target: Path) -> None:
    report = supervise_review(str(filtered_security_target))

    assert report["summary"]["no_issues"] is False
    assert report["summary"]["claim_unverified"] is True
    assert report["summary"]["inconclusive"] is False
    assert report["summary"]["finding_count"] == 0


def test_changed_files_preserve_filtered_security_claim(filtered_security_target: Path) -> None:
    report = review_changed_files([ChangedFile(filtered_security_target, [(2, 2)])])

    assert report["no_issues"] is False
    assert report["claim_unverified"] is True
    assert report["review_result"]["claim_status"] == "unverified"
    assert report["review_result"]["coverage_status"] == "ok"
    assert report["finding_count"] == 0
    assert "unverified" in report["message"]


@pytest.mark.parametrize("diff", [False, True])
@pytest.mark.parametrize("with_static_finding", [False, True])
def test_cli_does_not_display_filtered_security_claim_as_clean(
    filtered_security_target: Path, monkeypatch, static_match,
    diff: bool, with_static_finding: bool,
) -> None:
    from typer.testing import CliRunner

    from app import cli

    if with_static_finding:
        monkeypatch.setattr("app.agent.run_static_scan", lambda paths: static_match)
    if diff:
        monkeypatch.chdir(filtered_security_target.parent)
        subprocess.run(["git", "init", "--quiet"], check=True)
        subprocess.run(["git", "add", "--", filtered_security_target.name], check=True)
    args = ["review", "--diff"] if diff else ["review", str(filtered_security_target)]

    result = CliRunner().invoke(cli.app, args)

    assert result.exit_code == 0, result.output
    security_output = result.output.split("secondpass review", 1)[1].split("Architecture review", 1)[0]
    assert "Evidence bar not met" in security_output
    assert "No issues found" not in security_output
    if with_static_finding:
        assert "test.static-match" in security_output


@pytest.mark.parametrize("incomplete_stage", ["source", "static"])
def test_filtered_security_claim_keeps_coverage_independent(
    filtered_security_target: Path, monkeypatch, incomplete_stage: str,
) -> None:
    if incomplete_stage == "source":
        filtered_security_target.write_text("#" * 12001, encoding="utf-8")
    else:
        def fail_scan(paths):
            raise ScanError("Semgrep unavailable")
        monkeypatch.setattr("app.agent.run_static_scan", fail_scan)

    report = review_code(str(filtered_security_target))

    assert report["no_issues"] is False
    assert report["claim_unverified"] is True
    assert report["review_result"]["claim_status"] == "unverified"
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert "inconclusive" in report["message"]
    assert "unverified" in report["message"]


@pytest.mark.parametrize("claim_filtered", [False, True])
@pytest.mark.parametrize("changed_line", [1, 2])
def test_changed_files_keep_claims_separate_from_out_of_diff_findings(
    filtered_security_target: Path, monkeypatch, static_match,
    claim_filtered: bool, changed_line: int,
) -> None:
    if not claim_filtered:
        monkeypatch.setattr("app.agent.chat", lambda *a, **kw: _response(
            '{"has_issues": false, "summary": "No issues.", "issues": []}'
        ))
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: static_match)

    report = review_changed_files([
        ChangedFile(filtered_security_target, [(changed_line, changed_line)])
    ])

    assert report["claim_unverified"] is claim_filtered
    assert report["no_issues"] is (not claim_filtered and changed_line == 1)
    assert report["accepted_count"] == (1 if changed_line == 2 else 0)
    assert report["filtered_out_findings"] == (1 if changed_line == 1 else 0)
    if claim_filtered:
        assert "unverified" in report["message"]


@pytest.mark.parametrize("with_static_finding", [False, True])
def test_changed_files_preserve_failure_and_claim_explanations(
    target: Path, monkeypatch, static_match, with_static_finding: bool,
) -> None:
    failed_target = target.with_name("failed.py")
    failed_target.write_text("value = 1\n", encoding="utf-8")
    responses = iter([
        _response(json.dumps({
            "has_issues": True, "summary": "Possible issue.", "issues": [{
                "finding_type": "layering_violation",
                "message": "Handler bypasses the service layer.",
            }],
        })),
        _response("not JSON"),
    ])
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: next(responses))
    monkeypatch.setattr("app.supervisor.chat", lambda *a, **kw: _response(
        '{"use_memory": false, "use_web": false, "explanation": "Static rule matched."}'
    ))
    if with_static_finding:
        monkeypatch.setattr("app.agent.run_static_scan", lambda paths: (
            static_match if str(target) in paths else []
        ))

    report = review_changed_files([
        ChangedFile(target, [(2, 2)]), ChangedFile(failed_target, [(1, 1)])
    ])

    assert report["no_issues"] is False
    assert report["claim_unverified"] is True
    assert report["inconclusive"] is True
    assert report["accepted_count"] == int(with_static_finding)
    assert "invalid model response" in report["message"]
    assert "unverified" in report["message"]


@pytest.mark.parametrize("confidence,accepted,needs_review", [(90, 1, 0), (40, 0, 1)])
def test_surviving_security_finding_keeps_its_confidence_gate(
    filtered_security_target: Path, monkeypatch, confidence: int,
    accepted: int, needs_review: int,
) -> None:
    monkeypatch.setattr("app.agent.chat", lambda *a, **kw: _response(json.dumps({
        "has_issues": True, "summary": "Possible issues.", "issues": [
            {"finding_type": "layering_violation", "message": "Handler bypasses the service layer."},
            {"finding_type": "command_injection", "message": "Untrusted input reaches a shell.",
             "line": 2, "confidence": confidence, "suggested_fix": "Avoid the shell."},
        ],
    })))

    report = review_code(str(filtered_security_target))

    assert report["claim_unverified"] is False
    assert report["review_result"]["claim_status"] is None
    assert report["no_issues"] is False
    assert report["accepted_count"] == accepted
    assert report["needs_review_count"] == needs_review
    assert report["all_findings"][0]["structured_finding"]["finding_type"] == "command_injection"


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
