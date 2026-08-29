#!/usr/bin/env python3
"""Propagate the single source of truth for the version (spec section 43).

The version is declared in exactly one place — ``engine/src/volum_core/version.py``
— and copied from there into every other file that carries it. Nothing else may
be edited by hand.

Usage::

    python scripts/sync_version.py            # write
    python scripts/sync_version.py --check    # verify only; CI runs this

``--check`` exits non-zero on drift, which is the point: a release where the
app reports one version and the CLI another is a support problem that starts
silently.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "engine" / "src" / "volum_core" / "version.py"

#: JSON files with a top-level "version" key. Missing files are skipped —
#: the desktop app does not exist yet, and this script must work before it does.
JSON_TARGETS = (
    Path("apps/desktop/package.json"),
    Path("apps/desktop/src-tauri/tauri.conf.json"),
)

#: Cargo.toml-style targets: the first `version = "..."` under [package].
CARGO_TARGETS = (Path("apps/desktop/src-tauri/Cargo.toml"),)


def read_version() -> str:
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', VERSION_FILE.read_text(), re.MULTILINE)
    if match is None:
        raise SystemExit(f"No __version__ found in {VERSION_FILE}")
    return match.group(1)


def sync_json(path: Path, version: str, *, check: bool) -> str | None:
    """Return a drift description, or ``None`` when in sync."""
    full = ROOT / path
    if not full.exists():
        return None
    data = json.loads(full.read_text(encoding="utf-8"))
    current = data.get("version")
    if current == version:
        return None
    if check:
        return f"{path}: {current!r} != {version!r}"
    data["version"] = version
    full.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return None


def sync_cargo(path: Path, version: str, *, check: bool) -> str | None:
    full = ROOT / path
    if not full.exists():
        return None
    text = full.read_text(encoding="utf-8")
    pattern = re.compile(r'(?m)^(version\s*=\s*)"([^"]+)"')
    match = pattern.search(text)
    if match is None:
        return f"{path}: no version field found"
    if match.group(2) == version:
        return None
    if check:
        return f"{path}: {match.group(2)!r} != {version!r}"
    full.write_text(pattern.sub(rf'\g<1>"{version}"', text, count=1), encoding="utf-8")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify without writing.")
    args = parser.parse_args()

    version = read_version()
    drift = [
        message
        for path in JSON_TARGETS
        if (message := sync_json(path, version, check=args.check)) is not None
    ] + [
        message
        for path in CARGO_TARGETS
        if (message := sync_cargo(path, version, check=args.check)) is not None
    ]

    if drift:
        print(f"Version drift against {version}:", file=sys.stderr)
        for message in drift:
            print(f"  {message}", file=sys.stderr)
        print("\nRun: python scripts/sync_version.py", file=sys.stderr)
        return 1

    print(f"VOLUM {version}" + ("" if args.check else " - all targets synced"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
