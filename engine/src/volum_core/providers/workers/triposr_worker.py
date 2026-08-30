"""TripoSR inference worker.

**Runs in the provider's own virtual environment, not the engine's.** It must
therefore import nothing from ``volum_core`` — that package is not installed
here. The contract with the engine is the line protocol documented in
``providers/worker_protocol.py``: one JSON object per line on stdout.

Isolation is the point. A Metal command-buffer kill or an out-of-memory abort
takes this process down and fails one job, rather than the engine.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    """Write one protocol line and flush.

    Flushing matters: without it the engine sees no progress until the process
    exits, which for a multi-minute job is indistinguishable from a hang.
    """
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def progress(stage: str, message: str, fraction: float | None = None) -> None:
    emit({"type": "progress", "stage": stage, "message": message, "fraction": fraction})


def fail(message: str, technical: str = "", suggestions: list[str] | None = None) -> None:
    emit(
        {
            "type": "error",
            "message": message,
            "technical": technical,
            "suggestions": suggestions or [],
        }
    )
    sys.exit(1)


def prepare_image(path: Path, foreground_ratio: float) -> Any:
    """Load an image into the form TripoSR expects.

    TripoSR wants the subject on a neutral grey field. If the image carries an
    alpha channel — because VOLUM's preprocessing stage segmented it — that
    alpha is composited over grey. If it does not, the image is used as-is and
    the caller is told, because inventing a segmentation would silently change
    the result the user sees.
    """
    import numpy as np
    from PIL import Image

    image = Image.open(path)
    if image.mode == "RGBA":
        from tsr.utils import resize_foreground

        image = resize_foreground(image, foreground_ratio)
        array = np.array(image).astype(np.float32) / 255.0
        composited = array[:, :, :3] * array[:, :, 3:4] + (1 - array[:, :, 3:4]) * 0.5
        return Image.fromarray((composited * 255.0).astype(np.uint8))

    progress(
        "preprocessing",
        "The image has no transparency, so the background was not separated. "
        "Results are better with the background removed first.",
    )
    return image.convert("RGB")


def main() -> None:
    started = time.monotonic()
    request = json.loads(sys.stdin.read())

    source_dir = Path(request["source_dir"])
    weights_dir = Path(request["weights_dir"])
    output_dir = Path(request["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    device = request.get("device", "cpu")
    parameters = request.get("parameters", {})
    seed = request.get("seed")

    # The cloned upstream checkout goes on the path here rather than being
    # installed, so it stays exactly what its pinned commit says it is.
    sys.path.insert(0, str(source_dir))

    try:
        import torch
        from tsr.system import TSR
    except ImportError as exc:
        fail(
            "The TripoSR environment is incomplete.",
            technical=f"{type(exc).__name__}: {exc}",
            suggestions=["Reinstall the model with: volum models install triposr"],
        )
        return

    if seed is not None:
        torch.manual_seed(seed)

    progress("loading", "Loading the model")
    try:
        model = TSR.from_pretrained(
            str(weights_dir), config_name="config.yaml", weight_name="model.ckpt"
        )
        # Chunking bounds peak memory during volume rendering. It is exposed as
        # a parameter because the right value depends on the machine, not on the
        # model, and the default here suits unified-memory laptops.
        model.renderer.set_chunk_size(int(parameters.get("chunk_size", 8192)))
        model.to(device)
    except Exception as exc:
        fail(
            "The model could not be loaded.",
            technical=traceback.format_exc(limit=6),
            suggestions=[
                "Check the install with: volum models verify triposr",
                f"Underlying error: {type(exc).__name__}",
            ],
        )
        return

    progress("preprocessing", "Preparing the image")
    images = [
        prepare_image(Path(p), float(parameters.get("foreground_ratio", 0.85)))
        for p in request["images"]
    ]
    if len(images) > 1:
        # Declared honestly rather than silently averaging: TripoSR is a
        # single-image model, and pretending otherwise is the simulated
        # multi-image support the specification forbids.
        progress(
            "preprocessing",
            f"TripoSR uses one image; the first of {len(images)} was used.",
        )

    progress("reconstructing", "Reconstructing geometry")
    try:
        with torch.no_grad():
            scene_codes = model([images[0]], device=device)
    except RuntimeError as exc:
        text = str(exc).lower()
        if "out of memory" in text or "insufficient" in text:
            fail(
                "There was not enough memory to generate this model.",
                technical=traceback.format_exc(limit=6),
                suggestions=[
                    "Close other applications and try again.",
                    "Lower the mesh resolution.",
                    "Reduce the chunk size in advanced settings.",
                ],
            )
        fail(
            "Generation failed.",
            technical=traceback.format_exc(limit=6),
        )
        return

    progress("optimizing", "Extracting the mesh")
    resolution = int(parameters.get("mc_resolution", 256))
    try:
        # has_vertex_color is positional in the pinned commit. TripoSR produces
        # no UV map or material — vertex colours are the whole of its "texture",
        # so asking for them is not optional if the asset is to look like
        # anything. The provider declares neither PBR nor UV for exactly this
        # reason.
        meshes = model.extract_mesh(scene_codes, True, resolution=resolution)
    except Exception:
        fail("The mesh could not be extracted.", technical=traceback.format_exc(limit=6))
        return

    mesh = meshes[0]
    mesh_path = output_dir / "model.glb"
    progress("optimizing", "Writing the asset")
    mesh.export(str(mesh_path))

    emit(
        {
            "type": "result",
            "mesh_path": str(mesh_path),
            "artifacts": {},
            "vertices": len(mesh.vertices),
            "faces": len(mesh.faces),
            "seed": seed,
            "device": device,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # An unhandled traceback on stderr with no protocol line would surface
        # to the user as "the job stopped for no reason".
        emit(
            {
                "type": "error",
                "message": "The generation process stopped unexpectedly.",
                "technical": traceback.format_exc(limit=10),
                "suggestions": [],
            }
        )
        sys.exit(1)
