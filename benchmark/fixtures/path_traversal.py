"""Textbook path traversal — user-controlled filename escapes the intended dir."""

from __future__ import annotations

from pathlib import Path

EXPORT_DIR = Path("/var/data/exports")


def read_export(filename: str) -> str:
    # BUG: filename comes straight from the caller/request with no sanitization,
    # so "../../etc/passwd" escapes EXPORT_DIR entirely.
    target = EXPORT_DIR / filename
    return target.read_text(encoding="utf-8")
