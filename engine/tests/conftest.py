from __future__ import annotations

import pytest

from volum_core.hardware.types import (
    Availability,
    GpuInfo,
    HardwareInfo,
    RuntimeKind,
    RuntimeStatus,
)

GIB = 1024**3


def make_hardware(
    *,
    os: str = "Darwin",
    arch: str = "arm64",
    apple_silicon: bool = True,
    ram_gib: float | None = 16,
    unified: bool = True,
    vram_gib: float | None = None,
    disk_free_gib: float | None = 100,
    mps: Availability = Availability.AVAILABLE,
    cuda: Availability = Availability.UNAVAILABLE,
) -> HardwareInfo:
    """Build a hardware snapshot for tests.

    A helper rather than fixtures because nearly every gating test varies one
    axis; naming that axis at the call site is what makes the tests readable.
    """
    return HardwareInfo(
        os=os,
        os_version="26.6.2",
        arch=arch,
        is_apple_silicon=apple_silicon,
        cpu_name="Test CPU",
        cpu_cores_physical=8,
        cpu_cores_logical=10,
        ram_total_bytes=None if ram_gib is None else int(ram_gib * GIB),
        ram_available_bytes=None if ram_gib is None else int(ram_gib * GIB * 0.6),
        unified_memory=unified,
        gpus=[
            GpuInfo(
                name="Test GPU",
                vram_bytes=None if vram_gib is None else int(vram_gib * GIB),
                unified_memory=unified,
            )
        ],
        metal=Availability.AVAILABLE if apple_silicon else Availability.UNAVAILABLE,
        runtimes=[
            RuntimeStatus(kind=RuntimeKind.MPS, availability=mps),
            RuntimeStatus(kind=RuntimeKind.CUDA, availability=cuda),
            RuntimeStatus(kind=RuntimeKind.MLX, availability=Availability.UNAVAILABLE),
            RuntimeStatus(kind=RuntimeKind.CPU, availability=Availability.AVAILABLE),
        ],
        disk_free_bytes=None if disk_free_gib is None else int(disk_free_gib * GIB),
        disk_total_bytes=500 * GIB,
    )


@pytest.fixture
def m1_pro_16gb() -> HardwareInfo:
    """The actual development machine. Its verdicts are pinned deliberately —
    if a change makes TRELLIS.2 look 'runnable' here, that is a regression."""
    return make_hardware(ram_gib=16, disk_free_gib=29)
