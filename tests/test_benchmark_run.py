"""Offline tests for benchmark runner helpers (no live LLM / Semgrep)."""

from __future__ import annotations

from pathlib import Path

from app.benchmark import evaluate, load_ground_truth
from app.benchmark_run import (
    confidence_records_from_report_items,
    fixture_relative_path,
    normalize_benchmark_finding_type,
    predictions_from_report_items,
    run_benchmark,
)


def test_normalize_maps_semgrep_shell_rule_to_command_injection() -> None:
    assert (
        normalize_benchmark_finding_type(
            "python.lang.security.audit.subprocess-shell-true.subprocess-shell-true"
        )
        == "command_injection"
    )


def test_normalize_keeps_logic_label() -> None:
    assert (
        normalize_benchmark_finding_type("missing_ownership_check")
        == "missing_ownership_check"
    )


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
