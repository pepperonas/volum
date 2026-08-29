"""User settings, persisted as one small JSON file.

Deliberately minimal. Settings that can be derived (hardware, model
availability) are not stored, because a stored copy goes stale and then lies.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from .paths import DATA_DIR_ENV, default_data_dir, ensure_data_layout, settings_file


class Settings(BaseModel):
    """Everything the user can configure."""

    data_dir: Path | None = Field(
        default=None,
        description="Override for the data directory. None means the platform default. "
        "Exists so a user with a small system disk can put weights on an external volume.",
    )
    hugging_face_token: str | None = Field(
        default=None,
        description="Used only for gated model downloads that the user starts. "
        "Never sent anywhere else.",
    )
    allow_marginal_models: bool = Field(
        default=False,
        description="Permit models whose estimated peak memory exceeds this machine's. "
        "Off by default: the honest default is to not attempt them.",
    )

    def resolved_data_dir(self) -> Path:
        """The data directory actually in use.

        Precedence is environment, then setting, then platform default. The
        environment wins so tests and CI never touch a real user directory.
        """
        override = os.environ.get(DATA_DIR_ENV)
        if override:
            return Path(override).expanduser()
        if self.data_dir is not None:
            return self.data_dir.expanduser()
        return default_data_dir()


def load_settings(path: Path | None = None) -> Settings:
    """Read settings, falling back to defaults.

    A corrupt settings file must not stop VOLUM from starting — the user would
    have no way to fix it through the application. Defaults win instead.
    """
    target = path or settings_file()
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Settings()
    try:
        return Settings.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Write settings atomically.

    Atomic because a truncated settings file after a crash would silently move
    the user's data directory back to the default, and their models would appear
    to have vanished.
    """
    target = path or settings_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def get_data_dir(settings: Settings | None = None, *, create: bool = True) -> Path:
    """Resolve the data directory, creating its layout by default."""
    resolved = (settings or load_settings()).resolved_data_dir()
    return ensure_data_layout(resolved) if create else resolved
