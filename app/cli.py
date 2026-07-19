"""Command-line interface for secondpass."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

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


if __name__ == "__main__":
    app()
