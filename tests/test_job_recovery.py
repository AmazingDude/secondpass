"""Terminal job lookup survives a fresh executor without replaying reviews."""

import json
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.jobs import JobStore, JobSubmissionError


def test_running_job_is_visible_to_another_store_without_interrupting_it(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(path: str) -> dict:
        started.set()
        assert release.wait(timeout=10)
        return {"summary": {"inconclusive": True}}

    db_path = tmp_path / "reviews.db"
    owner = JobStore(db_path=db_path, runner=runner)
    reader = JobStore(db_path=db_path)
    try:
        job = owner.submit("example.py", memory_enabled=False)
        assert started.wait(timeout=5)
        visible = reader.get(job.job_id)
        assert visible is not None
        assert visible.status == "running"
        assert visible.options["memory_enabled"] is False
        assert owner.get(job.job_id).status == "running"
    finally:
        release.set()
        owner.shutdown(wait=True)
        reader.shutdown(wait=True)


def test_completed_job_survives_restart_without_replaying(tmp_path: Path) -> None:
    db_path = tmp_path / "reviews.db"
    report = {
        "persisted_review_ids": {"security": 17},
        "summary": {"accepted_count": 1, "inconclusive": True},
    }
    store = JobStore(db_path=db_path, runner=lambda path: report)
    job = store.submit("example.py", memory_enabled=False, include=("*.py",))
    store.shutdown(wait=True)
    original = json.loads(json.dumps(store.get(job.job_id).to_dict()))

    def must_not_run(path: str) -> dict:
        raise AssertionError("Looking up history must not replay a review")

    restarted = JobStore(db_path=db_path, runner=must_not_run)
    try:
        recovered = restarted.get(job.job_id)
        assert recovered is not None
        assert recovered.to_dict() == original
        assert recovered.status == "completed"
        assert recovered.options["memory_enabled"] is False
        assert recovered.options["include"] == ["*.py"]
        assert recovered.result == report
        assert recovered.created_at == job.created_at
    finally:
        restarted.shutdown(wait=True)


@pytest.mark.parametrize("fails", [False, True])
def test_polling_terminal_job_survives_fresh_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool,
) -> None:
    target = tmp_path / "example.py"
    target.write_text("pass\n", encoding="utf-8")
    db_path = tmp_path / "reviews.db"
    report = {
        "persisted_review_ids": {},
        "summary": {"accepted_count": 0, "inconclusive": True},
        "security": {"coverage_status": "inconclusive", "findings": []},
    }

    def runner(path: str) -> dict:
        if fails:
            raise RuntimeError("fake-secret-canary-must-not-survive")
        return report

    store = JobStore(db_path=db_path, runner=runner)
    monkeypatch.setattr("app.api.job_store", store)
    with TestClient(app) as client:
        response = client.post(
            "/reviews", json={"path": str(target), "memory_enabled": False},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        store.shutdown(wait=True)
        original = client.get(f"/reviews/jobs/{job_id}").json()

        restarted = JobStore(db_path=db_path, runner=runner)
        monkeypatch.setattr("app.api.job_store", restarted)
        try:
            recovered = client.get(f"/reviews/jobs/{job_id}")
            assert recovered.status_code == 200
            assert recovered.json() == original
            assert recovered.json()["options"]["memory_enabled"] is False
            if fails:
                assert recovered.json()["status"] == "failed"
                assert recovered.json()["error"] == "Review failed. Check your setup and retry."
                assert "fake-secret" not in recovered.text
                assert "result" not in recovered.json()
                assert "summary" not in recovered.json()
            else:
                assert recovered.json()["status"] == "completed"
                assert recovered.json()["result"] == report
            assert client.get("/reviews/jobs/missing-job").status_code == 404
        finally:
            restarted.shutdown(wait=True)


def test_storage_failure_is_not_advertised_as_saved_completion(tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked", encoding="utf-8")
    store = JobStore(db_path=blocked / "reviews.db", runner=lambda path: {"summary": {}})
    try:
        with pytest.raises(JobSubmissionError, match="Could not save the review request") as failure:
            store.submit("example.py")
        assert str(blocked) not in str(failure.value)
    finally:
        store.shutdown(wait=True)


def test_api_does_not_accept_a_request_when_storage_fails(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "example.py"
    target.write_text("pass\n", encoding="utf-8")
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked", encoding="utf-8")
    called = threading.Event()
    store = JobStore(db_path=blocked / "reviews.db", runner=lambda path: called.set())
    monkeypatch.setattr("app.api.job_store", store)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/reviews", json={"path": str(target)})
        assert response.status_code == 503
        assert response.json() == {
            "detail": "Could not save the review request. Check local storage and retry."
        }
        assert not called.is_set()
    finally:
        store.shutdown(wait=True)


def test_crashed_owner_recovers_running_and_queued_work_without_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "reviews.db"
    ready = tmp_path / "ready.json"
    child_code = '''
import json, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path
from app.jobs import JobStore
from app.schema import Finding, ReviewResult
from app.confidence_gate import apply_confidence_gate
from app.persistence import save_review
started = threading.Event()
review_ids = []
def runner(path, job_id):
    result = ReviewResult(
        findings=[Finding(finding_type="shell_injection", evidence="known static candidate",
                          confidence=95, suggested_fix="Use argument lists", detection_method="static_rule")],
        file_path=path, worker_name="security", coverage_status="inconclusive",
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    review_ids.append(save_review(result, apply_confidence_gate(result), db_path=sys.argv[1], job_id=job_id).id)
    started.set()
    time.sleep(120)
    return {"summary": {}}
store = JobStore(db_path=sys.argv[1], max_workers=1, runner=runner)
running = store.submit("running.py", memory_enabled=False)
assert started.wait(20)
queued = store.submit("queued.py", memory_enabled=False)
Path(sys.argv[2]).write_text(json.dumps([running.job_id, queued.job_id, review_ids[0]]))
time.sleep(120)
'''
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(db_path), str(ready)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    replayed = threading.Event()
    reader = JobStore(db_path=db_path, runner=lambda path: replayed.set())
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.02)
        assert ready.exists(), "Child failed to submit the fixture jobs"
        running_id, queued_id, review_id = json.loads(ready.read_text())
        assert reader.get(running_id).status == "running"
        assert reader.get(queued_id).status == "queued"
        process.kill()
        process.communicate(timeout=10)
        for job_id in (running_id, queued_id):
            recovered = reader.get(job_id)
            assert recovered.status == "interrupted"
            assert recovered.options["memory_enabled"] is False
            assert recovered.error.startswith("Review interrupted.")
            assert "result" not in recovered.to_dict()
            assert "summary" not in recovered.to_dict()
        assert not replayed.is_set()
        from app.persistence import get_review
        saved_review = get_review(review_id, db_path=db_path)
        assert saved_review.job_id == running_id
        assert saved_review.review_result.coverage_status == "inconclusive"
        assert saved_review.review_result.findings[0].evidence == "known static candidate"
        restarted = JobStore(db_path=db_path)
        try:
            assert restarted.get(running_id).status == "interrupted"
        finally:
            restarted.shutdown(wait=True)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        reader.shutdown(wait=True)


def test_previous_terminal_job_table_migrates_without_changing_response(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    payload = {
        "job_id": "old-job", "path": "old.py", "options": {"memory_enabled": False},
        "status": "completed", "error": None,
        "result": {"summary": {"inconclusive": True}, "persisted_review_ids": {"security": 17}},
        "created_at": "2026-10-01T10:00:00+00:00", "updated_at": "2026-10-01T10:01:00+00:00",
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE terminal_jobs (job_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
        conn.execute("INSERT INTO terminal_jobs VALUES (?, ?)", ("old-job", json.dumps(payload)))
    store = JobStore(db_path=db_path)
    try:
        recovered = store.get("old-job").to_dict()
        assert recovered["status"] == "completed"
        assert recovered["result"] == payload["result"]
        assert recovered["options"] == payload["options"]
        assert recovered["created_at"] == payload["created_at"]
        assert recovered["updated_at"] == payload["updated_at"]
    finally:
        store.shutdown(wait=True)


def test_concurrent_processes_can_open_previous_job_databases(tmp_path: Path) -> None:
    payload = {
        "job_id": "old-job", "path": "old.py", "options": {}, "status": "failed",
        "error": "Review failed. Check your setup and retry.", "result": None,
        "created_at": "2026-10-01T10:00:00Z", "updated_at": "2026-10-01T10:01:00Z",
    }
    for index in range(8):
        with sqlite3.connect(tmp_path / f"old-{index}.db") as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE terminal_jobs (job_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
            conn.execute("INSERT INTO terminal_jobs VALUES (?, ?)", ("old-job", json.dumps(payload)))
    code = '''
import json, sys, time
from pathlib import Path
from app.jobs import JobStore
root = Path(sys.argv[1]); worker = sys.argv[2]; answers = []
for index in range(8):
    (root / f"ready-{index}-{worker}").touch()
    while not (root / f"go-{index}").exists(): time.sleep(0.001)
    store = JobStore(db_path=root / f"old-{index}.db")
    try:
        job = store.get("old-job")
        answers.append(job.status if job else "missing")
    except Exception as error:
        answers.append(type(error).__name__ + ": " + str(error))
    finally:
        store.shutdown(wait=True)
print(json.dumps(answers))
'''
    processes = [subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path), str(worker)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for worker in range(4)]
    try:
        for index in range(8):
            deadline = time.monotonic() + 30
            while not all((tmp_path / f"ready-{index}-{worker}").exists() for worker in range(4)):
                assert time.monotonic() < deadline, "Concurrent fixture failed to become ready"
                time.sleep(0.01)
            (tmp_path / f"go-{index}").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            assert json.loads(stdout) == ["failed"] * 8
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def test_shutdown_interrupts_queued_job_but_keeps_running_owner_until_it_finishes(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(path: str) -> dict:
        started.set()
        assert release.wait(timeout=10)
        return {"summary": {"inconclusive": True}}

    db_path = tmp_path / "reviews.db"
    store = JobStore(db_path=db_path, max_workers=1, runner=runner)
    reader = JobStore(db_path=db_path)
    try:
        running = store.submit("running.py")
        assert started.wait(timeout=5)
        queued = store.submit("queued.py")
        store.shutdown(wait=False)
        assert reader.get(queued.job_id).status == "interrupted"
        assert reader.get(running.job_id).status == "running"
        with pytest.raises(JobSubmissionError, match="could not be started"):
            store.submit("after-shutdown.py")
        release.set()
        store.shutdown(wait=True)
        assert reader.get(running.job_id).status == "completed"
    finally:
        release.set()
        store.shutdown(wait=True)
        reader.shutdown(wait=True)
