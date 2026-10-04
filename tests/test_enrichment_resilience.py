"""Keep detector results when external enrichment cannot complete."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from openai import BadRequestError, RateLimitError

from app.agent import review_code
from app.benchmark_run import run_benchmark
from app.supervisor import supervise_review


_CLEAN_LOGIC = {"has_issues": False, "summary": "No additional issues.", "issues": []}
_SYNTHESIS = {"explanation": "Static rule found shell execution.", "suggested_fix": "Use an argument list."}


@pytest.fixture
def script_provider(monkeypatch):
    """Script only the external completion client, not the review pipeline."""
    scripts = []

    def install(responses):
        scripted = iter(responses)
        unexpected_calls = []
        scripts.append((scripted, unexpected_calls))

        class ScriptedClient:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=self)

            def create(self, **kwargs):
                try:
                    response = next(scripted)
                except StopIteration:
                    unexpected_calls.append(True)
                    raise RuntimeError("Unexpected offline provider request") from None
                if isinstance(response, Exception):
                    raise response
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps(response), tool_calls=None,
                ))])

        monkeypatch.setattr("app.llm.OpenAI", ScriptedClient)

    yield install
    # Assert outside production's error handlers: script bugs are not outages.
    for scripted, unexpected_calls in scripts:
        assert not unexpected_calls, "Review exceeded the external provider script"
        assert not list(scripted), "Review did not consume the external provider script"


@pytest.fixture
def scanned_target(tmp_path: Path, monkeypatch) -> Path:
    target = tmp_path / "bug.py"
    target.write_text("import subprocess\nsubprocess.run(command, shell=True)\n", encoding="utf-8")
    scanner_output = json.dumps({"results": [{
        "check_id": "subprocess-shell-true", "path": str(target),
        "start": {"line": 2}, "end": {"line": 2},
        "extra": {"severity": "ERROR", "message": "Shell injection"},
    }]})
    monkeypatch.setattr("app.scanner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr(
        "app.scanner.subprocess.run",
        lambda command, **kw: subprocess.CompletedProcess(command, 0, scanner_output, ""),
    )
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "hooks.jsonl")
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", tmp_path / "reviews.sqlite3")
    monkeypatch.setattr("app.memory._DEFAULT_DB_PATH", tmp_path / "memory")
    # A pre-seeded external database avoids embeddings and lesson writes.
    collection = SimpleNamespace(count=lambda: 1)
    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(
        PersistentClient=lambda **kw: SimpleNamespace(get_or_create_collection=lambda **kw: collection),
    ))
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "offline-test-key")
    return target


def test_provider_outage_preserves_static_finding(scanned_target: Path, monkeypatch) -> None:
    class UnavailableClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            raise RuntimeError("scripted provider unavailable")

    monkeypatch.setattr("app.llm.OpenAI", UnavailableClient)

    report = review_code(str(scanned_target), memory_enabled=False)

    assert len(report["accepted"]) == 1
    assert report["accepted"][0]["structured_finding"]["finding_type"] == "subprocess-shell-true"
    assert report["inconclusive"] is True
    assert report["no_issues"] is False
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["accepted"][0]["web_worker"]["error"] == "Enrichment provider request failed."
    assert report["tool_call_failures"] > 0


@pytest.mark.parametrize("worker", ["web", "memory"])
@pytest.mark.parametrize("failure", ["outage", "rate_limit", "rejection"])
def test_enrichment_only_failure_marks_review_incomplete(
    scanned_target: Path, script_provider, worker: str, failure: str,
) -> None:
    error = RuntimeError("private-provider-error-canary")
    attempts = 1
    if failure in {"rate_limit", "rejection"}:
        response = httpx.Response(
            429 if failure == "rate_limit" else 400,
            request=httpx.Request("POST", "https://provider.invalid/completions"),
        )
        error_type = RateLimitError if failure == "rate_limit" else BadRequestError
        error = error_type("private-provider-error-canary", response=response, body=None)
        attempts = 2 if failure == "rejection" else 1
    script_provider([
        _CLEAN_LOGIC,
        {"use_memory": worker == "memory", "use_web": worker == "web"},
        *([error] * attempts),
        _SYNTHESIS,
    ])

    report = review_code(str(scanned_target), memory_enabled=worker == "memory", max_iterations=2)

    assert len(report["accepted"]) == 1
    assert report["logic_review_status"] == "clean"
    assert report["inconclusive"] is True
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert "enrichment" in report["message"].lower()
    assert report["accepted"][0][f"{worker}_worker"]["error"]
    assert report["accepted"][0]["enrichment_inconclusive"] is True
    assert report["tool_call_failures"] == attempts
    assert "private-provider-error-canary" not in json.dumps(report)


def test_synthesis_failure_preserves_finding_without_exposing_provider_error(
    scanned_target: Path, script_provider,
) -> None:
    script_provider([
        _CLEAN_LOGIC,
        {"use_memory": False, "use_web": False},
        RuntimeError("private-synthesis-error-canary"),
    ])

    report = review_code(str(scanned_target), memory_enabled=False)

    assert len(report["accepted"]) == 1
    assert report["logic_review_status"] == "clean"
    assert report["inconclusive"] is True
    assert report["review_result"]["coverage_status"] == "inconclusive"
    assert report["accepted"][0]["suggested_fix"] == ""
    assert report["accepted"][0]["explanation"] == "Finding synthesis could not complete."
    assert "private-synthesis-error-canary" not in json.dumps(report)


@pytest.mark.parametrize("worker", ["web", "memory"])
def test_recovered_tool_request_rejection_is_complete(
    scanned_target: Path, script_provider, worker: str,
) -> None:
    error = BadRequestError("invalid tool call", response=httpx.Response(
        400, request=httpx.Request("POST", "https://provider.invalid/completions"),
    ), body=None)
    script_provider([
        _CLEAN_LOGIC,
        {"use_memory": worker == "memory", "use_web": worker == "web"},
        error,
        {"searched": False, "relevant": False, "worth_reporting": False},
        _SYNTHESIS,
    ])

    report = review_code(str(scanned_target), memory_enabled=worker == "memory", max_iterations=2)

    assert len(report["accepted"]) == 1
    assert report["tool_call_failures"] == 1
    assert report["inconclusive"] is False
    assert report["review_result"]["coverage_status"] == "ok"
    assert report["accepted"][0]["enrichment_inconclusive"] is False


def test_routing_fallback_can_complete_with_memory_disabled(scanned_target: Path, script_provider) -> None:
    script_provider([
        _CLEAN_LOGIC,
        RuntimeError("routing unavailable"),
        {"searched": False, "relevant": False},
        _SYNTHESIS,
    ])

    report = review_code(str(scanned_target), memory_enabled=False)

    assert len(report["accepted"]) == 1
    assert report["tool_call_failures"] == 1
    assert report["inconclusive"] is False
    assert report["review_result"]["coverage_status"] == "ok"
    assert report["accepted"][0]["memory_worker"]["disabled"] is True


def test_supervisor_retains_finding_and_incomplete_coverage(scanned_target: Path, script_provider) -> None:
    script_provider([
        _CLEAN_LOGIC,
        {"use_memory": False, "use_web": True},
        RuntimeError("provider unavailable"),
        _SYNTHESIS,
    ])

    report = supervise_review(str(scanned_target), memory_enabled=False, run_architecture=False)

    assert report["summary"]["accepted_count"] == 1
    assert report["summary"]["inconclusive"] is True
    assert report["summary"]["no_issues"] is False
    assert report["security"]["review_result"]["coverage_status"] == "inconclusive"
    assert report["persisted_review_ids"]["security"] > 0


def test_benchmark_json_keeps_static_yield_from_failed_enrichment(
    scanned_target: Path, script_provider, tmp_path: Path,
) -> None:
    script_provider([
        _CLEAN_LOGIC,
        {"use_memory": False, "use_web": True},
        RuntimeError("provider unavailable"),
        _SYNTHESIS,
    ])
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text(json.dumps({"fixtures": {
        str(scanned_target): [{"finding_type": "command_injection"}],
    }}), encoding="utf-8")

    payload = run_benchmark(ground_truth_path=gt_path, results_dir=tmp_path, label="enrichment-outage")

    assert len(payload["predictions"]) == 1
    assert payload["per_file"][0]["accepted_count"] == 1
    assert payload["evaluation"]["fixtures"] == {
        "requested": 1, "completed": 0, "inconclusive": 1, "errored": 0, "unknown": 0,
    }
    assert payload["evaluation"]["status"] == "invalid"
    assert payload["evaluation"]["conditional"]["recall"]["value"] is None
    assert payload["evaluation"]["detection_yield"] == {"numerator": 1, "denominator": 1, "value": 1.0}
    saved = json.loads(next(tmp_path.glob("enrichment-outage_*.json")).read_text(encoding="utf-8"))
    assert saved["predictions"] == payload["predictions"]
    assert saved["evaluation"] == payload["evaluation"]
