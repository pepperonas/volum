"""The Model Manager: install, verify, remove, account for disk.

Nothing here downloads anything the user did not ask for (spec section 12).
Installs are explicit, report real progress, and check free space first — on a
machine with 20 GB free, a half-finished 15 GB download is worse than a refusal.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from ..hardware.detect import detect_hardware
from ..providers.gating import Verdict, assess
from .install_spec import InstallSpec, get_install_spec
from .registry import get_model

MANIFEST_FILE = "install.json"
SHIM_DIRECTORY = Path(__file__).resolve().parent.parent / "providers" / "shims"
CONFIG_DIRECTORY = Path(__file__).resolve().parent / "pipeline_configs"

#: Installs pull large files and build environments; a short timeout would turn
#: a slow connection into a failure.
_INSTALL_TIMEOUT_S = 60 * 60

#: Headroom above the declared requirement. Downloads unpack, pip builds wheels,
#: and a disk that fills mid-install leaves a broken environment behind.
_DISK_HEADROOM_BYTES = 2 * 1024**3


class InstallState(StrEnum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    #: An install that started and did not finish. Distinguished from
    #: NOT_INSTALLED because the fix differs: this one needs cleaning up first.
    INCOMPLETE = "incomplete"


class InstallManifest(BaseModel):
    """What was installed, from where, and when. Written last, on purpose.

    Its presence is the completion marker: an install interrupted at any point
    leaves no manifest, so the state reads INCOMPLETE rather than INSTALLED.
    """

    model_id: str
    volum_version: str
    installed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    python_version: str
    source_commits: dict[str, str] = Field(default_factory=dict)
    skipped_components: list[str] = Field(
        default_factory=list,
        description="Optional parts that did not install. Recorded because they "
        "change how the model behaves, and a user comparing results needs to know.",
    )
    weight_revisions: dict[str, str] = Field(default_factory=dict)
    shims: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    worker_env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment the worker needs. Backends are chosen through "
        "environment variables read at import time, so this belongs to the install "
        "rather than to a single run.",
    )
    frozen_requirements: list[str] = Field(
        default_factory=list,
        description="The exact resolved environment. The install spec deliberately "
        "does not pin versions — the model's own 2024-era pins no longer install on "
        "a current interpreter — so this record is what makes a run reproducible "
        "after the fact, and what a job's metadata refers to.",
    )


class ModelInstallError(RuntimeError):
    """Install failure carrying a user-facing message and technical detail."""

    def __init__(self, message: str, technical: str = "", suggestions: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.technical = technical
        self.suggestions = suggestions or []


#: ``(step, message)`` — step names are stable, messages are for humans.
InstallProgress = Callable[[str, str], None]


class CommandResult(BaseModel):
    returncode: int
    stdout: str = ""
    stderr: str = ""


#: Runs one external command. Injectable so install orchestration can be tested
#: without spawning processes — which also removes a real platform problem:
#: faking an executable on Windows is not possible in the way it is on POSIX,
#: because CreateProcess will not run a batch file directly.
CommandRunner = Callable[[list[str], float], CommandResult]


def run_command(args: list[str], timeout: float) -> CommandResult:
    """The real runner. Fixed argv, never a shell (spec section 50)."""
    result = subprocess.run(  # noqa: S603
        args, capture_output=True, text=True, timeout=timeout, check=False
    )
    return CommandResult(
        returncode=result.returncode, stdout=result.stdout or "", stderr=result.stderr or ""
    )


def _noop(step: str, message: str) -> None:
    return None


class ModelManager:
    """Owns ``<data>/models/``."""

    def __init__(
        self,
        models_root: Path,
        *,
        uv_binary: str | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.root = models_root
        self.root.mkdir(parents=True, exist_ok=True)
        self._uv = uv_binary or shutil.which("uv") or "uv"
        self._run_command = runner or run_command

    # --- layout -----------------------------------------------------------

    def directory(self, model_id: str) -> Path:
        """Per-model directory, with the id validated rather than trusted."""
        if not model_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"Invalid model id: {model_id!r}")
        return self.root / model_id

    def venv_dir(self, model_id: str) -> Path:
        return self.directory(model_id) / "venv"

    def source_dir(self, model_id: str) -> Path:
        return self.directory(model_id) / "source"

    def weights_dir(self, model_id: str) -> Path:
        return self.directory(model_id) / "weights"

    def hf_cache_dir(self, model_id: str) -> Path:
        """The model's own Hugging Face cache.

        Kept per model rather than shared so that removing a model actually frees
        its disk, and so an install stays offline-capable: weights loaded by
        repository name resolve here instead of being fetched again on first run.
        """
        return self.directory(model_id) / "hf-cache"

    def python_executable(self, model_id: str) -> Path:
        """Where the provider's interpreter lives.

        Decided by platform, not by what happens to exist. The earlier
        existence check returned the Windows path on macOS whenever it was
        called before the environment had been created — which is precisely
        when an installer needs it.
        """
        venv = self.venv_dir(model_id)
        # os.name rather than sys.platform: mypy narrows sys.platform to the
        # platform it is running on, so with warn_unreachable the other branch
        # becomes an error — the code is correct, only unanalysable from one
        # side. os.name carries the same meaning without the narrowing.
        if os.name == "nt":
            return venv / "Scripts" / "python.exe"
        return venv / "bin" / "python"

    # --- state ------------------------------------------------------------

    def manifest(self, model_id: str) -> InstallManifest | None:
        path = self.directory(model_id) / MANIFEST_FILE
        try:
            return InstallManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def state(self, model_id: str) -> InstallState:
        directory = self.directory(model_id)
        if not directory.exists():
            return InstallState.NOT_INSTALLED
        if self.manifest(model_id) is None:
            return InstallState.INCOMPLETE
        if not self.python_executable(model_id).exists():
            return InstallState.INCOMPLETE
        return InstallState.INSTALLED

    def disk_usage(self, model_id: str | None = None) -> dict[str, int]:
        """Bytes used per installed model. Real numbers, walked from disk.

        Reported rather than taken from the declared requirement, because the
        two differ and the user cares about the one that is actually consuming
        their disk.
        """
        targets = [self.directory(model_id)] if model_id else list(self.root.iterdir())
        usage: dict[str, int] = {}
        for directory in targets:
            if not directory.is_dir():
                continue
            total = 0
            for path in directory.rglob("*"):
                try:
                    if path.is_file() and not path.is_symlink():
                        total += path.stat().st_size
                except OSError:
                    continue
            usage[directory.name] = total
        return usage

    # --- install ----------------------------------------------------------

    def _run(self, args: list[str], step: str) -> None:
        """Run an install command through the injected runner."""
        try:
            result = self._run_command(args, _INSTALL_TIMEOUT_S)
        except FileNotFoundError as exc:
            raise ModelInstallError(
                f"A tool needed to install this model is missing: {args[0]}.",
                technical=str(exc),
                suggestions=[f"Install {args[0]} and try again."],
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ModelInstallError(
                "The install timed out.",
                technical=str(exc),
                suggestions=["Check your network connection.", "Try again."],
            ) from exc
        if result.returncode != 0:
            raise ModelInstallError(
                f"The install failed while {step}.",
                technical=(result.stderr or result.stdout or "")[-4000:],
            )

    def preflight(self, model_id: str, *, allow_marginal: bool = False) -> None:
        """Refuse impossible installs before touching the disk or the network."""
        metadata = get_model(model_id)
        if metadata is None:
            raise ModelInstallError(f"Unknown model '{model_id}'.")
        if get_install_spec(model_id) is None:
            raise ModelInstallError(
                f"{metadata.name} has no installer yet.",
                technical=f"No InstallSpec registered for {model_id}.",
            )

        hardware = detect_hardware(data_dir=self.root)
        verdict = assess(metadata, hardware)
        if verdict.verdict is Verdict.BLOCKED:
            raise ModelInstallError(
                f"{metadata.name} cannot run on this machine.",
                technical="; ".join(reason.message for reason in verdict.reasons),
                suggestions=[reason.message for reason in verdict.reasons],
            )
        if verdict.verdict is Verdict.MARGINAL and not allow_marginal:
            raise ModelInstallError(
                f"{metadata.name} is larger than this machine comfortably supports.",
                technical="; ".join(reason.message for reason in verdict.reasons),
                suggestions=[
                    *[reason.message for reason in verdict.reasons],
                    "Install it anyway with --allow-marginal if you accept the risk.",
                ],
            )

        needed = metadata.requirements.disk_bytes + _DISK_HEADROOM_BYTES
        if hardware.disk_free_bytes is not None and hardware.disk_free_bytes < needed:
            raise ModelInstallError(
                f"Not enough disk space: {needed / 1024**3:.0f} GB needed, "
                f"{hardware.disk_free_bytes / 1024**3:.0f} GB free.",
                suggestions=["Free up space.", "Move the VOLUM data directory to another volume."],
            )

    def install(
        self,
        model_id: str,
        *,
        on_progress: InstallProgress = _noop,
        allow_marginal: bool = False,
        hf_token: str | None = None,
    ) -> InstallManifest:
        """Install one model. Explicit, user-triggered, never automatic."""
        from ..version import __version__  # noqa: PLC0415 - avoids an import cycle

        self.preflight(model_id, allow_marginal=allow_marginal)
        spec = get_install_spec(model_id)
        assert spec is not None  # noqa: S101 - guaranteed by preflight

        if self.state(model_id) is InstallState.INCOMPLETE:
            on_progress("cleanup", "Removing an unfinished earlier install")
            self.remove(model_id)

        directory = self.directory(model_id)
        directory.mkdir(parents=True, exist_ok=True)

        self._create_environment(spec, on_progress)
        commits, skipped = self._fetch_sources(spec, on_progress)
        frozen = self._freeze(spec.model_id)
        revisions = self._fetch_weights(spec, on_progress, hf_token)
        shims = self._write_shims(spec, on_progress)
        configs = self._write_config_files(spec, on_progress)

        manifest = InstallManifest(
            model_id=model_id,
            volum_version=__version__,
            python_version=spec.python_version,
            source_commits=commits,
            skipped_components=skipped,
            weight_revisions=revisions,
            shims=shims,
            config_files=configs,
            worker_env=dict(spec.worker_env),
            frozen_requirements=frozen,
        )
        # Written last: its presence is what marks the install complete.
        (directory / MANIFEST_FILE).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        on_progress("done", f"{model_id} is ready")
        return manifest

    def _create_environment(self, spec: InstallSpec, on_progress: InstallProgress) -> None:
        venv = self.venv_dir(spec.model_id)
        on_progress("environment", f"Creating a Python {spec.python_version} environment")
        self._run(
            [self._uv, "venv", "--python", spec.python_version, str(venv)],
            "creating the environment",
        )
        if spec.pip_packages:
            on_progress("dependencies", f"Installing {len(spec.pip_packages)} packages")
            self._run(
                [
                    self._uv,
                    "pip",
                    "install",
                    "--python",
                    str(self.python_executable(spec.model_id)),
                    *spec.pip_packages,
                ],
                "installing dependencies",
            )

    def _freeze(self, model_id: str) -> list[str]:
        """Capture the resolved package versions. Best effort — a failure here
        costs provenance, not the install."""
        try:
            result = self._run_command(
                [self._uv, "pip", "freeze", "--python", str(self.python_executable(model_id))],
                120,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())

    def check_prerequisites(self, spec: InstallSpec) -> tuple[list[str], list[str]]:
        """Probe declared prerequisites. Returns ``(blocking, degraded)``.

        Run before anything is downloaded. A missing Metal toolchain should cost
        the user a sentence, not ten gigabytes and a build error.
        """
        blocking: list[str] = []
        degraded: list[str] = []
        for prerequisite in spec.prerequisites:
            try:
                result = self._run_command(list(prerequisite.probe), 30)
                satisfied = result.returncode == 0
            except (OSError, subprocess.SubprocessError):
                satisfied = False
            if satisfied:
                continue
            message = f"{prerequisite.description} — {prerequisite.remedy}"
            if prerequisite.required:
                blocking.append(message)
            else:
                consequence = prerequisite.consequence_if_missing or "reduced functionality"
                degraded.append(f"{message} Without it: {consequence}")
        return blocking, degraded

    def _satisfied_prerequisites(self, spec: InstallSpec) -> set[str]:
        satisfied: set[str] = set()
        for prerequisite in spec.prerequisites:
            try:
                if self._run_command(list(prerequisite.probe), 30).returncode == 0:
                    satisfied.add(prerequisite.name)
            except (OSError, subprocess.SubprocessError):
                continue
        return satisfied

    def _fetch_sources(
        self, spec: InstallSpec, on_progress: InstallProgress
    ) -> tuple[dict[str, str], list[str]]:
        """Clone and optionally install every source. Returns commits and skips."""
        if not spec.sources:
            return {}, []

        available = self._satisfied_prerequisites(spec) if spec.prerequisites else set()
        root = self.source_dir(spec.model_id)
        root.mkdir(parents=True, exist_ok=True)
        commits: dict[str, str] = {}
        skipped: list[str] = []

        for source in spec.sources:
            missing = [name for name in source.requires if name not in available]
            if missing:
                skipped.append(f"{source.directory}: needs {', '.join(missing)}")
                on_progress("source", f"Skipping {source.directory} ({', '.join(missing)})")
                continue

            target = root / source.directory
            on_progress("source", f"Fetching {source.directory}")
            if not target.exists():
                # Clone then check out: `git clone --branch` does not take a bare
                # commit hash, and a branch name would not be a pin.
                self._run(
                    ["git", "clone", "--quiet", source.url, str(target)],
                    f"fetching {source.directory}",
                )
            self._run(
                ["git", "-C", str(target), "checkout", "--quiet", source.commit],
                f"checking out {source.directory}",
            )
            commits[source.directory] = source.commit

            if not source.pip_install:
                continue

            package = target / source.pip_subdirectory if source.pip_subdirectory else target
            args = [
                self._uv,
                "pip",
                "install",
                "--python",
                str(self.python_executable(spec.model_id)),
            ]
            if source.no_build_isolation:
                args.append("--no-build-isolation")
            args.append(str(package))
            on_progress("source", f"Building {source.directory}")
            try:
                self._run(args, f"building {source.directory}")
            except ModelInstallError:
                if not source.optional:
                    raise
                # An accelerator with a working fallback. Losing speed beats
                # losing the model, but the user is told which one went.
                skipped.append(f"{source.directory}: build failed, using the fallback")
                on_progress("source", f"{source.directory} did not build — continuing")

        return commits, skipped

    def _fetch_weights(
        self, spec: InstallSpec, on_progress: InstallProgress, token: str | None
    ) -> dict[str, str]:
        if not spec.weights:
            return {}
        try:
            from huggingface_hub import (  # noqa: PLC0415 - optional at import
                hf_hub_download,
                snapshot_download,
            )
        except ImportError as exc:
            raise ModelInstallError(
                "Downloading model weights needs the huggingface-hub package.",
                technical=str(exc),
            ) from exc

        target = self.weights_dir(spec.model_id)
        target.mkdir(parents=True, exist_ok=True)
        revisions: dict[str, str] = {}

        cache_dir = self.hf_cache_dir(spec.model_id)

        for weight in spec.weights:
            if weight.gated and not token:
                raise ModelInstallError(
                    f"{weight.repo_id} requires you to accept its licence and provide an "
                    "access token before the weights can be downloaded.",
                    technical=f"Gated repository {weight.repo_id}.",
                    suggestions=[
                        f"Open https://huggingface.co/{weight.repo_id} and accept the terms.",
                        "Add your Hugging Face token in Settings.",
                    ],
                )
            if weight.into_cache:
                on_progress("weights", f"Downloading {weight.repo_id}")
                cache_dir.mkdir(parents=True, exist_ok=True)
                try:
                    snapshot_download(
                        repo_id=weight.repo_id,
                        revision=weight.revision,
                        cache_dir=str(cache_dir),
                        token=token,
                    )
                except Exception as exc:
                    raise self._download_error(weight.repo_id, exc, gated=weight.gated) from exc
                revisions[weight.repo_id] = weight.revision
                continue

            destination = (
                target / weight.target_subdirectory if weight.target_subdirectory else target
            )
            for filename in weight.files:
                on_progress("weights", f"Downloading {filename} from {weight.repo_id}")
                try:
                    hf_hub_download(
                        repo_id=weight.repo_id,
                        filename=filename,
                        revision=weight.revision,
                        local_dir=str(destination),
                        token=token,
                    )
                except Exception as exc:
                    raise self._download_error(weight.repo_id, exc, gated=weight.gated) from exc
            revisions[weight.repo_id] = weight.revision
        return revisions

    def _download_error(self, repo_id: str, exc: Exception, *, gated: bool) -> ModelInstallError:
        """Turn a hub failure into something a user can act on.

        A gated repository refuses with an authorisation error that reads like a
        bug. Naming the page to visit is the difference between a dead end and a
        two-minute fix.
        """
        suggestions = ["Check your network connection.", "Try again."]
        if gated:
            suggestions = [
                f"Open https://huggingface.co/{repo_id} and request access.",
                "Access there is granted by a person and can take a while.",
                "Then add your Hugging Face token in Settings.",
            ]
        return ModelInstallError(
            f"Could not download {repo_id}.",
            technical=f"{type(exc).__name__}: {exc}",
            suggestions=suggestions,
        )

    def _write_config_files(self, spec: InstallSpec, on_progress: InstallProgress) -> list[str]:
        """Install VOLUM's own configuration alongside the weights."""
        written: list[str] = []
        for config in spec.config_files:
            source = CONFIG_DIRECTORY / config.source_file
            if not source.exists():
                raise ModelInstallError(
                    f"Missing configuration file {config.source_file}.",
                    technical=f"Expected at {source}",
                )
            target = self.weights_dir(spec.model_id) / config.target
            target.parent.mkdir(parents=True, exist_ok=True)
            on_progress("config", f"Writing {config.target}")
            shutil.copyfile(source, target)
            written.append(config.target)
        return written

    def _write_shims(self, spec: InstallSpec, on_progress: InstallProgress) -> list[str]:
        """Copy compatibility modules into the provider's site-packages.

        Written into the provider's own environment rather than patching the
        cloned source, so the upstream checkout stays exactly what its commit
        says it is.
        """
        if not spec.shims:
            return []
        site_packages = self._site_packages(spec.model_id)
        if site_packages is None:
            raise ModelInstallError(
                "Could not locate the new environment's site-packages directory.",
                technical=f"venv at {self.venv_dir(spec.model_id)}",
            )
        written: list[str] = []
        for shim in spec.shims:
            source = SHIM_DIRECTORY / shim.source_file
            if not source.exists():
                raise ModelInstallError(
                    f"Missing compatibility module {shim.source_file}.",
                    technical=f"Expected at {source}",
                )
            on_progress("shims", f"Installing the {shim.module_name} compatibility module")
            shutil.copyfile(source, site_packages / f"{shim.module_name}.py")
            written.append(shim.module_name)
        return written

    def _site_packages(self, model_id: str) -> Path | None:
        venv = self.venv_dir(model_id)
        for candidate in (venv / "lib").glob("python*/site-packages"):
            return candidate
        windows = venv / "Lib" / "site-packages"
        return windows if windows.exists() else None

    # --- verify and remove ------------------------------------------------

    def verify(self, model_id: str) -> list[str]:
        """Return a list of problems. Empty means the install looks sound."""
        problems: list[str] = []
        if self.state(model_id) is not InstallState.INSTALLED:
            return [f"{model_id} is not installed."]

        if not self.python_executable(model_id).exists():
            problems.append("The provider's Python environment is missing.")

        spec = get_install_spec(model_id)
        if spec is None:
            return problems

        if spec.sources and not self.source_dir(model_id).is_dir():
            problems.append("The model implementation is missing.")

        weights = self.weights_dir(model_id)
        for weight in spec.weights:
            for filename in weight.files:
                path = weights / filename
                if not path.exists():
                    problems.append(f"Missing weight file: {filename}")
                elif path.stat().st_size == 0:
                    problems.append(f"Weight file is empty: {filename}")
        return problems

    def remove(self, model_id: str) -> bool:
        directory = self.directory(model_id)
        if not directory.exists():
            return False
        shutil.rmtree(directory)
        return True
