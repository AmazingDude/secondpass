"""Cross-worker success requires completed review coverage."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.benchmark_run import run_benchmark
from app.benchmark_run_architecture import run_architecture_benchmark
from app.benchmark_cross_worker import run_cross_worker_bleed_checks


_CLEAN_ARCHITECTURE_FIXTURE = "benchmark/fixtures/architecture/clean_price_formatter.py"


@pytest.fixture
def clean_control_ground_truth(tmp_path: Path) -> Path:
    path = tmp_path / "ground_truth.json"
    path.write_text(json.dumps({"fixtures": {_CLEAN_ARCHITECTURE_FIXTURE: []}}), encoding="utf-8")
    return path


@pytest.fixture
def offline_boundaries(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "offline-test-key")
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "hooks.jsonl")
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", tmp_path / "reviews.sqlite3")
    monkeypatch.setattr("app.memory._DEFAULT_DB_PATH", tmp_path / "memory")
    collection = SimpleNamespace(count=lambda: 1)
    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(
        PersistentClient=lambda **kw: SimpleNamespace(get_or_create_collection=lambda **kw: collection),
    ))
    monkeypatch.setattr("app.scanner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("app.scanner.subprocess.run", lambda command, **kw:
        subprocess.CompletedProcess(command, 0, '{"results": []}', ""))

    def provider_content(content: str):
        class ScriptedClient:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=self)

            def create(self, **kwargs):
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=content, tool_calls=None,
                ))])

        monkeypatch.setattr("app.llm.OpenAI", ScriptedClient)

    return provider_content


def test_security_benchmark_rejects_inconclusive_cross_worker_control(
    tmp_path: Path, clean_control_ground_truth: Path, offline_boundaries, capsys,
) -> None:
    offline_boundaries("not a valid review")

    with pytest.raises(RuntimeError, match="cross-worker Security review is inconclusive"):
        run_benchmark(ground_truth_path=clean_control_ground_truth, results_dir=tmp_path, label="failed-cross-worker")

    saved = json.loads(next(tmp_path.glob("failed-cross-worker_*.json")).read_text(encoding="utf-8"))
    assert saved["evaluation"]["status"] == "invalid"
    assert saved["per_file"][0]["coverage_status"] == "inconclusive"
    assert "ok clean:" not in capsys.readouterr().out


def test_architecture_inconclusive_rerun_preserves_both_results(
    tmp_path: Path, clean_control_ground_truth: Path, offline_boundaries,
) -> None:
    offline_boundaries("not a valid review")

    for _ in range(2):
        with pytest.raises(RuntimeError, match="cross-worker Architecture review is inconclusive"):
            run_architecture_benchmark(
                ground_truth_path=clean_control_ground_truth,
                results_dir=tmp_path,
                label="architecture-rerun",
            )

    paths = list(tmp_path.glob("architecture-rerun_*.json"))
    assert len(paths) == 2
    assert all(json.loads(path.read_text(encoding="utf-8"))["evaluation"]["status"] == "invalid" for path in paths)


@pytest.mark.parametrize("relative_ground_truth", [False, True])
def test_architecture_result_records_inputs_even_when_inconclusive(
    tmp_path: Path, clean_control_ground_truth: Path, offline_boundaries,
    monkeypatch, relative_ground_truth: bool,
) -> None:
    offline_boundaries("not a valid review")
    if relative_ground_truth:
        monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="cross-worker Architecture review is inconclusive"):
        run_architecture_benchmark(
            ground_truth_path=Path("ground_truth.json") if relative_ground_truth else clean_control_ground_truth,
            results_dir=tmp_path,
            label="architecture-inputs",
        )

    saved = json.loads(next(tmp_path.glob("architecture-inputs_*.json")).read_text(encoding="utf-8"))
    rows = saved["input_manifest"]["inputs"]
    assert rows == [
        {"role": "ground_truth", "path": str(clean_control_ground_truth), "status": "present", "sha256": hashlib.sha256(clean_control_ground_truth.read_bytes()).hexdigest()},
        {"role": "source", "path": _CLEAN_ARCHITECTURE_FIXTURE, "status": "present", "sha256": hashlib.sha256((Path(__file__).resolve().parents[1] / _CLEAN_ARCHITECTURE_FIXTURE).read_bytes()).hexdigest()},
    ]


def test_legacy_unknown_coverage_cannot_pass_security_cross_worker_check(
    tmp_path: Path, clean_control_ground_truth: Path, offline_boundaries, monkeypatch, capsys,
) -> None:
    offline_boundaries("legacy control must not need a model response")
    # Explicitly agreed exception: the current pipeline cannot emit a legacy report.
    monkeypatch.setattr("app.agent.review_code", lambda path: {
        "accepted": [], "needs_review": [], "inconclusive": False,
    })

    with pytest.raises(RuntimeError, match="cross-worker Security review is unknown"):
        run_benchmark(ground_truth_path=clean_control_ground_truth, results_dir=tmp_path, label="legacy-unknown")

    saved = json.loads(next(tmp_path.glob("legacy-unknown_*.json")).read_text(encoding="utf-8"))
    assert saved["evaluation"]["fixtures"]["unknown"] == 1
    assert saved["evaluation"]["fixtures"]["completed"] == 0
    assert saved["per_file"][0]["coverage_status"] is None
    assert "ok clean:" not in capsys.readouterr().out


def test_failed_cross_worker_check_preserves_valid_static_findings(
    tmp_path: Path, offline_boundaries, monkeypatch,
) -> None:
    offline_boundaries("not a valid review")
    bug = tmp_path / "shell_bug.py"
    bug.write_text("import subprocess\nsubprocess.run(command, shell=True)\n", encoding="utf-8")
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text(json.dumps({"fixtures": {
        str(bug): [{"finding_type": "command_injection"}],
        _CLEAN_ARCHITECTURE_FIXTURE: [],
    }}), encoding="utf-8")

    def scanner_response(command, **kwargs):
        findings = [{
            "check_id": "subprocess-shell-true", "path": str(bug),
            "start": {"line": 2}, "end": {"line": 2},
            "extra": {"severity": "ERROR", "message": "Shell injection"},
        }] if Path(command[-1]) == bug else []
        return subprocess.CompletedProcess(command, 0, json.dumps({"results": findings}), "")

    monkeypatch.setattr("app.scanner.subprocess.run", scanner_response)

    with pytest.raises(RuntimeError, match="cross-worker Security review is inconclusive"):
        run_benchmark(ground_truth_path=gt_path, results_dir=tmp_path, label="retained-finding")

    saved = json.loads(next(tmp_path.glob("retained-finding_*.json")).read_text(encoding="utf-8"))
    assert len(saved["predictions"]) == 1
    assert saved["predictions"][0]["file_path"] == str(bug)
    assert saved["predictions"][0]["finding_type"] == "command_injection"
    assert saved["predictions"][0]["detection_method"] == "static_rule"
    assert saved["evaluation"]["detection_yield"] == {"numerator": 1, "denominator": 1, "value": 1.0}
    assert saved["evaluation"]["fixtures"]["completed"] == 0
    bug_result = next(row for row in saved["per_file"] if row["file_path"] == str(bug))
    assert bug_result["accepted_count"] == 1


def test_architecture_benchmark_rejects_inconclusive_cross_worker_control(
    tmp_path: Path, clean_control_ground_truth: Path, offline_boundaries, capsys,
) -> None:
    offline_boundaries("not a valid review")

    with pytest.raises(RuntimeError, match="cross-worker Architecture review is inconclusive"):
        run_architecture_benchmark(ground_truth_path=clean_control_ground_truth, results_dir=tmp_path, label="failed-architecture")

    saved = json.loads(next(tmp_path.glob("failed-architecture_*.json")).read_text(encoding="utf-8"))
    assert saved["evaluation"]["status"] == "invalid"
    assert "ok no-authz-bleed:" not in capsys.readouterr().out


def test_standing_cross_worker_check_rejects_inconclusive_review(offline_boundaries) -> None:
    offline_boundaries("not a valid review")

    with pytest.raises(RuntimeError, match="cross-worker Security review is inconclusive"):
        run_cross_worker_bleed_checks(live=True)


@pytest.mark.parametrize("suite", ["security", "architecture"])
def test_benchmark_does_not_call_unverified_claims_clean(
    tmp_path: Path, clean_control_ground_truth: Path, offline_boundaries, capsys, suite: str,
) -> None:
    offline_boundaries(json.dumps({
        "has_issues": True, "summary": "A layering concern was claimed.",
        "issues": [{
            "finding_type": "layering_violation", "message": "Forbidden dependency direction",
            "evidence": "depends upward", "line": 1,
            "suggested_fix": "Invert the dependency",
        }] if suite == "security" else [],
    }))

    runner = run_benchmark if suite == "security" else run_architecture_benchmark
    worker_name = "Security" if suite == "security" else "Architecture"
    with pytest.raises(RuntimeError, match=f"cross-worker {worker_name} review has unverified claims"):
        runner(ground_truth_path=clean_control_ground_truth, results_dir=tmp_path, label="filtered-claim")

    saved = json.loads(next(tmp_path.glob("filtered-claim_*.json")).read_text(encoding="utf-8"))
    if suite == "security":
        assert saved["per_file"][0]["claim_unverified"] is True
    assert saved["per_file"][0]["accepted_count"] == 0
    output = capsys.readouterr().out
    assert "ok clean:" not in output
    assert "ok no-authz-bleed:" not in output


@pytest.mark.parametrize("suite", ["security", "architecture", "standing"])
def test_cli_does_not_return_success_for_inconclusive_cross_worker_check(
    tmp_path: Path, clean_control_ground_truth: Path, offline_boundaries, monkeypatch, capsys, suite: str,
) -> None:
    offline_boundaries("not a valid review")
    if suite == "standing":
        from app.benchmark_cross_worker import main
        monkeypatch.setattr(sys, "argv", ["cross-worker", "--live"])
        with pytest.raises(RuntimeError, match="cross-worker Security review is inconclusive"):
            main()
    else:
        from app import benchmark_run, benchmark_run_architecture
        runner = benchmark_run if suite == "security" else benchmark_run_architecture
        monkeypatch.setattr(runner, "_RESULTS_DIR", tmp_path)
        with pytest.raises(RuntimeError, match="cross-worker .* review is inconclusive"):
            runner.main(["--ground-truth", str(clean_control_ground_truth), "--label", "cli-incomplete"])
        assert len(list(tmp_path.glob("cli-incomplete_*.json"))) == 1
    output = capsys.readouterr().out
    assert "ok clean:" not in output
    assert "ok no-authz-bleed:" not in output


def test_architecture_cli_fails_when_scored_fixture_is_missing(
    tmp_path: Path, offline_boundaries, monkeypatch, capsys,
) -> None:
    from app import benchmark_run_architecture

    offline_boundaries(json.dumps({"has_issues": False, "summary": "Clean control.", "issues": []}))
    ground_truth = tmp_path / "ground_truth.json"
    ground_truth.write_text(json.dumps({"fixtures": {
        str(tmp_path / "missing.py"): [{"finding_type": "layering_violation"}],
    }}), encoding="utf-8")
    results = tmp_path / "results"
    monkeypatch.setattr(benchmark_run_architecture, "_RESULTS_DIR", results)

    exit_code = benchmark_run_architecture.main([
        "--ground-truth", str(ground_truth), "--label", "missing-scored",
    ])

    saved = json.loads(next(results.glob("missing-scored_*.json")).read_text(encoding="utf-8"))
    assert exit_code == 1
    assert saved["evaluation"]["status"] == "invalid"
    assert saved["evaluation"]["fixtures"]["errored"] == 1
    assert "ok no-authz-bleed:" in capsys.readouterr().out


def test_architecture_cli_succeeds_when_scored_and_cross_worker_reviews_complete(
    tmp_path: Path, clean_control_ground_truth: Path, offline_boundaries, monkeypatch,
) -> None:
    from app import benchmark_run_architecture

    offline_boundaries(json.dumps({"has_issues": False, "summary": "Clean control.", "issues": []}))
    results = tmp_path / "results"
    monkeypatch.setattr(benchmark_run_architecture, "_RESULTS_DIR", results)

    exit_code = benchmark_run_architecture.main([
        "--ground-truth", str(clean_control_ground_truth), "--label", "completed",
    ])

    saved = json.loads(next(results.glob("completed_*.json")).read_text(encoding="utf-8"))
    assert exit_code == 0
    assert saved["evaluation"]["status"] == "complete"


@pytest.mark.parametrize("suite", ["security", "architecture", "standing"])
def test_completed_clean_cross_worker_controls_still_pass(
    tmp_path: Path, clean_control_ground_truth: Path, offline_boundaries, capsys, suite: str,
) -> None:
    offline_boundaries(json.dumps({"has_issues": False, "summary": "Clean control.", "issues": []}))
    if suite == "standing":
        payload = run_cross_worker_bleed_checks(live=True)
        assert payload["status"] == "ok"
        assert len(payload["security_on_architecture"]) == 6
        assert len(payload["architecture_on_security"]) == 5
    else:
        runner = run_benchmark if suite == "security" else run_architecture_benchmark
        payload = runner(ground_truth_path=clean_control_ground_truth, results_dir=tmp_path, label="completed")
        assert payload["evaluation"]["status"] == "complete"
        expected_label = "ok clean:" if suite == "security" else "ok no-authz-bleed:"
        assert expected_label in capsys.readouterr().out
