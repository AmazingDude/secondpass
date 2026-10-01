"""Installed-package behavior that cannot be verified from an editable checkout."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path


def test_installed_wheel_seeds_and_writes_state_outside_checkout(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(project_root),
        ],
        cwd=tmp_path,
        check=True,
    )
    wheel = next(wheel_dir.glob("secondpass-*.whl"))
    install_dir = tmp_path / "installed"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--target",
            str(install_dir),
            str(wheel),
        ],
        cwd=tmp_path,
        check=True,
    )
    script = """
import json
import sys
from importlib.resources import files
from pathlib import Path

if sys.argv[5] == "default":
    import platformdirs

    user_root = Path(sys.argv[6])
    platformdirs.user_data_path = lambda *args, **kwargs: user_root / "data"
    platformdirs.user_log_path = lambda *args, **kwargs: user_root / "logs"

sys.path.insert(0, sys.argv[1])
import app
from app import memory
from app.hooks import log_agent_event
from app.persistence import list_reviews

assert app.__file__.startswith(sys.argv[1])
lessons = json.loads(files("app").joinpath("security_lessons.json").read_text(encoding="utf-8"))
assert len(lessons) == 5
assert lessons[0]["id"] == "lesson-1"

class Collection:
    def count(self):
        return 0

    def add(self, *, ids, documents, metadatas):
        assert ids == [f"lesson-{number}" for number in range(1, 6)]
        assert len(documents) == len(metadatas) == 5

class Client:
    def __init__(self, *, path):
        assert path == sys.argv[3]

    def get_or_create_collection(self, *, name):
        assert name == "security_lessons"
        return Collection()

memory.chromadb.PersistentClient = Client
assert list_reviews() == []
assert memory.seed_memory() == 5
log_agent_event("supervisor -> security worker")
assert Path(sys.argv[2]).is_file()
assert Path(sys.argv[3]).is_dir()
assert "supervisor -> security worker" in Path(sys.argv[4]).read_text()
assert not (Path(sys.argv[1]) / ".secondpass").exists()
assert not (Path(sys.argv[1]) / ".chromadb").exists()
assert not (Path(sys.argv[1]) / "tool_calls.log").exists()
"""
    state_dir = tmp_path / "state"
    readonly_dirs = (
        [
            (path, stat.S_IMODE(path.stat().st_mode))
            for path in (install_dir, *install_dir.rglob("*"))
            if path.is_dir()
        ]
        if os.name == "posix"
        else []
    )
    for path, _ in readonly_dirs:
        path.chmod(0o555)
    try:
        subprocess.run(
            [
                sys.executable, "-c", script, str(install_dir),
                str(state_dir / "secondpass.db"), str(state_dir / "chromadb"),
                str(state_dir / "tool_calls.log"), "override", "",
            ],
            cwd=tmp_path,
            check=True,
            env={**os.environ, "SECONDPASS_DATA_DIR": str(state_dir)},
        )
        user_root = tmp_path / "user"
        default_env = os.environ.copy()
        default_env.pop("SECONDPASS_DATA_DIR", None)
        subprocess.run(
            [
                sys.executable, "-c", script, str(install_dir),
                str(user_root / "data" / "secondpass.db"),
                str(user_root / "data" / "chromadb"),
                str(user_root / "logs" / "tool_calls.log"), "default", str(user_root),
            ],
            cwd=tmp_path,
            check=True,
            env=default_env,
        )
    finally:
        for path, mode in reversed(readonly_dirs):
            path.chmod(mode)
