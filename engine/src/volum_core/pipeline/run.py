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

from pydantic import BaseModel, Field

from ..hardware.detect import detect_hardware
from ..jobs.manager import JobCancelled, JobManager
from ..jobs.types import JobError, JobRecord, JobStatus
from ..providers.types import GenerationRequest, ImageTo3DProvider
from ..providers.worker_provider import ProviderExecutionError
from ..validation import QualityReport, validate_asset
from ..version import __version__

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
    on_stage: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Run one generation to a validated asset, advancing the job as it goes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    token = manager.token(record.id)
    parameters = parameters or {}

    def progress(stage: str, fraction: float | None, message: str) -> None:
        target = _STAGE_MAP.get(stage)
        if target is not None and target is not record.status:
            manager.advance(record, target, message=message, fraction=fraction)
            if on_stage is not None:
                on_stage(target.value)
        else:
            manager.report(record, message, fraction)

    try:
        token.raise_if_cancelled()
        manager.advance(record, JobStatus.PREPROCESSING, message="Preparing images")
        if on_stage is not None:
            on_stage(JobStatus.PREPROCESSING.value)

        result = provider.generate(
            GenerationRequest(
                images=images, output_dir=output_dir, seed=seed, parameters=parameters
            ),
            progress,
        )

        token.raise_if_cancelled()
        manager.advance(record, JobStatus.VALIDATING, message="Checking the asset")
        if on_stage is not None:
            on_stage(JobStatus.VALIDATING.value)

        report = validate_asset(result.mesh_path)
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
            return PipelineResult(job=record, report=report)

        hardware = detect_hardware()
        metadata = AssetMetadata(
            id=record.id,
            source_images=[str(path) for path in images],
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
            "mesh": result.mesh_path,
            "quality_report": output_dir / "quality_report.json",
            "asset": output_dir / "asset.json",
        }
        record.runtime = result.runtime
        record.seed = metadata.seed
        manager.advance(record, JobStatus.COMPLETED, message="Done")
        if on_stage is not None:
            on_stage(JobStatus.COMPLETED.value)
        return PipelineResult(job=record, report=report, metadata=metadata)

    except JobCancelled:
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


def read_asset_metadata(path: Path) -> AssetMetadata | None:
    try:
        return AssetMetadata.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
