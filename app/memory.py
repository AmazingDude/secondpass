"""Persistent lesson memory backed by ChromaDB."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import chromadb

from app.hooks import log_tool_call

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LESSONS_PATH = _ROOT / "security_lessons.json"
_DEFAULT_DB_PATH = _ROOT / ".chromadb"
_COLLECTION_NAME = "security_lessons"


def _lesson_document(lesson: dict[str, Any]) -> str:
    """Build the text that gets embedded for a lesson."""
    return "\n".join(
        [
            f"Type: {lesson.get('type', '')}",
            f"Pattern: {lesson.get('pattern', '')}",
            f"Bad example: {lesson.get('bad_example', '')}",
            f"Fix: {lesson.get('fix', '')}",
            f"Source: {lesson.get('source', '')}",
        ]
    )


def _lesson_metadata(lesson: dict[str, Any]) -> dict[str, str]:
    return {
        "type": str(lesson.get("type", "")),
        "pattern": str(lesson.get("pattern", "")),
        "bad_example": str(lesson.get("bad_example", "")),
        "fix": str(lesson.get("fix", "")),
        "source": str(lesson.get("source", "")),
    }


def init_memory(persist_directory: str | Path | None = None) -> chromadb.Collection:
    """Initialize a persistent ChromaDB collection on disk."""
    db_path = Path(persist_directory) if persist_directory else _DEFAULT_DB_PATH
    db_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db_path))
    return client.get_or_create_collection(name=_COLLECTION_NAME)


def seed_memory(
    lessons_path: str | Path | None = None,
    persist_directory: str | Path | None = None,
) -> int:
    """Load lessons from JSON and embed them if the collection is empty."""
    collection = init_memory(persist_directory)
    if collection.count() > 0:
        return 0

    path = Path(lessons_path) if lessons_path else _DEFAULT_LESSONS_PATH
    lessons = json.loads(path.read_text(encoding="utf-8"))
    if not lessons:
        return 0

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, str]] = []
    for lesson in lessons:
        lesson_id = str(lesson.get("id") or f"lesson-{uuid.uuid4()}")
        ids.append(lesson_id)
        documents.append(_lesson_document(lesson))
        metadatas.append(_lesson_metadata(lesson))

    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    return len(ids)


@log_tool_call
def search_memory(
    query: str,
    n_results: int = 3,
    persist_directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return the closest matching lessons for a natural-language query."""
    collection = init_memory(persist_directory)
    if collection.count() == 0:
        return []

    limit = max(1, min(n_results, collection.count()))
    raw = collection.query(query_texts=[query], n_results=limit)

    matches: list[dict[str, Any]] = []
    ids = (raw.get("ids") or [[]])[0]
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]

    for index, lesson_id in enumerate(ids):
        metadata = metadatas[index] if index < len(metadatas) else {}
        distance = distances[index] if index < len(distances) else None
        confidence = None
        if isinstance(distance, (int, float)):
            confidence = round(1 / (1 + float(distance)), 4)
        matches.append(
            {
                "id": lesson_id,
                "document": documents[index] if index < len(documents) else "",
                "distance": distance,
                "confidence": confidence,
                "type": metadata.get("type", ""),
                "pattern": metadata.get("pattern", ""),
                "bad_example": metadata.get("bad_example", ""),
                "fix": metadata.get("fix", ""),
                "source": metadata.get("source", ""),
            }
        )
    return matches


@log_tool_call
def save_finding(
    finding: dict[str, Any],
    persist_directory: str | Path | None = None,
    *,
    duplicate_distance_threshold: float = 1.15,
) -> dict[str, Any]:
    """Add a confirmed lesson, skipping near-duplicates already in memory.

    Returns a status dict:
    - ``{"status": "saved", "id": "..."}``
    - ``{"status": "skipped", "reason": "...", "matched_id": "...", "distance": ...}``
    """
    collection = init_memory(persist_directory)
    document = _lesson_document(finding)

    if collection.count() > 0:
        raw = collection.query(query_texts=[document], n_results=1)
        matched_ids = (raw.get("ids") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        if matched_ids:
            distance = distances[0] if distances else None
            if isinstance(distance, (int, float)) and float(distance) <= duplicate_distance_threshold:
                return {
                    "status": "skipped",
                    "reason": (
                        "near_duplicate of an existing lesson; "
                        "not saved to avoid cluttering memory"
                    ),
                    "matched_id": matched_ids[0],
                    "distance": float(distance),
                }

    lesson_id = str(finding.get("id") or f"finding-{uuid.uuid4()}")
    # Avoid colliding with an existing id by appending a suffix when needed.
    existing = set(collection.get(include=[]).get("ids") or [])
    if lesson_id in existing:
        lesson_id = f"{lesson_id}-{uuid.uuid4().hex[:8]}"

    collection.add(
        ids=[lesson_id],
        documents=[document],
        metadatas=[_lesson_metadata(finding)],
    )
    return {"status": "saved", "id": lesson_id}
