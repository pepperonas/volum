# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Phase 0 environment and repository audit (`docs/audit-phase0.md`).
- Phase 1 image-to-3D model research, primary-source verified (`docs/research.md`).
- Model capability / hardware / licence evaluation (`docs/model-evaluation.md`).
- Per-artifact licence inventory including pipeline dependencies (`docs/licenses.md`).
- Technical decision and architecture (`docs/decision.md`, `docs/architecture.md`).
- Architecture decision records ADR-0001 (primary provider) and ADR-0002 (runtime strategy).
- Hardware detection (`HardwareDetector`) with a four-valued availability model
  separating "the hardware cannot" from "the software is not installed yet".
- `volum doctor`: platform, CPU, GPU, memory, disk, runtimes and a per-model
  verdict for this machine.
- `volum models list` / `volum models show <id>` including the full licence chain.
- Model registry with dated licence facts. Provider contract
  (`ImageTo3DProvider`) and hardware gating.
- Relocatable data directory with atomic settings and path-traversal guards.
- Version single source of truth (`scripts/sync_version.py`) and CI across
  Linux, macOS (Apple Silicon) and Windows on Python 3.11 and 3.12.
- Job system: persisted records, an explicit state machine that cannot move
  backwards, stage-based progress, and cancellation that both stops at stage
  boundaries and terminates the worker.
- Model manager: per-provider virtual environments, sources pinned to a commit,
  weights downloaded only on request, gated-download handling, verification,
  removal and real disk accounting.
- Provider subprocess boundary with a JSON line protocol, so a GPU fault fails
  one job rather than the engine.
- **TripoSR provider running real inference**, including a PyMCubes-backed
  compatibility module that removes the need to compile `torchmcubes`.
- Mandatory asset validation producing `quality_report.json`; a model that
  reports success while producing no geometry now fails the job.
- `volum generate`, end to end: job → inference → validation → GLB plus
  `asset.json` recording seed, runtime, parameters and hardware.
- **Printable output.** `volum generate --print` repairs the mesh to a closed solid
  (boundary-loop filling, or voxel remeshing when that is not enough), validates it
  against what a slicer needs rather than what a renderer tolerates, and exports **STL**
  and **3MF** scaled to real millimetres (`--size-mm`) and laid on the build plate.
  `--single-part` drops the loose fragments generated models routinely carry. A mesh
  that is merely renderable no longer passes as printable, and `repair_report.json`
  says what was changed.
- **TRELLIS.2 provider** (Apple Silicon path), geometry-only: 7.33 GB rather than the
  full 15.1 GB, because the 1024 and texture models carry nothing STL can hold. VOLUM
  ships its own pipeline configuration that substitutes **BiRefNet** (MIT) for the
  **RMBG-2.0** (CC BY-NC) the upstream config names.
- **Local HTTP engine (`volum-engine`)**, the Tauri sidecar: loopback only, ephemeral
  port announced as one JSON line on stdout, a per-session bearer token required on
  every request, CORS restricted to the desktop webview's origins. Jobs, models,
  doctor and settings over JSON; job and install progress as server-sent events
  carrying the full record; artifacts served with `model/gltf-binary`, `model/stl`
  and `model/3mf` media types. `--exit-with-parent` stops the engine when the shell
  that started it is gone.
- Application service (`volum_core.service.VolumService`) shared by the CLI and the
  engine, so device choice, format resolution, input staging and cancellation are
  decided in exactly one place.
- Input images are validated by content (PNG, JPEG, WebP), size-limited, copied into
  the job under UUID names and hashed; `asset.json` records the SHA-256 of each input.

### Changed
- TRELLIS.2's commercial-use status moved from `UNKNOWN` to `CONDITIONAL`: the four
  Metal packages (`mtldiffrast`, `mtlgemm`, `mtlbvh`, `mtlmesh`) are all **MIT**, read
  from their licence files, and `mtldiffrast` is implemented from Laine et al. (2020)
  rather than derived from `nvdiffrast`, so NVIDIA's non-commercial clause does not
  reach the Apple Silicon path. The remaining conditions are the CUDA path's exclusion,
  the "Built with DINOv3" attribution and the BiRefNet substitution.

### Fixed
- Cancelling a running job from another thread now ends as `cancelled`, not `failed`:
  the worker dying after the token was set, and the state machine refusing a move out
  of `cancelled`, were both being reported as failures.
- A worker that ignores SIGTERM is killed after a 5 s grace period; before, "cancelled"
  could be shown while the process kept the GPU for as long as it liked.
- Job progress streamed over SSE could collapse two stages into one frame when the
  pipeline moved faster than the event loop serialised; frames are now snapshotted
  when the notification fires.
- The test suite is fenced off from the real user directories (`VOLUM_CONFIG_DIR`,
  `VOLUM_DATA_DIR`); one test had written a fake token into the developer's real
  `settings.json`.
- `docs/integration-notes.md` recording findings that were expensive to
  establish.

### Fixed
- `.gitignore` directory rules are anchored to the repository root. Unanchored,
  `jobs/` also matched `engine/src/volum_core/jobs/` and excluded a source
  package from the repository entirely.
- PEP 561 `py.typed` markers, without which the installed package was treated as
  untyped.
- Import sorting now declares first-party packages rather than relying on
  filesystem detection, which differed between a working copy and a clean
  checkout.
