"""Run Architecture-path detection-quality eval against benchmark fixtures.

Parallel to app/benchmark_run.py (Security), not a modification of it. Reuses
the same PredictedFinding/evaluate scoring and the same
predictions_from_report_items/normalize_benchmark_finding_type helpers, since
Architecture's finding_type values (layering_violation, dependency_direction,
naming_convention, duplicated_logic) already match ground-truth labels
directly — no Semgrep-style rule-id remapping needed.

Own ground truth file (benchmark/ground_truth_architecture.json) and own
fixtures directory (benchmark/fixtures/architecture/) so Architecture's
cross-file context gathering (app/context.py pulls same-package siblings)
does not mix these fixtures in with the Security ones.

Scoring uses accepted findings only (confidence gate >= threshold), same
convention as Security. No offline mode: Architecture has no Semgrep-only
path, only the LLM worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.benchmark import PredictedFinding, ScoreReport, evaluate, load_ground_truth
from app.benchmark_run import (
    confidence_records_from_report_items,
    predictions_from_report_items,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RESULTS_DIR = _REPO_ROOT / "benchmark" / "results"
DEFAULT_ARCHITECTURE_GROUND_TRUTH_PATH = (
    _REPO_ROOT / "benchmark" / "ground_truth_architecture.json"
)


def list_fixture_paths(ground_truth: dict[str, Any]) -> list[str]:
    fixtures = ground_truth.get("fixtures") or {}
    return sorted(fixtures.keys())


def _review(fixture_abs: Path) -> dict[str, Any]:
    from app.agent import review_architecture

    return review_architecture(str(fixture_abs))


def run_architecture_benchmark(
    *,
    include_needs_review: bool = False,
    ground_truth_path: Path | None = None,
    results_dir: Path | None = None,
    label: str = "architecture_baseline",
) -> dict[str, Any]:
    """Review each Architecture fixture, score predictions, write a results JSON file."""
    load_dotenv(_REPO_ROOT / ".env")
    resolved_gt_path = ground_truth_path or DEFAULT_ARCHITECTURE_GROUND_TRUTH_PATH
    ground_truth = load_ground_truth(resolved_gt_path)
    fixture_keys = list_fixture_paths(ground_truth)
    if not fixture_keys:
        raise ValueError("ground_truth_architecture.json has no fixtures")

    all_predictions: list[PredictedFinding] = []
    per_file: list[dict[str, Any]] = []

    for fixture_key in fixture_keys:
        fixture_abs = (_REPO_ROOT / fixture_key).resolve()
        if not fixture_abs.is_file():
            per_file.append(
                {
                    "file_path": fixture_key,
                    "error": f"fixture missing: {fixture_abs}",
                    "predictions": [],
                    "accepted_count": 0,
                    "needs_review_count": 0,
                }
            )
            continue

        print(f"[architecture] reviewing {fixture_key} …", flush=True)
        try:
            report = _review(fixture_abs)
        except Exception as exc:  # noqa: BLE001 — capture per-file and continue
            per_file.append(
                {
                    "file_path": fixture_key,
                    "error": f"{type(exc).__name__}: {exc}",
                    "predictions": [],
                    "accepted_count": 0,
                    "needs_review_count": 0,
                }
            )
            print(f"  error: {exc}", flush=True)
            continue

        accepted = list(report.get("accepted") or [])
        needs_review = list(report.get("needs_review") or [])
        scored_items = list(accepted)
        if include_needs_review:
            scored_items.extend(needs_review)

        predictions = predictions_from_report_items(
            scored_items, fixture_path=fixture_key
        )
        all_predictions.extend(predictions)

        expected = [
            issue["finding_type"]
            for issue in (ground_truth.get("fixtures") or {}).get(fixture_key, [])
        ]
        predicted_types = [item.finding_type for item in predictions]
        note = {
            "file_path": fixture_key,
            "mode": "architecture_review",
            "accepted_count": len(accepted),
            "needs_review_count": len(needs_review),
            "scored_bucket": (
                "accepted+needs_review" if include_needs_review else "accepted"
            ),
            "expected_finding_types": expected,
            "predicted_finding_types": predicted_types,
            "accepted_raw_types": [
                (item.get("structured_finding") or {}).get("finding_type")
                for item in accepted
            ],
            "needs_review_raw_types": [
                (item.get("structured_finding") or {}).get("finding_type")
                for item in needs_review
            ],
            "confidence_records": (
                confidence_records_from_report_items(accepted, verdict="accepted")
                + confidence_records_from_report_items(
                    needs_review, verdict="needs_review"
                )
            ),
            "message": report.get("message"),
        }
        per_file.append(note)
        print(
            f"  accepted={len(accepted)} needs_review={len(needs_review)} "
            f"predicted={predicted_types} expected={expected}",
            flush=True,
        )

    score: ScoreReport = evaluate(all_predictions, ground_truth)
    out_dir = results_dir or _RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    out_path = out_dir / f"{label}_{stamp}.json"

    payload = {
        "label": label,
        "date": stamp,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "worker": "architecture",
        "mode": "architecture_review",
        "scored_bucket": (
            "accepted+needs_review" if include_needs_review else "accepted"
        ),
        "ground_truth": str(resolved_gt_path),
        "provider": os.getenv("LLM_PROVIDER", "groq"),
        "model": os.getenv("LLM_MODEL") or None,
        "score": score.model_dump(),
        "predictions": [item.model_dump() for item in all_predictions],
        "per_file": per_file,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nScoreReport: {score.model_dump()}")
    try:
        display = out_path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        display = str(out_path)
    print(f"Wrote {display}")

    from app.agent import review_architecture
    from app.benchmark_cross_worker import (
        SECURITY_FIXTURES_EXPECT_ARCHITECTURE_NO_AUTHZ_BLEED,
        assert_architecture_report_clean_of_security_bleed,
    )

    print(
        "\n[cross-worker] Architecture must not invent security bleed "
        "on Security fixtures …",
        flush=True,
    )
    for key in SECURITY_FIXTURES_EXPECT_ARCHITECTURE_NO_AUTHZ_BLEED:
        fixture_abs = (_REPO_ROOT / key).resolve()
        print(f"  reviewing {key} …", flush=True)
        report = review_architecture(str(fixture_abs))
        assert_architecture_report_clean_of_security_bleed(report, file_path=key)
        print(
            f"  ok no-authz-bleed: {key} "
            f"(accepted={len(report.get('accepted') or [])} "
            f"needs_review={len(report.get('needs_review') or [])})",
            flush=True,
        )

    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Architecture review_architecture against benchmark fixtures."
    )
    parser.add_argument(
        "--include-needs-review",
        action="store_true",
        help="Score needs_review findings too (default: accepted only).",
    )
    parser.add_argument(
        "--label",
        default="architecture_baseline",
        help="Results filename prefix (default: architecture_baseline).",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=None,
        help="Override ground_truth_architecture.json path.",
    )
    args = parser.parse_args(argv)

    load_dotenv(_REPO_ROOT / ".env")
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    key_env = {
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }.get(provider, "GROQ_API_KEY")
    if not (os.getenv(key_env) or "").strip():
        print(
            f"No {key_env} for LLM_PROVIDER={provider}. "
            "Architecture Worker has no offline mode (LLM-only).",
            file=sys.stderr,
        )
        return 2

    run_architecture_benchmark(
        include_needs_review=args.include_needs_review,
        ground_truth_path=args.ground_truth,
        label=args.label,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
