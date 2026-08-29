# Contributing to VOLUM

Thanks for considering it. This document covers the things that are specific to VOLUM —
general open-source etiquette is assumed.

## Before you start

Read [`docs/decision.md`](docs/decision.md). It records why the architecture is what it
is, including several decisions that look wrong until you know the reason. If you are
about to change one of them, that document is where the counter-argument belongs.

## Ground rules that are not negotiable

These come from the project specification and are enforced in review:

1. **No fakes in the production path.** No placeholder GLB files, no invented progress
   percentages, no mock providers outside unit tests. If a model reports no real progress,
   show pipeline stages — not a number you made up.
2. **Never commit model weights.** They are downloaded on request into the user's data
   directory. `.gitignore` blocks the common extensions; do not work around it.
3. **Licences are verified from the licence text, with a date.** If a term is unclear the
   answer is `UNKNOWN`, never an optimistic guess. See [`docs/licenses.md`](docs/licenses.md).
4. **One core pipeline.** All logic lives in `engine/volum_core/`. The HTTP engine and the
   CLI are thin shells over it. Business logic in either is a bug.
5. **Honest hardware behaviour.** If a model cannot run on this machine, say so with the
   numbers. No crash loops, no silent CPU fallback of a GPU model.

## Development

```bash
# Engine
cd engine && uv sync && uv run pytest
cd engine && uv run ruff check . && uv run ruff format --check . && uv run mypy .

# Frontend
cd apps/desktop && pnpm install && pnpm test && pnpm lint

# Desktop app
cd apps/desktop && pnpm tauri dev
```

The engine pins Python `>=3.11,<3.13`; `uv` provisions it. Do not rely on your system
Python — recent versions have no wheels for the ML stack.

## Tests

New behaviour needs a test. Pure logic — hashing, validation, the job state machine,
provider selection — must be testable without a GPU and without downloading weights;
that is what keeps CI honest. Tests that need real inference belong in the GPU smoke
tests, which are optional in CI (`volum benchmark --smoke`).

## Commits and versions

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `perf:`,
`refactor:`, `test:`, `docs:`, `build:`, `ci:`, `chore:`.

[Semantic Versioning](https://semver.org/). The version has a single source of truth and
is propagated by script; do not edit version numbers in individual files by hand.

## Adding a model provider

Implement `ImageTo3DProvider` and register it. If that requires changes in more than a
couple of places, the abstraction is wrong — say so in the pull request instead of
working around it.

Every new provider needs an entry in [`docs/licenses.md`](docs/licenses.md) covering the
model **and its pipeline dependencies**. A permissive model licence does not imply a
permissive pipeline; this has already caught us twice.
