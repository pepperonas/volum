"""Export tests.

Scale is the part worth guarding. For a render the unit does not matter; for a
print it decides whether the object is 6 cm or 6 mm across, and the failure is
silent — the file is valid either way.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh

from volum_core.export import (
    PRINT_FORMATS,
    ExportFormat,
    ExportOptions,
    UnknownFormatError,
    export_mesh,
    resolve_export_options,
)


@pytest.fixture
def sphere() -> trimesh.Trimesh:
    return trimesh.creation.icosphere(subdivisions=2)


def test_glb_is_the_default(tmp_path: Path, sphere: trimesh.Trimesh) -> None:
    written = export_mesh(sphere, tmp_path, "m", ExportOptions())
    assert [f.format for f in written] == [ExportFormat.GLB]


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_every_declared_format_can_be_written(
    tmp_path: Path, sphere: trimesh.Trimesh, fmt: ExportFormat
) -> None:
    """A format in the enum that cannot be written is a promise the UI would make
    and the export would break."""
    written = export_mesh(sphere, tmp_path, "m", ExportOptions(formats=(fmt,)))
    assert written[0].path.exists()
    assert written[0].size_bytes > 0


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_every_written_format_can_be_read_back(
    tmp_path: Path, sphere: trimesh.Trimesh, fmt: ExportFormat
) -> None:
    written = export_mesh(sphere, tmp_path, "m", ExportOptions(formats=(fmt,)))
    back = trimesh.load(written[0].path, force="mesh")
    assert len(back.vertices) > 0
    assert len(back.faces) > 0


def test_print_formats_are_stl_and_3mf() -> None:
    assert set(PRINT_FORMATS) == {ExportFormat.STL, ExportFormat.THREEMF}
    assert all(fmt.is_print_format for fmt in PRINT_FORMATS)
    assert not ExportFormat.GLB.is_print_format


def test_3mf_is_a_zip_declaring_millimetres(tmp_path: Path, sphere: trimesh.Trimesh) -> None:
    """3MF is the only print format that carries units at all; STL is unitless
    by convention and every slicer simply assumes millimetres."""
    written = export_mesh(sphere, tmp_path, "m", ExportOptions(formats=(ExportFormat.THREEMF,)))
    with zipfile.ZipFile(written[0].path) as archive:
        model = archive.read("3D/3dmodel.model").decode("utf-8")
    assert 'unit="millimeter"' in model


def test_3mf_is_not_claimed_to_carry_colour(tmp_path: Path) -> None:
    """Measured, not assumed: the writer in use emits geometry only. Advertising
    colour and shipping a grey file would be worse than not offering it."""
    mesh = trimesh.creation.icosphere(subdivisions=2)
    mesh.visual.vertex_colors = np.tile([200, 40, 40, 255], (len(mesh.vertices), 1))
    written = export_mesh(mesh, tmp_path, "m", ExportOptions(formats=(ExportFormat.THREEMF,)))
    assert written[0].carries_colour is False
    assert not ExportFormat.THREEMF.carries_colour


def test_colour_survives_into_formats_that_declare_it(tmp_path: Path) -> None:
    mesh = trimesh.creation.icosphere(subdivisions=2)
    mesh.visual.vertex_colors = np.tile([200, 40, 40, 255], (len(mesh.vertices), 1))
    written = export_mesh(mesh, tmp_path, "m", ExportOptions(formats=(ExportFormat.PLY,)))
    assert written[0].carries_colour
    assert trimesh.load(written[0].path, force="mesh").visual.kind == "vertex"


# --- scale ----------------------------------------------------------------


def test_target_size_sets_the_longest_side(tmp_path: Path, sphere: trimesh.Trimesh) -> None:
    written = export_mesh(
        sphere, tmp_path, "m", ExportOptions(formats=(ExportFormat.STL,), target_size_mm=60.0)
    )
    back = trimesh.load(written[0].path, force="mesh")
    assert float(np.max(back.extents)) == pytest.approx(60.0, rel=1e-4)


def test_proportions_are_preserved_when_scaling(tmp_path: Path) -> None:
    """Uniform scale only. A slicer showing a squashed model would be VOLUM's fault."""
    box = trimesh.creation.box(extents=(1.0, 2.0, 4.0))
    written = export_mesh(
        box, tmp_path, "m", ExportOptions(formats=(ExportFormat.STL,), target_size_mm=40.0)
    )
    extents = np.asarray(trimesh.load(written[0].path, force="mesh").extents)
    assert extents / extents.max() == pytest.approx(np.array([0.25, 0.5, 1.0]), rel=1e-4)


def test_without_a_target_size_the_model_scale_is_kept(
    tmp_path: Path, sphere: trimesh.Trimesh
) -> None:
    written = export_mesh(sphere, tmp_path, "m", ExportOptions(formats=(ExportFormat.STL,)))
    back = trimesh.load(written[0].path, force="mesh")
    assert float(np.max(back.extents)) == pytest.approx(float(np.max(sphere.extents)), rel=1e-4)
    assert written[0].size_mm is None


def test_laying_on_the_build_plate_puts_the_base_at_zero(tmp_path: Path) -> None:
    box = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
    box.apply_translation([5.0, -3.0, 7.0])
    written = export_mesh(
        box,
        tmp_path,
        "m",
        ExportOptions(formats=(ExportFormat.STL,), lay_on_build_plate=True),
    )
    bounds = np.asarray(trimesh.load(written[0].path, force="mesh").bounds)
    assert bounds[0][2] == pytest.approx(0.0, abs=1e-5)
    assert (bounds[0][:2] + bounds[1][:2]) / 2 == pytest.approx(np.zeros(2), abs=1e-5)


def test_the_caller_s_mesh_is_not_modified(tmp_path: Path, sphere: trimesh.Trimesh) -> None:
    """Export prepares a copy; the pipeline reuses the mesh afterwards."""
    before = np.asarray(sphere.extents).copy()
    export_mesh(
        sphere,
        tmp_path,
        "m",
        ExportOptions(formats=(ExportFormat.STL,), target_size_mm=99.0, lay_on_build_plate=True),
    )
    assert np.asarray(sphere.extents) == pytest.approx(before)


def test_several_formats_in_one_pass_share_the_same_geometry(
    tmp_path: Path, sphere: trimesh.Trimesh
) -> None:
    written = export_mesh(
        sphere,
        tmp_path,
        "m",
        ExportOptions(formats=PRINT_FORMATS, target_size_mm=50.0),
    )
    assert len(written) == 2
    sizes = [np.asarray(trimesh.load(f.path, force="mesh").extents) for f in written]
    assert sizes[0] == pytest.approx(sizes[1], rel=1e-4)


# --- resolving what the user asked for ---------------------------------------
#
# Shared by the CLI flags and the HTTP request body, so the two front doors
# cannot drift on what "--print" or "for_print" means.


def test_no_formats_means_glb() -> None:
    options, warnings = resolve_export_options(None, for_print=False, target_size_mm=None)
    assert options.formats == (ExportFormat.GLB,)
    assert warnings == []


def test_print_alone_means_stl_and_3mf_laid_on_the_plate() -> None:
    options, _ = resolve_export_options(None, for_print=True, target_size_mm=60.0)
    assert options.formats == PRINT_FORMATS
    assert options.lay_on_build_plate is True
    assert options.target_size_mm == 60.0


def test_print_adds_to_explicit_formats_rather_than_replacing_them() -> None:
    options, _ = resolve_export_options(["glb"], for_print=True, target_size_mm=60.0)
    assert options.formats == (ExportFormat.GLB, *PRINT_FORMATS)


def test_formats_are_deduplicated_and_case_insensitive() -> None:
    options, _ = resolve_export_options(["STL", "stl", "3MF"], for_print=True, target_size_mm=1)
    assert options.formats == (ExportFormat.STL, ExportFormat.THREEMF)


def test_a_print_format_without_a_size_warns_out_loud() -> None:
    """A slicer showing a few millimetres looks like a printer bug; say it first."""
    _, warnings = resolve_export_options(["stl"], for_print=False, target_size_mm=None)
    assert len(warnings) == 1
    assert "size" in warnings[0].lower()


def test_an_unknown_format_names_the_known_ones() -> None:
    with pytest.raises(UnknownFormatError) as excinfo:
        resolve_export_options(["fbx"], for_print=False, target_size_mm=None)
    assert "fbx" in str(excinfo.value)
    assert "stl" in str(excinfo.value)
