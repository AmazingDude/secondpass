"""Run Security-path detection-quality eval against the real-world mini-suite.

Separate from app/benchmark_run.py and benchmark/ground_truth.json on purpose:
this suite scores secondpass against provenance-backed real (not planted)
vulnerable code from benchmark/real_world/manifest.json, using
benchmark/ground_truth_real_world.json for the same {file_path, finding_type}
scoring shape. Results are written under benchmark/results/ with the
"real_world" label so they never collide with the main suite's history.

Scoring uses accepted findings only (confidence gate >= threshold), same as
app/benchmark_run.py. See benchmark/real_world/README.md for scope/limits —
this is a small, single-file, Security-only check, not a general reliability
claim.
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
    SEMGREP_TO_BENCHMARK_TYPE,
    _live_review,
    _offline_review,
    predictions_from_report_items,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_WORLD_DIR = _REPO_ROOT / "benchmark" / "real_world"
_MANIFEST_PATH = _REAL_WORLD_DIR / "manifest.json"
_GROUND_TRUTH_PATH = _REPO_ROOT / "benchmark" / "ground_truth_real_world.json"
_RESULTS_DIR = _REPO_ROOT / "benchmark" / "results"

_REQUIRED_MANIFEST_FIELDS = (
    "id",
    "vulnerable_file",
    "fixed_file",
    "source_repo",
    "vulnerable_commit",
    "license",
    "advisory",
    "advisory_url",
    "expected_finding_type",
    "why_visible_single_file",
)


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    target = path or _MANIFEST_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return a list of validation problems (empty = manifest is well-formed)."""
    problems: list[str] = []
    cases = manifest.get("cases") or []
    if not cases:
        problems.append("manifest has no cases")
    for case in cases:
        case_id = case.get("id", "<missing id>")
        for field in _REQUIRED_MANIFEST_FIELDS:
            if not case.get(field):
                problems.append(f"case {case_id}: missing required field {field!r}")
        for file_field in ("vulnerable_file", "fixed_file"):
            rel = case.get(file_field)
            if rel and not (_REPO_ROOT / rel).is_file():
                problems.append(f"case {case_id}: {file_field} not found on disk: {rel}")
    return problems


def list_fixture_paths(ground_truth: dict[str, Any]) -> list[str]:
    fixtures = ground_truth.get("fixtures") or {}
    return sorted(fixtures.keys())


def run_benchmark(
    *,
    offline: bool = False,
    include_needs_review: bool = False,
    label: str = "real_world",
) -> dict[str, Any]:
    load_dotenv(_REPO_ROOT / ".env")
    manifest = load_manifest()
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("real-world manifest is invalid:\n" + "\n".join(problems))

    ground_truth = load_ground_truth(_GROUND_TRUTH_PATH)
    fixture_keys = list_fixture_paths(ground_truth)
    if not fixture_keys:
        raise ValueError("ground_truth_real_world.json has no fixtures")

    mode = "offline_semgrep" if offline else "review_code"
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

        print(f"[{mode}] reviewing {fixture_key} …", flush=True)
        try:
            report = _offline_review(fixture_abs) if offline else _live_review(fixture_abs)
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

        predictions = predictions_from_report_items(scored_items, fixture_path=fixture_key)
        all_predictions.extend(predictions)

        expected = [
            issue["finding_type"]
            for issue in (ground_truth.get("fixtures") or {}).get(fixture_key, [])
        ]
        predicted_types = [item.finding_type for item in predictions]
        note = {
            "file_path": fixture_key,
            "mode": mode,
            "static_scan_error": report.get("static_scan_error"),
            "used_logic_fallback": report.get("used_logic_fallback"),
            "used_logic_review": report.get("used_logic_review"),
            "inconclusive": report.get("inconclusive"),
            "accepted_count": len(accepted),
            "needs_review_count": len(needs_review),
            "scored_bucket": "accepted+needs_review" if include_needs_review else "accepted",
            "expected_finding_types": expected,
            "predicted_finding_types": predicted_types,
            "accepted_raw_types": [
                (item.get("structured_finding") or {}).get("finding_type") for item in accepted
            ],
            "needs_review_raw_types": [
                (item.get("structured_finding") or {}).get("finding_type")
                for item in needs_review
            ],
            "message": report.get("message"),
        }
        per_file.append(note)
        print(
            f"  accepted={len(accepted)} needs_review={len(needs_review)} "
            f"predicted={predicted_types} expected={expected}",
            flush=True,
        )

    score: ScoreReport = evaluate(all_predictions, ground_truth)
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    out_path = _RESULTS_DIR / f"{label}_{stamp}.json"

    payload = {
        "label": label,
        "date": stamp,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "suite": "real_world",
        "scored_bucket": "accepted+needs_review" if include_needs_review else "accepted",
        "finding_type_mapping": SEMGREP_TO_BENCHMARK_TYPE,
        "ground_truth": str(_GROUND_TRUTH_PATH),
        "manifest": str(_MANIFEST_PATH),
        "provider": os.getenv("LLM_PROVIDER", "groq") if not offline else None,
        "model": (os.getenv("LLM_MODEL") or None) if not offline else None,
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
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Security review_code against the real-world mini-suite."
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Semgrep mappings only (no LLM / Supervisor).",
    )
    parser.add_argument(
        "--include-needs-review",
        action="store_true",
        help="Score needs_review findings too (default: accepted only).",
    )
    parser.add_argument(
        "--label",
        default="real_world",
        help="Results filename prefix (default: real_world).",
    )
    args = parser.parse_args(argv)

    if not args.offline:
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
                "Pass --offline for Semgrep-only eval, or set the key.",
                file=sys.stderr,
            )
            return 2

    run_benchmark(
        offline=args.offline,
        include_needs_review=args.include_needs_review,
        label=args.label,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
