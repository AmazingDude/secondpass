"""Terminal job lookup survives a fresh executor without replaying reviews."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.jobs import JobStore


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
    job = store.submit("example.py")
    store.shutdown(wait=True)
    failed = store.get(job.job_id).to_dict()
    assert failed["status"] == "failed"
    assert failed["error"] == "Could not save the job result. Check local storage and retry."
    assert "result" not in failed
    assert "summary" not in failed
    assert str(blocked) not in failed["error"]
