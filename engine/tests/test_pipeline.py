"""Pipeline tests.

The provider here is a test double, which is legitimate: these tests are about
the *pipeline's* behaviour — stage transitions, validation gating, metadata,
cancellation — not about a model. The production path never sees it (spec
section 37); the real provider is exercised by the GPU smoke test.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from volum_core.export import PRINT_FORMATS, ExportFormat, ExportOptions
from volum_core.jobs import JobManager, JobStatus, JobStore
from volum_core.models.registry import TRIPOSR
from volum_core.pipeline import PipelineResult, read_asset_metadata, run_pipeline
from volum_core.providers.types import (
    EnvironmentReport,
    GenerationRequest,
    GenerationResult,
    ImageTo3DProvider,
    ProgressCallback,
    ProviderMetadata,
    ResourceEstimate,
)
from volum_core.providers.worker_provider import ProviderExecutionError


class FakeProvider(ImageTo3DProvider):
    """Writes a real mesh, or fails, or produces nothing — on demand."""

    def __init__(self, *, mode: str = "good") -> None:
        self.mode = mode
        self.cleaned_up = False
        self.cancelled = False

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
        on_progress("preprocessing", None, "Preparing")
        on_progress("reconstructing", None, "Reconstructing")

        if self.mode == "raise":
            raise ProviderExecutionError("The model failed.", "technical detail", ["retry"])

        target = request.output_dir / "model.glb"
        if self.mode == "empty":
            # The documented upstream failure: success reported, nothing produced.
            mesh = trimesh.Trimesh(
                vertices=np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]]),
                faces=np.array([[0, 1, 2]]),
            )
        else:
            mesh = trimesh.creation.icosphere(subdivisions=2)
        mesh.export(target)

        on_progress("optimizing", None, "Writing")
        return GenerationResult(
            mesh_path=target, seed=request.seed, runtime="cpu", duration_seconds=0.1
        )

    def cancel(self) -> None:
        self.cancelled = True

    def cleanup(self) -> None:
        self.cleaned_up = True


@pytest.fixture
def manager(tmp_path: Path) -> JobManager:
    return JobManager(JobStore(tmp_path / "jobs"))


def _run(
    manager: JobManager, provider: FakeProvider, tmp_path: Path, **kwargs: object
) -> PipelineResult:
    record = manager.create(model_id="triposr", input_files=[tmp_path / "in.png"])
    return run_pipeline(
        manager=manager,
        provider=provider,
        record=record,
        images=[tmp_path / "in.png"],
        output_dir=tmp_path / "out",
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_successful_run_completes(manager: JobManager, tmp_path: Path) -> None:
    result = _run(manager, FakeProvider(), tmp_path)
    assert result.job.status is JobStatus.COMPLETED
    assert result.report is not None and result.report.valid


def test_it_walks_the_real_stages(manager: JobManager, tmp_path: Path) -> None:
    """Progress comes from stage transitions, not a percentage (spec section 18)."""
    result = _run(manager, FakeProvider(), tmp_path)
    stages = [entry.stage for entry in result.job.progress]
    assert JobStatus.RECONSTRUCTING in stages
    assert JobStatus.VALIDATING in stages
    assert stages[-1] is JobStatus.COMPLETED


def test_it_writes_all_three_artifacts(manager: JobManager, tmp_path: Path) -> None:
    result = _run(manager, FakeProvider(), tmp_path)
    assert set(result.job.artifacts) == {"mesh", "quality_report", "asset"}
    for path in result.job.artifacts.values():
        assert path.exists()


def test_asset_metadata_records_how_it_was_made(manager: JobManager, tmp_path: Path) -> None:
    _run(manager, FakeProvider(), tmp_path, seed=1234)
    metadata = read_asset_metadata(tmp_path / "out" / "asset.json")
    assert metadata is not None
    assert metadata.seed == 1234
    assert metadata.model == "triposr"
    assert metadata.hardware["os"]


def test_metadata_states_the_reproducibility_limit(manager: JobManager, tmp_path: Path) -> None:
    """MPS and CUDA kernels differ and the Apple path substitutes implementations,
    so cross-machine reproduction is not something VOLUM may promise."""
    result = _run(manager, FakeProvider(), tmp_path)
    assert result.metadata is not None
    assert "same machine" in result.metadata.reproducibility_note


def test_an_invalid_asset_fails_the_job(manager: JobManager, tmp_path: Path) -> None:
    """A model that produces nothing while reporting success must not yield an
    asset. This is the silent-empty-mesh case, documented upstream."""
    result = _run(manager, FakeProvider(mode="empty"), tmp_path)
    assert result.job.status is JobStatus.FAILED
    assert result.report is not None and not result.report.valid
    assert result.job.error is not None


def test_a_failed_validation_still_writes_the_report(manager: JobManager, tmp_path: Path) -> None:
    """The evidence for the refusal has to survive it."""
    _run(manager, FakeProvider(mode="empty"), tmp_path)
    assert (tmp_path / "out" / "quality_report.json").exists()


def test_a_provider_error_becomes_a_readable_job_error(manager: JobManager, tmp_path: Path) -> None:
    result = _run(manager, FakeProvider(mode="raise"), tmp_path)
    assert result.job.status is JobStatus.FAILED
    assert result.job.error is not None
    assert result.job.error.message == "The model failed."
    assert result.job.error.technical == "technical detail"
    assert result.job.error.suggestions == ["retry"]


def test_cleanup_runs_even_when_generation_fails(manager: JobManager, tmp_path: Path) -> None:
    """Otherwise a failed job leaks whatever the provider was holding."""
    provider = FakeProvider(mode="raise")
    _run(manager, provider, tmp_path)
    assert provider.cleaned_up


def test_cancelling_before_the_provider_runs_stops_the_job(
    manager: JobManager, tmp_path: Path
) -> None:
    provider = FakeProvider()
    record = manager.create(model_id="triposr", input_files=[])
    manager.token(record.id).cancel()
    result = run_pipeline(
        manager=manager,
        provider=provider,
        record=record,
        images=[tmp_path / "in.png"],
        output_dir=tmp_path / "out",
    )
    assert result.job.status is JobStatus.CANCELLED
    assert provider.cancelled
    assert provider.cleaned_up


# --- the print path -------------------------------------------------------


class OpenMeshProvider(FakeProvider):
    """Produces a mesh with a hole — acceptable to render, not to print."""

    def generate(
        self, request: GenerationRequest, on_progress: ProgressCallback
    ) -> GenerationResult:
        on_progress("reconstructing", None, "Reconstructing")
        sphere = trimesh.creation.icosphere(subdivisions=3)
        open_mesh = trimesh.Trimesh(vertices=sphere.vertices, faces=sphere.faces[:-6])
        target = request.output_dir / "model.glb"
        open_mesh.export(target)
        return GenerationResult(mesh_path=target, runtime="cpu", duration_seconds=0.1)


def test_a_render_target_leaves_the_mesh_alone(manager: JobManager, tmp_path: Path) -> None:
    result = _run(manager, OpenMeshProvider(), tmp_path)
    assert result.job.status is JobStatus.COMPLETED
    assert result.repair is None
    assert result.report is not None and not result.report.checked_for_printing


def test_a_print_target_repairs_and_exports(manager: JobManager, tmp_path: Path) -> None:
    result = _run(
        manager,
        OpenMeshProvider(),
        tmp_path,
        export=ExportOptions(formats=PRINT_FORMATS, target_size_mm=50.0),
    )
    assert result.job.status is JobStatus.COMPLETED
    assert result.repair is not None
    assert result.repair.watertight_before is False
    assert result.repair.watertight_after is True
    assert {e.format for e in result.exports} == set(PRINT_FORMATS)
    for exported in result.exports:
        assert exported.path.exists()


def test_the_print_export_is_scaled(manager: JobManager, tmp_path: Path) -> None:
    result = _run(
        manager,
        OpenMeshProvider(),
        tmp_path,
        export=ExportOptions(formats=(ExportFormat.STL,), target_size_mm=50.0),
    )
    back = trimesh.load(result.exports[0].path, force="mesh")
    assert float(np.max(back.extents)) == pytest.approx(50.0, rel=1e-3)


def test_the_generator_output_is_kept_alongside_the_repair(
    manager: JobManager, tmp_path: Path
) -> None:
    """A user comparing the repaired model against what the model actually
    produced needs both files."""
    _run(
        manager,
        OpenMeshProvider(),
        tmp_path,
        export=ExportOptions(formats=PRINT_FORMATS),
    )
    assert (tmp_path / "out" / "model.glb").exists()
    assert (tmp_path / "out" / "model_printable.glb").exists()


def test_a_repair_report_is_written(manager: JobManager, tmp_path: Path) -> None:
    result = _run(
        manager, OpenMeshProvider(), tmp_path, export=ExportOptions(formats=PRINT_FORMATS)
    )
    assert (tmp_path / "out" / "repair_report.json").exists()
    assert "repair_report" in result.job.artifacts


def test_the_metadata_records_the_print_settings(manager: JobManager, tmp_path: Path) -> None:
    _run(
        manager,
        OpenMeshProvider(),
        tmp_path,
        export=ExportOptions(formats=PRINT_FORMATS, target_size_mm=42.0),
    )
    metadata = read_asset_metadata(tmp_path / "out" / "asset.json")
    assert metadata is not None
    assert metadata.target_size_mm == 42.0
    assert set(metadata.export_formats) == {"stl", "3mf"}
    assert metadata.repair_strategy


def test_the_stage_machine_survives_a_provider_naming_the_same_stage(
    manager: JobManager, tmp_path: Path
) -> None:
    """The provider reports 'optimizing' for mesh extraction and the repair stage
    is also optimizing. Advancing twice tripped the state machine, which was
    right to refuse — reporting instead keeps the message without faking a move."""

    class OptimizingProvider(OpenMeshProvider):
        def generate(
            self, request: GenerationRequest, on_progress: ProgressCallback
        ) -> GenerationResult:
            result = super().generate(request, on_progress)
            on_progress("optimizing", None, "Extracting the mesh")
            return result

    result = _run(
        manager, OptimizingProvider(), tmp_path, export=ExportOptions(formats=PRINT_FORMATS)
    )
    assert result.job.status is JobStatus.COMPLETED
