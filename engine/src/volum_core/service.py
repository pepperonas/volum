"""The application service: one place that wires the core together.

Both front doors — the CLI and the HTTP engine — are thin shells over this
class (spec section 32). Anything that decides *what happens* to a request
belongs here, so the two can never disagree: which device a job runs on, how a
``--print`` flag turns into export formats, where inputs are copied, what a
cancel does to a running worker.

Threading model: jobs run one at a time on a single worker thread (one GPU, one
job), installs on another. Callers on other threads — the HTTP server — only
ever talk to the :class:`~volum_core.jobs.JobManager`, which is thread-safe,
and to this class, which locks the little state it holds itself.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .config import (
    Settings,
    default_data_dir,
    ensure_data_layout,
    get_data_dir,
    is_within,
    load_settings,
    save_settings,
)
from .export import UnknownFormatError, resolve_export_options
from .hardware import DoctorReport, run_doctor
from .hardware.detect import detect_hardware
from .hardware.types import RuntimeKind
from .inputs import InputError, stage_inputs
from .jobs import JobError, JobManager, JobRecord, JobStore
from .models import InstallState, ModelInstallError, ModelManager
from .models.registry import all_models, get_model
from .pipeline import PipelineResult, run_pipeline
from .providers.factory import available_provider_ids, create_provider
from .providers.gating import Assessment, assess
from .providers.types import Capability, ImageTo3DProvider, ProviderMetadata

ErrorKind = Literal["invalid", "not_found", "conflict", "unavailable"]


class ProviderFactory(Protocol):
    """How a provider is obtained for a job.

    Injectable so the service can be tested with a double; the default is the
    one real table in :mod:`volum_core.providers.factory`.
    """

    def __call__(
        self, model_id: str, manager: ModelManager, *, device: str
    ) -> ImageTo3DProvider | None: ...


class ServiceError(Exception):
    """A request that cannot be honoured, with a message for a person.

    ``kind`` is what the HTTP layer maps onto a status code; the CLI prints the
    message. Neither ever shows the user a traceback for these (spec section 55).
    """

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        technical: str = "",
        suggestions: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.technical = technical
        self.suggestions = suggestions or []


class JobRequest(BaseModel):
    """Everything a caller may say about a generation. Mirrors the CLI flags."""

    model_id: str
    images: list[Path] = Field(min_length=1)
    seed: int | None = None
    parameters: dict[str, object] = Field(default_factory=dict)
    formats: list[str] | None = None
    for_print: bool = False
    target_size_mm: float | None = Field(default=None, gt=0)
    single_part: bool = False
    #: ``mps``, ``cuda`` or ``cpu``. ``None`` takes the doctor's recommendation.
    device: str | None = None


class JobSubmission(BaseModel):
    job: JobRecord
    #: Things the user should hear now, none of which stopped the job — a
    #: single-image model given three images, a print without a size.
    warnings: list[str] = Field(default_factory=list)


class InstallTask(BaseModel):
    """Progress of one model install, for polling or streaming."""

    model_id: str
    state: Literal["running", "done", "failed"] = "running"
    step: str = ""
    message: str = ""
    error: JobError | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class ModelEntry(BaseModel):
    """One catalogue row as the UI needs it: what it is, whether it is here,
    whether it can run here."""

    metadata: ProviderMetadata
    assessment: Assessment
    install_state: InstallState
    disk_bytes: int = 0
    #: A model can be in the catalogue — documented, licence-checked — before
    #: it has an implementation. Say so rather than fail at generation time.
    has_implementation: bool
    install: InstallTask | None = None


class SettingsView(BaseModel):
    """Settings as returned to a client. The token itself never leaves the process."""

    data_dir: str
    data_dir_is_default: bool
    hugging_face_token_set: bool
    allow_marginal_models: bool


InstallListener = Callable[[InstallTask], None]


class VolumService:
    def __init__(  # noqa: PLR0913 - keyword-only wiring seams, each optional
        self,
        data_dir: Path | None = None,
        *,
        settings: Settings | None = None,
        settings_path: Path | None = None,
        model_manager: ModelManager | None = None,
        provider_factory: ProviderFactory = create_provider,
        recover_interrupted: bool = False,
    ) -> None:
        self._settings_path = settings_path
        self._settings = settings or load_settings(settings_path)
        self.data_dir = data_dir or get_data_dir(self._settings)
        # ``get_data_dir`` creates the layout for the default; do the same for
        # an explicit directory so callers never see a half-made tree.
        ensure_data_layout(self.data_dir)

        self.jobs_dir = self.data_dir / "jobs"
        self.jobs = JobManager(JobStore(self.jobs_dir))
        self.models = model_manager or ModelManager(self.data_dir / "models")
        self._provider_factory = provider_factory

        self._job_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="volum-job")
        self._install_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="volum-install"
        )
        self._running: dict[str, ImageTo3DProvider] = {}
        self._futures: dict[str, Future[PipelineResult]] = {}
        self._installs: dict[str, InstallTask] = {}
        self._install_listeners: list[InstallListener] = []
        self._lock = threading.Lock()
        self._closed = False

        if recover_interrupted:
            # Whatever was mid-flight when the previous process died is failed
            # now, with an error that says so — not left spinning. Only the
            # long-lived owner of the data directory (the engine) may do this:
            # a CLI command running beside a live engine would otherwise fail
            # the engine's running jobs.
            self.jobs._store.recover_interrupted()

    # --- system -----------------------------------------------------------------

    def doctor(self, *, deep: bool = True) -> DoctorReport:
        return run_doctor(data_dir=self.data_dir, deep=deep)

    def default_device(self) -> str:
        """The runtime a job runs on unless told otherwise.

        The same preference order as the doctor, so what the doctor shows and
        what a job actually uses cannot drift apart.
        """
        recommended = detect_hardware(deep=False).recommended_runtime
        if recommended is RuntimeKind.MPS:
            return "mps"
        if recommended is RuntimeKind.CUDA:
            return "cuda"
        return "cpu"

    # --- settings ---------------------------------------------------------------

    def settings_view(self) -> SettingsView:
        return SettingsView(
            data_dir=str(self.data_dir),
            data_dir_is_default=self.data_dir == default_data_dir(),
            hugging_face_token_set=bool(self._settings.hugging_face_token),
            allow_marginal_models=self._settings.allow_marginal_models,
        )

    def update_settings(
        self,
        *,
        hugging_face_token: str | None = None,
        clear_hugging_face_token: bool = False,
        allow_marginal_models: bool | None = None,
        data_dir: Path | None = None,
    ) -> SettingsView:
        """Persist changed settings. A new data directory applies at next start."""
        update: dict[str, object] = {}
        if clear_hugging_face_token:
            update["hugging_face_token"] = None
        elif hugging_face_token is not None:
            update["hugging_face_token"] = hugging_face_token
        if allow_marginal_models is not None:
            update["allow_marginal_models"] = allow_marginal_models
        if data_dir is not None:
            update["data_dir"] = data_dir
        self._settings = self._settings.model_copy(update=update)
        save_settings(self._settings, self._settings_path)
        return self.settings_view()

    # --- models -----------------------------------------------------------------

    def list_models(self) -> list[ModelEntry]:
        hardware = detect_hardware(data_dir=self.data_dir, deep=False)
        usage = self.models.disk_usage()
        implemented = set(available_provider_ids())
        with self._lock:
            installs = dict(self._installs)
        return [
            ModelEntry(
                metadata=metadata,
                assessment=assess(metadata, hardware),
                install_state=self.models.state(metadata.id),
                disk_bytes=usage.get(metadata.id, 0),
                has_implementation=metadata.id in implemented,
                install=installs.get(metadata.id),
            )
            for metadata in all_models()
        ]

    def get_model(self, model_id: str) -> ModelEntry:
        for entry in self.list_models():
            if entry.metadata.id == model_id:
                return entry
        raise self._unknown_model(model_id)

    def _unknown_model(self, model_id: str) -> ServiceError:
        known = ", ".join(m.id for m in all_models())
        return ServiceError(
            "not_found", f"Unknown model '{model_id}'.", technical=f"Known models: {known}."
        )

    def subscribe_installs(self, listener: InstallListener) -> Callable[[], None]:
        self._install_listeners.append(listener)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._install_listeners.remove(listener)

        return unsubscribe

    def _notify_install(self, task: InstallTask) -> None:
        for listener in list(self._install_listeners):
            with suppress(Exception):
                listener(task)

    def install_model(
        self,
        model_id: str,
        *,
        allow_marginal: bool | None = None,
        hf_token: str | None = None,
    ) -> InstallTask:
        """Start installing in the background. Returns the task to watch.

        Explicit and user-triggered — nothing else in the service downloads.
        """
        if get_model(model_id) is None:
            raise self._unknown_model(model_id)
        if self.models.state(model_id) is InstallState.INSTALLED:
            raise ServiceError("conflict", f"{model_id} is already installed.")

        with self._lock:
            current = self._installs.get(model_id)
            if current is not None and current.state == "running":
                raise ServiceError("conflict", f"{model_id} is already being installed.")
            task = InstallTask(model_id=model_id, step="starting", message="Preparing")
            self._installs[model_id] = task

        token = hf_token or self._settings.hugging_face_token
        marginal = (
            allow_marginal if allow_marginal is not None else self._settings.allow_marginal_models
        )

        def progress(step: str, message: str) -> None:
            updated = task.model_copy(update={"step": step, "message": message})
            with self._lock:
                self._installs[model_id] = updated
            self._notify_install(updated)

        def work() -> None:
            try:
                self.models.install(
                    model_id, on_progress=progress, allow_marginal=marginal, hf_token=token
                )
            except ModelInstallError as error:
                final = task.model_copy(
                    update={
                        "state": "failed",
                        "finished_at": datetime.now(UTC),
                        "error": JobError(
                            message=error.message,
                            technical=error.technical,
                            suggestions=error.suggestions,
                        ),
                    }
                )
            except Exception as error:
                final = task.model_copy(
                    update={
                        "state": "failed",
                        "finished_at": datetime.now(UTC),
                        "error": JobError(
                            message="The install failed unexpectedly.",
                            technical=f"{type(error).__name__}: {error}",
                        ),
                    }
                )
            else:
                final = task.model_copy(
                    update={
                        "state": "done",
                        "step": "done",
                        "message": f"{model_id} is ready",
                        "finished_at": datetime.now(UTC),
                    }
                )
            with self._lock:
                self._installs[model_id] = final
            self._notify_install(final)

        self._notify_install(task)
        self._install_executor.submit(work)
        return task

    def remove_model(self, model_id: str) -> bool:
        if get_model(model_id) is None:
            raise self._unknown_model(model_id)
        with self._lock:
            running = self._installs.get(model_id)
            if running is not None and running.state == "running":
                raise ServiceError("conflict", f"{model_id} is being installed right now.")
            if any(record.model_id == model_id for record in self._running_records()):
                raise ServiceError("conflict", f"{model_id} is being used by a running job.")
            self._installs.pop(model_id, None)
        return self.models.remove(model_id)

    def _running_records(self) -> list[JobRecord]:
        records = (self.jobs.get(job_id) for job_id in self._running)
        return [record for record in records if record is not None]

    # --- jobs -------------------------------------------------------------------

    def prepare_job(self, request: JobRequest) -> JobSubmission:
        """Validate, stage the inputs, create the record. Does not run anything."""
        metadata = get_model(request.model_id)
        if metadata is None:
            raise self._unknown_model(request.model_id)
        if self.models.state(request.model_id) is not InstallState.INSTALLED:
            raise ServiceError(
                "conflict",
                f"{request.model_id} is not installed.",
                suggestions=[f"Install it first: volum models install {request.model_id}"],
            )
        if request.model_id not in available_provider_ids():
            raise ServiceError(
                "unavailable",
                f"'{request.model_id}' is in the catalogue but has no implementation yet.",
                technical=f"Available: {', '.join(available_provider_ids())}.",
            )

        try:
            export_options, warnings = resolve_export_options(
                request.formats, for_print=request.for_print, target_size_mm=request.target_size_mm
            )
        except UnknownFormatError as error:
            raise ServiceError("invalid", str(error)) from error

        if len(request.images) > 1 and Capability.MULTI_IMAGE not in metadata.capabilities:
            # Said out loud, not quietly done: using the first image silently
            # would be the simulated multi-image support the spec forbids.
            warnings.append(
                f"{metadata.name} uses a single image. The first of {len(request.images)} "
                "will be used; the others are ignored."
            )

        record = self.jobs.create(
            model_id=request.model_id,
            input_files=[],
            parameters=dict(request.parameters),
            seed=request.seed,
            model_version=metadata.version,
            runtime=request.device or self.default_device(),
            export_formats=[fmt.value for fmt in export_options.formats],
            target_size_mm=export_options.target_size_mm,
            single_part=request.single_part,
        )
        try:
            staged = stage_inputs(request.images, self.jobs_dir / record.id / "input")
        except InputError as error:
            # Nothing half-made stays behind: the record goes with its inputs.
            self.jobs._store.delete(record.id)
            raise ServiceError(
                "invalid", error.message, technical=error.technical, suggestions=error.suggestions
            ) from error

        record.input_files = [info.path for info in staged]
        record.input_hashes = [info.sha256 for info in staged]
        self.jobs._store.save(record)
        return JobSubmission(job=record, warnings=warnings)

    def submit_job(self, request: JobRequest) -> JobSubmission:
        """Prepare and schedule. The job runs when the worker thread is free."""
        submission = self.prepare_job(request)
        job_id = submission.job.id
        future = self._job_executor.submit(self.run_job, job_id)
        with self._lock:
            self._futures[job_id] = future
        return submission

    def run_job(
        self,
        job_id: str,
        *,
        output_dir: Path | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> PipelineResult:
        """Run a prepared job to completion on the calling thread."""
        record = self.jobs.get(job_id)
        if record is None:
            raise ServiceError("not_found", f"No job '{job_id}'.")
        if record.status.is_terminal:
            # Cancelled while queued, most likely. Nothing to do and nothing
            # to report — the record already says what happened.
            return PipelineResult(job=record)

        provider = self._provider_factory(
            record.model_id, self.models, device=record.runtime or "cpu"
        )
        if provider is None:
            self.jobs.fail(
                record,
                JobError(
                    message=f"'{record.model_id}' has no implementation on this build.",
                    technical=f"Available: {', '.join(available_provider_ids())}.",
                ),
            )
            return PipelineResult(job=record)

        export_options, _ = resolve_export_options(
            record.export_formats, for_print=False, target_size_mm=record.target_size_mm
        )
        with self._lock:
            self._running[job_id] = provider
        try:
            return run_pipeline(
                manager=self.jobs,
                provider=provider,
                record=record,
                images=list(record.input_files),
                output_dir=output_dir or (self.jobs_dir / job_id / "output"),
                parameters=dict(record.parameters),
                seed=record.seed,
                export=export_options,
                drop_loose_parts=record.single_part,
                on_stage=on_stage,
            )
        finally:
            with self._lock:
                self._running.pop(job_id, None)
                self._futures.pop(job_id, None)

    def cancel_job(self, job_id: str) -> JobRecord | None:
        """Mark the job cancelled and stop its worker. ``None`` if unknown."""
        record = self.jobs.cancel(job_id) if _is_job_id(job_id) else None
        if record is None:
            return None
        with self._lock:
            provider = self._running.get(job_id)
        if provider is not None:
            provider.cancel()
        return record

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id) if _is_job_id(job_id) else None

    def list_jobs(self, *, limit: int | None = None) -> list[JobRecord]:
        return self.jobs.list(limit=limit)

    def subscribe_jobs(self, listener: Callable[[JobRecord], None]) -> Callable[[], None]:
        return self.jobs.subscribe(listener)

    def artifact_path(self, job_id: str, name: str) -> Path:
        """Where a named artifact of a job is — only ever inside that job's directory.

        A record is data, and data can be wrong or tampered with. Even one that
        points at ``/etc/passwd`` does not make the engine read it (spec
        section 50).
        """
        record = self.get_job(job_id)
        if record is None:
            raise ServiceError("not_found", f"No job '{job_id}'.")
        path = record.artifacts.get(name)
        if path is None:
            raise ServiceError(
                "not_found",
                f"Job {job_id} has no artifact '{name}'.",
                technical=f"Available: {', '.join(sorted(record.artifacts)) or 'none'}.",
            )
        if not is_within(path, self.jobs_dir / job_id) or not path.is_file():
            raise ServiceError(
                "not_found",
                f"The artifact '{name}' is not available.",
                technical="The recorded path is outside the job directory or missing.",
            )
        return path

    # --- lifecycle --------------------------------------------------------------

    def shutdown(self, *, wait_seconds: float = 10.0) -> None:
        """Cancel running work and stop the worker threads."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            running = list(self._running)
            futures = list(self._futures.values())
        for job_id in running:
            with suppress(Exception):
                self.cancel_job(job_id)
        for future in futures:
            with suppress(Exception):
                future.result(timeout=wait_seconds)
        self._job_executor.shutdown(wait=False, cancel_futures=True)
        self._install_executor.shutdown(wait=False, cancel_futures=True)


def _is_job_id(value: str) -> bool:
    """Job ids are UUID hex; anything else never reaches the filesystem."""
    return value.isalnum()


__all__ = [
    "InstallTask",
    "JobRequest",
    "JobSubmission",
    "ModelEntry",
    "ServiceError",
    "SettingsView",
    "VolumService",
]
