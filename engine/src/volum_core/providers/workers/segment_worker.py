"""Background segmentation worker: BiRefNet over ONNX Runtime.

**Runs in the tool's own virtual environment, not the engine's.** It imports
nothing from ``volum_core`` — that package is not installed here. One JSON
object in on stdin, one JSON object out on stdout, same discipline as the
generation workers.

Why a subprocess at all: ONNX Runtime and NumPy are not in the engine's
environment and must not be. The engine ships inside the
application bundle, so every megabyte there is a megabyte in the installer,
while a tool's environment lives in the data directory and can be removed again.

The preprocessing numbers below are BiRefNet's own, from the
``preprocessor_config.json`` that ships beside the weights. They are written out
rather than read from that file on purpose: getting them wrong does not fail, it
silently returns a worse mask, so they belong somewhere a test can pin them.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Any

#: BiRefNet's input resolution, and not a choice: the ONNX export declares a
#: fixed input shape of [1, 3, 1024, 1024], so there is no cheaper size to run
#: at. The mask is scaled back to the image's own size afterwards, so the user's
#: resolution is never reduced.
INPUT_SIZE = 1024

#: ImageNet statistics, as in preprocessor_config.json. Note that the first mean
#: is 0.485 and not the more common 0.485/0.456/0.406 ordering mistake — these
#: are copied from the file, not from memory.
IMAGE_MEAN = (0.485, 0.456, 0.406)
IMAGE_STD = (0.229, 0.224, 0.225)

#: Above this share of fully-opaque pixels an alpha channel is not a matte —
#: it is a channel that happens to exist and says nothing. Below it, the image
#: already carries a cut-out and segmenting again could only make it worse.
OPAQUE_SHARE_WITHOUT_MATTE = 0.995

#: What counts as opaque. Not 1.0: an 8-bit alpha that went through a resize
#: carries values like 254, and those pixels are opaque in every sense that
#: matters here.
OPAQUE = 0.99

#: A mask is two-dimensional. Whatever batch and channel axes the export happens
#: to carry are peeled off down to this.
MASK_RANK = 2


def respond(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def fail(message: str, technical: str = "") -> None:
    respond({"type": "error", "message": message, "technical": technical})
    sys.exit(1)


def existing_matte(image: Any) -> float | None:
    """The coverage of a matte the image already has, or ``None`` if it has none.

    An alpha channel alone does not mean the subject was cut out: plenty of PNGs
    carry a fully opaque one. What distinguishes a real matte is that some of it
    is *not* opaque — which is the only thing tested here.

    There is deliberately no check on ``image.mode`` first. It would be
    redundant, because an image without transparency converts to a fully opaque
    alpha and fails the test below anyway; and it would be *wrong*, because a
    palette image carrying a transparency index is a real matte that no list of
    modes names. ``convert("RGBA")`` resolves all of those cases.
    """
    import numpy as np

    alpha = np.asarray(image.convert("RGBA").split()[-1], dtype=np.float32) / 255.0
    if float((alpha > OPAQUE).mean()) > OPAQUE_SHARE_WITHOUT_MATTE:
        return None
    return float(alpha.mean())


def preprocess(image: Any) -> Any:
    """Image to the model's input tensor: resize, rescale, normalise, NCHW."""
    import numpy as np
    from PIL import Image

    resized = image.convert("RGB").resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
    scaled = np.asarray(resized, dtype=np.float32) / 255.0
    mean = np.array(IMAGE_MEAN, dtype=np.float32)
    std = np.array(IMAGE_STD, dtype=np.float32)
    normalised = ((scaled - mean) / std).astype(np.float32)
    return np.transpose(normalised, (2, 0, 1))[None, ...]


def pick_output(session: Any, outputs: list[Any]) -> Any:
    """Choose the prediction among possibly several heads.

    BiRefNet is trained with intermediate supervision and some exports keep
    those heads. The final prediction is the one named ``output_image``; where
    the name is absent the last output is the deepest head, which is the
    convention upstream follows.
    """
    names = [out.name for out in session.get_outputs()]
    if "output_image" in names:
        return outputs[names.index("output_image")]
    return outputs[-1]


def to_mask(raw: Any, size: tuple[int, int]) -> Any:
    """Logits to an 8-bit alpha channel at the image's own size."""
    import numpy as np
    from PIL import Image

    array = np.asarray(raw, dtype=np.float32)
    # Drop batch and channel dimensions however they are shaped: what matters is
    # the trailing two axes.
    while array.ndim > MASK_RANK:
        array = array[0]
    mask = 1.0 / (1.0 + np.exp(-array))
    as_image = Image.fromarray((mask * 255.0).astype(np.uint8), mode="L")
    return as_image.resize(size, Image.BILINEAR)


def main() -> None:
    started = time.monotonic()
    request = json.loads(sys.stdin.read())

    image_path = request["image"]
    model_path = request["model_path"]
    output_path = request["output"]
    threads = int(request.get("threads", 0))

    try:
        import numpy as np
        import onnxruntime
        from PIL import Image
    except ImportError as exc:
        fail(
            "The background-removal environment is incomplete.",
            technical=f"{type(exc).__name__}: {exc}",
        )
        return

    try:
        image = Image.open(image_path)
        image.load()
    except Exception as exc:
        fail("The image could not be read.", technical=f"{type(exc).__name__}: {exc}")
        return

    # Checked before the model is loaded, which is the whole point: loading it
    # takes longer than this test and the answer would be the same.
    existing = existing_matte(image)
    if existing is not None:
        respond(
            {
                "type": "result",
                "already_transparent": True,
                "output": image_path,
                "coverage": existing,
                "width": image.size[0],
                "height": image.size[1],
                "duration_seconds": time.monotonic() - started,
            }
        )
        return

    options = onnxruntime.SessionOptions()
    if threads > 0:
        # Left at the default (all cores) unless asked. Measured on a 10-core
        # M1 Pro, 8 and 4 threads came out within 6% of the default, so there is
        # nothing to gain by choosing — but a caller sharing the machine with
        # something else may still want to.
        options.intra_op_num_threads = threads
    try:
        session = onnxruntime.InferenceSession(
            model_path, sess_options=options, providers=["CPUExecutionProvider"]
        )
    except Exception as exc:
        fail(
            "The background-removal model could not be loaded.",
            technical=f"{type(exc).__name__}: {exc}",
        )
        return

    input_name = session.get_inputs()[0].name
    try:
        outputs = session.run(None, {input_name: preprocess(image)})
    except Exception as exc:
        fail("Background removal failed.", technical=f"{type(exc).__name__}: {exc}")
        return

    mask = to_mask(pick_output(session, outputs), image.size)

    # The share of the frame the subject covers. This is the number that decides
    # whether the result is usable, so it is measured and reported rather than
    # assumed: a mask covering nothing means no subject was found, and a mask
    # covering everything means no background was.
    alpha = np.asarray(mask, dtype=np.float32) / 255.0
    coverage = float(alpha.mean())

    composited = image.convert("RGB")
    composited.putalpha(mask)
    composited.save(output_path, format="PNG")

    respond(
        {
            "type": "result",
            "output": output_path,
            "coverage": coverage,
            "width": image.size[0],
            "height": image.size[1],
            "duration_seconds": time.monotonic() - started,
            "input_name": input_name,
            "output_names": [out.name for out in session.get_outputs()],
        }
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # last resort: the engine needs a line, not a traceback
        fail(f"Background removal failed unexpectedly: {exc}", technical=traceback.format_exc())
