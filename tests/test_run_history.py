"""Read-only legacy run discovery through the local HTTP interface."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.confidence_gate import apply_confidence_gate
from app.persistence import save_audit_event, save_review, save_verified_outcome
from app.schema import Finding, ReviewResult


@pytest.fixture()
def history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Path]]:
    db = tmp_path / "history.db"
    monkeypatch.setattr("app.api.DEFAULT_DB_PATH", db)
    with TestClient(app) as client:
        yield client, db


def record(
    db: Path, job_id: str | None, *, path: str = "a.py", worker: str = "security",
):
    result = ReviewResult(
        findings=[], file_path=path, worker_name=worker,
        timestamp=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    return save_review(result, apply_confidence_gate(result), db_path=db, job_id=job_id)


def test_shared_job_id_groups_saved_workers_and_files_once(history):
    client, db = history
    first = record(db, "old-directory")
    record(db, "old-directory", worker="architecture")
    last = record(db, "old-directory", path="b.py")
    other = record(db, "different-job")
    unlinked = record(db, None)

    response = client.get("/v1/runs")
    assert response.status_code == 200
    assert response.json()["runs"] == [
        {
            "job_id": "different-job", "metadata_status": "legacy",
            "execution_status": "unknown", "coverage_status": "unknown",
            "review_count": 1, "latest_review_id": other.id,
        },
        {
            "job_id": "old-directory", "metadata_status": "legacy",
            "execution_status": "unknown", "coverage_status": "unknown",
            "review_count": 3, "latest_review_id": last.id,
        },
    ]
    assert client.get(f"/reviews/{first.id}").status_code == 200
    assert client.get(f"/reviews/{unlinked.id}").status_code == 200


def test_saved_run_detail_is_available_without_a_live_job(history):
    client, db = history
    first = record(db, "old-run")
    second = record(db, "old-run", worker="architecture")
    record(db, "other-run")

    # No submission to the in-memory job store: results predate this client.
    assert client.get("/reviews/jobs/old-run").status_code == 404
    response = client.get("/v1/runs/old-run")
    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["review_count"] == 2
    assert payload["run"]["execution_status"] == "unknown"
    assert [item["id"] for item in payload["reviews"]] == [second.id, first.id]
    saved = payload["reviews"][1]
    original = client.get(f"/reviews/{first.id}").json()
    assert datetime.fromisoformat(saved["created_at"]) == datetime.fromisoformat(original["created_at"])
    saved["created_at"] = original["created_at"]
    assert saved == original
    assert client.get("/v1/runs/nonexistent").status_code == 404


def test_run_pages_cover_more_than_200_groups_without_chasing_new_records(history):
    client, db = history
    for number in range(205):
        record(db, f"run-{number}")
    page = client.get("/v1/runs", params={"limit": 100}).json()
    seen = [item["job_id"] for item in page["runs"]]
    snapshot = page["snapshot_review_id"]
    # A new run and a new row for an older run cannot reorder this traversal.
    record(db, "new-run")
    record(db, "run-0", worker="architecture")
    while page["next_before_review_id"] is not None:
        page = client.get("/v1/runs", params={
            "limit": 100, "snapshot_review_id": snapshot,
            "before_review_id": page["next_before_review_id"],
        }).json()
        assert page["snapshot_review_id"] == snapshot
        assert len(page["runs"]) <= 100
        seen.extend(item["job_id"] for item in page["runs"])
    assert seen == [f"run-{number}" for number in range(204, -1, -1)]
    assert page["runs"][-1]["review_count"] == 1


def test_detail_pages_preserve_all_worker_rows_at_the_saved_snapshot(history):
    client, db = history
    saved_ids = [record(db, "large-run", path=f"file-{i}.py").id for i in range(205)]
    page = client.get("/v1/runs/large-run", params={"limit": 100}).json()
    snapshot = page["snapshot_review_id"]
    seen = [item["id"] for item in page["reviews"]]
    record(db, "large-run", path="new.py")
    while page["next_before_review_id"] is not None:
        page = client.get("/v1/runs/large-run", params={
            "limit": 100, "snapshot_review_id": snapshot,
            "before_review_id": page["next_before_review_id"],
        }).json()
        assert page["run"]["review_count"] == 205
        assert len(page["reviews"]) <= 100
        seen.extend(item["id"] for item in page["reviews"])
    assert seen == list(reversed(saved_ids))


@pytest.mark.parametrize("coverage,claim,has_finding", [
    (None, None, False), ("ok", None, False),
    ("inconclusive", None, False), ("inconclusive", None, True),
    ("ok", "unverified", False),
])
def test_legacy_runs_do_not_invent_completion_or_clean_outcomes(
    history, coverage, claim, has_finding,
):
    client, db = history
    finding = Finding(
        finding_type="command_injection", evidence="subprocess.run uses shell=True",
        confidence=90, suggested_fix="Use an argument list without a shell",
        detection_method="static_rule",
    )
    result = ReviewResult(
        findings=[finding] if has_finding else [], file_path="a.py",
        worker_name="security", timestamp=datetime(2026, 7, 28, tzinfo=timezone.utc),
        coverage_status=coverage, claim_status=claim,
    )
    saved = save_review(result, apply_confidence_gate(result), job_id="legacy", db_path=db)
    outcome = save_verified_outcome(
        finding, accepted=False, reason="Tracked human decision", file_path="a.py",
        review_id=saved.id, db_path=db,
    )
    for run in (
        client.get("/v1/runs").json()["runs"][0],
        client.get("/v1/runs/legacy").json()["run"],
    ):
        assert run["metadata_status"] == "legacy"
        assert run["execution_status"] == "unknown"
        assert run["coverage_status"] == "unknown"
        assert "outcome" not in run
        assert "started_at" not in run
        assert "options" not in run
    recorded = client.get("/v1/runs/legacy").json()["reviews"][0]["review_result"]
    assert recorded == result.model_dump(mode="json")
    decisions = client.get("/outcomes", params={"file_path": "a.py"}).json()["outcomes"]
    assert decisions[0]["id"] == outcome.id
    assert decisions[0]["review_id"] == saved.id


def test_unlinked_reviews_and_audit_only_jobs_are_not_fabricated_runs(history):
    client, db = history
    for job_id in (None, "", "   "):
        saved = record(db, job_id)
        assert client.get(f"/reviews/{saved.id}").status_code == 200
    save_audit_event("audit-only", "review_start", db_path=db)
    assert client.get("/v1/runs").json()["runs"] == []
    assert client.get("/v1/runs/audit-only").status_code == 404
    assert client.get("/reviews/jobs/audit-only/audit").status_code == 200


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"limit": "invalid"},
    {"snapshot_review_id": -1}, {"snapshot_review_id": 2**63},
    {"before_review_id": 0}, {"before_review_id": 2**63},
])
@pytest.mark.parametrize("route", ["/v1/runs", "/v1/runs/old-run"])
def test_history_rejects_invalid_page_windows(history, route, params):
    client, _ = history
    assert client.get(route, params=params).status_code == 422


def test_empty_history_returns_a_complete_empty_page(history):
    client, _ = history
    assert client.get("/v1/runs").json() == {
        "schema_version": 1, "runs": [], "snapshot_review_id": 0,
        "next_before_review_id": None,
    }


def test_exact_lookup_accepts_a_recorded_job_id_containing_slashes(history):
    client, db = history
    saved = record(db, "team/review")
    assert client.get("/v1/runs").json()["runs"][0]["job_id"] == "team/review"
    response = client.get(f"/v1/runs/{quote('team/review', safe='')}")
    assert response.status_code == 200
    assert response.json()["reviews"][0]["id"] == saved.id


@pytest.mark.parametrize("route", ["/v1/runs", "/v1/runs/saved-run"])
def test_history_rejects_a_snapshot_ahead_of_saved_records(history, route):
    client, db = history
    saved = record(db, "saved-run")
    response = client.get(route, params={"snapshot_review_id": saved.id + 100})
    assert response.status_code == 422
    assert response.json()["detail"] == "History snapshot exceeds saved review records"
