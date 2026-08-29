"""Can this machine run this provider?

Pure logic over declared requirements and detected hardware — no imports of ML
frameworks, no I/O — so it is fully testable in CI without a GPU. This is where
spec section 7 lives: the answer to "not enough memory" is an honest refusal
carrying the numbers, never a crash loop and never a silent CPU fallback of a
GPU model.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from ..hardware.types import Availability, HardwareInfo, RuntimeKind
from .types import CommercialUse, ProviderMetadata, Requirements


class Verdict(StrEnum):
    """Why the middle value exists: unified memory and swap make "will it run" a
    genuinely uncertain question near the limit, and pretending otherwise either
    blocks a machine that would have worked or promises one that will not."""

    RUNNABLE = "runnable"
    MARGINAL = "marginal"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class Reason(BaseModel):
    code: str
    message: str = Field(description="User-facing, carries the actual numbers.")


class Assessment(BaseModel):
    provider_id: str
    verdict: Verdict
    runtime: RuntimeKind | None = None
    reasons: list[Reason] = Field(default_factory=list)
    #: Set when the user may proceed anyway. Never for BLOCKED.
    overridable: bool = False

    @property
    def can_run(self) -> bool:
        return self.verdict in (Verdict.RUNNABLE, Verdict.MARGINAL)


def _gib(n: int | None) -> str:
    return "unknown" if n is None else f"{n / (1024**3):.1f} GB"


def _select_runtime(hardware: HardwareInfo, requirements: Requirements) -> RuntimeKind | None:
    """Pick the best runtime both the machine and the provider support.

    Preference order is CUDA, then MPS, then CPU — MLX is never auto-selected
    (ADR-0002).

    ``EXPECTED`` counts as selectable, ``UNKNOWN`` does not. That line matters:
    before the first provider is installed there is no PyTorch to confirm MPS
    with, and treating that as "no runtime" made every model unavailable on a
    perfectly capable Mac. ``UNKNOWN`` stays excluded, because offering a model
    that then fails at import time is worse than declining it.
    """
    supported = requirements.supported_runtimes
    for kind in (RuntimeKind.CUDA, RuntimeKind.MPS, RuntimeKind.CPU):
        if kind.value not in supported:
            continue
        status = hardware.runtime(kind)
        if status is not None and status.availability.is_usable:
            return kind
    return None


def assess(
    metadata: ProviderMetadata,
    hardware: HardwareInfo,
    *,
    marginal_ratio: float = 0.85,
) -> Assessment:
    """Decide whether ``metadata``'s provider can run on ``hardware``.

    ``marginal_ratio``: a provider whose estimated peak exceeds available memory
    but stays under ``peak * marginal_ratio`` of it is reported MARGINAL rather
    than BLOCKED. On unified-memory machines this is the honest verdict — macOS
    will swap and the job may well complete, slowly. The user is told, and
    decides.
    """
    reasons: list[Reason] = []
    requirements = metadata.requirements

    # --- Licence gate. First, because no amount of hardware fixes it. ---------
    if metadata.license.territorial_restriction:
        reasons.append(
            Reason(
                code="license.territory",
                message=(
                    f"Licence restriction: {metadata.license.territorial_restriction}. "
                    "VOLUM does not offer this model."
                ),
            )
        )
        return Assessment(provider_id=metadata.id, verdict=Verdict.BLOCKED, reasons=reasons)

    if metadata.license.commercial_use is CommercialUse.NOT_ALLOWED:
        reasons.append(
            Reason(
                code="license.non_commercial",
                message="This model or a dependency it needs is licensed for "
                "non-commercial use only.",
            )
        )

    # --- Platform ------------------------------------------------------------
    if hardware.os not in requirements.supported_platforms:
        supported = ", ".join(sorted(requirements.supported_platforms))
        reasons.append(
            Reason(
                code="platform.unsupported",
                message=f"Not supported on {hardware.os}. Supported: {supported}.",
            )
        )
        return Assessment(provider_id=metadata.id, verdict=Verdict.BLOCKED, reasons=reasons)

    if requirements.requires_apple_silicon and not hardware.is_apple_silicon:
        reasons.append(
            Reason(
                code="platform.needs_apple_silicon",
                message="Requires Apple Silicon.",
            )
        )
        return Assessment(provider_id=metadata.id, verdict=Verdict.BLOCKED, reasons=reasons)

    # --- Runtime -------------------------------------------------------------
    runtime = _select_runtime(hardware, requirements)
    if runtime is not None:
        status = hardware.runtime(runtime)
        if status is not None and status.availability is Availability.EXPECTED:
            reasons.append(
                Reason(
                    code="runtime.expected",
                    message=(
                        f"The {runtime.value} runtime is inferred from this hardware but "
                        "not yet confirmed; it is verified when the model is installed."
                    ),
                )
            )
    if runtime is None:
        supported = ", ".join(sorted(requirements.supported_runtimes))
        reasons.append(
            Reason(
                code="runtime.unavailable",
                message=(
                    f"No usable runtime. This model needs one of: {supported}. "
                    "None of them is confirmed available on this machine."
                ),
            )
        )
        return Assessment(
            provider_id=metadata.id,
            verdict=Verdict.BLOCKED,
            reasons=reasons,
            overridable=False,
        )

    # --- Disk ----------------------------------------------------------------
    if hardware.disk_free_bytes is None:
        reasons.append(
            Reason(code="disk.unknown", message="Free disk space could not be determined.")
        )
    elif hardware.disk_free_bytes < requirements.disk_bytes:
        reasons.append(
            Reason(
                code="disk.insufficient",
                message=(
                    f"Needs {_gib(requirements.disk_bytes)} of disk space, "
                    f"{_gib(hardware.disk_free_bytes)} free. "
                    "The data directory can be moved to another volume in Settings."
                ),
            )
        )
        return Assessment(
            provider_id=metadata.id,
            verdict=Verdict.BLOCKED,
            runtime=runtime,
            reasons=reasons,
        )

    # --- Memory --------------------------------------------------------------
    usable = hardware.usable_memory_bytes
    if usable is None:
        reasons.append(
            Reason(
                code="memory.unknown",
                message="Usable memory could not be determined, so this model's "
                "fit cannot be confirmed.",
            )
        )
        return Assessment(
            provider_id=metadata.id,
            verdict=Verdict.UNKNOWN,
            runtime=runtime,
            reasons=reasons,
            overridable=True,
        )

    memory_label = "unified memory" if hardware.unified_memory else "VRAM"
    peak = requirements.estimated_peak_memory_bytes

    if usable < requirements.minimum_memory_bytes:
        reasons.append(
            Reason(
                code="memory.insufficient",
                message=(
                    f"Needs at least {_gib(requirements.minimum_memory_bytes)} of "
                    f"{memory_label}; this machine has {_gib(usable)}."
                ),
            )
        )
        return Assessment(
            provider_id=metadata.id,
            verdict=Verdict.BLOCKED,
            runtime=runtime,
            reasons=reasons,
        )

    if usable < peak:
        if usable >= peak * marginal_ratio:
            swap_note = (
                " On unified memory the system will page to disk, so this may complete "
                "but will be slow."
                if hardware.unified_memory
                else ""
            )
            reasons.append(
                Reason(
                    code="memory.marginal",
                    message=(
                        f"Estimated peak is {_gib(peak)} of {memory_label}; this machine "
                        f"has {_gib(usable)}.{swap_note}"
                    ),
                )
            )
            return Assessment(
                provider_id=metadata.id,
                verdict=Verdict.MARGINAL,
                runtime=runtime,
                reasons=reasons,
                overridable=True,
            )
        reasons.append(
            Reason(
                code="memory.insufficient",
                message=(
                    f"Estimated peak is {_gib(peak)} of {memory_label}; this machine "
                    f"has {_gib(usable)}. Too far below to attempt."
                ),
            )
        )
        return Assessment(
            provider_id=metadata.id,
            verdict=Verdict.BLOCKED,
            runtime=runtime,
            reasons=reasons,
            overridable=True,
        )

    return Assessment(
        provider_id=metadata.id,
        verdict=Verdict.RUNNABLE,
        runtime=runtime,
        reasons=reasons,
    )
