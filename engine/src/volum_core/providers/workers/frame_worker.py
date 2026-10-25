"""Frame measurement worker: what is actually in each candidate image.

**Runs in the engine's own interpreter**, unlike the generation and segmentation
workers, which each have an environment of their own. It needs only Pillow and
NumPy, both of which the engine already depends on, so there is nothing to
download and frame analysis works on a fresh install.

It is still a subprocess, for the reason the rest of this package is: images are
untrusted input (spec section 50). A malformed file that makes a decoder abort
takes the worker down, not the engine, and a frame that cannot be read comes
back as one row with an ``error`` instead of failing the whole set.

Two commands, because the caller needs the second one only after it has seen the
first: ``measure`` reports every candidate, ``upright`` writes a rotation-corrected
copy of the single frame that was chosen.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

#: The pixel grid every image is measured on, and not an arbitrary choice:
#: TripoSR's ``config.yaml`` declares ``cond_image_size: 512`` and TRELLIS.2
#: conditions DINOv3 at 518. Resolution beyond this never reaches a model, so
#: measuring above it would reward pixels nothing consumes.
#:
#: Images are resized to this grid in **both** directions, small ones included,
#: and that is load-bearing rather than tidy. A coarser pixel grid has more
#: contrast per step, so measuring a small image at its own size rewards it for
#: being small. Measured on the normalised score this worker actually reports:
#: resized only downwards, a 64 px copy of a 768 px image scores 1.380 against
#: the original's 1.274 — it would be *chosen*. Resized up to the grid it scores
#: 0.001, which is what is true of it at the size a model reads.
WORKING_GRID = 512

#: Rec. 709 luminance.
_LUMA = (0.2126, 0.7152, 0.0722)

#: What counts as clipped, in 8-bit terms. Not 255/0: an 8-bit channel that has
#: been through a resize or a JPEG round trip carries 254 and 1, and those
#: pixels are clipped in every sense that matters.
_CLIP_HIGH = 254.0 / 255.0
_CLIP_LOW = 1.0 / 255.0

#: Above this share of fully-opaque pixels an alpha channel is not a matte.
#: **Must stay equal to ``segment_worker.OPAQUE_SHARE_WITHOUT_MATTE``** — the two
#: workers cannot share a module, because one of them runs in an environment
#: where ``volum_core`` is not installed. A test pins the two together.
OPAQUE_SHARE_WITHOUT_MATTE = 0.995
OPAQUE = 0.99

#: EXIF tag 274. Orientation values 2..8 mean the stored pixels are mirrored or
#: rotated and a viewer is expected to correct them. Nothing in VOLUM did, so a
#: phone photograph was reconstructed lying on its side; this is where that is
#: caught.
_ORIENTATION_TAG = 274
_UPRIGHT = 1

#: Below this luminance variance an image is flat and the detail ratio has no
#: denominator. Far under anything a photograph reaches.
_FLAT = 1e-9

#: dHash grid. 8x8 comparisons need a 9x8 sample.
_HASH_SIZE = 8


def respond(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def fail(message: str, technical: str = "") -> None:
    respond({"type": "error", "message": message, "technical": technical})
    sys.exit(1)


def to_grid(image: Any, grid: int) -> Any:
    """Resize so the short edge is exactly ``grid``, up or down."""
    from PIL import Image

    rgb = image.convert("RGB")
    scale = grid / min(rgb.size)
    width = max(2, round(rgb.width * scale))
    height = max(2, round(rgb.height * scale))
    return rgb.resize((width, height), Image.Resampling.LANCZOS)


def luminance(rgb: Any) -> Any:
    import numpy as np

    array = np.asarray(rgb, dtype=np.float32) / 255.0
    return _LUMA[0] * array[:, :, 0] + _LUMA[1] * array[:, :, 1] + _LUMA[2] * array[:, :, 2]


def detail_of(lum: Any) -> float:
    """Gradient energy divided by the image's own spread.

    The plain variance of a Laplacian is what the literature calls sharpness, and
    on its own it is unusable here: it scales with exposure and contrast, so the
    same photograph brightened measures sharper. Measured on captured.jpeg,
    multiplying brightness by 2.6 raised it 2.8x while visibly destroying the
    surface. Dividing by the luminance variance cancels that gain exactly — the
    Laplacian is linear in intensity and the variance quadratic — and the
    measurement held to within 3% across brightness 0.3x and contrast 0.5x,
    while still falling 25-29x under a one-pixel blur and 11-13x at half scale.

    It does **not** survive clipping, which destroys surface detail while leaving
    steep edges behind. That is why the caller penalises clipping separately
    rather than trusting this number alone.
    """
    inner = lum[:-2, 1:-1] + lum[2:, 1:-1] + lum[1:-1, :-2] + lum[1:-1, 2:] - 4.0 * lum[1:-1, 1:-1]
    spread = float(lum.var())
    if spread <= _FLAT:
        # A uniform image has no detail, and no denominator either.
        return 0.0
    return float(inner.var()) / spread


def fingerprint(image: Any) -> str:
    """A 64-bit dHash, for spotting near-identical frames.

    Rows of "is this pixel brighter than its right-hand neighbour", which is
    invariant to scale, exposure and compression — measured on the example set:
    an image against its blurred, half-scale, quarter-scale, brightened, 5%
    cropped and JPEG-40 self differs by at most 5 bits of 64, while 91 pairs of
    *different* images differ by at least 15.

    Note that the caller hands this the **upright** image, which makes the same
    photograph stored rotated come back as near-identical to its upright twin —
    measured, and right: they are the same frame. A 90-degree rotation of
    *different* content still measures 24-36 bits away.
    """
    from PIL import Image

    small = image.convert("L").resize((_HASH_SIZE + 1, _HASH_SIZE), Image.Resampling.LANCZOS)
    import numpy as np

    rows = np.asarray(small, dtype=np.int16)
    bits = rows[:, 1:] > rows[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def matte_coverage(image: Any) -> float | None:
    """The subject share of an alpha matte the image already carries, or ``None``.

    Same rule as the segmentation worker: an alpha channel is only a matte if
    some of it is not opaque.
    """
    import numpy as np

    alpha = np.asarray(image.convert("RGBA").split()[-1], dtype=np.float32) / 255.0
    if float((alpha > OPAQUE).mean()) > OPAQUE_SHARE_WITHOUT_MATTE:
        return None
    return float(alpha.mean())


def orientation_of(image: Any) -> tuple[bool, int | None]:
    """``(has_exif, orientation)``. Both are facts about the file, not guesses."""
    try:
        exif = image.getexif()
    except Exception:
        # Some files carry EXIF that Pillow refuses; that is not a reason to
        # drop the whole measurement.
        return False, None
    if not exif:
        return False, None
    value = exif.get(_ORIENTATION_TAG)
    return True, int(value) if isinstance(value, int) else None


def measure_one(path: Path, grid: int) -> dict[str, Any]:
    from PIL import Image, ImageOps

    image = Image.open(path)
    image.load()
    stored = image.size
    has_exif, orientation = orientation_of(image)

    upright = ImageOps.exif_transpose(image) or image
    rgb = to_grid(upright, grid)
    lum = luminance(rgb)

    return {
        "width": upright.size[0],
        "height": upright.size[1],
        "stored_width": stored[0],
        "stored_height": stored[1],
        "size_bytes": path.stat().st_size,
        "format": (image.format or "").lower(),
        "detail": detail_of(lum),
        "contrast": float(lum.std()),
        "highlight_clipping": float((lum >= _CLIP_HIGH).mean()),
        "shadow_clipping": float((lum <= _CLIP_LOW).mean()),
        "alpha_coverage": matte_coverage(image),
        "has_exif": has_exif,
        "exif_orientation": orientation,
        # Reported, not inferred from the orientation value: Pillow decides what
        # it can actually correct, and a tag it does not understand changes
        # nothing about the pixels.
        "needs_upright": upright.size != stored
        or (orientation is not None and orientation != _UPRIGHT),
        "fingerprint": fingerprint(rgb),
    }


def do_measure(request: dict[str, Any]) -> None:
    grid = int(request.get("working_grid", WORKING_GRID))
    rows: list[dict[str, Any]] = []
    for entry in request["images"]:
        path = Path(entry["path"])
        row: dict[str, Any] = {"path": str(path), "name": entry.get("name") or path.name}
        try:
            row.update(measure_one(path, grid))
        except Exception as exc:
            # One unreadable frame must not cost the others. The reason travels
            # with the row so the caller can say which frame and why.
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    respond({"type": "result", "frames": rows, "working_grid": grid})


def do_upright(request: dict[str, Any]) -> None:
    """Write a rotation-corrected copy of one image.

    Only ever called for a frame whose EXIF says the stored pixels are not
    upright. Nothing else in VOLUM applied the tag, so a photograph taken in
    portrait on a phone was handed to the generator lying on its side.
    """
    from PIL import Image, ImageOps

    source = Path(request["image"])
    target = Path(request["output"])
    image = Image.open(source)
    image.load()
    corrected = ImageOps.exif_transpose(image) or image
    # PNG, so an alpha matte already on the image survives the rewrite.
    corrected.convert("RGBA" if corrected.mode in ("RGBA", "LA", "PA", "P") else "RGB").save(
        target, format="PNG"
    )
    respond(
        {
            "type": "result",
            "output": str(target),
            "width": corrected.size[0],
            "height": corrected.size[1],
            "was_rotated": corrected.size != image.size,
        }
    )


def main() -> None:
    request = json.loads(sys.stdin.read())
    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        fail(
            "Image analysis is unavailable in this build.",
            technical=f"{type(exc).__name__}: {exc}",
        )
        return

    command = request.get("command", "measure")
    if command == "measure":
        do_measure(request)
    elif command == "upright":
        do_upright(request)
    else:
        fail(f"Unknown command '{command}'.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # last resort: the engine needs a line, not a traceback
        fail(f"Image analysis failed unexpectedly: {exc}", technical=traceback.format_exc())
