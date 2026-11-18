"""Architecture evidence must identify the target, even with repeated filenames."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import review_architecture
from app.context import ContextFile
from app.workers.architecture_worker import is_off_target_finding


@pytest.mark.parametrize("include_related", [False, True])
@pytest.mark.parametrize("citation,on_target", [
    ("storage/config.py:1", False),
    ("storage\\config.py:1", False),
    ("./storage/config.py:1", False),
    ("absolute-related", False),
    ("unknown/config.py:1", False),
    ("services/config.py:3", True),
    ("services\\config.py:3", True),
    ("./services/config.py:3", True),
    ("absolute-target", True),
    ("symbol-grounded", True),
])
def test_architecture_attributes_same_named_files_using_qualified_evidence(
    tmp_path: Path, monkeypatch, citation: str, on_target: bool,
    include_related: bool,
) -> None:
    target = tmp_path / "services" / "config.py"
    related = tmp_path / "storage" / "config.py"
    for directory in (target.parent, related.parent):
        directory.mkdir()
        (directory / "__init__.py").write_text("", encoding="utf-8")
    target.write_text(
        "from storage.config import imported_value\n\ndef SelectedValue():\n    return imported_value()\n",
        encoding="utf-8",
    )
    if include_related:
        related.write_text("def BadName():\n    return 1\n\ndef imported_value():\n    return BadName()\n", encoding="utf-8")
    location = {
        "absolute-related": f"{related}:1",
        "absolute-target": f"{target}:3",
        "symbol-grounded": "storage/config.py:1",
    }.get(citation, citation)
    symbol = "SelectedValue" if citation == "symbol-grounded" else "BadName"
    content = json.dumps({
        "has_issues": True, "summary": "A naming discrepancy was found.",
        "issues": [{
            "finding_type": "naming_convention", "confidence": 90,
            "location": location,
            "evidence": f"{symbol} contradicts the visible naming pattern.",
            "message": f"{symbol} uses a different naming pattern from nearby functions.",
            "suggested_fix": f"Rename {symbol} consistently with its neighbors.",
        }],
    })

    class ScriptedClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "offline-test-key")
    monkeypatch.setattr("app.llm.OpenAI", ScriptedClient)
    monkeypatch.setattr("app.hooks._DEFAULT_LOG_PATH", tmp_path / "hooks.jsonl")

    result = review_architecture(str(target), project_root=str(tmp_path))

    assert any(Path(item["path"]).name == "config.py" for item in result["context_files"]) is include_related
    assert result["finding_count"] == (1 if on_target else 0)
    assert result["claim_unverified"] is (not on_target)
    assert result["no_issues"] is False
    assert result["review_result"]["coverage_status"] == "ok"


def test_target_context_entry_does_not_turn_target_evidence_off_target(tmp_path: Path) -> None:
    target = tmp_path / "config.py"
    target.write_text("value = 1\n", encoding="utf-8")

    assert is_off_target_finding(
        str(target), location=f"{target}:1", evidence="This assignment contradicts the visible pattern.",
        target_source="value = 1\n",
        context_files=[ContextFile(path=str(target), relation="import", content="value = 1\n")],
    ) is False
