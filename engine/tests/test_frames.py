"""Frame selection: the measurements, the ranking, and the honesty of both.

The images here are generated rather than fixtures, so the suite does not
depend on a model install, and they are deliberately *textured*: a flat image
has no gradient energy and would make every detail assertion vacuous.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageEnhance, ImageFilter

from volum_core.preprocess import (
    FrameAnalysisError,
    FrameMeasurement,
    FrameOutcome,
    analyse_frames,
    make_upright,
)
from volum_core.preprocess import frames as frames_module
from volum_core.providers.workers import frame_worker, segment_worker

SIZE = 768


def textured(seed: int = 7, size: int = SIZE) -> Image.Image:
    """Structured content at several scales, as a photograph has.

    Pure noise would be unfair to the blur assertions — it is all high
    frequency — so this carries large shapes as well as fine grain.
    """
    rng = np.random.default_rng(seed)
    grid = np.indices((size, size), dtype=np.float32)
    shapes = (
        np.sin(grid[0] / 29.0) * np.cos(grid[1] / 37.0) * 0.35
        + np.sin((grid[0] + grid[1]) / 11.0) * 0.15
    )
    grain = rng.normal(0.0, 0.10, (size, size)).astype(np.float32)
    field = np.clip(0.5 + shapes + grain, 0.0, 1.0)
    rgb = np.stack([field, field * 0.85 + 0.05, field * 0.7 + 0.1], axis=2)
    return Image.fromarray((rgb * 255).astype(np.uint8), mode="RGB")


def write(path: Path, image: Image.Image) -> Path:
    image.save(path)
    return path


# --- the pure ranking logic ------------------------------------------------
#
# Built by hand so the arithmetic is visible; the worker is exercised further
# down against real pixels.


def measurement(name: str, index: int, detail: float, clipping: float = 0.0) -> FrameMeasurement:
    return FrameMeasurement(
        name=name,
        path=Path(name),
        index=index,
        width=SIZE,
        height=SIZE,
        detail=detail,
        highlight_clipping=clipping,
        fingerprint=f"{index:016x}",
    )


def test_the_frame_with_the_most_detail_wins() -> None:
    frames = [measurement("a.png", 0, 0.2), measurement("b.png", 1, 0.9)]
    frames_module._score(frames)
    assert [f.rank for f in frames] == [2, 1]


def test_clipping_beats_the_detail_it_inflates() -> None:
    """The case the clipping term exists for.

    Measured on a real photograph: brightening it by 2.6x blew out 19% of the
    frame and *raised* its detail score 2.1x. Ranking on detail alone therefore
    prefers the ruined frame, which is the opposite of the truth.
    """
    good = measurement("good.png", 0, 0.74, clipping=0.0)
    blown = measurement("blown.png", 1, 1.57, clipping=0.19)
    frames_module._score([good, blown])
    assert good.score > blown.score
    assert good.rank == 1


def test_clipping_shared_by_the_whole_set_is_not_held_against_anyone() -> None:
    """A white backdrop is a backdrop, not a defect.

    The penalty is on excess over the least-clipped frame, so a series shot
    against the same background cancels out and the ranking falls back to
    detail.
    """
    dull = measurement("dull.png", 0, 0.30, clipping=0.42)
    sharp = measurement("sharp.png", 1, 0.90, clipping=0.42)
    frames_module._score([dull, sharp])
    assert sharp.rank == 1
    assert sharp.score == pytest.approx(1.0)


def test_frames_that_measure_the_same_are_reported_as_tied_not_decided() -> None:
    """A 0.4% difference is noise, and presenting it as a reason is inventing one."""
    first = measurement("first.png", 0, 0.739)
    second = measurement("second.png", 1, 0.742)
    frames = [first, second]
    frames_module._score(frames)

    # Second measures marginally higher, but inside the band the order the user
    # gave decides — otherwise "the first of them" names the wrong frame.
    assert [f.name for f in frames_module._tied(frames)] == ["first.png", "second.png"]
    reason = frames_module._reason(first, frames, frames_module.WORKING_GRID)
    assert "measure the same" in reason
    assert "first of them" in reason
    assert "x the next frame" not in reason


def test_a_real_difference_is_stated_as_a_ratio() -> None:
    frames = [measurement("sharp.png", 0, 0.90), measurement("soft.png", 1, 0.18)]
    frames_module._score(frames)
    reason = frames_module._reason(frames[0], frames, 512)
    assert "5.0x the next frame" in reason


def test_near_identical_frames_are_marked_and_still_eligible() -> None:
    """Reported, never dropped.

    A near-symmetric object photographed from opposite sides measures 2-5 bits
    apart, so false positives are real; they cost nothing as long as the best
    of a group can still be chosen.
    """
    frames = [
        measurement("a.png", 0, 0.3),
        measurement("b.png", 1, 0.9),
    ]
    frames[0].fingerprint = "ffffffffffffffff"
    frames[1].fingerprint = "ffffffffffffff0f"  # 3 bits apart
    frames_module._mark_near_identical(frames)
    frames_module._score(frames)

    assert frames[1].near_identical_to == "a.png"
    assert frames[1].rank == 1, "a near-duplicate must still be able to win"


# --- the worker, against real pixels ---------------------------------------


def test_blur_lowers_the_detail_measure(tmp_path: Path) -> None:
    base = textured()
    sharp = write(tmp_path / "sharp.png", base)
    soft = write(tmp_path / "soft.png", base.filter(ImageFilter.GaussianBlur(3)))

    selection = analyse_frames([sharp, soft])

    by_name = {f.name: f for f in selection.frames}
    assert by_name["sharp.png"].detail > by_name["soft.png"].detail * 2
    assert selection.chosen == "sharp.png"
    assert selection.outcome is FrameOutcome.SELECTED


def test_exposure_that_does_not_clip_leaves_the_measure_alone(tmp_path: Path) -> None:
    """The invariance the detail measure is built on.

    Without it the same photograph brightened measures sharper, and the
    selector prefers whichever frame was exposed hottest.
    """
    base = textured()
    plain = write(tmp_path / "plain.png", base)
    dim = write(tmp_path / "dim.png", ImageEnhance.Brightness(base).enhance(0.5))
    flat = write(tmp_path / "flat.png", ImageEnhance.Contrast(base).enhance(0.5))

    frames = {f.name: f for f in analyse_frames([plain, dim, flat]).frames}

    for name in ("dim.png", "flat.png"):
        assert frames[name].detail == pytest.approx(frames["plain.png"].detail, rel=0.05), name


def test_a_thumbnail_cannot_out_measure_the_full_frame(tmp_path: Path) -> None:
    """Why every image is resized to the grid in **both** directions.

    Measured: with only downscaling, a 64 px copy of this image scores 1.380
    against the 768 px original's 1.274 — a coarser pixel grid has more
    contrast per step, so the thumbnail would be *chosen*. Upscaling it to the
    grid instead gives 0.001, which is what is true of it.

    The size matters to this test. At a sixth scale the thumbnail loses either
    way, so it would pass with the upscale removed; a mutation probe found
    exactly that and this is the case that actually discriminates.
    """
    base = textured()
    full = write(tmp_path / "full.png", base)
    small = write(
        tmp_path / "small.png", base.resize((SIZE // 12, SIZE // 12), Image.Resampling.LANCZOS)
    )

    selection = analyse_frames([full, small])
    frames = {f.name: f for f in selection.frames}

    assert frames["small.png"].detail < frames["full.png"].detail
    assert selection.chosen == "full.png"
    assert any("short edge" in note for note in frames["small.png"].notes)


def test_resolution_above_the_grid_buys_nothing(tmp_path: Path) -> None:
    """Why resolution is not a separate reward.

    Halving a 1536 px image still leaves more pixels than a model consumes, so
    the measure must not prefer the larger file.
    """
    base = textured(size=1536)
    large = write(tmp_path / "large.png", base)
    half = write(tmp_path / "half.png", base.resize((768, 768), Image.Resampling.LANCZOS))

    frames = {f.name: f for f in analyse_frames([large, half]).frames}
    assert frames["half.png"].detail == pytest.approx(frames["large.png"].detail, rel=0.15)


def test_a_blown_out_frame_is_not_chosen(tmp_path: Path) -> None:
    base = textured()
    plain = write(tmp_path / "plain.png", base)
    blown = write(tmp_path / "blown.png", ImageEnhance.Brightness(base).enhance(2.6))

    selection = analyse_frames([plain, blown])
    frames = {f.name: f for f in selection.frames}

    assert frames["blown.png"].highlight_clipping > 0.1
    assert selection.chosen == "plain.png"
    assert any("blown out" in note for note in frames["blown.png"].notes)


def test_one_frame_is_measured_but_not_announced(tmp_path: Path) -> None:
    """Nothing to choose is not worth a progress line, but a defect is."""
    selection = analyse_frames([write(tmp_path / "only.png", textured())])

    assert selection.outcome is FrameOutcome.ONLY_ONE
    assert selection.chosen == "only.png"
    assert selection.note == ""
    assert selection.frames[0].detail > 0


def test_one_small_frame_still_says_what_is_wrong_with_it(tmp_path: Path) -> None:
    tiny = write(tmp_path / "tiny.png", textured().resize((120, 120), Image.Resampling.LANCZOS))
    selection = analyse_frames([tiny])
    assert "short edge" in selection.note


def test_an_unreadable_frame_does_not_cost_the_others(tmp_path: Path) -> None:
    good = write(tmp_path / "good.png", textured())
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\nnot really")

    selection = analyse_frames([broken, good])
    frames = {f.name: f for f in selection.frames}

    assert frames["broken.png"].error is not None
    assert frames["good.png"].error is None
    assert selection.chosen == "good.png"


def test_every_frame_unreadable_falls_back_to_the_first(tmp_path: Path) -> None:
    """A frame selection failure must not fail a job: the first image is still
    an image."""
    broken = [tmp_path / "a.png", tmp_path / "b.png"]
    for path in broken:
        path.write_bytes(b"\x89PNG\r\n\x1a\nrubbish")

    selection = analyse_frames(broken)
    assert selection.outcome is FrameOutcome.FAILED
    assert selection.chosen_index == 0
    assert "could be measured" in selection.note


def test_the_names_reported_are_the_user_s_not_the_staged_uuids(tmp_path: Path) -> None:
    staged = write(tmp_path / "0f3a9c21d4e5.png", textured())
    selection = analyse_frames([staged], names=["holiday snap.png"])
    assert selection.chosen == "holiday snap.png"


def test_the_measurements_are_the_file_s_own(tmp_path: Path) -> None:
    """Spec section 15: the analysis reports resolution, size and format, and
    reports them as read rather than as assumed."""
    path = write(tmp_path / "shot.png", textured())
    frame = analyse_frames([path]).frames[0]

    assert (frame.width, frame.height) == (SIZE, SIZE)
    assert frame.size_bytes == path.stat().st_size
    assert frame.format == "png"
    assert 0.0 < frame.contrast < 1.0


def test_an_existing_matte_is_reported_exactly(tmp_path: Path) -> None:
    base = textured().convert("RGBA")
    alpha = Image.new("L", base.size, 0)
    alpha.paste(255, (100, 100, 400, 400))
    base.putalpha(alpha)
    path = write(tmp_path / "cutout.png", base)

    frame = analyse_frames([path]).frames[0]
    covered = (300 * 300) / (SIZE * SIZE)
    assert frame.alpha_coverage == pytest.approx(covered, rel=0.02)
    assert any("Already has a cut-out" in note for note in frame.notes)


def test_a_fully_opaque_alpha_channel_is_not_a_matte(tmp_path: Path) -> None:
    """Plenty of PNGs carry one, and treating it as a cut-out would report a
    subject covering the whole frame."""
    path = write(tmp_path / "opaque.png", textured().convert("RGBA"))
    assert analyse_frames([path]).frames[0].alpha_coverage is None


# --- the orientation defect ------------------------------------------------


def oriented(path: Path, image: Image.Image, orientation: int) -> Path:
    """A JPEG that stores its pixels rotated, as a phone writes it."""
    exif = Image.Exif()
    exif[274] = orientation
    image.save(path, format="JPEG", quality=92, exif=exif)
    return path


def test_a_rotated_frame_is_detected(tmp_path: Path) -> None:
    """Nothing in VOLUM applied the EXIF orientation tag before this.

    Verified against a constructed file: 4032x3024 on disk, 3024x4032 upright.
    Left uncorrected, a photograph taken in portrait is reconstructed on its
    side — silently, because every stage reads the stored pixels.
    """
    portrait = textured().resize((400, 600), Image.Resampling.LANCZOS)
    stored = portrait.transpose(Image.Transpose.ROTATE_90)
    path = oriented(tmp_path / "phone.jpg", stored, orientation=6)

    frame = analyse_frames([path]).frames[0]

    assert (frame.stored_width, frame.stored_height) == (600, 400)
    assert (frame.width, frame.height) == (400, 600), "measured upright"
    assert frame.exif_orientation == 6
    assert frame.needs_upright is True
    assert any("Stored rotated" in note for note in frame.notes)


def test_an_upright_copy_is_written_for_a_rotated_frame(tmp_path: Path) -> None:
    portrait = textured().resize((400, 600), Image.Resampling.LANCZOS)
    stored = portrait.transpose(Image.Transpose.ROTATE_90)
    path = oriented(tmp_path / "phone.jpg", stored, orientation=6)

    corrected = make_upright(path, tmp_path / "out")

    assert corrected != path, "the user's own file is never rewritten"
    assert Image.open(corrected).size == (400, 600)


def test_an_already_upright_frame_is_not_flagged(tmp_path: Path) -> None:
    path = oriented(tmp_path / "fine.jpg", textured().resize((400, 600)), orientation=1)
    frame = analyse_frames([path]).frames[0]
    assert frame.needs_upright is False
    assert frame.has_exif is True


# --- contracts between files -----------------------------------------------


def test_the_two_workers_agree_on_what_a_matte_is() -> None:
    """The constant is duplicated because it has to be.

    ``segment_worker`` runs in the tool's own environment, where ``volum_core``
    is not installed, so the two cannot share a module. A drift between them
    would mean one worker calling an image cut out while the other does not.
    """
    assert frame_worker.OPAQUE_SHARE_WITHOUT_MATTE == segment_worker.OPAQUE_SHARE_WITHOUT_MATTE
    assert frame_worker.OPAQUE == segment_worker.OPAQUE


def test_the_worker_is_missing_is_an_error_a_person_can_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(frames_module, "WORKER_SCRIPT", tmp_path / "gone.py")
    with pytest.raises(FrameAnalysisError) as raised:
        analyse_frames([write(tmp_path / "a.png", textured())])
    assert "missing" in raised.value.message


def test_no_image_at_all_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FrameAnalysisError):
        analyse_frames([])


# --- against the real install ----------------------------------------------

TRIPOSR_CONFIG = Path.home() / "volum-data" / "models" / "triposr" / "weights" / "config.yaml"


@pytest.mark.skipif(
    not TRIPOSR_CONFIG.exists(), reason="triposr is not installed in the developer data directory"
)
def test_the_working_grid_is_the_size_the_model_actually_reads() -> None:
    """Not an arbitrary 512.

    TripoSR's own config declares ``cond_image_size``, and measuring above it
    would reward pixels nothing consumes. Read from the shipped file rather
    than trusted, because a change there silently changes what "enough
    resolution" means.
    """
    declared = next(
        int(line.split(":", 1)[1])
        for line in TRIPOSR_CONFIG.read_text(encoding="utf-8").splitlines()
        if line.startswith("cond_image_size:")
    )
    assert declared == frame_worker.WORKING_GRID
