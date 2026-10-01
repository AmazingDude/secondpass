"""Writable state locations, independent of review-file identity."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path, user_log_path


@dataclass(frozen=True)
class StatePaths:
    review_db: Path
    memory_dir: Path
    tool_log: Path


def _default_state_paths() -> StatePaths:
    override = os.getenv("SECONDPASS_DATA_DIR")
    if override:
        data_dir = Path(override).expanduser()
        if not data_dir.is_absolute():
            raise ValueError("SECONDPASS_DATA_DIR must be an absolute path")
        return StatePaths(
            review_db=data_dir / "secondpass.db",
            memory_dir=data_dir / "chromadb",
            tool_log=data_dir / "tool_calls.log",
        )

    source_root = Path(__file__).resolve().parent.parent
    if (source_root / ".git").exists():
        # Existing checkouts keep their history and lesson memory in place.
        return StatePaths(
            review_db=source_root / ".secondpass" / "secondpass.db",
            memory_dir=source_root / ".chromadb",
            tool_log=source_root / "tool_calls.log",
        )

    data_dir = user_data_path("secondpass", appauthor=False)
    return StatePaths(
        review_db=data_dir / "secondpass.db",
        memory_dir=data_dir / "chromadb",
        tool_log=user_log_path("secondpass", appauthor=False) / "tool_calls.log",
    )


DEFAULT_STATE_PATHS = _default_state_paths()
