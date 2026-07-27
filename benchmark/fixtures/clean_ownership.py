"""Clean ownership check — should produce zero findings."""

from __future__ import annotations

NOTES: dict[int, dict[str, object]] = {
    10: {"owner_id": 1, "title": "Alice private standup", "body": "salary notes"},
    11: {"owner_id": 2, "title": "Bob travel plan", "body": "passport on file"},
}


def get_note(note_id: int, current_user_id: int) -> dict[str, object] | None:
    note = NOTES.get(note_id)
    if note is None:
        return None
    if note["owner_id"] != current_user_id:
        raise PermissionError("not the owner")
    return note
