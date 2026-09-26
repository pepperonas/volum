# ADR 0003 — Package a relocatable CPython, not a frozen binary

Date: 2026-09-26 · Status: **Accepted**

## Context

The bundle has to carry the engine: `volum_core`, `volum_engine`, the CLI and their pure
dependencies (pydantic, FastAPI, uvicorn, trimesh, numpy, scipy, manifold3d, PyMCubes).
The models do not go in it — that was settled as "thin installer, fat data directory"
(`docs/decision.md` §4) and holds: one provider plus its environment is gigabytes.

Tauri's usual answer for a non-Rust helper is `externalBin`, which wants exactly one
executable file, so the obvious route is to freeze the engine with PyInstaller or Nuitka.

There is a second requirement that route does not meet. The Model Manager does not merely
*run* Python — it **builds virtual environments** with `uv`, one per provider, because ML
providers pin mutually incompatible Torch versions (`docs/decision.md` §4). A frozen
binary is an engine and nothing else: it has no interpreter that `uv venv` can base an
environment on. Taking that route would mean shipping a real interpreter anyway, next to
the frozen one, to do the job the frozen one cannot.

The third consideration is what "first run" may require. Shipping only `uv` and letting it
download a Python on first launch produces the smallest installer, but an application that
cannot start its own engine without a network is not local-first in any sense the user
would recognise. Downloading *weights* on demand is a size decision the user makes;
downloading the *runtime* is the application failing to be installed.

## Decision

**Bundle a relocatable CPython with the engine installed into it, and bundle `uv`
alongside it.**

- The interpreter is an [astral-sh/python-build-standalone] distribution, the same
  artefact `uv python install` places on disk. It is relocatable by construction — on
  macOS the binary links `@executable_path/../lib/libpython3.11.dylib`, so the tree works
  wherever the bundle is opened.
- The engine is installed into that tree's `site-packages`; it is started as
  `python -m volum_engine.main`, not through a console script, because a console script
  carries an absolute shebang written at install time.
- The tree ships as a Tauri **resource**, not as `externalBin`: it is a directory, and
  `externalBin` is for single files.
- `uv` ships beside it, and the shell tells the engine where it is
  (`VOLUM_UV_BINARY`). Without that the Model Manager would look for `uv` on `PATH`,
  which a bundled application has no business relying on.

One interpreter therefore serves both jobs: it runs the engine, and it is the base that
provider environments are built from.

## Consequences

- **The installer is around 200 MB** and the application works offline the moment it is
  installed: doctor, the model catalogue with its licence chain, the library and the
  viewer all run with no network at all. The first *generation* still needs a download,
  which is the same bargain as before.
- **No freezing step, so no freezing problems.** numpy, scipy and trimesh load from a
  normal `site-packages`; there are no hidden-import hooks to maintain and no
  frozen-binary signing quirks.
- **The engine stays runnable by hand** inside the bundle, which matters for support: a
  user can be asked to run the CLI from the installed application.
- **`.dist-info` directories are kept.** They carry the licence texts of everything
  shipped, and this is a project that takes licence provenance seriously.
- **A build machine per platform.** The runtime is built from wheels for its own platform,
  so macOS, Windows and Linux bundles are produced on their own runners. That is a CI
  arrangement, not an architectural cost.
- **Update size.** Every application update ships the whole runtime again. Tauri's
  updater does not do binary diffs, so a patch release is a ~200 MB download. Acceptable
  for now; if it stops being so, the runtime becomes a separately versioned resource.
- **Reversible.** The shell resolves where the engine comes from in one function
  (`engine::resolve_command`). Swapping in a frozen binary later changes that function
  and the build script, nothing else.

[astral-sh/python-build-standalone]: https://github.com/astral-sh/python-build-standalone
