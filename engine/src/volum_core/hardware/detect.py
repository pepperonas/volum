"""Hardware and runtime detection.

Runs on every start-up and must therefore be fast and must never raise. Every
probe is wrapped: a detection failure degrades one field to ``None`` or
``UNKNOWN``, it never takes the application down.

Detection deliberately does **not** require PyTorch. The engine environment is
kept light; the heavy ML stack lives in per-provider virtual environments
(``docs/architecture.md`` section 2). So the probes fall back to evidence that
exists without it: ``nvidia-smi`` for CUDA, the Metal framework on disk for
Metal, ``sysctl`` for Apple Silicon specifics.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import psutil

from .types import Availability, GpuInfo, HardwareInfo, RuntimeKind, RuntimeStatus

_SUBPROCESS_TIMEOUT_S = 5.0

#: nvidia-smi CSV rows are "<name>, <memory.total>".
_NVIDIA_SMI_FIELDS = 2


def _run(args: list[str]) -> str | None:
    """Run a probe command, returning stdout, or ``None`` if anything at all went wrong.

    Detection is best-effort by definition: a missing binary, a non-zero exit,
    a hang and a permissions error are all simply "could not establish this".
    """
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, never shell (spec section 50)
            args,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _sysctl(key: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    return _run(["/usr/sbin/sysctl", "-n", key])


def detect_metal() -> Availability:
    """Metal availability.

    Checked by the presence of the framework rather than assumed from the
    architecture, so the answer stays evidence-based on Intel Macs too.
    """
    if platform.system() != "Darwin":
        return Availability.UNAVAILABLE
    if Path("/System/Library/Frameworks/Metal.framework").exists():
        return Availability.AVAILABLE
    return Availability.UNAVAILABLE


@lru_cache(maxsize=1)
def _apple_gpu_cores() -> int | None:
    """GPU core count on Apple Silicon.

    Cached for the process lifetime: the number is a static hardware fact, and
    the probe costs about two seconds. Without the cache the test suite spent
    most of its runtime asking ``system_profiler`` the same question.

    There is no ``sysctl`` for this — ``hw.perflevel0.gpu_core_count`` does not
    exist, which the first run of the doctor demonstrated by printing nothing.
    The only reliable source is ``system_profiler``, which costs about a second.
    That is why this lives behind ``deep=True``: too slow for every start-up,
    fine for a user-invoked ``volum doctor``. The value is cosmetic — no gating
    decision depends on it — so ``None`` is a perfectly good answer.
    """
    raw = _run(["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"])
    if raw is None:
        return None
    try:
        entries = json.loads(raw).get("SPDisplaysDataType", [])
    except (json.JSONDecodeError, AttributeError):
        return None
    for entry in entries:
        cores = entry.get("sppci_cores")
        if cores is None:
            continue
        try:
            return int(cores)
        except (TypeError, ValueError):
            continue
    return None


def detect_gpus(is_apple_silicon: bool, *, deep: bool = False) -> list[GpuInfo]:
    """Enumerate GPUs.

    On Apple Silicon the GPU is part of the SoC and shares system memory, so
    ``vram_bytes`` is left ``None`` on purpose — reporting total RAM as VRAM
    would be the exact kind of confident-but-wrong number this module avoids.
    """
    system = platform.system()

    if is_apple_silicon:
        name = _sysctl("machdep.cpu.brand_string") or "Apple Silicon GPU"
        cores = _apple_gpu_cores() if deep else None
        return [
            GpuInfo(name=name, vendor="Apple", vram_bytes=None, cores=cores, unified_memory=True)
        ]

    if shutil.which("nvidia-smi"):
        raw = _run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ]
        )
        if raw:
            gpus: list[GpuInfo] = []
            for line in raw.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) != _NVIDIA_SMI_FIELDS:
                    continue
                try:
                    vram = int(float(parts[1])) * 1024 * 1024  # nvidia-smi reports MiB
                except ValueError:
                    vram = None
                gpus.append(GpuInfo(name=parts[0], vendor="NVIDIA", vram_bytes=vram))
            if gpus:
                return gpus

    if system == "Darwin":
        return [GpuInfo(name="Unknown GPU", vendor=None)]
    return []


def _probe_torch() -> tuple[str | None, RuntimeStatus, RuntimeStatus]:
    """Ask PyTorch about MPS and CUDA, if PyTorch is importable here.

    Returns ``(version, mps_status, cuda_status)``. When PyTorch is absent the
    statuses are ``UNKNOWN`` with an explanation rather than ``UNAVAILABLE`` —
    the hardware may well support them; this environment simply cannot tell.
    """
    try:
        import torch  # noqa: PLC0415 - optional dependency, probed at runtime
    except ImportError:
        return None, _expected_mps(), _expected_cuda()

    version = str(torch.__version__)

    try:
        mps_ok = bool(torch.backends.mps.is_available())
        mps = RuntimeStatus(
            kind=RuntimeKind.MPS,
            availability=Availability.AVAILABLE if mps_ok else Availability.UNAVAILABLE,
            version=version,
            detail=None if mps_ok else "PyTorch reports the MPS backend as unavailable.",
        )
    except (AttributeError, RuntimeError) as exc:
        mps = RuntimeStatus(
            kind=RuntimeKind.MPS, availability=Availability.UNKNOWN, detail=f"probe failed: {exc}"
        )

    try:
        cuda_ok = bool(torch.cuda.is_available())
        cuda = RuntimeStatus(
            kind=RuntimeKind.CUDA,
            availability=Availability.AVAILABLE if cuda_ok else Availability.UNAVAILABLE,
            version=getattr(torch.version, "cuda", None) if cuda_ok else None,
            detail=None if cuda_ok else "PyTorch reports no usable CUDA device.",
        )
    except (AttributeError, RuntimeError) as exc:
        cuda = RuntimeStatus(
            kind=RuntimeKind.CUDA, availability=Availability.UNKNOWN, detail=f"probe failed: {exc}"
        )

    return version, mps, cuda


def _expected_mps() -> RuntimeStatus:
    """MPS verdict when PyTorch is not installed here.

    Apple Silicon with the Metal framework present *does* support MPS; the only
    missing piece is a PyTorch build, which arrives with the first provider
    environment. Saying ``UNKNOWN`` here would block every model on a machine
    that is entirely capable of running them.
    """
    if platform.system() != "Darwin":
        return RuntimeStatus(
            kind=RuntimeKind.MPS,
            availability=Availability.UNAVAILABLE,
            detail="MPS exists only on Apple hardware.",
        )
    if platform.machine() != "arm64":
        return RuntimeStatus(
            kind=RuntimeKind.MPS,
            availability=Availability.UNAVAILABLE,
            detail="MPS requires Apple Silicon; this Mac is Intel-based.",
        )
    if detect_metal() is not Availability.AVAILABLE:
        return RuntimeStatus(
            kind=RuntimeKind.MPS,
            availability=Availability.UNKNOWN,
            detail="Metal could not be found, which is unexpected on Apple Silicon.",
        )
    return RuntimeStatus(
        kind=RuntimeKind.MPS,
        availability=Availability.EXPECTED,
        detail="Apple Silicon with Metal. PyTorch is not installed in the engine "
        "environment, so this is confirmed when the first model provider is installed.",
    )


def _expected_cuda() -> RuntimeStatus:
    """CUDA verdict when PyTorch is not installed here.

    An NVIDIA driver proves a device exists; it does not prove a working PyTorch
    CUDA build, which is why this is ``EXPECTED`` rather than ``AVAILABLE``.
    """
    if platform.system() == "Darwin":
        return RuntimeStatus(
            kind=RuntimeKind.CUDA,
            availability=Availability.UNAVAILABLE,
            detail="CUDA is not available on macOS.",
        )
    if not shutil.which("nvidia-smi"):
        return RuntimeStatus(
            kind=RuntimeKind.CUDA,
            availability=Availability.UNAVAILABLE,
            detail="No NVIDIA driver found (nvidia-smi is not on PATH).",
        )
    raw = _run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"])
    driver = raw.splitlines()[0].strip() if raw else "unknown version"
    return RuntimeStatus(
        kind=RuntimeKind.CUDA,
        availability=Availability.EXPECTED,
        detail=f"NVIDIA driver {driver} is present. PyTorch is not installed in the "
        "engine environment, so this is confirmed when the first model provider "
        "is installed.",
    )


def _probe_mlx() -> RuntimeStatus:
    try:
        import mlx.core as mx  # noqa: PLC0415 - optional dependency
    except ImportError:
        return RuntimeStatus(
            kind=RuntimeKind.MLX,
            availability=Availability.UNAVAILABLE,
            detail="MLX is not installed. It is an experimental backend, not the default "
            "Apple Silicon path (see ADR-0002).",
        )
    return RuntimeStatus(
        kind=RuntimeKind.MLX,
        availability=Availability.AVAILABLE,
        version=getattr(mx, "__version__", None),
    )


def detect_hardware(data_dir: Path | None = None, *, deep: bool = False) -> HardwareInfo:
    """Produce a full hardware snapshot. Never raises.

    ``deep`` enables probes that are accurate but slow (about a second). Use it
    for ``volum doctor``, not for application start-up.
    """
    system = platform.system()
    machine = platform.machine()
    is_apple_silicon = system == "Darwin" and machine == "arm64"

    virtual_memory = psutil.virtual_memory()

    disk_free: int | None = None
    disk_total: int | None = None
    probe_path = data_dir if data_dir is not None else Path.home()
    try:
        usage = psutil.disk_usage(str(probe_path))
        disk_free, disk_total = usage.free, usage.total
    except (OSError, PermissionError):
        pass

    torch_version, mps, cuda = _probe_torch()

    cpu_name = _sysctl("machdep.cpu.brand_string") or (platform.processor() or None)

    return HardwareInfo(
        os=system,
        os_version=platform.mac_ver()[0] or platform.release(),
        arch=machine,
        is_apple_silicon=is_apple_silicon,
        cpu_name=cpu_name,
        cpu_cores_physical=psutil.cpu_count(logical=False),
        cpu_cores_logical=psutil.cpu_count(logical=True),
        ram_total_bytes=virtual_memory.total,
        ram_available_bytes=virtual_memory.available,
        unified_memory=is_apple_silicon,
        gpus=detect_gpus(is_apple_silicon, deep=deep),
        metal=detect_metal(),
        runtimes=[
            mps,
            cuda,
            _probe_mlx(),
            RuntimeStatus(
                kind=RuntimeKind.CPU,
                availability=Availability.AVAILABLE,
                detail="Always available; usable only for providers that declare CPU support.",
            ),
        ],
        torch_version=torch_version,
        disk_free_bytes=disk_free,
        disk_total_bytes=disk_total,
        data_dir=str(data_dir) if data_dir is not None else None,
    )


@lru_cache(maxsize=1)
def _cached_hardware() -> HardwareInfo:
    return detect_hardware()


def cached_hardware() -> HardwareInfo:
    """Hardware snapshot cached for the process lifetime.

    Static facts (CPU, GPU, architecture) do not change while VOLUM runs.
    Volatile ones — free memory, free disk — must be re-read via
    :func:`detect_hardware` at the moment they matter, such as immediately
    before a download or a job.
    """
    return _cached_hardware()
