# VOLUM desktop

The window: a Tauri 2 shell (Rust) around a React + Three.js frontend. It holds no
pipeline logic — everything it shows comes from the engine over HTTP on loopback
(`../../docs/architecture.md` §3 and §3a).

```bash
pnpm install
pnpm tauri dev          # window + engine + Vite, all at once
pnpm tauri build        # a bundled application
```

Checks, all of which CI runs:

```bash
pnpm lint               # type-aware ESLint
pnpm typecheck
pnpm test               # Vitest
pnpm build              # ⚠️ before any cargo command — see below
cd src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
```

⚠️ **The Rust build needs `dist/` to exist.** `tauri::generate_context!` reads
`tauri.conf.json` at compile time and refuses to expand if `frontendDist` points at
nothing, so a fresh checkout cannot even run `cargo test` until the frontend has been
built once. `pnpm tauri dev` and `pnpm tauri build` handle this themselves; a bare cargo
command does not. An empty `dist/index.html` is enough, which is what CI creates.

## Running against a particular engine

`pnpm tauri dev` starts the engine out of `../../engine` with `uv`. To point it somewhere
else:

```bash
VOLUM_ENGINE_COMMAND='["uv","run","volum-engine"]' pnpm tauri dev
VOLUM_DATA_DIR=/Volumes/Fast/volum pnpm tauri dev      # models and jobs elsewhere
```

`VOLUM_ENGINE_COMMAND` is a **JSON array**, not a command line — whitespace splitting
breaks on any path with a space in it.

## The end-to-end shell test

The Rust unit tests are pure and always run. One test starts the *real* engine and checks
the whole handshake — announcement, token, and that closing stdin actually stops it. It
needs `uv` and a synced engine environment, so it is marked `#[ignore]` — the runner
then reports it as *ignored* rather than passed, and a skip can never be mistaken for a
proof:

```bash
cd src-tauri && cargo test --test real_engine -- --ignored --nocapture
```

## Things worth knowing before changing something

- **The window has no filesystem access.** `capabilities/default.json` grants a file
  picker and a save dialog — the choice of a *path*. Reading and writing happen in Rust.
  Adding a permission here is a decision, not a formality.
- **The engine is spawned by Rust**, never by the webview, and always with
  `--exit-with-parent`. The shell holds the child's stdin open on purpose: closing it is
  how the engine learns the window is gone.
- **TypeScript is pinned to 5.9**, not 7. No `typescript-eslint` release supports 7
  (`>=4.8.4 <6.1.0`), and a type checker whose results the linter cannot read is half a
  tool. Revisit when the linter catches up.
- **There is no jsdom.** jsdom 30 pulls undici 8, which needs a newer Node than this
  project targets. The tests are pure logic; a component test that needs a DOM should
  declare `// @vitest-environment happy-dom` per file.
- **Icons are generated, not drawn:** `src-tauri/icons/build-icons.py` computes the
  geometry and writes the PNG, ICO and ICNS set. Edit the script, not the output.
- **Dark only.** This is a tool whose main surface is a 3D viewport, and light chrome
  around a dark viewport fights the thing being looked at.

## Verifying a change in the real window

There is no automated UI test yet. What works today:

```bash
pnpm tauri dev
# find the window and capture only it — not the whole screen
python3 -c "from Quartz import *; print([(w['kCGWindowNumber'], w.get('kCGWindowName')) \
  for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) \
  if w.get('kCGWindowOwnerName') == 'volum-desktop'])"
screencapture -l<id> -o -x window.png
```

The frontend can also be driven in an ordinary browser against a real engine, which is how
the screens were checked: start an engine with a known token, open
`http://127.0.0.1:1420`, and define `window.__TAURI_INTERNALS__` with an `invoke` that
answers `engine_info` with that engine's address. The engine's CORS allow-list already
names this origin.
