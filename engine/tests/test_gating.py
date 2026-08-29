"""Gating is where spec section 7 lives, so it gets the most tests.

Everything here is pure logic over declared requirements and detected hardware:
no GPU, no downloads, no ML imports. That is what lets CI verify the behaviour
that actually protects users from crash loops.
"""

from __future__ import annotations

import pytest

from conftest import GIB, make_hardware
from volum_core.hardware.types import Availability
from volum_core.models.registry import TRELLIS2, TRIPOSR
from volum_core.providers.gating import Verdict, assess
from volum_core.providers.types import (
    Capability,
    CommercialUse,
    LicenseMetadata,
    ProviderMetadata,
    Requirements,
)


def make_metadata(
    *,
    model_id: str = "test",
    platforms: frozenset[str] = frozenset({"Darwin", "Linux", "Windows"}),
    runtimes: frozenset[str] = frozenset({"mps", "cuda", "cpu"}),
    minimum_gib: float = 4,
    peak_gib: float = 6,
    disk_gib: float = 5,
    apple_silicon: bool = False,
    commercial: CommercialUse = CommercialUse.ALLOWED,
    territory: str | None = None,
) -> ProviderMetadata:
    return ProviderMetadata(
        id=model_id,
        name=model_id,
        version="1",
        description="",
        capabilities=frozenset({Capability.SINGLE_IMAGE}),
        requirements=Requirements(
            supported_platforms=platforms,
            supported_runtimes=runtimes,
            minimum_memory_bytes=int(minimum_gib * GIB),
            estimated_peak_memory_bytes=int(peak_gib * GIB),
            disk_bytes=int(disk_gib * GIB),
            requires_apple_silicon=apple_silicon,
        ),
        license=LicenseMetadata(
            code_license="MIT",
            weights_license="MIT",
            commercial_use=commercial,
            territorial_restriction=territory,
        ),
    )


# --- the happy path -------------------------------------------------------


def test_model_that_fits_is_runnable() -> None:
    result = assess(make_metadata(peak_gib=6), make_hardware(ram_gib=16))
    assert result.verdict is Verdict.RUNNABLE
    assert result.can_run


def test_runnable_model_reports_the_runtime_it_would_use() -> None:
    result = assess(make_metadata(), make_hardware(mps=Availability.AVAILABLE))
    assert result.runtime is not None
    assert result.runtime.value == "mps"


# --- memory ---------------------------------------------------------------


def test_slightly_too_large_is_marginal_not_blocked() -> None:
    """16 GB against an 18 GB peak: macOS will swap and it may well finish.
    Reporting BLOCKED here would deny a machine that would have worked."""
    result = assess(make_metadata(minimum_gib=16, peak_gib=18), make_hardware(ram_gib=16))
    assert result.verdict is Verdict.MARGINAL
    assert result.can_run
    assert result.overridable


def test_marginal_verdict_explains_swapping_on_unified_memory() -> None:
    result = assess(make_metadata(minimum_gib=16, peak_gib=18), make_hardware(ram_gib=16))
    assert any("page to disk" in reason.message for reason in result.reasons)


def test_marginal_verdict_does_not_mention_swap_on_discrete_gpu() -> None:
    """A discrete GPU does not page VRAM to disk; saying so would be nonsense."""
    result = assess(
        make_metadata(minimum_gib=16, peak_gib=18),
        make_hardware(ram_gib=64, unified=False, vram_gib=16, cuda=Availability.AVAILABLE),
    )
    assert result.verdict is Verdict.MARGINAL
    assert not any("page to disk" in reason.message for reason in result.reasons)


def test_far_too_large_is_blocked() -> None:
    result = assess(make_metadata(minimum_gib=20, peak_gib=24), make_hardware(ram_gib=8))
    assert result.verdict is Verdict.BLOCKED
    assert not result.can_run


def test_blocked_for_memory_states_both_numbers() -> None:
    """A refusal without the numbers is not actionable (spec section 55)."""
    result = assess(make_metadata(minimum_gib=24, peak_gib=24), make_hardware(ram_gib=8))
    message = " ".join(reason.message for reason in result.reasons)
    assert "24.0 GB" in message
    assert "8.0 GB" in message


def test_unknown_memory_yields_unknown_not_a_guess() -> None:
    hardware = make_hardware(ram_gib=None, unified=False, vram_gib=None)
    result = assess(make_metadata(), hardware)
    assert result.verdict is Verdict.UNKNOWN
    assert result.overridable


# --- platform and runtime -------------------------------------------------


def test_unsupported_platform_is_blocked() -> None:
    result = assess(
        make_metadata(platforms=frozenset({"Linux"})),
        make_hardware(os="Darwin"),
    )
    assert result.verdict is Verdict.BLOCKED
    assert any(r.code == "platform.unsupported" for r in result.reasons)


def test_apple_silicon_requirement_blocks_other_hardware() -> None:
    result = assess(
        make_metadata(apple_silicon=True),
        make_hardware(os="Linux", apple_silicon=False, unified=False, vram_gib=24),
    )
    assert result.verdict is Verdict.BLOCKED


def test_no_available_runtime_is_blocked_and_not_overridable() -> None:
    """There is nothing for the user to override: the code cannot execute."""
    result = assess(
        make_metadata(runtimes=frozenset({"cuda"})),
        make_hardware(cuda=Availability.UNAVAILABLE),
    )
    assert result.verdict is Verdict.BLOCKED
    assert not result.overridable


def test_cpu_only_machine_can_still_run_a_cpu_capable_model() -> None:
    hardware = make_hardware(
        os="Linux",
        apple_silicon=False,
        unified=False,
        vram_gib=None,
        ram_gib=32,
        mps=Availability.UNAVAILABLE,
    )
    hardware.unified_memory = False
    result = assess(make_metadata(runtimes=frozenset({"cpu"})), hardware)
    # Memory is unknown on this shape (no VRAM, not unified), so the honest
    # verdict is UNKNOWN with an override rather than a confident yes.
    assert result.verdict is Verdict.UNKNOWN
    assert result.runtime is not None and result.runtime.value == "cpu"


# --- disk -----------------------------------------------------------------


def test_insufficient_disk_is_blocked_and_suggests_moving_the_data_directory() -> None:
    result = assess(make_metadata(disk_gib=21), make_hardware(disk_free_gib=10))
    assert result.verdict is Verdict.BLOCKED
    assert any("data directory" in reason.message for reason in result.reasons)


def test_disk_is_checked_before_memory() -> None:
    """Order matters for the message the user sees: a full disk is the fixable
    problem, and mentioning memory first would send them down the wrong path."""
    result = assess(
        make_metadata(disk_gib=100, minimum_gib=64, peak_gib=64),
        make_hardware(ram_gib=8, disk_free_gib=5),
    )
    assert [r.code for r in result.reasons] == ["disk.insufficient"]


# --- licence --------------------------------------------------------------


def test_territorial_restriction_blocks_regardless_of_hardware() -> None:
    """No amount of RAM fixes a licence, so this gate runs first."""
    result = assess(
        make_metadata(territory="Excludes the European Union.", peak_gib=1),
        make_hardware(ram_gib=128),
    )
    assert result.verdict is Verdict.BLOCKED
    assert result.reasons[0].code == "license.territory"


def test_non_commercial_licence_warns_but_does_not_block() -> None:
    """VOLUM is usable for private and research work; the user is told and decides."""
    result = assess(make_metadata(commercial=CommercialUse.NOT_ALLOWED), make_hardware(ram_gib=32))
    assert result.verdict is Verdict.RUNNABLE
    assert any(r.code == "license.non_commercial" for r in result.reasons)


# --- the real registry on the real machine --------------------------------


def test_trellis2_is_marginal_on_a_16gb_machine(m1_pro_16gb) -> None:
    """Pinned deliberately. TRELLIS.2 peaks around 18 GB; on this 16 GB machine
    the honest answer is 'marginal, will swap', not 'runnable' and not 'blocked'.
    If this flips to RUNNABLE, someone has quietly lowered the estimate."""
    result = assess(TRELLIS2, m1_pro_16gb)
    assert result.verdict is Verdict.MARGINAL
    assert result.overridable


def test_triposr_is_runnable_on_a_16gb_machine(m1_pro_16gb) -> None:
    """The whole reason TripoSR is in V1: without it nothing runs here or in CI."""
    assert assess(TRIPOSR, m1_pro_16gb).verdict is Verdict.RUNNABLE


def test_trellis2_is_blocked_on_a_small_machine() -> None:
    assert assess(TRELLIS2, make_hardware(ram_gib=8)).verdict is Verdict.BLOCKED


def test_trellis2_is_blocked_when_the_disk_is_too_full() -> None:
    """29 GB free is fine; 15 GB is not. Real constraint on the dev machine."""
    assert assess(TRELLIS2, make_hardware(ram_gib=16, disk_free_gib=15)).verdict is Verdict.BLOCKED


@pytest.mark.parametrize("model", [TRELLIS2, TRIPOSR])
def test_registry_models_never_crash_the_assessor(model) -> None:
    """Detection can return almost anything; gating must survive all of it."""
    for hardware in (
        make_hardware(ram_gib=None, disk_free_gib=None),
        make_hardware(os="Windows", apple_silicon=False, unified=False),
        make_hardware(
            os="Linux", apple_silicon=False, unified=False, vram_gib=80, cuda=Availability.AVAILABLE
        ),
    ):
        assert assess(model, hardware).verdict in set(Verdict)


def test_assessment_is_serialisable() -> None:
    """It crosses the HTTP boundary to the frontend, so this must hold."""
    payload = assess(TRIPOSR, make_hardware()).model_dump_json()
    assert '"verdict"' in payload


# --- the clean-machine case ----------------------------------------------
#
# Found by running `volum doctor` on a machine with no provider installed:
# nothing could confirm MPS, so every model reported "no usable runtime" on a
# perfectly capable Mac. These pin the fix.


def test_expected_runtime_is_selectable() -> None:
    hardware = make_hardware(ram_gib=32, mps=Availability.EXPECTED)
    result = assess(make_metadata(runtimes=frozenset({"mps"})), hardware)
    assert result.verdict is Verdict.RUNNABLE
    assert result.runtime is not None and result.runtime.value == "mps"


def test_expected_runtime_says_it_is_not_yet_confirmed() -> None:
    """Selectable, but the user is told the difference."""
    hardware = make_hardware(ram_gib=32, mps=Availability.EXPECTED)
    result = assess(make_metadata(runtimes=frozenset({"mps"})), hardware)
    assert any(reason.code == "runtime.expected" for reason in result.reasons)


def test_confirmed_runtime_adds_no_caveat() -> None:
    hardware = make_hardware(ram_gib=32, mps=Availability.AVAILABLE)
    result = assess(make_metadata(runtimes=frozenset({"mps"})), hardware)
    assert not any(reason.code == "runtime.expected" for reason in result.reasons)


def test_unknown_runtime_is_still_not_selectable() -> None:
    """EXPECTED was added; UNKNOWN must keep its old, stricter meaning.
    Offering a model that then fails at import time is worse than declining it."""
    hardware = make_hardware(ram_gib=32, mps=Availability.UNKNOWN)
    result = assess(make_metadata(runtimes=frozenset({"mps"})), hardware)
    assert result.verdict is Verdict.BLOCKED


def test_trellis2_is_not_blocked_before_any_provider_is_installed() -> None:
    """The exact regression: a 16 GB Apple Silicon Mac with no PyTorch yet must
    see TRELLIS.2 as marginal-but-attemptable, not as 'no usable runtime'."""
    hardware = make_hardware(ram_gib=16, disk_free_gib=40, mps=Availability.EXPECTED)
    result = assess(TRELLIS2, hardware)
    assert result.verdict is Verdict.MARGINAL
    assert not any(reason.code == "runtime.unavailable" for reason in result.reasons)
