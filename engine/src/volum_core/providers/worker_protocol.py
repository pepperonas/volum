"""The line protocol between the engine and a provider worker subprocess.

Deliberately plain: one JSON object per line on stdout. The worker runs in a
*different* Python environment — the provider's own venv — so the two sides
cannot share types or import each other. A line protocol is the only contract
that survives that boundary, and it is inspectable by a human when something
goes wrong.

Anything the worker writes to stderr is treated as diagnostics and logged; only
stdout carries protocol.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    PROGRESS = "progress"
    RESULT = "result"
    ERROR = "error"


class WorkerRequest(BaseModel):
    """Sent to the worker's stdin as a single JSON document."""

    images: list[str]
    output_dir: str
    source_dir: str
    weights_dir: str
    #: The install's own Hugging Face cache. Set so a model that loads weights by
    #: repository name resolves locally instead of downloading them again.
    hf_cache_dir: str | None = None
    device: str = "cpu"
    seed: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProgressEvent(BaseModel):
    type: EventType = EventType.PROGRESS
    stage: str
    message: str
    #: ``None`` unless the provider genuinely knows. Never synthesised.
    fraction: float | None = None


class ResultEvent(BaseModel):
    type: EventType = EventType.RESULT
    mesh_path: str
    artifacts: dict[str, str] = Field(default_factory=dict)
    vertices: int | None = None
    faces: int | None = None
    seed: int | None = None
    device: str | None = None
    duration_seconds: float | None = None


class ErrorEvent(BaseModel):
    type: EventType = EventType.ERROR
    #: Written for a person. The technical text goes in ``technical``.
    message: str
    technical: str = ""
    suggestions: list[str] = Field(default_factory=list)


def parse_event(line: str) -> ProgressEvent | ResultEvent | ErrorEvent | None:
    """Parse one protocol line, or return ``None`` if it is not protocol.

    Returning ``None`` rather than raising is deliberate: model libraries print
    to stdout uninvited — progress bars, deprecation notices, CUDA banners — and
    a stray line must not kill a job that is otherwise going fine.
    """
    import json  # noqa: PLC0415 - keeps this module importable in bare workers

    stripped = line.strip()
    if not stripped or not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    match payload.get("type"):
        case EventType.PROGRESS:
            return ProgressEvent.model_validate(payload)
        case EventType.RESULT:
            return ResultEvent.model_validate(payload)
        case EventType.ERROR:
            return ErrorEvent.model_validate(payload)
        case _:
            return None
