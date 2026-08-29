"""Detection tests.

Detection reads the real machine, so these assert *invariants* rather than
values — anything else would only pass on one laptop. The one hard requirement
is that detection never raises, because it runs on every start-up.
"""

from __future__ import annotations

import platform
from pathlib import Path

from volum_core.hardware import cached_hardware, detect_hardware
from volum_core.hardware.detect import _expected_cuda, _expected_mps, _run, detect_metal
from volum_core.hardware.types import Availability, RuntimeKind


def test_detection_succeeds_on_this_machine() -> None:
    hardware = detect_hardware()
    assert hardware.os == platform.system()
    assert hardware.arch == platform.machine()


def test_every_runtime_kind_is_reported() -> None:
    """The doctor must never show a blank where a runtime should be."""
    kinds = {status.kind for status in detect_hardware().runtimes}
    assert kinds == set(RuntimeKind)


def test_cpu_runtime_is_always_available() -> None:
    status = detect_hardware().runtime(RuntimeKind.CPU)
    assert status is not None
    assert status.availability is Availability.AVAILABLE


def test_unavailable_runtimes_explain_themselves() -> None:
    """A bare 'unavailable' in the doctor is not actionable."""
    for status in detect_hardware().runtimes:
        if status.availability is not Availability.AVAILABLE:
            assert status.detail, f"{status.kind} is not available but gives no reason"


def test_detection_survives_an_unreadable_data_directory() -> None:
    hardware = detect_hardware(data_dir=Path("/definitely/not/a/real/path"))
    assert hardware.disk_free_bytes is None


def test_probe_helper_returns_none_for_a_missing_binary() -> None:
    """Every probe funnels through _run; if it raised, start-up would die."""
    assert _run(["/definitely/not/a/real/binary", "--version"]) is None


def test_probe_helper_returns_none_on_nonzero_exit() -> None:
    assert _run(["/bin/sh", "-c", "exit 3"]) is None


def test_apple_silicon_reports_unified_memory_and_no_vram() -> None:
    """Reporting total RAM as VRAM would be exactly the confident-but-wrong
    number this module exists to avoid."""
    hardware = detect_hardware()
    if not hardware.is_apple_silicon:
        return
    assert hardware.unified_memory
    assert hardware.gpus
    assert all(gpu.vram_bytes is None for gpu in hardware.gpus)
    assert hardware.usable_memory_bytes == hardware.ram_total_bytes


def test_metal_matches_the_platform() -> None:
    if platform.system() == "Darwin":
        assert detect_metal() is Availability.AVAILABLE
    else:
        assert detect_metal() is Availability.UNAVAILABLE


def test_cached_hardware_is_cached() -> None:
    assert cached_hardware() is cached_hardware()


def test_apple_silicon_expects_mps_when_torch_is_absent() -> None:
    """Without PyTorch there is nothing to confirm MPS with, but the hardware
    plainly supports it. Reporting UNKNOWN here made every model unavailable."""
    status = _expected_mps()
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        assert status.availability is Availability.EXPECTED
        assert status.detail and "confirmed" in status.detail
    else:
        assert status.availability is Availability.UNAVAILABLE


def test_cuda_is_unavailable_not_unknown_on_macos() -> None:
    """A definite no is more useful than a shrug when the answer is definite."""
    if platform.system() == "Darwin":
        assert _expected_cuda().availability is Availability.UNAVAILABLE


def test_deep_probe_adds_gpu_core_count_on_apple_silicon() -> None:
    """The shallow path has no source for this: hw.perflevel0.gpu_core_count
    does not exist, which the first doctor run revealed by printing nothing."""
    hardware = detect_hardware(deep=True)
    if not hardware.is_apple_silicon:
        return
    assert hardware.gpus
    assert hardware.gpus[0].cores is not None
    assert hardware.gpus[0].cores > 0


def test_shallow_probe_stays_fast_and_reports_no_core_count() -> None:
    hardware = detect_hardware(deep=False)
    if hardware.is_apple_silicon:
        assert hardware.gpus[0].cores is None
