# The bundled engine runtime

This directory holds a relocatable CPython with the engine installed into it, plus the
`uv` binary the Model Manager builds provider environments with. It is built, not
committed — a few hundred megabytes of third-party binaries:

    python3 scripts/build_runtime.py

Why this file is here, alone, under version control: `tauri.conf.json` lists
`runtime/**/*` as a bundle resource, and Tauri's build script **fails outright** when a
resource glob matches nothing. Without something committed here, a fresh checkout could
not compile the shell at all — not even `pnpm tauri dev`, which never needed the runtime
in the first place.

An application bundled while this is all the directory holds will start and report that
it cannot find its engine. That is the correct failure and it says so plainly; see
`docs/packaging.md`.
