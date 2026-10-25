"""Work done on the input before a generator ever sees it."""

from .background import (
    SEGMENTER_ID,
    BackgroundOutcome,
    BackgroundRemoval,
    BackgroundRemovalError,
    remove_background,
    segmenter_available,
)

__all__ = [
    "SEGMENTER_ID",
    "BackgroundOutcome",
    "BackgroundRemoval",
    "BackgroundRemovalError",
    "remove_background",
    "segmenter_available",
]
