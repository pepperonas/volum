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
    """Owns job transitions. Safe to call from more than one thread.

    Two threads touch a running job: the one running the pipeline, which holds
    the record object, and the one serving HTTP, which knows only the id. The
    manager keeps every **active** record in memory so both talk about the same
    object — a cancel that only updated a freshly loaded copy would be
    overwritten the next time the runner saved. Finished records are dropped
    and read from disk again; the store, not this map, is the source of truth.
    """

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._listeners: list[JobListener] = []
        self._tokens: dict[str, CancellationToken] = {}
        self._live: dict[str, JobRecord] = {}
        # Re-entrant: ``fail`` and ``cancel`` take it and then call ``advance``.
        self._lock = threading.RLock()

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
        input_hashes: Iterable[str] = (),
        input_names: Iterable[str] = (),
        export_formats: Iterable[str] = ("glb",),
        target_size_mm: float | None = None,
        single_part: bool = False,
    ) -> JobRecord:
        record = JobRecord(
            model_id=model_id,
            model_version=model_version,
            runtime=runtime,
            input_files=list(input_files),
            input_hashes=list(input_hashes),
            input_names=list(input_names),
            parameters=parameters or {},
            seed=seed,
            export_formats=list(export_formats),
            target_size_mm=target_size_mm,
            single_part=single_part,
        )
        with self._lock:
            self._live[record.id] = record
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
        with self._lock:
            if not can_transition(record.status, status):
                raise InvalidTransitionError(record.status, status)

            if record.started_at is None and status is not JobStatus.QUEUED:
                record.started_at = datetime.now(UTC)
            record.status = status
            if status.is_terminal:
                record.finished_at = datetime.now(UTC)
                # The token stays: a runner that only now reaches ``token()``
                # must still learn that the job was cancelled.
                self._live.pop(record.id, None)
            record.progress.append(JobProgress(stage=status, fraction=fraction, message=message))
            self._store.save(record)
        self._notify(record)
        return record

    def report(self, record: JobRecord, message: str, fraction: float | None = None) -> JobRecord:
        """Record progress **within** the current stage without a transition.

        ``fraction`` stays ``None`` unless the provider reported a real one. A
        finished job ignores reports: a worker still talking after a cancel must
        not put fresh progress under a terminal badge.
        """
        with self._lock:
            if record.status.is_terminal:
                return record
            record.progress.append(
                JobProgress(stage=record.status, fraction=fraction, message=message)
            )
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
        with self._lock:
            record = self.get(job_id)
            if record is None:
                return None
            if record.status.is_terminal:
                return record
            self.token(job_id).cancel()
            return self.advance(record, JobStatus.CANCELLED, message="Cancelled by the user.")

    def get(self, job_id: str) -> JobRecord | None:
        """The live record while a job is active, the stored one afterwards."""
        with self._lock:
            live = self._live.get(job_id)
        return live if live is not None else self._store.load(job_id)

    def list(self, *, limit: int | None = None) -> list[JobRecord]:
        return self._store.list_jobs(limit=limit)
