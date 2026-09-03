# VOLUM engine

The Python core (`volum_core`), the CLI (`volum`) and the local HTTP engine
(`volum-engine`). Architecture and API: `../docs/architecture.md`.

```bash
uv sync --extra dev --extra engine
uv run pytest
uv run volum doctor
```

## Running the engine by hand

The engine is normally started by the desktop shell. To start it yourself:

```bash
export VOLUM_ENGINE_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
uv run volum-engine                 # prints {"event":"listening","port":…} on stdout
curl -H "Authorization: Bearer $VOLUM_ENGINE_TOKEN" http://127.0.0.1:<port>/health
```

It binds `127.0.0.1` only, on a port the OS chooses, and answers nothing without the token.
`--exit-with-parent` makes it stop when stdin closes; `--data-dir` overrides the data
directory for one run (`VOLUM_DATA_DIR` does the same for the CLI and the tests).
