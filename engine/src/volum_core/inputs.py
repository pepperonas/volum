"""Input images: validated by content, copied under generated names.

Images are untrusted input (spec section 50). Three things follow:

- **The type comes from the bytes, not the name.** A renamed text file is
  rejected before any model library sees it, where it would fail with a
  traceback that reads like a bug in VOLUM.
- **Size is checked on what the filesystem reports**, so a huge file is refused
  without being read.
- **Inputs are copied into the job under UUID names.** The original name never
  becomes a path inside the data directory, and a job stays reproducible when
  the user later deletes or edits the original.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

#: Above this a file is refused. Generous for a photo, and far below anything
#: that could exhaust memory in preprocessing.
MAX_IMAGE_BYTES = 50 * 1024 * 1024

#: image type -> file extension used for the staged copy.
_EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}

_SUPPORTED_DESCRIPTION = "PNG, JPEG or WebP"


class InputError(ValueError):
    """An input the user gave cannot be used. Carries a message for a person."""

    def __init__(self, message: str, technical: str = "", suggestions: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.technical = technical
        self.suggestions = suggestions or []


class ImageInput(BaseModel):
    path: Path
    image_type: str
    size_bytes: int
    sha256: str


def sniff_image_type(path: Path) -> str | None:
    """The image format according to the file's leading bytes, or ``None``."""
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError:
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def validate_image(path: Path) -> ImageInput:
    """Check one input image, or raise :class:`InputError` saying what is wrong."""
    if not path.exists():
        raise InputError(
            f"The image '{path.name}' was not found.",
            technical=f"{path} does not exist.",
            suggestions=["Check that the file has not been moved or deleted."],
        )
    if not path.is_file():
        raise InputError(
            f"'{path.name}' is not a file.",
            technical=f"{path} is a directory or a special file.",
        )

    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise InputError(
            f"The image '{path.name}' is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB.",
            technical=f"{size} bytes.",
            suggestions=["Export a smaller copy — a few megapixels is plenty for generation."],
        )
    if size == 0:
        raise InputError(f"The image '{path.name}' is empty.")

    image_type = sniff_image_type(path)
    if image_type is None:
        raise InputError(
            f"'{path.name}' is not a {_SUPPORTED_DESCRIPTION} image.",
            technical="The file's leading bytes match none of the supported formats.",
            suggestions=[f"Convert the image to {_SUPPORTED_DESCRIPTION} and try again."],
        )

    return ImageInput(path=path, image_type=image_type, size_bytes=size, sha256=_sha256(path))


def stage_inputs(paths: Iterable[Path], into: Path) -> list[ImageInput]:
    """Validate every image, then copy each into ``into`` under a UUID name.

    All-or-nothing: validation runs over the whole set before the first copy,
    so a bad third image does not leave two orphaned copies behind.
    """
    validated = [validate_image(path) for path in paths]
    if not validated:
        raise InputError("No input image was given.")

    into.mkdir(parents=True, exist_ok=True)
    staged: list[ImageInput] = []
    for info in validated:
        target = into / f"{uuid.uuid4().hex}{_EXTENSIONS[info.image_type]}"
        shutil.copyfile(info.path, target)
        staged.append(info.model_copy(update={"path": target}))
    return staged


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
