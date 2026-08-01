"""Standing cross-worker category-bleed checks for the benchmark suite.

Security must stay clean on Architecture fixtures (no invented layering /
business-rule labels). Architecture must not emit security/authz-flavored
findings on Security fixtures. Filter-level asserts run offline; live runners
also score these as empty expected rows in the respective ground_truth files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent import is_architecture_category_bleed
from app.workers.architecture_worker import is_security_category_bleed

_REPO_ROOT = Path(__file__).resolve().parent.parent

ARCHITECTURE_FIXTURES_EXPECT_SECURITY_CLEAN = (
    "benchmark/fixtures/architecture/checkout_handler.py",
    "benchmark/fixtures/architecture/low_level_persistence_client.py",
    "benchmark/fixtures/architecture/clean_price_formatter.py",
    "benchmark/fixtures/architecture/inventory_data_store.py",
    "benchmark/fixtures/architecture/inventory_service.py",
    "benchmark/fixtures/architecture/high_level_order_workflow.py",
)

SECURITY_FIXTURES_EXPECT_ARCHITECTURE_NO_AUTHZ_BLEED = (
    "benchmark/fixtures/notes_idor.py",
    "benchmark/fixtures/ops_shell.py",
    "benchmark/fixtures/hardcoded_secret.py",
    "benchmark/fixtures/path_traversal.py",
    "benchmark/fixtures/clean_ownership.py",
)


def security_items_have_architecture_bleed(items: list[dict[str, Any]]) -> list[str]:
    """Return finding_type labels that look like Architecture bleed."""
    bled: list[str] = []
    for item in items:
        structured = item.get("structured_finding") or {}
        finding_type = str(structured.get("finding_type") or "")
        if is_architecture_category_bleed(
            finding_type=finding_type,
            message=str(structured.get("evidence") or ""),
            evidence=str(structured.get("evidence") or ""),
            suggested_fix=str(structured.get("suggested_fix") or ""),
        ):
            bled.append(finding_type or "(untyped)")
    return bled


def architecture_findings_have_security_bleed(
    findings: list[Any],
) -> list[str]:
    """Return finding_type labels that look like Security/authz bleed."""
    bled: list[str] = []
    for finding in findings:
        if hasattr(finding, "model_dump"):
            payload = finding.model_dump()
        elif isinstance(finding, dict):
            payload = finding
        else:
            continue
        finding_type = str(payload.get("finding_type") or "")
        if is_security_category_bleed(
            finding_type=finding_type,
            message=str(payload.get("evidence") or ""),
            evidence=str(payload.get("evidence") or ""),
            suggested_fix=str(payload.get("suggested_fix") or ""),
        ):
            bled.append(finding_type or "(untyped)")
    return bled


def assert_security_report_clean_of_architecture_bleed(
    report: dict[str, Any],
    *,
    file_path: str,
) -> None:
    items = list(report.get("accepted") or []) + list(report.get("needs_review") or [])
    bled = security_items_have_architecture_bleed(items)
    if bled:
        raise AssertionError(
            f"Security invented architecture-bleed finding(s) on {file_path}: {bled}"
        )


def assert_architecture_report_clean_of_security_bleed(
    report: dict[str, Any],
    *,
    file_path: str,
) -> None:
    gate = report.get("gate_result") or {}
    findings: list[Any] = []
    if isinstance(gate, dict):
        findings.extend(gate.get("accepted") or [])
        findings.extend(gate.get("needs_review") or [])
    for item in list(report.get("accepted") or []) + list(report.get("needs_review") or []):
        structured = item.get("structured_finding")
        if structured is not None:
            findings.append(structured)
    bled = architecture_findings_have_security_bleed(findings)
    if bled:
        raise AssertionError(
            f"Architecture invented security-bleed finding(s) on {file_path}: {bled}"
        )


def run_cross_worker_bleed_checks(*, live: bool = False) -> dict[str, Any]:
    """Run standing cross-worker checks.

    When live=False, only validates that fixture paths exist (filter coverage
    lives in unit tests). When live=True, runs real review_code /
    review_architecture and asserts no reverse-category bleed.
    """
    missing = [
        key
        for key in (
            *ARCHITECTURE_FIXTURES_EXPECT_SECURITY_CLEAN,
            *SECURITY_FIXTURES_EXPECT_ARCHITECTURE_NO_AUTHZ_BLEED,
        )
        if not (_REPO_ROOT / key).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"cross-worker fixtures missing: {missing}")

    payload: dict[str, Any] = {
        "live": live,
        "security_on_architecture": [],
        "architecture_on_security": [],
    }
    if not live:
        payload["status"] = "fixtures_present"
        return payload

    from app.agent import review_architecture, review_code

    for key in ARCHITECTURE_FIXTURES_EXPECT_SECURITY_CLEAN:
        print(f"[cross-worker] Security review {key} …", flush=True)
        report = review_code(str(_REPO_ROOT / key))
        assert_security_report_clean_of_architecture_bleed(report, file_path=key)
        accepted = len(report.get("accepted") or [])
        needs = len(report.get("needs_review") or [])
        if accepted or needs:
            # Clean of architecture bleed, but still unexpected security noise.
            raise AssertionError(
                f"Security reported {accepted} accepted / {needs} needs_review "
                f"on architecture fixture {key} (expected clean)"
            )
        payload["security_on_architecture"].append(
            {"file_path": key, "accepted": 0, "needs_review": 0}
        )

    for key in SECURITY_FIXTURES_EXPECT_ARCHITECTURE_NO_AUTHZ_BLEED:
        print(f"[cross-worker] Architecture review {key} …", flush=True)
        report = review_architecture(str(_REPO_ROOT / key))
        assert_architecture_report_clean_of_security_bleed(report, file_path=key)
        payload["architecture_on_security"].append(
            {
                "file_path": key,
                "accepted": len(report.get("accepted") or []),
                "needs_review": len(report.get("needs_review") or []),
            }
        )

    payload["status"] = "ok"
    return payload


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Standing cross-worker category-bleed regression checks."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run real Security/Architecture reviews (needs LLM).",
    )
    args = parser.parse_args()
    result = run_cross_worker_bleed_checks(live=args.live)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
