"""Qualitative OSS confidence re-measurement (measurement/reporting only).

Runs Security and Architecture once each over a **fixed** cohort of 10 real
OSS Python files that were already selected and manually reviewed before
confidence-bucket reporting existed (see prompts.md §§37-39,
benchmark/REPORT.md's OSS-smoke notes, and the raw transcripts under
smoke_test_external/ and smoke_test_security/). This module does not:

- add or change ground truth (these files have none, on purpose — they are
  real-world code, not planted fixtures);
- touch detection prompts, filters, or the confidence gate threshold;
- compute or claim precision, recall, accuracy, or calibration.

It only persists what happened: for every finding that reached
``accepted``/``needs_review``, and separately for Architecture
claim-unverified outcomes and zero-finding outcomes, so a human can look at
the confidence distribution and detection_method mix without re-deriving it
from raw CLI transcripts. Call the result a "qualitative OSS confidence
distribution," not a scored benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from app.benchmark_confidence_buckets import BUCKET_EDGES, confidence_bucket

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RESULTS_DIR = _REPO_ROOT / "benchmark" / "results"

ReviewFn = Callable[[str], dict[str, Any]]

# Fixed 10-file / 8-project cohort. Selected and reviewed (see prior
# transcripts) before confidence-bucket reporting existed. Do NOT add/remove
# files here to chase a more attractive distribution — one complete
# measurement over this exact list is the whole point of this module.
COHORT: tuple[dict[str, str], ...] = (
    {"project": "werkzeug", "file_path": "smoke_test_external/werkzeug_security.py"},
    {"project": "django", "file_path": "smoke_test_external/django_crypto.py"},
    {"project": "requests", "file_path": "smoke_test_external/requests_exceptions.py"},
    {
        "project": "itsdangerous",
        "file_path": "smoke_test_external/itsdangerous_encoding.py",
    },
    {"project": "tqdm", "file_path": "smoke_test_external/tqdm_utils.py"},
    {
        "project": "werkzeug",
        "file_path": (
            "smoke_test_security/werkzeug_debug_console/werkzeug_debug_console.py"
        ),
    },
    {
        "project": "tornado",
        "file_path": "smoke_test_security/tornado_process/tornado_process.py",
    },
    {
        "project": "python-dotenv",
        "file_path": "smoke_test_security/dotenv_parser/dotenv_parser.py",
    },
    {
        "project": "django",
        "file_path": "smoke_test_security/django_detail_view/django_detail_view.py",
    },
    {
        "project": "humanize",
        "file_path": (
            "smoke_test_security/clean_control_humanize/humanize_filesize.py"
        ),
    },
)

WORKERS = ("security", "architecture")


def relative_posix(path: Path, *, root: Path = _REPO_ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


@dataclass
class WorkerRunResult:
    """One worker's single run over one file."""

    source_file: str
    worker: str
    project: str
    status: str  # "ok" | "inconclusive" | "error"
    error_message: str | None
    accepted_count: int
    needs_review_count: int
    claim_unverified: bool
    zero_finding: bool
    item_records: list[dict[str, Any]] = field(default_factory=list)
    raw_report: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "worker": self.worker,
            "project": self.project,
            "status": self.status,
            "error_message": self.error_message,
            "accepted_count": self.accepted_count,
            "needs_review_count": self.needs_review_count,
            "claim_unverified": self.claim_unverified,
            "zero_finding": self.zero_finding,
            "raw_report": self.raw_report,
        }


def finding_item_records(
    items: list[dict[str, Any]],
    *,
    source_file: str,
    worker: str,
    verdict: str,
    provider: str,
    model: str | None,
    timestamp: str,
) -> list[dict[str, Any]]:
    """Persist every emitted finding with the fields the task requires.

    Not deduplicated by finding_type — every individual item that reached
    accepted/needs_review is one record, matching how confidence is actually
    attached (per finding instance).
    """
    records: list[dict[str, Any]] = []
    for item in items:
        structured = item.get("structured_finding") or {}
        confidence = structured.get("confidence")
        evidence = str(structured.get("evidence") or "")
        records.append(
            {
                "source_file": source_file,
                "worker": worker,
                "finding_type": structured.get("finding_type"),
                "confidence": (
                    int(confidence) if isinstance(confidence, (int, float)) else None
                ),
                "detection_method": structured.get("detection_method"),
                "verdict": verdict,
                "provider": provider,
                "model": model,
                "timestamp": timestamp,
                "status": "ok",
                "evidence_summary": evidence[:240],
                "suggested_fix": str(structured.get("suggested_fix") or "")[:240],
            }
        )
    return records


def run_security_file(
    review_fn: ReviewFn,
    file_path: Path,
    *,
    project: str,
    provider: str,
    model: str | None,
) -> WorkerRunResult:
    source_file = relative_posix(file_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        report = review_fn(str(file_path))
    except Exception as exc:  # noqa: BLE001 — record, do not abort the cohort
        from app.llm import LLMRateLimitedError

        status = "inconclusive" if isinstance(exc, LLMRateLimitedError) else "error"
        return WorkerRunResult(
            source_file=source_file,
            worker="security",
            project=project,
            status=status,
            error_message=f"{type(exc).__name__}: {exc}",
            accepted_count=0,
            needs_review_count=0,
            claim_unverified=False,
            zero_finding=False,
        )

    inconclusive = bool(report.get("inconclusive"))
    accepted = list(report.get("accepted") or [])
    needs_review = list(report.get("needs_review") or [])
    records = finding_item_records(
        accepted,
        source_file=source_file,
        worker="security",
        verdict="accepted",
        provider=provider,
        model=model,
        timestamp=timestamp,
    ) + finding_item_records(
        needs_review,
        source_file=source_file,
        worker="security",
        verdict="needs_review",
        provider=provider,
        model=model,
        timestamp=timestamp,
    )
    zero_finding = not records and not inconclusive
    return WorkerRunResult(
        source_file=source_file,
        worker="security",
        project=project,
        status="inconclusive" if inconclusive else "ok",
        error_message=str(report.get("message")) if inconclusive else None,
        accepted_count=len(accepted),
        needs_review_count=len(needs_review),
        claim_unverified=False,
        zero_finding=zero_finding,
        item_records=records,
        raw_report=report,
    )


def run_architecture_file(
    review_fn: ReviewFn,
    file_path: Path,
    *,
    project: str,
    provider: str,
    model: str | None,
) -> WorkerRunResult:
    source_file = relative_posix(file_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        report = review_fn(str(file_path))
    except Exception as exc:  # noqa: BLE001 — record, do not abort the cohort
        from app.llm import LLMRateLimitedError

        status = "inconclusive" if isinstance(exc, LLMRateLimitedError) else "error"
        return WorkerRunResult(
            source_file=source_file,
            worker="architecture",
            project=project,
            status=status,
            error_message=f"{type(exc).__name__}: {exc}",
            accepted_count=0,
            needs_review_count=0,
            claim_unverified=False,
            zero_finding=False,
        )

    claim_unverified = bool(report.get("claim_unverified"))
    accepted = list(report.get("accepted") or [])
    needs_review = list(report.get("needs_review") or [])
    records = finding_item_records(
        accepted,
        source_file=source_file,
        worker="architecture",
        verdict="accepted",
        provider=provider,
        model=model,
        timestamp=timestamp,
    ) + finding_item_records(
        needs_review,
        source_file=source_file,
        worker="architecture",
        verdict="needs_review",
        provider=provider,
        model=model,
        timestamp=timestamp,
    )
    zero_finding = not records and not claim_unverified
    return WorkerRunResult(
        source_file=source_file,
        worker="architecture",
        project=project,
        status="ok",
        error_message=None,
        accepted_count=len(accepted),
        needs_review_count=len(needs_review),
        claim_unverified=claim_unverified,
        zero_finding=zero_finding,
        item_records=records,
        raw_report=report,
    )


def bucket_occupancy_by_worker(
    item_records: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Confidence-bucket counts per worker, over every persisted item record."""
    occupancy: dict[str, dict[str, int]] = {
        worker: {label: 0 for label, _, _ in BUCKET_EDGES} for worker in WORKERS
    }
    for record in item_records:
        confidence = record.get("confidence")
        if not isinstance(confidence, int):
            continue
        worker = record.get("worker")
        if worker not in occupancy:
            continue
        occupancy[worker][confidence_bucket(confidence)] += 1
    return occupancy


def run_qualitative_oss(
    *,
    label: str = "qualitative_oss",
    results_dir: Path | None = None,
    security_fn: ReviewFn | None = None,
    architecture_fn: ReviewFn | None = None,
    cohort: tuple[dict[str, str], ...] = COHORT,
) -> dict[str, Any]:
    """Run Security + Architecture once each over ``cohort`` and persist results.

    One run per file/worker — no reruns, no cherry-picking. Missing files,
    exceptions, and rate limits are recorded, never silently skipped.
    """
    load_dotenv(_REPO_ROOT / ".env")

    if security_fn is None or architecture_fn is None:
        from app.agent import review_architecture, review_code

        security_fn = security_fn or review_code
        architecture_fn = architecture_fn or review_architecture

    provider = os.getenv("LLM_PROVIDER", "groq")
    model = os.getenv("LLM_MODEL") or None

    file_runs: list[dict[str, Any]] = []
    item_records: list[dict[str, Any]] = []
    claim_unverified_records: list[dict[str, Any]] = []
    zero_finding_records: list[dict[str, Any]] = []

    for entry in cohort:
        project = entry["project"]
        rel_path = entry["file_path"]
        file_path = _REPO_ROOT / rel_path

        for worker in WORKERS:
            if not file_path.is_file():
                run = WorkerRunResult(
                    source_file=rel_path,
                    worker=worker,
                    project=project,
                    status="error",
                    error_message=f"file missing on disk: {file_path}",
                    accepted_count=0,
                    needs_review_count=0,
                    claim_unverified=False,
                    zero_finding=False,
                )
            elif worker == "security":
                run = run_security_file(
                    security_fn, file_path, project=project, provider=provider, model=model
                )
            else:
                run = run_architecture_file(
                    architecture_fn,
                    file_path,
                    project=project,
                    provider=provider,
                    model=model,
                )

            file_runs.append(run.to_dict())
            item_records.extend(run.item_records)
            if run.claim_unverified:
                claim_unverified_records.append(
                    {
                        "source_file": run.source_file,
                        "project": run.project,
                        "worker": run.worker,
                        "provider": provider,
                        "model": model,
                        "summary": (run.raw_report or {}).get("message"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            if run.zero_finding:
                zero_finding_records.append(
                    {
                        "source_file": run.source_file,
                        "project": run.project,
                        "worker": run.worker,
                        "provider": provider,
                        "model": model,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )

    completed = sum(1 for run in file_runs if run["status"] == "ok")
    inconclusive = sum(1 for run in file_runs if run["status"] == "inconclusive")
    errored = sum(1 for run in file_runs if run["status"] == "error")

    payload: dict[str, Any] = {
        "label": label,
        "date": date.today().strftime("%Y%m%d"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "cohort_size": len(cohort),
        "cohort_projects": sorted({entry["project"] for entry in cohort}),
        "run_counts": {
            "file_worker_runs": len(file_runs),
            "completed": completed,
            "inconclusive": inconclusive,
            "error": errored,
        },
        "file_runs": file_runs,
        "item_records": item_records,
        "claim_unverified_records": claim_unverified_records,
        "zero_finding_records": zero_finding_records,
        "bucket_occupancy_by_worker": bucket_occupancy_by_worker(item_records),
        "note": (
            "Qualitative OSS confidence distribution — no exhaustive ground "
            "truth for this cohort. Do NOT compute or cite precision, recall, "
            "accuracy, or calibration from this payload."
        ),
    }

    out_dir = results_dir or _RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{label}_{payload['date']}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    payload["_results_path"] = str(out_path)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Qualitative OSS confidence re-measurement over a fixed 10-file "
            "cohort. Reporting only — no ground truth, no P/R claim."
        )
    )
    parser.add_argument("--label", default="qualitative_oss")
    args = parser.parse_args(argv)

    payload = run_qualitative_oss(label=args.label)
    print(f"provider={payload['provider']} model={payload['model'] or 'default'}")
    print(f"run_counts={payload['run_counts']}")
    print(f"bucket_occupancy_by_worker={payload['bucket_occupancy_by_worker']}")
    print(f"claim_unverified={len(payload['claim_unverified_records'])}")
    print(f"zero_finding={len(payload['zero_finding_records'])}")
    print(f"wrote {payload['_results_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
