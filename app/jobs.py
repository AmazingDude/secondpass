"""In-process execution with durable terminal-job lookup in local SQLite."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from app.persistence import StoredTerminalJob, get_terminal_job, save_terminal_job

JobStatus = Literal["queued", "running", "completed", "failed"]

ReviewRunner = Callable[..., dict[str, Any]]


@dataclass
class ReviewJob:
    job_id: str
    path: str
    options: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = "queued"
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "path": self.path,
            "options": self.options,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if self.status == "completed" and self.result is not None:
            payload["persisted_review_ids"] = self.result.get("persisted_review_ids")
            payload["summary"] = self.result.get("summary")
            payload["result"] = self.result
        return payload


class JobStore:
    """Live jobs plus saved terminal responses; historical lookup never replays."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
        runner: ReviewRunner | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        self._jobs: dict[str, ReviewJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._runner = runner or self._default_runner
        self._db_path = db_path

    @staticmethod
    def _default_runner(
        path: str,
        *,
        job_id: str | None = None,
        workers: int = 2,
        max_files: int = 10,
        include_init: bool = False,
        include: tuple[str, ...] = (),
        exclude: tuple[str, ...] = (),
        memory_enabled: bool = True,
    ) -> dict[str, Any]:
        target = Path(path)
        from app.supervisor import supervise_review

        if target.is_file():
            return supervise_review(path, job_id=job_id, memory_enabled=memory_enabled)

        from app.multifile import review_python_files, select_python_files

        selection = select_python_files(
            target,
            max_files=max_files,
            include_init=include_init,
            include=include,
            exclude=exclude,
        )
        if not selection.selected:
            raise ValueError("No eligible Python files matched the directory options")

        aggregate = review_python_files(
            selection.selected,
            workers=workers,
            review_one=lambda file_path: supervise_review(
                file_path, job_id=job_id, memory_enabled=memory_enabled
            ),
        )
        aggregate["path"] = str(selection.root)
        aggregate["mode"] = "directory"
        aggregate["workers"] = workers
        aggregate["selection"] = {
            "selected_count": selection.selected_count,
            "eligible_count": selection.eligible_count,
            "discovered_count": selection.discovered_count,
            "capped": selection.capped,
            "include_init": selection.include_init,
            "selected": selection.relative_selected(),
        }
        aggregate["persisted_review_ids"] = [
            review_id
            for item in aggregate["files"]
            for review_id in (item["report"].get("persisted_review_ids") or {}).values()
            if isinstance(review_id, int)
        ]
        aggregate["summary"] = {
            "accepted_count": aggregate["accepted_count"],
            "needs_review_count": aggregate["needs_review_count"],
            "file_count": aggregate["file_count"],
            "workers": workers,
        }
        return aggregate

    def set_runner(self, runner: ReviewRunner) -> None:
        """Override the review callable (tests inject a delayed mock)."""
        self._runner = runner

    def submit(self, path: str, **options: Any) -> ReviewJob:
        target = str(Path(path).expanduser())
        job = ReviewJob(
            job_id=str(uuid.uuid4()),
            path=target,
            options=dict(options),
            status="queued",
        )
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(self._execute, job.job_id)
        return job

    def get(self, job_id: str) -> ReviewJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                saved = get_terminal_job(job_id, db_path=self._db_path)
                return ReviewJob(**saved.model_dump()) if saved is not None else None
            # Return a shallow snapshot so callers don't mutate internal state.
            return ReviewJob(
                job_id=job.job_id,
                path=job.path,
                options=dict(job.options),
                status=job.status,
                error=job.error,
                result=job.result,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )

    def _update(
        self,
        job_id: str,
        *,
        status: JobStatus,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            when = datetime.now(timezone.utc)
            if status in {"completed", "failed"}:
                try:
                    save_terminal_job(
                        StoredTerminalJob(
                            job_id=job.job_id, path=job.path, options=job.options,
                            status=status, error=error, result=result,
                            created_at=job.created_at, updated_at=when,
                        ),
                        db_path=self._db_path,
                    )
                except Exception:
                    # Do not advertise durable completion when saving failed.
                    status = "failed"
                    error = "Could not save the job result. Check local storage and retry."
                    result = None
            job.status = status
            job.error = error
            job.result = result
            job.updated_at = when

    def _execute(self, job_id: str) -> None:
        self._update(job_id, status="running")
        try:
            with self._lock:
                path = self._jobs[job_id].path
                options = dict(self._jobs[job_id].options)
            result = self._call_runner(path, job_id, options)
            self._update(job_id, status="completed", result=result, error=None)
        except Exception:  # noqa: BLE001 — surface as failed job status
            self._update(
                job_id, status="failed", error="Review failed. Check your setup and retry."
            )

    def _call_runner(
        self, path: str, job_id: str, options: dict[str, Any]
    ) -> dict[str, Any]:
        """Pass job_id when the runner accepts it (default + new tests)."""
        import inspect

        try:
            signature = inspect.signature(self._runner)
        except (TypeError, ValueError):
            return self._runner(path)
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        kwargs = {
            key: value
            for key, value in {"job_id": job_id, **options}.items()
            if accepts_kwargs or key in signature.parameters
        }
        return self._runner(path, **kwargs)

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)


# Process-wide executor; terminal responses are looked up lazily after restart.
job_store = JobStore()
