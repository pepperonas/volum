"""The engine's HTTP surface: routes only.

Every handler is a few lines that call the service and shape the response.
If a handler starts deciding *what happens*, that logic belongs in
:mod:`volum_core.service`, where the CLI can reach it too (spec section 32).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from volum_core.hardware import DoctorReport
from volum_core.jobs import JobRecord
from volum_core.service import (
    InstallTask,
    JobRequest,
    JobSubmission,
    ModelEntry,
    ServiceError,
    SettingsView,
    VolumService,
)
from volum_core.version import __version__

from .auth import check_token, is_authorised, unauthorised_response
from .errors import install_error_handlers
from .sse import EventSource

#: Origins the desktop webview loads from. Tauri 2 serves the frontend from
#: its own scheme; the Vite dev server is what `pnpm tauri dev` uses. Anything
#: else is refused by the browser before it ever reaches a handler — and would
#: still need the token if it did.
DEFAULT_ALLOWED_ORIGINS = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
)
ALLOWED_ORIGINS_ENV = "VOLUM_ENGINE_ALLOWED_ORIGINS"

#: What a browser or viewer should be told an artifact is.
_MEDIA_TYPES = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".stl": "model/stl",
    ".3mf": "model/3mf",
    ".obj": "model/obj",
    ".ply": "application/octet-stream",
    ".json": "application/json",
}


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = __version__


class InstallBody(BaseModel):
    allow_marginal: bool | None = None
    hf_token: str | None = None


class SettingsPatch(BaseModel):
    hugging_face_token: str | None = None
    clear_hugging_face_token: bool = False
    allow_marginal_models: bool | None = None
    data_dir: Path | None = None


class RemovedResponse(BaseModel):
    removed: bool


class JobListQuery(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=500)


def allowed_origins() -> list[str]:
    raw = os.environ.get(ALLOWED_ORIGINS_ENV)
    if raw is None:
        return list(DEFAULT_ALLOWED_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(service: VolumService, *, token: str) -> FastAPI:
    """Build the ASGI application around an already-constructed service."""
    check_token(token)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        service.shutdown()

    app = FastAPI(
        title="VOLUM engine",
        version=__version__,
        lifespan=lifespan,
        # No interactive docs: the engine is a sidecar, not a public API, and
        # the docs pages would be the only unauthenticated thing it served.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def _require_token(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method == "OPTIONS":
            # CORS preflight carries no credentials by design; the CORS layer
            # outside this one answers it.
            return await call_next(request)
        if not is_authorised(request, token):
            return unauthorised_response()
        return await call_next(request)

    # Added last so it sits outermost and answers preflight before auth.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    job_events: EventSource[JobRecord] = EventSource(
        service.subscribe_jobs,
        accept=lambda _: True,
        sequence=lambda record: len(record.progress),
        is_final=lambda record: record.status.is_terminal,
    )

    # --- system ---------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/system/doctor", response_model=DoctorReport)
    async def doctor(deep: bool = True) -> DoctorReport:
        return service.doctor(deep=deep)

    @app.get("/api/settings", response_model=SettingsView)
    async def get_settings() -> SettingsView:
        return service.settings_view()

    @app.patch("/api/settings", response_model=SettingsView)
    async def patch_settings(body: SettingsPatch) -> SettingsView:
        return service.update_settings(
            hugging_face_token=body.hugging_face_token,
            clear_hugging_face_token=body.clear_hugging_face_token,
            allow_marginal_models=body.allow_marginal_models,
            data_dir=body.data_dir,
        )

    # --- models ---------------------------------------------------------------

    @app.get("/api/models", response_model=list[ModelEntry])
    async def list_models() -> list[ModelEntry]:
        return service.list_models()

    @app.get("/api/models/{model_id}", response_model=ModelEntry)
    async def get_model(model_id: str) -> ModelEntry:
        return service.get_model(model_id)

    @app.post("/api/models/{model_id}/install", response_model=InstallTask, status_code=202)
    async def install_model(model_id: str, body: InstallBody | None = None) -> InstallTask:
        body = body or InstallBody()
        return service.install_model(
            model_id, allow_marginal=body.allow_marginal, hf_token=body.hf_token
        )

    @app.get("/api/models/{model_id}/install/events")
    async def install_events(model_id: str, request: Request) -> Response:
        entry = service.get_model(model_id)
        source: EventSource[InstallTask] = EventSource(
            service.subscribe_installs,
            accept=lambda task: task.model_id == model_id,
            # Installs have no progress list; every update is newer than the last.
            sequence=_monotonic_sequence(),
            is_final=lambda task: task.state != "running",
        )
        return source.response(request, entry.install)

    @app.delete("/api/models/{model_id}", response_model=RemovedResponse)
    async def remove_model(model_id: str) -> RemovedResponse:
        return RemovedResponse(removed=service.remove_model(model_id))

    # --- jobs -----------------------------------------------------------------

    @app.get("/api/jobs", response_model=list[JobRecord])
    async def list_jobs(
        limit: int | None = Query(default=None, ge=1, le=500),
    ) -> list[JobRecord]:
        return service.list_jobs(limit=limit)

    @app.post("/api/jobs", response_model=JobSubmission, status_code=202)
    async def create_job(body: JobRequest) -> JobSubmission:
        return service.submit_job(body)

    @app.get("/api/jobs/{job_id}", response_model=JobRecord)
    async def get_job(job_id: str) -> JobRecord:
        record = service.get_job(job_id)
        if record is None:
            raise _no_job(job_id)
        return record

    @app.get("/api/jobs/{job_id}/events")
    async def job_events_stream(job_id: str, request: Request) -> Response:
        record = service.get_job(job_id)
        if record is None:
            raise _no_job(job_id)
        return job_events.response(request, record)

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobRecord)
    async def cancel_job(job_id: str) -> JobRecord:
        record = service.cancel_job(job_id)
        if record is None:
            raise _no_job(job_id)
        return record

    @app.delete("/api/jobs/{job_id}", response_model=RemovedResponse)
    async def delete_job(job_id: str) -> RemovedResponse:
        if not service.delete_job(job_id):
            raise _no_job(job_id)
        return RemovedResponse(removed=True)

    @app.get("/api/jobs/{job_id}/artifacts/{name}")
    async def get_artifact(job_id: str, name: str) -> Response:
        path = service.artifact_path(job_id, name)
        media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
        if media_type == "application/json":
            return JSONResponse(content=_read_json(path))
        return FileResponse(path, media_type=media_type, filename=path.name)

    return app


def _no_job(job_id: str) -> ServiceError:
    return ServiceError("not_found", f"No job '{job_id}'.")


def _monotonic_sequence() -> Callable[[BaseModel], int]:
    counter = 0

    def next_seq(_: BaseModel) -> int:
        nonlocal counter
        counter += 1
        return counter

    return next_seq


def _read_json(path: Path) -> object:
    import json  # noqa: PLC0415 - tiny helper, keeps the module header honest

    return json.loads(path.read_text(encoding="utf-8"))
