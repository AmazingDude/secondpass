"""Offline FastAPI tests: submit → poll (including running) → completed → outcomes."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.confidence_gate import apply_confidence_gate
from app.jobs import JobStore, job_store
from app.persistence import save_review
from app.schema import Finding, ReviewResult


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "api.db"
    chroma = tmp_path / "chromadb"
    monkeypatch.setattr("app.api.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("app.memory._DEFAULT_DB_PATH", chroma)

    store = JobStore(max_workers=2)
    monkeypatch.setattr("app.api.job_store", store)
    monkeypatch.setattr("app.jobs.job_store", store)

    with TestClient(app) as test_client:
        yield test_client
    store.shutdown(wait=False)


def _fake_combined(path: str) -> dict:
    finding = Finding(
        finding_type="missing_ownership_check",
        evidence="get_note skips owner_id",
        confidence=95,
        suggested_fix="Check owner_id",
        detection_method="llm_reasoning",
    )
    result = ReviewResult(
        findings=[finding],
        file_path=path,
        timestamp=datetime(2026, 7, 30, tzinfo=timezone.utc),
        worker_name="security",
    )
    gate = apply_confidence_gate(result)
    from app.verified import persist_combined_review

    report = {
        "path": path,
        "security": {
            "path": path,
            "worker_name": "security",
            "review_result": result.model_dump(mode="json"),
            "gate_result": gate.model_dump(mode="json"),
            "accepted_count": 1,
            "needs_review_count": 0,
        },
        "architecture": {
            "path": path,
            "worker_name": "architecture",
            "review_result": ReviewResult(
                findings=[],
                file_path=path,
                timestamp=result.timestamp,
                worker_name="architecture",
            ).model_dump(mode="json"),
            "gate_result": apply_confidence_gate(
                ReviewResult(
                    findings=[],
                    file_path=path,
                    timestamp=result.timestamp,
                    worker_name="architecture",
                )
            ).model_dump(mode="json"),
            "accepted_count": 0,
            "needs_review_count": 0,
            "skipped": False,
        },
        "summary": {
            "accepted_count": 1,
            "needs_review_count": 0,
            "workers_run": ["security", "architecture"],
        },
    }
    persisted = persist_combined_review(report)
    report["persisted_review_ids"] = persisted
    report["summary"]["persisted_review_ids"] = persisted
    return report


def test_submit_poll_completed_shape(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "notes.py"
    target.write_text("def get_note():\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.api.job_store._runner",
        lambda path: _fake_combined(path),
    )

    response = client.post("/reviews", json={"path": str(target)})
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    final = None
    for _ in range(50):
        status = client.get(f"/reviews/jobs/{job_id}")
        assert status.status_code == 200
        body = status.json()
        if body["status"] in {"completed", "failed"}:
            final = body
            break
        time.sleep(0.05)

    assert final is not None
    assert final["status"] == "completed"
    assert final["persisted_review_ids"]["security"] is not None
    assert final["summary"]["accepted_count"] == 1

    review_id = final["persisted_review_ids"]["security"]
    fetched = client.get(f"/reviews/{review_id}")
    assert fetched.status_code == 200
    assert fetched.json()["worker_name"] == "security"

    listed = client.get("/reviews", params={"limit": 10})
    assert listed.status_code == 200
    assert any(item["id"] == review_id for item in listed.json()["reviews"])


def test_job_status_can_be_running_before_completion(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "notes.py"
    target.write_text("x = 1\n", encoding="utf-8")
    started = threading.Event()
    release = threading.Event()

    def slow_runner(path: str) -> dict:
        started.set()
        assert release.wait(timeout=2.0)
        return _fake_combined(path)

    monkeypatch.setattr("app.api.job_store._runner", slow_runner)

    job_id = client.post("/reviews", json={"path": str(target)}).json()["job_id"]
    assert started.wait(timeout=2.0)

    mid = client.get(f"/reviews/jobs/{job_id}")
    assert mid.status_code == 200
    assert mid.json()["status"] == "running"

    release.set()
    for _ in range(50):
        body = client.get(f"/reviews/jobs/{job_id}").json()
        if body["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("job did not complete")


def test_accept_reject_outcome_via_api(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "api.db"
    finding = Finding(
        finding_type="missing_ownership_check",
        evidence="owner_id unchecked",
        confidence=90,
        suggested_fix="check owner",
        detection_method="llm_reasoning",
    )
    result = ReviewResult(
        findings=[finding],
        file_path="benchmark/fixtures/notes_idor.py",
        timestamp=datetime(2026, 7, 30, tzinfo=timezone.utc),
        worker_name="security",
    )
    stored = save_review(
        result,
        apply_confidence_gate(result),
        db_path=db_path,
    )

    created = client.post(
        "/outcomes",
        json={
            "review_id": stored.id,
            "index": 0,
            "accepted": True,
            "reason": "Confirmed IDOR on get_note",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["accepted"] is True
    assert body.get("memory_promotion", {}).get("status") in {"saved", "skipped"}

    listed = client.get(
        "/outcomes",
        params={"file_path": "benchmark/fixtures/notes_idor.py"},
    )
    assert listed.status_code == 200
    assert len(listed.json()["outcomes"]) == 1
    assert listed.json()["outcomes"][0]["reason"].startswith("Confirmed IDOR")


def test_submit_missing_path_returns_400(client: TestClient) -> None:
    response = client.post("/reviews", json={"path": "does/not/exist.py"})
    assert response.status_code == 400


def test_preview_directory_reuses_deterministic_selection(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "b.py").write_text("VALUE = 2\n", encoding="utf-8")
    (root / "__init__.py").write_text("PACKAGE = True\n", encoding="utf-8")

    response = client.get(
        "/reviews/preview",
        params={"path": str(root), "max_files": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "directory"
    assert body["eligible_count"] == 2
    assert body["selected_count"] == 1
    assert body["capped"] is True
    assert body["files"] == ["a.py"]
    assert body["skipped"]["init"] == 1


def test_directory_submit_passes_multifile_options_to_runner(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    received: dict[str, object] = {}

    def runner(
        path: str,
        *,
        job_id: str,
        workers: int,
        max_files: int,
        include_init: bool,
        include: tuple[str, ...],
        exclude: tuple[str, ...],
    ) -> dict:
        received.update(
            {
                "path": path,
                "job_id": job_id,
                "workers": workers,
                "max_files": max_files,
                "include_init": include_init,
                "include": include,
                "exclude": exclude,
            }
        )
        return {
            "summary": {"accepted_count": 0, "needs_review_count": 0},
            "persisted_review_ids": [],
        }

    monkeypatch.setattr("app.api.job_store._runner", runner)
    response = client.post(
        "/reviews",
        json={
            "path": str(root),
            "workers": 3,
            "max_files": 7,
            "include_init": True,
            "include": ["src/**"],
            "exclude": ["**/generated.py"],
        },
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    for _ in range(50):
        body = client.get(f"/reviews/jobs/{job_id}").json()
        if body["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("directory job did not complete")

    assert received["workers"] == 3
    assert received["max_files"] == 7
    assert received["include_init"] is True
    assert received["include"] == ("src/**",)
    assert received["exclude"] == ("**/generated.py",)


def test_directory_submit_runs_selected_files_end_to_end(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "b.py").write_text("VALUE = 2\n", encoding="utf-8")
    reviewed: list[str] = []

    def fake_supervise(path: str, *, job_id: str | None = None) -> dict:
        assert job_id
        reviewed.append(Path(path).name)
        return _fake_combined(path)

    monkeypatch.setattr("app.supervisor.supervise_review", fake_supervise)
    response = client.post(
        "/reviews",
        json={"path": str(root), "workers": 2, "max_files": 2},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    for _ in range(50):
        body = client.get(f"/reviews/jobs/{job_id}").json()
        if body["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    else:
        pytest.fail("directory job did not complete")

    assert body["status"] == "completed"
    assert sorted(reviewed) == ["a.py", "b.py"]
    assert body["result"]["mode"] == "directory"
    assert body["result"]["selection"]["selected"] == ["a.py", "b.py"]
    assert body["summary"]["file_count"] == 2
    assert len(body["persisted_review_ids"]) == 4
