"""Run Security-path detection-quality eval against benchmark fixtures.

Scoring uses accepted findings only (confidence gate ≥ threshold). needs_review
findings are recorded in per-file notes for inspection but do not count toward
precision/recall unless --include-needs-review is passed.

finding_type alignment: Semgrep rule ids are mapped to ground-truth labels via
SEMGREP_TO_BENCHMARK_TYPE (e.g. subprocess-shell-true → command_injection).
Logic-review finding_type values are used as-is when they already match labels
like missing_ownership_check.
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

from app.benchmark import (
    DEFAULT_GROUND_TRUTH_PATH,
    PredictedFinding,
    ScoreReport,
    evaluate,
    load_ground_truth,
)
from app.scanner import ScanError, run_static_scan

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES_DIR = _REPO_ROOT / "benchmark" / "fixtures"
_RESULTS_DIR = _REPO_ROOT / "benchmark" / "results"

# Map Semgrep rule id fragments → ground_truth finding_type labels.
# Prefer normalizing predictions to GT labels rather than rewriting ground truth
# every time a rule pack renames an id.
SEMGREP_TO_BENCHMARK_TYPE: dict[str, str] = {
    "subprocess-shell-true": "command_injection",
    "subprocess-shell": "command_injection",
    "os-system": "command_injection",
    "command-injection": "command_injection",
    "shell-injection": "command_injection",
}


def normalize_benchmark_finding_type(raw_type: str) -> str:
    """Map a Security finding_type (rule id or LLM label) to a GT label when known."""
    text = (raw_type or "").strip()
    if not text:
        return "unknown"
    lowered = text.lower().replace("_", "-")
    for fragment, label in SEMGREP_TO_BENCHMARK_TYPE.items():
        if fragment in lowered:
            return label
    return text


def fixture_relative_path(path: str | Path, *, repo_root: Path = _REPO_ROOT) -> str:
    """Normalize an absolute/relative path to repo-relative forward-slash form."""
    candidate = Path(path)
    try:
        if candidate.is_absolute():
            relative = candidate.resolve().relative_to(repo_root.resolve())
        else:
            relative = Path(str(candidate).replace("\\", "/"))
    except ValueError:
        # Fall back to trailing benchmark/fixtures/... if present.
        normalized = str(path).replace("\\", "/")
        marker = "benchmark/fixtures/"
        if marker in normalized:
            return marker + normalized.split(marker, 1)[1]
        return normalized
    return relative.as_posix()


def predictions_from_report_items(
    items: list[dict[str, Any]],
    *,
    fixture_path: str,
) -> list[PredictedFinding]:
    """Convert enriched report findings into PredictedFinding rows for one fixture."""
    predictions: list[PredictedFinding] = []
    seen: set[str] = set()
    for item in items:
        structured = item.get("structured_finding") or {}
        raw_type = str(
            structured.get("finding_type")
            or (item.get("finding") or {}).get("rule_id")
            or ""
        )
        finding_type = normalize_benchmark_finding_type(raw_type)
        if finding_type in seen:
            continue
        seen.add(finding_type)
        predictions.append(
            PredictedFinding(file_path=fixture_path, finding_type=finding_type)
        )
    return predictions


def _offline_review(fixture_abs: Path) -> dict[str, Any]:
    """Semgrep-only review: map static findings, no LLM / Supervisor."""
    from app.agent import map_semgrep_finding
    from app.confidence_gate import apply_confidence_gate
    from app.schema import ReviewResult

    scan_error: str | None = None
    findings: list[Any] = []
    try:
        findings = run_static_scan([str(fixture_abs)])
    except ScanError as exc:
        scan_error = str(exc)

    structured = [map_semgrep_finding(finding) for finding in findings]
    review_result = ReviewResult(
        findings=structured,
        file_path=str(fixture_abs),
        timestamp=datetime.now(timezone.utc),
        worker_name="security",
    )
    gate = apply_confidence_gate(review_result)
    accepted = [
        {
            "finding": finding,
            "structured_finding": mapped.model_dump(mode="json"),
        }
        for finding, mapped in zip(findings, structured)
        if mapped.confidence >= gate.threshold
    ]
    needs_review = [
        {
            "finding": finding,
            "structured_finding": mapped.model_dump(mode="json"),
        }
        for finding, mapped in zip(findings, structured)
        if mapped.confidence < gate.threshold
    ]
    return {
        "path": str(fixture_abs),
        "accepted": accepted,
        "needs_review": needs_review,
        "static_scan_error": scan_error,
        "used_logic_fallback": False,
        "offline": True,
        "finding_count": len(structured),
        "no_issues": len(structured) == 0,
        "message": None if structured else "No static findings (offline).",
    }


def _live_review(fixture_abs: Path) -> dict[str, Any]:
    from app.agent import review_code

    return review_code(str(fixture_abs))


def list_fixture_paths(ground_truth: dict[str, Any]) -> list[str]:
    fixtures = ground_truth.get("fixtures") or {}
    return sorted(fixtures.keys())


def run_benchmark(
    *,
    offline: bool = False,
    include_needs_review: bool = False,
    ground_truth_path: Path | None = None,
    results_dir: Path | None = None,
    label: str = "baseline",
) -> dict[str, Any]:
    """Review each fixture, score predictions, write a results JSON file."""
    load_dotenv(_REPO_ROOT / ".env")
    ground_truth = load_ground_truth(ground_truth_path)
    fixture_keys = list_fixture_paths(ground_truth)
    if not fixture_keys:
        raise ValueError("ground_truth.json has no fixtures")

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
            "mode": mode,
            "static_scan_error": report.get("static_scan_error"),
            "used_logic_fallback": report.get("used_logic_fallback"),
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
        "mode": mode,
        "scored_bucket": (
            "accepted+needs_review" if include_needs_review else "accepted"
        ),
        "finding_type_mapping": SEMGREP_TO_BENCHMARK_TYPE,
        "ground_truth": str(
            Path(ground_truth_path) if ground_truth_path else DEFAULT_GROUND_TRUTH_PATH
        ),
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

    if not offline:
        from app.benchmark_cross_worker import (
            ARCHITECTURE_FIXTURES_EXPECT_SECURITY_CLEAN,
            assert_security_report_clean_of_architecture_bleed,
        )

        print("\n[cross-worker] Security must stay clean on Architecture fixtures …", flush=True)
        for key in ARCHITECTURE_FIXTURES_EXPECT_SECURITY_CLEAN:
            note = next((row for row in per_file if row.get("file_path") == key), None)
            if note is None:
                continue
            if note.get("error"):
                raise RuntimeError(
                    f"cross-worker Security review failed for {key}: {note['error']}"
                )
            fake_report = {
                "accepted": [
                    {
                        "structured_finding": {
                            "finding_type": t,
                            "evidence": "",
                            "suggested_fix": "",
                        }
                    }
                    for t in (note.get("accepted_raw_types") or [])
                    if t
                ],
                "needs_review": [
                    {
                        "structured_finding": {
                            "finding_type": t,
                            "evidence": "",
                            "suggested_fix": "",
                        }
                    }
                    for t in (note.get("needs_review_raw_types") or [])
                    if t
                ],
            }
            assert_security_report_clean_of_architecture_bleed(
                fake_report, file_path=key
            )
            if (note.get("accepted_count") or 0) or (note.get("needs_review_count") or 0):
                raise AssertionError(
                    f"Security reported findings on architecture fixture {key} "
                    f"(accepted={note.get('accepted_count')} "
                    f"needs_review={note.get('needs_review_count')})"
                )
            print(f"  ok clean: {key}", flush=True)

    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Security review_code against benchmark fixtures."
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
        default="baseline",
        help="Results filename prefix (default: baseline).",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=None,
        help="Override ground_truth.json path.",
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
        ground_truth_path=args.ground_truth,
        label=args.label,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
