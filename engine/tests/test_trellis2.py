"""TRELLIS.2 integration guards.

Nothing here downloads or runs anything: these pin the decisions that make the
install correct, each of which was established by reading a primary source and
would be expensive to rediscover.
"""

from __future__ import annotations

import json
import tokenize
from pathlib import Path

import pytest

from volum_core.models import ModelManager, all_models, get_install_spec
from volum_core.models.manager import CONFIG_DIRECTORY
from volum_core.models.registry import TRELLIS2
from volum_core.providers.factory import available_provider_ids, create_provider
from volum_core.providers.trellis2 import WORKER_SCRIPT, Trellis2Provider

GIB = 1024**3


@pytest.fixture
def spec():
    installer = get_install_spec("trellis2")
    assert installer is not None
    return installer


# --- what gets downloaded, and what deliberately does not -----------------


def test_the_texture_models_are_not_downloaded(spec) -> None:
    """They are 2.9 GB, STL carries no colour, and baking them needs an Xcode
    Metal toolchain that may not be present. Fetching them anyway would be
    three gigabytes for nothing."""
    files = [f for weight in spec.weights for f in weight.files]
    assert not any("tex_" in name for name in files)
    assert any("shape_" in name for name in files)


def test_only_the_512_pipeline_is_downloaded(spec) -> None:
    """The 1024 checkpoints are separate files and another 4.8 GB."""
    files = [f for weight in spec.weights for f in weight.files]
    assert any("_512_" in name for name in files)
    assert not any("_1024_" in name for name in files)


def test_the_download_fits_the_declared_disk_requirement(spec) -> None:
    """The requirement has to leave room for the environment as well as the
    weights, or preflight passes and the install runs the disk out."""
    assert spec.download_bytes < TRELLIS2.requirements.disk_bytes
    assert TRELLIS2.requirements.disk_bytes - spec.download_bytes > 2 * GIB


def test_the_sparse_structure_decoder_comes_from_the_other_repository(spec) -> None:
    """The 4B pipeline references it across repositories; missing it means the
    pipeline cannot construct."""
    assert any(w.repo_id == "microsoft/TRELLIS-image-large" for w in spec.weights)


# --- licence decisions ----------------------------------------------------


def test_background_removal_uses_the_mit_model(spec) -> None:
    """Upstream names briaai/RMBG-2.0, which is CC BY-NC and gated. BiRefNet is
    MIT, ungated, and the wrapper class's own default."""
    repos = {weight.repo_id for weight in spec.weights}
    assert "ZhengPeng7/BiRefNet" in repos
    assert "briaai/RMBG-2.0" not in repos


def test_the_pipeline_config_substitutes_background_removal() -> None:
    config = json.loads((CONFIG_DIRECTORY / "trellis2_geometry_512.json").read_text())
    assert config["args"]["rembg_model"]["args"]["model_name"] == "ZhengPeng7/BiRefNet"


def test_the_pipeline_config_names_only_downloaded_models(spec) -> None:
    """The loader fetches every model listed. A name without a file is a failure
    part-way through loading, minutes in."""
    config = json.loads((CONFIG_DIRECTORY / "trellis2_geometry_512.json").read_text())
    listed = set(config["args"]["models"])
    assert listed == {
        "sparse_structure_flow_model",
        "sparse_structure_decoder",
        "shape_slat_flow_model_512",
        "shape_slat_decoder",
    }


def test_the_pipeline_config_defaults_to_512() -> None:
    """Upstream defaults to 1024_cascade, whose weights are not installed."""
    config = json.loads((CONFIG_DIRECTORY / "trellis2_geometry_512.json").read_text())
    assert config["args"]["default_pipeline_type"] == "512"


def test_dinov3_is_marked_as_needing_human_approval(spec) -> None:
    """A token is not enough: access is granted by a person and can take days.
    Worth saying before a download starts, not after it fails."""
    dinov3 = next(w for w in spec.weights if "dinov3" in w.repo_id)
    assert dinov3.gated
    assert dinov3.manual_approval


# --- pinning and prerequisites --------------------------------------------


def test_every_source_is_commit_pinned(spec) -> None:
    for source in spec.sources:
        assert len(source.commit) == 40, f"{source.directory} is not pinned"


def test_the_metal_packages_are_optional_and_gated_on_the_toolchain(spec) -> None:
    """They cannot build without an Xcode Metal compiler, and they only affect
    texture baking — which this install does not do. A failed build must not
    cost the user the model."""
    metal = [s for s in spec.sources if s.directory.startswith("trellis-mac/deps/mtl")]
    assert len(metal) == 4
    for source in metal:
        assert source.optional
        assert source.requires == ("metal-toolchain",)
        assert source.no_build_isolation


def test_the_metal_prerequisite_is_not_required(spec) -> None:
    prerequisite = next(p for p in spec.prerequisites if p.name == "metal-toolchain")
    assert not prerequisite.required
    assert prerequisite.consequence_if_missing
    assert "STL" in prerequisite.consequence_if_missing


def test_mps_fallback_is_set_before_torch_loads(spec) -> None:
    """Several operations have no MPS kernel; without this they raise instead of
    falling back. Setting it after the first torch import does nothing, silently."""
    assert spec.worker_env["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"


# --- the provider ---------------------------------------------------------


def test_the_worker_script_exists() -> None:
    assert WORKER_SCRIPT.exists()


def _code_only(path: Path) -> str:
    """Source with comments and docstrings removed.

    Necessary, not fussy: this file's own docstring states the rule it checks,
    so a plain text search finds the prohibition and calls it a violation. A
    test that cannot fail for the right reason is not a test.
    """
    kept: list[str] = []
    previous = tokenize.INDENT
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING and previous in (
                tokenize.INDENT,
                tokenize.NEWLINE,
                tokenize.NL,
                tokenize.ENCODING,
            ):
                continue  # a docstring
            kept.append(token.string)
            if token.type not in (tokenize.NL, tokenize.NEWLINE):
                previous = token.type
    return " ".join(kept)


def test_the_worker_never_imports_the_engine() -> None:
    """It runs in the provider's environment, where volum_core is not installed."""
    assert "volum_core" not in _code_only(WORKER_SCRIPT)


def test_the_worker_matches_the_gpu_watchdog_signatures() -> None:
    """A killed Metal kernel surfaces as an index error deep in a library. The
    port documents the two signatures; matching them is the difference between a
    usable message and a mystery. Checked in code, not in prose."""
    code = _code_only(WORKER_SCRIPT)
    assert "BVH needs at least 8 triangles" in code
    assert "non-zero size" in code


def test_an_uninstalled_provider_says_so(tmp_path: Path) -> None:
    provider = Trellis2Provider(ModelManager(tmp_path / "models"))
    report = provider.validate_environment()
    assert not report.ok
    assert "not installed" in report.problems[0]


def test_the_factory_knows_both_providers() -> None:
    assert set(available_provider_ids()) == {"triposr", "trellis2"}


def test_the_factory_returns_none_for_a_model_without_an_implementation(
    tmp_path: Path,
) -> None:
    """A model can be catalogued and licence-checked before it has code."""
    manager = ModelManager(tmp_path / "models")
    assert create_provider("something-else", manager, device="cpu") is None


def test_every_registry_model_has_an_implementation() -> None:
    """If this fails, the catalogue is advertising something that cannot run."""
    for model in all_models():
        assert model.id in available_provider_ids(), model.id
