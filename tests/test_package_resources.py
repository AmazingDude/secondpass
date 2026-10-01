"""Installed-package behavior that cannot be verified from an editable checkout."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def installed_wheel(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    tmp_path = tmp_path_factory.mktemp("installed-wheel")
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
            sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
            "--target", str(install_dir), str(wheel),
        ],
        cwd=tmp_path,
        check=True,
    )
    readonly_paths = (
        [
            (path, stat.S_IMODE(path.stat().st_mode))
            for path in (install_dir, *install_dir.rglob("*"))
        ]
        if os.name == "posix"
        else []
    )
    try:
        for path, _ in readonly_paths:
            path.chmod(0o555 if path.is_dir() else 0o444)
        yield install_dir
    finally:
        for path, mode in reversed(readonly_paths):
            path.chmod(mode)


_OFFLINE_CLI_SCRIPT = """
import importlib.abc
import importlib.metadata as metadata
import socket
import sqlite3
import sys

class MissingIntegrations(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"chromadb", "openai", "tavily", "dotenv", "fastapi", "mcp", "uvicorn"}:
            raise ModuleNotFoundError(f"Integration unavailable: {fullname}", name=fullname)

def unavailable(*args, **kwargs):
    raise AssertionError("Setup commands must not access the network or initialize SQLite")

sys.meta_path.insert(0, MissingIntegrations())
socket.socket.connect = unavailable
socket.socket.connect_ex = unavailable
socket.getaddrinfo = unavailable
sqlite3.connect = unavailable
sys.path.insert(0, sys.argv[1])
install_dir = sys.argv[1]
missing_packages = set(sys.argv[2].split(","))
real_version = metadata.version
def version(name):
    if name in missing_packages:
        raise metadata.PackageNotFoundError(name)
    return real_version(name)
metadata.version = version
sys.argv = ["secondpass", *sys.argv[3:]]
entry = next(entry for entry in metadata.distribution("secondpass").entry_points
             if entry.group == "console_scripts" and entry.name == "secondpass")
run = entry.load()
import app
assert app.__file__.startswith(install_dir)
run()
"""


def _offline_cli(
    installed_wheel: Path,
    tmp_path: Path,
    args: list[str],
    *,
    missing: str = "",
    data_dir: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {
        name: value for name, value in os.environ.items()
        if name not in {
            "OPENAI_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
            "TAVILY_API_KEY", "LLM_PROVIDER", "LLM_MODEL",
        }
    }
    environment["SECONDPASS_DATA_DIR"] = data_dir or str(tmp_path / "state")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", _OFFLINE_CLI_SCRIPT, str(installed_wheel), missing, *args],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "command", [[], ["review"], ["search-memory"], ["search-web"], ["doctor"]]
)
def test_installed_help_needs_no_integrations(
    installed_wheel: Path, tmp_path: Path, command: list[str],
) -> None:
    result = _offline_cli(installed_wheel, tmp_path, [*command, "--help"])

    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout
    assert not (tmp_path / "state").exists()


def test_installed_doctor_reports_missing_memory_without_initializing_it(
    installed_wheel: Path, tmp_path: Path,
) -> None:
    result = _offline_cli(installed_wheel, tmp_path, ["doctor"], missing="chromadb")

    assert result.returncode == 1, result.stderr
    assert "python -m pip install chromadb" in result.stdout
    assert "secondpass.db" in result.stdout
    assert "not tested" in result.stdout
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "state").exists()


def test_installed_doctor_inventory_does_not_load_repository_env(
    installed_wheel: Path, tmp_path: Path,
) -> None:
    canary = "doctor-private-key-canary"
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={canary}\n", encoding="utf-8")

    result = _offline_cli(installed_wheel, tmp_path, ["doctor"])

    assert result.returncode == 0, result.stderr
    assert "Package presence only" in result.stdout
    assert canary not in result.stdout + result.stderr
    assert not (tmp_path / "state").exists()


def test_installed_memory_search_explains_missing_dependency(
    installed_wheel: Path, tmp_path: Path,
) -> None:
    result = _offline_cli(installed_wheel, tmp_path, ["search-memory", "ownership checks"])

    assert result.returncode == 1, result.stdout + result.stderr
    assert "python -m pip install chromadb" in result.stdout
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("command", ["help", "doctor"])
def test_setup_commands_handle_invalid_state_configuration(
    installed_wheel: Path, tmp_path: Path, command: str,
) -> None:
    args = ["--help"] if command == "help" else ["doctor"]
    result = _offline_cli(installed_wheel, tmp_path, args, data_dir="relative-state")

    assert result.returncode == (0 if command == "help" else 1), result.stderr
    if command == "doctor":
        assert "must be an absolute path" in result.stdout
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "relative-state").exists()


def test_installed_wheel_seeds_and_writes_state_outside_checkout(
    installed_wheel: Path, tmp_path: Path,
) -> None:
    install_dir = installed_wheel
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

import chromadb
chromadb.PersistentClient = Client
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


@pytest.mark.parametrize("finding_count", [0, 1])
@pytest.mark.parametrize("missing_module", ["chromadb", "onnxruntime"])
def test_installed_review_survives_missing_memory_dependencies(
    installed_wheel: Path, tmp_path: Path, finding_count: int, missing_module: str,
) -> None:
    script = """
import importlib.abc
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

class MissingChroma(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] == "chromadb":
            raise ModuleNotFoundError("private-import-error-canary", name=sys.argv[4])

def no_network(*args, **kwargs):
    raise AssertionError("The scripted review must not access the network")

sys.meta_path.insert(0, MissingChroma())
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.getaddrinfo = no_network
sys.path.insert(0, sys.argv[1])
import app
from app import agent, supervisor
from app.memory import MissingMemoryDependencyError, seed_memory
from app.persistence import list_reviews
assert app.__file__.startswith(sys.argv[1])

# Only the absent Chroma package gets the install diagnostic. A missing
# transitive dependency must retain its original classification for callers.
try:
    seed_memory()
except MissingMemoryDependencyError as exc:
    assert sys.argv[4] == "chromadb"
    assert "python -m pip install chromadb" in str(exc)
except ModuleNotFoundError as exc:
    assert sys.argv[4] == "onnxruntime"
    assert exc.name == "onnxruntime"
else:
    raise AssertionError("Missing memory dependency must not initialize")

target = Path("shell.py").resolve()
target.write_text("import subprocess\\nsubprocess.run(command, shell=True)\\n")
finding_count = int(sys.argv[3])
agent.run_static_scan = lambda paths: [{
    "rule_id": "python.lang.security.audit.subprocess-shell-true",
    "severity": "ERROR", "path": str(target), "line": 2,
    "message": "Shell execution accepts untrusted input.",
    "snippet": "subprocess.run(command, shell=True)",
}] if finding_count else []

def chat(messages, tools=None, temperature=None):
    system = messages[0]["content"]
    if "careful security logic reviewer" in system:
        result = {"has_issues": False, "summary": "No additional issues.", "issues": []}
    elif "Decide which specialist workers" in system:
        result = {"use_memory": True, "use_web": False}
    elif "Synthesize a final security review" in system:
        result = {"explanation": "Shell execution accepts untrusted input.",
                  "suggested_fix": "Pass an argument list without a shell."}
    else:
        raise AssertionError("Unexpected model request")
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(result), tool_calls=None)
    )])

agent.chat = chat
supervisor.chat = chat
report = supervisor.supervise_review(str(target), run_architecture=False)
assert report["summary"]["inconclusive"] is True
assert report["summary"]["no_issues"] is False
security = report["security"]
assert security["finding_count"] == finding_count
if finding_count:
    assert security["all_findings"][0]["finding"]["rule_id"] == "python.lang.security.audit.subprocess-shell-true"
    assert security["all_findings"][0]["memory_worker"]["unavailable"] is True
    assert security["all_findings"][0]["routing"]["use_memory"] is False
reviews = list_reviews()
assert len(reviews) == 1
assert reviews[0].review_result.coverage_status == "inconclusive"
assert "private-import-error-canary" not in json.dumps(report)
assert not (Path(sys.argv[2]) / "chromadb").exists()
"""
    state_dir = tmp_path / "state"
    result = subprocess.run(
        [sys.executable, "-c", script, str(installed_wheel), str(state_dir), str(finding_count), missing_module],
        cwd=tmp_path,
        env={**os.environ, "SECONDPASS_DATA_DIR": str(state_dir), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "private-import-error-canary" not in result.stdout + result.stderr
