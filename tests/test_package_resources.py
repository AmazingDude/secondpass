"""Installed-package behavior that cannot be verified from an editable checkout."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_wheel_exposes_default_lessons_outside_checkout(tmp_path: Path) -> None:
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
        capture_output=True,
        text=True,
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
        capture_output=True,
        text=True,
    )
    script = """
import json
import sys
from importlib.resources import files

sys.path.insert(0, sys.argv[1])
import app
from app import memory

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
        assert path == sys.argv[2]

    def get_or_create_collection(self, *, name):
        assert name == "security_lessons"
        return Collection()

memory.chromadb.PersistentClient = Client
assert memory.seed_memory(persist_directory=sys.argv[2]) == 5
"""
    subprocess.run(
        [sys.executable, "-c", script, str(install_dir), str(tmp_path / "state")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
