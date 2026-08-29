# ADR 0001 — Primary provider: TRELLIS.2, with TripoSR as the bring-up provider

Date: 2026-08-30 · Status: **Proposed**

## Context

VOLUM needs a real image-to-3D model (spec §37: no mocks in the production path). The
survey in `docs/research.md` produced two facts that conflict:

- TRELLIS.2 is the best open model — 4B parameters, real PBR, GLB, **MIT weights** — and
  it needs ~24 GB. The development machine has 16 GB and 29 GB of free disk.
- The models that fit 16 GB either carry a territorial licence exclusion (Hunyuan3D-2.1,
  EU excluded), a revenue-conditional licence (SF3D), or are old and produce no PBR
  (TripoSR).

## Decision

Ship **two** providers in V1.

- **TRELLIS.2** as the primary/quality target, hardware-gated to ≥24 GB unified memory or
  VRAM, reached on macOS through a PyTorch-MPS backend.
- **TripoSR** as the bring-up provider: MIT with no conditions at any layer, small enough
  for this machine and for CPU CI.

Hunyuan3D-2.1 is documented but **not bundled** — its licence excludes the European
Union, where this project is developed (`docs/licenses.md`). SF3D is deferred past V1.

## Consequences

- Every pipeline stage is exercised by real inference from day one, on the developer's own
  machine and in CI. Spec §37 becomes enforceable rather than aspirational.
- The provider abstraction is validated by two genuinely different providers before V1
  ships, which is spec §68's test applied early rather than late.
- VOLUM must refuse TRELLIS.2 gracefully on insufficient hardware, with an explicit user
  override (spec §7). No crash loops, no silent CPU fallback of a GPU model.
- Users on 16 GB machines get a working product with a modest model rather than an
  unusable one — which is the same problem the developer has, so it will be well tested.
- Cost: two providers to maintain in V1, and TripoSR's output quality is visibly below the
  2026 state of the art. The UI must set expectations per provider rather than globally.

## Rejected

- **TRELLIS.2 only.** Nothing would run locally or in CI; every stage would be tested
  against a mock, which the spec forbids.
- **Hunyuan3D-2.1 as the 16 GB provider.** Technically the best fit for consumer hardware
  in the whole survey. Blocked by the EU territorial exclusion.
- **TRELLIS v1.** Superseded, no PBR, and a *harder* dependency set to port to Metal
  (`spconv`, `diffoctreerast`) than TRELLIS.2's.
