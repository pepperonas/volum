"""Value types for hardware and runtime detection.

Design rule for this module: **a fact that cannot be established reliably is
reported as unknown, never estimated.** Downstream code (the doctor, the model
manager, provider gating) treats ``UNKNOWN`` as "do not promise anything",
which is the behaviour spec section 7 requires. An optimistic guess here turns
into a crash loop three layers up.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Availability(StrEnum):
    """Four-valued availability. The values that are not yes/no are the point.

    ``EXPECTED`` exists because of a real failure found by running the doctor on
    a clean machine: PyTorch lives in per-provider environments, so before the
    first model is installed nothing can *confirm* MPS or CUDA. Collapsing that
    into ``UNKNOWN`` made every model report "no usable runtime" on a perfectly
    capable Apple Silicon Mac — technically defensible, and useless.

    The distinction that matters is between *hardware cannot* and *software is
    not installed yet*:

    ``AVAILABLE``   probed and working now.
    ``EXPECTED``    the hardware supports it; the runtime is not installed in
                    this environment, and installing a provider will bring it.
    ``UNAVAILABLE`` the hardware or platform does not support it at all.
    ``UNKNOWN``     could not be established. Never a guess.
    """

    AVAILABLE = "available"
    EXPECTED = "expected"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"

    @property
    def is_available(self) -> bool:
        """Confirmed working. Strict on purpose."""
        return self is Availability.AVAILABLE

    @property
    def is_usable(self) -> bool:
        """Confirmed working, or expected to work once a provider is installed.

        This is the predicate gating uses. ``is_available`` is for anything that
        must not act on an unconfirmed capability.
        """
        return self in (Availability.AVAILABLE, Availability.EXPECTED)


class RuntimeKind(StrEnum):
    """How tensors execute. Separate from the provider (see ADR-0002)."""

    MPS = "mps"
    CUDA = "cuda"
    CPU = "cpu"
    MLX = "mlx"


class RuntimeStatus(BaseModel):
    """Availability of one runtime, with the evidence behind the verdict."""

    kind: RuntimeKind
    availability: Availability
    version: str | None = None
    detail: str | None = Field(
        default=None,
        description="Why the verdict is what it is. Shown verbatim in the doctor "
        "report, so it must be meaningful to a user, not just to a developer.",
    )

    @property
    def is_available(self) -> bool:
        return self.availability.is_available

    @property
    def is_usable(self) -> bool:
        return self.availability.is_usable


class GpuInfo(BaseModel):
    name: str
    vendor: str | None = None
    #: Dedicated video memory. ``None`` on unified-memory systems, where the
    #: meaningful number is :attr:`HardwareInfo.ram_total_bytes` instead.
    vram_bytes: int | None = None
    cores: int | None = None
    unified_memory: bool = False


class HardwareInfo(BaseModel):
    """A snapshot of what this machine is. Cheap to produce, safe to cache."""

    os: str
    os_version: str
    arch: str
    is_apple_silicon: bool

    cpu_name: str | None = None
    cpu_cores_physical: int | None = None
    cpu_cores_logical: int | None = None

    ram_total_bytes: int | None = None
    ram_available_bytes: int | None = None
    unified_memory: bool = False

    gpus: list[GpuInfo] = Field(default_factory=list)
    metal: Availability = Availability.UNKNOWN

    runtimes: list[RuntimeStatus] = Field(default_factory=list)
    torch_version: str | None = None

    disk_free_bytes: int | None = None
    disk_total_bytes: int | None = None
    data_dir: str | None = None

    def runtime(self, kind: RuntimeKind) -> RuntimeStatus | None:
        for status in self.runtimes:
            if status.kind is kind:
                return status
        return None

    def available_runtimes(self) -> list[RuntimeKind]:
        """Runtimes confirmed working right now."""
        return [status.kind for status in self.runtimes if status.is_available]

    def usable_runtimes(self) -> list[RuntimeKind]:
        """Runtimes that work, or will once a provider environment is installed."""
        return [status.kind for status in self.runtimes if status.is_usable]

    @property
    def recommended_runtime(self) -> RuntimeKind:
        """The runtime VOLUM would pick on this machine.

        Order follows ADR-0002: CUDA where present, otherwise MPS on Apple
        Silicon, otherwise CPU. MLX is deliberately **not** preferred over MPS —
        it is an experimental backend, not the default path.
        """
        usable = self.usable_runtimes()
        for kind in (RuntimeKind.CUDA, RuntimeKind.MPS):
            if kind in usable:
                return kind
        return RuntimeKind.CPU

    @property
    def usable_memory_bytes(self) -> int | None:
        """Memory a model may plausibly use.

        On unified-memory machines the GPU draws from system RAM, so total RAM
        is the ceiling. On discrete GPUs it is VRAM. Returns ``None`` rather
        than a number when neither can be established.
        """
        if self.unified_memory:
            return self.ram_total_bytes
        vram = [gpu.vram_bytes for gpu in self.gpus if gpu.vram_bytes is not None]
        return max(vram) if vram else None
