"""Lightweight FastAPI backend: submit → poll job → fetch reviews / outcomes.

No auth. In-memory job store resets on process restart; completed ReviewResult
rows live in SQLite via the same persist path as the CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.jobs import job_store
from app.persistence import (
    DEFAULT_DB_PATH,
    get_review,
    list_audit_events,
    list_outcomes_for_file,
    list_reviews,
)
from app.verified import record_finding_decision

app = FastAPI(
    title="secondpass",
    description=(
        "Personal review API. Submit a path for async Security+Architecture "
        "review, poll job status, fetch persisted reviews, record verified outcomes."
    ),
    version="0.1.0",
)

# Browser dashboard (Vite default); no auth — local-dev origins only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReviewSubmit(BaseModel):
    path: str = Field(..., min_length=1, description="File or directory to review.")


class JobAccepted(BaseModel):
    job_id: str


class OutcomeSubmit(BaseModel):
    review_id: int
    index: int = Field(0, ge=0, description="0-based finding index in the review.")
    accepted: bool
    reason: str = Field(..., min_length=1)
    linked_fix_commit: str | None = None


def _serialize_review(stored: Any) -> dict[str, Any]:
    return {
        "id": stored.id,
        "file_path": stored.file_path,
        "worker_name": stored.worker_name,
        "created_at": stored.created_at.isoformat(),
        "gate_threshold": stored.gate_threshold,
        "accepted_count": stored.accepted_count,
        "needs_review_count": stored.needs_review_count,
        "job_id": stored.job_id,
        "review_result": stored.review_result.model_dump(mode="json"),
        "gate_result": stored.gate_result.model_dump(mode="json"),
    }


def _serialize_outcome(stored: Any, memory_promotion: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "id": stored.id,
        "file_path": stored.file_path,
        "accepted": stored.accepted,
        "reason": stored.reason,
        "linked_fix_commit": stored.linked_fix_commit,
        "review_id": stored.review_id,
        "created_at": stored.created_at.isoformat(),
        "finding": stored.finding.model_dump(mode="json"),
    }
    if memory_promotion is not None:
        payload["memory_promotion"] = memory_promotion
    return payload


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reviews", status_code=202, response_model=JobAccepted)
def submit_review(body: ReviewSubmit) -> JobAccepted:
    """Enqueue supervise_review on a worker thread; returns immediately."""
    target = Path(body.path).expanduser()
    if not target.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {body.path}")
    job = job_store.submit(str(target.resolve()))
    return JobAccepted(job_id=job.job_id)


@app.get("/reviews/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")
    return job.to_dict()


@app.get("/reviews/jobs/{job_id}/audit")
def get_job_audit(job_id: str) -> dict[str, Any]:
    """Return one ordered audit trail for a submission (both workers).

    Reads SQLite — works after process restart even when the in-memory job
    is gone. 404 only when no events exist for this job_id.
    """
    events = list_audit_events(job_id, db_path=DEFAULT_DB_PATH)
    if not events:
        raise HTTPException(
            status_code=404,
            detail=f"No audit trail for job_id={job_id}",
        )
    return {
        "job_id": job_id,
        "event_count": len(events),
        "events": [
            {
                "id": event.id,
                "stage": event.stage,
                "worker_name": event.worker_name,
                "timestamp": event.timestamp.isoformat(),
                "detail": event.detail,
            }
            for event in events
        ],
    }


@app.get("/reviews/{review_id}")
def get_persisted_review(review_id: int) -> dict[str, Any]:
    stored = get_review(review_id, db_path=DEFAULT_DB_PATH)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"No review with id={review_id}")
    return _serialize_review(stored)


@app.get("/reviews")
def list_persisted_reviews(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    items = list_reviews(limit=limit, db_path=DEFAULT_DB_PATH)
    return {"reviews": [_serialize_review(item) for item in items]}


@app.get("/outcomes")
def list_outcomes(
    file_path: str = Query(..., min_length=1),
) -> dict[str, Any]:
    outcomes = list_outcomes_for_file(file_path, db_path=DEFAULT_DB_PATH)
    return {
        "file_path": file_path,
        "outcomes": [_serialize_outcome(item) for item in outcomes],
    }


@app.post("/outcomes")
def create_outcome(body: OutcomeSubmit) -> dict[str, Any]:
    try:
        decision = record_finding_decision(
            body.review_id,
            body.index,
            accepted=body.accepted,
            reason=body.reason,
            linked_fix_commit=body.linked_fix_commit,
            db_path=DEFAULT_DB_PATH,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_outcome(
        decision.outcome,
        memory_promotion=decision.memory_promotion,
    )


def main() -> None:
    import uvicorn

    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
