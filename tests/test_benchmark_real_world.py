"""Unit tests for the provenance-backed real-world Security mini-suite.

Scope: manifest shape + ground-truth/disk consistency only. Does not run the
LLM/Semgrep pipeline (see app/benchmark_run_real_world.py for the live run).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.benchmark_run_real_world import (
    _GROUND_TRUTH_PATH,
    _MANIFEST_PATH,
    _REQUIRED_MANIFEST_FIELDS,
    load_manifest,
    validate_manifest,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_manifest_file_exists() -> None:
    assert _MANIFEST_PATH.is_file(), f"missing manifest: {_MANIFEST_PATH}"


def test_manifest_has_required_fields_per_case() -> None:
    manifest = load_manifest()
    cases = manifest.get("cases") or []
    assert cases, "manifest must have at least one case"
    for case in cases:
        for field in _REQUIRED_MANIFEST_FIELDS:
            assert case.get(field), f"case {case.get('id')} missing field {field!r}"


def test_manifest_case_count_within_v1_bound() -> None:
    manifest = load_manifest()
    cases = manifest.get("cases") or []
    assert 1 <= len(cases) <= 6, "v1 real-world suite should stay small (<=6 cases)"


def test_manifest_files_exist_on_disk() -> None:
    manifest = load_manifest()
    for case in manifest.get("cases") or []:
        for field in ("vulnerable_file", "fixed_file"):
            rel = case.get(field)
            assert rel, f"case {case.get('id')} missing {field}"
            assert (_REPO_ROOT / rel).is_file(), f"{field} not found on disk: {rel}"


def test_validate_manifest_reports_no_problems() -> None:
    manifest = load_manifest()
    assert validate_manifest(manifest) == []


def test_validate_manifest_flags_missing_field() -> None:
    manifest = load_manifest()
    broken = json.loads(json.dumps(manifest))
    del broken["cases"][0]["advisory_url"]
    problems = validate_manifest(broken)
    assert any("advisory_url" in problem for problem in problems)


def test_ground_truth_real_world_file_exists() -> None:
    assert _GROUND_TRUTH_PATH.is_file(), f"missing ground truth: {_GROUND_TRUTH_PATH}"


def test_ground_truth_real_world_paths_exist_on_disk() -> None:
    ground_truth = json.loads(_GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    fixtures = ground_truth.get("fixtures") or {}
    assert fixtures, "ground_truth_real_world.json has no fixtures"
    for file_path in fixtures:
        assert (_REPO_ROOT / file_path).is_file(), f"GT path missing on disk: {file_path}"


def test_ground_truth_matches_manifest_expected_types() -> None:
    manifest = load_manifest()
    ground_truth = json.loads(_GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    fixtures = ground_truth.get("fixtures") or {}

    for case in manifest.get("cases") or []:
        vulnerable = case["vulnerable_file"]
        fixed = case["fixed_file"]
        expected_type = case["expected_finding_type"]

        assert vulnerable in fixtures, f"{vulnerable} missing from ground truth"
        vuln_types = {issue["finding_type"] for issue in fixtures[vulnerable]}
        assert expected_type in vuln_types, (
            f"{vulnerable}: expected {expected_type!r} in ground truth, got {vuln_types}"
        )

        assert fixed in fixtures, f"{fixed} missing from ground truth"
        assert fixtures[fixed] == [], f"{fixed} should be a clean/fixed control (empty GT)"
