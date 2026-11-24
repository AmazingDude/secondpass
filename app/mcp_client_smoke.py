"""Minimal MCP client that calls secondpass review_code over stdio.

Usage (from project root, venv active):

    python -m app.mcp_client_smoke path/to/file.py
    python -m app.mcp_client_smoke --diff
    python -m app.mcp_client_smoke path/to/file.py --mode static --rules project-rules.yml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _run(arguments: dict[str, object]) -> int:
    root = Path(__file__).resolve().parent.parent
    static = arguments.get("mode") == "static"
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=str(Path.cwd() if static else root),
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print("tools:", names)

            print(f"calling review_code({arguments!r}) ...")
            result = await session.call_tool("review_code", arguments)
            static_completed = False

            for block in result.content:
                text = getattr(block, "text", None)
                if text is None:
                    print(block)
                    continue
                try:
                    parsed = json.loads(text)
                    if static and isinstance(parsed, dict):
                        static_completed = parsed.get("coverage_status") == "ok"
                    print(json.dumps(parsed, indent=2, ensure_ascii=False)[:8000])
                    if len(json.dumps(parsed)) > 8000:
                        print("\n... (truncated)")
                except json.JSONDecodeError:
                    print(text[:8000])

            if result.isError or (static and not static_completed):
                return 1
            return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test secondpass MCP review_code")
    parser.add_argument("path", nargs="?", help="File or directory to review")
    parser.add_argument("--mode", choices=["assisted", "static"], default="assisted")
    parser.add_argument("--rules", help="Local Semgrep rules file for static mode")
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Review git staged/unstaged changes instead of a path",
    )
    args = parser.parse_args()
    if args.diff and args.path:
        parser.error("Use either a path or --diff, not both")
    if not args.diff and not args.path:
        parser.error("Provide a path or pass --diff")
    if args.mode == "static" and args.diff:
        parser.error("Static mode requires a single Python file")
    if args.rules is not None and args.mode != "static":
        parser.error("--rules requires --mode static")

    arguments = {"diff": True} if args.diff else {"path": args.path}
    if args.mode == "static":
        arguments["mode"] = "static"
    if args.rules is not None:
        arguments["rules_path"] = args.rules
    raise SystemExit(asyncio.run(_run(arguments)))


if __name__ == "__main__":
    main()
