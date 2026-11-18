"""MCP server exposing secondpass security review as a single tool.

IMPORTANT for Cursor: do NOT run this manually in a terminal while Cursor MCP
is enabled — Cursor spawns its own stdio process. A second instance can look
“stuck” and get disabled.

Run only for debugging / Inspector:

    python -m app.mcp_server
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings


def _create_server() -> FastMCP:
    # FastMCP v1 reads a cwd .env even for stdio discovery. Its constructor
    # does not expose pydantic-settings' env_file option.
    previous = Settings.model_config["env_file"]
    try:
        Settings.model_config["env_file"] = None
        return FastMCP("secondpass", instructions=_INSTRUCTIONS)
    finally:
        Settings.model_config["env_file"] = previous


_INSTRUCTIONS = (
    "Personal security review agent. Call review_code on a file/directory "
    "path, or set diff=true to review only git-changed files in the cwd repo. "
    "For a single Python file without a model key, use mode=static, optionally "
    "with rules_path pointing to a local Semgrep file. Assisted reviews can "
    "take 30–120s (Semgrep + LLM); wait for the JSON report."
)
mcp = _create_server()


def _json_report(report: dict[str, Any]) -> str:
    """Serialize the agent report for MCP clients (text content)."""
    return json.dumps(report, indent=2, default=str, ensure_ascii=False)


@mcp.tool(name="review_code")
def review_code_tool(
    path: str | None = None,
    diff: bool = False,
    memory_enabled: bool = True,
    mode: Literal["assisted", "static"] = "assisted",
    rules_path: str | None = None,
) -> str:
    """Run secondpass security review and return a structured JSON report.

    Use this when you need a personal security pass over code: Semgrep scan,
    optional memory of past lessons, optional web context, and an LLM explanation/fix.
    Set memory_enabled=false to skip lesson indexing and retrieval.

    Use mode="static" for one Python file without provider credentials, dotenv,
    lesson memory or web access. An optional rules_path selects a local Semgrep
    file instead of bundled rules. Static mode ignores memory_enabled, rejects
    diff, and returns raw rule matches with coverage_status/inconclusive for the
    selected scan, analysis_scope and persisted=false. No no_issues or confidence
    gate is inferred; zero matches only means the selected rules did not match.
    Static results are not saved to history. Assisted mode remains the default;
    rules_path is only supported in static mode.

    Modes (mutually exclusive):
    - path mode: pass `path` to a file or directory (absolute or relative).
    - diff mode: set `diff=true` (omit path) to review the current git repo's
      staged changes, or unstaged if nothing is staged. Whole files are scanned;
      only findings on changed lines are reported.

    Do not pass both a path and diff=true.

    This call is slow (often 30–120 seconds). Prefer a single file path for tests.

    Assisted mode returns JSON with ReviewResult + GateResult, accepted[] and needs_review[]
    enriched with scan detail, memory/web context, explanation and suggested fix,
    plus provider metadata. ``findings`` remains an alias for accepted findings.
    A clean review has finding_count=0, empty buckets, and no_issues=true.
    """
    if mode == "static":
        try:
            if diff or not path:
                raise ValueError("Static mode requires a file path without diff.")
            from app.static_review import review_static_file

            return _json_report(review_static_file(path, rules_path=rules_path))
        except Exception:  # noqa: BLE001 — keep errors safe and the MCP process alive
            return _json_report({
                "mode": "static", "coverage_status": "inconclusive", "inconclusive": True,
                "finding_count": 0, "findings": [],
                "analysis_scope": "single_python_file_static_patterns", "persisted": False,
                "error": "Static review could not run. Check the Python file, local rules and Semgrep installation.",
            })
    if mode != "assisted" or rules_path is not None:
        return _json_report({
            "error": "Use mode=assisted or mode=static; rules_path requires mode=static.",
            "coverage_status": "inconclusive", "inconclusive": True,
            "finding_count": 0, "findings": [],
        })

    # Assisted-only setup keeps discovery and static calls free of provider keys.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    from app.agent import review_changed_files, review_code
    from app.gitdiff import GitDiffError, collect_diff_selection
    from app.scanner import ScanError

    if diff and path:
        return _json_report(
            {
                "error": (
                    "Pass either path=... or diff=true, not both. "
                    "path reviews a specific file/dir; diff reviews git changes."
                ),
                "finding_count": 0,
                "findings": [],
            }
        )
    if not diff and not path:
        return _json_report(
            {
                "error": (
                    "Provide path to a file/directory, or set diff=true "
                    "for git changes."
                ),
                "finding_count": 0,
                "findings": [],
            }
        )

    try:
        if diff:
            selection = collect_diff_selection()
            if not selection.files:
                return _json_report(
                    {
                        "path": f"git diff ({selection.mode})",
                        "diff_mode": selection.mode,
                        "changed_files": [],
                        "finding_count": 0,
                        "findings": [],
                        "message": (
                            f"No {selection.mode} changes to review. "
                            "Stage files (git add) or edit something first."
                        ),
                    }
                )
            report = review_changed_files(
                selection.files,
                mode=selection.mode,
                memory_enabled=memory_enabled,
            )
        else:
            target = Path(path).expanduser()
            if not target.exists():
                raise FileNotFoundError(f"Path does not exist: {target}")
            report = review_code(str(target.resolve()), memory_enabled=memory_enabled)
    except (ScanError, GitDiffError, FileNotFoundError, ValueError) as exc:
        return _json_report({"error": str(exc), "finding_count": 0, "findings": []})
    except Exception as exc:  # noqa: BLE001 — keep MCP process alive on unexpected errors
        return _json_report(
            {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
                "finding_count": 0,
                "findings": [],
            }
        )

    return _json_report(report)


def main() -> None:
    """Start the MCP server on stdio (default transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
