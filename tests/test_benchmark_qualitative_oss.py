"""Offline tests for the qualitative OSS confidence re-measurement runner.

No live LLM calls: review_fn is faked per test. These check extraction,
bucket occupancy, and that zero-finding / claim-unverified / error outcomes
are preserved rather than silently dropped.
"""

from __future__ import annotations

from pathlib import Path

from app.benchmark_qualitative_oss import (
    COHORT,
    bucket_occupancy_by_worker,
    finding_item_records,
    run_architecture_file,
    run_qualitative_oss,
    run_security_file,
)
from app.llm import LLMRateLimitedError


def test_cohort_is_ten_files_across_eight_projects() -> None:
    assert len(COHORT) == 10
    assert len({entry["project"] for entry in COHORT}) == 8


def test_finding_item_records_extracts_required_fields() -> None:
    items = [
        {
            "structured_finding": {
                "finding_type": "layering_violation",
                "confidence": 85,
                "detection_method": "llm_reasoning",
                "evidence": "sys.stdout._write(...)",
                "suggested_fix": "Use a logging module.",
            }
        }
    ]
    records = finding_item_records(
        items,
        source_file="smoke_test_external/werkzeug_security.py",
        worker="architecture",
        verdict="accepted",
        provider="openai",
        model="gpt-4o-mini",
        timestamp="2026-08-08T00:00:00+00:00",
    )
    assert len(records) == 1
    record = records[0]
    assert record["source_file"] == "smoke_test_external/werkzeug_security.py"
    assert record["worker"] == "architecture"
    assert record["finding_type"] == "layering_violation"
    assert record["confidence"] == 85
    assert record["detection_method"] == "llm_reasoning"
    assert record["verdict"] == "accepted"
    assert record["provider"] == "openai"
    assert record["model"] == "gpt-4o-mini"
    assert record["timestamp"] == "2026-08-08T00:00:00+00:00"
    assert record["status"] == "ok"


def test_run_security_file_zero_finding_preserved(tmp_path: Path) -> None:
    target = tmp_path / "clean.py"
    target.write_text("x = 1\n", encoding="utf-8")

    def fake_review(path: str) -> dict:
        return {"accepted": [], "needs_review": [], "inconclusive": False, "message": "clean"}

    run = run_security_file(fake_review, target, project="demo", provider="openai", model=None)

    assert run.status == "ok"
    assert run.zero_finding is True
    assert run.item_records == []


def test_run_security_file_inconclusive_preserved(tmp_path: Path) -> None:
    target = tmp_path / "ratelimited.py"
    target.write_text("x = 1\n", encoding="utf-8")

    def fake_review(path: str) -> dict:
        raise LLMRateLimitedError("rate limited")

    run = run_security_file(fake_review, target, project="demo", provider="openai", model=None)

    assert run.status == "inconclusive"
    assert run.zero_finding is False
    assert "rate limited" in (run.error_message or "")


def test_run_security_file_error_preserved_not_silently_omitted(tmp_path: Path) -> None:
    target = tmp_path / "broken.py"
    target.write_text("x = 1\n", encoding="utf-8")

    def fake_review(path: str) -> dict:
        raise RuntimeError("boom")

    run = run_security_file(fake_review, target, project="demo", provider="openai", model=None)

    assert run.status == "error"
    assert "boom" in (run.error_message or "")


def test_run_architecture_file_claim_unverified_preserved(tmp_path: Path) -> None:
    target = tmp_path / "maybe_layered.py"
    target.write_text("x = 1\n", encoding="utf-8")

    def fake_review(path: str) -> dict:
        return {
            "accepted": [],
            "needs_review": [],
            "claim_unverified": True,
            "message": "Architecture flagged a possible issue that didn't meet the evidence bar",
        }

    run = run_architecture_file(
        fake_review, target, project="demo", provider="openai", model=None
    )

    assert run.claim_unverified is True
    assert run.zero_finding is False  # claim-unverified is distinct from zero-finding
    assert run.item_records == []


def test_run_architecture_file_accepted_finding_has_confidence(tmp_path: Path) -> None:
    target = tmp_path / "layered.py"
    target.write_text("x = 1\n", encoding="utf-8")

    def fake_review(path: str) -> dict:
        return {
            "accepted": [
                {
                    "structured_finding": {
                        "finding_type": "layering_violation",
                        "confidence": 85,
                        "detection_method": "llm_reasoning",
                        "evidence": "...",
                        "suggested_fix": "...",
                    }
                }
            ],
            "needs_review": [],
            "claim_unverified": False,
        }

    run = run_architecture_file(
        fake_review, target, project="demo", provider="openai", model=None
    )

    assert run.accepted_count == 1
    assert run.zero_finding is False
    assert run.item_records[0]["confidence"] == 85


def test_bucket_occupancy_by_worker_counts_per_worker() -> None:
    records = [
        {"worker": "security", "confidence": 90},
        {"worker": "security", "confidence": 65},
        {"worker": "architecture", "confidence": 85},
        {"worker": "architecture", "confidence": None},  # ignored, no int confidence
    ]
    occupancy = bucket_occupancy_by_worker(records)

    assert occupancy["security"]["90-100"] == 1
    assert occupancy["security"]["<70"] == 1
    assert occupancy["architecture"]["80-89"] == 1
    assert sum(occupancy["architecture"].values()) == 1  # None confidence not counted


def test_run_qualitative_oss_end_to_end_preserves_all_outcome_kinds(tmp_path: Path) -> None:
    """Fake a full cohort run covering ok/zero-finding/claim-unverified/error/inconclusive."""
    cohort = (
        {"project": "alpha", "file_path": "smoke_test_external/werkzeug_security.py"},
        {"project": "beta", "file_path": "smoke_test_external/tqdm_utils.py"},
        {"project": "missing", "file_path": "smoke_test_external/does_not_exist.py"},
    )

    def fake_security(path: str) -> dict:
        if "tqdm_utils" in path:
            raise LLMRateLimitedError("rate limited")
        return {
            "accepted": [
                {
                    "structured_finding": {
                        "finding_type": "hardcoded_secret",
                        "confidence": 90,
                        "detection_method": "static_rule",
                        "evidence": "...",
                        "suggested_fix": "...",
                    }
                }
            ],
            "needs_review": [],
            "inconclusive": False,
        }

    def fake_architecture(path: str) -> dict:
        if "tqdm_utils" in path:
            raise RuntimeError("boom")
        return {
            "accepted": [],
            "needs_review": [],
            "claim_unverified": True,
            "message": "evidence bar not met",
        }

    payload = run_qualitative_oss(
        label="unit_test_qual",
        results_dir=tmp_path,
        security_fn=fake_security,
        architecture_fn=fake_architecture,
        cohort=cohort,
    )

    assert payload["provider"]
    assert payload["cohort_size"] == 3
    assert payload["run_counts"]["file_worker_runs"] == 6  # 3 files * 2 workers
    # missing file -> 2 errors (both workers) + tqdm_utils architecture RuntimeError -> 1 error
    assert payload["run_counts"]["error"] == 3
    assert payload["run_counts"]["inconclusive"] == 1  # tqdm_utils security rate limit

    # The "missing" file produces an error for both workers.
    missing_runs = [r for r in payload["file_runs"] if r["project"] == "missing"]
    assert len(missing_runs) == 2
    assert all(r["status"] == "error" for r in missing_runs)

    # tqdm_utils.py: security inconclusive (rate limit), architecture error (RuntimeError).
    tqdm_runs = {r["worker"]: r for r in payload["file_runs"] if r["project"] == "beta"}
    assert tqdm_runs["security"]["status"] == "inconclusive"
    assert tqdm_runs["architecture"]["status"] == "error"

    # werkzeug_security.py: security accepted a finding with confidence 90;
    # architecture claim-unverified (zero findings, but distinct from zero_finding).
    assert len(payload["item_records"]) == 1
    assert payload["item_records"][0]["confidence"] == 90
    assert payload["item_records"][0]["provider"] == payload["provider"]

    assert len(payload["claim_unverified_records"]) == 1
    assert payload["claim_unverified_records"][0]["source_file"] == (
        "smoke_test_external/werkzeug_security.py"
    )

    # Bucket occupancy reflects the one security finding at 90.
    assert payload["bucket_occupancy_by_worker"]["security"]["90-100"] == 1

    written = list(tmp_path.glob("unit_test_qual_*.json"))
    assert len(written) == 1
