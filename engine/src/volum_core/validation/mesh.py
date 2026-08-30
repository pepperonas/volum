"""Asset validation. Mandatory, and able to fail a job.

Upstream has a documented failure mode where the decoder silently produces an
**empty mesh** (trellis-mac issue #8, a killed Metal command buffer). A model
that returns nothing while reporting success must fail here rather than be
exported as an asset — that is the whole reason this stage cannot be optional.

The report is data (spec section 21). It is written next to the asset as
``quality_report.json`` and shown in the viewer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import trimesh
from pydantic import BaseModel, Field

#: A mesh below this is not a reconstruction, it is a failure that happened to
#: write a file.
MINIMUM_VERTICES = 8
MINIMUM_FACES = 4

#: A manifold edge is shared by exactly two faces.
_MANIFOLD_EDGE_FACES = 2


class ValidationIssue(BaseModel):
    code: str
    message: str
    #: Fatal issues fail the job. Warnings are recorded and shown.
    fatal: bool = False


class QualityReport(BaseModel):
    valid: bool
    file: str
    file_size_bytes: int

    vertices: int = 0
    triangles: int = 0
    materials: int = 0
    textures: int = 0

    bounding_box_min: list[float] = Field(default_factory=list)
    bounding_box_max: list[float] = Field(default_factory=list)
    dimensions: list[float] = Field(default_factory=list)

    watertight: bool | None = None
    winding_consistent: bool | None = None
    volume: float | None = None
    non_manifold_edges: int | None = None
    degenerate_faces: int = 0
    has_vertex_colors: bool = False
    has_uv: bool = False

    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def fatal_issues(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.fatal]

    def write(self, path: Path) -> Path:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path


def _load(path: Path) -> tuple[trimesh.Trimesh | None, int, int, ValidationIssue | None]:
    """Load an asset, flattening a scene into one mesh for measurement."""
    try:
        loaded: Any = trimesh.load(path, force="scene")
    except Exception as exc:
        return (
            None,
            0,
            0,
            ValidationIssue(
                code="unreadable",
                message=f"The file could not be read as a 3D asset: {type(exc).__name__}.",
                fatal=True,
            ),
        )

    if isinstance(loaded, trimesh.Trimesh):
        mesh: trimesh.Trimesh = loaded
        return mesh, 0, 0, None

    geometries = list(loaded.geometry.values())
    meshes = [g for g in geometries if isinstance(g, trimesh.Trimesh)]
    if not meshes:
        return (
            None,
            0,
            0,
            ValidationIssue(
                code="no_geometry",
                message="The file contains no mesh geometry.",
                fatal=True,
            ),
        )

    materials = 0
    textures = 0
    for mesh in meshes:
        visual = getattr(mesh, "visual", None)
        material = getattr(visual, "material", None)
        if material is not None:
            materials += 1
            if getattr(material, "baseColorTexture", None) is not None:
                textures += 1

    if len(meshes) == 1:
        combined = meshes[0]
    else:
        # concatenate is typed as returning the general Geometry base; for a
        # tuple of Trimesh it always returns a Trimesh.
        combined = cast("trimesh.Trimesh", trimesh.util.concatenate(tuple(meshes)))
    return combined, materials, textures, None


def _count_non_manifold_edges(mesh: trimesh.Trimesh) -> int | None:
    """Edges not shared by exactly two faces.

    Boundary edges (one face) and junction edges (three or more) both count:
    each breaks the manifold property that downstream tools — 3D printing
    slicers above all — assume.
    """
    try:
        edges = np.asarray(mesh.edges_sorted)
        if edges.size == 0:
            return 0
        _, counts = np.unique(edges, axis=0, return_counts=True)
        return int((counts != _MANIFOLD_EDGE_FACES).sum())
    except (AttributeError, ValueError, MemoryError):
        return None


def validate_asset(path: Path) -> QualityReport:
    """Inspect a generated asset and report what is actually in it."""
    if not path.exists():
        return QualityReport(
            valid=False,
            file=str(path),
            file_size_bytes=0,
            issues=[
                ValidationIssue(
                    code="missing", message="The asset file does not exist.", fatal=True
                )
            ],
        )

    size = path.stat().st_size
    report = QualityReport(valid=False, file=str(path), file_size_bytes=size)

    if size == 0:
        report.issues.append(
            ValidationIssue(code="empty_file", message="The asset file is empty.", fatal=True)
        )
        return report

    mesh, materials, textures, issue = _load(path)
    if issue is not None or mesh is None:
        report.issues.append(
            issue
            or ValidationIssue(
                code="unreadable", message="The asset could not be read.", fatal=True
            )
        )
        return report

    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    report.vertices = len(vertices)
    report.triangles = len(faces)
    report.materials = materials
    report.textures = textures

    if report.vertices < MINIMUM_VERTICES or report.triangles < MINIMUM_FACES:
        # The silent-empty-mesh failure. Fatal, deliberately.
        report.issues.append(
            ValidationIssue(
                code="empty_mesh",
                message=(
                    f"The model produced almost no geometry "
                    f"({report.vertices} vertices, {report.triangles} triangles). "
                    "This usually means generation failed part-way."
                ),
                fatal=True,
            )
        )
        return report

    if not np.isfinite(vertices).all():
        report.issues.append(
            ValidationIssue(
                code="non_finite",
                message="The geometry contains invalid coordinates (NaN or infinity).",
                fatal=True,
            )
        )
        return report

    lower = vertices.min(axis=0)
    upper = vertices.max(axis=0)
    report.bounding_box_min = [round(float(v), 6) for v in lower]
    report.bounding_box_max = [round(float(v), 6) for v in upper]
    report.dimensions = [round(float(v), 6) for v in (upper - lower)]

    if float((upper - lower).max()) <= 0.0:
        report.issues.append(
            ValidationIssue(
                code="zero_extent",
                message="The geometry has no size in any direction.",
                fatal=True,
            )
        )
        return report

    report.degenerate_faces = int((~mesh.nondegenerate_faces()).sum())
    report.non_manifold_edges = _count_non_manifold_edges(mesh)
    report.watertight = bool(mesh.is_watertight)
    report.winding_consistent = bool(mesh.is_winding_consistent)
    try:
        report.volume = round(float(mesh.volume), 8)
    except (ValueError, AttributeError):
        report.volume = None

    visual = getattr(mesh, "visual", None)
    report.has_vertex_colors = getattr(visual, "kind", None) == "vertex"
    uv = getattr(visual, "uv", None)
    report.has_uv = uv is not None and len(uv) > 0

    if not report.watertight:
        report.issues.append(
            ValidationIssue(
                code="not_watertight",
                message="The surface has holes. It is usable, but not suitable for "
                "3D printing without repair.",
            )
        )
    if report.volume is not None and report.volume < 0:
        # Only meaningful on a closed surface; an open mesh has no signed volume.
        report.issues.append(
            ValidationIssue(
                code="inverted_normals",
                message="The surface appears to be inside out.",
            )
        )
    if report.degenerate_faces:
        report.issues.append(
            ValidationIssue(
                code="degenerate_faces",
                message=f"{report.degenerate_faces} faces have no area.",
            )
        )
    if not report.materials and not report.has_vertex_colors:
        report.issues.append(
            ValidationIssue(
                code="no_surface_colour",
                message="The asset has no materials and no vertex colours; it will "
                "render untextured.",
            )
        )

    report.valid = not report.fatal_issues
    return report


def load_report(path: Path) -> QualityReport | None:
    try:
        return QualityReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
