# VOLUM

**Local-first, cross-platform image-to-3D desktop application.**

Turn one or more photographs into a usable, textured 3D asset — entirely on your own
machine. No cloud, no account, no telemetry, no remote inference.

> **Status: early, but it runs end to end.**
>
> The desktop application starts, spawns its engine, generates a model from a photograph
> and shows it in a 3D viewer. Also working: hardware detection and `volum doctor`, the
> model manager with the full licence chain on screen, printable output (repaired to a
> closed solid, exported as STL and 3MF at real millimetres), and the CLI. Measured on an
> M1 Pro (16 GB) over MPS: **50 s, 41,864 vertices, watertight** with TripoSR.
>
> The macOS application is packaged and runs from `/Applications`. The Windows and Linux
> installers build in CI and have not been started by anyone
> ([`docs/packaging.md`](docs/packaging.md) keeps that distinction). Not done:
> notarisation, updates, multi-image, and TRELLIS.2 — integrated and licence-checked, but
> not yet run here. See [`docs/decision.md`](docs/decision.md).

## What VOLUM is meant to be

- **Local-first.** Images, intermediate results, inference and generated assets stay on
  your machine. Network access is used only to download model weights you explicitly ask
  for, and models work offline afterwards.
- **A platform, not a demo.** Models sit behind an `ImageTo3DProvider` interface. The rest
  of the application does not depend on any particular model.
- **Honest about hardware.** VOLUM detects what your machine can actually run and says so,
  instead of crashing or silently falling back to something unusable.
- **Honest about licences.** Every model's licence — including the restrictive
  dependencies inside its pipeline — is documented in
  [`docs/licenses.md`](docs/licenses.md) and shown before you download anything.

## Planned platforms

| Platform | Runtime | Status |
|---|---|---|
| macOS Apple Silicon | PyTorch MPS + Metal kernels | Primary target |
| Windows (NVIDIA) | CUDA | Supported |
| Linux (NVIDIA) | CUDA | Supported |

Apple Silicon has priority during development. No architectural decision may exclude
Windows or Linux.

## Stack

Tauri 2 · React + TypeScript + Vite · Tailwind · Three.js / React Three Fiber ·
Python engine (`uv`) · pytest / Vitest · GitHub Actions ·
Semantic Versioning + Conventional Commits. Exports: **GLB** for rendering, **STL** and
**3MF** for printing.

## Try it

### The application

```bash
cd apps/desktop
pnpm install
pnpm tauri dev
```

On macOS, `bash scripts/install-macos.sh` builds it and installs it into `/Applications`.
Releases are ad-hoc signed but **not notarised**, so a downloaded build needs its
quarantine attribute cleared once:
`xattr -dr com.apple.quarantine /Applications/VOLUM.app`. Details and the other platforms:
[`docs/packaging.md`](docs/packaging.md).

### Or from the command line

```bash
cd engine
uv sync --extra dev
uv run volum doctor                  # what your machine can run
uv run volum models install triposr  # ~2.5 GB, downloaded on request only
uv run volum generate photo.png
```

For a 3D printer, ask for printable output instead. VOLUM then repairs the surface to a
closed solid, refuses the job if it is not printable, and writes STL and 3MF at a real
size:

```bash
uv run volum generate photo.png --print --size-mm 60 --single-part
```

Images with a transparent background work best — VOLUM does not yet separate the subject
for you, and it says so rather than pretending otherwise.

### The engine

The same pipeline is served over HTTP for the desktop app. It binds `127.0.0.1` on a port
the OS chooses, announces that port on stdout, and answers nothing without the session
token it is given:

```bash
export VOLUM_ENGINE_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
uv sync --extra dev --extra engine
uv run volum-engine     # {"event":"listening","host":"127.0.0.1","port":54321,…}
```

## Documentation

| Document | Contents |
|---|---|
| [`docs/decision.md`](docs/decision.md) | The technical decision: architecture, providers, runtimes, packaging, risks |
| [`docs/research.md`](docs/research.md) | Model survey, primary-source verified, dated |
| [`docs/model-evaluation.md`](docs/model-evaluation.md) | Capability / hardware / licence matrices |
| [`docs/licenses.md`](docs/licenses.md) | Per-artifact licence inventory including dependencies |
| [`docs/integration-notes.md`](docs/integration-notes.md) | Findings that cost time to establish: MPS on macOS 26, dependency pins, substitutions |
| [`docs/audit-phase0.md`](docs/audit-phase0.md) | Environment and hardware audit |
| [`docs/adr/`](docs/adr/) | Architecture decision records |

## Two things worth knowing up front

**The best open model does not fit most machines.** TRELLIS.2 (MIT weights, real PBR) needs
roughly 24 GB of unified memory or VRAM. VOLUM therefore ships a second, much smaller
provider so the application is useful — and testable — on ordinary hardware.

**A permissive model licence does not mean a permissive pipeline.** TRELLIS.2's weights are
MIT, but its official pipeline depends on `nvdiffrast`, which is non-commercial. VOLUM
tracks the licence of every artifact required to produce an asset, not just the headline
one, and will not claim commercial usability it cannot evidence.

## Licence

The VOLUM application is open source (licence to be finalised before the first release).
Model weights are **never** committed to this repository — they are downloaded on request
into your local data directory, under their own licences.
