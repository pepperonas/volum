from __future__ import annotations

from volum_core.hardware import run_doctor
from volum_core.models import all_models
from volum_core.version import __version__


def test_doctor_assesses_every_registered_model() -> None:
    report = run_doctor()
    assert {a.provider_id for a in report.assessments} == {m.id for m in all_models()}


def test_doctor_reports_the_running_version() -> None:
    assert run_doctor().volum_version == __version__


def test_doctor_is_json_serialisable() -> None:
    """It is served over HTTP to the frontend unchanged."""
    payload = run_doctor().model_dump_json()
    assert '"hardware"' in payload
    assert '"assessments"' in payload


def test_doctor_never_raises_regardless_of_machine() -> None:
    report = run_doctor()
    assert report.recommended_runtime is not None


def test_doctor_warns_when_pytorch_is_absent() -> None:
    """Expected before the first model install — but it must be stated, not silent."""
    report = run_doctor()
    if report.hardware.torch_version is None:
        assert any("PyTorch" in warning for warning in report.warnings)
