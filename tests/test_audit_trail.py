"""Offline tests for job_id-keyed persistent audit trail."""

from __future__ import annotations

import time
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.audit import (
    STAGE_CHROMA_PROMOTE,
    STAGE_CHROMA_SAVE_SKIP,
    STAGE_CONFIDENCE_GATE,
    STAGE_PROMPT_IO,
    STAGE_REVIEW_COMPLETE,
    STAGE_REVIEW_PERSISTED,
    STAGE_REVIEW_START,
    STAGE_SCHEMA_VALIDATION,
    STAGE_VERIFIED_OUTCOME,
    get_audit_trail,
    audit_scope,
    log_audit_stage,
    summarize_messages,
    summarize_model_out,
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


def test_prompt_audit_keeps_metadata_without_content(db_path: Path) -> None:
    canary = "FAKE-CREDENTIAL-DO-NOT-RETAIN"
    with audit_scope("prompt-privacy"):
        log_audit_stage(STAGE_PROMPT_IO, detail={
            "prompt": summarize_messages([{"role": "user", "content": canary}]),
            "model_out": summarize_model_out(canary),
        }, db_path=db_path)

    events = get_audit_trail("prompt-privacy", db_path=db_path)
    assert len(events) == 1
    detail = events[0].detail
    assert canary not in json.dumps(detail)
    assert detail["prompt"] == {
        "message_count": 1,
        "total_chars": 29,
        "messages": [{"role": "user", "chars": 29}],
        "storage": "metadata_only",
    }
    assert detail["model_out"] == {"chars": 29, "storage": "metadata_only"}


def test_schema_failure_audit_omits_free_form_details(db_path: Path) -> None:
    with audit_scope("schema-privacy"):
        log_audit_stage(
            STAGE_SCHEMA_VALIDATION,
            detail={
                "ok": False,
                "finding_count": 0,
                "error": "FAKE-CREDENTIAL-in-exception",
                "finding_type": "FAKE-CREDENTIAL-in-model-label",
                "new_debug_field": "FAKE-CREDENTIAL-in-future-field",
            },
            db_path=db_path,
        )

    event = get_audit_trail("schema-privacy", db_path=db_path)[0]
    assert event.detail == {"ok": False, "finding_count": 0}
    assert "FAKE-CREDENTIAL" not in json.dumps(event.detail)


def test_verified_outcome_audit_keeps_decision_metadata_not_reason(db_path: Path) -> None:
    with audit_scope("outcome-privacy"):
        log_audit_stage(
            STAGE_VERIFIED_OUTCOME,
            detail={
                "outcome_id": 9,
                "review_id": 4,
                "finding_index": 2,
                "accepted": True,
                "reason": "FAKE-CREDENTIAL-in-human-reason",
                "finding_type": "FAKE-CREDENTIAL-in-finding-label",
            },
            db_path=db_path,
        )

    detail = get_audit_trail("outcome-privacy", db_path=db_path)[0].detail
    assert detail == {"outcome_id": 9, "review_id": 4, "finding_index": 2, "accepted": True}


def test_chroma_promotion_audit_keeps_status_not_exception(db_path: Path) -> None:
    with audit_scope("promotion-privacy"):
        log_audit_stage(
            STAGE_CHROMA_PROMOTE,
            detail={
                "outcome_id": 9,
                "review_id": 4,
                "finding_index": 2,
                "status": "error",
                "error": "FAKE-CREDENTIAL-in-exception",
                "reason": "FAKE-CREDENTIAL-in-provider-reason",
                "finding_type": "FAKE-CREDENTIAL-in-model-label",
            },
            db_path=db_path,
        )

    detail = get_audit_trail("promotion-privacy", db_path=db_path)[0].detail
    assert detail == {"outcome_id": 9, "review_id": 4, "finding_index": 2, "status": "error"}


def test_review_start_audit_keeps_directory_progress_path_only(db_path: Path) -> None:
    with audit_scope("directory-progress"):
        log_audit_stage(
            STAGE_REVIEW_START,
            detail={
                "path": "C:/workspace/src/notes.py",
                "run_architecture": True,
                "debug": "FAKE-CREDENTIAL-in-future-field",
            },
            db_path=db_path,
        )

    detail = get_audit_trail("directory-progress", db_path=db_path)[0].detail
    assert detail == {"path": "C:/workspace/src/notes.py", "run_architecture": True}


def test_review_complete_audit_keeps_progress_metadata_only(db_path: Path) -> None:
    with audit_scope("directory-complete"):
        log_audit_stage(
            STAGE_REVIEW_COMPLETE,
            detail={
                "path": "C:/workspace/src/notes.py",
                "accepted_count": 1,
                "needs_review_count": 2,
                "workers_run": ["security", "architecture"],
                "persisted_review_ids": {"security": 7, "architecture": 8},
                "debug": "FAKE-CREDENTIAL-in-future-field",
            },
            db_path=db_path,
        )

    detail = get_audit_trail("directory-complete", db_path=db_path)[0].detail
    assert detail == {
        "path": "C:/workspace/src/notes.py",
        "accepted_count": 1,
        "needs_review_count": 2,
        "workers_run": ["security", "architecture"],
        "persisted_review_ids": {"security": 7, "architecture": 8},
    }


def test_gate_audit_keeps_counts_not_future_free_form_detail(db_path: Path) -> None:
    with audit_scope("gate-privacy"):
        log_audit_stage(
            STAGE_CONFIDENCE_GATE,
            detail={
                "threshold": 80,
                "accepted_count": 1,
                "needs_review_count": 2,
                "finding_count": 3,
                "debug": "FAKE-CREDENTIAL-in-future-field",
            },
            db_path=db_path,
        )

    detail = get_audit_trail("gate-privacy", db_path=db_path)[0].detail
    assert detail == {
        "threshold": 80,
        "accepted_count": 1,
        "needs_review_count": 2,
        "finding_count": 3,
    }


def test_review_persisted_audit_omits_redundant_file_path(db_path: Path) -> None:
    with audit_scope("persisted-privacy"):
        log_audit_stage(
            STAGE_REVIEW_PERSISTED,
            detail={
                "review_id": 4,
                "accepted_count": 1,
                "needs_review_count": 2,
                "file_path": "FAKE-CREDENTIAL-in-private-path",
            },
            db_path=db_path,
        )

    detail = get_audit_trail("persisted-privacy", db_path=db_path)[0].detail
    assert detail == {"review_id": 4, "accepted_count": 1, "needs_review_count": 2}


def test_memory_skip_audit_does_not_repeat_free_form_reason(db_path: Path) -> None:
    with audit_scope("memory-skip-privacy"):
        log_audit_stage(
            STAGE_CHROMA_SAVE_SKIP,
            detail={"reason": "FAKE-CREDENTIAL-in-reason", "saved_lesson_id": None},
            db_path=db_path,
        )

    detail = get_audit_trail("memory-skip-privacy", db_path=db_path)[0].detail
    assert detail == {}


def test_unknown_stage_audit_drops_detail_by_default(db_path: Path) -> None:
    with audit_scope("future-stage-privacy"):
        log_audit_stage(
            "future_stage",
            detail={"message": "FAKE-CREDENTIAL-in-unknown-stage"},
            db_path=db_path,
        )

    event = get_audit_trail("future-stage-privacy", db_path=db_path)[0]
    assert event.stage == "future_stage"
    assert event.detail == {}


def test_rate_limit_stage_keeps_event_without_exception_type(db_path: Path) -> None:
    with audit_scope("rate-limit-privacy"):
        log_audit_stage(
            "llm_rate_limited",
            detail={"status": "skipped — rate limited", "error_type": "FAKE-CREDENTIAL"},
            db_path=db_path,
        )

    event = get_audit_trail("rate-limit-privacy", db_path=db_path)[0]
    assert event.stage == "llm_rate_limited"
    assert event.detail == {}


def test_stage_metadata_rejects_type_spoofing(db_path: Path) -> None:
    with audit_scope("typed-metadata"):
        log_audit_stage(
            STAGE_CONFIDENCE_GATE,
            detail={"accepted_count": True, "needs_review_count": "FAKE-CREDENTIAL"},
            db_path=db_path,
        )
        log_audit_stage(
            STAGE_CHROMA_PROMOTE,
            detail={"status": "FAKE-CREDENTIAL"},
            db_path=db_path,
        )

    events = get_audit_trail("typed-metadata", db_path=db_path)
    assert events[0].detail == {}
    assert events[1].detail == {"status": "unknown"}


def test_prompt_stage_rejects_content_hidden_in_summary_fields(db_path: Path) -> None:
    with audit_scope("prompt-stage-privacy"):
        log_audit_stage(
            STAGE_PROMPT_IO,
            detail={
                "prompt": {
                    "message_count": 1,
                    "total_chars": 3,
                    "messages": [{"role": "user", "chars": 3, "content": "FAKE-CREDENTIAL"}],
                    "storage": "metadata_only",
                },
                "model_out": {"chars": 3, "preview": "FAKE-CREDENTIAL"},
                "debug": "FAKE-CREDENTIAL",
            },
            db_path=db_path,
        )

    detail = get_audit_trail("prompt-stage-privacy", db_path=db_path)[0].detail
    assert detail == {
        "prompt": {
            "message_count": 1,
            "total_chars": 3,
            "messages": [{"role": "user", "chars": 3}],
            "storage": "metadata_only",
        },
        "model_out": {"chars": 3, "storage": "metadata_only"},
    }


@pytest.mark.parametrize("role", ["FAKE-ROLE-SECRET", {"secret": "FAKE-ROLE-SECRET"}, None])
def test_prompt_audit_does_not_retain_arbitrary_role_data(db_path: Path, role: Any) -> None:
    with audit_scope("invalid-role"):
        log_audit_stage(STAGE_PROMPT_IO, detail={
            "prompt": summarize_messages([{"role": role, "content": None}]),
        }, db_path=db_path)

    detail = get_audit_trail("invalid-role", db_path=db_path)[0].detail
    assert detail["prompt"]["messages"] == [{"role": "unknown", "chars": 0}]


@pytest.mark.parametrize("role", ["system", "developer", "user", "assistant", "tool", "function"])
def test_prompt_audit_preserves_roles_and_structured_content_counts(db_path: Path, role: str) -> None:
    messages = [{"role": role, "content": [{"text": "FAKE"}], "name": "FAKE-NAME"}]
    with audit_scope("structured-prompt"):
        log_audit_stage(STAGE_PROMPT_IO, detail={
            "prompt": summarize_messages(messages),
            "model_out": summarize_model_out(None),
        }, db_path=db_path)

    detail = get_audit_trail("structured-prompt", db_path=db_path)[0].detail
    assert detail == {
        "prompt": {
            "message_count": 1, "total_chars": 18,
            "messages": [{"role": role, "chars": 18}], "storage": "metadata_only",
        },
        "model_out": {"chars": 0, "storage": "metadata_only"},
    }
    assert messages == [{"role": role, "content": [{"text": "FAKE"}], "name": "FAKE-NAME"}]


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
    assert json.loads(tool_ev.detail["args"]) == {
        "positional_count": 1, "keyword_count": 0, "storage": "metadata_only",
    }
    assert event_kind(agent_ev.stage) == "agent_event"
    assert event_kind(tool_ev.stage) == "tool"


@pytest.mark.parametrize("message,expected", [
    ("supervisor starting review of FAKE-PATH-SECRET", "supervisor starting review"),
    ("logic-review: clean — FAKE-MODEL-SECRET", "logic-review: clean"),
    ("logic-review: 2 concrete issue(s) — FAKE-MODEL-SECRET", "logic-review: concrete issue(s)"),
    ("logic-review: 2 concrete issue(s) — inconclusive — FAKE-MODEL-SECRET", "logic-review: inconclusive with issues"),
    ("architecture_worker: clean — FAKE-MODEL-SECRET", "architecture_worker: clean"),
    ("architecture_worker: 2 issue(s) — FAKE-MODEL-SECRET", "architecture_worker: claimed issues"),
    ("architecture_worker: 2 issue(s) — inconclusive — FAKE-MODEL-SECRET", "architecture_worker: inconclusive with issues"),
    ("architecture_worker: claim_unverified — inconclusive — FAKE-MODEL-SECRET", "architecture_worker: inconclusive with unverified claim"),
    ("supervisor routing: memory=True web=False (FAKE-MODEL-SECRET)", "supervisor routing: selected workers"),
    ("supervisor -> memory_worker", "supervisor -> memory_worker"),
    ("memory_worker -> supervisor (worth_reporting=FAKE-MODEL-SECRET)", "memory_worker -> supervisor"),
    ("supervisor -> security worker", "supervisor -> security worker"),
    ("FAKE-UNKNOWN-SECRET", "Agent activity"),
])
def test_agent_event_omits_free_form_content_from_all_sinks(
    db_path: Path, tmp_path: Path, monkeypatch, message: str, expected: str,
) -> None:
    from io import StringIO
    from rich.console import Console
    from app.hooks import agent_scope, log_agent_event

    output = StringIO()
    monkeypatch.setattr("app.hooks._stderr_console", Console(file=output, width=200))
    log_path = tmp_path / "agent.log"
    with audit_scope("agent-privacy"), agent_scope("supervisor"):
        log_agent_event(message, log_file=log_path)

    event = get_audit_trail("agent-privacy", db_path=db_path)[0]
    assert event.detail["message"] == expected
    for rendered in (output.getvalue(), log_path.read_text(encoding="utf-8"), json.dumps(event.detail)):
        assert expected in rendered
        assert "FAKE-" not in rendered


@pytest.mark.parametrize("fails", [False, True])
def test_tool_arguments_are_omitted_from_all_hook_sinks(
    db_path: Path, tmp_path: Path, monkeypatch, fails: bool,
) -> None:
    from io import StringIO
    from rich.console import Console
    from app.hooks import agent_scope, log_tool_call

    output = StringIO()
    monkeypatch.setattr("app.hooks._stderr_console", Console(file=output, width=200))
    log_path = tmp_path / "tools.log"
    canary = "FAKE-TOOL-SECRET"
    received = []
    error = ValueError(canary)

    @log_tool_call(log_file=log_path)
    def example_tool(*args, **kwargs):
        received.append((args, kwargs))
        if fails:
            raise error
        return "result"

    with audit_scope("tool-privacy"), agent_scope("memory_worker"):
        if fails:
            with pytest.raises(ValueError) as caught:
                example_tool({"secret": canary}, **{canary: canary})
            assert caught.value is error
        else:
            assert example_tool({"secret": canary}, **{canary: canary}) == "result"
    assert received == [(({"secret": canary},), {canary: canary})]
    events = get_audit_trail("tool-privacy", db_path=db_path)
    assert len(events) == 1
    detail = events[0].detail
    for rendered in (output.getvalue(), log_path.read_text(encoding="utf-8"), json.dumps(detail)):
        assert canary not in rendered
        assert "example_tool" in rendered
        assert "duration_ms" in rendered
    assert detail["ok"] is not fails
    assert detail["status"] == ("error=ValueError" if fails else "ok")
    assert json.loads(detail["args"]) == {
        "positional_count": 1, "keyword_count": 1, "storage": "metadata_only",
    }


def test_tool_logging_does_not_serialize_arguments(db_path: Path) -> None:
    from app.hooks import log_tool_call, live_stderr_scope

    class PrivateArgument:
        def __str__(self):
            raise AssertionError("Logging must not inspect private argument content")

        __repr__ = __str__

    argument = PrivateArgument()

    @log_tool_call(log_file=None)
    def identity(value):
        return value

    with audit_scope("opaque-argument"), live_stderr_scope(enabled=False):
        assert identity(argument) is argument
    event = get_audit_trail("opaque-argument", db_path=db_path)[0]
    assert event.detail["ok"] is True
    assert json.loads(event.detail["args"])["positional_count"] == 1


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
