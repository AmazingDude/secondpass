"""Confidence-bucket precision analysis over persisted benchmark results.

Measurement/reporting only — reads results JSON already written by
app/benchmark_run.py, app/benchmark_run_architecture.py, and
app/benchmark_run_real_world.py. Does not call the LLM, does not touch
detection logic, and does not touch the confidence gate threshold.

Each results file's ``per_file[*].confidence_records`` holds one entry per
individual finding (accepted AND needs_review, not deduplicated by type) with
its confidence, detection_method, and verdict. This module buckets those
records by confidence range and computes precision (hit rate against ground
truth) within each bucket, always labeled with the suite and the LLM
provider that produced the numbers (results files already record
``provider``/``model`` — see app/benchmark_run.py).

Honesty note baked into the output, not just this docstring: current suite
sizes are small. Several buckets will have 1-2 or even 0 data points. A
per-bucket precision computed from N=1 or N=2 is not a statistically
meaningful rate — the report and CLI output both say this plainly next to
the numbers, not just in prose above/below the table.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.benchmark import _normalize_path, load_ground_truth

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Bucket edges are inclusive on both ends; order matters for confidence_bucket.
BUCKET_EDGES: tuple[tuple[str, int, int], ...] = (
    ("<70", 0, 69),
    ("70-79", 70, 79),
    ("80-89", 80, 89),
    ("90-100", 90, 100),
)

# Below this many data points, precision is shown but flagged as not
# statistically meaningful rather than silently presented as a normal rate.
SMALL_SAMPLE_THRESHOLD = 3


def confidence_bucket(confidence: int) -> str:
    for label, lo, hi in BUCKET_EDGES:
        if lo <= confidence <= hi:
            return label
    return "unknown"


def _expected_keys(ground_truth: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for file_path, issues in (ground_truth.get("fixtures") or {}).items():
        norm = _normalize_path(file_path)
        for issue in issues:
            keys.add((norm, issue["finding_type"]))
    return keys


def load_results(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_ground_truth_path(payload: dict[str, Any]) -> Path:
    raw = payload.get("ground_truth")
    if not raw:
        raise ValueError("results file has no 'ground_truth' path recorded")
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (_REPO_ROOT / candidate)


def confidence_records_with_hits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten per_file confidence_records into (finding, is_hit) rows.

    A record is a "hit" when its (normalized file_path, finding_type) pair
    is present in this suite's ground truth — the same key used by
    app.benchmark.evaluate for the official TP/FP count. Records are NOT
    deduplicated by type per file (unlike the official scoring path): this
    analysis is about individual findings' confidence, not the per-type
    score.
    """
    ground_truth = load_ground_truth(_resolve_ground_truth_path(payload))
    expected = _expected_keys(ground_truth)

    rows: list[dict[str, Any]] = []
    for note in payload.get("per_file") or []:
        file_path = note.get("file_path")
        if not file_path:
            continue
        norm_path = _normalize_path(file_path)
        for record in note.get("confidence_records") or []:
            confidence = record.get("confidence")
            if not isinstance(confidence, int):
                continue
            finding_type = record.get("finding_type") or ""
            rows.append(
                {
                    "file_path": file_path,
                    "finding_type": finding_type,
                    "raw_finding_type": record.get("raw_finding_type"),
                    "confidence": confidence,
                    "detection_method": record.get("detection_method"),
                    "verdict": record.get("verdict"),
                    "is_hit": (norm_path, finding_type) in expected,
                    "bucket": confidence_bucket(confidence),
                }
            )
    return rows


def bucket_table(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Precision (hit rate) per confidence bucket, plus raw counts.

    Buckets with n == 0 keep precision=None (never silently show 0.0/1.0 for
    an empty bucket). Buckets with n < SMALL_SAMPLE_THRESHOLD are flagged.
    """
    table: dict[str, dict[str, Any]] = {
        label: {"n": 0, "hits": 0} for label, _, _ in BUCKET_EDGES
    }
    for row in rows:
        bucket = row["bucket"]
        if bucket not in table:
            continue
        table[bucket]["n"] += 1
        if row["is_hit"]:
            table[bucket]["hits"] += 1

    for stats in table.values():
        n = stats["n"]
        stats["precision"] = (stats["hits"] / n) if n else None
        stats["small_sample"] = 0 < n < SMALL_SAMPLE_THRESHOLD

    return table


def analyze_result_file(path: Path, *, suite_label: str) -> dict[str, Any]:
    payload = load_results(path)
    rows = confidence_records_with_hits(payload)
    return {
        "suite": suite_label,
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        "results_file": str(path),
        "rows": rows,
        "table": bucket_table(rows),
    }


def render_markdown_table(analyses: list[dict[str, Any]]) -> str:
    """One combined table, every row labeled with suite + provider."""
    lines = [
        "| Suite | Provider | Confidence bucket | Hits / N | Precision | Sample-size note |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for analysis in analyses:
        suite = analysis["suite"]
        provider = analysis["provider"] or "unknown"
        for label, _, _ in BUCKET_EDGES:
            stats = analysis["table"][label]
            n = stats["n"]
            if n == 0:
                lines.append(f"| {suite} | {provider} | {label} | 0 / 0 | n/a | no data in this bucket |")
                continue
            precision = stats["precision"]
            note = (
                f"**N={n} — too small to read as a rate**"
                if stats["small_sample"]
                else f"N={n}"
            )
            lines.append(
                f"| {suite} | {provider} | {label} | {stats['hits']} / {n} | "
                f"{precision:.2f} | {note} |"
            )
    return "\n".join(lines)


def render_text_summary(analyses: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for analysis in analyses:
        out.append(
            f"\n=== {analysis['suite']} — provider={analysis['provider']} "
            f"model={analysis['model'] or 'default'} ({analysis['results_file']}) ==="
        )
        for label, _, _ in BUCKET_EDGES:
            stats = analysis["table"][label]
            n = stats["n"]
            if n == 0:
                out.append(f"  {label:>7}: n=0 (no findings landed in this bucket)")
                continue
            flag = "  <-- N too small to trust as a rate" if stats["small_sample"] else ""
            out.append(
                f"  {label:>7}: {stats['hits']}/{n} hits, "
                f"precision={stats['precision']:.2f}{flag}"
            )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bucket persisted benchmark findings by confidence and compute "
            "precision per bucket. Reporting only; does not call the LLM."
        )
    )
    parser.add_argument(
        "results",
        nargs="+",
        help=(
            "One or more results JSON files, each paired with a suite label "
            "via --label (repeat --label once per file, same order), e.g.:\n"
            "  --label security:benchmark/results/foo.json "
            "--label architecture:benchmark/results/bar.json"
        ),
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Also print a Markdown table (for pasting into REPORT.md).",
    )
    args = parser.parse_args(argv)

    analyses = []
    for entry in args.results:
        if ":" not in entry:
            parser.error(
                f"expected SUITE_LABEL:PATH, got {entry!r} "
                '(e.g. "security:benchmark/results/final_20260804_20260804.json")'
            )
        suite_label, raw_path = entry.split(":", 1)
        analyses.append(
            analyze_result_file(Path(raw_path), suite_label=suite_label)
        )

    print(render_text_summary(analyses))
    print(
        "\nHonesty note: buckets flagged 'N too small to trust as a rate' have "
        f"fewer than {SMALL_SAMPLE_THRESHOLD} findings. A precision computed "
        "from 1-2 data points is not a statistically meaningful rate — it is "
        "shown for transparency, not as a calibration claim."
    )
    if args.markdown:
        print("\n" + render_markdown_table(analyses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
