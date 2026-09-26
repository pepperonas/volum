"""Input handling: images are untrusted (spec section 50).

The checks here are the ones that turn an unexplained crash deep in a model
library into a message the user can act on — and the ones that stop a request
from reading or writing outside the data directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_png
from volum_core.inputs import (
    MAX_IMAGE_BYTES,
    InputError,
    sniff_image_type,
    stage_inputs,
    validate_image,
)


def test_png_jpeg_and_webp_are_recognised_by_content(tmp_path: Path) -> None:
    assert sniff_image_type(make_png(tmp_path / "a.png")) == "png"
    jpeg = tmp_path / "b.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    assert sniff_image_type(jpeg) == "jpeg"
    webp = tmp_path / "c.webp"
    webp.write_bytes(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBPVP8 " + b"\x00" * 32)
    assert sniff_image_type(webp) == "webp"


def test_the_extension_is_not_trusted(tmp_path: Path) -> None:
    """A text file renamed to .png is still a text file."""
    fake = tmp_path / "photo.png"
    fake.write_text("this is not an image", encoding="utf-8")
    assert sniff_image_type(fake) is None
    with pytest.raises(InputError) as excinfo:
        validate_image(fake)
    assert "PNG, JPEG or WebP" in excinfo.value.message


def test_a_missing_file_is_a_user_error_not_a_traceback(tmp_path: Path) -> None:
    with pytest.raises(InputError) as excinfo:
        validate_image(tmp_path / "nope.png")
    assert "not found" in excinfo.value.message.lower()


def test_a_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        validate_image(tmp_path)


def test_oversized_files_are_refused_before_being_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limit is checked on the size the filesystem reports, so a huge
    file is rejected without loading it."""
    big = make_png(tmp_path / "big.png")
    monkeypatch.setattr("volum_core.inputs.MAX_IMAGE_BYTES", 8)
    with pytest.raises(InputError) as excinfo:
        validate_image(big)
    assert "larger than" in excinfo.value.message
    assert MAX_IMAGE_BYTES > 8  # the module constant itself is untouched


def test_validate_reports_type_size_and_hash(tmp_path: Path) -> None:
    info = validate_image(make_png(tmp_path / "a.png"))
    assert info.image_type == "png"
    assert info.size_bytes == (tmp_path / "a.png").stat().st_size
    assert len(info.sha256) == 64


def test_staging_copies_under_generated_names(tmp_path: Path) -> None:
    """Inputs are copied into the job with UUID names: the original name is
    untrusted, and the job must survive the user deleting the original."""
    source = make_png(tmp_path / "my photo (1).png")
    staged = stage_inputs([source], tmp_path / "job" / "input")

    assert len(staged) == 1
    assert staged[0].path.parent == tmp_path / "job" / "input"
    assert staged[0].path.suffix == ".png"
    assert staged[0].path.stem.isalnum() and len(staged[0].path.stem) == 32
    assert staged[0].path.read_bytes() == source.read_bytes()
    assert staged[0].sha256 == validate_image(source).sha256


def test_staging_validates_every_file_before_copying_any(tmp_path: Path) -> None:
    good = make_png(tmp_path / "good.png")
    bad = tmp_path / "bad.png"
    bad.write_text("nope", encoding="utf-8")
    target = tmp_path / "job" / "input"

    with pytest.raises(InputError):
        stage_inputs([good, bad], target)

    assert not target.exists() or not any(target.iterdir())


def test_staging_nothing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        stage_inputs([], tmp_path / "input")


def test_staging_remembers_the_name_the_user_chose(tmp_path: Path) -> None:
    """The staged copy has a generated name, which is right — but the library
    then had nothing to show but a UUID. The original name is not trusted as a
    path; it is kept as a label."""
    source = make_png(tmp_path / "my horse.png")
    staged = stage_inputs([source], tmp_path / "job" / "input")

    assert staged[0].original_name == "my horse.png"
    assert staged[0].path.name != "my horse.png"


def test_the_remembered_name_is_only_ever_a_name(tmp_path: Path) -> None:
    # It reaches the window, so it must not carry a path out of the job.
    source = make_png(tmp_path / "x.png")
    staged = stage_inputs([source], tmp_path / "job" / "input")
    assert "/" not in staged[0].original_name and "\\" not in staged[0].original_name
