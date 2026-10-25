"""Work done on the input before a generator ever sees it."""

from .background import (
    SEGMENTER_ID,
    BackgroundOutcome,
    BackgroundRemoval,
    BackgroundRemovalError,
    remove_background,
    segmenter_available,
)
from .frames import (
    WORKING_GRID,
    FrameAnalysisError,
    FrameMeasurement,
    FrameOutcome,
    FrameSelection,
    analyse_frames,
    make_upright,
)

__all__ = [
    "SEGMENTER_ID",
    "WORKING_GRID",
    "BackgroundOutcome",
    "BackgroundRemoval",
    "BackgroundRemovalError",
    "FrameAnalysisError",
    "FrameMeasurement",
    "FrameOutcome",
    "FrameSelection",
    "analyse_frames",
    "make_upright",
    "remove_background",
    "segmenter_available",
]
