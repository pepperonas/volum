# Licence Inventory

Established: **2026-08-30**; Metal packages re-verified **2026-09-03**. Re-verify before
every release.

**This is not legal advice.** It is a record of what the licence texts say, quoted, with
the date they were read and a link to the source. Where a term is unclear the entry says
**UNKNOWN** rather than guessing (spec §10). Nothing here asserts that a model is
commercially usable unless the licence says so unambiguously.

Rule for this repository: **no model weights are committed, ever.** Weights are fetched
at the user's explicit request by the Model Manager (spec §12), into the VOLUM data
directory, never into the source tree.

---

## 1. Why this file is separate from the model evaluation

A model's headline licence is routinely *not* the binding constraint. Twice in this
survey a permissively licensed model turned out to have a restrictive dependency inside
its own official pipeline. The relevant question for VOLUM is never "what licence is the
model?" but **"what is the licence of the most restrictive artifact required to produce
one asset?"**

For the official TRELLIS.2 pipeline the answer is `nvdiffrast`: non-commercial. The MIT
model licence does not rescue it.

---

## 2. Per-artifact inventory

### TRELLIS.2 — Microsoft

| Field | Value |
|---|---|
| Repository | https://github.com/microsoft/TRELLIS.2 |
| Weights | `microsoft/TRELLIS.2-4B` (Hugging Face) |
| Code licence | MIT |
| Weights licence | **MIT** |
| Commercial use | Permitted **for the model**. See dependency chain below. |
| Territorial restriction | None |
| Attribution required | MIT notice |
| Redistribution of weights | Permitted by MIT — VOLUM still will not redistribute them |
| Notes | Upstream states dependencies `nvdiffrast` and `nvdiffrec` carry their own licences |

### TRELLIS (v1) — Microsoft
MIT, code and models. Same `nvdiffrast` dependency caveat. Not planned for integration.

### Hunyuan3D-2.1 — Tencent

| Field | Value |
|---|---|
| Repository | https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1 |
| Licence | Tencent Hunyuan 3D 2.1 Community License |
| Commercial use | Permitted below 1M MAU; above it, a separate Tencent licence is required |
| **Territorial restriction** | **Yes.** Quoted: *"'Territory' shall mean the worldwide territory, excluding the territory of the European Union, United Kingdom and South Korea."* |
| MAU threshold | Quoted: *"If, on the Tencent Hunyuan 3D 2.1 version release date, the monthly active users of all products or services made available by or for Licensee is greater than 1 million monthly active users in the preceding calendar month, You must request a license from Tencent"* |
| Output ownership | Quoted: *"Tencent claims no rights in Outputs You generate."* |
| Attribution required | Yes — the notice *"Tencent Hunyuan 3D 2.1 is licensed under the Tencent Hunyuan 3D 2.1 Community License Agreement, Copyright © 2025 Tencent. All Rights Reserved."*, plus prominent disclosure of the actual provider and a statement that Tencent does not endorse the service |

**VOLUM decision: not bundled, not offered as a built-in provider.**

VOLUM is developed in the European Union. The licence grants rights in a Territory that
excludes the EU. Whether an EU-based developer may build on, distribute, or ship a
provider for these weights is exactly the kind of question this file refuses to answer by
assumption. The conservative reading is that they may not, and the conservative reading
is what an open-source project should follow when the downside is imposed on its users.

This is a *licensing* decision, not a technical judgement. Hunyuan3D-2.1 is technically
one of the best fits for consumer hardware in this survey — it is the only strong model
whose shape-only mode fits comfortably in 16 GB. It remains documented in
`docs/model-evaluation.md` so the decision is transparent and revisitable if Tencent
changes the terms.

### Stable Fast 3D (SF3D) — Stability AI

| Field | Value |
|---|---|
| Repository | https://github.com/Stability-AI/stable-fast-3d |
| Licence | Stability AI Community License (`stabilityai-ai-community`) |
| Commercial use | ⚠️ Permitted only for entities under **USD 1M annual revenue**; above that an enterprise licence from Stability AI is required |
| Territorial restriction | None found |
| Notes | Revenue-conditional licences are a poor fit for a bundled default in an open-source tool, because the *user's* revenue determines legality, not the project's. If integrated, VOLUM must surface this in the Model Manager before installation. |

### TripoSR

| Field | Value |
|---|---|
| Licence | **MIT**, code and weights |
| Commercial use | ✅ Unrestricted |
| Territorial restriction | None |
| Notes | The only model in this survey with no conditions attached at any layer. This is why it is the bring-up provider. |

### VGGT — Meta

| Field | Value |
|---|---|
| Repository | https://github.com/facebookresearch/vggt |
| Default checkpoint (`VGGT-1B`) | **Non-commercial** |
| Commercial checkpoint | `VGGT-1B-Commercial` — commercial use permitted, military excluded, **application/approval required** |
| Notes | Any VOLUM multi-image reconstruction track must default to the commercial checkpoint and make the approval requirement explicit. Apple Silicon ports (`vggt-mps`, MLX MASt3R) have **UNKNOWN** licences — verify before use. |

---

## 3. Dependency licences — the binding constraints

### `nvdiffrast` — NVlabs ❌ **NON-COMMERCIAL**

| Field | Value |
|---|---|
| Licence | Nvidia Source Code License (1-Way Commercial) |
| Key clause | Quoted: *"The Work and any derivative works thereof only may be used or intended for use non-commercially."* — where *non-commercially* means *"research or evaluation purposes only and not for any direct or indirect monetary gain"* |
| Used by | TRELLIS.2, TRELLIS v1 — differentiable rasterisation for texture baking |

**This is the most consequential licence in the entire project.** It means the official,
CUDA, Linux TRELLIS.2 pipeline cannot be shipped as a commercially usable feature, MIT
model weights notwithstanding.

**Mitigation, and it is a good one:** the Apple Silicon port replaces `nvdiffrast` with
`mtldiffrast` (a Metal implementation) plus a pure-Python fallback. If that replacement
is independently implemented and permissively licensed, the macOS path is *both* the only
one that runs on a Mac *and* the only one free of this restriction.

**Verified 2026-09-03: the replacement is clean.** All four Metal packages are **MIT**,
read from their own licence files:

| Package | File | Holder |
|---|---|---|
| `mtldiffrast` | `LICENSE.txt` | Pedro Augusto, 2025 |
| `mtlbvh` | `LICENSE.txt` | 2026 |
| `mtlgemm` | `LICENSE` | Jianfeng Xiang, 2025 — TRELLIS's own author |
| `mtlmesh` | `LICENSE` | Jianfeng Xiang, 2025 |

⚠️ Two traps in checking this. The file is `LICENSE.txt` in two of the four repositories,
so looking only for `LICENSE` reports "no licence" for a repository that has one. And
`mtlgemm`/`mtlmesh` are not by the same author as `mtldiffrast` — assuming one holder for
"the mtl\* packages" would misattribute two of them.

**`mtldiffrast` is not a derivative of `nvdiffrast`.** It is implemented from Laine et al.
(2020), the paper, and is only *API-compatible* with NVIDIA's library. API compatibility
is not derivation, so NVIDIA's non-commercial clause does not reach the Apple Silicon path.

The registry reflects this: TRELLIS.2 is `CommercialUse.CONDITIONAL`, not `UNKNOWN`. The
three conditions are the CUDA path's exclusion, the DINOv3 attribution, and the BiRefNet
substitution — not this.

### `RMBG-2.0` — BriaAI ❌ **NON-COMMERCIAL**

| Field | Value |
|---|---|
| Licence | CC BY-NC 4.0; commercial use requires an agreement with BRIA |
| Used for | Background removal |
| **Replacement** | **BiRefNet — MIT.** RMBG-2.0 is built on the BiRefNet architecture; the original BiRefNet weights are MIT throughout. `trellis.cpp` already uses BiRefNet. |

**VOLUM decision: BiRefNet only. RMBG-2.0 is not shipped and not offered.** Where a
model port pulls RMBG-2.0 by default, VOLUM substitutes BiRefNet in its own preprocessing
stage (spec §16) rather than accepting the port's default.

### `BiRefNet` — ZhengPeng7 ✅ MIT — **shipped**

| Field | Value |
|---|---|
| Licence | MIT License, "Copyright (c) 2024 ZhengPeng" |
| Read from | `LICENSE` at github.com/ZhengPeng7/BiRefNet, 2026-10-25 |
| Weights | `onnx-community/BiRefNet_lite-ONNX`, card declares `license: mit`, `base_model: ZhengPeng7/BiRefNet_lite` |
| Revision | `de15b22ba131738a16dff04aab8bdf8dc32e3ac1` (pinned) |
| Runtime | `onnxruntime` — MIT (PyPI classifier *License :: OSI Approved :: MIT License*) |
| Commercial use | **allowed**, no conditions, no territorial restriction, no attribution requirement |

This is the whole chain for VOLUM's background-removal stage, and all of it is MIT. Worth
stating plainly because the easy path is not: most background-removal code reaches for
RMBG-2.0 by default, which is the non-commercial entry directly above.

⚠️ **The ONNX repository has no `LICENSE` file**, only the `license: mit` tag in its model
card. The tag is a declaration rather than a text, which is why the upstream licence was
read as well — and why the chain is recorded as two rows rather than one.

### `Pillow` ✅ MIT-CMU — **shipped, a direct dependency**

| Field | Value |
|---|---|
| Licence | MIT-CMU (the "open source MIT-CMU License" named in its own text) |
| Read from | `pillow-13.0.0.dist-info/licenses/LICENSE` and `License-Expression: MIT-CMU` in its metadata, 2026-10-25 |
| Used for | Decoding the user's own images: frame measurement and the EXIF orientation fix |
| Commercial use | **allowed**, no conditions, no territorial restriction |

Named here because it moved from an optional extra to a **core dependency** when frame
selection was built, and a dependency that ships inside the application bundle is one
whose licence has to be read rather than assumed. The permission is broad — *"to use,
copy, modify and distribute this software and its documentation for any purpose and
without fee"* — with the usual attribution and no-endorsement conditions.

⚠️ The `LICENSE` file bundles the licences of **22 vendored C libraries** after Pillow's
own, under `----` separators: AOM, BROTLI, BZIP2, DAV1D, FREETYPE2, HARFBUZZ, LCMS2,
LIBAVIF, LIBJPEG, LIBLZMA, LIBPNG, LIBTIFF, LIBWEBP, LIBYUV, OPENJPEG, RAQM, TCL_TK, XAU,
XCB, XDMCP, ZLIB, ZSTD. Reading the first paragraphs is right for Pillow itself, and the
MIT-CMU grant above is what covers VOLUM's use of the library.

**The 22 are `UNKNOWN` here** — they have not been read, and they are not all the same
licence (FREETYPE2 alone is dual-licensed). They ship inside the wheel that ships inside
the application bundle, so a redistribution audit has to read them; nothing in this entry
should be taken as having done that.

### `DINOv3` — Meta ⚠️ usable, gated

| Field | Value |
|---|---|
| Licence | DINOv3 License |
| Commercial use | ✅ Permitted — *"a non-exclusive, worldwide, non-transferable and royalty-free limited license"* |
| Redistribution | ✅ Permitted, provided a copy of the agreement accompanies the materials |
| Attribution | ✅ **Required** — must prominently display *"Built with DINOv3"* |
| MAU threshold | None |
| Territorial restriction | None (different Meta contracting entity for EEA/Switzerland) |
| Other | Trade-control compliance; no ITAR / military or warfare end uses |
| **Gating** | ⚠️ **Yes.** The weights are gated on Hugging Face: an account and accepted terms are required for the first download. |

Two product consequences:

1. VOLUM's UI must carry **"Built with DINOv3"** wherever the attribution requirement
   applies. Tracked as a V1 requirement, not a nicety.
2. The Model Manager must support **authenticated, gated Hugging Face downloads** — a
   user-supplied token, a clear explanation of why it is needed, and a failure message
   that says "you need to accept Meta's terms on this page" rather than a 401. This is a
   real architectural requirement flowing directly from a licence.

### `nvdiffrec`, `flexgemm`, `o-voxel`, `cumesh`, `spconv`, `flash-attn`, `kaolin`

**UNKNOWN** individually — not yet verified. Most are irrelevant to the macOS path
because it replaces them, which is precisely why the macOS path is preferred. Each must
be checked before any CUDA provider is shipped.

---

## 4. Summary

| Artifact | Commercial? | Blocking? |
|---|---|---|
| TRELLIS.2 weights (MIT) | ✅ | no |
| TripoSR (MIT) | ✅ | no |
| BiRefNet (MIT) | ✅ | no |
| DINOv3 | ✅ with attribution | no — but gated download is an architectural requirement |
| `mtldiffrast` / `mtlgemm` / `mtlbvh` / `mtlmesh` (MIT) | ✅ | no — verified 2026-09-03 |
| SF3D | ⚠️ under USD 1M revenue | conditional — surface to the user |
| **`nvdiffrast`** | ❌ | **blocks the official CUDA pipeline** |
| **`RMBG-2.0`** | ❌ | avoidable — use BiRefNet |
| **Hunyuan3D-2.1** | ⚠️ | **EU excluded — not bundled** |
| VGGT default checkpoint | ❌ | use the commercial checkpoint, by application |

**Standing rule.** Before any release, and before any statement that VOLUM output may be
used commercially, re-verify this table and record the date. If a required artifact's
terms are unclear, the answer in the UI is "unclear — see licence", never "yes".
