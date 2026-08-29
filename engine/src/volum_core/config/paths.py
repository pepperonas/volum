"""Where VOLUM keeps things.

Two rules drive this module:

1. **Platform-native locations**, not a directory dumped in the working
   directory (spec section 27).
2. **The data directory is relocatable.** Not a preference — a functional
   requirement. One provider plus its environment is around 21 GB, and the
   development machine has about the same amount free. A user who cannot move
   the data directory to an external volume cannot use the product.

Which creates a bootstrap problem: the setting that says where the data lives
cannot itself live in the data directory. So the *config* directory is always
the platform default and holds one small file, and the *data* directory is
whatever that file (or the environment) says.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

APP_NAME = "VOLUM"

#: Overrides everything else. Intended for tests, CI and power users.
DATA_DIR_ENV = "VOLUM_DATA_DIR"
CONFIG_DIR_ENV = "VOLUM_CONFIG_DIR"


def default_config_dir() -> Path:
    """Where settings live. Always the platform default — never relocated."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()

    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if system == "Windows":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME.lower()


def default_data_dir() -> Path:
    """Where models, projects, jobs and the cache live by default."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / APP_NAME
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_NAME.lower()


def settings_file() -> Path:
    return default_config_dir() / "settings.json"


#: Subdirectories of the data directory, created on demand.
SUBDIRECTORIES = ("models", "projects", "jobs", "cache", "logs", "exports")


def ensure_data_layout(data_dir: Path) -> Path:
    """Create the data directory and its subdirectories if they are missing."""
    for name in SUBDIRECTORIES:
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    return data_dir


def is_within(child: Path, parent: Path) -> bool:
    """Whether ``child`` resolves to a location inside ``parent``.

    Used to reject path traversal before touching the filesystem (spec section
    50). Both sides are resolved first, so ``..`` segments and symlinks cannot
    smuggle a path out of the data directory.
    """
    try:
        child.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True
