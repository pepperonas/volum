# Technical Decision — Phase 0 + Phase 1 Outcome

Date: **2026-08-30** · Status: **proposed, awaiting approval** · Supersedes: nothing

This is the deliverable requested by spec §76. It answers, in order: recommended
architecture, primary model, secondary model, Apple/Windows/Linux runtimes, packaging
strategy, repository structure, key risks, licence risks, hardware risks.

It also proposes **four corrections to the spec**, each with reasoning. They are collected
in §11 so they can be accepted or rejected individually.

---

## 1. The two findings that drive everything

**Finding 1 — the best model does not run on the development machine.**

TRELLIS.2 is the correct primary model: 4B parameters, real PBR, GLB output, and MIT
weights — the only top-tier model with no usage restriction. The Apple Silicon port peaks
at **~18 GB unified memory** and recommends 24 GB. This machine has **16 GB**, with
**29 GB free disk** against ~15 GB of weights plus a multi-gigabyte Torch environment.

The consequence is not "buy more RAM". It is architectural: **which provider a machine can
run must be a first-class runtime decision**, computed at startup, with an honest refusal
rather than a crash (spec §7). VOLUM's own CI and daily development cannot depend on the
flagship model either.

**Finding 2 — a permissive model licence does not make a permissive pipeline.**

TRELLIS.2's weights are MIT. Its official pipeline depends on `nvdiffrast`, whose licence
reads: *"The Work and any derivative works thereof only may be used or intended for use
non-commercially."* The official CUDA pipeline is therefore non-commercial in practice.

The Apple Silicon port replaces `nvdiffrast` with a Metal implementation and can replace
the CC BY-NC background remover with MIT-licensed BiRefNet. **The macOS path is not only
the only one that runs on a Mac — it is the licence-clean one.** That inverts the usual
assumption that Apple support is a compatibility tax, and it is the strongest argument for
the architecture below.

---

## 2. Primary and secondary providers

| Role | Choice | Rationale |
|---|---|---|
| **Primary** | **TRELLIS.2** via a PyTorch-MPS backend (`trellis-mac` approach) | Best open geometry + real PBR + MIT weights. Hardware-gated to ≥24 GB. |
| **Bring-up / floor** | **TripoSR** | MIT with no conditions at any layer, small, runs on this machine and on CPU CI. Makes the pipeline genuinely end-to-end on day one — which is how spec §37's "no fake providers" rule is satisfied *in practice* rather than aspirationally. |
| **Second tier (post-V1)** | **SF3D** | Official MPS path, ~6 GB, better output than TripoSR. Gated on its revenue-conditional licence being acceptable and surfaced to the user. |
| **Documented, not bundled** | Hunyuan3D-2.1 | Technically excellent, fits 16 GB shape-only — but its licence excludes the European Union, where this project is developed. See `docs/licenses.md`. |
| **P1, separate family** | VGGT + meshing | The only honest route to true multi-image reconstruction. |

**Why TripoSR is in V1 and not cut as "too old".** The alternative is a V1 whose only
provider cannot run on the developer's machine or in CI. That produces exactly the failure
mode spec §37 forbids: mocked pipelines, fake GLBs, untested stages. TripoSR is modest in
quality and unrestricted in licence, and it turns every stage of the pipeline — analysis,
preprocessing, inference, mesh processing, validation, export, viewer — into something
that runs for real, today, on 16 GB. It is the test harness that happens to also be a
shipping feature.

---

## 3. Runtime strategy per platform

| Platform | Runtime | Status |
|---|---|---|
| **macOS Apple Silicon** | **PyTorch MPS + hand-written Metal kernels** | **Primary development and reference target** |
| macOS Apple Silicon | MLX | Experimental, behind a flag, only if it proves faster |
| macOS Intel | CPU | Best effort; not a target |
| **Windows** | PyTorch CUDA (NVIDIA) | Supported; CPU fallback for small providers only |
| **Linux** | PyTorch CUDA (NVIDIA) | Supported; upstream's own tested platform |
| Windows/Linux | ROCm, DirectML, Vulkan | ❌ Not promised. Only if a specific provider genuinely supports it (spec §5). |

**This reverses the spec's stated priority (§5: "Priorität: MLX / Metal, Fallback: PyTorch
MPS") and the reversal is deliberate.** The research found essentially no image-to-3D
ecosystem on MLX; MLX coverage is overwhelmingly LLM-oriented. Every working Apple Silicon
3D pipeline surveyed is PyTorch MPS with custom Metal kernels for the parts MPS cannot
express (sparse GEMM, differentiable rasterisation, BVH). The one MLX TRELLIS.2 fork that
exists has ~28 commits and documents neither its memory requirements nor which stages
actually run on MLX.

Choosing MLX first would mean building the product on the least mature layer of the stack.
The `Runtime` abstraction keeps MLX reachable — if `trellis2-apple` matures, it becomes an
alternative backend behind the same provider, not a rewrite.

**Rejected: CoreML / ONNX.** No conversion path exists for any surveyed model. Dynamic
shapes, sparse voxel ops and custom kernels are hostile to CoreML's fixed-graph model.
Converting one would be a research project. This confirms the spec's Python engine (§3)
rather than undermining it. Recorded in `docs/adr/0002-runtime-strategy.md`.

**Watch item: `trellis.cpp`.** Pure C++/GGML, MIT, no Python at runtime, **GGUF quantised
weights** — which is the actual answer to the 16 GB problem. It has CUDA, ROCm and Vulkan
backends and **no Metal backend**, and its issue tracker shows no plans for one. GGML's
Metal backend is mature upstream; the gap is this project's custom ops. This is the most
promising future second runtime and a plausible place for VOLUM to contribute upstream. It
is not a V1 option.

---

## 4. Recommended architecture

```
  Tauri 2 shell  ──────────────────────────────┐
    React + TS + Vite + Tailwind/shadcn        │  spawns + supervises
    Three.js / R3F viewer                      │
         │                                     ▼
         │  HTTP + SSE on 127.0.0.1        volum-engine  (Python sidecar)
         │  ephemeral port, session token        │
         └──────────────────────────────────────┤
                                                │
     volum CLI ─── imports directly ───────► volum-core  (one library, no duplicate logic)
                                                │
                    ┌───────────────────────────┼──────────────────────┐
                    │                           │                      │
              JobManager                  PipelineStages         ModelManager
              (state machine,          analyze → preprocess →    (registry, install,
               cancellation,           infer → mesh → texture →   verify, disk usage,
               persistence)            optimize → validate →      gated HF downloads)
                    │                  export
                    │                           │
                    └──── spawns per job ──►  Provider worker subprocess
                                                │   own venv, own memory
                                        ImageTo3DProvider
                                                │
                                    ┌───────────┴───────────┐
                                 Runtime                 Weights
                              MPS │ CUDA │ CPU        (data dir, never in repo)
```

Five decisions in that diagram are load-bearing:

**a) The engine is a Tauri sidecar speaking HTTP+SSE over loopback.** Spec §3 asks for
this to be evaluated rather than assumed. HTTP wins over stdio-IPC and Unix sockets
because: jobs are long-running and need *streaming* progress (SSE is the natural fit, and
spec §18 forbids fake progress bars — real stage transitions must be pushed); the same
interface serves the GUI and future tooling; and AF_UNIX support is uneven enough across
Windows tooling to be a liability for a cross-platform product. Security per spec §50:
bind `127.0.0.1` only, ephemeral port, per-session bearer token generated by the shell and
passed to the child through the environment, port reported back on stdout. No network
interface, no fixed port, no unauthenticated local endpoint.

**b) The CLI imports `volum-core` directly; it does not shell out to the engine.** Spec
§32 requires one core pipeline and no duplicated business logic. One library, two front
doors — the HTTP engine for the GUI, direct import for the CLI. Both operate the same
`JobManager` and the same stages.

**c) Every job runs in a separate provider subprocess.** This is not premature isolation.
The research surfaced a concrete failure mode: `trellis-mac` issue #8 reports the decoder
*silently producing an empty mesh* on M2 Ultra with a killed Metal command buffer. A
killed Metal command buffer can take the process with it. A subprocess boundary means an
OOM or GPU fault fails **one job** with a real error (spec §55) instead of the
application; it makes cancellation actually work (spec §17's `CANCELLED` state is
otherwise a lie, because you cannot interrupt a CUDA/Metal kernel from Python); and it
guarantees memory is genuinely returned to the OS, which matters acutely under unified
memory (spec §52).

**d) Each provider gets its own `uv`-managed virtual environment.** ML providers pin
mutually incompatible Torch versions and custom native packages. One shared environment
would make installing a second provider a dependency-resolution fight, and would violate
spec §68's test ("if provider 2 requires changes in ten places, stop"). Per-provider
environments also make `ModelManager.remove()` and `disk_usage()` truthful, which on a
29 GB disk is a feature, not bookkeeping.

**e) Provider capabilities and requirements are declared data, not code paths.** Each
provider declares `SINGLE_IMAGE`, `MULTI_IMAGE`, `PBR`, `UV`, supported platforms and
runtimes, minimum memory and estimated peak. The doctor and the Model Manager compute
availability from hardware facts. On this machine TRELLIS.2 reports *"needs ~24 GB unified
memory, this machine has 16 GB"* with an explicit user override — never a crash loop, and
never a silent CPU fallback of a GPU model (spec §7).

---

## 5. Packaging strategy

**Thin installer, fat data directory.**

| | Contents | Size |
|---|---|---|
| App bundle (`.dmg` / `.msi` / AppImage) | Tauri shell, frontend, `volum-core`, CLI, relocatable Python interpreter | target < 150 MB |
| Data directory | provider virtualenvs, model weights, projects, jobs, cache, logs, exports | multiple GB |

Heavy ML dependencies and all weights install **on first use**, triggered by the user
through the Model Manager, never automatically (spec §12, §26). This follows directly from
spec §40's requirement that app updates and model updates be independent — a 100 MB app
update must never drag 15 GB of weights behind it — and it is the only responsible design
given the disk findings.

**The data directory must be relocatable to external storage from the first release.** On
this machine, one provider plus its environment is roughly 20 GB of 29 GB free. Platform
defaults (`~/Library/Application Support/VOLUM`, `%APPDATA%\VOLUM`, `$XDG_DATA_HOME/volum`)
with an explicit override and a visible free-space check before every install.

The relocatable interpreter comes from `uv`'s python-build-standalone distributions, which
solves the "which Python does the user have" problem the audit already surfaced — the
system default here is 3.14.7, which the ML stack does not yet support. VOLUM pins
`>=3.11,<3.13` and provisions it itself.

macOS builds are **arm64-native**, never Rosetta. Universal binaries are explicitly not a
goal: an x86-64 slice would carry a CUDA-less, Metal-less runtime that cannot run the
primary provider, so it would ship a promise the software cannot keep.

---

## 6. Repository structure

```
volum/
├── apps/
│   ├── desktop/              Tauri 2 shell (Rust) + React/TS frontend
│   │   ├── src/              React, Three.js/R3F viewer, shadcn/ui
│   │   └── src-tauri/        Rust: sidecar supervision, data dir, updater
│   └── cli/                  `volum` entry point → imports volum-core
├── engine/
│   ├── volum_core/           the one pipeline — no logic lives anywhere else
│   │   ├── hardware/         detection, doctor
│   │   ├── jobs/             state machine, persistence, cancellation
│   │   ├── models/           registry, ModelManager, gated HF downloads
│   │   ├── providers/        ImageTo3DProvider ABC + implementations
│   │   ├── runtime/          MPS / CUDA / CPU abstraction
│   │   ├── pipeline/         analyze, preprocess, mesh, texture, optimize
│   │   ├── validation/       quality_report.json
│   │   ├── export/           GLB first; OBJ/STL/USDZ/FBX/3MF later
│   │   └── storage/          data dir, projects, cache, hashing
│   ├── volum_engine/         HTTP+SSE sidecar — a thin shell over volum_core
│   └── tests/
├── benchmarks/
├── docs/                     research, model-evaluation, licenses, decision, adr/
├── .github/workflows/
├── CHANGELOG.md  README.md  CONTRIBUTING.md  SECURITY.md  LICENSE  .env.example
```

Monorepo, single version source of truth (spec §43) propagated to `pyproject.toml`,
`package.json`, `tauri.conf.json`, the CLI and git tags by one script verified in CI.

---

## 7. Hardware risks

| Risk | Severity | Assessment |
|---|---|---|
| **16 GB cannot run the primary provider** | **High** | ~18 GB peak vs 16 GB physical. macOS will swap to a fast SSD, so it may complete — slowly, with heavy write amplification. Untested at this size; `trellis-mac` publishes no 16 GB data point. |
| **29 GB free disk** | **High** | ~15 GB weights + a Torch environment ≈ 20 GB. One provider nearly fills the disk. |
| Metal command-buffer kills | Medium | Observed upstream (`trellis-mac` #8) as a *silent empty mesh*. Mitigated by subprocess isolation plus mandatory output validation — a silent empty mesh must fail the job, not export as an asset. |
| Thermal/sustained load | Low–Medium | Multi-minute GPU saturation on a laptop; throttling affects benchmark comparability. Record thermal state alongside benchmark results. |
| CI has no GPU | Medium | Anticipated by spec §37/§38: CPU-safe integration tests in CI, GPU smoke tests separate and optional. TripoSR makes the CPU tier real. |

**This needs a decision from you.** Three viable paths, in preference order:

1. **Develop against TripoSR/SF3D locally; validate TRELLIS.2 on borrowed hardware.** The
   provider abstraction is exactly what makes this work — a rented CUDA box or a 24 GB+ Mac
   runs the smoke tests, while all pipeline development happens locally at full speed. Cheap,
   and it exercises the cross-platform architecture from day one rather than assuming it.
2. **Attempt TRELLIS.2 on this machine and measure it.** Nobody has published a 16 GB
   figure; producing one would be a genuine contribution. Requires freeing ~20 GB first.
   Worth doing once, as a measurement, not as the development loop.
3. **More memory.** A 24 GB+ Apple Silicon machine turns the primary provider into a
   daily driver. Outside my control; noted because it materially changes the plan.

I recommend **(1) plus (2) once**, and no change to the architecture either way — the
hardware gate is required regardless, because VOLUM's users will have the same problem.

---

## 8. Licence risks

| Risk | Severity | Status |
|---|---|---|
| **`nvdiffrast` non-commercial** | **High** | Blocks the official CUDA pipeline. Mitigated on macOS by `mtldiffrast`. |
| **`mtldiffrast` / `mtlgemm` / `mtlbvh` / `mtlmesh` licences UNKNOWN** | **High** | **Blocking for any commercial claim.** Must be resolved before V1 ships or claims commercial usability. |
| **Hunyuan3D-2.1 excludes the EU** | High | Resolved by exclusion: documented, not bundled. |
| RMBG-2.0 is CC BY-NC | Medium | Resolved: BiRefNet (MIT) instead, in VOLUM's own preprocessing stage. |
| DINOv3 gated + attribution | Medium | Usable. Two hard requirements: authenticated gated download in the Model Manager, and **"Built with DINOv3"** displayed in the UI. |
| SF3D revenue-conditional | Medium | The *user's* revenue decides legality, not the project's — must be surfaced before install if integrated. |
| Community forks' licences | Medium | `trellis-mac` states MIT; per-dependency verification still outstanding. |
| VGGT default checkpoint is NC | Medium | Use `VGGT-1B-Commercial`; approval required. |

Until the `mtl*` question is settled, **VOLUM makes no commercial-use claim for the
TRELLIS.2 provider.** The Model Manager shows each model's licence and an honest status —
including "unclear" — before download (spec §10, §12).

## 9. Project risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Apple path depends on a 4-month-old single-maintainer fork** | **High** | The `Runtime` abstraction must make the Mac backend swappable (`trellis-mac` → `trellis2-apple` → future `trellis.cpp` Metal) without touching the pipeline. Pin an exact commit; vendor if it goes unmaintained. |
| Field moves fast | Medium | Spec §73 already requires re-evaluation. `docs/research.md` is dated and re-verified per release. |
| MPS ≠ CUDA determinism | Medium | Do not promise cross-machine reproducibility. Record seed, model version, runtime, input hash, preprocessing config; state the limit plainly (spec §30). |
| Scope | Medium | V1 is one workflow done properly: single image → GLB → viewer → export. |
| Quality is unmeasured | Medium | No quality claim until `volum benchmark` produces numbers on named hardware. |

---

## 10. What V1 is — and is not

**Is:** single-image generation → real inference → GLB with PBR → validation → viewer →
export; hardware detection and `volum doctor`; Model Manager with licence disclosure and
gated downloads; job system with real stage progress; projects; a CLI sharing the core;
CI; SemVer; honest error messages.

**Is not:** true multi-image reconstruction; text-to-3D; editing/retopology/rigging; a
quality score; a commercial-use guarantee for TRELLIS.2 until the `mtl*` licences are
confirmed.

**Multi-image in V1 is a deliberately narrow, honestly labelled feature.** The models are
single-image; TRELLIS.2's own tracker reports multi-image conditioning performing *worse*.
So V1 accepts an image set, analyses it (spec §15), and helps the user **pick the best
frame** — labelled as exactly that. `MULTI_IMAGE` stays `false` on every V1 provider.
Simulating reconstruction is precisely what spec §14 forbids.

---

## 11. Proposed corrections to the spec

Each is independent; accept or reject individually.

1. **§5 runtime priority: PyTorch MPS + Metal kernels first, MLX experimental.** MLX has
   no meaningful image-to-3D ecosystem; every working Apple pipeline uses MPS. Building on
   MLX first would put the product on the least mature layer of the stack.
2. **§14 multi-image: reframe as frame selection for V1.** Generation models are
   single-image; real reconstruction is a different model family (VGGT/MASt3R) with no mesh
   output and its own licence problems. Shipping "multi-image" that silently uses one image
   would be the fake feature the spec forbids.
3. **§59 P0: add a second, lightweight provider (TripoSR).** Without it, no V1 stage can be
   tested on the development machine or in CI, and "no mocks in the production path" becomes
   unenforceable.
4. **§27 storage: make the data directory relocatable in V1, not later.** 29 GB free
   against ~20 GB for one provider makes this a functional requirement, not a preference.

---

## 12. Open questions before implementation

1. **Licences of `mtldiffrast`, `mtlgemm`, `mtlbvh`, `mtlmesh`** — blocking for any
   commercial claim. Read the repositories directly; if unlicensed, ask the author.
2. **Does TRELLIS.2 complete on 16 GB unified memory, and how slowly?** No published data
   point exists. Requires freeing ~20 GB of disk.
3. **GitHub repository** — `pepperonas/volum` does not exist. Public from the first commit,
   or private until V1? (Spec §41 assumes public; creating it is an outward-facing action I
   have not taken.)
4. **Hardware plan** — §7 above, options 1/2/3.
5. **Documentation language** — these documents are in English on the assumption that a
   public OSS project wants international contributors (spec §49). The spec itself is
   German. Say the word and I will switch.

---

## Next steps once this is approved

1. Resolve the `mtl*` licence question (blocking).
2. Repository scaffold: Tauri 2 + React + `volum-core`, `pyproject.toml`, version
   single-source-of-truth, lint/test/CI green on an empty-but-real pipeline.
3. `HardwareDetector` + `volum doctor` — the first thing that must work, because every
   later decision depends on it.
4. `ImageTo3DProvider` interface + `ModelManager` + **TripoSR** as the first real provider,
   end-to-end to a validated GLB in the viewer.
5. Only then TRELLIS.2, against the interface proven by step 4 — which is also spec §68's
   test of whether the abstraction is real.
