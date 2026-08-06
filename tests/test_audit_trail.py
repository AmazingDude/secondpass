"""Offline tests for job_id-keyed persistent audit trail."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.audit import (
    STAGE_CHROMA_SAVE_SKIP,
    STAGE_CONFIDENCE_GATE,
    STAGE_PROMPT_IO,
    STAGE_REVIEW_COMPLETE,
    STAGE_REVIEW_PERSISTED,
    STAGE_REVIEW_START,
    STAGE_SCHEMA_VALIDATION,
    STAGE_VERIFIED_OUTCOME,
    get_audit_trail,
    log_audit_stage,
    summarize_messages,
)
from app.jobs import JobStore
from app.persistence import list_audit_events, save_audit_event

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTES_IDOR = REPO_ROOT / "benchmark" / "fixtures" / "notes_idor.py"


def _msg(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ScriptedChat:
    def __call__(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> SimpleNamespace:
        import json

        system = ""
        for message in messages:
            if message.get("role") == "system":
                system = str(message.get("content") or "")
                break

        if "careful security logic reviewer" in system:
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
                                "confidence": 95,
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
                        "routing_rationale": "clear from code",
                    }
                )
            )
        if "Synthesize a final security review" in system:
            return _msg(
                json.dumps(
                    {
                        "explanation": "IDOR on get_note",
                        "suggested_fix": "Check owner_id",
                    }
                )
            )
        if "ArchitectureWorker" in system:
            return _msg(
                json.dumps(
                    {
                        "has_issues": True,
                        "summary": "mixed concerns",
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
        return _msg("{}")


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "audit.db"
    monkeypatch.setattr("app.api.DEFAULT_DB_PATH", path)
    monkeypatch.setattr("app.persistence.DEFAULT_DB_PATH", path)
    monkeypatch.setattr("app.memory._DEFAULT_DB_PATH", tmp_path / "chromadb")
    return path


def test_summarize_messages_is_redacted_not_full_dump() -> None:
    huge = "X" * 5000
    summary = summarize_messages([{"role": "user", "content": huge}])
    assert summary["storage"] == "redacted_summary"
    assert summary["total_chars"] == 5000
    assert len(summary["messages"][0]["preview"]) < 500
    assert huge not in summary["messages"][0]["preview"]


def test_save_and_list_audit_events_ordered(db_path: Path) -> None:
    job = "job-unit-1"
    save_audit_event(job, "a", worker_name="security", detail={"n": 1}, db_path=db_path)
    save_audit_event(job, "b", worker_name="architecture", detail={"n": 2}, db_path=db_path)
    save_audit_event("other", "x", detail={}, db_path=db_path)

    events = list_audit_events(job, db_path=db_path)
    assert [e.stage for e in events] == ["a", "b"]
    assert [e.worker_name for e in events] == ["security", "architecture"]


def test_log_audit_stage_noop_without_job(db_path: Path) -> None:
    assert log_audit_stage("orphan", detail={"x": 1}, db_path=db_path) is None
    assert list_audit_events("missing", db_path=db_path) == []


def test_hooks_persist_under_job_id_and_not_without(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """agent_event + tool_call land in audit_events only when audit_scope has job_id."""
    from app.audit import (
        STAGE_AGENT_EVENT,
        STAGE_TOOL_CALL,
        audit_scope,
        event_kind,
    )
    from app.hooks import agent_scope, log_agent_event, log_tool_call

    @log_tool_call(log_file=None)
    def search_memory(query: str) -> list[dict]:
        return [{"id": "lesson-1"}]

    # No job_id → stderr only; nothing in SQLite.
    with agent_scope("supervisor"):
        log_agent_event("orphan handoff", log_file=None)
    with agent_scope("memory_worker"):
        search_memory("ownership check")
    assert list_audit_events("job-hooks-a", db_path=db_path) == []

    with audit_scope("job-hooks-a"):
        with agent_scope("supervisor"):
            log_agent_event("supervisor -> memory_worker", log_file=None)
        with agent_scope("memory_worker"):
            search_memory("ownership check")

    # Sibling job must stay empty (no cross-job leakage).
    assert list_audit_events("job-hooks-b", db_path=db_path) == []

    events = list_audit_events("job-hooks-a", db_path=db_path)
    stages = [e.stage for e in events]
    assert STAGE_AGENT_EVENT in stages
    assert STAGE_TOOL_CALL in stages
    agent_ev = next(e for e in events if e.stage == STAGE_AGENT_EVENT)
    tool_ev = next(e for e in events if e.stage == STAGE_TOOL_CALL)
    assert agent_ev.detail["message"] == "supervisor -> memory_worker"
    assert agent_ev.detail["agent"] == "supervisor"
    assert tool_ev.detail["tool"] == "search_memory"
    assert tool_ev.detail["ok"] is True
    assert "ownership" in str(tool_ev.detail["args"])
    assert event_kind(agent_ev.stage) == "agent_event"
    assert event_kind(tool_ev.stage) == "tool"


def test_api_audit_returns_hook_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """API audit feed includes hook rows mixed with pipeline stages under job_id."""
    from app.audit import STAGE_AGENT_EVENT

    scripted = ScriptedChat()

    def wrapping_chat(messages, tools=None, temperature=None):
        response = scripted(messages, tools=tools, temperature=temperature)
        from app.llm import _audit_prompt_io

        _audit_prompt_io(messages, response)
        return response

    for target in (
        "app.agent.chat",
        "app.supervisor.chat",
        "app.workers.architecture_worker.chat",
        "app.workers.common.chat",
    ):
        monkeypatch.setattr(target, wrapping_chat)

    submitted = client.post("/reviews", json={"path": str(NOTES_IDOR)})
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    job = _wait_job(client, job_id)
    assert job["status"] == "completed", job.get("error")

    audit = client.get(f"/reviews/jobs/{job_id}/audit")
    assert audit.status_code == 200
    body = audit.json()
    kinds = {e["kind"] for e in body["events"]}
    stages = [e["stage"] for e in body["events"]]
    assert "agent_event" in kinds
    assert "stage" in kinds
    assert STAGE_AGENT_EVENT in stages
    agent_msgs = [
        str(e["detail"].get("message", ""))
        for e in body["events"]
        if e["stage"] == STAGE_AGENT_EVENT
    ]
    assert any("supervisor ->" in m for m in agent_msgs)
    # Stubs replace decorated tools; assert tool rows when a real @log_tool_call runs.
    # Direct hook persistence is covered by test_hooks_persist_under_job_id_and_not_without.
    other = client.get("/reviews/jobs/not-this-job/audit")
    assert other.status_code == 404


@pytest.fixture()
def client(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
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


def _wait_job(client: TestClient, job_id: str, *, timeout_s: float = 15.0) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        body = client.get(f"/reviews/jobs/{job_id}").json()
        last = body
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.05)
    pytest.fail(f"job did not finish: {last}")


def test_api_submit_writes_coherent_audit_trail_both_workers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """Real supervise_review chain; mock only Semgrep/LLM/seed/web."""
    scripted = ScriptedChat()

    def wrapping_chat(messages, tools=None, temperature=None):
        response = scripted(messages, tools=tools, temperature=temperature)
        from app.llm import _audit_prompt_io

        _audit_prompt_io(messages, response)
        return response

    for target in (
        "app.agent.chat",
        "app.supervisor.chat",
        "app.workers.architecture_worker.chat",
        "app.workers.common.chat",
    ):
        monkeypatch.setattr(target, wrapping_chat)

    submitted = client.post("/reviews", json={"path": str(NOTES_IDOR)})
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]

    job = _wait_job(client, job_id)
    assert job["status"] == "completed", job.get("error")
    assert job["result"]["job_id"] == job_id

    audit = client.get(f"/reviews/jobs/{job_id}/audit")
    assert audit.status_code == 200
    body = audit.json()
    assert body["job_id"] == job_id
    stages = [e["stage"] for e in body["events"]]
    workers = {e["worker_name"] for e in body["events"] if e["worker_name"]}

    assert STAGE_REVIEW_START in stages
    assert STAGE_SCHEMA_VALIDATION in stages
    assert STAGE_CONFIDENCE_GATE in stages
    assert STAGE_CHROMA_SAVE_SKIP in stages
    assert STAGE_REVIEW_PERSISTED in stages
    assert STAGE_REVIEW_COMPLETE in stages
    assert STAGE_PROMPT_IO in stages
    assert "security" in workers
    assert "architecture" in workers

    # One ordered sequence: security schema/gate before architecture equivalents.
    sec_schema = next(
        i
        for i, e in enumerate(body["events"])
        if e["stage"] == STAGE_SCHEMA_VALIDATION and e["worker_name"] == "security"
    )
    arch_schema = next(
        i
        for i, e in enumerate(body["events"])
        if e["stage"] == STAGE_SCHEMA_VALIDATION and e["worker_name"] == "architecture"
    )
    assert sec_schema < arch_schema

    # Decide attaches verified_outcome_write under the same job_id.
    review_id = job["persisted_review_ids"]["security"]
    decided = client.post(
        "/outcomes",
        json={
            "review_id": review_id,
            "index": 0,
            "accepted": True,
            "reason": "Confirmed IDOR for audit trail test",
        },
    )
    assert decided.status_code == 200

    trail = get_audit_trail(job_id, db_path=db_path)
    assert any(e.stage == STAGE_VERIFIED_OUTCOME for e in trail)


def test_cli_synthetic_job_id_has_trail(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    from app.supervisor import supervise_review

    scripted = ScriptedChat()

    def wrapping_chat(messages, tools=None, temperature=None):
        response = scripted(messages, tools=tools, temperature=temperature)
        from app.llm import _audit_prompt_io

        _audit_prompt_io(messages, response)
        return response

    for target in (
        "app.agent.chat",
        "app.supervisor.chat",
        "app.workers.architecture_worker.chat",
        "app.workers.common.chat",
    ):
        monkeypatch.setattr(target, wrapping_chat)
    monkeypatch.setattr("app.agent.seed_memory", lambda: 0)
    monkeypatch.setattr("app.agent.run_static_scan", lambda paths: [])

    report = supervise_review(str(NOTES_IDOR))
    job_id = report["job_id"]
    assert job_id
    events = get_audit_trail(job_id, db_path=db_path)
    assert events[0].stage == STAGE_REVIEW_START
    assert any(e.worker_name == "security" for e in events)
    assert any(e.worker_name == "architecture" for e in events)
