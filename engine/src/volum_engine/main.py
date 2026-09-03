"""``volum-engine``: start the sidecar.

The contract with the desktop shell (docs/architecture.md section 2):

1. The shell generates a session token and passes it in ``VOLUM_ENGINE_TOKEN``.
   Without one the engine refuses to start — there is no unauthenticated mode.
2. The engine binds ``127.0.0.1`` on an ephemeral port and announces it as one
   JSON line on stdout: ``{"event": "listening", "port": 54321, "pid": 4242}``.
   Everything else the engine says goes to stderr, so stdout stays parseable.
3. The shell polls ``/health`` with the token until it answers, then uses the API.
4. On exit the shell closes the engine's stdin (or sends SIGTERM); with
   ``--exit-with-parent`` the engine treats stdin closing as a stop signal, so a
   shell that crashes does not leave an orphaned engine holding the GPU.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from volum_core.version import __version__

TOKEN_ENV = "VOLUM_ENGINE_TOKEN"  # noqa: S105 - the variable *name*, not a secret
LOG_LEVEL_ENV = "VOLUM_LOG_LEVEL"
LOOPBACK = "127.0.0.1"

log = logging.getLogger("volum.engine")


class StartupError(RuntimeError):
    """The engine cannot start; the message says why, for a person."""


def resolve_token(environ: dict[str, str] | None = None) -> str:
    """The session token from the environment, validated."""
    from .auth import MIN_TOKEN_LENGTH  # noqa: PLC0415 - keeps this module import-light

    env = os.environ if environ is None else environ
    token = env.get(TOKEN_ENV, "").strip()
    if not token:
        raise StartupError(
            f"{TOKEN_ENV} is not set. The engine only runs with a per-session token; "
            "the desktop shell provides one. For a manual start:\n"
            f"  {TOKEN_ENV}=$(python3 -c 'import secrets;print(secrets.token_hex(32))') "
            "volum-engine"
        )
    if len(token) < MIN_TOKEN_LENGTH:
        raise StartupError(
            f"{TOKEN_ENV} is too short ({len(token)} characters); use at least {MIN_TOKEN_LENGTH}."
        )
    return token


def bind_loopback(port: int = 0) -> socket.socket:
    """A listening socket on the loopback interface only.

    Bound here rather than by the server so the chosen port is known — and can
    be announced — before the server starts accepting. Connections that arrive
    in between wait in the listen backlog; nothing is lost.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LOOPBACK, port))
    sock.listen(128)
    return sock


def announcement(port: int, *, pid: int | None = None) -> str:
    """The one line the shell parses. Stable; add fields, never rename them."""
    return json.dumps(
        {
            "event": "listening",
            "host": LOOPBACK,
            "port": port,
            "pid": os.getpid() if pid is None else pid,
            "version": __version__,
        }
    )


def watch_stdin(on_close: Callable[[], None]) -> threading.Thread:
    """Call ``on_close`` once stdin reaches EOF — the parent has gone away."""

    def wait() -> None:
        try:
            while sys.stdin.buffer.read(1024):
                pass
        except (OSError, ValueError):
            pass
        on_close()

    thread = threading.Thread(target=wait, name="volum-parent-watch", daemon=True)
    thread.start()
    return thread


def _configure_logging() -> None:
    level = os.environ.get(LOG_LEVEL_ENV, "INFO").upper()
    # stderr, deliberately: stdout carries the announcement and must stay clean.
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="volum-engine",
        description="VOLUM's local engine. Binds 127.0.0.1 only; needs VOLUM_ENGINE_TOKEN.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind on 127.0.0.1. Default 0 = let the OS choose; the choice is "
        "announced on stdout.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the data directory for this run (otherwise settings/VOLUM_DATA_DIR).",
    )
    parser.add_argument(
        "--exit-with-parent",
        action="store_true",
        help="Stop when stdin closes, i.e. when the process that started the engine is gone.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging()

    try:
        token = resolve_token()
    except StartupError as error:
        print(f"volum-engine: {error}", file=sys.stderr)
        return 2

    # Heavy imports after the cheap checks, so a misconfigured start fails fast.
    import uvicorn  # noqa: PLC0415

    from volum_core.service import VolumService  # noqa: PLC0415

    from .app import create_app  # noqa: PLC0415

    service = VolumService(data_dir=args.data_dir, recover_interrupted=True)
    app = create_app(service, token=token)

    sock = bind_loopback(args.port)
    port = sock.getsockname()[1]

    config = uvicorn.Config(
        app,
        log_level=os.environ.get(LOG_LEVEL_ENV, "info").lower(),
        # The engine's own log goes to stderr; uvicorn's access log would only
        # repeat every poll the UI makes.
        access_log=False,
        # Uvicorn's default handlers write to stdout for some records, which
        # would corrupt the announcement channel. Route everything to stderr.
        log_config=None,
    )
    server = uvicorn.Server(config)

    if args.exit_with_parent:

        def stop() -> None:
            log.info("stdin closed — the parent is gone, stopping")
            server.should_exit = True

        watch_stdin(stop)

    log.info(
        "VOLUM engine %s listening on %s:%s (data: %s)",
        __version__,
        LOOPBACK,
        port,
        service.data_dir,
    )
    print(announcement(port), flush=True)

    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
        service.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
