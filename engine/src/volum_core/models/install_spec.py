"""How a provider gets onto the machine.

Declared data, like capabilities and requirements. The manager executes the
declaration; it contains no per-model branches.

Two decisions worth explaining:

**Each provider gets its own virtual environment.** Model implementations pin
mutually incompatible versions — TripoSR pins ``transformers==4.35.0`` and
``Pillow==10.1.0`` from 2024, which no modern engine environment would tolerate.
A shared environment would make installing the second provider a dependency
fight and would make ``remove()`` and ``disk_usage()`` dishonest.

**Sources are pinned to a commit, and VOLUM does not fork them.** Some model
repositories are not pip-installable at all (TripoSR has no ``pyproject.toml``),
so the manager clones them at a fixed commit and puts them on the worker's
path. Vendoring the code into VOLUM would make VOLUM a fork of every model it
supports; pinning keeps it an integrator.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WeightSpec(BaseModel):
    """Weights to fetch from a model host."""

    repo_id: str = Field(description="Hugging Face repository, e.g. 'stabilityai/TripoSR'.")
    revision: str = Field(
        default="main",
        description="Pin a commit or tag. 'main' is a moving target and makes "
        "reproducibility claims false.",
    )
    files: tuple[str, ...] = Field(
        default=(),
        description="Specific files. Empty means the whole repository, which is "
        "usually wasteful — most repos carry several formats of the same weights.",
    )
    gated: bool = Field(
        default=False,
        description="Requires accepted terms and a token on the host. The manager "
        "must say so in words rather than surface a 401.",
    )
    manual_approval: bool = Field(
        default=False,
        description="Access is granted by a human, not by clicking accept. DINOv3 works "
        "this way, so a user can hold a valid token and still be refused for days. "
        "Worth saying before a download starts, not after.",
    )
    size_bytes: int | None = Field(
        default=None, description="Measured from the host, not estimated. Used for preflight."
    )
    license_note: str | None = None
    target_subdirectory: str | None = Field(
        default=None,
        description="Where the files must land relative to the weights directory. "
        "Model code often expects an exact layout.",
    )


class SourceSpec(BaseModel):
    """A git repository holding model or support code."""

    url: str
    commit: str = Field(description="Exact commit. Never a branch name.")
    directory: str = Field(description="Where to clone it, relative to the model's source dir.")
    subdirectory: str | None = Field(
        default=None,
        description="Directory within the repo to expose on the worker's path, "
        "e.g. 'tsr' for TripoSR.",
    )
    pip_install: bool = Field(
        default=False,
        description="Install the clone as a package rather than only putting it on "
        "the path. Some dependencies are only distributed as git checkouts.",
    )
    pip_subdirectory: str | None = Field(
        default=None, description="Install this subdirectory instead of the repository root."
    )
    no_build_isolation: bool = Field(
        default=False,
        description="Build against the environment's own packages. Required for native "
        "extensions that need torch at build time — an isolated build environment has no "
        "torch in it, and the build fails on an import rather than on anything meaningful.",
    )
    optional: bool = Field(
        default=False,
        description="A failed install is recorded and the install continues. For "
        "accelerators with a working software fallback: losing speed is much better "
        "than losing the model.",
    )
    build_env: dict[str, str] = Field(
        default_factory=dict, description="Environment variables for the build."
    )
    requires: tuple[str, ...] = Field(
        default=(),
        description="Prerequisite names (see Prerequisite) that must be satisfied, "
        "otherwise this source is skipped.",
    )


class Prerequisite(BaseModel):
    """Something outside VOLUM that a part of an install needs.

    Checked before anything is downloaded. Declared rather than discovered at
    failure time, so the manager can say what is missing and what it costs —
    instead of aborting midway through a ten-gigabyte download.
    """

    name: str
    description: str
    #: Command that must succeed. Checked with a short timeout.
    probe: tuple[str, ...]
    remedy: str = Field(description="What the user can do about it, in plain words.")
    #: False when the install can proceed without it, in a reduced form.
    required: bool = True
    consequence_if_missing: str | None = Field(
        default=None, description="What the user loses. Only meaningful when not required."
    )


class ShimSpec(BaseModel):
    """A compatibility module injected into the provider's environment.

    Needed where an upstream dependency cannot be installed on a target
    platform. TripoSR requires ``torchmcubes``, a CUDA extension compiled from
    git; a shim backed by PyMCubes — which ships an arm64 wheel — provides the
    same call without compilation and without forking TripoSR.

    A shim is a targeted, documented substitution, not a patch of upstream code.
    """

    module_name: str
    source_file: str = Field(description="File name under providers/shims/.")
    reason: str = Field(description="Shown to the user. Why the substitution exists.")


class InstallSpec(BaseModel):
    model_id: str
    python_version: str = Field(default="3.11")
    pip_packages: tuple[str, ...] = ()
    sources: tuple[SourceSpec, ...] = ()
    weights: tuple[WeightSpec, ...] = ()
    shims: tuple[ShimSpec, ...] = ()
    prerequisites: tuple[Prerequisite, ...] = ()
    #: Environment the worker needs. Backend selection for these models happens
    #: through environment variables read at import time, so it has to be part
    #: of the install record rather than passed per run.
    worker_env: dict[str, str] = Field(default_factory=dict)

    @property
    def requires_token(self) -> bool:
        return any(weight.gated for weight in self.weights)

    @property
    def download_bytes(self) -> int:
        return sum(weight.size_bytes or 0 for weight in self.weights)


TRIPOSR_INSTALL = InstallSpec(
    model_id="triposr",
    python_version="3.11",
    pip_packages=(
        "torch",
        "torchvision",
        # Upstream pins transformers==4.35.0. That pin is load-bearing, not
        # incidental: transformers 5.x renamed the ViT internals
        # (encoder.layer.N.attention.attention.query -> layers.N.attention.q_proj),
        # so the published checkpoint fails to load with a wall of missing keys.
        # 4.35.0 itself no longer resolves against a current interpreter, so the
        # constraint is the widest range that still has the old layout.
        "transformers>=4.35,<4.50",
        "einops",
        "omegaconf",
        "trimesh",
        "pillow",
        "numpy",
        "huggingface-hub",
        "safetensors",
        # tsr/utils.py imports both at module level, so `from tsr.system import
        # TSR` fails without them even though this worker calls neither.
        # moderngl and xatlas are NOT installed: they live in bake_texture.py,
        # which nothing on our path imports.
        "imageio",
        # The [cpu] extra is required: plain "rembg" imports, finds no
        # onnxruntime backend, prints instructions and calls sys.exit() —
        # at import time, killing the worker before it can report anything.
        "rembg[cpu]",
        # Replaces torchmcubes; see the shim below.
        "PyMCubes",
    ),
    sources=(
        SourceSpec(
            url="https://github.com/VAST-AI-Research/TripoSR",
            # Pinned rather than a branch: a moving source would silently invalidate
            # every reproducibility claim VOLUM makes. Verified 2026-08-30.
            commit="107cefdc244c39106fa830359024f6a2f1c78871",
            directory="TripoSR",
        ),
    ),
    weights=(
        WeightSpec(
            repo_id="stabilityai/TripoSR",
            # Commit-pinned for the same reason as the source, verified 2026-08-30.
            revision="5b521936b01fbe1890f6f9baed0254ab6351c04a",
            files=("config.yaml", "model.ckpt"),
            gated=False,
            size_bytes=1_780_000_000,
            license_note="MIT",
        ),
    ),
    shims=(
        ShimSpec(
            module_name="torchmcubes",
            source_file="torchmcubes_shim.py",
            reason=(
                "Upstream requires torchmcubes, a CUDA extension that must be compiled "
                "from git and does not build usefully on Apple Silicon. PyMCubes ships "
                "a prebuilt wheel and computes the same isosurface."
            ),
        ),
    ),
)


INSTALL_SPECS: dict[str, InstallSpec] = {TRIPOSR_INSTALL.model_id: TRIPOSR_INSTALL}


def get_install_spec(model_id: str) -> InstallSpec | None:
    return INSTALL_SPECS.get(model_id)
