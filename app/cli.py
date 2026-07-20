"""Command-line interface for secondpass."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from app.agent import review_code
from app.memory import search_memory, seed_memory
from app.scanner import ScanError
from app.websearch import search_web

app = typer.Typer(
    name="secondpass",
    help="Run personal security reviews from the command line.",
    no_args_is_help=True,
)
console = Console()


@app.callback()
def main() -> None:
    """Personal security review agent."""


def _render_scan_detail(finding: dict[str, Any]) -> Panel:
    scan = finding.get("finding") or {}
    body = Text()
    body.append("Rule: ", style="bold")
    body.append(f"{scan.get('rule_id', '')}\n")
    body.append("Severity: ", style="bold")
    body.append(f"{scan.get('severity', '')}\n")
    body.append("Location: ", style="bold")
    body.append(f"{scan.get('path', '')}:{scan.get('line', '')}\n\n")
    body.append("Message\n", style="bold cyan")
    body.append(f"{scan.get('message', '')}\n\n")
    body.append("Snippet\n", style="bold cyan")
    body.append(str(scan.get("snippet", "") or "(none)"))
    return Panel(body, title="Scan detail", border_style="cyan", padding=(1, 2))


def _render_memory(finding: dict[str, Any]) -> Panel:
    match = finding.get("memory_match")
    if not match:
        return Panel(
            Text("No memory lesson matched.", style="dim"),
            title="Matched memory lesson",
            border_style="magenta",
            padding=(1, 2),
        )

    confidence = match.get("confidence")
    distance = match.get("distance")
    confidence_text = (
        f"{confidence:.4f}" if isinstance(confidence, (int, float)) else "n/a"
    )
    distance_text = f"{distance:.4f}" if isinstance(distance, (int, float)) else "n/a"

    body = Text()
    body.append("ID: ", style="bold")
    body.append(f"{match.get('id', '')}\n")
    body.append("Type: ", style="bold")
    body.append(f"{match.get('type', '')}\n")
    body.append("Confidence: ", style="bold")
    body.append(f"{confidence_text}")
    body.append(f"  (distance={distance_text})\n\n")
    body.append("Pattern\n", style="bold magenta")
    body.append(f"{match.get('pattern', '')}\n\n")
    body.append("Remembered fix\n", style="bold magenta")
    body.append(f"{match.get('fix', '')}\n\n")
    body.append("Source: ", style="bold")
    body.append(str(match.get("source", "") or ""))
    return Panel(
        body,
        title="Matched memory lesson",
        border_style="magenta",
        padding=(1, 2),
    )


def _render_web_context(finding: dict[str, Any]) -> Panel:
    results = finding.get("web_context") or []
    if not results:
        return Panel(
            Text("No web context used.", style="dim"),
            title="Web context",
            border_style="blue",
            padding=(1, 2),
        )

    blocks: list[Any] = []
    for index, hit in enumerate(results, start=1):
        block = Text()
        block.append(f"{index}. ", style="bold")
        block.append(f"{hit.get('title', '')}\n", style="bold")
        block.append(f"{hit.get('url', '')}\n", style="blue underline")
        block.append(str(hit.get("snippet", "") or ""))
        blocks.append(block)
        if index < len(results):
            blocks.append(Text(""))
    return Panel(
        Group(*blocks),
        title="Web context",
        border_style="blue",
        padding=(1, 2),
    )


def _render_llm_analysis(finding: dict[str, Any]) -> Panel:
    explanation = str(finding.get("explanation") or "").strip() or "(none)"
    suggested_fix = str(finding.get("suggested_fix") or "").strip() or "(none)"
    body = Text()
    body.append("Explanation\n", style="bold green")
    body.append(f"{explanation}\n\n")
    body.append("Suggested fix\n", style="bold green")
    body.append(suggested_fix)
    return Panel(
        body,
        title="LLM analysis",
        border_style="green",
        padding=(1, 2),
    )


def _display_report(report: dict[str, Any]) -> None:
    console.print()
    console.print(
        Panel.fit(
            Text.from_markup(
                f"[bold]secondpass review[/bold]\n"
                f"Path: {report.get('path', '')}\n"
                f"Provider: {report.get('provider', 'unknown')}"
                + (
                    f"  Model: {report['model']}"
                    if report.get("model")
                    else ""
                )
                + f"\nFindings reviewed: {report.get('finding_count', 0)}"
                + (
                    "\n[dim]Static scan empty — used logic-review fallback[/dim]"
                    if report.get("static_scan_empty")
                    else ""
                )
            ),
            border_style="white",
        )
    )

    findings = report.get("findings") or []
    if not findings:
        console.print("\n[bold yellow]No findings to review.[/bold yellow]")
        return

    for index, item in enumerate(findings, start=1):
        scan = item.get("finding") or {}
        console.print()
        console.print(
            Rule(
                f"Finding {index}/{len(findings)} — "
                f"{scan.get('rule_id', 'unknown')} "
                f"({scan.get('severity', 'n/a')})"
            )
        )
        console.print(_render_scan_detail(item))
        console.print(_render_memory(item))
        console.print(_render_web_context(item))
        console.print(_render_llm_analysis(item))

    failures = int(report.get("tool_call_failures") or 0)
    if failures:
        console.print(
            f"\n[yellow]Note:[/yellow] {failures} tool-call formatting "
            "failure(s) were retried during this run."
        )


@app.command()
def review(
    path: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        resolve_path=True,
        help="File or directory to review.",
    ),
) -> None:
    """Run the full secondpass agent review and display a structured report."""
    console.print(
        f"[bold]Starting review of[/bold] {path}\n"
        "[dim]Tool calls will stream below as the agent works...[/dim]\n"
    )
    try:
        report = review_code(str(path))
    except (ScanError, ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001 — surface unexpected agent failures cleanly
        console.print(f"[bold red]Review failed:[/bold red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    _display_report(report)


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


@app.command("search-web")
def search_web_cmd(
    query: str = typer.Argument(..., help="Web search query."),
    max_results: int = typer.Option(
        3,
        "--max-results",
        "-n",
        help="Maximum number of results to return.",
    ),
) -> None:
    """Search the web with Tavily and print normalized results."""
    try:
        results = search_web(query, max_results=max_results)
    except RuntimeError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    if not results:
        console.print("No web results found.")
        raise typer.Exit(code=1)

    table = Table(title=f'Web results for "{query}"')
    table.add_column("Title", overflow="fold")
    table.add_column("URL", overflow="fold")
    table.add_column("Snippet", overflow="fold")

    for result in results:
        table.add_row(result["title"], result["url"], result["snippet"])

    console.print(table)


if __name__ == "__main__":
    app()
