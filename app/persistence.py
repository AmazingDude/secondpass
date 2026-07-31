"""SQLite persistence for review history and verified outcomes.

Separate from ChromaDB lesson memory in app.memory — this stores review runs
and human accept/reject outcomes for later FastAPI/history use.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.confidence_gate import GateResult
from app.schema import Finding, ReviewResult

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _ROOT / ".secondpass" / "secondpass.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    worker_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    review_result_json TEXT NOT NULL,
    gate_threshold INTEGER NOT NULL,
    accepted_count INTEGER NOT NULL,
    needs_review_count INTEGER NOT NULL,
    gate_result_json TEXT NOT NULL,
    job_id TEXT
);

CREATE TABLE IF NOT EXISTS verified_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_json TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    reason TEXT NOT NULL,
    linked_fix_commit TEXT,
    review_id INTEGER,
    file_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (review_id) REFERENCES reviews(id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    worker_name TEXT,
    timestamp TEXT NOT NULL,
    detail_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_file_path
    ON reviews(file_path);

CREATE INDEX IF NOT EXISTS idx_outcomes_file_path
    ON verified_outcomes(file_path);

CREATE INDEX IF NOT EXISTS idx_audit_events_job_id
    ON audit_events(job_id, id);
"""
# NOTE: idx_reviews_job_id is created in _migrate_schema AFTER ensuring the
# job_id column exists. Putting it in executescript breaks older DBs where
# CREATE TABLE IF NOT EXISTS leaves a pre-job_id reviews table in place.


class StoredReview(BaseModel):
    id: int
    file_path: str
    worker_name: str
    created_at: datetime
    review_result: ReviewResult
    gate_threshold: int
    accepted_count: int = Field(ge=0)
    needs_review_count: int = Field(ge=0)
    gate_result: GateResult
    job_id: str | None = None


class StoredVerifiedOutcome(BaseModel):
    id: int
    finding: Finding
    accepted: bool
    reason: str
    linked_fix_commit: str | None = None
    review_id: int | None = None
    file_path: str
    created_at: datetime


class StoredAuditEvent(BaseModel):
    id: int
    job_id: str
    stage: str
    worker_name: str | None = None
    timestamp: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


def _resolve_db_path(db_path: Path | str | None = None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_DB_PATH


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = _resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _dump_model(model: BaseModel) -> str:
    return model.model_dump_json()


def _row_keys(row: sqlite3.Row) -> set[str]:
    return set(row.keys())


def _row_to_review(row: sqlite3.Row) -> StoredReview:
    keys = _row_keys(row)
    return StoredReview(
        id=row["id"],
        file_path=row["file_path"],
        worker_name=row["worker_name"],
        created_at=datetime.fromisoformat(row["created_at"]),
        review_result=ReviewResult.model_validate_json(row["review_result_json"]),
        gate_threshold=row["gate_threshold"],
        accepted_count=row["accepted_count"],
        needs_review_count=row["needs_review_count"],
        gate_result=GateResult.model_validate_json(row["gate_result_json"]),
        job_id=row["job_id"] if "job_id" in keys else None,
    )


def _row_to_audit_event(row: sqlite3.Row) -> StoredAuditEvent:
    raw = row["detail_json"] or "{}"
    try:
        detail = json.loads(raw)
    except json.JSONDecodeError:
        detail = {"raw": raw}
    if not isinstance(detail, dict):
        detail = {"value": detail}
    return StoredAuditEvent(
        id=row["id"],
        job_id=row["job_id"],
        stage=row["stage"],
        worker_name=row["worker_name"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        detail=detail,
    )


def _row_to_outcome(row: sqlite3.Row) -> StoredVerifiedOutcome:
    return StoredVerifiedOutcome(
        id=row["id"],
        finding=Finding.model_validate_json(row["finding_json"]),
        accepted=bool(row["accepted"]),
        reason=row["reason"],
        linked_fix_commit=row["linked_fix_commit"],
        review_id=row["review_id"],
        file_path=row["file_path"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Additive migrations for DBs created before newer columns/tables."""
    review_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(reviews)").fetchall()
    }
    if "job_id" not in review_cols:
        conn.execute("ALTER TABLE reviews ADD COLUMN job_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reviews_job_id ON reviews(job_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            worker_name TEXT,
            timestamp TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_events_job_id
            ON audit_events(job_id, id)
        """
    )


def init_db(db_path: Path | str | None = None) -> Path:
    """Create the DB file and tables if they do not exist. Returns the path used."""
    path = _resolve_db_path(db_path)
    with _connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
        _migrate_schema(conn)
        conn.commit()
    return path


def save_review(
    review_result: ReviewResult,
    gate_result: GateResult,
    *,
    db_path: Path | str | None = None,
    created_at: datetime | None = None,
    job_id: str | None = None,
) -> StoredReview:
    """Persist a review run and its gate split. Creates tables if needed."""
    init_db(db_path)
    when = created_at or review_result.timestamp
    file_path = _normalize_path(review_result.file_path)

    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO reviews (
                file_path, worker_name, created_at,
                review_result_json, gate_threshold,
                accepted_count, needs_review_count, gate_result_json,
                job_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_path,
                review_result.worker_name,
                when.isoformat(),
                _dump_model(review_result),
                gate_result.threshold,
                len(gate_result.accepted),
                len(gate_result.needs_review),
                _dump_model(gate_result),
                job_id,
            ),
        )
        review_id = int(cursor.lastrowid)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM reviews WHERE id = ?", (review_id,)
        ).fetchone()

    return _row_to_review(row)


def get_review(
    review_id: int,
    *,
    db_path: Path | str | None = None,
) -> StoredReview | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM reviews WHERE id = ?", (review_id,)
        ).fetchone()
    return _row_to_review(row) if row is not None else None


def list_reviews(
    *,
    limit: int = 50,
    db_path: Path | str | None = None,
) -> list[StoredReview]:
    """Return newest reviews first."""
    init_db(db_path)
    limit = max(0, limit)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM reviews
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_review(row) for row in rows]


def save_verified_outcome(
    finding: Finding | dict[str, Any],
    *,
    accepted: bool,
    reason: str,
    file_path: str,
    review_id: int | None = None,
    linked_fix_commit: str | None = None,
    db_path: Path | str | None = None,
    created_at: datetime | None = None,
) -> StoredVerifiedOutcome:
    """Store a human accept/reject decision for a finding."""
    init_db(db_path)
    finding_model = (
        finding if isinstance(finding, Finding) else Finding.model_validate(finding)
    )
    when = created_at or datetime.now(timezone.utc)
    norm_path = _normalize_path(file_path)

    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO verified_outcomes (
                finding_json, accepted, reason, linked_fix_commit,
                review_id, file_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _dump_model(finding_model),
                1 if accepted else 0,
                reason,
                linked_fix_commit,
                review_id,
                norm_path,
                when.isoformat(),
            ),
        )
        outcome_id = int(cursor.lastrowid)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM verified_outcomes WHERE id = ?", (outcome_id,)
        ).fetchone()

    return _row_to_outcome(row)


def list_outcomes_for_file(
    file_path: str,
    *,
    db_path: Path | str | None = None,
) -> list[StoredVerifiedOutcome]:
    """Return verified outcomes for a file, newest first."""
    init_db(db_path)
    norm_path = _normalize_path(file_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM verified_outcomes
            WHERE file_path = ?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (norm_path,),
        ).fetchall()
    return [_row_to_outcome(row) for row in rows]


def save_audit_event(
    job_id: str,
    stage: str,
    *,
    worker_name: str | None = None,
    detail: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
    timestamp: datetime | None = None,
) -> StoredAuditEvent:
    """Append one pipeline-stage audit event keyed by job_id."""
    init_db(db_path)
    when = timestamp or datetime.now(timezone.utc)
    payload = detail if isinstance(detail, dict) else {}
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO audit_events (
                job_id, stage, worker_name, timestamp, detail_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                stage,
                worker_name,
                when.isoformat(),
                json.dumps(payload, default=str, ensure_ascii=False),
            ),
        )
        event_id = int(cursor.lastrowid)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM audit_events WHERE id = ?", (event_id,)
        ).fetchone()
    return _row_to_audit_event(row)


def list_audit_events(
    job_id: str,
    *,
    db_path: Path | str | None = None,
) -> list[StoredAuditEvent]:
    """Return the full audit trail for a job_id in insertion order."""
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM audit_events
            WHERE job_id = ?
            ORDER BY id ASC
            """,
            (job_id,),
        ).fetchall()
    return [_row_to_audit_event(row) for row in rows]
