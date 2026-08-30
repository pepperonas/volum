"""Guards on the model registry.

These are contract tests about honesty, not about data entry. Each one pins a
claim VOLUM makes to users and would notice if it quietly changed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from volum_core.models import all_models, get_model
from volum_core.models.registry import LICENSES_VERIFIED_ON, TRELLIS2, TRIPOSR
from volum_core.providers.types import Capability, CommercialUse

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_model_ids_are_unique() -> None:
    ids = [m.id for m in all_models()]
    assert len(ids) == len(set(ids))


def test_get_model_returns_none_for_unknown_ids() -> None:
    assert get_model("does-not-exist") is None


@pytest.mark.parametrize("model", all_models(), ids=lambda m: m.id)
def test_every_model_declares_a_verified_licence_date(model) -> None:
    assert model.license.verified_on, f"{model.id} has no licence verification date"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", model.license.verified_on)


@pytest.mark.parametrize("model", all_models(), ids=lambda m: m.id)
def test_no_bundled_model_carries_a_territorial_restriction(model) -> None:
    """A territorially restricted model is blocked by gating anyway, so shipping
    one in the registry would only ever produce a dead entry in the UI."""
    assert model.license.territorial_restriction is None


@pytest.mark.parametrize("model", all_models(), ids=lambda m: m.id)
def test_peak_memory_is_at_least_the_minimum(model) -> None:
    requirements = model.requirements
    assert requirements.estimated_peak_memory_bytes >= requirements.minimum_memory_bytes


@pytest.mark.parametrize("model", all_models(), ids=lambda m: m.id)
def test_estimates_declare_where_they_came_from(model) -> None:
    """An unattributed number hardens into a fact. Provenance keeps it a claim."""
    assert model.requirements.numbers_source


def test_hunyuan3d_is_not_in_the_registry() -> None:
    """Excluded on licence grounds (EU territorial exclusion), not technical ones.
    If someone adds it back, that has to be a deliberate, reviewed act."""
    assert all("hunyuan" not in m.id.lower() for m in all_models())


def test_no_v1_provider_claims_multi_image() -> None:
    """Spec section 14: multi-image must not be simulated. The generation models
    are single-image, and TRELLIS.2's own tracker reports multi-image conditioning
    performing worse."""
    for model in all_models():
        assert Capability.MULTI_IMAGE not in model.capabilities


def test_trellis2_commercial_use_is_conditional_and_says_why() -> None:
    """It was UNKNOWN until the Metal replacements were checked: mtldiffrast and
    friends are MIT, and mtldiffrast is implemented from the paper rather than
    ported from NVIDIA's code, so nvdiffrast's non-commercial clause does not
    reach the Apple path. Conditional, not allowed: the CUDA path is still
    blocked, DINOv3 needs its notice, and RMBG-2.0 must stay substituted."""
    assert TRELLIS2.license.commercial_use is CommercialUse.CONDITIONAL
    detail = TRELLIS2.license.commercial_use_detail or ""
    assert "nvdiffrast" in detail
    assert "DINOv3" in detail
    assert "BiRefNet" in detail


def test_the_metal_replacements_are_recorded_as_mit() -> None:
    """The finding that unblocked commercial use. Losing it would lose the reason
    the Apple path is preferred over the CUDA one."""
    licences = TRELLIS2.license.dependency_licenses
    for package in ("mtldiffrast", "mtlgemm", "mtlbvh", "mtlmesh"):
        assert licences[package].startswith("MIT"), package


def test_trellis2_records_the_nvdiffrast_problem() -> None:
    """The finding that makes the macOS path the licence-clean one. Losing this
    note would lose the reason the architecture looks the way it does."""
    assert "NON-COMMERCIAL" in TRELLIS2.license.dependency_licenses["nvdiffrast"]


def test_trellis2_requires_a_gated_download() -> None:
    """DINOv3 is gated; the manager has to explain that rather than show a 401."""
    assert TRELLIS2.requirements.requires_gated_download


def test_trellis2_carries_the_dinov3_attribution_requirement() -> None:
    assert TRELLIS2.license.attribution_required == "Built with DINOv3"


def test_triposr_is_unconditionally_usable() -> None:
    """Its entire role in V1 depends on having no strings attached."""
    assert TRIPOSR.license.commercial_use is CommercialUse.ALLOWED
    assert TRIPOSR.license.weights_license == "MIT"
    assert not TRIPOSR.license.dependency_licenses


def test_at_least_one_model_supports_cpu() -> None:
    """Without a CPU-capable provider, CI cannot exercise the pipeline for real
    and 'no mocks in the production path' becomes unenforceable."""
    assert any("cpu" in m.requirements.supported_runtimes for m in all_models())


def test_licence_verification_date_matches_the_documentation() -> None:
    """docs/licenses.md and the registry must not drift apart."""
    text = (DOCS / "licenses.md").read_text(encoding="utf-8")
    assert LICENSES_VERIFIED_ON in text
