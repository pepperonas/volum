#!/usr/bin/env bash
#
# install-macos.sh — build VOLUM and install it into /Applications.
#
# For developing against the packaged application rather than `tauri dev`:
# it builds what is stale, checks the signature really seals the bundle,
# installs, clears the download quarantine and starts it.
#
#   bash scripts/install-macos.sh
#   bash scripts/install-macos.sh --data-dir /Volumes/Fast/volum   # models elsewhere
#   bash scripts/install-macos.sh --no-build                       # install what is built
#   bash scripts/install-macos.sh --no-launch
#
# WHY THE SIGNATURE CHECK IS NOT CEREMONY
#   A plain `tauri build` without a signing identity leaves the bundle
#   `adhoc,linker-signed` — a linker-only signature with no
#   `_CodeSignature` directory at all, so nothing in `Contents/Resources`
#   is sealed. Downloaded and quarantined, macOS then reports that as
#   "is damaged and can't be opened", and right-click → Open does *not*
#   clear that particular wording. It matters more here than in most
#   applications: this bundle carries a 223 MB Python tree with 143
#   shared libraries and two Mach-O executables in Resources.
#
#   `bundle.macOS.signingIdentity: "-"` in tauri.conf.json makes Tauri
#   seal it properly. This script verifies that rather than trusting it,
#   because the failure is silent until someone downloads a release.
#
# WHAT THIS SCRIPT DELIBERATELY DOES NOT DO
#   Inspector Rust's equivalent creates a stable self-signed certificate,
#   because macOS keys TCC grants (Accessibility, Screen Recording) to a
#   signature that an ad-hoc rebuild changes every time. VOLUM asks for
#   no TCC permission at all — no camera, no microphone, no screen
#   recording, no accessibility — so there is no grant to preserve and
#   nothing that machinery would buy.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP="$ROOT/apps/desktop"
APP_NAME="VOLUM.app"
BUILT="$DESKTOP/src-tauri/target/release/bundle/macos/$APP_NAME"
INSTALLED="/Applications/$APP_NAME"
RUNTIME="$DESKTOP/src-tauri/runtime"

DO_BUILD=1
DO_LAUNCH=1
DATA_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) DO_BUILD=0 ;;
    --no-launch) DO_LAUNCH=0 ;;
    --data-dir) DATA_DIR="${2:?--data-dir needs a path}"; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "install-macos.sh is for macOS; see docs/packaging.md for the others." >&2
  exit 1
fi

step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

if [ "$DO_BUILD" -eq 1 ]; then
  if [ ! -d "$RUNTIME" ]; then
    step "Building the engine runtime (a few minutes, ~220 MB)"
    python3 "$ROOT/scripts/build_runtime.py"
  else
    step "Checking the engine runtime"
    python3 "$ROOT/scripts/build_runtime.py" --check
  fi

  step "Building the application"
  # CI=true makes Tauri pass --skip-jenkins to create-dmg. Not needed for
  # `--bundles app`, but harmless and keeps one habit for both paths.
  (cd "$DESKTOP" && CI=true pnpm tauri build --bundles app)
fi

[ -d "$BUILT" ] || { echo "nothing built at $BUILT" >&2; exit 1; }

step "Checking the signature seals the bundle"
if ! codesign --verify --deep --strict "$BUILT" 2>/tmp/volum-codesign.$$; then
  echo "The bundle is not properly sealed:" >&2
  cat /tmp/volum-codesign.$$ >&2
  echo >&2
  echo "Check that tauri.conf.json still sets bundle.macOS.signingIdentity to \"-\"." >&2
  rm -f /tmp/volum-codesign.$$
  exit 1
fi
rm -f /tmp/volum-codesign.$$
codesign -dv "$BUILT" 2>&1 | grep -E 'flags|Signature' || true

step "Installing into /Applications"
# Replaced wholesale: a merge would leave files from the previous build
# inside a bundle whose signature no longer covers them.
rm -rf "$INSTALLED"
# ditto, not cp -R: it preserves the metadata an application bundle carries,
# and it is what Apple's own tooling uses to move one.
ditto "$BUILT" "$INSTALLED"
# Only matters for a downloaded build, and costs nothing here.
xattr -dr com.apple.quarantine "$INSTALLED" 2>/dev/null || true

if [ -n "$DATA_DIR" ]; then
  step "Pointing it at $DATA_DIR"
  CONFIG="$HOME/Library/Application Support/VOLUM"
  mkdir -p "$CONFIG"
  python3 - "$CONFIG/settings.json" "$DATA_DIR" <<'PY'
import json, sys
from pathlib import Path

path, data_dir = Path(sys.argv[1]), sys.argv[2]
# Merged, not overwritten: the file may already hold a Hugging Face token,
# and an install is no reason to lose it.
try:
    settings = json.loads(path.read_text())
except (OSError, ValueError):
    settings = {}
settings["data_dir"] = data_dir
path.write_text(json.dumps(settings, indent=2) + "\n")
print(f"  {path}")
PY
fi

step "Checking the installed copy is still sealed"
# The built bundle verifying says nothing about the installed one: copying can
# lose metadata, and a first run that writes into the bundle breaks the seal
# afterwards. This is the check that matters to whoever opens the application.
if ! codesign --verify --deep --strict "$INSTALLED" 2>/tmp/volum-installed.$$; then
  echo "The installed bundle is not sealed:" >&2
  head -5 /tmp/volum-installed.$$ >&2
  rm -f /tmp/volum-installed.$$
  exit 1
fi
rm -f /tmp/volum-installed.$$
echo "  sealed"

step "Installed"
du -sh "$INSTALLED" | sed 's/^/  /'
echo "  $INSTALLED"

if [ "$DO_LAUNCH" -eq 1 ]; then
  step "Starting it"
  open "$INSTALLED"
fi
