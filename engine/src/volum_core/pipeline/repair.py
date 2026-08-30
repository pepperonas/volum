"""Make a mesh printable.

A slicer needs a closed solid. A generated mesh is frequently not one, and the
Apple Silicon TRELLIS.2 port says so outright: its decode-time hole filling is
disabled because the CUDA helper segfaults, so *"output meshes may have small
holes"*. For a rendering that is a blemish. For a print it is a refusal.

Two strategies, tried in order, because they trade different things:

**Conservative** — remove degenerate and duplicate faces, fill holes, fix winding.
Cheap, and it preserves the geometry exactly where it was already sound. Measured
on a real generated flamingo: not watertight → watertight in well under a second,
losing two faces and no measurable volume.

**Remesh** — voxelise, fill the interior, and rebuild the surface with marching
cubes. Always produces a closed solid, and unavoidably rounds fine detail to the
voxel pitch. Used only when the conservative pass fails, because paying that cost
when it is not needed would be silently degrading the user's model.

The result is verified with manifold3d rather than trusted: it accepts only truly
manifold input, so a mesh it rejects is one a slicer may reject too.
"""

from __future__ import annotations

import time
from enum import StrEnum

import numpy as np
import trimesh
from pydantic import BaseModel, Field

#: Voxels along the longest axis when remeshing. 256 keeps detail reasonable on
#: a printed object without producing meshes that take minutes to slice.
DEFAULT_VOXEL_RESOLUTION = 256

#: Fewer edges than this is not a rim, it is stray geometry.
_MINIMUM_LOOP_LENGTH = 3


class RepairStrategy(StrEnum):
    NONE = "none"
    CONSERVATIVE = "conservative"
    REMESH = "remesh"


class RepairReport(BaseModel):
    strategy: RepairStrategy
    watertight_before: bool
    watertight_after: bool
    manifold_after: bool
    faces_before: int
    faces_after: int
    volume_before: float | None = None
    volume_after: float | None = None
    duration_seconds: float = 0.0
    #: False once the surface has been rebuilt: the shape is equivalent, the
    #: fine detail is not the model's own any more.
    detail_preserved: bool = True
    notes: list[str] = Field(default_factory=list)


def _volume(mesh: trimesh.Trimesh) -> float | None:
    """Signed volume, or ``None`` when it is meaningless.

    An open surface has no volume; trimesh returns a number anyway, and using it
    would make the before/after comparison nonsense.
    """
    if not mesh.is_watertight:
        return None
    try:
        return round(float(mesh.volume), 8)
    except (ValueError, AttributeError):
        return None


def is_manifold(mesh: trimesh.Trimesh) -> bool:
    """Whether manifold3d accepts this mesh.

    A stricter test than ``is_watertight``: it also rejects self-intersections
    and inconsistent orientation, which a slicer will trip over too.
    """
    try:
        import manifold3d  # noqa: PLC0415 - optional at import, required here

        payload = manifold3d.Mesh(
            vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
            tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
        )
        return not manifold3d.Manifold(payload).is_empty()
    except Exception:
        return False


def _clean(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    cleaned = mesh.copy()
    cleaned.update_faces(cleaned.nondegenerate_faces())
    cleaned.update_faces(cleaned.unique_faces())
    cleaned.remove_unreferenced_vertices()
    cleaned.merge_vertices()
    return cleaned


def boundary_loops(mesh: trimesh.Trimesh) -> list[list[int]]:
    """Closed chains of edges that belong to only one face — the rims of holes."""
    edges = np.asarray(mesh.edges_sorted)
    if edges.size == 0:
        return []
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    border = unique[counts == 1]
    if len(border) == 0:
        return []

    neighbours: dict[int, list[int]] = {}
    for a, b in border.tolist():
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)

    loops: list[list[int]] = []
    seen: set[int] = set()
    for start in neighbours:
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        current = start
        while True:
            nxt = next((n for n in neighbours.get(current, []) if n not in seen), None)
            if nxt is None:
                break
            loop.append(nxt)
            seen.add(nxt)
            current = nxt
        # A chain shorter than a triangle is not a rim, it is stray geometry.
        if len(loop) >= _MINIMUM_LOOP_LENGTH:
            loops.append(loop)
    return loops


def fill_boundary_loops(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int]:
    """Close remaining holes with a fan from each rim's centroid.

    trimesh's own ``fill_holes`` only spans holes it can triangulate directly;
    measured here, six adjacent missing faces already defeat it. Without this
    step such a mesh falls all the way through to a full rebuild and loses
    detail it did not need to lose.

    A centroid fan is what mesh repair tools do. It is exact for a planar rim and
    approximate for a warped one, which is the right trade for closing a printed
    surface — the alternative is a hole.
    """
    loops = boundary_loops(mesh)
    if not loops:
        return mesh, 0

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    new_vertices = [vertices]
    new_faces = [faces]
    index = len(vertices)

    for loop in loops:
        ring = np.asarray(loop, dtype=np.int64)
        centroid = vertices[ring].mean(axis=0)
        new_vertices.append(centroid.reshape(1, 3))
        fan = np.column_stack([np.full(len(ring), index), ring, np.roll(ring, -1)])
        new_faces.append(fan)
        index += 1

    patched = trimesh.Trimesh(
        vertices=np.vstack(new_vertices), faces=np.vstack(new_faces), process=False
    )
    patched.merge_vertices()
    trimesh.repair.fix_normals(patched)
    return patched, len(loops)


def _conservative(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int]:
    repaired = _clean(mesh)
    trimesh.repair.fill_holes(repaired)
    patched = 0
    if not repaired.is_watertight:
        repaired, patched = fill_boundary_loops(repaired)
    trimesh.repair.fix_normals(repaired)
    return repaired, patched


def remesh(mesh: trimesh.Trimesh, resolution: int = DEFAULT_VOXEL_RESOLUTION) -> trimesh.Trimesh:
    """Rebuild the surface as a closed solid via voxels.

    The padding is not cosmetic: without a border of empty voxels the surface is
    cut off flat wherever the object touches the edge of the grid, which turns a
    hole into a different hole.
    """
    import mcubes  # noqa: PLC0415
    from scipy import ndimage  # noqa: PLC0415

    extents = np.asarray(mesh.extents, dtype=float)
    pitch = float(extents.max()) / float(resolution)
    if pitch <= 0:
        raise ValueError("Cannot remesh a mesh with no extent.")

    voxels = mesh.voxelized(pitch=pitch)
    dense = np.asarray(voxels.encoding.dense)
    solid = ndimage.binary_fill_holes(dense)
    padded = np.pad(solid.astype(np.float32), 1)

    vertices, faces = mcubes.marching_cubes(padded, 0.5)
    rebuilt = trimesh.Trimesh(
        vertices=(np.asarray(vertices) - 1.0) * pitch, faces=np.asarray(faces)
    )
    # Marching cubes works in grid space; put the result back where the original
    # object was, so exported dimensions still mean something.
    rebuilt.apply_translation(np.asarray(mesh.bounds[0]) - np.asarray(rebuilt.bounds[0]))
    return rebuilt


def keep_largest_solid(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int]:
    """Drop everything but the biggest connected piece.

    Generated meshes routinely carry loose fragments — measured on real output,
    five pieces for one model and six for another. A slicer treats each as its
    own object, so they arrive on the build plate as debris.

    Off by default even so: some models genuinely have several parts, and
    silently deleting one would be worse than printing it.
    """
    parts = mesh.split(only_watertight=False)
    if len(parts) <= 1:
        return mesh, 0
    largest = max(parts, key=lambda part: float(np.abs(part.volume)) or len(part.faces))
    return largest, len(parts) - 1


def repair_for_printing(
    mesh: trimesh.Trimesh,
    *,
    resolution: int = DEFAULT_VOXEL_RESOLUTION,
    allow_remesh: bool = True,
    drop_loose_parts: bool = False,
) -> tuple[trimesh.Trimesh, RepairReport]:
    """Return a printable mesh and an account of what was done to it."""
    started = time.monotonic()
    extra_notes: list[str] = []
    if drop_loose_parts:
        mesh, dropped = keep_largest_solid(mesh)
        if dropped:
            extra_notes.append(f"Removed {dropped} loose piece(s), keeping the largest.")
    watertight_before = bool(mesh.is_watertight)
    faces_before = len(mesh.faces)
    volume_before = _volume(mesh)

    def finish(
        result: trimesh.Trimesh, strategy: RepairStrategy, notes: list[str], detail: bool
    ) -> tuple[trimesh.Trimesh, RepairReport]:
        return result, RepairReport(
            strategy=strategy,
            watertight_before=watertight_before,
            watertight_after=bool(result.is_watertight),
            manifold_after=is_manifold(result),
            faces_before=faces_before,
            faces_after=len(result.faces),
            volume_before=volume_before,
            volume_after=_volume(result),
            duration_seconds=round(time.monotonic() - started, 3),
            detail_preserved=detail,
            notes=[*extra_notes, *notes],
        )

    if watertight_before and is_manifold(mesh):
        return finish(mesh, RepairStrategy.NONE, ["The mesh was already printable."], True)

    conservative, patched_loops = _conservative(mesh)
    if conservative.is_watertight and is_manifold(conservative):
        delta = len(conservative.faces) - faces_before
        note = f"Closed the surface without rebuilding it ({delta:+d} faces"
        note += f", {patched_loops} opening(s) patched)." if patched_loops else ")."
        return finish(conservative, RepairStrategy.CONSERVATIVE, [note], True)

    if not allow_remesh:
        return finish(
            conservative,
            RepairStrategy.CONSERVATIVE,
            [
                "The surface could not be closed without rebuilding it, and "
                "rebuilding was not permitted."
            ],
            True,
        )

    rebuilt = remesh(conservative, resolution=resolution)
    return finish(
        rebuilt,
        RepairStrategy.REMESH,
        [
            "The surface had openings too large to close directly, so it was "
            f"rebuilt at a {resolution}-voxel resolution. The shape is preserved; "
            "fine detail is rounded to the voxel size."
        ],
        False,
    )
