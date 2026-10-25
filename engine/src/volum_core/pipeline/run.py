"""The pipeline: from images to a validated asset.

One function, because there is one path. Each stage is a separate, testable
step, and the job is advanced through real stages rather than a progress
percentage nobody can justify (spec sections 18, 19).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, Field

from ..export import ExportedFile, ExportFormat, ExportOptions, export_mesh
from ..hardware.detect import detect_hardware
from ..jobs.manager import CancellationToken, JobCancelledError, JobManager
from ..jobs.types import InvalidTransitionError, JobError, JobRecord, JobStatus, can_transition
from ..preprocess import BackgroundOutcome, BackgroundRemoval, BackgroundRemovalError
from ..providers.types import GenerationRequest, GenerationResult, ImageTo3DProvider
from ..providers.worker_provider import ProviderExecutionError
from ..validation import QualityReport, validate_asset
from ..version import __version__
from .repair import RepairReport, repair_for_printing

if TYPE_CHECKING:
    import trimesh

#: Maps a provider's stage name onto the job state machine. Anything a provider
#: reports that is not in here becomes an in-stage message rather than a
#: transition, so a new provider cannot invent job states.
_STAGE_MAP = {
    "preprocessing": JobStatus.PREPROCESSING,
    "loading": JobStatus.PREPROCESSING,
    "reconstructing": JobStatus.RECONSTRUCTING,
    "texturing": JobStatus.TEXTURING,
    "optimizing": JobStatus.OPTIMIZING,
}


#: Prepares the images a generator will see. Given the staged inputs and the
#: job's output directory, returns what to generate from and what it did. The
#: pipeline owns the *stage*; what happens inside it is the service's decision,
#: which is why this arrives as a callable rather than a flag.
Preprocessor = Callable[[list[Path], Path], tuple[list[Path], "BackgroundRemoval"]]


class AssetMetadata(BaseModel):
    """``asset.json`` — everything needed to understand and re-run this result."""

    id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    volum_version: str = __version__
    source_images: list[str] = Field(default_factory=list)
    source_image_sha256: list[str] = Field(default_factory=list)
    export_formats: list[str] = Field(default_factory=list)
    target_size_mm: float | None = None
    repair_strategy: str | None = None
    #: What the preprocessing stage did. Absent on results made before the stage
    #: existed, which is why it is optional rather than defaulted to "not done".
    background_removal: BackgroundRemoval | None = None
    model: str
    model_version: str | None = None
    runtime: str | None = None
    seed: int | None = None
    parameters: dict[str, object] = Field(default_factory=dict)
    processing_time_seconds: float | None = None
    hardware: dict[str, object] = Field(default_factory=dict)
    reproducibility_note: str = (
        "Bit-identical reproduction is expected only on the same machine with the "
        "same runtime. MPS and CUDA kernels differ, and the Apple Silicon path "
        "substitutes attention, GEMM and rasterisation implementations."
    )


class PipelineResult(BaseModel):
    job: JobRecord
    report: QualityReport | None = None
    metadata: AssetMetadata | None = None
    repair: RepairReport | None = None
    exports: list[ExportedFile] = Field(default_factory=list)


def _record_success(  # noqa: PLR0913 - a stage boundary; all keyword-only
    *,
    manager: JobManager,
    record: JobRecord,
    provider: ImageTo3DProvider,
    images: list[Path],
    output_dir: Path,
    asset_path: Path,
    report: QualityReport,
    repair_report: RepairReport | None,
    export_options: ExportOptions,
    parameters: dict[str, object],
    seed: int | None,
    result: GenerationResult,
    background: BackgroundRemoval | None,
    token: CancellationToken,
    on_stage: Callable[[str], None] | None,
) -> PipelineResult:
    """Export, write the metadata, complete the job."""
    exports = _export_asset(asset_path, output_dir, export_options)
    # Exporting can take a while; a cancel that landed meanwhile must not be
    # followed by artifacts appearing on a cancelled record.
    token.raise_if_cancelled()

    hardware = detect_hardware()
    metadata = AssetMetadata(
        id=record.id,
        source_images=[str(path) for path in images],
        source_image_sha256=list(record.input_hashes),
        export_formats=[fmt.value for fmt in export_options.formats],
        target_size_mm=export_options.target_size_mm,
        repair_strategy=repair_report.strategy.value if repair_report else None,
        background_removal=background,
        model=record.model_id,
        model_version=provider.metadata.version,
        runtime=result.runtime,
        seed=result.seed if result.seed is not None else seed,
        parameters={**parameters, **result.parameters},
        processing_time_seconds=result.duration_seconds,
        hardware={
            "os": hardware.os,
            "arch": hardware.arch,
            "cpu": hardware.cpu_name,
            "gpu": hardware.gpus[0].name if hardware.gpus else None,
            "memory_bytes": hardware.ram_total_bytes,
        },
    )
    (output_dir / "asset.json").write_text(metadata.model_dump_json(indent=2), encoding="utf-8")

    record.artifacts = {
        "mesh": asset_path,
        "quality_report": output_dir / "quality_report.json",
        "asset": output_dir / "asset.json",
        **{f"export_{f.format.value}": f.path for f in exports},
    }
    if repair_report is not None:
        (output_dir / "repair_report.json").write_text(
            repair_report.model_dump_json(indent=2), encoding="utf-8"
        )
        record.artifacts["repair_report"] = output_dir / "repair_report.json"
    record.runtime = result.runtime
    record.seed = metadata.seed

    manager.advance(record, JobStatus.COMPLETED, message="Done")
    if on_stage is not None:
        on_stage(JobStatus.COMPLETED.value)
    return PipelineResult(
        job=record, report=report, metadata=metadata, repair=repair_report, exports=exports
    )


class _StageReporter:
    """Moves a job through stages, or reports inside one when a move is illegal.

    Providers report their own stages, and the pipeline has stages of its own;
    the two can name the same one. Advancing blindly then trips the state
    machine, which is right to refuse — a stage that re-enters itself would read
    as progress while nothing moved. Reporting instead keeps the message without
    faking a transition.
    """

    def __init__(
        self,
        manager: JobManager,
        record: JobRecord,
        on_stage: Callable[[str], None] | None = None,
    ) -> None:
        self._manager = manager
        self._record = record
        self._on_stage = on_stage

    def enter(self, status: JobStatus, message: str, fraction: float | None = None) -> None:
        if can_transition(self._record.status, status):
            self._manager.advance(self._record, status, message=message, fraction=fraction)
            if self._on_stage is not None:
                self._on_stage(status.value)
        else:
            self._manager.report(self._record, message, fraction)

    def progress(self, stage: str, fraction: float | None, message: str) -> None:
        """A provider's own stage name, mapped onto the job's state machine.

        Anything not in the map becomes an in-stage message rather than a
        transition, so a new provider cannot invent job states.
        """
        target = _STAGE_MAP.get(stage)
        if target is None:
            self._manager.report(self._record, message, fraction)
        else:
            self.enter(target, message, fraction)


def _fail_validation(manager: JobManager, record: JobRecord, report: QualityReport) -> None:
    """Fail the job for a mesh that did not pass.

    A model that returns nothing while reporting success must not produce an
    asset — the documented silent-empty-mesh case.
    """
    manager.fail(
        record,
        JobError(
            message="The generated model did not pass validation.",
            technical="; ".join(issue.message for issue in report.fatal_issues),
            suggestions=[
                "Try again — generation is not deterministic across runs.",
                "Try a different input image with a clearer subject.",
            ],
        ),
    )


def _preprocess(
    images: list[Path], output_dir: Path, preprocessor: Preprocessor | None
) -> tuple[list[Path], BackgroundRemoval | None]:
    """Prepare the images for the generator, surviving a preprocessor that fails.

    A failed cut-out does not fail the job. The original image is still a
    legitimate input — it just carries its background into the mesh — so the
    stage degrades to saying what happened rather than refusing to work.
    """
    if preprocessor is None:
        return images, None
    try:
        return preprocessor(images, output_dir)
    except BackgroundRemovalError as error:
        return images, BackgroundRemoval(
            outcome=BackgroundOutcome.FAILED,
            note=f"Background removal failed, so the image was used as it is. {error.message}",
        )


def run_pipeline(  # noqa: PLR0913 - all keyword-only; a parameter object would
    # only relocate the same fields
    *,
    manager: JobManager,
    provider: ImageTo3DProvider,
    record: JobRecord,
    images: list[Path],
    output_dir: Path,
    parameters: dict[str, object] | None = None,
    seed: int | None = None,
    export: ExportOptions | None = None,
    drop_loose_parts: bool = False,
    preprocessor: Preprocessor | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Run one generation to a validated asset, advancing the job as it goes.

    When ``export`` asks for a print format the pipeline gains a repair stage and
    validates against what a slicer needs. That is not a stricter mood — a
    surface with holes is a blemish in a render and a refusal in a slicer, so the
    same mesh is genuinely acceptable for one target and not the other.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    token = manager.token(record.id)
    parameters = parameters or {}

    stages = _StageReporter(manager, record, on_stage)

    try:
        token.raise_if_cancelled()
        stages.enter(JobStatus.PREPROCESSING, "Preparing images")

        # The generator sees these; `images` stays the user's own files, because
        # that is what the metadata records and what a re-run starts from.
        generator_images, background = _preprocess(images, output_dir, preprocessor)
        if background is not None:
            manager.report(record, background.note)
            token.raise_if_cancelled()

        result = provider.generate(
            GenerationRequest(
                images=generator_images, output_dir=output_dir, seed=seed, parameters=parameters
            ),
            stages.progress,
        )

        export_options = export or ExportOptions()
        for_printing = any(fmt.is_print_format for fmt in export_options.formats)

        asset_path: Path = result.mesh_path
        repair_report: RepairReport | None = None
        if for_printing:
            token.raise_if_cancelled()
            stages.enter(JobStatus.OPTIMIZING, "Making the model printable")
            asset_path, repair_report = _repair_asset(
                result.mesh_path, output_dir, drop_loose_parts=drop_loose_parts
            )
            manager.report(record, repair_report.notes[0] if repair_report.notes else "Repaired")

        token.raise_if_cancelled()
        stages.enter(JobStatus.VALIDATING, "Checking the asset")

        report = validate_asset(asset_path, for_printing=for_printing)
        report.write(output_dir / "quality_report.json")

        if not report.valid:
            _fail_validation(manager, record, report)
            return PipelineResult(job=record, report=report, repair=repair_report)

        return _record_success(
            manager=manager,
            record=record,
            provider=provider,
            images=images,
            output_dir=output_dir,
            asset_path=asset_path,
            report=report,
            repair_report=repair_report,
            export_options=export_options,
            parameters=parameters,
            seed=seed,
            result=result,
            background=background,
            token=token,
            on_stage=on_stage,
        )

    except (JobCancelledError, ProviderExecutionError, InvalidTransitionError) as error:
        # A cancel can arrive from another thread at any moment, and it shows
        # up here in three disguises: the boundary check raising, the worker
        # dying because it was terminated, or the state machine refusing a move
        # out of CANCELLED. All three mean the same thing once the token is set;
        # only when it is not are they real failures.
        if token.is_cancelled:
            provider.cancel()
            if record.status.is_active:
                manager.advance(record, JobStatus.CANCELLED, message="Cancelled by the user.")
            return PipelineResult(job=record)
        if isinstance(error, ProviderExecutionError):
            manager.fail(
                record,
                JobError(
                    message=error.message,
                    technical=error.technical,
                    suggestions=error.suggestions,
                ),
            )
            return PipelineResult(job=record)
        raise

    finally:
        provider.cleanup()


def _repair_asset(
    mesh_path: Path, output_dir: Path, *, drop_loose_parts: bool = False
) -> tuple[Path, RepairReport]:
    """Repair for printing, writing the result beside the original.

    The generator's own output is never overwritten: a user comparing the
    repaired model against what the model actually produced needs both.
    """
    import trimesh  # noqa: PLC0415 - heavy import, only needed on this path

    # force="mesh" always produces a Trimesh; the declared return type is the base class.
    loaded = cast("trimesh.Trimesh", trimesh.load(mesh_path, force="mesh"))
    repaired, report = repair_for_printing(loaded, drop_loose_parts=drop_loose_parts)
    target = output_dir / "model_printable.glb"
    repaired.export(target)
    return target, report


def _export_asset(mesh_path: Path, output_dir: Path, options: ExportOptions) -> list[ExportedFile]:
    """Write every requested format, skipping the one already on disk."""
    import trimesh  # noqa: PLC0415

    wanted = tuple(fmt for fmt in options.formats if fmt is not ExportFormat.GLB)
    if not wanted:
        return []
    loaded = cast("trimesh.Trimesh", trimesh.load(mesh_path, force="mesh"))
    return export_mesh(loaded, output_dir, "model", options.model_copy(update={"formats": wanted}))


def read_asset_metadata(path: Path) -> AssetMetadata | None:
    try:
        return AssetMetadata.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
