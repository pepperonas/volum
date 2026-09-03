"""The application service: what both front doors call.

The CLI and the HTTP engine are thin shells over ``VolumService`` (spec section
32). These tests pin the behaviour they share — validation, staging, scheduling,
cancellation, artifact access — with a test-double provider, which is the only
place one is allowed (spec section 37).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import trimesh

from conftest import make_png
from volum_core.jobs import JobRecord, JobStatus
from volum_core.models.manager import (
    MANIFEST_FILE,
    InstallManifest,
    ModelInstallError,
    ModelManager,
)
from volum_core.models.registry import TRIPOSR
from volum_core.providers.types import (
    EnvironmentReport,
    GenerationRequest,
    GenerationResult,
    ImageTo3DProvider,
    ProgressCallback,
    ProviderMetadata,
    ResourceEstimate,
)
from volum_core.service import JobRequest, ServiceError, VolumService


class SlowFakeProvider(ImageTo3DProvider):
    """Writes a real mesh after waiting on an event, so tests control timing."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.cancelled = False
        self.cleaned_up = False

    @property
    def metadata(self) -> ProviderMetadata:
        return TRIPOSR

    def validate_environment(self) -> EnvironmentReport:
        return EnvironmentReport(ok=True)

    def estimate_resources(self, request: GenerationRequest) -> ResourceEstimate:
        return ResourceEstimate(peak_memory_bytes=1, disk_bytes=1)

    def generate(
        self, request: GenerationRequest, on_progress: ProgressCallback
    ) -> GenerationResult:
        on_progress("reconstructing", None, "Reconstructing")
        self.started.set()
        self.release.wait(timeout=10)
        target = request.output_dir / "model.glb"
        trimesh.creation.icosphere(subdivisions=2).export(target)
        return GenerationResult(mesh_path=target, runtime="cpu", duration_seconds=0.1)

    def cancel(self) -> None:
        self.cancelled = True
        self.release.set()

    def cleanup(self) -> None:
        self.cleaned_up = True


def mark_installed(models: ModelManager, model_id: str = "triposr") -> None:
    directory = models.directory(model_id)
    interpreter = models.python_executable(model_id)
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.touch()
    (directory / MANIFEST_FILE).write_text(
        InstallManifest(
            model_id=model_id, volum_version="0.1.0", python_version="3.11"
        ).model_dump_json(),
        encoding="utf-8",
    )


@pytest.fixture
def provider() -> SlowFakeProvider:
    return SlowFakeProvider()


@pytest.fixture
def service(tmp_path: Path, provider: SlowFakeProvider) -> Iterator[VolumService]:
    svc = VolumService(
        data_dir=tmp_path / "data",
        provider_factory=lambda model_id, manager, *, device: provider,
    )
    mark_installed(svc.models)
    yield svc
    provider.release.set()
    svc.shutdown()


@pytest.fixture
def image(tmp_path: Path) -> Path:
    return make_png(tmp_path / "photo.png")


def _wait_for(record_getter, status: JobStatus, timeout: float = 10.0) -> JobRecord:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = record_getter()
        if record is not None and record.status is status:
            return record
        time.sleep(0.02)
    raise AssertionError(f"job did not reach {status}: {record_getter()}")


# --- layout -------------------------------------------------------------------


def test_the_service_creates_the_data_layout(tmp_path: Path) -> None:
    svc = VolumService(data_dir=tmp_path / "fresh")
    try:
        for name in ("models", "jobs", "projects", "cache", "logs", "exports"):
            assert (tmp_path / "fresh" / name).is_dir()
    finally:
        svc.shutdown()


def test_interrupted_jobs_are_failed_at_start_up(tmp_path: Path) -> None:
    """A job left 'reconstructing' by a crash must not spin for ever."""
    first = VolumService(data_dir=tmp_path / "d")
    record = first.jobs.create(model_id="triposr", input_files=[])
    first.jobs.advance(record, JobStatus.RECONSTRUCTING)
    first.shutdown()

    second = VolumService(data_dir=tmp_path / "d", recover_interrupted=True)
    try:
        loaded = second.get_job(record.id)
        assert loaded is not None and loaded.status is JobStatus.FAILED
        assert loaded.error is not None and "interrupted" in loaded.error.message
    finally:
        second.shutdown()


def test_a_second_process_does_not_fail_a_running_job_by_default(tmp_path: Path) -> None:
    """The CLI beside a live engine must not declare the engine's jobs dead."""
    engine = VolumService(data_dir=tmp_path / "d")
    record = engine.jobs.create(model_id="triposr", input_files=[])
    engine.jobs.advance(record, JobStatus.RECONSTRUCTING)

    cli = VolumService(data_dir=tmp_path / "d")
    try:
        loaded = cli.get_job(record.id)
        assert loaded is not None and loaded.status is JobStatus.RECONSTRUCTING
    finally:
        cli.shutdown()
        engine.shutdown()


# --- preparing a job -------------------------------------------------------------


def test_prepare_stages_inputs_and_records_their_hashes(service: VolumService, image: Path) -> None:
    submission = service.prepare_job(JobRequest(model_id="triposr", images=[image]))
    job = submission.job

    assert job.status is JobStatus.QUEUED
    assert len(job.input_files) == 1
    staged = job.input_files[0]
    assert staged != image
    assert staged.parent == service.jobs_dir / job.id / "input"
    assert staged.read_bytes() == image.read_bytes()
    assert len(job.input_hashes) == 1 and len(job.input_hashes[0]) == 64


def test_prepare_refuses_an_uninstalled_model(service: VolumService, image: Path) -> None:
    with pytest.raises(ServiceError) as excinfo:
        service.prepare_job(JobRequest(model_id="trellis2", images=[image]))
    assert excinfo.value.kind == "conflict"
    assert "not installed" in excinfo.value.message


def test_prepare_refuses_an_unknown_model(service: VolumService, image: Path) -> None:
    with pytest.raises(ServiceError) as excinfo:
        service.prepare_job(JobRequest(model_id="nope", images=[image]))
    assert excinfo.value.kind == "not_found"


def test_prepare_refuses_a_bad_image_with_the_input_message(
    service: VolumService, tmp_path: Path
) -> None:
    fake = tmp_path / "x.png"
    fake.write_text("not an image", encoding="utf-8")
    with pytest.raises(ServiceError) as excinfo:
        service.prepare_job(JobRequest(model_id="triposr", images=[fake]))
    assert excinfo.value.kind == "invalid"
    assert "PNG, JPEG or WebP" in excinfo.value.message
    # Nothing half-made is left behind.
    assert not any(service.jobs_dir.iterdir())


def test_prepare_warns_when_a_single_image_model_gets_several(
    service: VolumService, image: Path, tmp_path: Path
) -> None:
    second = make_png(tmp_path / "second.png")
    submission = service.prepare_job(JobRequest(model_id="triposr", images=[image, second]))
    assert any("single image" in w for w in submission.warnings)
    # Both are still staged: the record says what the user gave, honestly.
    assert len(submission.job.input_files) == 2


def test_prepare_resolves_print_output(service: VolumService, image: Path) -> None:
    submission = service.prepare_job(
        JobRequest(model_id="triposr", images=[image], for_print=True, target_size_mm=40)
    )
    assert submission.job.export_formats == ["stl", "3mf"]
    assert submission.job.target_size_mm == 40
    assert submission.warnings == []


def test_prepare_rejects_an_unknown_format_as_invalid(service: VolumService, image: Path) -> None:
    with pytest.raises(ServiceError) as excinfo:
        service.prepare_job(JobRequest(model_id="triposr", images=[image], formats=["fbx"]))
    assert excinfo.value.kind == "invalid"


def test_the_device_is_the_doctor_s_recommendation_unless_overridden(
    service: VolumService, image: Path
) -> None:
    default = service.prepare_job(JobRequest(model_id="triposr", images=[image])).job
    assert default.runtime in {"mps", "cuda", "cpu"}
    forced = service.prepare_job(JobRequest(model_id="triposr", images=[image], device="cpu")).job
    assert forced.runtime == "cpu"


# --- running ---------------------------------------------------------------------


def test_run_job_completes_synchronously(
    service: VolumService, image: Path, provider: SlowFakeProvider
) -> None:
    provider.release.set()
    job = service.prepare_job(JobRequest(model_id="triposr", images=[image])).job
    result = service.run_job(job.id)
    assert result.job.status is JobStatus.COMPLETED
    assert result.job.artifacts["mesh"].exists()
    assert result.job.artifacts["mesh"].is_relative_to(service.jobs_dir / job.id)


def test_submit_runs_in_the_background_and_streams_updates(
    service: VolumService, image: Path, provider: SlowFakeProvider
) -> None:
    seen: list[JobStatus] = []
    service.subscribe_jobs(lambda record: seen.append(record.status))

    job = service.submit_job(JobRequest(model_id="triposr", images=[image])).job
    assert provider.started.wait(timeout=10)
    assert service.get_job(job.id).status is JobStatus.RECONSTRUCTING  # type: ignore[union-attr]

    provider.release.set()
    final = _wait_for(lambda: service.get_job(job.id), JobStatus.COMPLETED)
    assert final.artifacts["mesh"].exists()
    assert seen[0] is JobStatus.QUEUED
    assert JobStatus.RECONSTRUCTING in seen
    assert seen[-1] is JobStatus.COMPLETED


def test_jobs_run_one_at_a_time(
    service: VolumService, image: Path, provider: SlowFakeProvider
) -> None:
    """One GPU, one job. The second waits as QUEUED — visibly, not silently."""
    first = service.submit_job(JobRequest(model_id="triposr", images=[image])).job
    second = service.submit_job(JobRequest(model_id="triposr", images=[image])).job
    assert provider.started.wait(timeout=10)

    assert service.get_job(second.id).status is JobStatus.QUEUED  # type: ignore[union-attr]

    provider.release.set()
    _wait_for(lambda: service.get_job(first.id), JobStatus.COMPLETED)
    _wait_for(lambda: service.get_job(second.id), JobStatus.COMPLETED)


def test_cancelling_a_running_job_terminates_the_provider(
    service: VolumService, image: Path, provider: SlowFakeProvider
) -> None:
    job = service.submit_job(JobRequest(model_id="triposr", images=[image])).job
    assert provider.started.wait(timeout=10)

    cancelled = service.cancel_job(job.id)

    assert cancelled is not None and cancelled.status is JobStatus.CANCELLED
    assert provider.cancelled
    final = _wait_for(lambda: service.get_job(job.id), JobStatus.CANCELLED)
    assert final.error is None
    assert "mesh" not in final.artifacts


def test_cancelling_a_queued_job_never_runs_it(
    service: VolumService, image: Path, provider: SlowFakeProvider
) -> None:
    first = service.submit_job(JobRequest(model_id="triposr", images=[image])).job
    second = service.submit_job(JobRequest(model_id="triposr", images=[image])).job
    assert provider.started.wait(timeout=10)

    service.cancel_job(second.id)
    provider.started.clear()
    provider.release.set()
    _wait_for(lambda: service.get_job(first.id), JobStatus.COMPLETED)

    # Give the executor a moment: if it were going to run the second job it
    # would signal `started` again.
    assert not provider.started.wait(timeout=0.5)
    assert service.get_job(second.id).status is JobStatus.CANCELLED  # type: ignore[union-attr]


def test_cancelling_an_unknown_job_is_none(service: VolumService) -> None:
    assert service.cancel_job("deadbeef") is None


def test_shutdown_cancels_running_work(tmp_path: Path, image: Path) -> None:
    provider = SlowFakeProvider()
    svc = VolumService(data_dir=tmp_path / "data", provider_factory=lambda *_, device: provider)
    mark_installed(svc.models)
    job = svc.submit_job(JobRequest(model_id="triposr", images=[image])).job
    assert provider.started.wait(timeout=10)

    svc.shutdown()

    assert provider.cancelled
    assert svc.get_job(job.id).status is JobStatus.CANCELLED  # type: ignore[union-attr]


# --- artifacts -------------------------------------------------------------------


def test_artifacts_are_served_by_name_from_inside_the_job(
    service: VolumService, image: Path, provider: SlowFakeProvider
) -> None:
    provider.release.set()
    job = service.prepare_job(JobRequest(model_id="triposr", images=[image])).job
    service.run_job(job.id)

    path = service.artifact_path(job.id, "mesh")
    assert path.exists() and path.suffix == ".glb"
    with pytest.raises(ServiceError) as excinfo:
        service.artifact_path(job.id, "nope")
    assert excinfo.value.kind == "not_found"


def test_an_artifact_outside_the_job_directory_is_never_served(
    service: VolumService, image: Path, provider: SlowFakeProvider, tmp_path: Path
) -> None:
    """Path traversal protection (spec section 50): even a record that claims an
    artifact lives elsewhere does not make the engine read it."""
    provider.release.set()
    job = service.prepare_job(JobRequest(model_id="triposr", images=[image])).job
    service.run_job(job.id)
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    record = service.get_job(job.id)
    assert record is not None
    record.artifacts["leak"] = outside
    service.jobs.advance  # noqa: B018 - the record is terminal; save it directly
    service.jobs._store.save(record)

    with pytest.raises(ServiceError) as excinfo:
        service.artifact_path(job.id, "leak")
    assert excinfo.value.kind == "not_found"


def test_job_ids_from_the_outside_are_validated(service: VolumService) -> None:
    with pytest.raises(ServiceError) as excinfo:
        service.artifact_path("../etc", "mesh")
    assert excinfo.value.kind == "not_found"
    assert service.get_job("../etc") is None


# --- models -----------------------------------------------------------------------


def test_model_entries_combine_catalogue_state_and_verdict(service: VolumService) -> None:
    entries = {entry.metadata.id: entry for entry in service.list_models()}
    assert entries["triposr"].install_state.value == "installed"
    assert entries["trellis2"].install_state.value == "not_installed"
    assert entries["triposr"].has_implementation
    assert entries["triposr"].assessment.provider_id == "triposr"


def test_an_unknown_model_is_not_found(service: VolumService) -> None:
    with pytest.raises(ServiceError) as excinfo:
        service.get_model("nope")
    assert excinfo.value.kind == "not_found"


def test_installing_reports_progress_and_finishes(
    service: VolumService, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps: list[str] = []
    service.subscribe_installs(lambda task: steps.append(f"{task.state}:{task.step}"))

    def fake_install(model_id: str, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["on_progress"]("weights", "Downloading")
        mark_installed(service.models, model_id)
        return InstallManifest(model_id=model_id, volum_version="0.1.0", python_version="3.11")

    monkeypatch.setattr(service.models, "install", fake_install)

    task = service.install_model("trellis2")
    deadline = time.monotonic() + 10
    while task.state == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
        task = service.get_model("trellis2").install  # type: ignore[assignment]

    assert task is not None and task.state == "done"
    assert "running:weights" in steps
    assert steps[-1].startswith("done")
    assert service.get_model("trellis2").install_state.value == "installed"


def test_only_one_install_runs_at_a_time(
    service: VolumService, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()

    def slow_install(model_id: str, **kwargs):  # type: ignore[no-untyped-def]
        gate.wait(timeout=10)
        return InstallManifest(model_id=model_id, volum_version="0.1.0", python_version="3.11")

    monkeypatch.setattr(service.models, "install", slow_install)
    service.install_model("trellis2")
    with pytest.raises(ServiceError) as excinfo:
        service.install_model("trellis2")
    assert excinfo.value.kind == "conflict"
    gate.set()


def test_a_failed_install_is_reported_not_raised_from_the_thread(
    service: VolumService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_install(model_id: str, **kwargs):  # type: ignore[no-untyped-def]
        raise ModelInstallError("No space left.", "ENOSPC", ["Free up space."])

    monkeypatch.setattr(service.models, "install", broken_install)
    service.install_model("trellis2")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        task = service.get_model("trellis2").install
        if task is not None and task.state != "running":
            break
        time.sleep(0.02)
    assert task is not None and task.state == "failed"
    assert task.error is not None and task.error.message == "No space left."
    assert task.error.suggestions == ["Free up space."]


def test_removing_a_model_that_is_not_installed_is_false(service: VolumService) -> None:
    assert service.remove_model("trellis2") is False


# --- settings ---------------------------------------------------------------------


def test_settings_never_expose_the_token(service: VolumService) -> None:
    service.update_settings(hugging_face_token="hf_secret")  # noqa: S106 - a test value
    view = service.settings_view()
    assert view.hugging_face_token_set is True
    assert "hf_secret" not in view.model_dump_json()
