"""Offline tests for the Architecture Worker (pure helpers + one mocked LLM path)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.context import ContextFile
from app.schema import Finding
from app.workers.architecture_worker import (
    CLAIM_UNVERIFIED_SUMMARY,
    _SYSTEM,
    _issue_to_finding,
    adjust_architecture_confidence,
    is_insufficient_structure_claim,
    is_off_target_finding,
    is_security_category_bleed,
    is_soft_only_smell,
    run_architecture_worker,
)


def test_system_prompt_encodes_fp_hardening_rules() -> None:
    assert "contradicts patterns visible" in _SYSTEM
    assert "Do NOT invent external style guides" in _SYSTEM
    assert "Leading underscore" in _SYSTEM
    assert "could extract a shared helper" in _SYSTEM
    assert "confidence < 80" in _SYSTEM
    assert "Reserve confidence >= 80" in _SYSTEM
    assert "IDOR" in _SYSTEM
    assert "ownership checks" in _SYSTEM
    assert "Security Worker only" in _SYSTEM
    assert "NOTES_WITHOUT_OWNERSHIP_CHECK" in _SYSTEM
    assert "NOT additional subjects" in _SYSTEM
    assert "MUST concern the target file" in _SYSTEM
    assert "INSUFFICIENT STRUCTURE" in _SYSTEM
    assert "standard library module" in _SYSTEM
    assert "no architectural layers" in _SYSTEM


def test_issue_to_finding_produces_schema_valid_finding() -> None:
    repo = Path(__file__).resolve().parents[1]
    finding = _issue_to_finding(
        str(repo / "app" / "agent.py"),
        {
            "finding_type": "layering_violation",
            "confidence": 82,
            "location": "app/agent.py:40",
            "evidence": "agent.py imports app.cli directly",
            "message": "Core module depends on the CLI layer",
            "suggested_fix": "Invert the dependency; move shared logic out of cli.py",
        },
        target_source="from app.cli import main\n",
        project_root=repo,
    )

    assert isinstance(finding, Finding)
    assert finding.finding_type == "layering_violation"
    assert finding.detection_method == "llm_reasoning"
    assert finding.confidence == 82
    assert "app/agent.py:40" in finding.evidence
    assert "agent.py imports app.cli directly" in finding.evidence


def test_issue_to_finding_clamps_out_of_range_confidence() -> None:
    repo = Path(__file__).resolve().parents[1]
    finding = _issue_to_finding(
        str(repo / "app" / "agent.py"),
        {
            "finding_type": "layering_violation",
            "location": "app/agent.py",
            "evidence": "agent.py imports CLI from app.cli",
            "message": "imports CLI from core",
            "confidence": 250,
        },
        target_source="from app.cli import main\n",
        project_root=repo,
    )
    assert finding is not None
    assert finding.confidence == 100


def test_issue_to_finding_returns_none_when_ungrounded() -> None:
    finding = _issue_to_finding("app/agent.py", {"confidence": 90})
    assert finding is None


def test_soft_duplication_like_agent_fp_is_dropped() -> None:
    finding = _issue_to_finding(
        "app/agent.py",
        {
            "finding_type": "duplicated_logic",
            "confidence": 90,
            "location": "map_semgrep_finding / map_logic_issue",
            "evidence": "Both functions look similar and build a StructuredFinding",
            "message": "Could extract a shared helper for mapping findings",
            "suggested_fix": "Extract a shared mapper helper",
        },
    )
    assert finding is None


def test_soft_naming_like_agent_fp_is_dropped() -> None:
    finding = _issue_to_finding(
        "app/agent.py",
        {
            "finding_type": "naming_convention",
            "confidence": 80,
            "location": "_LOGIC_ASSESS_SYSTEM",
            "evidence": "Leading underscore on a SCREAMING_SNAKE constant",
            "message": "Conventionally rename to LOGIC_ASSESS_SYSTEM (public constant)",
            "suggested_fix": "Rename to LOGIC_ASSESS_SYSTEM; remove leading underscore",
        },
    )
    assert finding is None


def test_adjust_caps_soft_smell_without_hard_markers() -> None:
    capped = adjust_architecture_confidence(
        "duplicated_logic",
        90,
        message="These two mappers overlap somewhat",
        evidence="parallel structure",
        suggested_fix="maybe refactor later",
    )
    assert capped == 79


def test_adjust_keeps_hard_duplication_above_gate() -> None:
    kept = adjust_architecture_confidence(
        "duplicated_logic",
        90,
        message="Near-identical retry loop copy-pasted into worker.py",
        evidence="verbatim identical block in both files",
        suggested_fix="Extract once; edits must stay in sync",
    )
    assert kept == 90


def test_adjust_keeps_hard_naming_contradiction_above_gate() -> None:
    kept = adjust_architecture_confidence(
        "naming_convention",
        85,
        message="Name contradicts visible pattern in related files",
        evidence="same package uses fetch_user; this file uses GetUser",
        suggested_fix="Rename to fetch_user",
    )
    assert kept == 85


def test_adjust_does_not_cap_layering() -> None:
    assert (
        adjust_architecture_confidence(
            "layering_violation",
            90,
            message="core imports ui",
        )
        == 90
    )


def test_is_soft_only_smell_true_for_mild_similarity() -> None:
    assert is_soft_only_smell(
        "duplicated_logic",
        message="These helpers are similar",
        evidence="could extract a shared helper",
    )


def test_security_bleed_idor_layering_is_dropped() -> None:
    """notes_idor-style category bleed must not become an architecture finding."""
    finding = _issue_to_finding(
        "benchmark/fixtures/notes_idor.py",
        {
            "finding_type": "layering_violation",
            "confidence": 90,
            "location": "get_note",
            "evidence": (
                "get_note returns NOTES.get(note_id) without comparing "
                "owner_id to current_user_id"
            ),
            "message": "Missing ownership check allows unauthorized access (IDOR)",
            "suggested_fix": "Add an ownership / authorization check before return",
        },
    )
    assert finding is None
    assert is_security_category_bleed(
        finding_type="layering_violation",
        evidence="owner_id is never compared to current_user_id",
        message="IDOR / missing ownership",
    )


def test_security_bleed_ownership_naming_suggestion_is_dropped() -> None:
    finding = _issue_to_finding(
        "benchmark/fixtures/notes_idor.py",
        {
            "finding_type": "naming_convention",
            "confidence": 79,
            "location": "NOTES",
            "evidence": "NOTES holds records keyed only by id",
            "message": "Name does not reflect missing ownership check",
            "suggested_fix": "Rename to NOTES_WITHOUT_OWNERSHIP_CHECK",
        },
    )
    assert finding is None


def test_genuine_architecture_finding_survives_security_filter() -> None:
    """Non-authz architecture signal must not be eaten by the bleed filter."""
    repo = Path(__file__).resolve().parents[1]
    finding = _issue_to_finding(
        str(repo / "app" / "agent.py"),
        {
            "finding_type": "dependency_direction",
            "confidence": 88,
            "location": "app/agent.py:12",
            "evidence": (
                "agent.py imports app.cli; related files use "
                "the same pattern inverted — core depends on the CLI layer"
            ),
            "message": "Core service layer depends on the CLI presentation layer",
            "suggested_fix": "Move shared rendering helpers out of cli.py",
        },
        target_source="from app.cli import render_report\n",
        project_root=repo,
    )
    assert finding is not None
    assert finding.finding_type == "dependency_direction"
    assert finding.confidence == 88
    assert not is_security_category_bleed(
        finding_type=finding.finding_type,
        message="Core service layer depends on the CLI presentation layer",
        evidence="service.py imports app.cli.render_report",
        suggested_fix="Move shared rendering helpers out of cli.py",
    )


def test_stdlib_subprocess_layering_claim_is_dropped() -> None:
    """ops_shell-shaped FP: inventing layers from a stdlib import."""
    repo = Path(__file__).resolve().parents[1]
    path = repo / "benchmark/fixtures/ops_shell.py"
    source = path.read_text(encoding="utf-8")
    issue = {
        "finding_type": "layering_violation",
        "confidence": 85,
        "location": "ops_shell.py:5",
        "evidence": (
            "The target file imports subprocess, which is a lower-level "
            "system module."
        ),
        "message": (
            "ops_shell.py should not depend on lower-level system utilities "
            "directly."
        ),
        "suggested_fix": (
            "Encapsulate subprocess calls in a higher-level service layer."
        ),
    }
    assert is_insufficient_structure_claim(
        finding_type=issue["finding_type"],
        target_source=source,
        target_path=str(path),
        project_root=repo,
        message=issue["message"],
        evidence=issue["evidence"],
        suggested_fix=issue["suggested_fix"],
    )
    assert (
        _issue_to_finding(
            str(path), issue, target_source=source, project_root=repo
        )
        is None
    )


def test_stdlib_pathlib_layering_claim_is_dropped() -> None:
    """path_traversal-shaped FP: pathlib framed as a higher-level layer."""
    repo = Path(__file__).resolve().parents[1]
    path = repo / "benchmark/fixtures/path_traversal.py"
    source = path.read_text(encoding="utf-8")
    issue = {
        "finding_type": "layering_violation",
        "confidence": 85,
        "location": "path_traversal.py:5",
        "evidence": (
            "The target file imports 'Path' from 'pathlib', which is a "
            "higher-level module."
        ),
        "message": (
            "The target file should not depend on higher-level modules that "
            "are not part of its layer."
        ),
        "suggested_fix": (
            "Refactor to use a dedicated service layer for file operations."
        ),
    }
    assert is_insufficient_structure_claim(
        finding_type=issue["finding_type"],
        target_source=source,
        target_path=str(path),
        project_root=repo,
        message=issue["message"],
        evidence=issue["evidence"],
        suggested_fix=issue["suggested_fix"],
    )
    assert (
        _issue_to_finding(
            str(path), issue, target_source=source, project_root=repo
        )
        is None
    )


def test_invented_application_layer_with_stdlib_is_dropped() -> None:
    """Invented 'service layer' wording must not rescue a stdlib-only file."""
    source = "from pathlib import Path\n\ndef read(p: str) -> str:\n    return Path(p).read_text()\n"
    assert is_insufficient_structure_claim(
        finding_type="layering_violation",
        target_source=source,
        target_path="tmp_only.py",
        evidence="pathlib is a standard library module / application layer",
        message="should not depend on higher-level abstractions",
        suggested_fix="use a dedicated service layer for file operations",
    )


def test_django_framework_self_import_layering_is_dropped() -> None:
    """Isolated Django view importing django.* has no resolved project edge."""
    source = (
        "from django.core.exceptions import ImproperlyConfigured\n"
        "from django.db import models\n"
        "from django.http import Http404\n\n"
        "class Detail:\n"
        "    def get_object(self):\n"
        "        return None\n"
    )
    issue = {
        "finding_type": "layering_violation",
        "confidence": 85,
        "location": "django_detail_view.py:1",
        "evidence": (
            "Imports from django.core.exceptions, django.db, and django.http "
            "indicate dependencies on Django's core framework."
        ),
        "message": (
            "The target file imports Django's core framework, which is a "
            "higher-level dependency."
        ),
        "suggested_fix": "Minimize direct dependencies on Django's core framework.",
    }
    assert is_insufficient_structure_claim(
        finding_type=issue["finding_type"],
        target_source=source,
        target_path="smoke/django_detail_view.py",
        message=issue["message"],
        evidence=issue["evidence"],
        suggested_fix=issue["suggested_fix"],
    )
    assert (
        _issue_to_finding(
            "smoke/django_detail_view.py", issue, target_source=source
        )
        is None
    )


def test_werkzeug_sys_stdout_with_unresolved_relative_is_dropped() -> None:
    """sys.stdout claim must not ride on an unresolved relative import."""
    source = (
        "import sys\n"
        "from .repr import debug_repr\n\n"
        "def showtraceback():\n"
        "    sys.stdout._write('x')\n"
    )
    issue = {
        "finding_type": "layering_violation",
        "confidence": 85,
        "location": "werkzeug_debug_console.py:118",
        "evidence": "sys.stdout._write(te.render_traceback_html())",
        "message": (
            "The method showtraceback is directly accessing sys.stdout, "
            "which is a higher-level component, violating the layering principle."
        ),
        "suggested_fix": "Use a dedicated logging module instead of sys.stdout.",
    }
    assert is_insufficient_structure_claim(
        finding_type=issue["finding_type"],
        target_source=source,
        target_path="smoke/werkzeug_debug_console.py",
        message=issue["message"],
        evidence=issue["evidence"],
        suggested_fix=issue["suggested_fix"],
    )
    assert (
        _issue_to_finding(
            "smoke/werkzeug_debug_console.py", issue, target_source=source
        )
        is None
    )


def test_stdlib_evidence_dropped_even_when_project_import_exists(
    tmp_path: Path,
) -> None:
    """Resolved project import does not rescue a finding that only cites stdlib."""
    (tmp_path / ".git").mkdir()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "store.py").write_text("STOCK = {}\n", encoding="utf-8")
    target = pkg / "handler.py"
    source = (
        "import sys\nfrom pkg.store import STOCK\n\n"
        "def run():\n    sys.stdout.write('x')\n"
    )
    target.write_text(source, encoding="utf-8")

    assert is_insufficient_structure_claim(
        finding_type="layering_violation",
        target_source=source,
        target_path=str(target),
        project_root=tmp_path,
        evidence="sys.stdout.write is a higher-level output dependency",
        message="handler should not talk to sys.stdout directly",
        suggested_fix="route output through a logging layer",
    )
    assert not is_insufficient_structure_claim(
        finding_type="layering_violation",
        target_source=source,
        target_path=str(target),
        project_root=tmp_path,
        evidence="handler imports pkg.store and mutates STOCK past the service",
        message="handler reaches into the store module",
        suggested_fix="call the service instead of pkg.store",
    )


def test_real_layering_with_service_boundary_survives_structure_filter() -> None:
    repo = Path(__file__).resolve().parents[1]
    path = repo / "benchmark/fixtures/architecture/checkout_handler.py"
    source = path.read_text(encoding="utf-8")
    assert not is_insufficient_structure_claim(
        finding_type="layering_violation",
        target_source=source,
        target_path=str(path),
        project_root=repo,
        evidence=(
            "checkout imports inventory_data_store and mutates STOCK, "
            "bypassing the inventory_service layer"
        ),
        message="Handler reaches into the data store past the service layer",
        suggested_fix="Call inventory_service.reserve_stock instead",
    )


def test_real_dependency_direction_survives_structure_filter() -> None:
    repo = Path(__file__).resolve().parents[1]
    path = repo / "benchmark/fixtures/architecture/low_level_persistence_client.py"
    source = path.read_text(encoding="utf-8")
    assert not is_insufficient_structure_claim(
        finding_type="dependency_direction",
        target_source=source,
        target_path=str(path),
        project_root=repo,
        evidence=(
            "The low-level persistence client imports and calls the "
            "high_level_order_workflow module"
        ),
        message="persistence should not depend upward on the workflow",
        suggested_fix="Have the workflow call the persistence client",
    )


def test_genuine_duplicated_logic_survives_security_filter() -> None:
    finding = _issue_to_finding(
        "app/worker.py",
        {
            "finding_type": "duplicated_logic",
            "confidence": 90,
            "location": "retry_once / retry_twice",
            "evidence": (
                "near-identical copy-pasted retry loop appears verbatim in "
                "worker.py and helper.py"
            ),
            "message": "Identical retry block duplicated across two modules",
            "suggested_fix": "Extract one shared retry helper; keep edits in sync",
        },
    )
    assert finding is not None
    assert finding.finding_type == "duplicated_logic"
    assert finding.confidence == 90


def test_empty_source_returns_clean_shape_without_llm_call(
    tmp_path: Path, monkeypatch
) -> None:
    empty_file = tmp_path / "empty.py"
    empty_file.write_text("   \n", encoding="utf-8")

    def _fail_chat(*args, **kwargs):
        raise AssertionError("chat() should not be called for empty source")

    monkeypatch.setattr("app.workers.architecture_worker.chat", _fail_chat)

    result = run_architecture_worker(str(empty_file))

    assert result["has_issues"] is False
    assert result["structured_findings"] == []
    assert result["context_files"] == []
    assert result["failures"] == 0


def _fake_response(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def test_run_architecture_worker_maps_mocked_llm_issues(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / ".git").mkdir()
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ui.py").write_text("def render():\n    return None\n", encoding="utf-8")
    target = pkg / "service.py"
    # Resolved project import + evidence that cites it.
    target.write_text(
        "from app.ui import render\n\ndef do_thing():\n    render()\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *args, **kwargs: _fake_response(
            '{"has_issues": true, "summary": "1 layering issue", '
            '"issues": [{"finding_type": "layering_violation", "confidence": 88, '
            '"location": "service.py:1", '
            '"evidence": "service imports app.ui directly", '
            '"message": "service layer depends on UI", '
            '"suggested_fix": "invert the dependency"}]}'
        ),
    )

    result = run_architecture_worker(str(target), project_root=str(tmp_path))

    assert result["has_issues"] is True
    assert result["failures"] == 0
    assert len(result["structured_findings"]) == 1
    finding = result["structured_findings"][0]
    assert finding.finding_type == "layering_violation"
    assert finding.detection_method == "llm_reasoning"
    assert finding.confidence == 88


def test_run_architecture_worker_drops_soft_smells_from_mocked_llm(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "service.py"
    target.write_text("def do_thing():\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *args, **kwargs: _fake_response(
            '{"has_issues": true, "summary": "2 soft smells", "issues": ['
            '{"finding_type": "duplicated_logic", "confidence": 90, '
            '"location": "a/b", "evidence": "functions look similar", '
            '"message": "could extract a shared helper", '
            '"suggested_fix": "merge them"},'
            '{"finding_type": "naming_convention", "confidence": 80, '
            '"location": "_FOO", "evidence": "leading underscore", '
            '"message": "conventionally rename to FOO", '
            '"suggested_fix": "remove leading underscore"}'
            "]}"
        ),
    )

    result = run_architecture_worker(str(target))

    assert result["has_issues"] is False
    assert result["structured_findings"] == []
    assert result["claim_unverified"] is True
    assert result["summary"] == CLAIM_UNVERIFIED_SUMMARY
    assert result["summary"] != "No architecture issues found."


def test_run_architecture_worker_treats_ungrounded_claim_as_unverified(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "service.py"
    target.write_text("def do_thing():\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *args, **kwargs: _fake_response(
            '{"has_issues": true, "summary": "vague", "issues": []}'
        ),
    )

    result = run_architecture_worker(str(target))

    assert result["has_issues"] is False
    assert result["structured_findings"] == []
    assert result["claim_unverified"] is True
    assert result["summary"] == CLAIM_UNVERIFIED_SUMMARY


def test_genuine_clean_differs_from_claim_unverified(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "service.py"
    target.write_text("def do_thing():\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *args, **kwargs: _fake_response(
            '{"has_issues": false, "summary": "No architecture issues found.", '
            '"issues": []}'
        ),
    )

    clean = run_architecture_worker(str(target))
    assert clean["claim_unverified"] is False
    assert clean["summary"] == "No architecture issues found."

    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *args, **kwargs: _fake_response(
            '{"has_issues": true, "summary": "layering violation in checkout", '
            '"issues": [{'
            '"finding_type": "layering_violation", '
            '"evidence": "mutates the data store directly", '
            '"confidence": 90, '
            '"suggested_fix": "use the service layer"'
            "}]}"
        ),
    )
    unverified = run_architecture_worker(str(target))
    assert unverified["claim_unverified"] is True
    assert unverified["structured_findings"] == []
    assert unverified["summary"] != clean["summary"]
    assert unverified["summary"] == CLAIM_UNVERIFIED_SUMMARY


def test_filtered_claim_does_not_leak_into_user_facing_summary(
    tmp_path: Path, monkeypatch
) -> None:
    """Authz-bleed (etc.) drops the finding; panel body must not echo the claim."""
    target = tmp_path / "notes.py"
    target.write_text(
        "def get_note(note_id, user_id):\n    return NOTES[note_id]\n",
        encoding="utf-8",
    )
    events: list[str] = []

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *args, **kwargs: _fake_response(
            '{"has_issues": true, '
            '"summary": "The target file has a layering violation due to a '
            'missing ownership check", '
            '"issues": [{'
            '"finding_type": "layering_violation", '
            '"evidence": "get_note lacks an ownership check before returning NOTES", '
            '"confidence": 90, '
            '"suggested_fix": "Verify the note belongs to the caller."'
            "}]}"
        ),
    )
    monkeypatch.setattr(
        "app.workers.architecture_worker.log_agent_event",
        lambda message, **kwargs: events.append(message),
    )

    result = run_architecture_worker(str(target))

    assert result["has_issues"] is False
    assert result["structured_findings"] == []
    assert result["claim_unverified"] is True
    assert result["summary"] == CLAIM_UNVERIFIED_SUMMARY
    assert "ownership" not in result["summary"].lower()
    assert any("missing ownership check" in event for event in events)
    assert any("none met the evidence bar" in event for event in events)

def test_run_architecture_worker_treats_llm_error_as_clean(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "service.py"
    target.write_text("def do_thing():\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: [],
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("app.workers.architecture_worker.chat", _raise)

    result = run_architecture_worker(str(target))

    assert result["has_issues"] is False
    assert result["failures"] == 1
    assert result["structured_findings"] == []


_CHECKOUT = "benchmark/fixtures/architecture/checkout_handler.py"
_PERSISTENCE = "benchmark/fixtures/architecture/low_level_persistence_client.py"
_REPO = Path(__file__).resolve().parents[1]
_CHECKOUT_SOURCE = Path(_CHECKOUT).read_text(encoding="utf-8")
_PERSISTENCE_SOURCE = Path(_PERSISTENCE).read_text(encoding="utf-8")
_ARCH_CONTEXT = [
    ContextFile(path=_CHECKOUT, relation="same_package", content=_CHECKOUT_SOURCE),
    ContextFile(path=_PERSISTENCE, relation="same_package", content=_PERSISTENCE_SOURCE),
    ContextFile(
        path="benchmark/fixtures/architecture/inventory_data_store.py",
        relation="imported_by_target",
        content="STOCK = {}",
    ),
    ContextFile(
        path="benchmark/fixtures/architecture/high_level_order_workflow.py",
        relation="imported_by_target",
        content="def finalize_order(order_id: str) -> None:\n    pass\n",
    ),
]


def test_sibling_dependency_finding_dropped_when_reviewing_checkout() -> None:
    """§30 leak: reviewing checkout must not keep the persistence client's bug."""
    finding = _issue_to_finding(
        _CHECKOUT,
        {
            "finding_type": "dependency_direction",
            "confidence": 100,
            "location": "low_level_persistence_client.py: write_record function",
            "evidence": (
                "benchmark/fixtures/architecture/low_level_persistence_client.py: "
                "write_record function\nThe low_level_persistence_client imports "
                "and calls the high_level_order_workflow.finalize_order function."
            ),
            "message": (
                "The low-level persistence client should not depend on the "
                "high-level order workflow"
            ),
            "suggested_fix": "Remove the import of finalize_order",
        },
        context_files=_ARCH_CONTEXT,
        target_source=_CHECKOUT_SOURCE,
        project_root=_REPO,
    )
    assert finding is None
    assert is_off_target_finding(
        _CHECKOUT,
        location="low_level_persistence_client.py: write_record",
        evidence="low_level_persistence_client imports high_level_order_workflow",
        message="dependency direction is wrong in the persistence client",
        context_files=_ARCH_CONTEXT,
        target_source=_CHECKOUT_SOURCE,
    )


def test_sibling_layering_finding_dropped_when_reviewing_persistence() -> None:
    """§30 leak: reviewing persistence must not keep checkout's layering bug."""
    finding = _issue_to_finding(
        _PERSISTENCE,
        {
            "finding_type": "layering_violation",
            "confidence": 100,
            "location": "checkout_handler.py:checkout",
            "evidence": (
                "checkout_handler.py:checkout\nThe checkout handler imports the "
                "data-store module directly and mutates the raw dict itself."
            ),
            "message": (
                "The checkout handler should go through the inventory service layer"
            ),
            "suggested_fix": "Call reserve_stock instead of mutating STOCK",
        },
        context_files=_ARCH_CONTEXT,
        target_source=_PERSISTENCE_SOURCE,
        project_root=_REPO,
    )
    assert finding is None


def test_checkout_keeps_its_own_layering_finding() -> None:
    finding = _issue_to_finding(
        _CHECKOUT,
        {
            "finding_type": "layering_violation",
            "confidence": 100,
            "location": "checkout_handler.py: checkout function",
            "evidence": (
                "The checkout function directly imports and mutates the "
                "inventory_data_store, bypassing the inventory_service layer."
            ),
            "message": (
                "The checkout handler should go through "
                "inventory_service.reserve_stock for any stock change"
            ),
            "suggested_fix": "Replace direct STOCK mutation with reserve_stock",
        },
        context_files=_ARCH_CONTEXT,
        target_source=_CHECKOUT_SOURCE,
        project_root=_REPO,
    )
    assert finding is not None
    assert finding.finding_type == "layering_violation"
    assert finding.confidence == 100


def test_persistence_keeps_its_own_dependency_finding() -> None:
    finding = _issue_to_finding(
        _PERSISTENCE,
        {
            "finding_type": "dependency_direction",
            "confidence": 100,
            "location": "low_level_persistence_client.py:write_record",
            "evidence": (
                "The low-level persistence client imports and calls back into "
                "high_level_order_workflow.finalize_order after every write."
            ),
            "message": (
                "The low-level persistence client should not depend on the "
                "high-level order workflow"
            ),
            "suggested_fix": "Have the workflow call the client, not the reverse",
        },
        context_files=_ARCH_CONTEXT,
        target_source=_PERSISTENCE_SOURCE,
        project_root=_REPO,
    )
    assert finding is not None
    assert finding.finding_type == "dependency_direction"
    assert finding.confidence == 100


def test_symbol_only_own_finding_survives_via_content_match() -> None:
    """Own finding can cite a target symbol without repeating the file path."""
    finding = _issue_to_finding(
        _CHECKOUT,
        {
            "finding_type": "layering_violation",
            "confidence": 95,
            "location": "checkout",
            "evidence": "checkout mutates STOCK from inventory_data_store directly",
            "message": "Bypasses inventory_service.reserve_stock",
            "suggested_fix": "Call reserve_stock from checkout",
        },
        context_files=_ARCH_CONTEXT,
        target_source=_CHECKOUT_SOURCE,
        project_root=_REPO,
    )
    assert finding is not None
    assert finding.finding_type == "layering_violation"


def test_mocked_llm_filters_sibling_leak_keeps_own_finding(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end filter: mixed LLM payload → only the target's finding remains."""
    repo = Path(__file__).resolve().parents[1]
    target = repo / _CHECKOUT

    monkeypatch.setattr(
        "app.workers.architecture_worker.gather_cross_file_context",
        lambda *args, **kwargs: list(_ARCH_CONTEXT),
    )
    monkeypatch.setattr(
        "app.workers.architecture_worker.chat",
        lambda *args, **kwargs: _fake_response(
            "{"
            '"has_issues": true, '
            '"summary": "layering and dependency issues", '
            '"issues": ['
            "{"
            '"finding_type": "layering_violation", "confidence": 100, '
            '"location": "checkout_handler.py:checkout", '
            '"evidence": "checkout_handler mutates inventory_data_store.STOCK", '
            '"message": "checkout bypasses inventory_service", '
            '"suggested_fix": "use reserve_stock"'
            "}, "
            "{"
            '"finding_type": "dependency_direction", "confidence": 100, '
            '"location": "low_level_persistence_client.py:write_record", '
            '"evidence": "low_level_persistence_client imports finalize_order", '
            '"message": "persistence depends upward on workflow", '
            '"suggested_fix": "remove finalize_order import"'
            "}"
            "]}"
        ),
    )

    result = run_architecture_worker(str(target))
    types = [f.finding_type for f in result["structured_findings"]]
    assert types == ["layering_violation"]
    assert result["has_issues"] is True
