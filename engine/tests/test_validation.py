"""Validation tests.

Meshes are built with trimesh rather than generated, so all of this runs in CI
without a GPU or weights — which is the point: validation is the stage that
stops a broken generation becoming an asset, so it must be the best-tested one.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from volum_core.validation import load_report, validate_asset


def _write(mesh: trimesh.Trimesh, path: Path) -> Path:
    mesh.export(path)
    return path


@pytest.fixture
def good_mesh(tmp_path: Path) -> Path:
    return _write(trimesh.creation.icosphere(subdivisions=2), tmp_path / "good.glb")


def test_a_sound_mesh_validates(good_mesh: Path) -> None:
    report = validate_asset(good_mesh)
    assert report.valid
    assert not report.fatal_issues
    assert report.vertices > 0
    assert report.triangles > 0


def test_a_sphere_is_watertight_and_not_inside_out(good_mesh: Path) -> None:
    report = validate_asset(good_mesh)
    assert report.watertight
    assert report.volume is not None and report.volume > 0
    assert report.non_manifold_edges == 0


def test_dimensions_are_reported(good_mesh: Path) -> None:
    report = validate_asset(good_mesh)
    assert len(report.dimensions) == 3
    assert all(d > 0 for d in report.dimensions)


# --- the failures that matter ---------------------------------------------


def test_a_missing_file_is_fatal(tmp_path: Path) -> None:
    report = validate_asset(tmp_path / "nope.glb")
    assert not report.valid
    assert report.fatal_issues[0].code == "missing"


def test_an_empty_file_is_fatal(tmp_path: Path) -> None:
    target = tmp_path / "empty.glb"
    target.write_bytes(b"")
    report = validate_asset(target)
    assert not report.valid
    assert report.fatal_issues[0].code == "empty_file"


def test_an_unreadable_file_is_fatal(tmp_path: Path) -> None:
    target = tmp_path / "junk.glb"
    target.write_bytes(b"this is not a GLB at all")
    report = validate_asset(target)
    assert not report.valid
    assert report.fatal_issues


def test_an_almost_empty_mesh_is_fatal(tmp_path: Path) -> None:
    """The documented upstream failure: the decoder silently returns nothing
    while reporting success. It must fail the job, not become an asset."""
    mesh = trimesh.Trimesh(
        vertices=np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]]),
        faces=np.array([[0, 1, 2]]),
    )
    report = validate_asset(_write(mesh, tmp_path / "tiny.glb"))
    assert not report.valid
    assert report.fatal_issues[0].code == "empty_mesh"


def test_a_flat_mesh_is_fatal(tmp_path: Path) -> None:
    """Zero extent in every direction is not geometry."""
    vertices = np.zeros((12, 3))
    faces = np.array([[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10, 11]])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    report = validate_asset(_write(mesh, tmp_path / "flat.glb"))
    assert not report.valid
    assert {issue.code for issue in report.fatal_issues} & {"zero_extent", "empty_mesh"}


# --- warnings, which must not fail a job ----------------------------------


def test_an_open_surface_warns_but_stays_valid(tmp_path: Path) -> None:
    """Holes make a mesh unfit for printing, not unfit for use."""
    sphere = trimesh.creation.icosphere(subdivisions=2)
    open_mesh = trimesh.Trimesh(
        vertices=sphere.vertices, faces=sphere.faces[: len(sphere.faces) // 2]
    )
    report = validate_asset(_write(open_mesh, tmp_path / "open.glb"))
    assert report.valid
    assert any(issue.code == "not_watertight" for issue in report.issues)
    assert not report.fatal_issues


def test_an_untextured_mesh_is_flagged_but_valid(good_mesh: Path) -> None:
    report = validate_asset(good_mesh)
    if not report.has_vertex_colors and not report.materials:
        assert any(issue.code == "no_surface_colour" for issue in report.issues)
        assert report.valid


def test_vertex_colours_are_detected(tmp_path: Path) -> None:
    mesh = trimesh.creation.icosphere(subdivisions=2)
    mesh.visual.vertex_colors = np.tile([200, 40, 40, 255], (len(mesh.vertices), 1))
    report = validate_asset(_write(mesh, tmp_path / "coloured.glb"))
    assert report.has_vertex_colors


# --- the report as an artifact --------------------------------------------


def test_the_report_round_trips(good_mesh: Path, tmp_path: Path) -> None:
    """It is written beside the asset and read back by the viewer."""
    report = validate_asset(good_mesh)
    target = report.write(tmp_path / "quality_report.json")
    loaded = load_report(target)
    assert loaded is not None
    assert loaded.vertices == report.vertices


def test_a_corrupt_report_reads_as_none(tmp_path: Path) -> None:
    target = tmp_path / "quality_report.json"
    target.write_text("{ not json", encoding="utf-8")
    assert load_report(target) is None
