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


_INSTALLED_REVIEW_SCRIPT = """
import importlib.abc
import importlib.metadata as metadata
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

class MissingChroma(importlib.abc.MetaPathFinder):
    attempts = 0
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] == "chromadb":
            self.attempts += 1
            raise ModuleNotFoundError("private-import-error-canary", name=sys.argv[4])

def no_network(*args, **kwargs):
    raise AssertionError("The scripted review must not access the network")

missing_chroma = MissingChroma()
sys.meta_path.insert(0, missing_chroma)
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
mode = sys.argv[5]
state_dir = Path(sys.argv[2])
if mode == "supervised":
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
finding_count = int(sys.argv[3])
target.write_text(
    "import subprocess\\nsubprocess.run(command, shell=True)\\n" if finding_count
    else "def greet():\\n    return 'hello'\\n"
)
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
    elif "You are ArchitectureWorker" in system:
        result = {"has_issues": False, "summary": "No architecture issues.", "issues": []}
    else:
        raise AssertionError("Unexpected model request")
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(result), tool_calls=None)
    )])

agent.chat = chat
supervisor.chat = chat
if mode == "supervised":
    report = supervisor.supervise_review(str(target), run_architecture=False)
    assert report["summary"]["inconclusive"] is True
    assert report["summary"]["no_issues"] is False
    security = report["security"]
    assert security["finding_count"] == finding_count
    if finding_count:
        assert security["all_findings"][0]["finding"]["rule_id"] == "python.lang.security.audit.subprocess-shell-true"
        assert security["all_findings"][0]["memory_worker"]["unavailable"] is True
        assert security["all_findings"][0]["routing"]["use_memory"] is False
    assert "private-import-error-canary" not in json.dumps(report)
else:
    from app.workers import architecture_worker
    architecture_worker.chat = chat
    if mode == "diff":
        subprocess.run(["git", "init", "--quiet"], check=True)
        subprocess.run(["git", "add", "shell.py"], check=True)
        args = ["--diff"]
    else:
        args = [str(target) if mode == "file" else str(target.parent), "--workers", "1"]
    entry = next(entry for entry in metadata.distribution("secondpass").entry_points
                 if entry.group == "console_scripts" and entry.name == "secondpass")
    run = entry.load()
    sys.argv = ["secondpass", "review", *args, "--no-memory"]
    try:
        run()
    except SystemExit as exc:
        assert exc.code in (0, None)
    assert missing_chroma.attempts == 0

reviews = list_reviews()
security_reviews = [review for review in reviews if review.worker_name == "security"]
if mode != "diff":
    assert len(security_reviews) == 1
    assert len(security_reviews[0].review_result.findings) == finding_count
    expected_coverage = "inconclusive" if mode == "supervised" else "ok"
    assert security_reviews[0].review_result.coverage_status == expected_coverage
assert not (state_dir / "chromadb").exists()
"""


@pytest.mark.parametrize("finding_count", [0, 1])
@pytest.mark.parametrize("missing_module", ["chromadb", "onnxruntime"])
def test_installed_review_survives_missing_memory_dependencies(
    installed_wheel: Path, tmp_path: Path, finding_count: int, missing_module: str,
) -> None:
    state_dir = tmp_path / "state"
    result = subprocess.run(
        [sys.executable, "-c", _INSTALLED_REVIEW_SCRIPT, str(installed_wheel),
         str(state_dir), str(finding_count), missing_module, "supervised"],
        cwd=tmp_path,
        env={**os.environ, "SECONDPASS_DATA_DIR": str(state_dir), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "private-import-error-canary" not in result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["file", "directory", "diff"])
@pytest.mark.parametrize("finding_count", [0, 1])
def test_installed_cli_can_disable_lesson_retrieval(
    installed_wheel: Path, tmp_path: Path, mode: str, finding_count: int,
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _INSTALLED_REVIEW_SCRIPT, str(installed_wheel),
         str(tmp_path / "state"), str(finding_count), "chromadb", mode],
        cwd=tmp_path,
        env={**os.environ, "SECONDPASS_DATA_DIR": str(tmp_path / "state"),
             "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "private-import-error-canary" not in result.stdout + result.stderr
    if mode == "diff":
        assert f"Findings reviewed: {finding_count}" in result.stdout
        assert "Coverage: inconclusive" not in result.stdout


_INSTALLED_STATIC_SCRIPT = """
import importlib.abc
import importlib.metadata as metadata
import json
import socket
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

class NoAgentIntegrations(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'openai', 'chromadb', 'tavily', 'dotenv', 'fastapi', 'mcp', 'uvicorn'}:
            raise AssertionError('Static mode must not import agent integrations')

def no_network(*args, **kwargs):
    raise AssertionError('Static CLI must not contact the network')

sys.meta_path.insert(0, NoAgentIntegrations())
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.getaddrinfo = no_network
sqlite3.connect = no_network
install_dir = Path(sys.argv[1])
case = sys.argv[2]
sys.path.insert(0, str(install_dir))
from app import scanner
target = Path('shell.py').resolve()
target.write_text(
    'import subprocess\\nsubprocess.run(["echo", "hello"], shell=False)\\n'
    if case in {'clean', 'real-clean'} else
    'import subprocess\\nsubprocess.run(command, shell=True)\\n'
)

def semgrep(command, **kwargs):
    configs = [command[i + 1] for i, value in enumerate(command) if value == '--config']
    assert len(configs) == 1
    rule_path = Path(configs[0])
    assert rule_path.is_relative_to(install_dir)
    assert rule_path.is_file()
    assert 'subprocess.run' in rule_path.read_text()
    assert '--metrics=off' in command
    assert '--disable-version-check' in command
    if case == 'malformed':
        return SimpleNamespace(returncode=0, stderr='', stdout='[]')
    if case == 'malformed-findings':
        return SimpleNamespace(returncode=0, stderr='', stdout='{"results": [null]}')
    if case == 'malformed-coverage':
        return SimpleNamespace(returncode=0, stderr='', stdout='{"results": [], "paths": null}')
    if case == 'empty-finding':
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({
            'results': [{}], 'errors': [], 'paths': {'scanned': [str(target)]},
        }))
    payload = {
        'results': [] if case in {'skipped', 'clean'} else [{
                     'check_id': 'secondpass.python.subprocess-shell' if '--no-rewrite-rule-ids' in command
                                 else 'installed.app.secondpass.python.subprocess-shell',
                     'path': str(target), 'start': {'line': 2}, 'end': {'line': 2},
                     'extra': {'severity': 'WARNING', 'message': 'Inspect shell execution.'}}],
        'errors': None if case == 'invalid-errors' else
                  [{'message': 'private-scanner-error-canary'}] if case == 'partial' else [],
        'paths': {'scanned': [] if case == 'skipped' else
                  [str(target) + chr(0)] if case == 'invalid-path' else [str(target)]},
    }
    if case == 'invalid-finding-path':
        payload['results'].append({**payload['results'][0], 'path': str(target) + chr(0)})
    return SimpleNamespace(returncode=2 if case == 'partial-exit' else 0,
                           stderr='', stdout=json.dumps(payload))

if not case.startswith('real-'):
    scanner.subprocess.run = semgrep
entry = next(entry for entry in metadata.distribution('secondpass').entry_points
             if entry.group == 'console_scripts' and entry.name == 'secondpass')
run = entry.load()
sys.argv = ['secondpass', 'review', str(target), '--mode', 'static']
run()
"""


def test_installed_static_cli_runs_without_agent_integrations(
    installed_wheel: Path, tmp_path: Path,
) -> None:
    result = _installed_static_cli(installed_wheel, tmp_path, "finding")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Static rule matches: 1" in result.stdout
    assert "limited Python rule pack" in result.stdout
    assert "Rule: secondpass.python.subprocess-shell" in result.stdout, result.stdout
    assert not (tmp_path / "state" / "chromadb").exists()


def _installed_static_cli(
    installed_wheel: Path, tmp_path: Path, case: str,
) -> subprocess.CompletedProcess[str]:
    environment = {
        name: value for name, value in os.environ.items()
        if not name.endswith("_API_KEY") and name not in {"LLM_PROVIDER", "LLM_MODEL", "SEMGREP_APP_TOKEN"}
    }
    state_dir = tmp_path / "state"
    return subprocess.run(
        [sys.executable, "-c", _INSTALLED_STATIC_SCRIPT, str(installed_wheel), case],
        cwd=tmp_path,
        env={**environment, "SECONDPASS_DATA_DIR": str(state_dir),
             "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=90 if case.startswith("real-") else 30,
    )


@pytest.mark.parametrize("case", ["partial", "partial-exit", "invalid-errors", "invalid-path", "invalid-finding-path"])
def test_installed_static_cli_preserves_matches_from_incomplete_scan(
    installed_wheel: Path, tmp_path: Path, case: str,
) -> None:
    result = _installed_static_cli(installed_wheel, tmp_path, case)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Static review incomplete" in result.stdout
    assert "Static rule matches: 1" in result.stdout
    assert "secondpass.python.subprocess-shell" in result.stdout
    assert "private-scanner-error-canary" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_installed_static_cli_labels_complete_zero_match_result(
    installed_wheel: Path, tmp_path: Path,
) -> None:
    result = _installed_static_cli(installed_wheel, tmp_path, "clean")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Static rule matches: 0" in result.stdout
    assert "Zero matches only means this rule did not match" in result.stdout
    assert "No logic or architecture analysis was performed" in result.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="Real Semgrep startup is validated on Linux CI only")
@pytest.mark.parametrize("case, count", [("real-finding", 1), ("real-clean", 0)])
def test_installed_static_cli_with_real_semgrep(
    installed_wheel: Path, tmp_path: Path, case: str, count: int,
) -> None:
    result = _installed_static_cli(installed_wheel, tmp_path, case)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Static rule matches: {count}" in result.stdout
    assert "limited Python rule pack" in result.stdout
    if count:
        assert "Rule: secondpass.python.subprocess-shell" in result.stdout, result.stdout
    assert not (tmp_path / "state" / "chromadb").exists()


def test_installed_static_cli_does_not_treat_skipped_file_as_complete(
    installed_wheel: Path, tmp_path: Path,
) -> None:
    result = _installed_static_cli(installed_wheel, tmp_path, "skipped")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Static review incomplete" in result.stdout
    assert "Zero matches only means" not in result.stdout


@pytest.mark.parametrize("case", ["malformed", "malformed-findings", "malformed-coverage", "empty-finding"])
def test_installed_static_cli_handles_malformed_output_without_traceback(
    installed_wheel: Path, tmp_path: Path, case: str,
) -> None:
    result = _installed_static_cli(installed_wheel, tmp_path, case)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Static review incomplete" in result.stdout
    assert "Traceback" not in result.stderr
