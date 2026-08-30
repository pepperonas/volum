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
from ..jobs.manager import JobCancelledError, JobManager
from ..jobs.types import JobError, JobRecord, JobStatus, can_transition
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


class AssetMetadata(BaseModel):
    """``asset.json`` — everything needed to understand and re-run this result."""

    id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    volum_version: str = __version__
    source_images: list[str] = Field(default_factory=list)
    export_formats: list[str] = Field(default_factory=list)
    target_size_mm: float | None = None
    repair_strategy: str | None = None
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
    on_stage: Callable[[str], None] | None,
) -> PipelineResult:
    """Export, write the metadata, complete the job."""
    exports = _export_asset(asset_path, output_dir, export_options)

    hardware = detect_hardware()
    metadata = AssetMetadata(
        id=record.id,
        source_images=[str(path) for path in images],
        export_formats=[fmt.value for fmt in export_options.formats],
        target_size_mm=export_options.target_size_mm,
        repair_strategy=repair_report.strategy.value if repair_report else None,
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

    def enter(status: JobStatus, message: str, fraction: float | None = None) -> None:
        """Move to ``status``, or report within the current stage if that is not legal.

        Providers report their own stages, and the pipeline has stages of its own;
        the two can name the same one. Advancing blindly then trips the state
        machine, which is right to refuse — a stage that re-enters itself would
        read as progress while nothing moved. Reporting instead keeps the message
        without faking a transition.
        """
        if can_transition(record.status, status):
            manager.advance(record, status, message=message, fraction=fraction)
            if on_stage is not None:
                on_stage(status.value)
        else:
            manager.report(record, message, fraction)

    def progress(stage: str, fraction: float | None, message: str) -> None:
        target = _STAGE_MAP.get(stage)
        if target is None:
            manager.report(record, message, fraction)
        else:
            enter(target, message, fraction)

    try:
        token.raise_if_cancelled()
        enter(JobStatus.PREPROCESSING, "Preparing images")

        result = provider.generate(
            GenerationRequest(
                images=images, output_dir=output_dir, seed=seed, parameters=parameters
            ),
            progress,
        )

        export_options = export or ExportOptions()
        for_printing = any(fmt.is_print_format for fmt in export_options.formats)

        repair_report: RepairReport | None = None
        asset_path = result.mesh_path

        if for_printing:
            token.raise_if_cancelled()
            enter(JobStatus.OPTIMIZING, "Making the model printable")
            asset_path, repair_report = _repair_asset(
                result.mesh_path, output_dir, drop_loose_parts=drop_loose_parts
            )
            manager.report(record, repair_report.notes[0] if repair_report.notes else "Repaired")

        token.raise_if_cancelled()
        enter(JobStatus.VALIDATING, "Checking the asset")

        report = validate_asset(asset_path, for_printing=for_printing)
        report.write(output_dir / "quality_report.json")

        if not report.valid:
            # A model that returns nothing while reporting success must not
            # produce an asset. This is the documented silent-empty-mesh case.
            reasons = "; ".join(issue.message for issue in report.fatal_issues)
            manager.fail(
                record,
                JobError(
                    message="The generated model did not pass validation.",
                    technical=reasons,
                    suggestions=[
                        "Try again — generation is not deterministic across runs.",
                        "Try a different input image with a clearer subject.",
                    ],
                ),
            )
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
            on_stage=on_stage,
        )

    except JobCancelledError:
        provider.cancel()
        if record.status.is_active:
            manager.advance(record, JobStatus.CANCELLED, message="Cancelled by the user.")
        return PipelineResult(job=record)

    except ProviderExecutionError as error:
        manager.fail(
            record,
            JobError(
                message=error.message,
                technical=error.technical,
                suggestions=error.suggestions,
            ),
        )
        return PipelineResult(job=record)

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
