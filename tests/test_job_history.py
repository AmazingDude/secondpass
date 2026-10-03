"""Durable request discovery through the local HTTP interface."""

from pathlib import Path
from datetime import datetime, timezone
import json
import sqlite3
import threading

import pytest

from fastapi.testclient import TestClient

from app.api import app
from app.jobs import JobStore
from app.persistence import StoredJob, save_job


def test_job_without_worker_reviews_is_discoverable_after_restart(tmp_path: Path, monkeypatch):
    db = tmp_path / "history.db"
    original = JobStore(db_path=db, runner=lambda path: {"summary": {"inconclusive": True}})
    job = original.submit("example.py", memory_enabled=False)
    original.shutdown(wait=True)
    restarted = JobStore(db_path=db)
    monkeypatch.setattr("app.api.job_store", restarted)
    try:
        with TestClient(app) as client:
            response = client.get("/v1/jobs")
            assert response.status_code == 200
            page = response.json()
            assert len(page["jobs"]) == 1
            saved = page["jobs"][0]
            assert saved["job_id"] == job.job_id
            assert saved["path"] == "example.py"
            assert saved["execution_status"] == "completed"
            assert saved["coverage_status"] == "unknown"
            assert "result" not in saved
            assert "options" not in saved
            assert "outcome" not in saved
            assert client.get(f"/reviews/jobs/{job.job_id}").json()["summary"] == {"inconclusive": True}
            assert page["next_before_sequence"] is None
    finally:
        restarted.shutdown(wait=True)


def saved_request(db, job_id, *, status="completed"):
    when = datetime(2026, 10, 1, tzinfo=timezone.utc)
    job = StoredJob(
        job_id=job_id, path="example.py", options={}, status=status,
        created_at=when, updated_at=when,
    )
    save_job(job, db_path=db)
    return job


def test_more_than_200_requests_page_once_despite_new_submissions_and_updates(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    for number in range(205):
        saved_request(db, f"job-{number}")
    store = JobStore(db_path=db)
    monkeypatch.setattr("app.api.job_store", store)
    try:
        with TestClient(app) as client:
            page = client.get("/v1/jobs", params={"limit": 100}).json()
            snapshot = page["snapshot_sequence"]
            seen = [job["job_id"] for job in page["jobs"]]
            # Identical/backdated timestamps cannot admit a new request into
            # this traversal or move an existing request to a different page.
            saved_request(db, "new-request")
            saved_request(db, "job-0", status="failed")
            while page["next_before_sequence"] is not None:
                page = client.get("/v1/jobs", params={
                    "limit": 100, "snapshot_sequence": snapshot,
                    "before_sequence": page["next_before_sequence"],
                }).json()
                assert page["snapshot_sequence"] == snapshot
                assert len(page["jobs"]) <= 100
                seen.extend(job["job_id"] for job in page["jobs"])
            assert seen == [f"job-{number}" for number in range(204, -1, -1)]
            assert page["jobs"][-1]["execution_status"] == "failed"
            assert client.get("/v1/jobs").json()["jobs"][0]["job_id"] == "new-request"
    finally:
        store.shutdown(wait=True)


def test_discovery_recovers_abandoned_requests_but_not_live_executors(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    release = threading.Event()
    started = threading.Event()

    def runner(path):
        started.set()
        assert release.wait(timeout=20)
        return {}

    owner = JobStore(db_path=db, runner=runner)
    reader = JobStore(db_path=db)
    monkeypatch.setattr("app.api.job_store", reader)
    try:
        live = owner.submit("live.py")
        assert started.wait(timeout=5)
        saved_request(db, "abandoned", status="running")
        with TestClient(app) as client:
            jobs = client.get("/v1/jobs").json()["jobs"]
            assert [(job["job_id"], job["execution_status"]) for job in jobs] == [
                ("abandoned", "interrupted"), (live.job_id, "running"),
            ]
            assert all(job["coverage_status"] == "unknown" for job in jobs)
            assert client.get("/reviews/jobs/abandoned").json()["status"] == "interrupted"
            assert owner.get(live.job_id).status == "running"
    finally:
        release.set()
        owner.shutdown(wait=True)
        reader.shutdown(wait=True)


def test_pre_sequence_ledger_migrates_with_payload_and_worker_history_intact(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    when = "2026-10-01T00:00:00Z"
    payload = dict(job_id="old", path="old.py", options={"memory_enabled": False},
                   status="completed", error=None, result={"summary": {"inconclusive": True}},
                   created_at=when, updated_at=when)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE review_jobs (job_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
        conn.execute("INSERT INTO review_jobs VALUES (?, ?)", ("old", json.dumps(payload)))
    store = JobStore(db_path=db)
    monkeypatch.setattr("app.api.job_store", store)
    monkeypatch.setattr("app.api.DEFAULT_DB_PATH", db)
    from app.confidence_gate import apply_confidence_gate
    from app.persistence import save_review
    from app.schema import ReviewResult
    result = ReviewResult(findings=[], file_path="old.py", worker_name="security",
                          timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc))
    saved = save_review(result, apply_confidence_gate(result), db_path=db, job_id="old")
    second = result.model_copy(update={"worker_name": "architecture"})
    other = save_review(second, apply_confidence_gate(second), db_path=db, job_id="old")
    try:
        with TestClient(app) as client:
            assert [job["job_id"] for job in client.get("/v1/jobs").json()["jobs"]] == ["old"]
            assert client.get("/reviews/jobs/old").json()["result"] == payload["result"]
            assert [review["id"] for review in client.get("/v1/runs/old").json()["reviews"]] == [other.id, saved.id]
            assert client.get("/v1/runs").json()["runs"][0]["execution_status"] == "unknown"
    finally:
        store.shutdown(wait=True)


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"snapshot_sequence": -1},
    {"snapshot_sequence": 2**63}, {"before_sequence": 0},
    {"before_sequence": 2**63}, {"snapshot_sequence": 100},
])
def test_discovery_rejects_invalid_page_windows(tmp_path, monkeypatch, params):
    store = JobStore(db_path=tmp_path / "history.db")
    monkeypatch.setattr("app.api.job_store", store)
    try:
        with TestClient(app) as client:
            assert client.get("/v1/jobs", params=params).status_code == 422
    finally:
        store.shutdown(wait=True)


def test_empty_job_history_has_a_complete_empty_page(tmp_path, monkeypatch):
    store = JobStore(db_path=tmp_path / "history.db")
    monkeypatch.setattr("app.api.job_store", store)
    try:
        with TestClient(app) as client:
            assert client.get("/v1/jobs").json() == {
                "schema_version": 1, "jobs": [], "snapshot_sequence": 0,
                "next_before_sequence": None,
            }
    finally:
        store.shutdown(wait=True)
