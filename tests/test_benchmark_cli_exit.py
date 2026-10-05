"""Benchmark CLI exit status reflects scored fixture coverage."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import benchmark_run, benchmark_run_real_world


@pytest.fixture
def script_scanner(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("app.scanner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "hooks.jsonl")

    def install(return_code: int) -> None:
        output = '{"results": []}' if return_code == 0 else ""
        monkeypatch.setattr(
            "app.scanner.subprocess.run",
            lambda command, **kw: subprocess.CompletedProcess(
                command, return_code, output, "scan unavailable" if return_code else "",
            ),
        )

    return install


def test_security_cli_fails_when_scored_fixture_is_missing(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    ground_truth = tmp_path / "ground_truth.json"
    ground_truth.write_text(json.dumps({"fixtures": {
        str(tmp_path / "missing.py"): [{"finding_type": "command_injection"}],
    }}), encoding="utf-8")
    results = tmp_path / "results"
    monkeypatch.setattr(benchmark_run, "_RESULTS_DIR", results)

    exit_code = benchmark_run.main([
        "--offline", "--ground-truth", str(ground_truth), "--label", "missing-scored",
    ])

    saved = json.loads(next(results.glob("missing-scored_*.json")).read_text(encoding="utf-8"))
    assert exit_code == 1
    assert saved["evaluation"]["status"] == "invalid"
    assert saved["evaluation"]["fixtures"]["errored"] == 1
    assert saved["score"]["false_negatives"] == 1
    assert "Evaluation: invalid" in capsys.readouterr().out


def test_real_world_cli_fails_when_static_scans_do_not_complete(
    tmp_path: Path, monkeypatch, capsys, script_scanner,
) -> None:
    results = tmp_path / "results"
    monkeypatch.setattr(benchmark_run_real_world, "_RESULTS_DIR", results)
    script_scanner(2)

    exit_code = benchmark_run_real_world.main(["--offline", "--label", "failed-scans"])

    saved = json.loads(next(results.glob("failed-scans_*.json")).read_text(encoding="utf-8"))
    assert exit_code == 1
    assert saved["evaluation"]["status"] == "invalid"
    assert saved["evaluation"]["fixtures"]["completed"] == 0
    assert "Evaluation: invalid" in capsys.readouterr().out


@pytest.mark.parametrize(
    "include_missing,expected_status,expected_exit",
    [(False, "complete", 0), (True, "incomplete", 1)],
)
def test_security_cli_exit_tracks_partial_and_complete_coverage(
    tmp_path: Path, monkeypatch, script_scanner, include_missing: bool,
    expected_status: str, expected_exit: int,
) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("print('clean')\n", encoding="utf-8")
    fixtures = {str(clean): []}
    if include_missing:
        fixtures[str(tmp_path / "missing.py")] = [{"finding_type": "command_injection"}]
    ground_truth = tmp_path / "ground_truth.json"
    ground_truth.write_text(json.dumps({"fixtures": fixtures}), encoding="utf-8")
    results = tmp_path / "results"
    monkeypatch.setattr(benchmark_run, "_RESULTS_DIR", results)
    script_scanner(0)

    exit_code = benchmark_run.main([
        "--offline", "--ground-truth", str(ground_truth), "--label", "coverage",
    ])

    saved = json.loads(next(results.glob("coverage_*.json")).read_text(encoding="utf-8"))
    assert exit_code == expected_exit
    assert saved["evaluation"]["status"] == expected_status
    assert saved["evaluation"]["fixtures"]["completed"] == 1


def test_real_world_cli_succeeds_when_static_scans_complete(
    tmp_path: Path, monkeypatch, script_scanner,
) -> None:
    results = tmp_path / "results"
    monkeypatch.setattr(benchmark_run_real_world, "_RESULTS_DIR", results)
    script_scanner(0)

    exit_code = benchmark_run_real_world.main(["--offline", "--label", "completed"])

    saved = json.loads(next(results.glob("completed_*.json")).read_text(encoding="utf-8"))
    assert exit_code == 0
    assert saved["evaluation"]["status"] == "complete"
    assert saved["evaluation"]["fixtures"]["completed"] == 8
