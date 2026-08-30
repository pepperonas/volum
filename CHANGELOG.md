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
