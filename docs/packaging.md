# Packaging

Thin installer, fat data directory. The bundle carries the window, the shell and a
complete engine; models and their environments are downloaded on request and live in the
data directory (`docs/decision.md` §4, `docs/adr/0003-packaging-runtime.md`).

## What is in the bundle

| Part | Size | Notes |
|---|---|---|
| Relocatable CPython 3.12 | ~45 MB | [python-build-standalone], the artefact `uv python install` places on disk |
| The engine and its dependencies | ~160 MB | scipy 58, lxml 19, numpy 18, networkx 10, the rest smaller |
| `uv` | 34 MB | the Model Manager builds a virtual environment per provider with it |
| Shell, window, icons | ~15 MB | Rust binary plus the built frontend |
| **macOS `.app`** | **239 MB** | |
| **macOS `.dmg`** | **82 MB** | compressed |

Measured on macOS arm64, VOLUM 0.1.0.

No model weights, ever. The smallest provider is 2.5 GB and the primary one 7.3 GB; they
are downloaded when asked for and can be removed again.

## What works without a network

Everything except generating: the doctor, the model catalogue with its full licence chain,
the library, the viewer, exports of anything already made. The first generation needs a
download, which is the same bargain the data directory has always made.

## Building

```bash
python3 scripts/build_runtime.py        # the engine runtime — a few minutes, ~220 MB
cd apps/desktop && pnpm install
pnpm tauri build
```

`build_runtime.py` verifies what it built before it returns: it imports the whole engine
through the bundled interpreter, runs the doctor, and checks that the engine refuses to
start without a session token. That check is not ceremony — it caught a runtime that
installed cleanly and could not load scipy, because stripping had removed `numpy.testing`,
which is public API and not a test directory.

Run `python3 scripts/build_runtime.py --check` to re-verify an existing build, and
`--no-strip` to keep pip, setuptools and the test suites.

### ⚠️ On macOS, build the DMG with `CI=true`

```bash
CI=true pnpm tauri build
```

Without it the DMG step fails with `couldn't unmount … Resource busy`. The cause, named
by `diskutil eject`, is Finder:

```
Unmount was dissented by PID 89637 (…/Finder.app/Contents/MacOS/Finder)
```

`create-dmg` runs an AppleScript that opens the mounted image in a Finder window to
arrange its icons, and never closes it; Finder then refuses to let the volume go, and no
amount of retrying helps. `CI=true` makes Tauri pass `--skip-jenkins`, which skips the
styling step entirely. The DMG is then plain rather than prettily arranged, which is a
fair trade for one that exists. CI sets `CI` itself, so the release workflow is unaffected.

## Platform status

| Platform | Target | Built | Run |
|---|---|---|---|
| macOS arm64 | `.dmg`, `.app` | ✅ verified here | ✅ the packaged app starts its engine from the bundled runtime and generates |
| Windows x64 | NSIS `.exe` | ⚙️ wired in CI, never built | ❌ |
| Linux x64 | `.AppImage`, `.deb` | ⚙️ wired in CI, never built | ❌ |

The macOS line is a measurement. The other two are an arrangement: the release workflow
builds them on their own runners, and nothing about them has been observed working. They
should not be described as supported until a build has run and someone has started the
result.

**macOS is arm64 only, deliberately.** An x86-64 slice could not run the primary provider
(`docs/architecture.md` §10), so a universal binary would ship half an application.

**Model availability is not the same as platform availability.** TripoSR runs on every
platform VOLUM builds for, including CPU. TRELLIS.2 runs on Apple Silicon and on NVIDIA;
its official CUDA pipeline additionally depends on `nvdiffrast`, which is non-commercial
(`docs/licenses.md` §3). The doctor reports what *this* machine can run, and the model
list says so before anything is downloaded.

## Signing

**Nothing is signed.** The binaries a release produces are unsigned, and both desktop
platforms say so loudly:

- macOS refuses to open the application at all until the quarantine attribute is removed:
  `xattr -dr com.apple.quarantine /Applications/VOLUM.app`
- Windows SmartScreen warns before running the installer.

Signing needs certificates this project does not have: an Apple Developer ID plus
notarisation, and an Authenticode certificate for Windows. Tauri reads both from the
environment (`APPLE_CERTIFICATE`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID` and
`APPLE_PASSWORD`; `WINDOWS_CERTIFICATE`), so the release workflow gains signing by gaining
secrets — there is nothing to redesign.

## Releasing

`.github/workflows/release.yml` builds all three on a `v*` tag or on demand, and on a tag
collects the results into a **draft** release. Draft, not published: what reaches people
is a decision a person makes, not something a tag does on their behalf.

## Updates

Not wired. Tauri's updater would ship the whole runtime on every patch — around 200 MB —
because it does no binary diffing. Until that is worth solving, updates are a download
from the releases page. If it becomes worth solving, the runtime moves to a separately
versioned resource so the application and its engine can be updated apart
(`docs/adr/0003-packaging-runtime.md`).

[python-build-standalone]: https://github.com/astral-sh/python-build-standalone
