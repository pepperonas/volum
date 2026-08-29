# Phase 0 — Repository & Environment Audit

Date: 2026-08-30
Machine: primary development machine (Apple Silicon)

## 1. Repository state

The working directory `/Users/martin/claude/volum` contained **no code**:

```
VOLUM_Masterprompt_Local_CrossPlatform.md   (spec, 2065 lines)
temp/                                        (empty)
```

- Not a git repository before this audit (`git init` run as part of Phase 0; branch `main`).
- No `package.json`, `pyproject.toml`, `Cargo.toml`, `Dockerfile`, tests, CI config or `README`.
- **No `pepperonas/volum` repository exists on GitHub** (`gh repo view pepperonas/volum` → *Could not resolve to a Repository*).
- No pre-existing repo in the account matches `volum`, `3d`, `mesh` or `model`.

**Conclusion: greenfield.** Nothing to preserve, nothing to migrate, no existing code at risk of being overwritten (spec §61).

## 2. Hardware

| Property | Value |
|---|---|
| Model | Apple **M1 Pro** |
| Architecture | `arm64` |
| CPU | 10 cores (8 performance + 2 efficiency) |
| GPU | Apple M1 Pro, **16 cores**, Metal 4 |
| Unified memory | **16 GB** |
| OS | macOS 26.6.2 (build 25G83) |
| Free disk | **29 GB** of 460 GB |

Two deviations from the assumption in the spec (which says "Apple M1"):

- The machine is an **M1 Pro with 16 GB**, not a base M1. Good news for GPU throughput (16 GPU cores).
- **16 GB unified memory and 29 GB free disk are the binding constraints of this project.** See `docs/decision.md` §Hardware risks. This is not a footnote: the leading model of this class needs ~18 GB at peak and ~15 GB of weights on disk.

## 3. Toolchain present

| Tool | Version | Note |
|---|---|---|
| Rust / Cargo | 1.98.0 | host `aarch64-apple-darwin`; target `x86_64-pc-windows-gnu` also installed |
| Node | v20.19.5 | npm 10.8.2, pnpm 10.2.1 |
| Python | 3.14.7 (Homebrew default) | 3.13, 3.12, **3.11.12** also available via `uv` |
| uv | 0.7.15 | |
| git / gh | 2.50.1 / 2.75.1 | `gh` authenticated as `pepperonas`, scopes incl. `repo`, `workflow` |
| Blender | 5.0.0 | usable as an optional headless mesh/export backend |
| Docker | 28.5.1 | |
| ffmpeg | 8.1 | |
| pkg-config | 2.5.1 | |

**Missing and required later:**

- `cmake`, `ninja` — needed to build native/Metal kernels (`brew install cmake ninja`).
- Tauri CLI — not installed (`cargo tauri` unavailable).
- No ML runtime installed yet: no PyTorch, no MLX in any global environment.

**Python version trap:** the default `python3` is **3.14.7**. The ML stack (PyTorch, and every model port surveyed) does not reliably ship wheels for 3.14 yet. VOLUM must pin its engine interpreter — `requires-python = ">=3.11,<3.13"` — and let `uv` provision it, rather than inheriting the shell default. `uv` already has 3.11.12 on disk.

## 4. Consequences for Phase 1

The audit changes two questions that Phase 1 had to answer:

1. Not "which model is best?" but **"which model is best *and* runs in 16 GB unified memory?"** — those are different models.
2. Disk budget is real: a single 4B-parameter model plus a Torch environment is roughly 20 GB of the 29 GB free. The data directory (spec §27) must therefore be **relocatable to external storage from day one**, not a nice-to-have.
