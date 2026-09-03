"""Local HTTP engine — the Tauri sidecar.

HTTP + SSE on ``127.0.0.1`` only, on an ephemeral port announced on stdout, and
every request authenticated with a per-session bearer token passed in through
the environment. See ``docs/architecture.md`` sections 2 and 3.
"""

from .app import create_app

__all__ = ["create_app"]
