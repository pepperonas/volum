"""Job lifecycle: create, advance, cancel.

The manager owns transitions and persistence. It does **not** run inference —
that happens in a separate worker subprocess, so a GPU fault or an out-of-memory
kill fails one job instead of the engine (``docs/architecture.md`` section 2).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from .store import JobStore
from .types import (
    InvalidTransitionError,
    JobError,
    JobProgress,
    JobRecord,
    JobStatus,
    can_transition,
)

#: Called whenever a job changes. The HTTP engine turns this into an SSE stream.
JobListener = Callable[[JobRecord], None]


class CancellationToken:
    """Cooperative cancellation, checked between stages.

    Cooperative because a running GPU kernel cannot be interrupted from Python.
    The token stops the pipeline at the next stage boundary; killing work
    already inside a kernel is what terminating the worker subprocess is for.
    Without both, ``CANCELLED`` would be a state the product could not honour.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise JobCancelledError


class JobCancelledError(Exception):
    """Raised at a stage boundary when the user has cancelled."""


class JobManager:
    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._listeners: list[JobListener] = []
        self._tokens: dict[str, CancellationToken] = {}
        self._lock = threading.Lock()

    # --- observation ------------------------------------------------------

    def subscribe(self, listener: JobListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    def _notify(self, record: JobRecord) -> None:
        for listener in list(self._listeners):
            # One misbehaving listener must not stop a job or the others.
            with suppress(Exception):
                listener(record)

    # --- lifecycle --------------------------------------------------------

    def create(  # noqa: PLR0913 - keyword-only job fields; a parameter object
        # would only relocate them
        self,
        *,
        model_id: str,
        input_files: Iterable[Path],
        parameters: dict[str, object] | None = None,
        seed: int | None = None,
        model_version: str | None = None,
        runtime: str | None = None,
    ) -> JobRecord:
        record = JobRecord(
            model_id=model_id,
            model_version=model_version,
            runtime=runtime,
            input_files=list(input_files),
            parameters=parameters or {},
            seed=seed,
        )
        self._store.save(record)
        self._notify(record)
        return record

    def token(self, job_id: str) -> CancellationToken:
        with self._lock:
            return self._tokens.setdefault(job_id, CancellationToken())

    def advance(
        self,
        record: JobRecord,
        status: JobStatus,
        *,
        message: str = "",
        fraction: float | None = None,
    ) -> JobRecord:
        """Move a job to ``status`` and record the progress entry."""
        if not can_transition(record.status, status):
            raise InvalidTransitionError(record.status, status)

        if record.started_at is None and status is not JobStatus.QUEUED:
            record.started_at = datetime.now(UTC)
        record.status = status
        if status.is_terminal:
            record.finished_at = datetime.now(UTC)
        record.progress.append(JobProgress(stage=status, fraction=fraction, message=message))

        self._store.save(record)
        self._notify(record)
        return record

    def report(self, record: JobRecord, message: str, fraction: float | None = None) -> JobRecord:
        """Record progress **within** the current stage without a transition.

        ``fraction`` stays ``None`` unless the provider reported a real one.
        """
        record.progress.append(JobProgress(stage=record.status, fraction=fraction, message=message))
        self._store.save(record)
        self._notify(record)
        return record

    def fail(self, record: JobRecord, error: JobError) -> JobRecord:
        record.error = error
        return self.advance(record, JobStatus.FAILED, message=error.message)

    def cancel(self, job_id: str) -> JobRecord | None:
        """Request cancellation. Returns the record, or ``None`` if unknown.

        A job that has already finished is returned unchanged: cancelling a
        completed job is a no-op, not an error.
        """
        record = self._store.load(job_id)
        if record is None:
            return None
        self.token(job_id).cancel()
        if record.status.is_terminal:
            return record
        return self.advance(record, JobStatus.CANCELLED, message="Cancelled by the user.")

    def get(self, job_id: str) -> JobRecord | None:
        return self._store.load(job_id)

    def list(self, *, limit: int | None = None) -> list[JobRecord]:
        return self._store.list_jobs(limit=limit)
