"""Installable components that are not generators.

``registry.py`` catalogues things that turn an image into a mesh. A background
segmenter does not, and putting it there would make ``volum models list``
claim you could generate with it. It is still *installed* by the same
``ModelManager`` — same virtual environment, same pinned weights, same manifest,
same removal — so the only thing that differs is the catalogue it appears in and
the fact that there is nothing to hardware-gate: ONNX Runtime on the CPU runs
wherever VOLUM runs.

Licence facts here are read from the licence text with the date, exactly as in
the model registry (spec section 10).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..providers.types import CommercialUse, LicenseMetadata

MIB = 1024**2


class ToolMetadata(BaseModel):
    """A component that serves one pipeline stage."""

    id: str
    name: str
    description: str
    stage: str = Field(description="The pipeline stage this serves, e.g. 'preprocessing'.")
    license: LicenseMetadata
    disk_bytes: int
    numbers_source: str = Field(description="Where the disk figure comes from. Never a guess.")


BIREFNET = ToolMetadata(
    id="birefnet",
    name="BiRefNet (lite)",
    description=(
        "Separates the subject from its background before reconstruction. Every "
        "generator in VOLUM is single-image and reconstructs what it is shown, so a "
        "wall behind the subject becomes geometry. Runs on the CPU and takes about "
        "as long as a small generation does."
    ),
    stage="preprocessing",
    license=LicenseMetadata(
        code_license="MIT",
        weights_license="MIT",
        commercial_use=CommercialUse.ALLOWED,
        # Named explicitly because the obvious alternative is not usable: RMBG-2.0,
        # which most background-removal code reaches for by default, is CC BY-NC.
        # BiRefNet is what makes this stage commercially clean (docs/licenses.md).
        commercial_use_detail=(
            "MIT throughout: the model, the ONNX export and ONNX Runtime. No "
            "attribution requirement and no territorial restriction."
        ),
        dependency_licenses={
            "BiRefNet (ZhengPeng7)": "MIT, Copyright (c) 2024 ZhengPeng",
            "ONNX export (onnx-community/BiRefNet_lite-ONNX)": "MIT",
            "onnxruntime": "MIT",
        },
        source_url="https://github.com/ZhengPeng7/BiRefNet",
        verified_on="2026-10-25",
    ),
    # 224 MB weights plus roughly 90 MB for ONNX Runtime, NumPy and Pillow.
    disk_bytes=330 * MIB,
    numbers_source=(
        "Weights measured from the model host: onnx/model.onnx is 224 MB. The "
        "fp16 export is half that but ONNX Runtime has no fp16 CPU kernels and "
        "would insert casts, so the fp32 file is the smaller cost in practice. "
        "Measured on a 10-core M1 Pro: 2.4 s to load, then 43 s per image under "
        "moderate load and 60-95 s under heavy load. The export's input shape is "
        "fixed at 1024x1024, so there is no cheaper resolution to choose."
    ),
)


TOOLS: tuple[ToolMetadata, ...] = (BIREFNET,)


def all_tools() -> tuple[ToolMetadata, ...]:
    return TOOLS


def get_tool(tool_id: str) -> ToolMetadata | None:
    return next((tool for tool in TOOLS if tool.id == tool_id), None)
