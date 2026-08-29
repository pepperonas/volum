"""Config and path tests.

All of these run against tmp_path — no test may touch a real user directory,
which is also why the environment override exists and takes precedence.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path

import pytest

from volum_core.config import (
    SUBDIRECTORIES,
    Settings,
    default_config_dir,
    default_data_dir,
    ensure_data_layout,
    get_data_dir,
    is_within,
    load_settings,
    save_settings,
)
from volum_core.config.paths import DATA_DIR_ENV


def test_default_directories_are_platform_native() -> None:
    """Never a directory dumped in the working directory (spec section 27)."""
    data = default_data_dir()
    assert data.is_absolute()
    system = platform.system()
    if system == "Darwin":
        assert "Library/Application Support" in str(data)
    elif system == "Windows":
        assert "AppData" in str(data)
    else:
        assert ".local/share" in str(data) or "XDG" in str(data) or data.parts[-2:] != ()


def test_config_directory_is_absolute() -> None:
    assert default_config_dir().is_absolute()


def test_layout_creates_every_subdirectory(tmp_path: Path) -> None:
    ensure_data_layout(tmp_path)
    for name in SUBDIRECTORIES:
        assert (tmp_path / name).is_dir()


def test_layout_is_idempotent(tmp_path: Path) -> None:
    ensure_data_layout(tmp_path)
    ensure_data_layout(tmp_path)
    assert (tmp_path / "models").is_dir()


def test_environment_override_beats_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI and tests must never be able to reach a real user directory."""
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "from-env"))
    settings = Settings(data_dir=tmp_path / "from-settings")
    assert settings.resolved_data_dir() == tmp_path / "from-env"


def test_setting_beats_the_platform_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The relocatable data directory is a functional requirement, not a nicety:
    one provider is around 21 GB."""
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    settings = Settings(data_dir=tmp_path / "external-volume")
    assert settings.resolved_data_dir() == tmp_path / "external-volume"


def test_platform_default_is_used_when_nothing_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert Settings().resolved_data_dir() == default_data_dir()


def test_settings_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    save_settings(Settings(data_dir=tmp_path / "d", allow_marginal_models=True), target)
    loaded = load_settings(target)
    assert loaded.data_dir == tmp_path / "d"
    assert loaded.allow_marginal_models


def test_missing_settings_file_yields_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "absent.json") == Settings()


def test_corrupt_settings_file_yields_defaults_rather_than_crashing(tmp_path: Path) -> None:
    """A corrupt file must not stop VOLUM from starting — the user would have no
    way to fix it from inside the application."""
    target = tmp_path / "settings.json"
    target.write_text("{not json at all", encoding="utf-8")
    assert load_settings(target) == Settings()


def test_settings_with_wrong_types_yield_defaults(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"allow_marginal_models": "yes please"}), encoding="utf-8")
    assert load_settings(target) == Settings()


def test_saving_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    """The write is atomic; a truncated settings file would silently relocate the
    user's data directory and their models would appear to have vanished."""
    target = tmp_path / "settings.json"
    save_settings(Settings(), target)
    assert not list(tmp_path.glob("*.tmp"))
    assert target.exists()


def test_marginal_models_are_not_allowed_by_default() -> None:
    """The honest default is to not attempt a model that does not fit."""
    assert Settings().allow_marginal_models is False


def test_get_data_dir_creates_the_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "data"))
    resolved = get_data_dir()
    assert (resolved / "models").is_dir()


def test_get_data_dir_can_skip_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "untouched"))
    resolved = get_data_dir(create=False)
    assert not resolved.exists()


# --- path traversal -------------------------------------------------------


def test_is_within_accepts_a_child(tmp_path: Path) -> None:
    child = tmp_path / "models" / "trellis2"
    child.mkdir(parents=True)
    assert is_within(child, tmp_path)


def test_is_within_rejects_a_sibling(tmp_path: Path) -> None:
    parent = tmp_path / "data"
    parent.mkdir()
    assert not is_within(tmp_path / "elsewhere", parent)


def test_is_within_rejects_dot_dot_escape(tmp_path: Path) -> None:
    """The actual attack: a model id or file name containing '..'."""
    parent = tmp_path / "data"
    parent.mkdir()
    assert not is_within(parent / ".." / ".." / "etc" / "passwd", parent)


def test_is_within_rejects_a_symlink_escape(tmp_path: Path) -> None:
    """Resolving both sides is what makes this hold — a symlink pointing out of
    the data directory must not be treated as inside it."""
    parent = tmp_path / "data"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = parent / "link"
    link.symlink_to(outside)
    assert not is_within(link, parent)
