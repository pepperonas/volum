# Model Evaluation

Evaluation date: **2026-08-30**. Companion to `docs/research.md`, which carries the
sources and the raw findings.

## How to read this document

**No inference has been run yet.** Nothing here is a VOLUM measurement. Every quality
column is either an author claim, a community consensus, or absent. Quality rows are
therefore marked as **unmeasured** and are explicitly *not* used as the deciding factor
between the two front-runners — licence and memory are, because those are facts that
could be verified today.

Empty benchmark tables are deliberate. They will be filled by `volum benchmark` under the
conditions recorded in `docs/benchmarks.md` (spec §34), on identical inputs, on named
hardware. Until then the honest value is "—".

Legend: ✅ yes · ⚠️ conditional, see note · ❌ no · **?** UNKNOWN (not established from a
primary source)

---

## 1. Capability matrix

| | TRELLIS.2 | TRELLIS v1 | Hunyuan3D-2.1 | SF3D | TripoSR | VGGT |
|---|---|---|---|---|---|---|
| Parameters | 4B | 1.2B (image) | ? | ~1B | small | 1B |
| Single image | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Multi image | ❌ official; community patches report *worse* results | ⚠️ tuning-free, authors caveat quality | ✅ multi-view | ❌ | ❌ | ✅ (reconstruction) |
| Text input | ❌ | ✅ (separate text models) | ✅ | ❌ | ❌ | ❌ |
| PBR materials | ✅ base colour / roughness / metallic / opacity | ❌ | ✅ | ⚠️ predicts material params | ❌ vertex colours only | ❌ |
| UV unwrapping | ✅ | ⚠️ | ✅ | ✅ | ❌ | ❌ |
| Mesh output | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ point cloud / poses only |
| Native formats | GLB, OBJ, PLY, radiance field, Gaussians | GLB, PLY (Gaussians) | GLB | GLB | OBJ/GLB | COLMAP, PLY |
| Max texture res | 4096² | — | up to 8K (claimed) | ? | n/a | n/a |

## 2. Hardware and platform matrix

| | TRELLIS.2 | TRELLIS v1 | Hunyuan3D-2.1 | SF3D | TripoSR |
|---|---|---|---|---|---|
| Stated VRAM | **≥24 GB** (quoted) | **≥16 GB** (quoted) | 6 GB shape / 16 GB full | ~6 GB | low |
| Weights on disk | ~15 GB (Mac port) | ? | ? | ? | small |
| CUDA | ✅ (12.4 recommended) | ✅ (11.8 / 12.2) | ✅ | ✅ | ✅ |
| Linux | ✅ only tested platform | ✅ | ✅ | ✅ | ✅ |
| Windows | ⚠️ untested upstream; `trellis.cpp` ships a Win x64 binary | ⚠️ *"not fully tested"* | ✅ | ✅ | ✅ |
| macOS / Apple Silicon | ⚠️ **community forks only** (`trellis-mac` MPS, `trellis2-apple` MLX) | ❌ dependency set harder to port | ❌ | ⚠️ official MPS, *experimental* | ✅ |
| **Runs on M1 Pro 16 GB** | ❌ peak ~18 GB, 24 GB recommended | ❌ | ⚠️ shape-only would; licence blocks it | ⚠️ upstream advises CPU under 32 GB unified | ✅ |

## 3. Licence and commercial-use matrix

Full per-artifact detail, including the dependency chain, is in `docs/licenses.md`.

| | Code licence | Weights licence | Commercial use | Territorial limit |
|---|---|---|---|---|
| TRELLIS.2 | MIT | **MIT** | ✅ for the model — ⚠️ **blocked by `nvdiffrast` in the official pipeline** | none |
| TRELLIS v1 | MIT | MIT | same `nvdiffrast` caveat | none |
| Hunyuan3D-2.1 | Tencent Community | Tencent Community | ⚠️ yes, under 1M MAU | ❌ **EU, UK, South Korea excluded** |
| SF3D | Stability Community | Stability Community | ⚠️ only under USD 1M annual revenue | none |
| TripoSR | MIT | **MIT** | ✅ unrestricted | none |
| VGGT | see repo | ❌ NC — commercial checkpoint by application | ⚠️ application required | none |

The single most important cell in this table: **TRELLIS.2's MIT weights do not make the
official TRELLIS.2 pipeline commercially usable**, because `nvdiffrast` — the
differentiable rasteriser used for texture baking — is non-commercial. Replacing it (as
the Apple Silicon port does, with `mtldiffrast`) is what actually unlocks commercial use.

## 4. Maintenance and integration effort

| | Upstream activity | Apple path maturity | Integration effort for VOLUM |
|---|---|---|---|
| TRELLIS.2 | Active, Microsoft | `trellis-mac` ~470★, ~24 commits, single maintainer, Apr–May 2026 issues | **High.** Fork dependency, five replaced kernels, disabled hole-filling, one open "silent empty mesh" bug |
| TRELLIS v1 | Stable/mature | none | High, and superseded — not worth it |
| Hunyuan3D-2.1 | Active, Tencent | none | Medium — but licence-blocked for this project |
| SF3D | Stability, low activity | Official but *"experimental"* | **Low–medium** |
| TripoSR | Old (2024), quiet | Works, incl. CPU | **Low** |

"Maintenance" cuts two ways here. TRELLIS.2 upstream is a Microsoft repo and will be
maintained. The *Apple Silicon layer* VOLUM would depend on is a four-month-old
single-maintainer fork. That asymmetry is the dominant sustainability risk of the whole
plan and is why the provider interface must make the Mac backend swappable
(`trellis-mac` → `trellis2-apple` → a future `trellis.cpp` Metal backend) without
touching the pipeline.

## 5. Reproducibility

| | Seed support | Deterministic? |
|---|---|---|
| TRELLIS.2 | ✅ diffusion seed | **Not guaranteed.** MPS and CUDA kernels differ; the Mac port substitutes attention, GEMM and rasterisation implementations. Same seed, same input, *different* machine ⇒ expect a different mesh. |
| Others | generally ✅ | same caveat |

Consequence for spec §30: VOLUM stores seed, model version, runtime, parameters, input
hash and preprocessing config, and **states plainly that bit-identical reproduction is
only expected on the same machine with the same runtime**. Claiming more would be false.

## 6. Benchmarks

### Measured by VOLUM

First real measurements. Wall clock from `volum generate`, single image, default
settings, `mc_resolution=256`, on an otherwise idle machine.

| Model | Runtime | Hardware | Input | Total | Vertices | Triangles | Watertight | Validation |
|---|---|---|---|---|---|---|---|---|
| TripoSR | MPS | M1 Pro, 16 GB, macOS 26.6.2 | chair.png | 51.5 s | 41,864 | 83,732 | yes | pass |
| TripoSR | MPS | M1 Pro, 16 GB, macOS 26.6.2 | flamingo.png | 63.3 s | 23,095 | 46,184 | no | pass, with notes |
| TRELLIS.2 | MPS | M1 Pro, 16 GB | — | — | — | — | — | not yet run |

Notes worth keeping with the numbers: the flamingo result is not watertight and
has two zero-area faces — reported as notes rather than failures, because holes
make a mesh unfit for printing, not unfit for use. Both runs produced vertex
colours and no UV map, which is what TripoSR declares.

These are *not* a quality judgement. They establish that the pipeline works and
give an honest baseline for the machine most likely to run it.

### Third-party claims

Gathered during research and recorded as *claims* with their conditions, not as
VOLUM results:

| Source | Model | Hardware | Time |
|---|---|---|---|
| `microsoft/TRELLIS.2` | TRELLIS.2 1024³ | H100 | ~17 s |
| `microsoft/TRELLIS.2` | TRELLIS.2 1536³ | H100 | ~60 s |
| `shivampkumar/trellis-mac` | TRELLIS.2 | M4 Pro 24 GB | 5 m 13 s cold / 3 m 20 s warm |
| `pwilkin/trellis.cpp` | TRELLIS.2 res-1024 | RTX 5060 Ti | 3 m 16 s – 7 m 23 s |

Third-party figures gathered during research, recorded as *claims* with their conditions,
not as VOLUM results:

| Source | Model | Hardware | Time |
|---|---|---|---|
| `microsoft/TRELLIS.2` | TRELLIS.2 1024³ | H100 | ~17 s |
| `microsoft/TRELLIS.2` | TRELLIS.2 1536³ | H100 | ~60 s |
| `shivampkumar/trellis-mac` | TRELLIS.2 | M4 Pro 24 GB | 5 m 13 s cold / 3 m 20 s warm |
| `pwilkin/trellis.cpp` | TRELLIS.2 res-1024 | RTX 5060 Ti | 3 m 16 s – 7 m 23 s |

Note the spread: the same model is ~17 s on an H100 and ~5 minutes on a 24 GB Mac. Any
UI copy, progress estimate or marketing claim must be derived from VOLUM's own numbers on
the user's own hardware, never from the H100 figure. This is why
`ResourceEstimate.estimated_seconds` stays `None` until VOLUM has measured the
model on the machine in front of it.

## 7. Verdict

| Role | Model | Why |
|---|---|---|
| **Primary (quality target)** | **TRELLIS.2** | Only model combining top-tier geometry, real PBR and an MIT weights licence. Hardware-gated to ≥24 GB unified / ≥24 GB VRAM. |
| **Bring-up + floor** | **TripoSR** | MIT, unrestricted, small, runs on this machine and on CI. Makes the pipeline real end-to-end from day one without a fake provider. |
| **Candidate second tier** | **SF3D** | Better output than TripoSR, official MPS path, ~6 GB — but a revenue-capped licence. Evaluate after V1. |
| **Documented, not bundled** | **Hunyuan3D-2.1** | Technically excellent and would fit 16 GB shape-only. EU territorial exclusion makes it unshippable from this project. |
| **P1 track, separate family** | **VGGT** + meshing | The only honest route to real multi-image reconstruction. Needs its own licence review (NC default checkpoint). |
| **Rejected for V1** | TRELLIS v1 | Superseded by TRELLIS.2; harder to port; no PBR. |
| **Future runtime, not a model choice** | `trellis.cpp` | GGUF quantisation is the real answer to consumer memory limits. Blocked on a Metal backend. |

Reasoning is recorded in `docs/adr/0001-primary-provider.md` and
`docs/adr/0002-runtime-strategy.md`.
