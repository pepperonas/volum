"""Choosing which frame to reconstruct from.

Every generator in VOLUM is single-image. Given several photographs of one
object, the honest thing is therefore not to fuse them — none of these models
can — but to **measure them and reconstruct from the best one, saying so**.
``Capability.MULTI_IMAGE`` stays ``false`` and nothing here pretends otherwise
(spec section 14, ``docs/decision.md`` section 10).

What is measured, and what is not:

- **Detail** — gradient energy relative to the image's own spread, on the pixel
  grid a model actually reads. This is the signal that decides the ranking, and
  it is only comparable **within one set**: measured on the TripoSR examples,
  two equally sharp images of different subjects differ by 5.8x, because a
  furry horse has more gradient energy than a smooth teapot. So there is no
  absolute verdict here, and VOLUM does not offer one.
- **Highlight clipping** — a separate term, because it is the one thing that
  makes the detail measure *lie*: brightening captured.jpeg by 2.6x blew out 19%
  of it and raised its detail score 2.1x. Without this penalty the selector
  would prefer the ruined frame.
- **Near-identical frames** — reported, never dropped. The best of a group is
  still eligible, so a false positive costs nothing; and false positives exist,
  because a near-symmetric object photographed from opposite sides measures
  2-5 bits apart.
- **Resolution** — reported, not scored. It is already inside the detail
  measure: a half-size copy of a 512 px image scores 11-15x lower on the fixed
  grid, while a 3024 px photograph downscaled by four scores identically,
  because nothing above the grid reaches a model.
- **Contrast** — reported, not scored. Measured to carry no independent
  information: halving contrast moves detail and contrast together, by exactly
  the factor the algebra predicts.
- **Shadow clipping** — reported, not scored. A large black area is routinely
  legitimate: robot.png is 70% pure black by design, and penalising that would
  punish a studio shot for its backdrop.
- **Subject size** — reported only when the image carries a real alpha matte,
  where it is exact. Estimating it otherwise would need the segmenter on every
  candidate at 43 s each, and a guess dressed as a measurement is worse than an
  honest gap.

The exposure invariance cuts both ways, and that is a real limitation rather
than a detail: a frame at 30% brightness that does not clip measures the same as
a well-exposed one, to within 0.4%. It is *not* as good — it carries less signal
— but nothing here can see that, so the two come back as a tie and the order the
user gave them decides. Saying "they measure the same" is the truthful report;
inventing a preference would not be.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from ..providers.worker_provider import worker_environment
from ..providers.workers.frame_worker import WORKING_GRID

WORKER_SCRIPT = Path(__file__).resolve().parent.parent / "providers" / "workers" / "frame_worker.py"

#: Excess highlight clipping, over the least-clipped frame in the set, that
#: halves a frame's score.
#:
#: Grounded in the measured inflation rather than chosen: on captured.jpeg the
#: detail score rose by 33% at 3.1 points of clipping, 81% at 5.8 and 113% at
#: 19.1. A half-weight of 0.05 cancels that artefact and leaves a real penalty
#: on top, which is wanted — blown highlights are damage, not just a
#: measurement problem.
#:
#: It is **excess over the set**, so a whole series shot against a white
#: backdrop is not punished for the backdrop; only the frame that is worse than
#: its siblings is.
_CLIPPING_HALF_WEIGHT = 0.05

#: Highlight clipping worth mentioning on a frame at all. Measured: at 3.1% the
#: same photograph is visibly bright but usable, at 19% the surface is gone.
#: This only decides whether a note is written; the score uses excess over the
#: set, which is a different question.
_CLIPPING_WORTH_SAYING = 0.02

#: Relative score difference under which two frames are not distinguishable.
#:
#: Measured, not chosen: the detail measure held to within 3% across brightness
#: 0.3x and contrast 0.5x of the same photograph, so 3% is the noise floor of
#: the instrument. Inside it there is nothing to choose, and saying there is
#: would be inventing a reason — a darkened copy of captured.jpeg scored 0.4%
#: above the original, which is a tie dressed up as a decision.
_TIE_BAND = 0.03

#: Hamming distance, of 64 bits, under which two frames count as near-identical.
#: Measured on the TripoSR examples: an image against its blurred, half-scale,
#: quarter-scale, brightened, cropped and JPEG-40 self differs by at most 5
#: bits, while 91 pairs of different images differ by at least 15. Ten is the
#: middle of that gap.
_DUPLICATE_DISTANCE = 10

#: Generous. Measured on this machine, median of five warm runs including
#: process start: 0.27 s for one 3024x4032 JPEG, 0.09 s for a 512 px PNG, 0.45 s
#: for ten mixed images in one call — of which 0.08 s is the interpreter and its
#: imports. Measuring the input therefore costs well under a second against a
#: generation measured in minutes, which is why it runs for a single image too.
#: The limit exists so a wedged worker cannot hold a job open for ever, not to
#: catch a slow machine.
_TIMEOUT_S = 300.0


class FrameAnalysisError(RuntimeError):
    """The frames could not be measured."""

    def __init__(self, message: str, technical: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.technical = technical


class FrameOutcome(StrEnum):
    """How frame selection ended.

    An enum rather than a boolean for the same reason as
    :class:`~volum_core.preprocess.background.BackgroundOutcome`: "one frame was
    given" and "several were given and measuring them failed" both end with the
    first image being used, and they are not the same thing to anyone looking at
    a disappointing mesh.
    """

    SELECTED = "selected"
    ONLY_ONE = "only_one"
    FAILED = "failed"


class FrameMeasurement(BaseModel):
    """One candidate frame, measured.

    ``score`` and ``rank`` are properties of the *set*, not of the image: the
    same photograph scores differently among different siblings. Everything
    above them is a measurement of the file alone.
    """

    name: str
    path: Path
    #: Position in the submitted list. Carried explicitly because two frames can
    #: measure identically, and a value-equal lookup would then find the wrong
    #: one — and because "frame 3 of 5" is what a person needs to be told.
    index: int = 0
    #: Upright dimensions — after the EXIF orientation tag, which is what every
    #: later stage sees once the frame has been corrected.
    width: int = 0
    height: int = 0
    #: As the bytes are laid out on disk. Differs from the above exactly when
    #: the orientation tag says the pixels are rotated, which is the evidence
    #: that the correction was needed.
    stored_width: int = 0
    stored_height: int = 0
    size_bytes: int = 0
    format: str = ""
    #: Gradient energy over luminance variance at ``working_grid``. Relative
    #: within a set only; see the module docstring.
    detail: float = 0.0
    contrast: float = 0.0
    highlight_clipping: float = 0.0
    shadow_clipping: float = 0.0
    #: Exact subject share when the image already carries a matte, else ``None``.
    #: Never estimated.
    alpha_coverage: float | None = None
    has_exif: bool = False
    exif_orientation: int | None = None
    #: Whether the stored pixels are rotated or mirrored per EXIF. Nothing in
    #: VOLUM applied that tag before, so a phone photograph taken in portrait
    #: reached the generator lying on its side.
    needs_upright: bool = False
    fingerprint: str = ""
    score: float = 0.0
    rank: int = 0
    #: The name of the first frame of its near-identical group, when it is not
    #: that frame itself.
    near_identical_to: str | None = None
    notes: list[str] = Field(default_factory=list)
    #: Set when this one frame could not be measured. The others still are.
    error: str | None = None


class FrameSelection(BaseModel):
    """Which frame was reconstructed, and why — for ``asset.json`` and the UI."""

    outcome: FrameOutcome
    #: Index into the submitted list, so a reader can tell *which* of
    #: ``source_images`` the mesh actually came from.
    chosen_index: int
    chosen: str
    reason: str
    note: str
    working_grid: int = WORKING_GRID
    frames: list[FrameMeasurement] = Field(default_factory=list)
    duration_seconds: float | None = None

    @property
    def combined(self) -> bool:
        """Always false, and said out loud rather than left to be assumed.

        ``source_images`` in the asset metadata lists everything that was
        submitted, which could be read as everything having been used.
        """
        return False


def _hamming(left: str, right: str) -> int:
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except ValueError:
        # A fingerprint is missing on a frame that failed to measure; such a
        # frame is in no group.
        return 64


def _mark_near_identical(frames: list[FrameMeasurement]) -> None:
    """Group frames that look the same, keeping every one of them eligible."""
    for index, frame in enumerate(frames):
        if frame.error is not None or not frame.fingerprint:
            continue
        for earlier in frames[:index]:
            if earlier.error is not None or earlier.near_identical_to is not None:
                continue
            if _hamming(frame.fingerprint, earlier.fingerprint) <= _DUPLICATE_DISTANCE:
                frame.near_identical_to = earlier.name
                break


def _score(frames: list[FrameMeasurement]) -> None:
    """Score and rank, relative to this set and no other."""
    usable = [frame for frame in frames if frame.error is None]
    if not usable:
        return
    best_detail = max(frame.detail for frame in usable)
    least_clipped = min(frame.highlight_clipping for frame in usable)

    for frame in usable:
        relative = frame.detail / best_detail if best_detail > 0 else 0.0
        excess = max(0.0, frame.highlight_clipping - least_clipped)
        frame.score = relative / (1.0 + excess / _CLIPPING_HALF_WEIGHT)

    # Ties break on the order the user gave, which is the only information left
    # once the measurements agree — and it is predictable, unlike a float.
    for rank, frame in enumerate(
        sorted(usable, key=lambda frame: (-frame.score, frame.index)), start=1
    ):
        frame.rank = rank


def _notes_for(frame: FrameMeasurement, grid: int) -> list[str]:
    notes: list[str] = []
    short = min(frame.width, frame.height)
    if short and short < grid:
        notes.append(
            f"{short} px on the short edge, below the {grid} px a model reads, "
            "so detail has to be invented."
        )
    if frame.needs_upright:
        notes.append("Stored rotated; the orientation tag was applied before use.")
    if frame.highlight_clipping > _CLIPPING_WORTH_SAYING:
        notes.append(f"{frame.highlight_clipping * 100:.0f}% of it is blown out.")
    if frame.alpha_coverage is not None:
        notes.append(
            f"Already has a cut-out; the subject covers {frame.alpha_coverage * 100:.0f}%."
        )
    return notes


def _tied(frames: list[FrameMeasurement]) -> list[FrameMeasurement]:
    """The frames this measure cannot tell apart, **in the order they were given**.

    Always at least one. More than one means the choice between them is not a
    measurement and must not be reported as though it were — so inside the band
    the submission order decides, not the third decimal place of a score.
    Ordering these by score instead would make "the first of them" name the
    frame that happened to measure 0.4% higher, which is the opposite of what
    the sentence promises.
    """
    usable = [frame for frame in frames if frame.error is None]
    if not usable:
        return []
    band = max(frame.score for frame in usable) * (1.0 - _TIE_BAND)
    return sorted((frame for frame in usable if frame.score >= band), key=lambda frame: frame.index)


def _reason(chosen: FrameMeasurement, frames: list[FrameMeasurement], grid: int) -> str:
    usable = [frame for frame in frames if frame.error is None]
    if len(usable) == 1:
        return f"{chosen.name} was the only frame that could be measured."

    tied = _tied(frames)
    if len(tied) > 1:
        others = ", ".join(frame.name for frame in tied[1:])
        return (
            f"{chosen.name} and {others} measure the same to within the "
            f"{_TIE_BAND * 100:.0f}% this can distinguish, so the first of them was used."
        )

    runner_up = min(
        (frame for frame in usable if frame.index != chosen.index), key=lambda frame: frame.rank
    )
    if runner_up.highlight_clipping > chosen.highlight_clipping + _CLIPPING_HALF_WEIGHT:
        return (
            f"{chosen.name} carries the most usable detail: {runner_up.name} measures "
            f"more gradient energy, but {runner_up.highlight_clipping * 100:.0f}% of it "
            f"is blown out against {chosen.highlight_clipping * 100:.0f}%."
        )
    if runner_up.detail > 0:
        return (
            f"{chosen.name} has the most detail at {grid} px — "
            f"{chosen.detail / runner_up.detail:.1f}x the next frame, {runner_up.name}."
        )
    return f"{chosen.name} has the most detail at {grid} px."


def _note(outcome: FrameOutcome, chosen: FrameMeasurement, count: int) -> str:
    """What to tell the user during the job. Empty when there is nothing to say.

    One frame is not a decision, and reporting "there was nothing to choose
    between" on every single-image job would be noise on the common path. What
    is worth hearing either way is anything wrong with the frame itself — too
    few pixels, stored rotated, blown out — so that is what comes through.
    """
    about_the_frame = " ".join(chosen.notes)
    if outcome is FrameOutcome.ONLY_ONE:
        return about_the_frame
    headline = (
        f"{count} frames were measured and one was reconstructed: {chosen.name}. "
        "The frames are not combined — no model in VOLUM reconstructs from several "
        "views, so using more than one would mean pretending."
    )
    return f"{headline} {about_the_frame}".strip()


def _run_worker(request: dict[str, object], python: Path | None) -> dict[str, object]:
    if not WORKER_SCRIPT.exists():
        raise FrameAnalysisError(
            "The image-analysis worker is missing.", technical=f"Expected {WORKER_SCRIPT}."
        )
    interpreter = str(python) if python is not None else sys.executable
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, never a shell
            [interpreter, str(WORKER_SCRIPT)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            env=worker_environment(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FrameAnalysisError(
            "Measuring the images took too long and was stopped.",
            technical=f"Exceeded {_TIMEOUT_S}s.",
        ) from exc
    except OSError as exc:
        raise FrameAnalysisError(
            "The image-analysis worker could not be started.",
            technical=f"{type(exc).__name__}: {exc}",
        ) from exc

    payload = _last_json_line(completed.stdout)
    if payload is None:
        raise FrameAnalysisError(
            "Measuring the images produced no result.",
            technical=(completed.stderr or completed.stdout or "no output").strip()[-2000:],
        )
    if payload.get("type") == "error":
        raise FrameAnalysisError(
            str(payload.get("message", "Measuring the images failed.")),
            technical=str(payload.get("technical", "")),
        )
    return payload


def analyse_frames(
    images: list[Path],
    *,
    names: list[str] | None = None,
    python: Path | None = None,
) -> FrameSelection:
    """Measure every frame and say which one to reconstruct from.

    ``names`` are the labels to report — the staged copies are named by UUID, so
    without them a report has nothing readable in it.
    """
    if not images:
        raise FrameAnalysisError("No image was given.")

    started = time.monotonic()
    labels = names or [path.name for path in images]
    payload = _run_worker(
        {
            "command": "measure",
            "images": [
                {"path": str(path), "name": label}
                for path, label in zip(images, labels, strict=False)
            ],
        },
        python,
    )

    raw_grid = payload.get("working_grid")
    grid = raw_grid if isinstance(raw_grid, int) and raw_grid > 0 else WORKING_GRID
    rows = payload.get("frames")
    if not isinstance(rows, list) or len(rows) != len(images):
        got = len(rows) if isinstance(rows, list) else type(rows).__name__
        raise FrameAnalysisError(
            "The image analysis came back incomplete.",
            technical=f"Expected {len(images)} rows, got {got}.",
        )

    frames = [
        FrameMeasurement.model_validate({**row, "index": index}) for index, row in enumerate(rows)
    ]
    for frame in frames:
        frame.notes = _notes_for(frame, grid)
    _mark_near_identical(frames)
    _score(frames)

    usable = [frame for frame in frames if frame.error is None]
    if not usable:
        first = frames[0]
        return FrameSelection(
            outcome=FrameOutcome.FAILED,
            chosen_index=0,
            chosen=first.name,
            reason="No frame could be measured, so the first one was used.",
            note=(
                f"None of the {len(frames)} images could be measured, so the first was "
                "used unchanged. The reason is on each frame."
            ),
            working_grid=grid,
            frames=frames,
            duration_seconds=time.monotonic() - started,
        )

    chosen = _tied(frames)[0]
    outcome = FrameOutcome.ONLY_ONE if len(frames) == 1 else FrameOutcome.SELECTED
    return FrameSelection(
        outcome=outcome,
        chosen_index=chosen.index,
        chosen=chosen.name,
        reason=_reason(chosen, frames, grid),
        note=_note(outcome, chosen, len(frames)),
        working_grid=grid,
        frames=frames,
        duration_seconds=time.monotonic() - started,
    )


def make_upright(image: Path, output_dir: Path, *, python: Path | None = None) -> Path:
    """Write a rotation-corrected copy of ``image``, or return it unchanged.

    Only called for a frame whose EXIF says the stored pixels are not upright.
    Nothing else in VOLUM applies that tag, so without this a photograph taken
    in portrait on a phone is reconstructed on its side — verified by building
    such a file: 4032x3024 on disk, 3024x4032 upright.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{image.stem}_upright.png"
    payload = _run_worker(
        {"command": "upright", "image": str(image), "output": str(target)}, python
    )
    written = payload.get("output")
    if not isinstance(written, str) or not Path(written).exists():
        raise FrameAnalysisError(
            "The rotation-corrected copy was not written.", technical=str(payload)
        )
    return Path(written)


def _last_json_line(stdout: str) -> dict[str, object] | None:
    """The worker's reply, ignoring anything a library printed before it."""
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


__all__ = [
    "WORKING_GRID",
    "FrameAnalysisError",
    "FrameMeasurement",
    "FrameOutcome",
    "FrameSelection",
    "analyse_frames",
    "make_upright",
]
