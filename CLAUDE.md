# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

**VOLUM** — a local-first, cross-platform image-to-3D desktop application (Tauri 2 +
React + Python engine). Public open-source project: https://github.com/pepperonas/volum

**Current state: the whole chain runs.** The desktop window starts, spawns its engine,
generates from a photograph and shows the result in a 3D viewer — verified end to end
(chair.png → 50 s on MPS → 41,864 vertices, watertight, STL at exactly 60.0 mm). Core,
CLI, HTTP engine, hardware detection, doctor, model manager, job system, provider
abstraction, TripoSR and TRELLIS.2 providers, validation, printable export, the shared
application service and the Tauri app are all in place.

**The macOS application is packaged and verified**: a 239 MB `.app` / 82 MB `.dmg` that
starts its engine from a bundled relocatable CPython with no development checkout in
sight. Windows and Linux bundles are wired into a release workflow and have never been
built.

Not done: signing (no certificates), updates, multi-image, and TRELLIS.2 — integrated and
licence-checked but never installed or run here (needs DINOv3 access and an idle
machine).

The full specification is `VOLUM_Masterprompt_Local_CrossPlatform.md` (2065 lines,
German) — it is the requirements document and takes precedence over this file on
questions of scope.

Read before doing anything: **`docs/decision.md`** (the approved technical plan) and
**`docs/licenses.md`** (why several obvious choices are unavailable).

## Commands

Working today:

```bash
# Engine (Python, uv)
cd engine && uv sync && uv run pytest                  # all tests
cd engine && uv run pytest tests/test_x.py::test_y     # one test
cd engine && uv run ruff check src tests && uv run --with mypy mypy
cd engine && uv run --with mypy mypy --platform win32   # catches Windows-only type errors

# Desktop app (Tauri shell + React window)
cd apps/desktop && pnpm install
cd apps/desktop && pnpm tauri dev                      # window + engine + Vite
cd apps/desktop && pnpm test && pnpm lint && pnpm typecheck
cd apps/desktop && pnpm build                          # ⚠️ cargo cannot compile without dist/
cd apps/desktop/src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings
cd apps/desktop/src-tauri && cargo test                # pure unit tests
cd apps/desktop/src-tauri && cargo test --test real_engine -- --ignored  # starts the real engine

# CLI (shares volum_core — never reimplement pipeline logic here)
uv run volum doctor                                    # what this machine can run
uv run volum models list | show <id> | disk
uv run volum models install triposr                    # ~2.5 GB, ~90 s
uv run volum generate image.png --seed 7               # real inference
uv run volum generate image.png --print --size-mm 60   # STL + 3MF, repaired, validated

# Packaging (docs/packaging.md)
python3 scripts/build_runtime.py                       # the bundled engine runtime, ~220 MB
cd apps/desktop && CI=true pnpm tauri build            # ⚠️ CI=true or the DMG step hangs on Finder

# HTTP engine (the Tauri sidecar) — needs a token, binds 127.0.0.1:<ephemeral>
export VOLUM_ENGINE_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
uv run volum-engine                                    # stdout: {"event":"listening","port":…}
```

Engine tests need `uv sync --extra dev --extra engine` (FastAPI, uvicorn, and `httpx2` —
Starlette 1.6 deprecated `httpx` for its test client). They `importorskip` without it.

**TripoSR on this machine is installed under `/Users/martin/volum-data`**, not the
platform default — run the CLI or the engine with `VOLUM_DATA_DIR=/Users/martin/volum-data`
to reach it. The example images live in `…/models/triposr/source/examples/`.

**Set `VOLUM_DATA_DIR` when developing.** It overrides everything else and keeps tests
and experiments out of the real user data directory.

**Python must be pinned to `>=3.11,<3.13`.** The machine's default `python3` is 3.14.7 and
the ML stack has no wheels for it. Let `uv` provision the interpreter; never inherit the
shell default.

`cmake` and `ninja` are **not installed** and are needed for Metal/native kernels:
`brew install cmake ninja`.

## Architecture — the load-bearing decisions

Full reasoning in `docs/decision.md` §4. The parts that are easy to break by accident:

- **One core, two front doors.** All pipeline logic lives in `engine/volum_core/`. The
  HTTP engine (`volum_engine`) and the CLI are both thin shells over it. Business logic in
  the CLI or in the Tauri layer is a bug (spec §32).
- **The engine is a Tauri sidecar** speaking HTTP + SSE on `127.0.0.1`, ephemeral port,
  per-session bearer token passed via environment, port announced on the child's stdout.
  Never bind a non-loopback interface; never use a fixed port; never leave the endpoint
  unauthenticated.
- **Every job runs in its own provider subprocess.** Not premature isolation: a killed
  Metal command buffer can take the process down, GPU kernels cannot be interrupted from
  Python (so `CANCELLED` is otherwise a lie), and unified memory must actually be returned
  to the OS.
- **Every provider gets its own `uv` virtualenv.** ML providers pin mutually incompatible
  Torch versions. A shared environment makes adding the second provider a dependency
  fight and makes `ModelManager.remove()` / `disk_usage()` dishonest.
- **Provider capabilities and requirements are declared data**, not branches in the
  pipeline. The doctor computes availability from hardware facts.
- **Orchestration lives in `volum_core.service.VolumService`.** Device choice, format
  resolution, input staging, scheduling and cancellation are decided there once; the CLI's
  `generate` and every engine route are a few lines over it. Only the engine passes
  `recover_interrupted=True` — a CLI command beside a live engine must not fail the
  engine's running jobs.
- **Two threads touch a running job.** `JobManager` keeps active records live so a cancel
  by id reaches the object the pipeline thread holds; SSE frames are snapshotted on the
  producer's thread (`model_copy(deep=True)`) or two stages collapse into one frame —
  seen live as `seq 7 → 9`. Cancel is SIGTERM then SIGKILL after 5 s; a worker wedged in a
  Metal kernel ignores the first.
- **The window is granted a file picker and a save dialog and nothing else.** The engine is
  spawned by Rust with `std::process::Command` — not Tauri's shell plugin — and files are
  read and written by Rust. A permission added to `capabilities/default.json` is a
  decision, not a formality.
- **The shell holds the engine's stdin open on purpose.** Closing it is how
  `--exit-with-parent` fires. Dropping the handle stops the engine.
- **A non-loopback announcement is refused, `localhost` included.** A name is resolved
  through the machine's host file, which is not something to trust when deciding where to
  send a session token.
- **The bundle carries a real CPython, not a frozen binary.** The Model Manager builds a
  virtual environment per provider, and a frozen binary has no interpreter to build one
  from (ADR-0003). One interpreter runs the engine and bases the provider environments;
  `uv` ships beside it and the shell names it in `VOLUM_UV_BINARY`.

## Verify against a clean checkout, not your working copy

Twice in one session a check passed locally and failed in CI on identical files,
both times because the working copy carried artefacts a fresh checkout does not:

- **Ruff infers first-party packages from the filesystem.** A built virtualenv made
  `volum_core` first-party locally and third-party in CI, changing the required import
  order. Fixed by declaring `known-first-party`.
- **PEP 561.** Without `py.typed`, mypy resolved the *installed* package as untyped in
  CI while the source passed locally.

Before pushing anything structural: `git clone . /tmp/verify && cd /tmp/verify/engine &&
uv sync --extra dev --extra engine && uv run ruff check src tests && uv run --with mypy mypy
&& uv run pytest`. For the window, add `cd ../apps/desktop && pnpm install --frozen-lockfile
&& pnpm lint && pnpm typecheck && pnpm test && pnpm build && cd src-tauri && cargo clippy
--all-targets -- -D warnings && cargo test` — **`pnpm build` first**, because
`tauri::generate_context!` refuses to expand when `frontendDist` points at nothing, so a
fresh checkout cannot compile the shell at all until the frontend exists once.

**The suite is fenced off from the real user directories** by an autouse fixture in
`tests/conftest.py` that sets `VOLUM_CONFIG_DIR` and `VOLUM_DATA_DIR`. It exists because
a service test once saved a fake Hugging Face token into the developer's real
`settings.json`. `test_repo_hygiene.py` pins the fixture; keep it.

## Non-obvious constraints

- **Apple Silicon runtime is PyTorch MPS + Metal kernels — NOT MLX.** This deliberately
  contradicts spec §5. MLX has no image-to-3D ecosystem. See `docs/adr/0002`. Do not
  "fix" this back to MLX without new evidence.
- **The development machine (M1 Pro, 16 GB, ~29 GB free disk) cannot run the primary
  model.** TRELLIS.2 peaks at ~18 GB and its weights are ~15 GB. This is why TripoSR is a
  V1 provider: it is what makes the pipeline testable here and in CI. Do not remove it as
  "too old".
- **`nvdiffrast` is non-commercial** and sits inside the official TRELLIS.2 pipeline — the
  MIT model licence does not rescue it. The Metal replacement (`mtldiffrast`) is what makes
  the macOS path licence-clean. **Its own licence is currently UNKNOWN and is blocking for
  any commercial claim.**
- **Hunyuan3D-2.1 is excluded on licence grounds, not technical ones.** Its licence
  excludes the European Union, where this project is developed. It is otherwise one of the
  best fits for 16 GB machines. Do not add it back without a licence change.
- **RMBG-2.0 is CC BY-NC — use BiRefNet (MIT).** Model ports pull RMBG by default;
  substitute it in VOLUM's own preprocessing stage.
- **DINOv3 is gated** on Hugging Face (account + accepted terms) and requires a visible
  **"Built with DINOv3"** attribution in the UI. The Model Manager must handle authenticated
  gated downloads with a real explanation, not a bare 401.
- **Multi-image in V1 means frame selection, honestly labelled.** Generation models are
  single-image; TRELLIS.2's own tracker reports multi-image conditioning performing worse.
  `MULTI_IMAGE` stays `false` on every V1 provider. Real reconstruction (VGGT/MASt3R) is a
  separate model family with no mesh output — P1 at the earliest.
- **Reproducibility is per-machine only.** MPS and CUDA kernels differ and the Mac port
  substitutes attention, GEMM and rasterisation. Same seed + same input + different machine
  ⇒ different mesh. Record seed/version/runtime/input hash; do not promise more.
- **The data directory must be relocatable.** One provider plus its environment is ~20 GB
  of the free space here. Check free space before every install.
- **`.gitignore` directory rules must be anchored.** `jobs/` matched
  `engine/src/volum_core/jobs/` and silently erased a whole source package from the
  repository — and ruff honours `.gitignore`, so it went unlinted too. Guarded by
  `tests/test_repo_hygiene.py`, which asks **git** which directories hold tracked files;
  walking the disk counted `dist/` and `node_modules/` as source as soon as the desktop
  app had been built once. ⚠️ A pattern containing a slash is anchored to the directory
  holding the `.gitignore`, so `gen/schemas/` matches only at the root — the one under
  `apps/desktop/src-tauri` needs `**/gen/schemas/`.
- **TypeScript is pinned to 5.9, not 7.** No `typescript-eslint` release supports 7
  (`>=4.8.4 <6.1.0`), and a type checker whose results the linter cannot read is half a
  tool. There is also no jsdom: jsdom 30 pulls undici 8, which needs a newer Node than
  this project targets.
- **Look up dependency versions; do not guess them.** Four invented version numbers in one
  `package.json` cost an install cycle each.
- **`uv` refuses to install into its own managed Pythons** (`EXTERNALLY-MANAGED`).
  `build_runtime.py` removes that marker from its *copy*, which is a build artefact and no
  longer uv's to manage.
- **`numpy.testing` is public API, not a test directory.** Stripping directories named
  `testing` produced a runtime that installed cleanly and could not import scipy. Only
  `tests` and `__pycache__` are safe, and the build script verifies what it built.
- **On macOS the DMG needs `CI=true`.** `create-dmg` opens a Finder window it never
  closes, and Finder then dissents the unmount — `diskutil eject` names the process.
  `CI=true` makes Tauri pass `--skip-jenkins`.
- **Model dependency pins are load-bearing.** TripoSR's `transformers==4.35.0` is not
  housekeeping: transformers 5.x renamed the ViT internals, so the published checkpoint
  fails to load entirely. Where a pin cannot be carried, the install manifest records what
  was actually resolved. See `docs/integration-notes.md` for this and the rest —
  MPS on macOS 26, the rembg `[cpu]` extra and its `sys.exit()` at import, and the
  torchmcubes substitution.

## House rules

- **No fakes in the production path** (spec §37): no `fake.glb`, no invented progress
  percentages, no mock providers outside unit tests. If a model returns no real progress,
  show pipeline stages, not a fabricated number.
- **A failed poll is not an empty result.** A silent empty mesh must fail the job, not
  export as an asset — this failure mode is documented upstream.
- **Numbers on screen must say what they measure.** The quality report measures the mesh
  as the model produced it, in model units; the scale to millimetres happens on export.
  Labelling the report's dimensions "mm" because a print size was asked for read
  "1.1 × 0.6 × 0.6 mm" for a chair exported at 60 mm.
- **Never commit model weights.** `.gitignore` blocks the common extensions; the Model
  Manager downloads into the data directory.
- **Verify licences from the licence text, dated.** Secondary sources were wrong on
  several material points during Phase 1 (`docs/research.md` §7). Unclear ⇒ `UNKNOWN`,
  never an optimistic guess.
- Conventional Commits; Semantic Versioning with a single version source of truth
  propagated to `pyproject.toml`, `package.json`, `tauri.conf.json`, the CLI and git tags.
- Documentation is in English (public OSS project); the specification is German.
