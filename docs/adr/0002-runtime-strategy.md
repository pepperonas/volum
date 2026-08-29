# ADR 0002 — Apple Silicon runtime: PyTorch MPS + Metal kernels, not MLX

Date: 2026-08-30 · Status: **Proposed** · **Contradicts spec §5**

## Context

The spec sets the Apple Silicon priority as *"MLX / Metal"* with *"PyTorch MPS"* as the
fallback. The research does not support that ordering.

- MLX coverage is overwhelmingly LLM-oriented; there is no meaningful image-to-3D
  ecosystem on it.
- Every working Apple Silicon 3D pipeline surveyed uses **PyTorch MPS plus hand-written
  Metal kernels** for the operations MPS cannot express — sparse GEMM, differentiable
  rasterisation, BVH construction.
- The mature Apple port of TRELLIS.2 (`shivampkumar/trellis-mac`, ~470★, MIT) is MPS-based
  with Metal kernels. The MLX fork (`pedronaugusto/trellis2-apple`, ~36★, ~28 commits)
  documents neither its memory requirements nor which stages actually run on MLX.

## Decision

**PyTorch MPS + Metal kernels is the primary Apple Silicon runtime.** MLX is an optional,
experimental backend behind a flag, adopted only if it demonstrates a measured advantage.

The `Runtime` abstraction (MPS / CUDA / CPU) keeps this reversible: if `trellis2-apple`
matures, it becomes an alternative backend behind the same `ImageTo3DProvider`, not a
rewrite.

## Consequences

- VOLUM depends on PyTorch on macOS, so the Python engine and the "thin installer, fat
  data directory" packaging (`docs/decision.md` §5) are both required rather than optional.
- The Apple path inherits `trellis-mac`'s known limitations: hole-filling disabled,
  meshes pre-decimated ~800 K → ~200 K faces before Metal BVH construction, and a reported
  *silent empty mesh* under a killed Metal command buffer. The last one is why output
  validation is mandatory and providers run in isolated subprocesses.
- Replacing `nvdiffrast` with `mtldiffrast` also removes a **non-commercial** dependency —
  the Apple path is the licence-clean path, not merely the compatible one.

## Rejected

- **MLX first** — least mature layer of the stack for this domain; would put the product's
  primary path on a fork with 28 commits.
- **CoreML / ONNX** — no conversion path exists for any surveyed model. Dynamic shapes,
  sparse voxel operations and custom kernels are hostile to CoreML's fixed-graph model.
  Converting one is a research project, not an integration task. Revisit if Apple's
  tooling changes materially.
- **`trellis.cpp` (C++/GGML, MIT, GGUF-quantised, no Python at runtime)** — strategically
  the most interesting option, because quantisation is the real answer to consumer memory
  limits, and it would remove Python from the runtime entirely. It has CUDA, ROCm and
  Vulkan backends and **no Metal backend**, with no stated plans for one. GGML's Metal
  backend is mature upstream, so the gap is this project's custom ops. Tracked as the
  most promising future second runtime and a plausible upstream contribution.
