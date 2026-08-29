"""``volum doctor`` — the report the whole product depends on.

It answers one question honestly: *what can this machine actually do?* Every
later decision — which models the manager offers, which runtime a job uses,
whether an install is even attempted — is downstream of this, which is why it
is the first thing built (spec section 33).

This module produces **data**. Rendering lives in the CLI, so the report stays
testable and can be served over the API unchanged.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..models.registry import all_models
from ..providers.gating import Assessment, assess
from ..version import __version__
from .detect import detect_hardware
from .types import HardwareInfo, RuntimeKind

#: Below this, model installs start failing in ways users read as bugs.
_LOW_DISK_WARNING_GIB = 30


class DoctorReport(BaseModel):
    volum_version: str
    hardware: HardwareInfo
    recommended_runtime: RuntimeKind
    assessments: list[Assessment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def runnable_model_ids(self) -> list[str]:
        return [a.provider_id for a in self.assessments if a.can_run]


def _warnings(hardware: HardwareInfo, assessments: list[Assessment]) -> list[str]:
    """Machine-level warnings that are not tied to one model.

    Kept separate from per-model reasons on purpose: "your disk is nearly full"
    is advice about the machine, and repeating it once per model would bury it.
    """
    warnings: list[str] = []

    if hardware.disk_free_bytes is not None:
        free_gib = hardware.disk_free_bytes / (1024**3)
        if free_gib < _LOW_DISK_WARNING_GIB:
            warnings.append(
                f"Only {free_gib:.0f} GB of disk space is free. Model weights are large; "
                "consider moving the VOLUM data directory to another volume."
            )

    if not any(a.can_run for a in assessments):
        warnings.append(
            "No model in the registry can run on this machine as configured. "
            "The reasons are listed per model above."
        )

    if hardware.torch_version is None:
        warnings.append(
            "PyTorch is not installed in the engine environment, so runtimes marked "
            "'expected' are inferred from the hardware rather than confirmed. This is "
            "normal before the first model is installed — providers bring their own "
            "environments, and the runtime is verified then."
        )

    return warnings


def run_doctor(data_dir: Path | None = None, *, deep: bool = True) -> DoctorReport:
    """Detect hardware and assess every known model against it.

    ``deep`` defaults to ``True`` here: the doctor is user-invoked, so a second
    of extra probing buys accuracy that start-up cannot afford.
    """
    hardware = detect_hardware(data_dir=data_dir, deep=deep)
    assessments = [assess(model, hardware) for model in all_models()]
    return DoctorReport(
        volum_version=__version__,
        hardware=hardware,
        recommended_runtime=hardware.recommended_runtime,
        assessments=assessments,
        warnings=_warnings(hardware, assessments),
    )
