"""Minimal MCP client that calls secondpass review_code over stdio.

Usage (from project root, venv active):

    python -m app.mcp_client_smoke path/to/file.py
    python -m app.mcp_client_smoke --diff
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _run(path: str | None, diff: bool) -> int:
    root = Path(__file__).resolve().parent.parent
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=str(root),
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print("tools:", names)

            arguments: dict[str, object] = {}
            if diff:
                arguments["diff"] = True
            else:
                arguments["path"] = path

            print(f"calling review_code({arguments!r}) ...")
            result = await session.call_tool("review_code", arguments)

            for block in result.content:
                text = getattr(block, "text", None)
                if text is None:
                    print(block)
                    continue
                try:
                    parsed = json.loads(text)
                    print(json.dumps(parsed, indent=2, ensure_ascii=False)[:8000])
                    if len(json.dumps(parsed)) > 8000:
                        print("\n... (truncated)")
                except json.JSONDecodeError:
                    print(text[:8000])

            if result.isError:
                return 1
            return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test secondpass MCP review_code")
    parser.add_argument("path", nargs="?", help="File or directory to review")
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

    raise SystemExit(asyncio.run(_run(args.path, args.diff)))


if __name__ == "__main__":
    main()
