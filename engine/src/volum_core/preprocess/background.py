"""Separating the subject from its background.

Every generator in VOLUM is single-image, and a single-image generator
reconstructs what it is shown. A wall behind the subject is not ignored — it
becomes geometry, which for a printable model means a surface fused to the back
of the object. Removing the background is therefore not cosmetic; it changes the
mesh.

The stage is **optional and honest about itself**. The segmenter is a separate
download, so a fresh install has no background removal, and a job then says so
instead of pretending. Nothing here ever silently substitutes a worse input:
when the mask is implausible the original image is used and the reason is
recorded.
"""

from __future__ import annotations

import json
import subprocess
import time
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from ..models.manager import InstallState, ModelManager
from ..providers.worker_provider import worker_environment

#: The tool that does the work. One id, named once.
SEGMENTER_ID = "birefnet"

WORKER_SCRIPT = (
    Path(__file__).resolve().parent.parent / "providers" / "workers" / ("segment_worker.py")
)

#: Generous on purpose. Measured on a 10-core M1 Pro: 43 s per image under
#: moderate load, 60-95 s under heavy load. The limit exists so a wedged worker
#: cannot hold a job open for ever — a timeout that fires on a machine that is
#: merely slow would be a false failure, which is worse than waiting.
_TIMEOUT_S = 600.0

#: Below this share of the frame, nothing recognisable was found. Handing such a
#: mask to a generator produces a mesh of almost nothing, so the cut-out is
#: discarded rather than used — the documented silent-empty-result failure,
#: applied one stage earlier.
_MIN_COVERAGE = 0.005

#: Above this, the mask covers essentially the whole frame: there was no
#: separable background. Usable, but the user is told, because the thing they
#: asked for did not happen.
_FULL_FRAME_COVERAGE = 0.98


class BackgroundRemovalError(RuntimeError):
    """The segmenter was asked to run and could not."""

    def __init__(self, message: str, technical: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.technical = technical


class BackgroundOutcome(StrEnum):
    """How the stage ended.

    An enum rather than a boolean because there are five distinct answers and
    only one of them is "it worked". Collapsing them loses the difference that
    matters most: an image that *already* carried a matte is isolated just as
    well as one this stage cut out, while a missing segmenter means the
    background is in the mesh. A reader of a single ``applied=False`` could not
    tell those apart.
    """

    REMOVED = "removed"
    ALREADY_TRANSPARENT = "already_transparent"
    NO_SUBJECT_FOUND = "no_subject_found"
    NOT_INSTALLED = "not_installed"
    FAILED = "failed"


class BackgroundRemoval(BaseModel):
    """What this stage did, in the asset metadata."""

    outcome: BackgroundOutcome
    note: str
    model_id: str | None = None
    model_revision: str | None = None
    coverage: float | None = Field(
        default=None,
        description="Share of the frame the subject covers, 0..1. Measured, not estimated.",
    )
    duration_seconds: float | None = None

    @property
    def subject_is_isolated(self) -> bool:
        """Whether the generator saw the subject without its background.

        True for a cut-out this stage made *and* for an image that already had
        one. That is the question a caller asks about the mesh it is holding.
        """
        return self.outcome in (
            BackgroundOutcome.REMOVED,
            BackgroundOutcome.ALREADY_TRANSPARENT,
        )


def segmenter_available(manager: ModelManager) -> bool:
    return manager.state(SEGMENTER_ID) is InstallState.INSTALLED


def _weights(manager: ModelManager) -> Path:
    return manager.weights_dir(SEGMENTER_ID) / "onnx" / "model.onnx"


def remove_background(
    image: Path,
    output_dir: Path,
    manager: ModelManager,
    *,
    threads: int = 0,
) -> tuple[Path, BackgroundRemoval]:
    """Cut the subject out of ``image``.

    Returns the image the generator should actually use and a record of what
    happened. The returned path is the original whenever the cut-out would be
    worse than no cut-out at all, so a caller can hand it straight on.
    """
    if not segmenter_available(manager):
        return image, BackgroundRemoval(
            outcome=BackgroundOutcome.NOT_INSTALLED,
            note=(
                "Background removal is not installed, so the image was used as it is. "
                "The background becomes part of the model. Install it with: "
                f"volum models install {SEGMENTER_ID}"
            ),
        )

    weights = _weights(manager)
    if not weights.exists():
        raise BackgroundRemovalError(
            "The background-removal model is installed but its weights are missing.",
            technical=f"Expected {weights}.",
        )
    if not WORKER_SCRIPT.exists():
        raise BackgroundRemovalError(
            "The background-removal worker is missing.",
            technical=f"Expected {WORKER_SCRIPT}.",
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{image.stem}_cutout.png"
    request = json.dumps(
        {
            "image": str(image),
            "model_path": str(weights),
            "output": str(target),
            "threads": threads,
        }
    )

    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, never a shell
            [str(manager.python_executable(SEGMENTER_ID)), str(WORKER_SCRIPT)],
            input=request,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            env=worker_environment(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackgroundRemovalError(
            "Background removal took too long and was stopped.",
            technical=f"Exceeded {_TIMEOUT_S}s.",
        ) from exc
    except OSError as exc:
        raise BackgroundRemovalError(
            "The background-removal environment could not be started.",
            technical=f"{type(exc).__name__}: {exc}",
        ) from exc

    payload = _last_json_line(completed.stdout)
    if payload is None:
        raise BackgroundRemovalError(
            "Background removal produced no result.",
            technical=(completed.stderr or completed.stdout or "no output").strip()[-2000:],
        )
    if payload.get("type") == "error":
        raise BackgroundRemovalError(
            str(payload.get("message", "Background removal failed.")),
            technical=str(payload.get("technical", "")),
        )

    coverage = _as_float(payload.get("coverage"), 0.0)
    duration = _as_float(payload.get("duration_seconds"), time.monotonic() - started)
    revision = _installed_revision(manager)

    if payload.get("already_transparent"):
        # The image arrived with a matte of its own — a PNG exported from another
        # tool, or one of a model's own example images. Replacing it would be
        # work for nothing and could only make the silhouette worse.
        return image, BackgroundRemoval(
            outcome=BackgroundOutcome.ALREADY_TRANSPARENT,
            note=(
                f"The image already had transparency covering {coverage * 100:.0f}% of "
                "the frame, so it was used as it is."
            ),
            model_id=SEGMENTER_ID,
            model_revision=revision,
            coverage=coverage,
            duration_seconds=duration,
        )

    if coverage < _MIN_COVERAGE:
        # Nothing was found. Using this would hand the generator a blank frame.
        target.unlink(missing_ok=True)
        return image, BackgroundRemoval(
            outcome=BackgroundOutcome.NO_SUBJECT_FOUND,
            note=(
                f"No subject was found — the mask covered {coverage * 100:.1f}% of the "
                "image — so the original was used unchanged. A photograph with one "
                "clear subject against a plain background works best."
            ),
            model_id=SEGMENTER_ID,
            model_revision=revision,
            coverage=coverage,
            duration_seconds=duration,
        )

    if coverage > _FULL_FRAME_COVERAGE:
        note = (
            f"The subject filled {coverage * 100:.0f}% of the frame, so there was "
            "effectively no background to remove."
        )
    else:
        note = f"Background removed; the subject covers {coverage * 100:.0f}% of the image."

    return target, BackgroundRemoval(
        outcome=BackgroundOutcome.REMOVED,
        note=note,
        model_id=SEGMENTER_ID,
        model_revision=revision,
        coverage=coverage,
        duration_seconds=duration,
    )


def _as_float(value: object, fallback: float) -> float:
    """A number out of untyped JSON, or the fallback.

    The worker is a separate process in a separate environment; its reply is
    data, not a trusted type. A missing or malformed field must not raise here
    and lose an otherwise good cut-out.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


def _last_json_line(stdout: str) -> dict[str, object] | None:
    """The worker's reply, ignoring anything a library printed before it.

    ONNX Runtime writes warnings to stdout on some builds, so the reply cannot be
    assumed to be the only line.
    """
    for raw in reversed(stdout.splitlines()):
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _installed_revision(manager: ModelManager) -> str | None:
    manifest = manager.manifest(SEGMENTER_ID)
    if manifest is None:
        return None
    return next(iter(manifest.weight_revisions.values()), None)
