from __future__ import annotations

from conftest import GIB, make_hardware
from volum_core.hardware.types import Availability, RuntimeKind


def test_recommended_runtime_prefers_cuda_over_mps() -> None:
    hardware = make_hardware(mps=Availability.AVAILABLE, cuda=Availability.AVAILABLE)
    assert hardware.recommended_runtime is RuntimeKind.CUDA


def test_recommended_runtime_falls_back_to_cpu_when_nothing_accelerated() -> None:
    hardware = make_hardware(mps=Availability.UNAVAILABLE, cuda=Availability.UNAVAILABLE)
    assert hardware.recommended_runtime is RuntimeKind.CPU


def test_unknown_runtime_is_not_treated_as_available() -> None:
    """An UNKNOWN runtime must not be selected. Offering a model that then fails
    at import time is worse than declining it."""
    hardware = make_hardware(mps=Availability.UNKNOWN, cuda=Availability.UNKNOWN)
    assert hardware.recommended_runtime is RuntimeKind.CPU
    assert hardware.available_runtimes() == [RuntimeKind.CPU]


def test_usable_memory_is_system_ram_on_unified_memory() -> None:
    hardware = make_hardware(ram_gib=16, unified=True, vram_gib=None)
    assert hardware.usable_memory_bytes == 16 * GIB


def test_usable_memory_is_vram_on_discrete_gpu() -> None:
    hardware = make_hardware(ram_gib=64, unified=False, vram_gib=24)
    assert hardware.usable_memory_bytes == 24 * GIB


def test_usable_memory_is_none_rather_than_guessed() -> None:
    """The module's core rule: unknown stays unknown."""
    hardware = make_hardware(ram_gib=None, unified=False, vram_gib=None)
    assert hardware.usable_memory_bytes is None


def test_expected_runtime_counts_as_usable_but_not_available() -> None:
    """The whole point of the fourth state: 'hardware can, software is not
    installed yet' must not read the same as 'hardware cannot'."""
    hardware = make_hardware(mps=Availability.EXPECTED, cuda=Availability.UNAVAILABLE)
    assert hardware.available_runtimes() == [RuntimeKind.CPU]
    assert RuntimeKind.MPS in hardware.usable_runtimes()
    assert hardware.recommended_runtime is RuntimeKind.MPS


def test_recommended_runtime_prefers_confirmed_cuda_over_expected_mps() -> None:
    hardware = make_hardware(mps=Availability.EXPECTED, cuda=Availability.AVAILABLE)
    assert hardware.recommended_runtime is RuntimeKind.CUDA
