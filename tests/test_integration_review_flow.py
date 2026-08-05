"""End-to-end API → Supervisor → gate → persist → outcomes (real chain).

Mocks only Semgrep / LLM chat / seed_memory / web search at the edges.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.jobs import JobStore

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTES_IDOR = REPO_ROOT / "benchmark" / "fixtures" / "notes_idor.py"
CLEAN_OWNERSHIP = REPO_ROOT / "benchmark" / "fixtures" / "clean_ownership.py"
NOTES_REL = "benchmark/fixtures/notes_idor.py"
CLEAN_REL = "benchmark/fixtures/clean_ownership.py"


def _msg(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ScriptedChat:
    """Return canned JSON keyed off the system prompt (outermost LLM only)."""

    def __init__(
        self,
        *,
        logic_confidence: int | None = 95,
        architecture_finding: bool = False,
    ) -> None:
        self.logic_confidence = logic_confidence
        self.architecture_finding = architecture_finding
        self.calls: list[str] = []

    def __call__(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> SimpleNamespace:
        system = ""
        for message in messages:
            if message.get("role") == "system":
                system = str(message.get("content") or "")
                break
        self.calls.append(system[:80])

        if "careful security logic reviewer" in system:
            if self.logic_confidence is None:
                return _msg(
                    json.dumps(
                        {
                            "has_issues": False,
                            "summary": "No security issues found.",
                            "issues": [],
                        }
                    )
                )
            return _msg(
                json.dumps(
                    {
                        "has_issues": True,
                        "summary": "get_note skips owner_id check",
                        "issues": [
                            {
                                "line": 14,
                                "severity": "ERROR",
                                "finding_type": "missing_ownership_check",
                                "confidence": self.logic_confidence,
                                "message": "get_note returns any note without checking owner_id",
                                "snippet": "return NOTES.get(note_id)",
                                "suggested_fix": "Require note['owner_id'] == user_id",
                            }
                        ],
                    }
                )
            )

        if "Decide which specialist workers" in system:
            return _msg(
                json.dumps(
                    {
                        "use_memory": False,
                        "use_web": False,
                        "reason": "Planted IDOR is clear from code alone",
                    }
                )
            )

        if "Synthesize a final security review" in system:
            conf = self.logic_confidence if self.logic_confidence is not None else 95
            return _msg(
                json.dumps(
                    {
                        "finding_type": "missing_ownership_check",
                        "evidence": "get_note returns NOTES.get(note_id) with no owner_id check",
                        "confidence": conf,
                        "suggested_fix": "Require note['owner_id'] == user_id before return",
                        "detection_method": "llm_reasoning",
                    }
                )
            )

        if "ArchitectureWorker" in system:
            if self.architecture_finding:
                return _msg(
                    json.dumps(
                        {
                            "has_issues": True,
                            "summary": "Handler mixes I/O and business rules",
                            "issues": [
                                {
                                    "line": 10,
                                    "severity": "WARNING",
                                    "finding_type": "mixed_concerns",
                                    "confidence": 88,
                                    "message": (
                                        "get_note mixes persistent storage access "
                                        "with response shaping in one function"
                                    ),
                                    "evidence": "def get_note(...): return NOTES.get(note_id)",
                                    "suggested_fix": (
                                        "Split repository lookup from presentation logic"
                                    ),
                                    "category": "mixed_concerns",
                                }
                            ],
                        }
                    )
                )
            return _msg(
                json.dumps(
                    {
                        "has_issues": False,
                        "summary": "No architecture issues found.",
                        "issues": [],
                    }
                )
            )

        if tools:
            return _msg("{}")

        return _msg("{}")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "integration.db"
    monkeypatch.setattr("app.api.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("app.memory._DEFAULT_DB_PATH", tmp_path / "chromadb")

    store = JobStore(max_workers=2)
    monkeypatch.setattr("app.api.job_store", store)
    monkeypatch.setattr("app.jobs.job_store", store)

    monkeypatch.setattr("app.agent.seed_memory", lambda: 0)
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])
    monkeypatch.setattr(
        "app.websearch.search_web",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("web should not be called")),
    )

    with TestClient(app) as test_client:
        yield test_client
    store.shutdown(wait=False)


def _patch_chat(monkeypatch: pytest.MonkeyPatch, scripted: ScriptedChat) -> None:
    for target in (
        "app.agent.chat",
        "app.supervisor.chat",
        "app.workers.architecture_worker.chat",
        "app.workers.common.chat",
    ):
        monkeypatch.setattr(target, scripted)


def _wait_job(client: TestClient, job_id: str, *, timeout_s: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        response = client.get(f"/reviews/jobs/{job_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"completed", "failed"}:
            return last
        time.sleep(0.05)
    pytest.fail(f"job {job_id} did not finish; last={last}")


def test_happy_path_security_finding_accept_outcome(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted = ScriptedChat(logic_confidence=95)
    _patch_chat(monkeypatch, scripted)

    submitted = client.post("/reviews", json={"path": str(NOTES_IDOR)})
    assert submitted.status_code == 202
    job = _wait_job(client, submitted.json()["job_id"])

    assert job["status"] == "completed"
    assert job["error"] is None
    security_id = job["persisted_review_ids"]["security"]
    assert security_id is not None

    review = client.get(f"/reviews/{security_id}")
    assert review.status_code == 200
    body = review.json()
    assert body["worker_name"] == "security"
    assert body["accepted_count"] == 1
    assert body["needs_review_count"] == 0
    assert len(body["gate_result"]["accepted"]) == 1
    assert body["gate_result"]["accepted"][0]["confidence"] >= 80
    assert body["gate_result"]["needs_review"] == []

    decided = client.post(
        "/outcomes",
        json={
            "review_id": security_id,
            "index": 0,
            "accepted": True,
            "reason": "Confirmed IDOR on get_note via integration test",
        },
    )
    assert decided.status_code == 200
    assert decided.json()["accepted"] is True

    outcomes = client.get("/outcomes", params={"file_path": NOTES_REL})
    assert outcomes.status_code == 200
    listed = outcomes.json()["outcomes"]
    assert len(listed) == 1
    assert listed[0]["accepted"] is True
    assert "Confirmed IDOR" in listed[0]["reason"]
    assert listed[0]["finding"]["finding_type"] == "missing_ownership_check"


def test_needs_review_then_explicit_reject(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted = ScriptedChat(logic_confidence=70)
    _patch_chat(monkeypatch, scripted)

    job = _wait_job(
        client,
        client.post("/reviews", json={"path": str(NOTES_IDOR)}).json()["job_id"],
    )
    assert job["status"] == "completed"
    security_id = job["persisted_review_ids"]["security"]
    assert security_id is not None

    review = client.get(f"/reviews/{security_id}").json()
    assert review["accepted_count"] == 0
    assert review["needs_review_count"] == 1
    assert review["gate_result"]["accepted"] == []
    assert len(review["gate_result"]["needs_review"]) == 1
    assert review["gate_result"]["needs_review"][0]["confidence"] == 70

    # Still decideable — reject with reason (not auto-included).
    decided = client.post(
        "/outcomes",
        json={
            "review_id": security_id,
            "index": 0,
            "accepted": False,
            "reason": "Below threshold and looks like a false positive",
        },
    )
    assert decided.status_code == 200
    assert decided.json()["accepted"] is False

    outcomes = client.get("/outcomes", params={"file_path": NOTES_REL}).json()
    assert len(outcomes["outcomes"]) == 1
    assert outcomes["outcomes"][0]["accepted"] is False


def test_clean_file_zero_findings(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted = ScriptedChat(logic_confidence=None)
    _patch_chat(monkeypatch, scripted)

    job = _wait_job(
        client,
        client.post("/reviews", json={"path": str(CLEAN_OWNERSHIP)}).json()["job_id"],
    )
    assert job["status"] == "completed"
    assert job["error"] is None
    assert job["summary"]["accepted_count"] == 0
    assert job["summary"]["needs_review_count"] == 0

    security_id = job["persisted_review_ids"]["security"]
    architecture_id = job["persisted_review_ids"]["architecture"]
    assert security_id is not None
    assert architecture_id is not None

    security = client.get(f"/reviews/{security_id}").json()
    architecture = client.get(f"/reviews/{architecture_id}").json()
    assert security["gate_result"]["accepted"] == []
    assert security["gate_result"]["needs_review"] == []
    assert architecture["gate_result"]["accepted"] == []
    assert architecture["gate_result"]["needs_review"] == []

    outcomes = client.get("/outcomes", params={"file_path": CLEAN_REL})
    assert outcomes.status_code == 200
    assert outcomes.json()["outcomes"] == []


def test_both_workers_persist_on_single_submit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted = ScriptedChat(logic_confidence=95, architecture_finding=True)
    _patch_chat(monkeypatch, scripted)

    job = _wait_job(
        client,
        client.post("/reviews", json={"path": str(NOTES_IDOR)}).json()["job_id"],
    )
    assert job["status"] == "completed"
    ids = job["persisted_review_ids"]
    assert ids["security"] is not None
    assert ids["architecture"] is not None
    assert ids["security"] != ids["architecture"]
    assert "security" in job["summary"]["workers_run"]
    assert "architecture" in job["summary"]["workers_run"]

    security = client.get(f"/reviews/{ids['security']}").json()
    architecture = client.get(f"/reviews/{ids['architecture']}").json()
    assert security["worker_name"] == "security"
    assert architecture["worker_name"] == "architecture"
    assert security["accepted_count"] == 1
    assert architecture["accepted_count"] == 1
    assert architecture["gate_result"]["accepted"][0]["finding_type"] == "mixed_concerns"
