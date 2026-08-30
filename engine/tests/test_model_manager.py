"""Model manager tests.

Nothing here touches the network. The install is exercised through a fake `uv`
and a fake `git` so the orchestration is genuinely tested — the alternative,
mocking the manager's own methods, would test nothing but the mock.

The real download is covered by a marked integration test that CI skips.
"""

from __future__ import annotations

import os
import sys
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
from volum_core.models.manager import (
    MANIFEST_FILE,
    SHIM_DIRECTORY,
    CommandResult,
    InstallManifest,
    run_command,
)


@pytest.fixture
def manager(tmp_path: Path) -> ModelManager:
    return ModelManager(tmp_path / "models")


def _site_packages(manager: ModelManager, model_id: str) -> Path:
    """Where a virtual environment keeps site-packages on this platform.

    Windows uses ``Lib/site-packages``; POSIX uses ``lib/pythonX.Y/site-packages``.
    Hardcoding the POSIX form made a test build a layout the manager could not
    recognise on Windows — which is the same class of mistake the production
    code had in `python_executable`.
    """
    venv = manager.venv_dir(model_id)
    if os.name == "nt":
        return venv / "Lib" / "site-packages"
    return (
        venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )


class FakeRunner:
    """Stands in for the external commands an install shells out to.

    Injected rather than faked on disk. Writing a fake executable works on
    POSIX and cannot work on Windows — CreateProcess will not run a batch file
    directly, which is how the Windows leg of CI failed. Injecting the runner
    tests the orchestration (which commands, in what order, and what must exist
    afterwards) without depending on the operating system at all.
    """

    def __init__(
        self,
        *,
        dirs: tuple[Path, ...] = (),
        files: tuple[Path, ...] = (),
        fail_with: str | None = None,
        freeze_output: str = "",
    ) -> None:
        self.dirs = dirs
        self.files = files
        self.fail_with = fail_with
        self.freeze_output = freeze_output
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout: float) -> CommandResult:
        self.calls.append(args)
        if self.fail_with is not None:
            return CommandResult(returncode=1, stderr=self.fail_with)
        if "freeze" in args:
            return CommandResult(returncode=0, stdout=self.freeze_output)
        for directory in self.dirs:
            directory.mkdir(parents=True, exist_ok=True)
        for file in self.files:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.touch()
        return CommandResult(returncode=0)


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
        sources=(
            SourceSpec(url="https://example.invalid/repo.git", commit="a" * 40, directory="repo"),
        ),
        weights=(),
        shims=(),
    )


def _install_with_fakes(
    manager: ModelManager, spec: InstallSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, str]]:
    """Run an install with every external command replaced."""
    manager._run_command = FakeRunner(
        dirs=(
            _site_packages(manager, spec.model_id),
            manager.source_dir(spec.model_id),
            manager.source_dir(spec.model_id) / "repo",
        ),
        files=(manager.python_executable(spec.model_id),),
        freeze_output="torch==2.13.0\ntrimesh==4.4.0\n",
    )
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
    assert manifest.source_commits == {"repo": "a" * 40}


def test_install_reports_progress_for_each_phase(
    manager: ModelManager, fake_spec: InstallSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = dict(_install_with_fakes(manager, fake_spec, tmp_path, monkeypatch))
    assert {"environment", "dependencies", "source", "done"} <= set(steps)


def test_a_failing_tool_leaves_no_manifest(
    manager: ModelManager, fake_spec: InstallSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The completion marker must not appear for a broken install."""
    manager._run_command = FakeRunner(fail_with="boom")
    monkeypatch.setattr(manager, "preflight", lambda *a, **k: None)
    monkeypatch.setattr("volum_core.models.manager.get_install_spec", lambda model_id: fake_spec)

    with pytest.raises(ModelInstallError) as excinfo:
        manager.install("fakemodel")
    assert "boom" in excinfo.value.technical
    assert manager.state("fakemodel") is InstallState.INCOMPLETE


def test_a_missing_tool_says_which_one(
    manager: ModelManager, fake_spec: InstallSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(args: list[str], timeout: float) -> CommandResult:
        raise FileNotFoundError(args[0])

    manager._uv = "/definitely/not/a/real/uv"
    manager._run_command = missing
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
    manager._run_command = FakeRunner(files=(manager.python_executable("gatedmodel"),))
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
    site_packages = _site_packages(manager, "shimmed")
    manager._run_command = FakeRunner(
        dirs=(site_packages,),
        files=(manager.python_executable("shimmed"),),
    )
    monkeypatch.setattr(manager, "preflight", lambda *a, **k: None)
    monkeypatch.setattr("volum_core.models.manager.get_install_spec", lambda model_id: spec)

    manager.install("shimmed")
    shim = site_packages / "torchmcubes.py"
    assert shim.exists()
    assert "PyMCubes" in shim.read_text(encoding="utf-8")


# --- verify and remove ----------------------------------------------------


def test_verify_reports_a_missing_install(manager: ModelManager) -> None:
    assert manager.verify("triposr") == ["triposr is not installed."]


def test_verify_flags_missing_and_empty_weights(manager: ModelManager, tmp_path: Path) -> None:
    directory = manager.directory("triposr")
    (directory / "weights").mkdir(parents=True)
    (directory / "source").mkdir(parents=True)
    interpreter = manager.python_executable("triposr")
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
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
    assert spec.sources
    for source in spec.sources:
        assert len(source.commit) == 40, f"{source.directory} is not commit-pinned"
        assert source.commit.isalnum()


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


# --- the real runner ------------------------------------------------------
#
# The tests above inject a fake runner so install orchestration can be checked
# without spawning processes. These cover the runner itself, using the current
# interpreter so they work identically on every platform in the matrix.


def test_the_real_runner_captures_output() -> None:
    result = run_command([sys.executable, "-c", "print('hello')"], 30)
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_the_real_runner_reports_failure_without_raising() -> None:
    """Failures come back as data, so _run can turn them into a message with
    the command's own stderr in it."""
    result = run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(2)"], 30
    )
    assert result.returncode == 2
    assert "bad" in result.stderr


def test_the_real_runner_raises_for_a_missing_binary() -> None:
    with pytest.raises(FileNotFoundError):
        run_command(["/definitely/not/a/real/binary"], 30)


def test_install_records_the_resolved_environment(
    manager: ModelManager, fake_spec: InstallSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The install spec cannot carry upstream's own pins, so the manifest records
    what was actually resolved. Without it a result is not explicable later."""
    _install_with_fakes(manager, fake_spec, tmp_path, monkeypatch)
    manifest = manager.manifest("fakemodel")
    assert manifest is not None
    assert "torch==2.13.0" in manifest.frozen_requirements


# --- the venv layout ------------------------------------------------------
#
# Both branches are exercised on every platform. Without this, the Windows
# layout was only ever checked by the Windows leg of CI — which is how the
# POSIX path came to be hardcoded twice, once in the manager and once here.


def test_interpreter_path_follows_the_platform(
    manager: ModelManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("volum_core.models.manager.os.name", "nt")
    assert manager.python_executable("m").parts[-2:] == ("Scripts", "python.exe")

    monkeypatch.setattr("volum_core.models.manager.os.name", "posix")
    assert manager.python_executable("m").parts[-2:] == ("bin", "python")


def test_interpreter_path_does_not_depend_on_what_exists(manager: ModelManager) -> None:
    """It is needed before the environment is created, which is when an
    existence check returns the wrong platform's answer."""
    before = manager.python_executable("m")
    before.parent.mkdir(parents=True)
    before.touch()
    assert manager.python_executable("m") == before


def test_site_packages_is_found_in_both_layouts(manager: ModelManager) -> None:
    """The manager discovers rather than declares this, because the Python
    minor version is part of the POSIX path and is not known statically."""
    venv = manager.venv_dir("m")

    (venv / "Lib" / "site-packages").mkdir(parents=True)
    assert manager._site_packages("m") == venv / "Lib" / "site-packages"

    posix = venv / "lib" / "python3.11" / "site-packages"
    posix.mkdir(parents=True)
    assert manager._site_packages("m") == posix
