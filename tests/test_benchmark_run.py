"""Offline tests for benchmark runner helpers (no live LLM / Semgrep)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.benchmark import evaluate, load_ground_truth
from app.benchmark_run import (
    confidence_records_from_report_items,
    fixture_relative_path,
    normalize_benchmark_finding_type,
    predictions_from_report_items,
    run_benchmark,
)


def _shell_scanner_output(path: Path) -> str:
    """One scripted external Semgrep detection, shared by the runner controls."""
    return json.dumps({"results": [{
        "check_id": "subprocess-shell-true", "path": str(path),
        "start": {"line": 2}, "end": {"line": 2},
        "extra": {"severity": "ERROR", "message": "Shell injection"},
    }]})


def test_architecture_invalid_provider_response_is_not_perfect_accuracy(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    from app.benchmark_run_architecture import run_architecture_benchmark

    class ScriptedClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="not a review", tool_calls=None),
            )])

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "offline-test-key")
    monkeypatch.setattr("app.llm.OpenAI", ScriptedClient)
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "hooks.jsonl")

    payload = run_architecture_benchmark(results_dir=tmp_path, label="invalid-model")

    assert payload["score"]["precision"] == payload["score"]["recall"] == 1.0
    summary = payload["evaluation"]
    assert summary["status"] == "invalid"
    assert summary["fixtures"] == {
        "requested": 3, "completed": 0, "inconclusive": 3, "errored": 0, "unknown": 0,
    }
    assert summary["conditional"]["recall"]["value"] is None
    assert summary["detection_yield"] == {"numerator": 0, "denominator": 2, "value": 0.0}
    assert "Evaluation: invalid" in capsys.readouterr().out


def test_normalize_maps_semgrep_shell_rule_to_command_injection() -> None:
    assert (
        normalize_benchmark_finding_type(
            "python.lang.security.audit.subprocess-shell-true.subprocess-shell-true"
        )
        == "command_injection"
    )


@pytest.mark.parametrize("suite,requested", [("security", 11), ("real_world", 8)])
def test_failed_static_benchmark_has_no_completed_coverage(
    tmp_path: Path, monkeypatch, capsys, suite: str, requested: int,
) -> None:
    monkeypatch.setattr("app.scanner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr(
        "app.scanner.subprocess.run",
        lambda command, **kw: subprocess.CompletedProcess(command, 2, "", "scan unavailable"),
    )
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "hooks.jsonl")

    if suite == "real_world":
        from app.benchmark_run_real_world import run_benchmark as run_real_world
        monkeypatch.setattr("app.benchmark_run_real_world._RESULTS_DIR", tmp_path)
        payload = run_real_world(offline=True, label="failed")
    else:
        payload = run_benchmark(offline=True, results_dir=tmp_path, label="failed")

    summary = payload["evaluation"]
    assert summary["version"] == "coverage-v1"
    assert summary["matching"] == "legacy-v1-file-type"
    assert summary["status"] == "invalid"
    assert summary["fixtures"] == {
        "requested": requested, "completed": 0, "inconclusive": requested,
        "errored": 0, "unknown": 0,
    }
    assert summary["conditional"] == {
        "precision": {"numerator": 0, "denominator": 0, "value": None},
        "recall": {"numerator": 0, "denominator": 0, "value": None},
    }
    assert summary["detection_yield"] == {"numerator": 0, "denominator": 4, "value": 0.0}
    saved = json.loads(next(tmp_path.glob("failed_*.json")).read_text())
    assert saved["evaluation"] == summary
    output = capsys.readouterr().out
    assert "Evaluation: invalid" in output
    assert "precision=n/a (0/0)" in output
    assert "ScoreReport:" not in output


def test_normalize_keeps_logic_label() -> None:
    assert (
        normalize_benchmark_finding_type("missing_ownership_check")
        == "missing_ownership_check"
    )


def test_partial_benchmark_separates_conditional_recall_from_requested_yield(
    tmp_path: Path, monkeypatch,
) -> None:
    bug = tmp_path / "bug.py"
    bug.write_text("import subprocess\nsubprocess.run(command, shell=True)\n")
    unavailable = tmp_path / "unavailable.py"
    unavailable.write_text("print('not scanned')\n")
    clean = tmp_path / "clean.py"
    clean.write_text("print('clean')\n")
    missing = tmp_path / "missing.py"
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text(json.dumps({"fixtures": {
        str(bug): [{"finding_type": "command_injection"}],
        str(unavailable): [{"finding_type": "command_injection"}],
        str(missing): [{"finding_type": "command_injection"}],
        str(clean): [],
    }}))

    def scanner_process(command, **kwargs):
        target = Path(command[-1])
        if target == unavailable:
            return subprocess.CompletedProcess(command, 2, "", "unavailable")
        output = _shell_scanner_output(bug) if target == bug else '{"results": []}'
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr("app.scanner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("app.scanner.subprocess.run", scanner_process)
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "hooks.jsonl")

    payload = run_benchmark(offline=True, ground_truth_path=gt_path, results_dir=tmp_path)

    summary = payload["evaluation"]
    assert summary["status"] == "incomplete"
    assert summary["fixtures"] == {
        "requested": 4, "completed": 2, "inconclusive": 1, "errored": 1, "unknown": 0,
    }
    assert summary["conditional"] == {
        "precision": {"numerator": 1, "denominator": 1, "value": 1.0},
        "recall": {"numerator": 1, "denominator": 1, "value": 1.0},
    }
    assert summary["completion"] == {"numerator": 2, "denominator": 4, "value": 0.5}
    assert summary["detection_yield"] == {"numerator": 1, "denominator": 3, "value": 1 / 3}
    assert payload["score"]["false_negatives"] == 2  # historical scorer still includes errors


def test_completed_clean_control_has_undefined_detection_metrics(
    tmp_path: Path, monkeypatch,
) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("print('clean')\n")
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text(json.dumps({"fixtures": {str(clean): []}}))
    monkeypatch.setattr("app.scanner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr(
        "app.scanner.subprocess.run",
        lambda command, **kw: subprocess.CompletedProcess(command, 0, '{"results": []}', ""),
    )
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "hooks.jsonl")

    payload = run_benchmark(offline=True, ground_truth_path=gt_path, results_dir=tmp_path)

    summary = payload["evaluation"]
    assert summary["status"] == "complete"
    assert summary["completion"]["value"] == 1.0
    assert summary["conditional"] == {
        "precision": {"numerator": 0, "denominator": 0, "value": None},
        "recall": {"numerator": 0, "denominator": 0, "value": None},
    }
    assert summary["detection_yield"]["value"] is None


def test_model_failure_keeps_static_hit_in_yield_but_not_conditional_metrics(
    tmp_path: Path, monkeypatch,
) -> None:
    bug = tmp_path / "bug.py"
    bug.write_text("import subprocess\nsubprocess.run(command, shell=True)\n")
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text(json.dumps({"fixtures": {
        str(bug): [{"finding_type": "command_injection"}],
    }}))

    provider_responses = iter([
        RuntimeError("scripted logic assessment unavailable"),
        '{"use_memory": false, "use_web": false}',
        RuntimeError("scripted synthesis unavailable"),
    ])

    class FailedClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            response = next(provider_responses)
            if isinstance(response, Exception):
                raise response
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=response, tool_calls=None,
            ))])

    def unavailable_memory(**kwargs):
        raise RuntimeError("scripted database unavailable")

    scanner_output = _shell_scanner_output(bug)
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "offline-test-key")
    monkeypatch.setattr("app.llm.OpenAI", FailedClient)
    monkeypatch.setitem(
        sys.modules, "chromadb", SimpleNamespace(PersistentClient=unavailable_memory),
    )
    monkeypatch.setattr("app.memory._DEFAULT_DB_PATH", tmp_path / "memory")
    monkeypatch.setattr("app.scanner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr(
        "app.scanner.subprocess.run",
        lambda command, **kw: subprocess.CompletedProcess(command, 0, scanner_output, ""),
    )
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "hooks.jsonl")

    payload = run_benchmark(ground_truth_path=gt_path, results_dir=tmp_path)

    summary = payload["evaluation"]
    assert summary["status"] == "invalid"
    assert summary["fixtures"]["inconclusive"] == 1
    assert summary["conditional"]["recall"] == {
        "numerator": 0, "denominator": 0, "value": None,
    }
    assert summary["detection_yield"] == {"numerator": 1, "denominator": 1, "value": 1.0}
    assert len(payload["predictions"]) == 1
    assert payload["score"]["true_positives"] == 0  # legacy exclusion retained


def test_predictions_from_accepted_items_dedupe_and_remap(
    tmp_path: Path,
) -> None:
    items = [
        {
            "structured_finding": {
                "finding_type": (
                    "python.lang.security.audit.subprocess-shell-true"
                    ".subprocess-shell-true"
                )
            }
        },
        {
            "structured_finding": {
                "finding_type": (
                    "python.lang.security.audit.subprocess-shell-true"
                    ".subprocess-shell-true"
                )
            }
        },
    ]
    predictions = predictions_from_report_items(
        items, fixture_path="benchmark/fixtures/ops_shell.py"
    )
    assert len(predictions) == 1
    assert predictions[0].finding_type == "command_injection"


def test_predictions_from_report_items_carries_confidence_and_method() -> None:
    items = [
        {
            "structured_finding": {
                "finding_type": "missing_ownership_check",
                "confidence": 92,
                "detection_method": "llm_reasoning",
            }
        }
    ]
    predictions = predictions_from_report_items(
        items, fixture_path="benchmark/fixtures/notes_idor.py"
    )
    assert len(predictions) == 1
    assert predictions[0].confidence == 92
    assert predictions[0].detection_method == "llm_reasoning"


def test_predictions_from_report_items_confidence_missing_is_none() -> None:
    items = [{"structured_finding": {"finding_type": "missing_ownership_check"}}]
    predictions = predictions_from_report_items(
        items, fixture_path="benchmark/fixtures/notes_idor.py"
    )
    assert predictions[0].confidence is None
    assert predictions[0].detection_method is None


def test_confidence_records_include_both_verdicts_without_dedup() -> None:
    accepted = [
        {
            "structured_finding": {
                "finding_type": "missing_ownership_check",
                "confidence": 95,
                "detection_method": "llm_reasoning",
            }
        }
    ]
    needs_review = [
        {
            "structured_finding": {
                "finding_type": "missing_ownership_check",
                "confidence": 60,
                "detection_method": "llm_reasoning",
            }
        },
        {
            "structured_finding": {
                "finding_type": "hardcoded_secret",
                "confidence": 65,
                "detection_method": "static_rule",
            }
        },
    ]
    records = confidence_records_from_report_items(
        accepted, verdict="accepted"
    ) + confidence_records_from_report_items(needs_review, verdict="needs_review")

    assert len(records) == 3  # no dedup by finding_type, unlike predictions_from_report_items
    assert records[0] == {
        "finding_type": "missing_ownership_check",
        "raw_finding_type": "missing_ownership_check",
        "confidence": 95,
        "detection_method": "llm_reasoning",
        "verdict": "accepted",
    }
    assert [r["verdict"] for r in records] == [
        "accepted",
        "needs_review",
        "needs_review",
    ]


def test_confidence_records_skip_missing_confidence() -> None:
    items = [{"structured_finding": {"finding_type": "missing_ownership_check"}}]
    assert confidence_records_from_report_items(items, verdict="accepted") == []


def test_fixture_relative_path_from_absolute(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = repo / "benchmark" / "fixtures" / "ops_shell.py"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    assert (
        fixture_relative_path(target, repo_root=repo)
        == "benchmark/fixtures/ops_shell.py"
    )


def test_run_benchmark_offline_writes_results(tmp_path: Path, monkeypatch) -> None:
    results_dir = tmp_path / "results"

    def fake_offline(fixture_abs: Path) -> dict:
        name = fixture_abs.name
        if name == "ops_shell.py":
            return {
                "path": str(fixture_abs),
                "accepted": [
                    {
                        "structured_finding": {
                            "finding_type": (
                                "python.lang.security.audit.subprocess-shell-true"
                                ".subprocess-shell-true"
                            )
                        }
                    }
                ],
                "needs_review": [],
                "static_scan_error": None,
                "used_logic_fallback": False,
                "message": None,
            }
        if name == "notes_idor.py":
            return {
                "path": str(fixture_abs),
                "accepted": [],
                "needs_review": [],
                "static_scan_error": None,
                "used_logic_fallback": False,
                "message": "No static findings (offline).",
            }
        return {
            "path": str(fixture_abs),
            "accepted": [],
            "needs_review": [],
            "static_scan_error": None,
            "used_logic_fallback": False,
            "message": "clean",
        }

    monkeypatch.setattr("app.benchmark_run._offline_review", fake_offline)

    payload = run_benchmark(
        offline=True,
        results_dir=results_dir,
        label="unit",
    )

    score = payload["score"]
    # offline Semgrep hits ops_shell only → 1 TP; the other 3 non-clean
    # fixtures (notes_idor, hardcoded_secret, path_traversal) need the LLM
    # logic-review fallback, which this stub never runs → 3 FN, 0 FP.
    assert score["true_positives"] == 1
    assert score["false_positives"] == 0
    assert score["false_negatives"] == 3
    assert score["precision"] == 1.0
    assert score["recall"] == 0.25
    assert payload["scored_bucket"] == "accepted"
    assert payload["mode"] == "offline_semgrep"
    # These historical scripted reports have no explicit coverage metadata.
    assert payload["evaluation"]["status"] == "invalid"
    assert payload["evaluation"]["fixtures"]["unknown"] == 11
    assert payload["evaluation"]["fixtures"]["completed"] == 0
    assert payload["evaluation"]["detection_yield"] == {
        "numerator": 1, "denominator": 4, "value": 0.25,
    }
    written = list(results_dir.glob("unit_*.json"))
    assert len(written) == 1


def test_run_benchmark_excludes_inconclusive_fixture_from_false_negatives(
    tmp_path: Path, monkeypatch
) -> None:
    """A rate-limited / errored LLM call must not be scored as a miss.

    ground_truth expects path_traversal on path_traversal.py. If review_code
    comes back inconclusive (zero predictions, report["inconclusive"]=True),
    that fixture must be excluded from the denominator entirely — not
    silently counted as a false negative — and must show up in the
    inconclusive/excluded bookkeeping instead.
    """
    results_dir = tmp_path / "results"

    def fake_live(fixture_abs: Path) -> dict:
        name = fixture_abs.name
        if name == "path_traversal.py":
            return {
                "path": str(fixture_abs),
                "accepted": [],
                "needs_review": [],
                "static_scan_error": None,
                "used_logic_fallback": True,
                "inconclusive": True,
                "message": "inconclusive — rate limited",
            }
        # Every other fixture in ground_truth.json: no findings either, but
        # a *conclusive* zero — a genuine miss for anything with expected
        # issues, which is fine, since this test only asserts on the
        # inconclusive fixture's treatment.
        return {
            "path": str(fixture_abs),
            "accepted": [],
            "needs_review": [],
            "static_scan_error": None,
            "used_logic_fallback": False,
            "inconclusive": False,
            "message": "clean",
        }

    monkeypatch.setattr("app.benchmark_run._live_review", fake_live)

    payload = run_benchmark(
        offline=False,
        results_dir=results_dir,
        label="unit-inconclusive",
    )

    score = payload["score"]
    assert payload["inconclusive_fixtures"] == [
        "benchmark/fixtures/path_traversal.py"
    ]
    assert payload["scoring_note"] == (
        "1 fixture(s) inconclusive — excluded from scored recall"
    )
    # path_traversal must NOT be one of the false negatives: only the other
    # three real-issue fixtures (notes_idor, ops_shell, hardcoded_secret)
    # are scored as misses.
    assert score["false_negatives"] == 3
    assert score["true_positives"] == 0
    assert score["false_positives"] == 0

    note = next(
        row
        for row in payload["per_file"]
        if row["file_path"] == "benchmark/fixtures/path_traversal.py"
    )
    assert note["inconclusive"] is True
    assert note["coverage_status"] == "inconclusive"
    assert note["predicted_finding_types"] == []


def test_run_benchmark_inconclusive_fixture_with_a_prediction_is_not_scored_as_fp(
    tmp_path: Path, monkeypatch
) -> None:
    """A fixture can be inconclusive (LLM leg rate-limited) yet still carry a
    prediction from an earlier stage (e.g. a Semgrep static hit before the
    logic-review call failed). That prediction must be dropped from scoring
    entirely, not counted as a false positive just because its GT entry was
    excluded as inconclusive.
    """
    results_dir = tmp_path / "results"

    def fake_live(fixture_abs: Path) -> dict:
        name = fixture_abs.name
        if name == "ops_shell.py":
            return {
                "path": str(fixture_abs),
                "accepted": [
                    {
                        "structured_finding": {
                            "finding_type": (
                                "python.lang.security.audit.subprocess-shell-true"
                                ".subprocess-shell-true"
                            )
                        }
                    }
                ],
                "needs_review": [],
                "static_scan_error": None,
                "used_logic_fallback": True,
                "inconclusive": True,
                "message": "inconclusive — rate limited",
            }
        return {
            "path": str(fixture_abs),
            "accepted": [],
            "needs_review": [],
            "static_scan_error": None,
            "used_logic_fallback": False,
            "inconclusive": False,
            "message": "clean",
        }

    monkeypatch.setattr("app.benchmark_run._live_review", fake_live)

    payload = run_benchmark(
        offline=False,
        results_dir=results_dir,
        label="unit-inconclusive-fp",
    )

    score = payload["score"]
    assert payload["inconclusive_fixtures"] == ["benchmark/fixtures/ops_shell.py"]
    # The command_injection prediction on ops_shell must not surface as a
    # false positive just because its GT entry was excluded.
    assert score["false_positives"] == 0
    assert score["true_positives"] == 0
    # notes_idor / hardcoded_secret / path_traversal each expect one finding
    # that this fake review never produces → genuine misses.
    assert score["false_negatives"] == 3


def test_mapped_predictions_score_against_real_ground_truth() -> None:
    """Scope to the two original fixtures — full ground truth now covers more
    fixtures (hardcoded_secret, path_traversal) added for Security's
    diversified-bug-class benchmark; this test only checks that ops_shell /
    notes_idor still map cleanly, not the whole suite."""
    predictions = [
        {
            "file_path": "benchmark/fixtures/ops_shell.py",
            "finding_type": normalize_benchmark_finding_type(
                "python.lang.security.audit.subprocess-shell-true"
            ),
        },
        {
            "file_path": "benchmark/fixtures/notes_idor.py",
            "finding_type": "missing_ownership_check",
        },
    ]
    full_ground_truth = load_ground_truth()
    scoped_ground_truth = {
        **full_ground_truth,
        "fixtures": {
            key: value
            for key, value in full_ground_truth["fixtures"].items()
            if key
            in {
                "benchmark/fixtures/ops_shell.py",
                "benchmark/fixtures/notes_idor.py",
            }
        },
    }
    report = evaluate(predictions, scoped_ground_truth)
    assert report.true_positives == 2
    assert report.false_positives == 0
    assert report.false_negatives == 0
