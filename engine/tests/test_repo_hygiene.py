"""Guards on the repository itself.

These exist because of a real incident: the unanchored .gitignore rule ``jobs/``
matched ``engine/src/volum_core/jobs/`` as well as the runtime data directory it
was written for. The package worked perfectly on the machine that wrote it and
was simply absent from the repository for everyone else. It also went unlinted,
because ruff honours .gitignore. CI did catch it — as an obscure "cannot find
implementation" from the type checker, three commits later.

The guards below are on the cause, not the symptom.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from volum_core.config import default_config_dir, settings_file

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "engine" / "src"

#: Where source lives. A .gitignore rule that matches anything in here is the bug.
_SOURCE_ROOTS = ("engine/src", "engine/tests", "scripts", "apps")


def _gitignore_lines() -> list[str]:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _source_directory_names() -> set[str]:
    names: set[str] = set()
    for root in _SOURCE_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        names.update(
            path.name for path in base.rglob("*") if path.is_dir() and path.name != "__pycache__"
        )
    return names


def test_no_unanchored_ignore_rule_matches_a_source_directory() -> None:
    """Checked against the repository as it is, rather than a guessed list.

    ``dist/`` and ``build/`` legitimately need to match at any depth — the
    frontend writes into ``apps/desktop/dist``. What must never happen is an
    unanchored rule whose name collides with a real source directory, which is
    exactly how ``jobs/`` erased a package.
    """
    source_names = _source_directory_names()
    collisions = [
        line
        for line in _gitignore_lines()
        if line.endswith("/") and not line.startswith("/") and line.rstrip("/") in source_names
    ]
    assert not collisions, (
        f"These .gitignore rules match real source directories: {collisions}. "
        "Anchor them with a leading '/' so they apply only at the repository root."
    )


def test_the_runtime_data_rules_are_anchored() -> None:
    """The specific rules that caused the incident, pinned by name."""
    lines = set(_gitignore_lines())
    for name in ("data", "jobs", "cache", "logs", "exports", "projects"):
        assert f"/{name}/" in lines, f"'{name}/' must be anchored as '/{name}/'"
        assert f"{name}/" not in lines, f"'{name}/' is unanchored and can hide source"


@pytest.mark.parametrize(
    "package",
    sorted(
        path.parent.name
        for path in SRC.glob("volum_core/*/__init__.py")
        if path.parent.name != "__pycache__"
    ),
)
def test_every_core_subpackage_is_importable(package: str) -> None:
    """A package present on disk but absent from the install is the failure mode
    the .gitignore bug produced. Importing each by name states it plainly."""
    __import__(f"volum_core.{package}")


def test_every_distributed_package_declares_its_types() -> None:
    """PEP 561: without py.typed the installed package is treated as untyped, so
    a strict type check against the install fails while the source passes."""
    for package in ("volum_core", "volum_cli", "volum_engine"):
        assert (SRC / package / "py.typed").exists(), f"{package} has no py.typed marker"


def test_the_suite_is_fenced_off_from_the_real_user_directories() -> None:
    """Pins the autouse fixture in conftest. Without it a service that saves
    settings writes to the developer's real config directory."""
    assert os.environ.get("VOLUM_CONFIG_DIR"), "conftest must set VOLUM_CONFIG_DIR"
    assert os.environ.get("VOLUM_DATA_DIR"), "conftest must set VOLUM_DATA_DIR"
    assert "Application Support" not in str(default_config_dir())
    assert not str(settings_file()).startswith(str(Path.home() / ".config"))
