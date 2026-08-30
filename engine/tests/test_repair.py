"""Repair tests.

The stage exists because generated meshes are frequently not closed solids, and
the Apple Silicon TRELLIS.2 port says so of its own output. Everything here runs
on constructed geometry, so it holds in CI without a model.
"""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from volum_core.pipeline.repair import (
    RepairStrategy,
    _clean,
    boundary_loops,
    fill_boundary_loops,
    is_manifold,
    keep_largest_solid,
    repair_for_printing,
)


@pytest.fixture
def sphere() -> trimesh.Trimesh:
    return trimesh.creation.icosphere(subdivisions=3)


def _with_hole(mesh: trimesh.Trimesh, faces_removed: int) -> trimesh.Trimesh:
    return trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces[:-faces_removed])


# --- the strategy ladder --------------------------------------------------


def test_a_sound_mesh_is_left_alone(sphere: trimesh.Trimesh) -> None:
    """Rebuilding a mesh that did not need it would quietly cost the user detail."""
    repaired, report = repair_for_printing(sphere)
    assert report.strategy is RepairStrategy.NONE
    assert report.detail_preserved
    assert len(repaired.faces) == len(sphere.faces)


@pytest.mark.parametrize("faces_removed", [1, 6, 40])
def test_holes_are_closed_without_rebuilding(sphere: trimesh.Trimesh, faces_removed: int) -> None:
    """The sizes here are deliberate. trimesh's own fill_holes already fails at
    six adjacent faces — without the boundary-loop stage all three of these fell
    through to a full rebuild and lost detail they did not need to lose."""
    _, report = repair_for_printing(_with_hole(sphere, faces_removed))
    assert report.strategy is RepairStrategy.CONSERVATIVE
    assert report.watertight_after
    assert report.detail_preserved


def test_a_ragged_opening_falls_back_to_remeshing(sphere: trimesh.Trimesh) -> None:
    """Some damage cannot be patched: a rim that touches itself produces
    non-manifold geometry when fanned, so the rebuild is the honest answer."""
    ragged = _with_hole(sphere, 200)
    repaired, report = repair_for_printing(ragged, resolution=96)
    assert report.strategy is RepairStrategy.REMESH
    assert report.watertight_after
    assert not report.detail_preserved
    assert repaired.is_watertight


def test_remeshing_can_be_refused(sphere: trimesh.Trimesh) -> None:
    """Some callers would rather have an open mesh than a rounded one."""
    _, report = repair_for_printing(_with_hole(sphere, 200), allow_remesh=False)
    assert report.strategy is RepairStrategy.CONSERVATIVE
    assert not report.watertight_after


# --- what the report has to be honest about -------------------------------


def test_the_report_says_when_detail_was_lost(sphere: trimesh.Trimesh) -> None:
    _, report = repair_for_printing(_with_hole(sphere, 200), resolution=96)
    assert not report.detail_preserved
    assert any("rounded" in note for note in report.notes)


def test_volume_is_absent_while_the_mesh_is_open(sphere: trimesh.Trimesh) -> None:
    """An open surface has no volume; reporting one would make the before/after
    comparison meaningless."""
    _, report = repair_for_printing(_with_hole(sphere, 6))
    assert report.volume_before is None
    assert report.volume_after is not None


def test_repair_preserves_position(sphere: trimesh.Trimesh) -> None:
    """A remesh that moved the object would silently change its exported size."""
    moved = _with_hole(sphere, 200)
    moved.apply_translation([3.0, -2.0, 1.0])
    repaired, _ = repair_for_printing(moved, resolution=96)
    assert np.asarray(repaired.bounds[0]) == pytest.approx(np.asarray(moved.bounds[0]), abs=0.05)


# --- manifold check -------------------------------------------------------


def test_manifold_accepts_a_closed_mesh(sphere: trimesh.Trimesh) -> None:
    assert is_manifold(sphere)


def test_manifold_rejects_an_open_mesh(sphere: trimesh.Trimesh) -> None:
    """This is why manifold3d is a checker here and not the repair tool: it
    refuses open input rather than healing it."""
    assert not is_manifold(_with_hole(sphere, 40))


def test_manifold_check_never_raises() -> None:
    assert is_manifold(trimesh.Trimesh(vertices=np.zeros((3, 3)), faces=np.array([[0, 1, 2]]))) in (
        True,
        False,
    )


# --- loose parts ----------------------------------------------------------


def _two_spheres() -> trimesh.Trimesh:
    big = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
    speck = trimesh.creation.icosphere(subdivisions=1, radius=0.05)
    speck.apply_translation([5.0, 0.0, 0.0])
    return trimesh.util.concatenate([big, speck])


def test_keeping_the_largest_part_drops_the_debris() -> None:
    kept, dropped = keep_largest_solid(_two_spheres())
    assert dropped == 1
    assert len(kept.split(only_watertight=False)) == 1
    assert float(np.max(kept.extents)) == pytest.approx(2.0, rel=0.05)


def test_a_single_part_is_returned_untouched() -> None:
    sphere = trimesh.creation.icosphere(subdivisions=2)
    kept, dropped = keep_largest_solid(sphere)
    assert dropped == 0
    assert len(kept.faces) == len(sphere.faces)


def test_loose_parts_are_kept_unless_asked(sphere: trimesh.Trimesh) -> None:
    """Deciding for the user would be wrong: some models genuinely have parts."""
    _, report = repair_for_printing(_two_spheres())
    assert not any("loose" in note for note in report.notes)


def test_dropping_loose_parts_is_recorded(sphere: trimesh.Trimesh) -> None:
    _, report = repair_for_printing(_two_spheres(), drop_loose_parts=True)
    assert any("loose" in note for note in report.notes)


# --- boundary-loop patching -----------------------------------------------


def test_boundary_loops_are_found_on_an_open_mesh(sphere: trimesh.Trimesh) -> None:
    assert boundary_loops(_with_hole(sphere, 6))
    assert boundary_loops(sphere) == []


def test_patching_closes_what_fill_holes_cannot(sphere: trimesh.Trimesh) -> None:
    """The measurement that justified this stage existing at all."""
    damaged = _clean(_with_hole(sphere, 6))
    trimesh.repair.fill_holes(damaged)
    assert not damaged.is_watertight, "trimesh managed it after all; this test is obsolete"

    patched, loops = fill_boundary_loops(damaged)
    assert loops >= 1
    assert patched.is_watertight
