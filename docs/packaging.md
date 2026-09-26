# Packaging

Thin installer, fat data directory. The bundle carries the window, the shell and a
complete engine; models and their environments are downloaded on request and live in the
data directory (`docs/decision.md` §4, `docs/adr/0003-packaging-runtime.md`).

## What is in the bundle

| Part | Size | Notes |
|---|---|---|
| Relocatable CPython 3.12 | ~45 MB | [python-build-standalone], the artefact `uv python install` places on disk |
| The engine and its dependencies | ~160 MB | scipy 58, lxml 19, numpy 18, networkx 10, the rest smaller |
| Precompiled bytecode | ~30 MB | hash-based, so the bundle never rewrites it — see below |
| `uv` | 34 MB | the Model Manager builds a virtual environment per provider with it |
| Shell, window, icons | ~15 MB | Rust binary plus the built frontend |
| **macOS `.app`** | **274 MB** | |
| **macOS `.dmg`** | **~90 MB** | compressed |

Measured on macOS arm64, VOLUM 0.1.0. The Windows installer is 64 MB and the Linux
AppImage plus `.deb` together 343 MB (both as CI uploads them, zipped).

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
through the bundled interpreter, runs the doctor, checks that the engine refuses to start
without a session token, and confirms that doing all that left the tree byte-for-byte as
it found it. None of that is ceremony — it caught a runtime that installed cleanly and
could not load scipy, because stripping had removed `numpy.testing`, which is public API
and not a test directory.

Run `python3 scripts/build_runtime.py --check` to re-verify an existing build, and
`--no-strip` to keep pip, setuptools and the test suites.

### On macOS: install it

```bash
bash scripts/install-macos.sh                                  # build, sign, install, launch
bash scripts/install-macos.sh --data-dir /Volumes/Fast/volum   # models elsewhere
```

It builds what is stale, checks the signature really seals the bundle, installs into
`/Applications` with `ditto`, clears the download quarantine, checks the *installed* copy
is still sealed, and starts it.

Unlike its counterpart in Inspector Rust, it creates no signing certificate. That machinery
exists there because macOS keys TCC grants — Accessibility, Screen Recording — to a
signature that an ad-hoc rebuild changes every time. VOLUM asks for no TCC permission at
all, so there is no grant to preserve.

### ⚠️ A sealed bundle must not be written to — including by itself

The first run of an installed build broke its own code signature. `codesign --verify`
produced **1,280 complaints**: 60 files added, 1,219 modified, every one of them a `.pyc`.

Two things compounded. A bundle is *sealed* — every file's hash is recorded at signing
time — so the interpreter writing bytecode into `Contents/Resources` invalidates it, and
macOS reports the application as damaged from then on. And ordinary `.pyc` files are
validated against the source file's **modification time**, which copying a bundle rarely
preserves, so every precompiled file looked stale on arrival and Python rewrote all of
them.

Three changes, each addressing one part:

- The runtime is precompiled with **hash-based invalidation** (PEP 552,
  `--invalidation-mode unchecked-hash`), which validates against the source's *contents*
  and therefore survives any copy. Costs ~30 MB.
- The shell starts the bundled engine with **`PYTHONDONTWRITEBYTECODE=1`**. The bundle
  ships compiled, so nothing is lost.
- Provider workers explicitly **do not** inherit that: their environments live in the data
  directory, nothing seals them, and without a cache every job recompiles the whole of
  torch (`volum_core.providers.worker_provider._NOT_INHERITED`).

`install-macos.sh` verifies the installed copy, not just the built one, because that is
the distinction this defect lived in.

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
| macOS arm64 | `.dmg`, `.app` | ✅ 4 min | ✅ installed in `/Applications`, starts its engine from the bundle, generates |
| Windows x64 | NSIS `.exe` | ✅ 9 min | ❌ never started |
| Linux x64 | `.AppImage`, `.deb` | ✅ 6 min | ❌ never started |

All three build (run `36258549203`). Only the macOS line has been *started*: the installer
exists for the other two and nobody has opened it. Until someone has, they are built
artefacts, not supported platforms.

**macOS is arm64 only, deliberately.** An x86-64 slice could not run the primary provider
(`docs/architecture.md` §10), so a universal binary would ship half an application.

**Model availability is not the same as platform availability.** TripoSR runs on every
platform VOLUM builds for, including CPU. TRELLIS.2 runs on Apple Silicon and on NVIDIA;
its official CUDA pipeline additionally depends on `nvdiffrast`, which is non-commercial
(`docs/licenses.md` §3). The doctor reports what *this* machine can run, and the model
list says so before anything is downloaded.

## Signing

The macOS bundle **is** signed, ad-hoc: `bundle.macOS.signingIdentity: "-"` in
`tauri.conf.json`. That is not a formality. Without it Tauri leaves the bundle
`adhoc,linker-signed` — a linker-only signature with no `_CodeSignature` directory at all,
so nothing in `Contents/Resources` is sealed. It matters more here than in most
applications, because that directory holds 143 shared libraries and two Mach-O
executables. Measured: `codesign --verify --deep --strict` fails on such a build and
passes on a properly sealed one.

**It is not notarised**, and that is what Gatekeeper rejects it for. `spctl --assess
--raw` reports `assessment:remote: true` with a false verdict — the remote check, not the
signature. Notarisation needs a paid Apple Developer ID, and Windows SmartScreen warns for
the same reason: no Authenticode certificate.

What someone downloading a release has to do:

```bash
xattr -dr com.apple.quarantine /Applications/VOLUM.app
```

⚠️ For an **ad-hoc**-signed build, right-click → Open is not reliably an alternative.
macOS reserves the "unidentified developer, open anyway" path for builds signed with a
real Developer ID; an ad-hoc one can be reported as *"is damaged and can't be opened"*
instead, and that wording has no Open-anyway button. This is documented field experience
from the sibling project Inspector Rust; VOLUM's own downloaded-and-double-clicked path
has not been observed, only its signature state measured.

Tauri reads signing credentials from the environment (`APPLE_CERTIFICATE`,
`APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID`;
`WINDOWS_CERTIFICATE`), so the release workflow gains signing by gaining secrets — there is
nothing to redesign.

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
