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
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load .env before tool calls (keys for Groq/Tavily). Keep imports light so
# Cursor's MCP handshake completes before chromadb/semgrep/openai load.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

mcp = FastMCP(
    "secondpass",
    instructions=(
        "Personal security review agent. Call review_code on a file/directory "
        "path, or set diff=true to review only git-changed files in the cwd repo. "
        "Reviews can take 30–120s (Semgrep + LLM); wait for the JSON report."
    ),
)


def _json_report(report: dict[str, Any]) -> str:
    """Serialize the agent report for MCP clients (text content)."""
    return json.dumps(report, indent=2, default=str, ensure_ascii=False)


@mcp.tool(name="review_code")
def review_code_tool(
    path: str | None = None,
    diff: bool = False,
) -> str:
    """Run secondpass security review and return a structured JSON report.

    Use this when you need a personal security pass over code: Semgrep scan,
    memory of past lessons, optional web context, and an LLM explanation/fix.

    Modes (mutually exclusive):
    - path mode: pass `path` to a file or directory (absolute or relative).
    - diff mode: set `diff=true` (omit path) to review the current git repo's
      staged changes, or unstaged if nothing is staged. Whole files are scanned;
      only findings on changed lines are reported.

    Do not pass both a path and diff=true.

    This call is slow (often 30–120 seconds). Prefer a single file path for tests.

    Returns JSON with finding_count, findings[] (scan detail, memory_match,
    web_context, explanation, suggested_fix), and metadata (provider, etc.).
    When the review concludes cleanly, finding_count is 0, findings is [],
    no_issues is true, and message explains that no issues were found.
    """
    # Lazy-import heavy deps so server startup / list_tools stays fast.
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
            )
        else:
            target = Path(path).expanduser()
            if not target.exists():
                raise FileNotFoundError(f"Path does not exist: {target}")
            report = review_code(str(target.resolve()))
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
