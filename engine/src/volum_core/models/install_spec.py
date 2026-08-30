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
    into_cache: bool = Field(
        default=False,
        description="Fetch the whole repository into the model's own Hugging Face "
        "cache instead of copying files into the weights directory. Needed for "
        "weights the model loads by repository name rather than by path — putting "
        "them anywhere else means the first run silently downloads them again, and "
        "the install is not offline-capable as promised.",
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


class ConfigFile(BaseModel):
    """A configuration file VOLUM writes into the weights directory.

    Not a patch of upstream code — a configuration of our own, alongside the
    weights we actually downloaded. It is how a model gets installed in a
    reduced form (geometry without texturing) and how a dependency with an
    unacceptable licence gets swapped for an equivalent one.
    """

    source_file: str = Field(description="File name under models/pipeline_configs/.")
    target: str = Field(description="Path relative to the model's weights directory.")
    reason: str = Field(description="Why it differs from upstream. Shown to the user.")


class InstallSpec(BaseModel):
    model_id: str
    python_version: str = Field(default="3.11")
    pip_packages: tuple[str, ...] = ()
    sources: tuple[SourceSpec, ...] = ()
    weights: tuple[WeightSpec, ...] = ()
    shims: tuple[ShimSpec, ...] = ()
    prerequisites: tuple[Prerequisite, ...] = ()
    config_files: tuple[ConfigFile, ...] = ()
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


TRELLIS2_INSTALL = InstallSpec(
    model_id="trellis2",
    python_version="3.11",
    pip_packages=(
        "torch",
        "torchvision",
        "transformers",
        "accelerate",
        "huggingface_hub",
        "safetensors",
        "pillow",
        "numpy",
        "trimesh",
        "scipy",
        "tqdm",
        "easydict",
        "kornia",
        "timm",
        "imageio",
        "opencv-python-headless",
        "xatlas",
        "fast-simplification",
    ),
    sources=(
        # The Apple Silicon port. It owns the MPS patches and the entry point;
        # everything else is a dependency it expects to find beside it.
        SourceSpec(
            url="https://github.com/shivampkumar/trellis-mac",
            commit="d58628f4f5b9c3de8274cb110074154f4b31cef2",
            directory="trellis-mac",
        ),
        SourceSpec(
            url="https://github.com/microsoft/TRELLIS.2",
            commit="75fbf0183001ed9876c8dbb35de6b68552ee08bd",
            directory="trellis-mac/TRELLIS.2",
        ),
        SourceSpec(
            url="https://github.com/EasternJournalist/utils3d",
            commit="9a4eb15e4021b67b12c460c7057d642626897ec8",
            directory="trellis-mac/deps/utils3d",
            pip_install=True,
        ),
        # Metal accelerators. Optional in the strict sense: each has a software
        # fallback, and none of them can build without an Xcode Metal toolchain.
        # They only affect texture baking, which STL cannot carry anyway.
        *(
            SourceSpec(
                url=f"https://github.com/pedronaugusto/{name}",
                commit=commit,
                directory=f"trellis-mac/deps/{name}",
                pip_install=True,
                no_build_isolation=True,
                optional=True,
                requires=("metal-toolchain",),
                # PyTorch's MPS headers need macOS 12; some interpreters set a
                # lower minimum and the compiler rejects the headers outright.
                build_env={"MACOSX_DEPLOYMENT_TARGET": "12.0"},
            )
            for name, commit in (
                ("mtlbvh", "6b2a0f63b476ad8225695d092902104beafd7f58"),
                ("mtldiffrast", "c9499ba2e4ed6849e95ce5da7aae5bf61addb528"),
                ("mtlmesh", "7de3864f783407201486bf6c900a4bd76a4018a3"),
                ("mtlgemm", "566c133781ce4992de3f77c3a97dd6feaae9d013"),
            )
        ),
    ),
    weights=(
        WeightSpec(
            repo_id="microsoft/TRELLIS.2-4B",
            revision="5b521936b01fbe1890f6f9baed0254ab6351c04a",
            # The geometry half only. The texture models are another 2.9 GB and
            # produce PBR maps that neither STL nor this install can use: baking
            # them needs the Metal toolchain, and STL carries no colour at all.
            files=(
                "ckpts/ss_flow_img_dit_1_3B_64_bf16.json",
                "ckpts/ss_flow_img_dit_1_3B_64_bf16.safetensors",
                "ckpts/slat_flow_img2shape_dit_1_3B_512_bf16.json",
                "ckpts/slat_flow_img2shape_dit_1_3B_512_bf16.safetensors",
                "ckpts/shape_dec_next_dc_f16c32_fp16.json",
                "ckpts/shape_dec_next_dc_f16c32_fp16.safetensors",
            ),
            size_bytes=6_066_000_000,
            license_note="MIT",
        ),
        WeightSpec(
            repo_id="microsoft/TRELLIS-image-large",
            revision="main",
            # The sparse-structure decoder lives in the older repository; the
            # 4B pipeline references it across repositories.
            files=(
                "ckpts/ss_dec_conv3d_16l8_fp16.json",
                "ckpts/ss_dec_conv3d_16l8_fp16.safetensors",
            ),
            size_bytes=150_000_000,
            license_note="MIT",
        ),
        WeightSpec(
            repo_id="facebook/dinov3-vitl16-pretrain-lvd1689m",
            revision="main",
            into_cache=True,
            gated=True,
            manual_approval=True,
            size_bytes=1_213_000_000,
            license_note="DINOv3 License - commercial use permitted, requires "
            "the notice 'Built with DINOv3'",
        ),
        WeightSpec(
            repo_id="ZhengPeng7/BiRefNet",
            revision="main",
            into_cache=True,
            size_bytes=440_000_000,
            # Substituted for briaai/RMBG-2.0, which the upstream config names and
            # which is CC BY-NC and gated. Same class, and its own default.
            license_note="MIT - replaces the non-commercial RMBG-2.0",
        ),
    ),
    prerequisites=(
        Prerequisite(
            name="metal-toolchain",
            description="The Xcode Metal compiler is not available",
            probe=("xcrun", "-sdk", "macosx", "metal", "--version"),
            remedy=(
                "Install Xcode (not just the Command Line Tools) and run "
                "'xcodebuild -downloadComponent MetalToolchain'."
            ),
            required=False,
            consequence_if_missing=(
                "texture baking runs on a slower software path with visible "
                "artefacts. Geometry, and therefore STL and 3MF output, is unaffected."
            ),
        ),
    ),
    config_files=(
        ConfigFile(
            source_file="trellis2_geometry_512.json",
            target="pipeline.json",
            reason=(
                "Loads only the models VOLUM downloaded (geometry at 512) and points "
                "background removal at BiRefNet, which is MIT and ungated, instead of "
                "RMBG-2.0, which is CC BY-NC and gated."
            ),
        ),
    ),
    worker_env={
        # Several operations have no MPS kernel; without this they raise instead
        # of falling back to the CPU. Must be set before torch is imported.
        "PYTORCH_ENABLE_MPS_FALLBACK": "1",
        "ATTN_BACKEND": "sdpa",
        "SPARSE_ATTN_BACKEND": "sdpa",
    },
)


INSTALL_SPECS: dict[str, InstallSpec] = {
    spec.model_id: spec for spec in (TRIPOSR_INSTALL, TRELLIS2_INSTALL)
}


def get_install_spec(model_id: str) -> InstallSpec | None:
    return INSTALL_SPECS.get(model_id)
