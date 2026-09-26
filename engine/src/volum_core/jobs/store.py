"""Job persistence.

One directory per job under ``<data>/jobs/<id>/``, holding ``job.json`` plus the
job's artifacts. Plain files rather than a database: a job directory is
self-contained, inspectable, and can be deleted with ``rm``. SQLite is reserved
for state that genuinely needs querying (spec section 3).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .types import JobError, JobRecord, JobStatus

JOB_FILE = "job.json"


class JobStore:
    """Reads and writes job records under a jobs root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, job_id: str) -> Path:
        """The directory for ``job_id``.

        ``job_id`` is validated rather than trusted: it reaches this method from
        HTTP paths and CLI arguments, and a value like ``../../etc`` would
        otherwise write outside the data directory (spec section 50).
        """
        if not job_id.isalnum():
            raise ValueError(f"Invalid job id: {job_id!r}")
        return self.root / job_id

    def save(self, record: JobRecord) -> Path:
        """Write a job record atomically.

        Atomic because the record is read by the UI while the job is running; a
        half-written file would surface as a crash mid-generation.
        """
        directory = self.directory(record.id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / JOB_FILE
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)
        return target

    def load(self, job_id: str) -> JobRecord | None:
        target = self.directory(job_id) / JOB_FILE
        try:
            raw = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        try:
            return JobRecord.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            return None

    def list_jobs(self, *, limit: int | None = None) -> list[JobRecord]:
        """All readable jobs, newest first.

        An unreadable job directory is skipped rather than raising: one corrupt
        record must not make the job list inaccessible.
        """
        records: list[JobRecord] = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            record = self.load(directory.name) if directory.name.isalnum() else None
            if record is not None:
                records.append(record)
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records[:limit] if limit is not None else records

    def delete(self, job_id: str) -> bool:
        directory = self.directory(job_id)
        if not directory.is_dir():
            return False
        for path in sorted(directory.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        directory.rmdir()
        return True

    def recover_interrupted(self) -> list[JobRecord]:
        """Fail jobs that were still running when the engine died.

        Without this a job left in ``reconstructing`` by a crash or a power cut
        shows a spinner for ever. The engine calls this at start-up, and the
        distinction matters to the user: the job did not fail on its own, the
        application stopped.
        """
        recovered: list[JobRecord] = []
        for record in self.list_jobs():
            if record.status.is_terminal:
                continue
            record.status = JobStatus.FAILED
            # Not "now": the gap until VOLUM was started again is not time the
            # job spent running, and reporting it as a duration produced
            # readings like "93:55 h" for a job that lived half a minute. The
            # last progress entry is the last moment it was known to be alive.
            last_seen = record.progress[-1].at if record.progress else record.started_at
            record.finished_at = last_seen or datetime.now(UTC)
            record.error = JobError(
                message="This job was interrupted when VOLUM stopped.",
                technical="Job was in a non-terminal state at engine start-up.",
                suggestions=["Start the job again."],
            )
            self.save(record)
            recovered.append(record)
        return recovered
