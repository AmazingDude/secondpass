"""CLI review progress helpers (Rich spinner / stage status)."""

from __future__ import annotations

from types import TracebackType
from typing import Callable

from rich.console import Console
from rich.status import Status

StageCallback = Callable[[str], None]

STAGE_LABELS: dict[str, str] = {
    "scanning": "[cyan]Scanning with Semgrep…[/cyan]",
    "logic_review": "[yellow]Running logic review…[/yellow]",
    "workers": "[magenta]Routing findings to workers…[/magenta]",
    "architecture_review": "[blue]Running architecture review…[/blue]",
    "building_report": "[green]Building report…[/green]",
}


def stage_label(stage: str) -> str:
    """Return a colored Rich markup label for a review stage id."""
    known = STAGE_LABELS.get(stage)
    if known is not None:
        return known
    pretty = stage.replace("_", " ").strip().capitalize() or "Working"
    return f"[blue]{pretty}…[/blue]"


class ReviewProgress:
    """Context manager: Rich spinner updated via ``on_stage(stage_id)``."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._status: Status | None = None

    def __enter__(self) -> StageCallback:
        self._status = self._console.status(
            "[bold]Starting review…[/bold]",
            spinner="dots",
        )
        self._status.start()
        return self.update

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def update(self, stage: str) -> None:
        if self._status is not None:
            self._status.update(stage_label(stage))
