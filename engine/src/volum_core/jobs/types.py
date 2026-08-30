"""Job value types and the state machine.

3D inference runs for minutes, so nothing may block on it (spec section 17).
A job is a persisted record that survives a restart, and its progress is
reported by **stage**, not by an invented percentage (spec section 18).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    RECONSTRUCTING = "reconstructing"
    TEXTURING = "texturing"
    OPTIMIZING = "optimizing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL

    @property
    def is_active(self) -> bool:
        return not self.is_terminal


_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})

#: The ordered happy path. Every active status may also go to FAILED or
#: CANCELLED, which is added below rather than written out five times.
_SEQUENCE: tuple[JobStatus, ...] = (
    JobStatus.QUEUED,
    JobStatus.PREPROCESSING,
    JobStatus.RECONSTRUCTING,
    JobStatus.TEXTURING,
    JobStatus.OPTIMIZING,
    JobStatus.VALIDATING,
    JobStatus.COMPLETED,
)

_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {}
for _index, _status in enumerate(_SEQUENCE[:-1]):
    # Stages may be skipped forwards: a provider without a texturing step goes
    # straight from reconstructing to optimizing. Going backwards is a bug.
    _TRANSITIONS[_status] = frozenset(
        set(_SEQUENCE[_index + 1 :]) | {JobStatus.FAILED, JobStatus.CANCELLED}
    )
for _status in _TERMINAL:
    _TRANSITIONS[_status] = frozenset()


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    """Whether ``current -> target`` is legal.

    Terminal states are final. Re-entering a state is not a transition and is
    rejected, so a stuck provider cannot look like progress.
    """
    return target in _TRANSITIONS[current]


class InvalidTransitionError(ValueError):
    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        super().__init__(f"Cannot move a job from {current.value} to {target.value}.")
        self.current = current
        self.target = target


class JobError(BaseModel):
    """User-facing and technical detail, kept apart (spec section 55).

    ``message`` is what a person reads; ``technical`` is the exception. Showing
    'RuntimeError: MPS backend out of memory' as the message is the failure this
    split exists to prevent, and the technical text is still always logged.
    """

    message: str
    technical: str | None = None
    suggestions: list[str] = Field(default_factory=list)


class JobProgress(BaseModel):
    stage: JobStatus
    #: ``None`` whenever the provider does not report a real fraction, which is
    #: most of the time. Renderers show an indeterminate indicator instead of
    #: inventing a number.
    fraction: float | None = None
    message: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: JobStatus = JobStatus.QUEUED
    model_id: str
    model_version: str | None = None
    runtime: str | None = None

    input_files: list[Path] = Field(default_factory=list)
    parameters: dict[str, object] = Field(default_factory=dict)
    seed: int | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None

    artifacts: dict[str, Path] = Field(default_factory=dict)
    progress: list[JobProgress] = Field(default_factory=list)
    error: JobError | None = None
    log_path: Path | None = None

    @property
    def current_progress(self) -> JobProgress | None:
        return self.progress[-1] if self.progress else None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()
