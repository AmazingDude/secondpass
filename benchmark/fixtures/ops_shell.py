"""Textbook command injection via shell=True."""

from __future__ import annotations

import subprocess


def run_backup(archive_name: str) -> str:
    # BUG: user-controlled archive_name is interpolated into a shell command.
    completed = subprocess.run(
        f"tar -czf {archive_name} ./data",
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout or completed.stderr
