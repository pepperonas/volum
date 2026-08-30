"""The catalogue of models VOLUM knows about.

Every entry carries licence facts read from the licence text, with the date they
were read (spec section 10). A model whose licence chain has not been verified
end to end declares ``CommercialUse.UNKNOWN`` — never an optimistic guess.

Deliberately absent: **Hunyuan3D-2.1.** Technically one of the best fits for
consumer hardware in the whole survey, and excluded on licence grounds alone —
its Community License defines its Territory as *"the worldwide territory,
excluding the territory of the European Union, United Kingdom and South Korea"*,
and VOLUM is developed in the EU. Documented in ``docs/licenses.md`` so the
decision stays visible and revisitable rather than looking like an oversight.
"""

from __future__ import annotations

from ..providers.types import (
    Capability,
    CommercialUse,
    LicenseMetadata,
    ProviderMetadata,
    Requirements,
)

GIB = 1024**3

#: Date the licence texts in this file were last read against their sources.
LICENSES_VERIFIED_ON = "2026-08-30"


TRELLIS2 = ProviderMetadata(
    id="trellis2",
    name="TRELLIS.2",
    version="4B",
    description=(
        "Microsoft's 4B-parameter sparse-voxel model. The best open geometry and "
        "material quality currently available, with real PBR output. Needs a large "
        "amount of memory."
    ),
    capabilities=frozenset(
        {
            Capability.SINGLE_IMAGE,
            Capability.TEXTURE,
            Capability.PBR,
            Capability.UV,
            Capability.HIGH_RESOLUTION,
        }
        # MULTI_IMAGE is absent on purpose: upstream is single-image, and its own
        # issue tracker reports multi-image conditioning performing worse.
    ),
    requirements=Requirements(
        supported_platforms=frozenset({"Darwin", "Linux", "Windows"}),
        supported_runtimes=frozenset({"mps", "cuda"}),
        minimum_memory_bytes=16 * GIB,
        estimated_peak_memory_bytes=18 * GIB,
        disk_bytes=11 * GIB,
        requires_gated_download=True,  # DINOv3 conditioner is gated on Hugging Face
        numbers_source=(
            "Disk measured from the model host: 7.33 GB of weights for the "
            "geometry-only 512 pipeline, plus roughly 3 GB for the provider "
            "environment. The full 15.1 GB set includes 1024 and texture models "
            "that STL cannot carry and that this install does not fetch. Memory is "
            "still an upstream claim: shivampkumar/trellis-mac reports a ~18 GB peak "
            "on Apple Silicon, and no figure exists for 16 GB machines."
        ),
    ),
    license=LicenseMetadata(
        code_license="MIT",
        weights_license="MIT",
        commercial_use=CommercialUse.CONDITIONAL,
        commercial_use_detail=(
            "MIT throughout on the Apple Silicon path, which is the one VOLUM installs. "
            "Three conditions. The official CUDA pipeline is excluded: it depends on "
            "nvdiffrast, which is non-commercial. The 'Built with DINOv3' notice must be "
            "displayed. And background removal must use BiRefNet, not the RMBG-2.0 that "
            "the upstream config names, which is CC BY-NC — VOLUM substitutes it."
        ),
        attribution_required="Built with DINOv3",
        dependency_licenses={
            "nvdiffrast": "NVIDIA Source Code License (1-Way Commercial) - NON-COMMERCIAL, "
            "CUDA path only; not installed on Apple Silicon",
            "mtldiffrast": "MIT - implemented from the Laine et al. 2020 paper, only "
            "API-compatible with nvdiffrast, so not a derivative of it",
            "mtlgemm": "MIT",
            "mtlbvh": "MIT",
            "mtlmesh": "MIT",
            "trellis2-apple": "MIT (fork of the MIT-licensed TRELLIS.2)",
            "utils3d": "MIT",
            "DINOv3": "DINOv3 License - commercial use permitted, attribution required, gated",
            "BiRefNet": "MIT (VOLUM substitutes this for RMBG-2.0, which is CC BY-NC)",
        },
        source_url="https://github.com/microsoft/TRELLIS.2",
        verified_on=LICENSES_VERIFIED_ON,
    ),
    output_formats=frozenset({"glb", "obj"}),
    supports_seed=True,
)


TRIPOSR = ProviderMetadata(
    id="triposr",
    name="TripoSR",
    version="1.0",
    description=(
        "Small, fast, MIT-licensed reconstruction model. Output quality is well below "
        "the current state of the art and it produces vertex colours rather than PBR "
        "materials — but it runs on ordinary hardware, including CPU, which makes the "
        "whole pipeline verifiable without a 24 GB machine."
    ),
    capabilities=frozenset({Capability.SINGLE_IMAGE, Capability.FAST_INFERENCE}),
    requirements=Requirements(
        supported_platforms=frozenset({"Darwin", "Linux", "Windows"}),
        supported_runtimes=frozenset({"mps", "cuda", "cpu"}),
        minimum_memory_bytes=4 * GIB,
        estimated_peak_memory_bytes=5 * GIB,
        disk_bytes=5 * GIB,
        numbers_source=(
            "Conservative estimate from the model size plus a PyTorch environment. "
            "Replace with measured figures once `volum benchmark` has run."
        ),
    ),
    license=LicenseMetadata(
        code_license="MIT",
        weights_license="MIT",
        commercial_use=CommercialUse.ALLOWED,
        commercial_use_detail="MIT throughout, with no restrictive pipeline dependency.",
        dependency_licenses={},
        source_url="https://github.com/VAST-AI-Research/TripoSR",
        verified_on=LICENSES_VERIFIED_ON,
    ),
    output_formats=frozenset({"glb", "obj"}),
    supports_seed=True,
)


#: Order matters: it is the order shown in the UI. Best first.
REGISTRY: tuple[ProviderMetadata, ...] = (TRELLIS2, TRIPOSR)


def all_models() -> tuple[ProviderMetadata, ...]:
    return REGISTRY


def get_model(model_id: str) -> ProviderMetadata | None:
    for model in REGISTRY:
        if model.id == model_id:
            return model
    return None
