"""The provider contract: what a model can do, what it needs, and what it costs.

Capabilities and requirements are **declared data, not code paths**. Nothing in
the pipeline branches on a provider's identity; it asks the declaration. That is
what makes spec section 68's test meaningful — adding a second provider must not
require edits in ten places.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class Capability(StrEnum):
    """What a provider can actually do.

    ``MULTI_IMAGE`` means genuine multi-view conditioning. It is ``False`` on
    every V1 provider: the generation models surveyed are single-image, and
    TRELLIS.2's own tracker reports multi-image conditioning performing *worse*
    (``docs/research.md`` section 5). Declaring it falsely would be exactly the
    simulated feature spec section 14 forbids.
    """

    SINGLE_IMAGE = "single_image"
    MULTI_IMAGE = "multi_image"
    TEXTURE = "texture"
    PBR = "pbr"
    UV = "uv"
    HIGH_RESOLUTION = "high_resolution"
    FAST_INFERENCE = "fast_inference"


class CommercialUse(StrEnum):
    """Whether output may be used commercially.

    ``UNKNOWN`` is a first-class answer and the required one whenever the
    licence chain has not been verified end to end (spec section 10). The UI
    shows it verbatim; it must never be silently rendered as "yes".
    """

    ALLOWED = "allowed"
    CONDITIONAL = "conditional"
    NOT_ALLOWED = "not_allowed"
    UNKNOWN = "unknown"


class LicenseMetadata(BaseModel):
    """Licence facts for the model *and the pipeline needed to run it*.

    Split deliberately. A permissive model licence does not imply a permissive
    pipeline — TRELLIS.2 ships MIT weights behind a non-commercial rasteriser.
    ``dependency_licenses`` is where that shows up instead of being lost.
    """

    code_license: str
    weights_license: str
    commercial_use: CommercialUse
    commercial_use_detail: str | None = None
    territorial_restriction: str | None = None
    attribution_required: str | None = Field(
        default=None,
        description="Text that must be displayed in the UI, e.g. 'Built with DINOv3'.",
    )
    dependency_licenses: dict[str, str] = Field(default_factory=dict)
    source_url: str | None = None
    verified_on: str | None = Field(
        default=None, description="ISO date the licence text was last read. Stale is a bug."
    )

    @property
    def is_blocking(self) -> bool:
        """Whether this licence should stop VOLUM from offering the model at all."""
        return (
            self.commercial_use is CommercialUse.NOT_ALLOWED
            or self.territorial_restriction is not None
        )


class Requirements(BaseModel):
    """What a provider needs from the machine.

    ``estimated_peak_memory_bytes`` is the number that gates availability, not
    the model file size. For TRELLIS.2 those differ by several gigabytes and the
    peak is what makes a 16 GB machine fail.
    """

    supported_platforms: frozenset[str] = Field(
        description="platform.system() values, e.g. {'Darwin', 'Linux', 'Windows'}"
    )
    supported_runtimes: frozenset[str] = Field(description="RuntimeKind values")
    minimum_memory_bytes: int
    estimated_peak_memory_bytes: int
    disk_bytes: int
    requires_apple_silicon: bool = False
    requires_gated_download: bool = Field(
        default=False,
        description="Weights need an accepted licence and an access token on the model "
        "host. DINOv3 is one. The manager must explain this, not surface a bare 401.",
    )
    numbers_source: str | None = Field(
        default=None,
        description="Where the memory and disk figures come from. Upstream claims and "
        "VOLUM measurements are not the same thing and must not be confused; this "
        "field is what keeps an unverified estimate from hardening into a fact.",
    )

    model_config = {"frozen": True}


class ProviderMetadata(BaseModel):
    id: str
    name: str
    version: str
    description: str
    capabilities: frozenset[Capability]
    requirements: Requirements
    license: LicenseMetadata
    output_formats: frozenset[str] = frozenset({"glb"})
    supports_seed: bool = True


class ResourceEstimate(BaseModel):
    peak_memory_bytes: int
    disk_bytes: int
    estimated_seconds: float | None = Field(
        default=None,
        description="Only set when VOLUM has measured this model on this hardware. "
        "Never carried over from an upstream benchmark on other hardware — the same "
        "model is ~17 s on an H100 and ~5 minutes on a Mac.",
    )


class EnvironmentReport(BaseModel):
    """Whether the provider's own environment is actually usable right now."""

    ok: bool
    problems: list[str] = Field(default_factory=list)
    detail: dict[str, str] = Field(default_factory=dict)


class GenerationRequest(BaseModel):
    images: list[Path]
    output_dir: Path
    seed: int | None = None
    parameters: dict[str, object] = Field(default_factory=dict)


class GenerationResult(BaseModel):
    mesh_path: Path
    artifacts: dict[str, Path] = Field(default_factory=dict)
    seed: int | None = None
    runtime: str | None = None
    duration_seconds: float | None = None
    parameters: dict[str, object] = Field(default_factory=dict)


#: Progress callback: ``(stage, fraction_or_None, message)``.
#:
#: The fraction is ``None`` whenever the provider does not know — and it usually
#: does not. Callers render an indeterminate indicator for the stage rather than
#: inventing a percentage (spec section 18).
ProgressCallback = Callable[[str, float | None, str], None]


class ImageTo3DProvider(ABC):
    """Every model integration implements exactly this."""

    @property
    @abstractmethod
    def metadata(self) -> ProviderMetadata: ...

    @abstractmethod
    def validate_environment(self) -> EnvironmentReport:
        """Check the provider's own environment: weights present, imports resolvable."""

    @abstractmethod
    def estimate_resources(self, request: GenerationRequest) -> ResourceEstimate: ...

    @abstractmethod
    def generate(
        self, request: GenerationRequest, on_progress: ProgressCallback
    ) -> GenerationResult:
        """Run real inference.

        Implementations must not fabricate output. A provider that cannot run
        raises; it never writes a placeholder mesh (spec section 37).
        """

    @abstractmethod
    def cleanup(self) -> None:
        """Release models and GPU memory. Called after every job."""
