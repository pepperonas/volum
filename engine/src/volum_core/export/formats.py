"""Export to the formats a target actually consumes.

The format is not a preference, it is a consequence of what the asset is for:

- **STL** carries triangles and nothing else. No colour, no material, no units.
  It is what a slicer wants for a single-material print.
- **3MF** carries geometry *and* units, and can carry colour and materials. It is
  the right target for multi-material printing, and the only printing format that
  is not effectively unitless.
- **GLB** carries PBR materials and UVs. It is the right target for engines and
  the web, and useless to a printer.

Scale matters here in a way it does not for rendering. Generated meshes come out
at an arbitrary unit scale; a printer needs millimetres. Exporting a print format
without deciding a size produces an object a few millimetres across, which looks
like a bug in the printer rather than in the export.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

import numpy as np
import trimesh
from pydantic import BaseModel, Field


class ExportFormat(StrEnum):
    GLB = "glb"
    STL = "stl"
    THREEMF = "3mf"
    OBJ = "obj"
    PLY = "ply"

    @property
    def is_print_format(self) -> bool:
        """Whether this format is meant for a slicer.

        Print formats are validated more strictly and are scaled to real units;
        a mesh with holes is a rendering blemish and a printing failure.
        """
        return self in (ExportFormat.STL, ExportFormat.THREEMF)

    @property
    def carries_colour(self) -> bool:
        """Whether the format can carry surface colour *as VOLUM writes it*.

        3MF is capable of colour through its Materials extension, but the writer
        in use here emits geometry only — measured, not assumed. Saying 3MF
        "supports colour" and then shipping a grey file is the kind of claim
        this property exists to prevent.
        """
        return self in (ExportFormat.GLB, ExportFormat.OBJ, ExportFormat.PLY)


#: Formats a slicer accepts, in the order VOLUM offers them.
PRINT_FORMATS = (ExportFormat.STL, ExportFormat.THREEMF)


class ExportOptions(BaseModel):
    formats: tuple[ExportFormat, ...] = (ExportFormat.GLB,)
    #: Longest side of the exported object, in millimetres. ``None`` keeps the
    #: model's own scale, which is arbitrary and almost never what a printer wants.
    target_size_mm: float | None = Field(default=None, gt=0)
    #: Move the object so it sits on z=0 and is centred in x/y. What a slicer
    #: expects on the build plate; harmless for other targets.
    lay_on_build_plate: bool = False


class UnknownFormatError(ValueError):
    def __init__(self, name: str) -> None:
        known = ", ".join(fmt.value for fmt in ExportFormat)
        super().__init__(f"Unknown export format '{name}'. Choose from: {known}.")
        self.name = name


def resolve_export_options(
    formats: Sequence[str] | None, *, for_print: bool, target_size_mm: float | None
) -> tuple[ExportOptions, list[str]]:
    """Turn what the user asked for into export options, plus warnings to show.

    One function for both front doors, so ``--print`` on the CLI and
    ``for_print`` over HTTP mean exactly the same thing: printable output is STL
    and 3MF laid on the build plate, and an explicit format list *adds* to that
    rather than replacing it. Unknown names raise :class:`UnknownFormatError`.
    """
    chosen: list[ExportFormat] = []
    for name in formats or ():
        try:
            chosen.append(ExportFormat(name.lower()))
        except ValueError:
            raise UnknownFormatError(name) from None
    if for_print:
        chosen.extend(PRINT_FORMATS)
    if not chosen:
        chosen.append(ExportFormat.GLB)
    unique = tuple(dict.fromkeys(chosen))

    printing = any(fmt.is_print_format for fmt in unique)
    warnings: list[str] = []
    if printing and target_size_mm is None:
        warnings.append(
            "No target size given, so the print formats keep the model's own scale, "
            "which is arbitrary. Most slicers will show a few millimetres."
        )
    return (
        ExportOptions(formats=unique, target_size_mm=target_size_mm, lay_on_build_plate=printing),
        warnings,
    )


class ExportedFile(BaseModel):
    format: ExportFormat
    path: Path
    size_bytes: int
    carries_colour: bool
    size_mm: list[float] | None = None


def _prepare(mesh: trimesh.Trimesh, options: ExportOptions) -> trimesh.Trimesh:
    """Apply scale and placement without touching the caller's mesh."""
    prepared = mesh.copy()

    if options.target_size_mm is not None:
        extents = np.asarray(prepared.extents, dtype=float)
        longest = float(extents.max())
        if longest > 0:
            prepared.apply_scale(options.target_size_mm / longest)

    if options.lay_on_build_plate:
        bounds = np.asarray(prepared.bounds, dtype=float)
        centre_xy = (bounds[0][:2] + bounds[1][:2]) / 2.0
        prepared.apply_translation([-centre_xy[0], -centre_xy[1], -bounds[0][2]])

    return prepared


def export_mesh(
    mesh: trimesh.Trimesh, output_dir: Path, stem: str, options: ExportOptions
) -> list[ExportedFile]:
    """Write ``mesh`` in every requested format. Returns what was written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    prepared = _prepare(mesh, options)
    dimensions = [round(float(v), 3) for v in prepared.extents]

    written: list[ExportedFile] = []
    for fmt in options.formats:
        target = output_dir / f"{stem}.{fmt.value}"
        prepared.export(target)
        written.append(
            ExportedFile(
                format=fmt,
                path=target,
                size_bytes=target.stat().st_size,
                carries_colour=fmt.carries_colour and _has_colour(prepared),
                size_mm=dimensions if options.target_size_mm is not None else None,
            )
        )
    return written


def _has_colour(mesh: trimesh.Trimesh) -> bool:
    visual = getattr(mesh, "visual", None)
    if getattr(visual, "kind", None) == "vertex":
        return True
    return getattr(visual, "material", None) is not None
