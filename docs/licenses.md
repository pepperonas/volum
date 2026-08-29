# Licence Inventory

Established: **2026-08-30**. Re-verify before every release.

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

**Open action before any commercial claim:** verify the licence of `mtldiffrast`,
`mtlgemm`, `mtlbvh`, `mtlmesh` (Pedro Naugusto) — currently **UNKNOWN**. Until verified,
VOLUM makes no commercial-use claim about the TRELLIS.2 provider. See
`docs/decision.md` §Open questions.

### `RMBG-2.0` — BriaAI ❌ **NON-COMMERCIAL**

| Field | Value |
|---|---|
| Licence | CC BY-NC 4.0; commercial use requires an agreement with BRIA |
| Used for | Background removal |
| **Replacement** | **BiRefNet — MIT.** RMBG-2.0 is built on the BiRefNet architecture; the original BiRefNet weights are MIT throughout. `trellis.cpp` already uses BiRefNet. |

**VOLUM decision: BiRefNet only. RMBG-2.0 is not shipped and not offered.** Where a
model port pulls RMBG-2.0 by default, VOLUM substitutes BiRefNet in its own preprocessing
stage (spec §16) rather than accepting the port's default.

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
| `mtldiffrast` / `mtlgemm` et al. | **UNKNOWN** | **verify before any commercial claim** |
| SF3D | ⚠️ under USD 1M revenue | conditional — surface to the user |
| **`nvdiffrast`** | ❌ | **blocks the official CUDA pipeline** |
| **`RMBG-2.0`** | ❌ | avoidable — use BiRefNet |
| **Hunyuan3D-2.1** | ⚠️ | **EU excluded — not bundled** |
| VGGT default checkpoint | ❌ | use the commercial checkpoint, by application |

**Standing rule.** Before any release, and before any statement that VOLUM output may be
used commercially, re-verify this table and record the date. If a required artifact's
terms are unclear, the answer in the UI is "unclear — see licence", never "yes".
