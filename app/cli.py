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

from app.agent import review_changed_files
from app.gitdiff import GitDiffError, collect_diff_selection
from app.memory import search_memory, seed_memory
from app.progress import ReviewProgress
from app.scanner import ScanError
from app.supervisor import supervise_review
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
    structured = finding.get("structured_finding") or {}
    body = Text()
    body.append("Type: ", style="bold")
    body.append(f"{structured.get('finding_type', scan.get('rule_id', ''))}\n")
    body.append("Detection: ", style="bold")
    body.append(f"{structured.get('detection_method', 'unknown')}\n")
    body.append("Confidence: ", style="bold")
    body.append(f"{structured.get('confidence', 'n/a')}%\n")
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
    structured = finding.get("structured_finding") or {}
    explanation = str(finding.get("explanation") or "").strip() or "(none)"
    suggested_fix = (
        str(
            structured.get("suggested_fix")
            or finding.get("suggested_fix")
            or ""
        ).strip()
        or "(none)"
    )
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
    header = Text()
    header.append("secondpass review\n", style="bold")
    header.append(f"Path: {report.get('path', '')}\n")
    header.append(f"Provider: {report.get('provider', 'unknown')}")
    if report.get("model"):
        header.append(f"  Model: {report['model']}")
    header.append(f"\nFindings reviewed: {report.get('finding_count', 0)}")
    header.append(
        f"\nAccepted: {report.get('accepted_count', 0)}",
        style="green",
    )
    header.append(
        f"  Needs your review: {report.get('needs_review_count', 0)}",
        style="yellow",
    )
    if report.get("gate_threshold") is not None:
        header.append(f"  Gate: ≥{report['gate_threshold']}%", style="dim")
    if report.get("diff_mode"):
        header.append(f"\nDiff mode: {report['diff_mode']}")
        changed = report.get("changed_files") or []
        header.append(f"\nChanged files: {len(changed)}")
        filtered_out = int(report.get("filtered_out_findings") or 0)
        if filtered_out:
            header.append(
                f"\nFiltered out (outside changed lines): {filtered_out}",
                style="dim",
            )
    if report.get("static_scan_error"):
        err = str(report["static_scan_error"])
        header.append(
            f"\n{err}" if err.lower().startswith("semgrep") else f"\nStatic scan error (continued): {err}",
            style="yellow",
        )
    elif report.get("used_logic_fallback") and report.get("no_issues"):
        header.append(
            "\nStatic scan empty — logic review found no issues",
            style="green",
        )
    elif report.get("used_logic_fallback"):
        header.append(
            "\nStatic scan empty — used logic-review fallback",
            style="dim",
        )
    elif report.get("static_scan_empty"):
        header.append("\nStatic scan reported no issues", style="dim")

    console.print(Panel.fit(header, border_style="white"))

    accepted = report.get("accepted")
    if accepted is None:
        accepted = report.get("findings") or []
    needs_review = report.get("needs_review") or []
    if not accepted and not needs_review:
        console.print()
        if report.get("diff_mode"):
            filtered_out = int(report.get("filtered_out_findings") or 0)
            detail = str(report.get("message") or "").strip()
            if not detail:
                detail = (
                    "No findings on the changed lines."
                    if filtered_out
                    else "No reviewable findings in the selected diff."
                )
            if filtered_out and "outside" not in detail.lower():
                detail += f"\n({filtered_out} finding(s) were outside the changed line ranges.)"
            console.print(
                Panel(
                    Text(detail, style="green"),
                    title="No issues found",
                    border_style="green",
                    padding=(1, 2),
                )
            )
        else:
            detail = str(report.get("message") or "").strip()
            if not detail:
                if report.get("used_logic_fallback"):
                    detail = "No security issues found."
                else:
                    detail = (
                        "Nothing to review.\n"
                        "Semgrep reported no issues and there was no source "
                        "content for a logic fallback."
                    )
            console.print(
                Panel(
                    Text(detail, style="green"),
                    title="No issues found",
                    border_style="green",
                    padding=(1, 2),
                )
            )
        return

    _display_finding_bucket(
        accepted,
        label="Accepted finding",
        rule_style="green",
    )
    _display_finding_bucket(
        needs_review,
        label="Needs your review",
        rule_style="yellow",
    )

    failures = int(report.get("tool_call_failures") or 0)
    if failures:
        console.print(
            f"\n[yellow]Note:[/yellow] {failures} tool-call formatting "
            "failure(s) were retried during this run."
        )


def _display_finding_bucket(
    findings: list[dict[str, Any]],
    *,
    label: str,
    rule_style: str,
) -> None:
    for index, item in enumerate(findings, start=1):
        scan = item.get("finding") or {}
        structured = item.get("structured_finding") or {}
        confidence = structured.get("confidence", "n/a")
        console.print()
        console.print(
            Rule(
                f"{label} {index}/{len(findings)} — "
                f"{structured.get('finding_type', scan.get('rule_id', 'unknown'))} "
                f"({confidence}% confidence)",
                style=rule_style,
            )
        )
        console.print(_render_scan_detail(item))
        console.print(_render_memory(item))
        console.print(_render_web_context(item))
        console.print(_render_llm_analysis(item))
        if item.get("saved_lesson_id"):
            console.print(
                f"[green]Saved new lesson:[/green] {item['saved_lesson_id']}"
            )


def _render_architecture_finding(item: dict[str, Any]) -> Panel:
    structured = item.get("structured_finding") or {}
    body = Text()
    body.append("Type: ", style="bold")
    body.append(f"{structured.get('finding_type', '')}\n")
    body.append("Confidence: ", style="bold")
    body.append(f"{structured.get('confidence', 'n/a')}%\n\n")
    body.append("Evidence\n", style="bold cyan")
    body.append(f"{structured.get('evidence', '') or '(none)'}\n\n")
    body.append("Suggested fix\n", style="bold green")
    body.append(str(structured.get("suggested_fix", "") or "(none)"))
    return Panel(body, title="Architecture finding", border_style="cyan", padding=(1, 2))


def _display_architecture_report(report: dict[str, Any]) -> None:
    console.print()
    header = Text()
    header.append("Architecture review\n", style="bold")
    header.append(f"Path: {report.get('path', '')}\n")
    header.append(f"Findings reviewed: {report.get('finding_count', 0)}")
    header.append(f"\nAccepted: {report.get('accepted_count', 0)}", style="green")
    header.append(
        f"  Needs your review: {report.get('needs_review_count', 0)}",
        style="yellow",
    )
    if report.get("gate_threshold") is not None:
        header.append(f"  Gate: ≥{report['gate_threshold']}%", style="dim")
    context_files = report.get("context_files") or []
    if context_files:
        header.append(
            f"\nCross-file context: {len(context_files)} related file(s)",
            style="dim",
        )
    console.print(Panel.fit(header, border_style="white"))

    if report.get("skipped"):
        console.print(
            Panel(
                Text(str(report.get("message") or ""), style="yellow"),
                title="Architecture review skipped",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        return

    accepted = report.get("accepted") or []
    needs_review = report.get("needs_review") or []
    if not accepted and not needs_review:
        console.print()
        console.print(
            Panel(
                Text(
                    str(report.get("message") or "No architecture issues found."),
                    style="green",
                ),
                title="No issues found",
                border_style="green",
                padding=(1, 2),
            )
        )
        return

    for label, bucket, style in (
        ("Accepted finding", accepted, "green"),
        ("Needs your review", needs_review, "yellow"),
    ):
        for index, item in enumerate(bucket, start=1):
            structured = item.get("structured_finding") or {}
            console.print()
            console.print(
                Rule(
                    f"{label} {index}/{len(bucket)} — "
                    f"{structured.get('finding_type', 'unknown')} "
                    f"({structured.get('confidence', 'n/a')}% confidence)",
                    style=style,
                )
            )
            console.print(_render_architecture_finding(item))


def _display_combined_summary(report: dict[str, Any]) -> None:
    summary = report.get("summary") or {}
    header = Text()
    header.append("secondpass supervisor\n", style="bold")
    header.append(f"Path: {report.get('path', '')}\n")
    workers = summary.get("workers_run") or []
    header.append(f"Workers: {', '.join(workers) or 'none'}\n")
    header.append(
        f"Overall accepted: {summary.get('accepted_count', 0)}",
        style="green",
    )
    header.append(
        f"  Needs your review: {summary.get('needs_review_count', 0)}",
        style="yellow",
    )
    if summary.get("gate_threshold") is not None:
        header.append(f"  Gate: ≥{summary['gate_threshold']}%", style="dim")
    header.append(
        f"\nSecurity: {summary.get('security_accepted', 0)} accepted / "
        f"{summary.get('security_needs_review', 0)} needs review"
    )
    if summary.get("architecture_skipped"):
        header.append("\nArchitecture: skipped", style="dim")
    else:
        header.append(
            f"\nArchitecture: {summary.get('architecture_accepted', 0)} accepted / "
            f"{summary.get('architecture_needs_review', 0)} needs review"
        )
    persisted = summary.get("persisted_review_ids") or report.get("persisted_review_ids")
    job_id = summary.get("job_id") or report.get("job_id")
    if job_id:
        header.append(f"\nAudit job_id: {job_id}", style="dim")
        header.append(
            f"\nAudit: secondpass audit {job_id}",
            style="dim",
        )
    if persisted:
        header.append("\nPersisted review ids: ", style="dim")
        parts = [
            f"{worker}={rid}"
            for worker, rid in persisted.items()
            if rid is not None
        ]
        header.append(", ".join(parts) or "(none)", style="dim")
        header.append(
            "\nDecide: secondpass decide --review-id <id> --index 0 "
            "--accept|--reject --reason \"...\"",
            style="dim",
        )
    console.print()
    console.print(Panel.fit(header, border_style="white"))


@app.command()
def review(
    path: Path | None = typer.Argument(
        None,
        exists=True,
        readable=True,
        resolve_path=True,
        help="File or directory to review. Omit when using --diff.",
    ),
    diff: bool = typer.Option(
        False,
        "--diff",
        help=(
            "Review files from git diff (staged preferred; unstaged if nothing "
            "is staged). Analyzes whole files, reports only findings on changed lines."
        ),
    ),
) -> None:
    """Run the full secondpass agent review and display a structured report."""
    if diff and path is not None:
        console.print(
            "[bold red]Error:[/bold red] Use either `review <path>` or "
            "`review --diff`, not both.",
            highlight=False,
        )
        raise typer.Exit(code=1)
    if not diff and path is None:
        console.print(
            "[bold red]Error:[/bold red] Provide a path or pass --diff.",
            highlight=False,
        )
        raise typer.Exit(code=1)

    combined_report: dict[str, Any] | None = None
    security_report: dict[str, Any] | None = None
    try:
        if diff:
            selection = collect_diff_selection()
            if not selection.files:
                console.print(
                    Panel(
                        Text(
                            f"No {selection.mode} changes to review.\n"
                            "Stage files (`git add`) or edit something first.",
                            style="yellow",
                        ),
                        title="Clean result",
                        border_style="yellow",
                        padding=(1, 2),
                    )
                )
                return

            console.print(
                f"[bold]Starting diff review[/bold] "
                f"([cyan]{selection.mode}[/cyan], "
                f"{len(selection.files)} file(s))\n"
                "[dim]Whole files are scanned for context; only findings on "
                "changed lines are reported. Progress updates below; "
                "tool traces go to stderr. Architecture is Security-only "
                "in --diff mode.[/dim]\n"
            )
            for changed in selection.files:
                ranges = ", ".join(f"{start}-{end}" for start, end in changed.ranges) or "n/a"
                console.print(f"  • {changed.path}  [dim]lines {ranges}[/dim]")
            console.print()
            with ReviewProgress(console) as on_stage:
                security_report = review_changed_files(
                    selection.files,
                    mode=selection.mode,
                    on_stage=on_stage,
                )
        else:
            console.print(
                f"[bold]Starting review of[/bold] {path}\n"
                "[dim]Supervisor runs Security then Architecture; "
                "stage progress below; tool traces go to stderr.[/dim]\n"
            )
            with ReviewProgress(console) as on_stage:
                combined_report = supervise_review(str(path), on_stage=on_stage)
    except (ScanError, GitDiffError, ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001 — surface unexpected agent failures cleanly
        console.print(f"[bold red]Review failed:[/bold red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    if combined_report is not None:
        _display_combined_summary(combined_report)
        _display_report(combined_report.get("security") or {})
        architecture = combined_report.get("architecture")
        if architecture is not None:
            _display_architecture_report(architecture)
    elif security_report is not None:
        _display_report(security_report)


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


@app.command()
def decide(
    review_id: int = typer.Option(..., "--review-id", help="Persisted review id from a review run."),
    index: int = typer.Option(
        0,
        "--index",
        "-i",
        help="0-based index into that review's findings list.",
    ),
    reason: str = typer.Option(
        ...,
        "--reason",
        "-r",
        help="Required human reason for the accept/reject decision.",
    ),
    accept: bool = typer.Option(False, "--accept", help="Mark the finding accepted."),
    reject: bool = typer.Option(False, "--reject", help="Mark the finding rejected."),
    linked_fix_commit: str | None = typer.Option(
        None,
        "--fix-commit",
        help="Optional linked fix commit hash.",
    ),
) -> None:
    """Record a human accept/reject decision into verified-outcome SQLite memory."""
    if accept == reject:
        console.print(
            "[bold red]Error:[/bold red] Pass exactly one of --accept or --reject.",
            highlight=False,
        )
        raise typer.Exit(code=1)

    from app.verified import record_finding_decision

    try:
        outcome = record_finding_decision(
            review_id,
            index,
            accepted=accept,
            reason=reason,
            linked_fix_commit=linked_fix_commit,
        )
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    decision = "accepted" if outcome.accepted else "rejected"
    console.print(
        Panel(
            Text(
                f"Outcome #{outcome.id}: {decision}\n"
                f"File: {outcome.file_path}\n"
                f"Review id: {outcome.review_id}\n"
                f"Type: {outcome.finding.finding_type}\n"
                f"Reason: {outcome.reason}"
            ),
            title="Verified outcome saved",
            border_style="green" if outcome.accepted else "yellow",
            padding=(1, 2),
        )
    )


@app.command("list-outcomes")
def list_outcomes_cmd(
    path: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        resolve_path=True,
        help="File path whose verified outcomes to list.",
    ),
) -> None:
    """List SQLite verified outcomes for a file (newest first)."""
    from app.persistence import _ROOT, _normalize_path, list_outcomes_for_file

    absolute_key = _normalize_path(str(path.resolve()))
    try:
        relative_key = _normalize_path(str(path.resolve().relative_to(_ROOT.resolve())))
    except ValueError:
        relative_key = absolute_key

    outcomes = list_outcomes_for_file(absolute_key)
    if not outcomes and relative_key != absolute_key:
        outcomes = list_outcomes_for_file(relative_key)

    display_key = relative_key if outcomes else absolute_key
    if not outcomes:
        console.print(f"No verified outcomes for {display_key}")
        raise typer.Exit(code=1)

    table = Table(title=f'Verified outcomes for "{display_key}"')
    table.add_column("ID", justify="right")
    table.add_column("Decision")
    table.add_column("Type", overflow="fold")
    table.add_column("Reason", overflow="fold")
    table.add_column("Review", justify="right")
    table.add_column("When", overflow="fold")

    for item in outcomes:
        table.add_row(
            str(item.id),
            "accepted" if item.accepted else "rejected",
            item.finding.finding_type,
            item.reason,
            str(item.review_id) if item.review_id is not None else "",
            item.created_at.isoformat(),
        )

    console.print(table)


@app.command("list-reviews")
def list_reviews_cmd(
    path: Path | None = typer.Argument(
        None,
        exists=True,
        readable=True,
        resolve_path=True,
        help="Optional file path filter (shows matching reviews).",
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max reviews to show."),
) -> None:
    """List recent persisted review runs (for decide --review-id)."""
    from app.persistence import _ROOT, _normalize_path, list_reviews

    reviews = list_reviews(limit=limit)
    if path is not None:
        try:
            key = _normalize_path(str(path.resolve().relative_to(_ROOT.resolve())))
        except ValueError:
            key = _normalize_path(str(path.resolve()))
        reviews = [item for item in reviews if item.file_path == key]

    if not reviews:
        console.print("No persisted reviews found.")
        raise typer.Exit(code=1)

    table = Table(title="Persisted reviews")
    table.add_column("ID", justify="right")
    table.add_column("Worker")
    table.add_column("File", overflow="fold")
    table.add_column("Findings", justify="right")
    table.add_column("Accepted", justify="right")
    table.add_column("Needs review", justify="right")
    table.add_column("When", overflow="fold")

    for item in reviews:
        table.add_row(
            str(item.id),
            item.worker_name,
            item.file_path,
            str(len(item.review_result.findings)),
            str(item.accepted_count),
            str(item.needs_review_count),
            item.created_at.isoformat(),
        )

    console.print(table)


@app.command("audit")
def audit_cmd(
    job_id: str = typer.Argument(..., help="Submission job_id from API or CLI review."),
) -> None:
    """Show one ordered audit trail for a job_id (both workers)."""
    from app.audit import get_audit_trail

    events = get_audit_trail(job_id)
    if not events:
        console.print(f"No audit trail for job_id={job_id}")
        raise typer.Exit(code=1)

    table = Table(title=f"Audit trail — {job_id}")
    table.add_column("#", justify="right")
    table.add_column("When", overflow="fold")
    table.add_column("Worker")
    table.add_column("Stage")
    table.add_column("Detail", overflow="fold")

    for event in events:
        detail = event.detail or {}
        compact: dict[str, Any] = {}
        for key in (
            "threshold",
            "accepted_count",
            "needs_review_count",
            "finding_count",
            "ok",
            "reason",
            "review_id",
            "outcome_id",
            "accepted",
            "path",
            "workers_run",
            "persisted_review_ids",
            "storage",
        ):
            if key in detail:
                compact[key] = detail[key]
        if "prompt" in detail and isinstance(detail["prompt"], dict):
            compact["prompt_chars"] = detail["prompt"].get("total_chars")
        if "model_out" in detail and isinstance(detail["model_out"], dict):
            compact["model_out_chars"] = detail["model_out"].get("chars")
        if not compact:
            compact = detail
        table.add_row(
            str(event.id),
            event.timestamp.isoformat(),
            event.worker_name or "",
            event.stage,
            str(compact),
        )

    console.print(table)


if __name__ == "__main__":
    app()
