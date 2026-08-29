# VOLUM

**Local-first, cross-platform image-to-3D desktop application.**

Turn one or more photographs into a usable, textured 3D asset — entirely on your own
machine. No cloud, no account, no telemetry, no remote inference.

> **Status: pre-implementation.** Research and architecture are complete; no code has been
> written yet. See [`docs/decision.md`](docs/decision.md) for the technical plan and
> [`docs/research.md`](docs/research.md) for the model survey it rests on.
> Nothing here works yet — this README describes what is being built, not what ships.

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

## Planned stack

Tauri 2 · React + TypeScript + Vite · Tailwind + shadcn/ui · Three.js / React Three Fiber ·
Python engine (`uv`) · SQLite · pytest / Vitest / Playwright · GitHub Actions ·
Semantic Versioning + Conventional Commits. Primary export: **GLB**.

## Documentation

| Document | Contents |
|---|---|
| [`docs/decision.md`](docs/decision.md) | The technical decision: architecture, providers, runtimes, packaging, risks |
| [`docs/research.md`](docs/research.md) | Model survey, primary-source verified, dated |
| [`docs/model-evaluation.md`](docs/model-evaluation.md) | Capability / hardware / licence matrices |
| [`docs/licenses.md`](docs/licenses.md) | Per-artifact licence inventory including dependencies |
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
