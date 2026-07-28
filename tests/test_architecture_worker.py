"""Offline tests for the Architecture Worker (pure helpers + one mocked LLM path)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.schema import Finding
from app.workers.architecture_worker import _issue_to_finding, run_architecture_worker


def test_issue_to_finding_produces_schema_valid_finding() -> None:
    finding = _issue_to_finding(
        "app/agent.py",
        {
            "finding_type": "layering_violation",
            "confidence": 82,
            "location": "app/agent.py:40",
            "evidence": "agent.py imports app.cli directly",
            "message": "Core module depends on the CLI layer",
            "suggested_fix": "Invert the dependency; move shared logic out of cli.py",
        },
    )

    assert isinstance(finding, Finding)
    assert finding.finding_type == "layering_violation"
    assert finding.detection_method == "llm_reasoning"
    assert finding.confidence == 82
    assert "app/agent.py:40" in finding.evidence
    assert "agent.py imports app.cli directly" in finding.evidence


def test_issue_to_finding_clamps_out_of_range_confidence() -> None:
    finding = _issue_to_finding(
        "app/agent.py",
        {"message": "duplicated retry logic", "confidence": 250},
    )
    assert finding is not None
    assert finding.confidence == 100


def test_issue_to_finding_returns_none_when_ungrounded() -> None:
    finding = _issue_to_finding("app/agent.py", {"confidence": 90})
    assert finding is None


def test_empty_source_returns_clean_shape_without_llm_call(
    tmp_path: Path, monkeypatch
) -> None:
    empty_file = tmp_path / "empty.py"
    empty_file.write_text("   \n", encoding="utf-8")

    def _fail_chat(*args, **kwargs):
        raise AssertionError("chat() should not be called for empty source")

    monkeypatch.setattr("app.workers.architecture_worker.chat", _fail_chat)

    result = run_architecture_worker(str(empty_file))

    assert result["has_issues"] is False
    assert result["structured_findings"] == []
    assert result["context_files"] == []
    assert result["failures"] == 0


def _fake_response(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def test_run_architecture_worker_maps_mocked_llm_issues(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "service.py"
    target.write_text("def do_thing():\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *args, **kwargs: _fake_response(
            '{"has_issues": true, "summary": "1 layering issue", '
            '"issues": [{"finding_type": "layering_violation", "confidence": 88, '
            '"location": "service.py:1", "evidence": "imports UI module directly", '
            '"message": "service layer depends on UI", '
            '"suggested_fix": "invert the dependency"}]}'
        ),
    )

    result = run_architecture_worker(str(target))

    assert result["has_issues"] is True
    assert result["failures"] == 0
    assert len(result["structured_findings"]) == 1
    finding = result["structured_findings"][0]
    assert finding.finding_type == "layering_violation"
    assert finding.detection_method == "llm_reasoning"
    assert finding.confidence == 88


def test_run_architecture_worker_treats_ungrounded_claim_as_clean(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "service.py"
    target.write_text("def do_thing():\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *args, **kwargs: _fake_response(
            '{"has_issues": true, "summary": "vague", "issues": []}'
        ),
    )

    result = run_architecture_worker(str(target))

    assert result["has_issues"] is False
    assert result["structured_findings"] == []


def test_run_architecture_worker_treats_llm_error_as_clean(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "service.py"
    target.write_text("def do_thing():\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: [],
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("app.workers.architecture_worker.chat", _raise)

    result = run_architecture_worker(str(target))

    assert result["has_issues"] is False
    assert result["failures"] == 1
    assert result["structured_findings"] == []
