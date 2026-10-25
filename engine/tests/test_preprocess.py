"""The background-removal stage.

The segmenter runs in its own environment, so the worker is faked here with a
small script speaking the same one-JSON-line protocol. What is under test is the
part that decides *whether to trust the mask*: a cut-out of nothing and a
cut-out of everything are both failures, and silently handing either to a
generator is the mistake this stage exists to avoid.

The real worker is exercised separately, at the bottom, and skipped when the
segmenter is not installed — a skip can never be mistaken for a proof.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from volum_core.models.manager import MANIFEST_FILE, InstallManifest, ModelManager
from volum_core.preprocess import (
    SEGMENTER_ID,
    BackgroundOutcome,
    BackgroundRemoval,
    BackgroundRemovalError,
    remove_background,
    segmenter_available,
)
from volum_core.preprocess import background as bg
from volum_core.providers.workers import segment_worker

WEIGHT_REPO = "onnx-community/BiRefNet_lite-ONNX"
REVISION = "de15b22ba131738a16dff04aab8bdf8dc32e3ac1"


def install_segmenter(models: ModelManager) -> Path:
    """Make the segmenter look installed, down to its weights file."""
    interpreter = models.python_executable(SEGMENTER_ID)
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.touch()
    weights = models.weights_dir(SEGMENTER_ID) / "onnx" / "model.onnx"
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"not a real model")
    (models.directory(SEGMENTER_ID) / MANIFEST_FILE).write_text(
        InstallManifest(
            model_id=SEGMENTER_ID,
            volum_version="0.1.0",
            python_version="3.11",
            weight_revisions={WEIGHT_REPO: REVISION},
        ).model_dump_json(),
        encoding="utf-8",
    )
    return weights


def fake_worker(tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the worker script and run it with this interpreter."""
    script = tmp_path / "fake_worker.py"
    script.write_text(body, encoding="utf-8")
    monkeypatch.setattr(bg, "WORKER_SCRIPT", script)
    monkeypatch.setattr(
        ModelManager, "python_executable", lambda self, model_id: Path(sys.executable)
    )


REPLY = """
import json, sys
request = json.loads(sys.stdin.read())
{extra}
print(json.dumps({{**{payload}, "output": request["output"]}}))
"""


def worker_reporting(payload: dict[str, object], extra: str = "") -> str:
    # repr, not json.dumps: this is embedded in Python source, where JSON's
    # true/false/null are not names that exist.
    return REPLY.format(payload=repr(payload), extra=extra)


@pytest.fixture
def models(tmp_path: Path) -> ModelManager:
    return ModelManager(tmp_path / "models")


@pytest.fixture
def image(tmp_path: Path) -> Path:
    path = tmp_path / "subject.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return path


# --- not installed -------------------------------------------------------


def test_without_the_segmenter_the_original_is_used_and_says_so(
    models: ModelManager, image: Path, tmp_path: Path
) -> None:
    assert not segmenter_available(models)
    used, report = remove_background(image, tmp_path / "out", models)

    assert used == image
    assert report.outcome is BackgroundOutcome.NOT_INSTALLED
    assert not report.subject_is_isolated
    # The remedy belongs in the note: this is the only place the user is told.
    assert f"volum models install {SEGMENTER_ID}" in report.note


def test_installed_but_missing_weights_is_an_error_naming_the_path(
    models: ModelManager, image: Path, tmp_path: Path
) -> None:
    weights = install_segmenter(models)
    weights.unlink()
    with pytest.raises(BackgroundRemovalError) as excinfo:
        remove_background(image, tmp_path / "out", models)
    assert str(weights) in excinfo.value.technical


# --- a usable mask -------------------------------------------------------


def test_a_healthy_mask_is_used_and_the_revision_recorded(
    models: ModelManager, image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_segmenter(models)
    fake_worker(
        tmp_path,
        worker_reporting(
            {"type": "result", "coverage": 0.31, "duration_seconds": 1.5},
            extra='open(request["output"], "wb").write(b"cutout")',
        ),
        monkeypatch,
    )
    used, report = remove_background(image, tmp_path / "out", models)

    assert used != image
    assert used.read_bytes() == b"cutout"
    assert report.outcome is BackgroundOutcome.REMOVED
    assert report.subject_is_isolated
    assert report.coverage == pytest.approx(0.31)
    # Which weights made this mask is part of what makes the asset explainable.
    assert report.model_revision == REVISION
    assert "31%" in report.note


def test_a_mask_covering_the_whole_frame_is_used_but_reported_as_such(
    models: ModelManager, image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_segmenter(models)
    fake_worker(
        tmp_path,
        worker_reporting(
            {"type": "result", "coverage": 0.999},
            extra='open(request["output"], "wb").write(b"cutout")',
        ),
        monkeypatch,
    )
    _, report = remove_background(image, tmp_path / "out", models)

    assert report.outcome is BackgroundOutcome.REMOVED
    # Usable, but the thing the user asked for did not happen, so it is said.
    assert "no background" in report.note


# --- the guards ----------------------------------------------------------


def test_a_mask_of_nothing_is_discarded_rather_than_handed_on(
    models: ModelManager, image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The silent-empty-result rule, one stage before the mesh."""
    install_segmenter(models)
    fake_worker(
        tmp_path,
        worker_reporting(
            {"type": "result", "coverage": 0.0001},
            extra='open(request["output"], "wb").write(b"blank")',
        ),
        monkeypatch,
    )
    used, report = remove_background(image, tmp_path / "out", models)

    assert used == image, "a blank cut-out must never reach the generator"
    assert report.outcome is BackgroundOutcome.NO_SUBJECT_FOUND
    assert not report.subject_is_isolated
    # And it does not stay on disk pretending to be an input.
    assert not (tmp_path / "out" / f"{image.stem}_cutout.png").exists()


def test_an_image_that_already_has_a_matte_is_left_alone(
    models: ModelManager, image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_segmenter(models)
    fake_worker(
        tmp_path,
        worker_reporting({"type": "result", "already_transparent": True, "coverage": 0.23}),
        monkeypatch,
    )
    used, report = remove_background(image, tmp_path / "out", models)

    assert used == image
    assert report.outcome is BackgroundOutcome.ALREADY_TRANSPARENT
    # Isolated all the same: the question is about the mesh, not about who cut it.
    assert report.subject_is_isolated


# --- the worker misbehaving ----------------------------------------------


def test_a_worker_error_line_becomes_the_users_message(
    models: ModelManager, image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_segmenter(models)
    fake_worker(
        tmp_path,
        worker_reporting(
            {"type": "error", "message": "Could not load the model.", "technical": "x"}
        ),
        monkeypatch,
    )
    with pytest.raises(BackgroundRemovalError) as excinfo:
        remove_background(image, tmp_path / "out", models)
    assert excinfo.value.message == "Could not load the model."


def test_library_noise_before_the_reply_does_not_break_it(
    models: ModelManager, image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ONNX Runtime writes warnings to stdout on some builds."""
    install_segmenter(models)
    fake_worker(
        tmp_path,
        worker_reporting(
            {"type": "result", "coverage": 0.4},
            extra=(
                'print("2026-01-01 [W:onnxruntime] some warning")\n'
                'open(request["output"], "wb").write(b"cutout")'
            ),
        ),
        monkeypatch,
    )
    _, report = remove_background(image, tmp_path / "out", models)
    assert report.outcome is BackgroundOutcome.REMOVED


def test_a_worker_that_says_nothing_reports_what_it_printed_instead(
    models: ModelManager, image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_segmenter(models)
    fake_worker(
        tmp_path,
        'import sys; sys.stderr.write("Killed: 9\\n"); sys.exit(137)',
        monkeypatch,
    )
    with pytest.raises(BackgroundRemovalError) as excinfo:
        remove_background(image, tmp_path / "out", models)
    assert "Killed: 9" in excinfo.value.technical


def test_a_malformed_number_does_not_lose_an_otherwise_good_cutout(
    models: ModelManager, image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_segmenter(models)
    fake_worker(
        tmp_path,
        worker_reporting(
            {"type": "result", "coverage": "lots"},
            extra='open(request["output"], "wb").write(b"cutout")',
        ),
        monkeypatch,
    )
    used, report = remove_background(image, tmp_path / "out", models)
    # Coverage falls back to 0.0, which reads as "no subject found" — the safe
    # direction: the original is kept rather than a mask nobody can vouch for.
    assert used == image
    assert report.outcome is BackgroundOutcome.NO_SUBJECT_FOUND


# --- what each outcome means ---------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "isolated"),
    [
        (BackgroundOutcome.REMOVED, True),
        (BackgroundOutcome.ALREADY_TRANSPARENT, True),
        (BackgroundOutcome.NO_SUBJECT_FOUND, False),
        (BackgroundOutcome.NOT_INSTALLED, False),
        (BackgroundOutcome.FAILED, False),
    ],
)
def test_only_a_real_cutout_counts_as_isolated(outcome: BackgroundOutcome, isolated: bool) -> None:
    assert BackgroundRemoval(outcome=outcome, note="").subject_is_isolated is isolated


# --- against the real install --------------------------------------------
#
# These need the segmenter on disk, so they are skipped rather than faked. A
# skip is reported as a skip; it can never read as a pass.

INSTALLED_ROOT = Path.home() / "volum-data" / "models" / SEGMENTER_ID
real_install = pytest.mark.skipif(
    not (INSTALLED_ROOT / "weights" / "preprocessor_config.json").exists(),
    reason=f"{SEGMENTER_ID} is not installed in the developer data directory",
)


@real_install
def test_the_workers_constants_match_the_shipped_preprocessor_config() -> None:
    """The numbers in the worker are BiRefNet's own, not an approximation.

    Getting these wrong does not raise: it returns a worse mask. So the only
    thing that can catch a drift is a comparison against the file that ships
    beside the weights, which is what this is.
    """
    config = json.loads(
        (INSTALLED_ROOT / "weights" / "preprocessor_config.json").read_text(encoding="utf-8")
    )
    assert config["size"] == {
        "height": segment_worker.INPUT_SIZE,
        "width": segment_worker.INPUT_SIZE,
    }
    assert tuple(config["image_mean"]) == segment_worker.IMAGE_MEAN
    assert tuple(config["image_std"]) == segment_worker.IMAGE_STD
    # 2 is PIL's BILINEAR, which is what preprocess() asks for.
    assert config["resample"] == 2


@real_install
def test_the_real_worker_short_circuits_an_image_that_is_already_cut_out(
    tmp_path: Path,
) -> None:
    """Measured, not assumed: the model must not even be loaded for this.

    Loading it takes seconds and running it takes a great deal longer, so a
    short-circuit that still paid for the load would not be one.
    """
    example = (
        Path.home() / "volum-data" / "models" / "triposr" / "source" / "examples" / "chair.png"
    )
    if not example.exists():
        pytest.skip("the TripoSR example images are not on disk")

    request = json.dumps(
        {
            "image": str(example),
            "model_path": str(INSTALLED_ROOT / "weights" / "onnx" / "model.onnx"),
            "output": str(tmp_path / "out.png"),
        }
    )
    started = time.monotonic()
    completed = subprocess.run(  # noqa: S603 - fixed argv from this repository
        [str(INSTALLED_ROOT / "venv" / "bin" / "python"), str(bg.WORKER_SCRIPT)],
        input=request,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    elapsed = time.monotonic() - started

    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["already_transparent"] is True
    # chair.png is TripoSR's own example and carries a real matte.
    assert 0.1 < payload["coverage"] < 0.5
    # Loading the ONNX alone took 3.1 s when measured; this must be well under
    # that to prove the model was never touched.
    assert elapsed < 2.5, f"took {elapsed:.1f}s, so the model was probably loaded"


@real_install
def test_the_export_really_has_the_fixed_shape_the_worker_assumes() -> None:
    """``INPUT_SIZE`` is the export's requirement, not a tunable.

    Worth pinning because the comment in the worker claims it, and a future
    export with a dynamic axis would make the claim quietly wrong — along with
    the reasoning that there is no cheaper resolution to run at.
    """
    venv_python = INSTALLED_ROOT / "venv" / "bin" / "python"
    if not venv_python.exists():
        pytest.skip("the segmenter environment is missing")
    probe = (
        "import json, onnxruntime as ort;"
        f"s = ort.InferenceSession({str(INSTALLED_ROOT / 'weights' / 'onnx' / 'model.onnx')!r},"
        " providers=['CPUExecutionProvider']);"
        "print(json.dumps({'in': s.get_inputs()[0].shape,"
        " 'out': [o.name for o in s.get_outputs()]}))"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv from this repository
        [str(venv_python), "-c", probe],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    shape = json.loads(completed.stdout.strip().splitlines()[-1])
    assert shape["in"] == [1, 3, segment_worker.INPUT_SIZE, segment_worker.INPUT_SIZE]
    # pick_output() prefers this name; if it ever goes the fallback silently
    # changes which head is used.
    assert "output_image" in shape["out"]


@real_install
def test_an_alpha_channel_that_is_all_opaque_is_not_a_matte(tmp_path: Path) -> None:
    """A channel that exists says nothing; a channel with holes in it does.

    Plenty of PNGs carry a fully opaque alpha channel. Treating one as an
    existing cut-out would skip segmentation for exactly the images that need
    it most, and the skip would look like a success.

    Run through the segmenter's own interpreter: ``existing_matte`` needs Pillow
    and NumPy, which the engine environment deliberately does not have.
    """
    venv_python = INSTALLED_ROOT / "venv" / "bin" / "python"
    if not venv_python.exists():
        pytest.skip("the segmenter environment is missing")

    probe = f"""
import json, sys
sys.path.insert(0, {str(Path(segment_worker.__file__).parent)!r})
from segment_worker import existing_matte
from PIL import Image

opaque = Image.new("RGBA", (64, 64), (200, 30, 30, 255))
matted = Image.new("RGBA", (64, 64), (200, 30, 30, 255))
matted.putalpha(Image.new("L", (64, 64), 0))
matted.paste((200, 30, 30, 255), (16, 16, 48, 48))
plain = Image.new("RGB", (64, 64), (200, 30, 30))

# A palette image with a transparency index: a real matte that no list of
# modes names, which is why there is no mode check to list them.
paletted = Image.new("P", (64, 64), 0)
paletted.putpalette([200, 30, 30] + [0, 0, 0] * 255)
for y in range(16, 48):
    for x in range(16, 48):
        paletted.putpixel((x, y), 1)
paletted.info["transparency"] = 0

print(json.dumps({{
    "opaque": existing_matte(opaque),
    "matted": existing_matte(matted),
    "plain": existing_matte(plain),
    "paletted": existing_matte(paletted),
}}))
"""
    completed = subprocess.run(  # noqa: S603 - fixed argv from this repository
        [str(venv_python), "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    answer = json.loads(completed.stdout.strip().splitlines()[-1])

    assert answer["opaque"] is None, "an all-opaque alpha channel is not a cut-out"
    assert answer["plain"] is None, "an image with no alpha channel has no matte"
    # A quarter of the frame, which is what was pasted opaque.
    assert answer["matted"] == pytest.approx(0.25, abs=0.01)
    # Same quarter, reached through a palette transparency index.
    assert answer["paletted"] == pytest.approx(0.25, abs=0.01)
