"""TRELLIS.2 inference worker for Apple Silicon.

Runs in the provider's own environment and speaks the line protocol in
``providers/worker_protocol.py``. It must import nothing from ``volum_core``.

Two things make this worker different from a thin wrapper.

**Backends are chosen before torch is imported.** The sparse-attention and
sparse-convolution backends are read from the environment at import time, and
several operations have no MPS kernel at all — without
``PYTORCH_ENABLE_MPS_FALLBACK`` they raise instead of falling back to the CPU.
Setting them after the first torch import has no effect and no error.

**The macOS GPU watchdog is a real failure mode here, and a silent one.** It
kills long-running Metal kernels in the decoder without raising; execution
continues on empty tensors and fails somewhere else entirely. The port that this
integration builds on documents the signatures, and they are caught below and
reported as what they are — with the workarounds — instead of as an index error
deep in a library.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

#: Failures downstream of a watchdog kill. Neither mentions the GPU.
_WATCHDOG_SIGNATURES = (
    "non-zero size",
    "BVH needs at least 8 triangles",
)

_WATCHDOG_ADVICE = [
    "Close other applications — the watchdog tightens as the display server gets busier.",
    "Disconnect external displays, or run over SSH with the lid closed.",
    "Try a lower resolution.",
]


def emit(payload: dict[str, Any]) -> None:
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


def configure_backends(request: dict[str, Any]) -> None:
    """Set every environment variable that must precede the first torch import."""
    for key, value in (request.get("parameters", {}).get("worker_env") or {}).items():
        os.environ.setdefault(str(key), str(value))
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("ATTN_BACKEND", "sdpa")
    os.environ.setdefault("SPARSE_ATTN_BACKEND", "sdpa")

    # Keep every Hugging Face lookup inside the model's own directory, so an
    # installed model works offline instead of quietly re-downloading.
    cache = request.get("hf_cache_dir")
    if cache:
        os.environ.setdefault("HF_HOME", str(cache))
        os.environ.setdefault("HF_HUB_CACHE", str(cache))

    # flex_gemm is the faster sparse-convolution path but ships a Metal library
    # that only loads on macOS 26 and up, and only exists if the Metal toolchain
    # was available at install time. Probe rather than assume.
    if "SPARSE_CONV_BACKEND" not in os.environ:
        try:
            import flex_gemm  # noqa: F401

            os.environ["SPARSE_CONV_BACKEND"] = "flex_gemm"
        except (ImportError, RuntimeError):
            os.environ["SPARSE_CONV_BACKEND"] = "none"


def load_pipeline(weights_dir: Path, device: str, torch: Any) -> Any:
    """Construct the pipeline from the local weights directory.

    The path matters: ``from_pretrained`` treats a directory containing
    ``pipeline.json`` as local, which is what makes VOLUM's own configuration —
    geometry-only models, MIT background removal — take effect.
    """
    progress("loading", "Loading the model (this takes a couple of minutes)")
    try:
        from trellis2.pipelines.trellis2_image_to_3d import Trellis2ImageTo3DPipeline

        pipeline = Trellis2ImageTo3DPipeline.from_pretrained(str(weights_dir))
        pipeline.to(torch.device(device))
    except Exception as exc:
        fail(
            "The model could not be loaded.",
            technical=traceback.format_exc(limit=8),
            suggestions=[
                "Check the install with: volum models verify trellis2",
                f"Underlying error: {type(exc).__name__}",
            ],
        )
        raise SystemExit(1) from exc
    return pipeline


def write_result(mesh_out: Any, output_dir: Path, seed: int, device: str, started: float) -> None:
    """Export the mesh and report, refusing an empty one."""
    vertices = mesh_out.vertices.cpu().numpy()
    faces = mesh_out.faces.cpu().numpy()

    if vertices.shape[0] == 0 or faces.shape[0] == 0:
        # The watchdog's quiet signature: success reported, nothing produced.
        fail(
            "The model produced no geometry.",
            technical="The decoder returned an empty mesh, which on Apple Silicon is "
            "almost always the macOS GPU watchdog killing a Metal kernel.",
            suggestions=_WATCHDOG_ADVICE,
        )
        return

    progress("optimizing", "Writing the asset")
    import trimesh

    mesh = trimesh.Trimesh(vertices=vertices, faces=faces)
    mesh_path = output_dir / "model.glb"
    mesh.export(mesh_path)

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


def main() -> None:
    started = time.monotonic()
    request = json.loads(sys.stdin.read())
    configure_backends(request)

    source_dir = Path(request["source_dir"])
    port = source_dir / "trellis-mac"
    weights_dir = Path(request["weights_dir"])
    output_dir = Path(request["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    device = request.get("device", "mps")
    parameters = request.get("parameters", {})
    seed = request.get("seed") or 0

    # The port expects to be the working directory: its patches and stub packages
    # are found relative to it.
    sys.path.insert(0, str(port / "TRELLIS.2"))
    sys.path.insert(0, str(port))
    sys.path.append(str(port / "stubs"))

    try:
        import torch
        from PIL import Image
    except ImportError as exc:
        fail(
            "The TRELLIS.2 environment is incomplete.",
            technical=f"{type(exc).__name__}: {exc}",
            suggestions=["Reinstall the model with: volum models install trellis2"],
        )
        return

    torch.manual_seed(seed)

    pipeline = load_pipeline(weights_dir, device, torch)

    progress("preprocessing", "Preparing the image")
    image = Image.open(request["images"][0])
    if len(request["images"]) > 1:
        progress(
            "preprocessing",
            f"TRELLIS.2 uses one image; the first of {len(request['images'])} was used.",
        )

    pipeline_type = str(parameters.get("pipeline_type", "512"))
    progress("reconstructing", f"Reconstructing geometry at {pipeline_type}")
    try:
        outputs = pipeline.run(image, seed=seed, pipeline_type=pipeline_type)
    except (IndexError, AssertionError) as exc:
        if any(signature in str(exc) for signature in _WATCHDOG_SIGNATURES):
            # A killed Metal kernel. It raises nowhere near the cause, so the
            # message has to name it.
            fail(
                "The graphics driver stopped the model part-way through.",
                technical=(
                    "macOS GPU watchdog: a long-running Metal kernel in the decoder "
                    f"was killed, leaving empty tensors.\n{traceback.format_exc(limit=6)}"
                ),
                suggestions=_WATCHDOG_ADVICE,
            )
        fail("Generation failed.", technical=traceback.format_exc(limit=8))
        return
    except RuntimeError:
        message = traceback.format_exc()
        if "out of memory" in message.lower():
            fail(
                "There was not enough memory to generate this model.",
                technical=message[-4000:],
                suggestions=[
                    "Close other applications and try again.",
                    "TRELLIS.2 needs about 18 GB at peak.",
                ],
            )
        fail("Generation failed.", technical=message[-4000:])
        return

    mesh_out = outputs[0] if isinstance(outputs, list) else outputs
    write_result(mesh_out, output_dir, seed, device, started)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        emit(
            {
                "type": "error",
                "message": "The generation process stopped unexpectedly.",
                "technical": traceback.format_exc(limit=12),
                "suggestions": [],
            }
        )
        sys.exit(1)
