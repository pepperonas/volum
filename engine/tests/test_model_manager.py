"""Model manager tests.

Nothing here touches the network. The install is exercised through a fake `uv`
and a fake `git` so the orchestration is genuinely tested — the alternative,
mocking the manager's own methods, would test nothing but the mock.

The real download is covered by a marked integration test that CI skips.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from volum_core.models import (
    InstallState,
    ModelInstallError,
    ModelManager,
    get_install_spec,
)
from volum_core.models.install_spec import (
    INSTALL_SPECS,
    InstallSpec,
    ShimSpec,
    SourceSpec,
    WeightSpec,
)
from volum_core.models.manager import MANIFEST_FILE, SHIM_DIRECTORY, InstallManifest


@pytest.fixture
def manager(tmp_path: Path) -> ModelManager:
    return ModelManager(tmp_path / "models")


def _fake_binary(path: Path, script: str) -> Path:
    """Write an executable stand-in for uv or git."""
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


# --- layout and validation ------------------------------------------------


def test_model_ids_are_validated_not_trusted(manager: ModelManager) -> None:
    """Ids reach this from HTTP paths and CLI arguments (spec section 50)."""
    for bad in ("../escape", "a/b", "..", "a b", "a;rm -rf /"):
        with pytest.raises(ValueError, match="Invalid model id"):
            manager.directory(bad)


def test_hyphens_and_underscores_are_accepted(manager: ModelManager) -> None:
    assert manager.directory("stable-fast_3d").name == "stable-fast_3d"


def test_a_missing_model_is_not_installed(manager: ModelManager) -> None:
    assert manager.state("triposr") is InstallState.NOT_INSTALLED


def test_a_directory_without_a_manifest_is_incomplete(manager: ModelManager) -> None:
    """The manifest is written last, so its absence means the install stopped
    partway. The distinction matters: this needs cleaning up first."""
    manager.directory("triposr").mkdir(parents=True)
    assert manager.state("triposr") is InstallState.INCOMPLETE


def test_a_manifest_without_an_interpreter_is_incomplete(manager: ModelManager) -> None:
    directory = manager.directory("triposr")
    directory.mkdir(parents=True)
    (directory / MANIFEST_FILE).write_text(
        InstallManifest(
            model_id="triposr", volum_version="0.1.0", python_version="3.11"
        ).model_dump_json(),
        encoding="utf-8",
    )
    assert manager.state("triposr") is InstallState.INCOMPLETE


def test_a_corrupt_manifest_reads_as_incomplete(manager: ModelManager) -> None:
    directory = manager.directory("triposr")
    directory.mkdir(parents=True)
    (directory / MANIFEST_FILE).write_text("{ not json", encoding="utf-8")
    assert manager.state("triposr") is InstallState.INCOMPLETE


# --- disk accounting ------------------------------------------------------


def test_disk_usage_reports_real_bytes(manager: ModelManager) -> None:
    """Walked from disk rather than taken from the declared requirement: the two
    differ, and the user cares about the one consuming their disk."""
    directory = manager.directory("triposr")
    (directory / "weights").mkdir(parents=True)
    (directory / "weights" / "model.ckpt").write_bytes(b"x" * 2048)
    assert manager.disk_usage("triposr")["triposr"] == 2048


def test_disk_usage_covers_every_installed_model(manager: ModelManager) -> None:
    for name, size in (("triposr", 100), ("trellis2", 200)):
        directory = manager.directory(name)
        directory.mkdir(parents=True)
        (directory / "blob").write_bytes(b"x" * size)
    assert manager.disk_usage() == {"triposr": 100, "trellis2": 200}


def test_disk_usage_survives_a_broken_symlink(manager: ModelManager) -> None:
    directory = manager.directory("triposr")
    directory.mkdir(parents=True)
    (directory / "dangling").symlink_to(directory / "does-not-exist")
    assert manager.disk_usage("triposr")["triposr"] == 0


# --- preflight ------------------------------------------------------------


def test_preflight_rejects_an_unknown_model(manager: ModelManager) -> None:
    with pytest.raises(ModelInstallError, match="Unknown model"):
        manager.preflight("no-such-model")


def test_preflight_rejects_a_model_without_an_installer(manager: ModelManager) -> None:
    """TRELLIS.2 is in the registry but has no installer yet. Saying so beats
    failing halfway through an install."""
    with pytest.raises(ModelInstallError, match="no installer yet"):
        manager.preflight("trellis2")


def test_preflight_error_carries_suggestions(manager: ModelManager) -> None:
    """A refusal the user cannot act on is not much better than a crash."""
    with pytest.raises(ModelInstallError) as excinfo:
        manager.preflight("trellis2")
    assert excinfo.value.technical


# --- install orchestration ------------------------------------------------


@pytest.fixture
def fake_spec(tmp_path: Path) -> InstallSpec:
    return InstallSpec(
        model_id="fakemodel",
        python_version="3.11",
        pip_packages=("nothing-real",),
        source=SourceSpec(url="https://example.invalid/repo.git", commit="a" * 40),
        weights=(),
        shims=(),
    )


def _install_with_fakes(
    manager: ModelManager, spec: InstallSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, str]]:
    """Run an install with uv and git replaced by scripts that create the layout."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    venv_python = manager.venv_dir(spec.model_id) / "bin" / "python"
    site_packages = manager.venv_dir(spec.model_id) / "lib" / "python3.11" / "site-packages"
    _fake_binary(
        bin_dir / "uv",
        f'mkdir -p "{venv_python.parent}" "{site_packages}" && touch "{venv_python}" '
        f'&& chmod +x "{venv_python}"',
    )
    _fake_binary(bin_dir / "git", f'mkdir -p "{manager.source_dir(spec.model_id)}"')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    manager._uv = str(bin_dir / "uv")

    monkeypatch.setattr(manager, "preflight", lambda *a, **k: None)
    monkeypatch.setattr("volum_core.models.manager.get_install_spec", lambda model_id: spec)

    steps: list[tuple[str, str]] = []
    manager.install(spec.model_id, on_progress=lambda step, message: steps.append((step, message)))
    return steps


def test_install_writes_the_manifest_last(
    manager: ModelManager, fake_spec: InstallSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = _install_with_fakes(manager, fake_spec, tmp_path, monkeypatch)
    assert manager.state("fakemodel") is InstallState.INSTALLED
    assert steps[-1][0] == "done"


def test_install_records_the_pinned_commit(
    manager: ModelManager, fake_spec: InstallSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproducibility claims rest on this being an exact commit."""
    _install_with_fakes(manager, fake_spec, tmp_path, monkeypatch)
    manifest = manager.manifest("fakemodel")
    assert manifest is not None
    assert manifest.source_commit == "a" * 40


def test_install_reports_progress_for_each_phase(
    manager: ModelManager, fake_spec: InstallSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = dict(_install_with_fakes(manager, fake_spec, tmp_path, monkeypatch))
    assert {"environment", "dependencies", "source", "done"} <= set(steps)


def test_a_failing_tool_leaves_no_manifest(
    manager: ModelManager, fake_spec: InstallSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The completion marker must not appear for a broken install."""
    bin_dir = tmp_path / "failbin"
    bin_dir.mkdir()
    _fake_binary(bin_dir / "uv", "echo 'boom' >&2; exit 1")
    manager._uv = str(bin_dir / "uv")
    monkeypatch.setattr(manager, "preflight", lambda *a, **k: None)
    monkeypatch.setattr("volum_core.models.manager.get_install_spec", lambda model_id: fake_spec)

    with pytest.raises(ModelInstallError) as excinfo:
        manager.install("fakemodel")
    assert "boom" in excinfo.value.technical
    assert manager.state("fakemodel") is InstallState.INCOMPLETE


def test_a_missing_tool_says_which_one(
    manager: ModelManager, fake_spec: InstallSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._uv = "/definitely/not/a/real/uv"
    monkeypatch.setattr(manager, "preflight", lambda *a, **k: None)
    monkeypatch.setattr("volum_core.models.manager.get_install_spec", lambda model_id: fake_spec)
    with pytest.raises(ModelInstallError, match="uv"):
        manager.install("fakemodel")


def test_gated_weights_without_a_token_explain_themselves(
    manager: ModelManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare 401 is the failure this message exists to prevent."""
    spec = InstallSpec(
        model_id="gatedmodel",
        weights=(WeightSpec(repo_id="meta/gated", revision="abc", files=("w.bin",), gated=True),),
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    venv_python = manager.venv_dir("gatedmodel") / "bin" / "python"
    _fake_binary(bin_dir / "uv", f'mkdir -p "{venv_python.parent}" && touch "{venv_python}"')
    manager._uv = str(bin_dir / "uv")
    monkeypatch.setattr(manager, "preflight", lambda *a, **k: None)
    monkeypatch.setattr("volum_core.models.manager.get_install_spec", lambda model_id: spec)

    with pytest.raises(ModelInstallError) as excinfo:
        manager.install("gatedmodel")
    assert "accept" in excinfo.value.message.lower()
    assert any("huggingface.co" in s for s in excinfo.value.suggestions)


def test_shims_are_copied_into_the_provider_environment(
    manager: ModelManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Written into the provider's environment, not into the cloned source, so
    the upstream checkout stays exactly what its commit says it is."""
    spec = InstallSpec(
        model_id="shimmed",
        shims=(
            ShimSpec(
                module_name="torchmcubes",
                source_file="torchmcubes_shim.py",
                reason="test",
            ),
        ),
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    venv = manager.venv_dir("shimmed")
    _fake_binary(
        bin_dir / "uv",
        f'mkdir -p "{venv}/bin" "{venv}/lib/python3.11/site-packages" && touch "{venv}/bin/python"',
    )
    manager._uv = str(bin_dir / "uv")
    monkeypatch.setattr(manager, "preflight", lambda *a, **k: None)
    monkeypatch.setattr("volum_core.models.manager.get_install_spec", lambda model_id: spec)

    manager.install("shimmed")
    shim = venv / "lib" / "python3.11" / "site-packages" / "torchmcubes.py"
    assert shim.exists()
    assert "PyMCubes" in shim.read_text(encoding="utf-8")


# --- verify and remove ----------------------------------------------------


def test_verify_reports_a_missing_install(manager: ModelManager) -> None:
    assert manager.verify("triposr") == ["triposr is not installed."]


def test_verify_flags_missing_and_empty_weights(manager: ModelManager, tmp_path: Path) -> None:
    directory = manager.directory("triposr")
    (directory / "weights").mkdir(parents=True)
    (directory / "source").mkdir(parents=True)
    (directory / "venv" / "bin").mkdir(parents=True)
    (directory / "venv" / "bin" / "python").touch()
    (directory / "weights" / "config.yaml").write_bytes(b"")  # empty on purpose
    (directory / MANIFEST_FILE).write_text(
        InstallManifest(
            model_id="triposr", volum_version="0.1.0", python_version="3.11"
        ).model_dump_json(),
        encoding="utf-8",
    )

    problems = manager.verify("triposr")
    assert any("empty" in p for p in problems)
    assert any("model.ckpt" in p for p in problems)


def test_remove_deletes_everything(manager: ModelManager) -> None:
    directory = manager.directory("triposr")
    (directory / "weights").mkdir(parents=True)
    (directory / "weights" / "big.ckpt").write_bytes(b"x" * 10)
    assert manager.remove("triposr")
    assert not directory.exists()
    assert manager.state("triposr") is InstallState.NOT_INSTALLED


def test_removing_something_absent_is_false_not_an_error(manager: ModelManager) -> None:
    assert manager.remove("triposr") is False


# --- specs ----------------------------------------------------------------


def test_triposr_pins_an_exact_commit() -> None:
    """A branch name is not a pin, and 'main' would make every reproducibility
    claim VOLUM prints false."""
    spec = get_install_spec("triposr")
    assert spec is not None
    assert spec.source is not None
    assert len(spec.source.commit) == 40
    assert spec.source.commit.isalnum()


def test_triposr_pins_a_weight_revision() -> None:
    spec = get_install_spec("triposr")
    assert spec is not None
    assert spec.weights[0].revision != "main"


def test_triposr_requires_no_token() -> None:
    spec = get_install_spec("triposr")
    assert spec is not None
    assert not spec.requires_token


def test_triposr_ships_the_torchmcubes_shim() -> None:
    """Upstream's torchmcubes has no wheels and does not build usefully on Apple
    Silicon; without the substitution the provider cannot install at all."""
    spec = get_install_spec("triposr")
    assert spec is not None
    assert [shim.module_name for shim in spec.shims] == ["torchmcubes"]
    assert "torchmcubes" not in spec.pip_packages


def test_every_shim_file_exists() -> None:
    for spec in INSTALL_SPECS.values():
        for shim in spec.shims:
            assert (SHIM_DIRECTORY / shim.source_file).exists()
            assert shim.reason, "a shim must say why it exists"
