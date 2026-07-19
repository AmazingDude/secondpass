"""Command-line interface for secondpass."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.memory import search_memory, seed_memory
from app.scanner import ScanError, run_static_scan

app = typer.Typer(
    name="secondpass",
    help="Run personal security reviews from the command line.",
    no_args_is_help=True,
)
console = Console()


@app.callback()
def main() -> None:
    """Personal security review agent."""


@app.command()
def review(
    path: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        resolve_path=True,
        help="File or directory to scan.",
    ),
) -> None:
    """Run a static security scan and display normalized findings."""
    try:
        findings = run_static_scan([str(path)])
    except (ScanError, ValueError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    table = Table(title="Static scan findings")
    table.add_column("Rule ID", overflow="fold")
    table.add_column("Severity")
    table.add_column("Path", overflow="fold")
    table.add_column("Line", justify="right")
    table.add_column("Message", overflow="fold")
    table.add_column("Snippet", overflow="fold")

    for finding in findings:
        table.add_row(
            finding["rule_id"],
            finding["severity"],
            finding["path"],
            str(finding["line"]),
            finding["message"],
            finding["snippet"],
        )

    if not findings:
        table.caption = "No findings."

    console.print(table)


@app.command("search-memory")
def search_memory_cmd(
    query: str = typer.Argument(..., help="Natural-language query to search lessons."),
    n_results: int = typer.Option(3, "--n-results", "-n", help="Max lessons to return."),
) -> None:
    """Search the persistent security lesson memory."""
    seeded = seed_memory()
    if seeded:
        console.print(f"Seeded {seeded} lesson(s) into memory.")

    matches = search_memory(query, n_results=n_results)
    if not matches:
        console.print("No lessons found. Seed memory first or add findings.")
        raise typer.Exit(code=1)

    table = Table(title=f'Memory matches for "{query}"')
    table.add_column("ID")
    table.add_column("Type", overflow="fold")
    table.add_column("Pattern", overflow="fold")
    table.add_column("Fix", overflow="fold")
    table.add_column("Source", overflow="fold")
    table.add_column("Distance", justify="right")

    for match in matches:
        distance = match.get("distance")
        table.add_row(
            str(match.get("id", "")),
            str(match.get("type", "")),
            str(match.get("pattern", "")),
            str(match.get("fix", "")),
            str(match.get("source", "")),
            f"{distance:.4f}" if isinstance(distance, (int, float)) else "",
        )

    console.print(table)


if __name__ == "__main__":
    app()
