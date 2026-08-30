# Integration Notes

Findings that cost time to establish and would cost the same again. Each was
verified on the machine, not inferred. Dated, because several are version-bound.

---

## PyTorch MPS on macOS 26 works — measured 2026-08-30

Public issue trackers report MPS as built-but-unavailable on macOS 26 (Tahoe)
with PyTorch 2.9.1 and 2.10 nightlies, which would have blocked the entire Apple
Silicon plan. It does not apply to current PyTorch:

```
macOS 26.6.2 arm64 · torch 2.13.0
torch.backends.mps.is_built()     True
torch.backends.mps.is_available() True
matmul on device="mps"            OK
```

Before concluding a platform is unsupported, install the current version and ask
it. The bug was real and is fixed; the issue text outlives the defect.

## transformers must stay below 5.x for TripoSR

Upstream pins `transformers==4.35.0`. That pin is **load-bearing**, not
incidental housekeeping. transformers 5.x renamed the ViT internals:

```
old  image_tokenizer.model.encoder.layer.N.attention.attention.query.weight
new  image_tokenizer.model.layers.N.attention.q_proj.weight
```

The published checkpoint therefore fails to load with several hundred missing
keys. `transformers==4.35.0` itself no longer resolves against a current
interpreter, so the install spec uses the widest range that still has the old
layout: `>=4.35,<4.50`.

**General lesson.** Unpinning a model's dependencies does not merely risk subtle
numerical drift — it can stop the weights loading at all. Where the spec cannot
carry the original pin, the install manifest records the versions that were
actually resolved, so a result stays explicable afterwards.

## rembg needs its `[cpu]` extra, and exits at import without it

`import rembg` with no onnxruntime backend prints installation advice and calls
`sys.exit()` — at import time. Two consequences:

1. The install spec asks for `rembg[cpu]`, not `rembg`.
2. `sys.exit()` raises `SystemExit`, which does **not** inherit from `Exception`.
   The worker's catch-all missed it and the process died without emitting a
   protocol line, which the engine could only report as "the process stopped".
   The worker now catches `SystemExit` separately.

`moderngl` and `xatlas` are *not* installed: they live in `bake_texture.py`,
which nothing on our path imports. `imageio` and `rembg` are imported at module
level in `tsr/utils.py` and so are unavoidable.

## torchmcubes: replaced, not compiled

TripoSR requires `torchmcubes`, a C++/CUDA extension built from a git checkout.
It has no wheels and does not build usefully on Apple Silicon. VOLUM installs a
compatibility module backed by **PyMCubes**, which ships an arm64 wheel.

This is a substitution in the provider's own environment, not a patch of
upstream: the cloned checkout stays exactly what its pinned commit says it is.

**The sign convention was verified twice, deliberately.** First against a known
sphere — a radius-10 sphere in a 48³ grid came back with mean radius 10.00.
That only proved the shim matched *my assumption* about torchmcubes, so it was
then traced through the real call chain:

```
extract_mesh   passes -(density - threshold)     negative inside
helper         negates again                     positive inside
mc_func        receives "above threshold = inside"
```

which is the convention the shim implements. Confirmed empirically: the
generated mesh has **positive volume**, so it is not inside out. An inverted
mesh looks plausible in a viewer and is exactly the kind of defect that survives
to a release.

## Ruff's first-party detection depends on the checkout

Lint passed locally and failed in CI on byte-identical files. Ruff infers
first-party packages from the filesystem, so a working copy containing a built
virtualenv classified `volum_core` as first-party while a clean checkout did not,
producing a different required import order.

Reproduced by cloning the pushed repository and linting that. Fixed by declaring
`known-first-party` explicitly. **Any lint rule that depends on filesystem
detection will drift between a developer's machine and CI; declare it.**

## GPU core count is unavailable on virtualised Apple Silicon

`hw.perflevel0.gpu_core_count` does not exist as a sysctl. The only source is
`system_profiler`, which costs about a second — hence the opt-in deep probe used
by the doctor but not at start-up, cached for the process lifetime.

CI then showed the second half: on a virtualised runner (`Apple M1 (Virtual)`)
`system_profiler` reports no core count at all. The value is cosmetic — nothing
gates on it — so the contract is "a positive number or nothing, never an
exception". A test that demanded a number was the wrong test.
