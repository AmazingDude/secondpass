"""Textbook IDOR: fetch by id with no ownership check."""

from __future__ import annotations

NOTES: dict[int, dict[str, object]] = {
    10: {"owner_id": 1, "title": "Alice private standup", "body": "salary notes"},
    11: {"owner_id": 2, "title": "Bob travel plan", "body": "passport on file"},
}


def get_note(note_id: int, current_user_id: int) -> dict[str, object] | None:
    # BUG: returns any note by id; never compares owner_id to current_user_id.
    return NOTES.get(note_id)
