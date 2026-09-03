"""Start-up contract of the sidecar process.

These are the guarantees the desktop shell builds on: a token is mandatory,
the port is announced as one JSON line on stdout, and the server binds loopback
only. The last test launches the real process.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import pytest

from volum_engine.main import (
    LOOPBACK,
    TOKEN_ENV,
    StartupError,
    announcement,
    bind_loopback,
    parse_args,
    resolve_token,
)

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")


def test_a_missing_token_is_refused_with_instructions() -> None:
    with pytest.raises(StartupError) as excinfo:
        resolve_token({})
    assert TOKEN_ENV in str(excinfo.value)
    assert "volum-engine" in str(excinfo.value)


def test_a_short_token_is_refused() -> None:
    with pytest.raises(StartupError, match="too short"):
        resolve_token({TOKEN_ENV: "abc"})


def test_a_proper_token_is_returned_stripped() -> None:
    token = secrets.token_hex(32)
    assert resolve_token({TOKEN_ENV: f" {token}\n"}) == token


def test_the_socket_is_loopback_only_on_an_ephemeral_port() -> None:
    sock = bind_loopback()
    try:
        host, port = sock.getsockname()
        assert host == LOOPBACK
        assert port > 0
    finally:
        sock.close()


def test_the_announcement_is_one_parseable_line() -> None:
    line = announcement(54321, pid=7)
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed == {
        "event": "listening",
        "host": LOOPBACK,
        "port": 54321,
        "pid": 7,
        "version": parsed["version"],
    }
    assert parsed["version"]


def test_defaults_are_ephemeral_and_not_exit_with_parent() -> None:
    args = parse_args([])
    assert args.port == 0
    assert args.exit_with_parent is False
    assert args.data_dir is None


@pytest.mark.slow
def test_the_real_process_announces_then_answers_health(tmp_path: Path) -> None:
    """Launch the engine as the shell would, read the port, hit /health, stop it
    by closing stdin."""
    import httpx2 as httpx  # noqa: PLC0415 - test-only client

    token = secrets.token_hex(32)
    env = {**os.environ, TOKEN_ENV: token, "VOLUM_DATA_DIR": str(tmp_path / "data")}
    process = subprocess.Popen(
        [sys.executable, "-m", "volum_engine.main", "--exit-with-parent"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        line = process.stdout.readline()
        assert line, process.stderr.read() if process.stderr else ""
        parsed = json.loads(line)
        assert parsed["event"] == "listening"
        assert parsed["host"] == LOOPBACK
        port = parsed["port"]

        base = f"http://{LOOPBACK}:{port}"
        deadline = time.monotonic() + 20
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get(
                    f"{base}/health", headers={"Authorization": f"Bearer {token}"}, timeout=2
                )
                break
            except httpx.HTTPError as error:  # not yet accepting
                last = error
                time.sleep(0.1)
        else:
            raise AssertionError(f"engine never answered: {last}")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert httpx.get(f"{base}/health", timeout=2).status_code == 401

        # Closing stdin is how a vanished parent looks from inside the engine.
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=20)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
