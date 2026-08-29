# Phase 1 — Image-to-3D Model Research

Research date: **2026-08-30**. Every claim below was checked against a primary source
(official repository, official model card, or the license text itself) on that date.
Secondary sources — blog posts, "best of 2026" listicles, model-aggregator sites — were
used only to *find* candidates, never as evidence. Several of them turned out to be wrong
(see §7), which is why the distinction matters.

Where a fact could not be established from a primary source it is marked **UNKNOWN**
rather than guessed (spec §10).

---

## 1. The landscape in one paragraph

Single-image 3D **generation** consolidated during 2025–2026 around sparse-voxel latent
diffusion. Microsoft's TRELLIS line defined the approach; TRELLIS.2 (December 2025, 4B
parameters, MIT) is the current open quality leader and the first fully-permissive model
producing PBR materials. Tencent's Hunyuan3D-2.1 is its closest open rival, but ships
under a community license that **excludes the European Union**. Everything else in the
open field is either older and lighter (TripoSR, SF3D, InstantMesh) or not open-weight at
all (Hunyuan3D 2.5/3.x, which have papers and APIs but no released weights).

Multi-image **reconstruction** is a separate technology family (VGGT, MASt3R/DUSt3R,
photogrammetry) that outputs point clouds and camera poses, not textured meshes. It is
not a mode of the generation models. This distinction is the single most consequential
research finding for VOLUM's roadmap — see §5.

---

## 2. Candidate models

### 2.1 TRELLIS.2 — Microsoft

- Repo: `microsoft/TRELLIS.2` · Weights: `microsoft/TRELLIS.2-4B` · Released December 2025
- 4B parameters, "O-Voxel" field-free sparse voxel representation, vanilla DiT.
- **License: MIT for both code and weights.** The only top-tier model in this survey with
  no usage restriction on the model itself.
- Output: GLB with PBR — base colour, roughness, metallic, opacity; textures to 4096².
- Official hardware requirement, quoted: *"An NVIDIA GPU with at least 24GB of memory is
  necessary."* Verified by the authors on A100 and H100.
- Official platform support, quoted: *"The code is currently tested only on Linux."*
- Inference on H100: ~3 s at 512³, ~17 s at 1024³, ~60 s at 1536³.
- Input: **single image only** in the official release. Multi-image exists as community
  patches and Hugging Face Spaces; upstream issue #103 reports multi-image results are
  *worse* than single-image. Do not present it as a supported feature.
- Hard CUDA dependencies: `flash-attn`, `flexgemm`, `o-voxel`, `cumesh`, `nvdiffrast`,
  `nvdiffrec`.

TRELLIS.2 is the quality and licensing target. As shipped it runs on none of VOLUM's
three platforms without work, and on macOS not at all.

### 2.2 TRELLIS (v1) — Microsoft

- Repo: `microsoft/TRELLIS`, CVPR'25 Spotlight. Image model 1.2B parameters.
- **License: MIT, code and models.**
- Requirement, quoted: *"An NVIDIA GPU with at least 16GB of memory is necessary."*
  Linux tested; Windows *"not fully tested"*.
- Outputs 3D Gaussians, radiance fields and meshes; GLB export.
- **Has multi-image input** — a tuning-free algorithm added December 2024, with the
  authors' own caveat that it *"may not give the best results for all input images"*.
- CUDA dependencies: `flash-attn`, `spconv`, `diffoctreerast`, `mip-splatting`, `kaolin`,
  `nvdiffrast`.

Superseded by TRELLIS.2 on quality and PBR. Its dependency set (`spconv`,
`diffoctreerast`) is *harder* to port to Metal than TRELLIS.2's, so it is not a useful
Apple Silicon fallback. Retained here only as the origin of the architecture.

### 2.3 Hunyuan3D-2.1 — Tencent

- Repo: `Tencent-Hunyuan/Hunyuan3D-2.1`, June 2025. Newest Hunyuan3D line with **open
  weights**; 2.5 and 3.x exist as papers/APIs only (confirmed by listing the org's repos).
- Production-ready PBR; textures reported up to 8K; accepts text, single image and
  multi-view. Weights are **not** gated on Hugging Face.
- VRAM: ~6 GB for shape only, ~16 GB for shape + texture.
- **License: Tencent Hunyuan 3D 2.1 Community License.** Quoted verbatim from the
  license file:

  > "Territory" shall mean the worldwide territory, **excluding the territory of the
  > European Union, United Kingdom and South Korea**.

  Plus a >1M monthly-active-user threshold requiring a separate Tencent licence, a
  mandatory attribution notice, and mandatory provider disclosure. Tencent claims no
  rights in generated outputs.

On capability alone Hunyuan3D-2.1 would be an excellent second provider — it is the one
model here whose *shape-only* mode fits comfortably in 16 GB. The territorial exclusion
is decisive for this project and is treated in `docs/licenses.md`.

### 2.4 Stable Fast 3D (SF3D) — Stability AI

- Repo: `Stability-AI/stable-fast-3d`. Fast feed-forward reconstruction with UV
  unwrapping, illumination disentanglement and predicted material parameters.
- **License: Stability AI Community License** — free for research/non-commercial, and
  commercial use permitted only for entities under **USD 1M annual revenue**; above that
  an enterprise licence is required.
- ~6 GB VRAM for a single image.
- **Has an official Apple Silicon path.** Quoted from the repo:
  > "Stable Fast 3D can also run on Macs via the MPS backend, with the texture baker
  > using custom metal kernels similar to the corresponding CUDA kernels."

  Qualified immediately by:
  > "Support is experimental and not guaranteed to give the same performance and/or
  > quality as the CUDA backend."

  and, critically for this machine:
  > "We recommend running the CPU version if your system has less than 32GB of unified
  > memory."

- Successor `stable-point-aware-3d` (SPAR3D) exists under the same licence family.

### 2.5 TripoSR — Stability AI / Tripo AI

- **License: MIT**, code and weights. Small, fast, runs on modest hardware including CPU.
- Outputs a mesh with vertex colours. **It does not produce PBR materials or UV maps** —
  several 2026 blog posts claim otherwise; that claim is not supported by the project
  itself and is one of the errors catalogued in §7.
- Dates from early 2024. Quality is well below the 2026 state of the art.

Its value to VOLUM is not quality — it is that it is small, MIT, dependency-light and
therefore the one provider that can prove the entire pipeline end-to-end on a 16 GB
machine on day one.

### 2.6 Multi-view reconstruction — VGGT, MASt3R

- `facebookresearch/vggt` (1B). Outputs camera intrinsics/extrinsics, depth maps, point
  maps, point clouds, tracks; exports to COLMAP. **It does not output a mesh.**
- **License is split**: the original VGGT-1B checkpoint is **non-commercial**; a separate
  `VGGT-1B-Commercial` checkpoint permits commercial use (military excluded) and requires
  an application/approval process.
- Apple Silicon ports exist (`vggt-mps`, and an MLX MASt3R implementation claiming
  ~1.6–1.9× over PyTorch MPS). Licences of those ports: **UNKNOWN** — not verified.

To turn VGGT output into a VOLUM asset you additionally need meshing (TSDF/Poisson) and
texture projection. That is a project in its own right, correctly placed at P1/P2.

---

## 3. Apple Silicon: what actually exists

This was the decisive part of the research, because no upstream model supports macOS.

### 3.1 `shivampkumar/trellis-mac` — the viable path

A port of **TRELLIS.2** to Apple Silicon via PyTorch MPS. ~470 stars, ~24 commits, issues
dated April–May 2026, MIT licence on the porting code.

Each CUDA dependency is replaced rather than stubbed:

| Upstream (CUDA) | Replacement | Purpose |
|---|---|---|
| `flash_attn` | PyTorch SDPA | sparse-transformer attention |
| `flex_gemm` | `mtlgemm` (Metal) + pure-PyTorch fallback | sparse 3D convolution |
| `o_voxel._C` hashmap | pure-Python mesh extraction | dual-voxel-grid mesh extraction |
| `cumesh` | `fast_simplification` | decimation before baking |
| `nvdiffrast` | `mtldiffrast` (Metal) + Python fallback | differentiable rasterisation for texture baking |

Reported numbers, **M4 Pro / 24 GB**: 5 m 13 s wall clock cold, 3 m 20 s excluding
pipeline load; ~400 K vertices with baked PBR base-colour/metallic/roughness, exported as
GLB.

Stated requirements, quoted: *"24GB+ unified memory recommended (the 4B model is large)"*,
*"Memory usage peaks at around 18GB unified memory during generation"*, *"~15GB disk space
for model weights"*.

Known limitations, from the project itself: decode-time hole filling is **disabled**
(the `cumesh` Metal port segfaults on decoder-sized meshes, so output meshes may contain
small holes); the SDPA-padded sparse-attention wrapper is the largest remaining
bottleneck (~80 s of a 5 m 13 s run); meshes are pre-decimated ~800 K → ~200 K faces to
avoid Metal BVH builder instability; inference only, no training. Open issue #8 reports
the decoder *silently producing an empty mesh* on M2 Ultra with a killed Metal command
buffer — a robustness problem VOLUM must detect rather than inherit.

### 3.2 `pedronaugusto/trellis2-apple` — MLX, but early

A fork of `microsoft/TRELLIS.2` adding an **MLX backend** plus Metal kernels
(`mtlgemm`, `mtldiffrast`, `mtlbvh`, `mtlmesh`). MIT. ~36 stars, ~28 commits.

Which pipeline stages actually run on MLX vs PyTorch MPS: **UNKNOWN** — not documented.
Memory requirements: **UNKNOWN**. Tested Macs and timings: **UNKNOWN**. This is the same
author who wrote the Metal kernels `trellis-mac` depends on, so the work is credible, but
it is not yet something to build a product's default path on.

### 3.3 `pwilkin/trellis.cpp` — strategically interesting, not available today

A standalone C++/GGML implementation of the TRELLIS.2-4B pipeline. MIT. ~275 stars, with
prebuilt binaries and a desktop app ("Trellis Studio"). *"All in native C++/GGML with no
Python at runtime."* Weights distributed as **GGUF** (`ilintar/trellis2-gguf`); three
1.3B flow transformers plus VAE decoders, DINOv3 ViT-L conditioning and BiRefNet for
background removal. Output: UV-textured GLB with WebP PBR textures. States a **16 GB
card** threshold for the 1024 cascade.

Backends: **CUDA, ROCm/HIP, Vulkan. No Metal.** Official installers cover Linux x86-64
and Windows x64 only; the issue tracker contains no macOS/Metal discussion and no
maintainer statement of intent.

Why it still matters: quantised GGUF weights are the obvious answer to the 16 GB problem,
and GGML *does* have a mature Metal backend upstream — the gap is this project's custom
ops, not the framework. It is the strongest candidate for a future second runtime and a
plausible place for VOLUM to contribute. It is not a V1 option.

### 3.4 MLX in general

MLX coverage for 3D generation is thin. The ecosystem is overwhelmingly LLM-oriented;
the 3D work that exists on Apple Silicon is **PyTorch MPS plus hand-written Metal
kernels**, not MLX. See `docs/decision.md` — this contradicts the spec's stated runtime
priority and is the main architectural correction proposed.

### 3.5 CoreML / ONNX

No established CoreML or ONNX path exists for any model in this survey. These pipelines
use dynamic shapes, sparse voxel operations and custom kernels — all hostile to CoreML's
fixed-graph conversion model. Converting one would be a research project, not an
integration task. This confirms the spec's choice of a Python engine (§3) rather than
undermining it, and is recorded as a rejected option in `docs/adr/0002-runtime-strategy.md`.

---

## 4. Dependency licences — where the real risk is

The model licence is not the whole story. Three dependencies inside these pipelines carry
restrictions stricter than the models they serve:

- **`nvdiffrast` (NVlabs)** — *Nvidia Source Code License (1-Way Commercial)*. Quoted:
  *"The Work and any derivative works thereof only may be used or intended for use
  non-commercially."* Used by TRELLIS.2 and TRELLIS for texture baking. **This makes the
  official TRELLIS.2 pipeline non-commercial in practice, despite the model's MIT licence.**
- **`RMBG-2.0` (BriaAI)** — CC BY-NC 4.0, non-commercial. Used for background removal in
  several of these pipelines. Replaceable: **BiRefNet is MIT** and is the architecture
  RMBG-2.0 was built on. `trellis.cpp` already uses BiRefNet.
- **`DINOv3` (Meta)** — used as the image conditioner. Commercial use **is** permitted and
  redistribution **is** permitted, with a *"Built with DINOv3"* attribution requirement,
  no MAU threshold and no territorial exclusion. But the weights are **gated** on Hugging
  Face: a user account and accepted terms are required for the first download.

A near-identical set of questions was raised against another model in this family
(`TencentARC/Pixal3D` issue #33: MIT repo vs. restrictive weights, DINOv3 gating,
nvdiffrast, RMBG, EU) and, as of this research date, **received no maintainer answer**.
The pattern — permissive headline licence, restrictive dependency underneath — is
systemic in this field and is the reason `docs/licenses.md` exists as a separate,
per-artifact inventory.

Note the pleasant consequence: replacing `nvdiffrast` with `mtldiffrast` and RMBG-2.0
with BiRefNet is not only how you reach Apple Silicon, it is also how you reach a
commercially clean pipeline. **The macOS path is the licence-clean path.**

---

## 5. Generation vs. reconstruction (spec §58)

The research confirms the spec's distinction is not academic:

- **Generation** (1 image → plausible geometry): TRELLIS.2, Hunyuan3D, SF3D, TripoSR.
  Sparse-voxel/latent diffusion. Unseen surfaces are *invented*, plausibly and
  convincingly. Officially single-image.
- **Reconstruction** (N images → consistent geometry): VGGT, MASt3R/DUSt3R,
  photogrammetry. Outputs poses, depth and point clouds — **no textured mesh** — and
  needs a separate meshing and texturing stage.

They are not two settings of one model. TRELLIS.2's own issue tracker reports multi-image
conditioning performing *worse* than single-image. Therefore:

- V1 ships **generation**, single image, and says so plainly in the UI (spec §13).
- "Multi-image" in V1 means: accept a set, analyse it, help the user **choose the best
  frame**, and use that — honestly labelled. It must not be presented as reconstruction.
- True multi-image reconstruction is a P1/P2 track with its own provider capability flag
  (`MULTI_IMAGE`) and its own licence review.

This directly satisfies spec §14's requirement not to simulate multi-image support.

---

## 6. Summary of what fits 16 GB

| Model | Peak memory | Fits 16 GB M1 Pro? |
|---|---|---|
| TRELLIS.2 (via `trellis-mac`) | ~18 GB, 24 GB recommended | **No** — will swap; integration target, not daily driver |
| Hunyuan3D-2.1 shape-only | ~6 GB | Yes — but EU-excluded licence |
| Hunyuan3D-2.1 shape + texture | ~16 GB | Marginal — and EU-excluded |
| SF3D | ~6 GB | Yes on paper; upstream advises CPU below 32 GB unified |
| TripoSR | small | **Yes**, comfortably, including CPU |
| VGGT | not measured here | Point cloud only, no mesh |

The uncomfortable conclusion: **the best model does not fit the development machine, and
the models that fit have licence or quality problems.** The architecture must therefore
treat "which provider can this machine actually run" as a first-class runtime decision
(spec §7), not as a deployment detail. It also means the project's own CI and daily
development cannot depend on the flagship model.

---

## 7. Secondary sources that were wrong

Recorded because it justifies the primary-source rule and will save the next contributor
time:

- Multiple 2026 listicles claim **TripoSR generates PBR textures**. It does not; it
  produces vertex colours.
- Several sites present **"Hunyuan3D v3.1 Pro"** as an available open model. No such
  open-weight release exists; the Tencent-Hunyuan org's newest open image-to-3D repo is
  **2.1**. Those pages are commercial API front-ends.
- A search result asserted TRELLIS.2 "has been ported to Apple Silicon" without
  qualification. True, but by an unaffiliated community fork with a 24 GB recommendation
  and disabled hole-filling — material facts the summary omitted.
- Aggregator pages describe Hunyuan3D-2.1 as permissively licensed for commercial
  distribution while omitting the EU/UK/South Korea territorial exclusion entirely.

---

## Sources

Primary:
- https://github.com/microsoft/TRELLIS.2 · https://github.com/microsoft/TRELLIS
- https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1 (+ its `LICENSE`)
- https://github.com/Stability-AI/stable-fast-3d
- https://github.com/facebookresearch/vggt
- https://github.com/shivampkumar/trellis-mac (+ issue tracker)
- https://github.com/pedronaugusto/trellis2-apple
- https://github.com/pwilkin/trellis.cpp (+ issue tracker)
- https://github.com/NVlabs/nvdiffrast/blob/main/LICENSE.txt
- https://ai.meta.com/resources/models-and-libraries/dinov3-license/
- https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m
- https://huggingface.co/briaai/RMBG-2.0
- https://github.com/TencentARC/Pixal3D/issues/33
