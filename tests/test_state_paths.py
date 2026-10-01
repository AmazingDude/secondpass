"""Default state behavior through the review-history and logging interfaces."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("checkout", [True, False])
def test_state_stays_in_checkout_or_user_directory(tmp_path: Path, checkout: bool) -> None:
    project_root = Path(__file__).resolve().parents[1]
    package_root = tmp_path / "package"
    shutil.copytree(
        project_root / "app",
        package_root / "app",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    if checkout:
        (package_root / ".git").mkdir()

    user_root = tmp_path / "user-state"
    environment = os.environ.copy()
    environment.pop("SECONDPASS_DATA_DIR", None)
    script = """
import sys
from pathlib import Path
import platformdirs

user_root = Path(sys.argv[2])
platformdirs.user_data_path = lambda *args, **kwargs: user_root / "data"
platformdirs.user_log_path = lambda *args, **kwargs: user_root / "logs"
sys.path.insert(0, sys.argv[1])
from app.hooks import log_agent_event
from app.persistence import list_reviews

assert list_reviews() == []
log_agent_event("supervisor -> security worker")
"""
    subprocess.run(
        [sys.executable, "-c", script, str(package_root), str(user_root)],
        cwd=tmp_path,
        env=environment,
        check=True,
    )

    if checkout:
        assert (package_root / ".secondpass" / "secondpass.db").is_file()
        assert (package_root / "tool_calls.log").is_file()
        assert not user_root.exists()
    else:
        assert len(list(user_root.rglob("secondpass.db"))) == 1
        assert len(list(user_root.rglob("tool_calls.log"))) == 1
        assert not (package_root / ".secondpass").exists()
        assert not (package_root / "tool_calls.log").exists()


def test_relative_state_override_is_rejected_before_writing(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["SECONDPASS_DATA_DIR"] = "relative-state"
    project_root = Path(__file__).resolve().parents[1]
    environment["PYTHONPATH"] = str(project_root)
    result = subprocess.run(
        [sys.executable, "-c", "from app.persistence import list_reviews; list_reviews()"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "SECONDPASS_DATA_DIR must be an absolute path" in result.stderr
    assert not (tmp_path / "relative-state").exists()
