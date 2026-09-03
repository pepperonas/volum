"""Per-session bearer token.

The desktop shell generates a token, hands it to the engine through the
environment, and sends it with every request. Nothing else is accepted: there
is no unauthenticated local endpoint, so a page in a browser that guesses the
port still gets nothing (docs/architecture.md section 2, spec section 50).
"""

from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse

from .errors import error_response

#: A token shorter than this is refused at start-up. The shell generates 64
#: hex characters; anything much shorter suggests a hand-typed or test value
#: leaking into a real run.
MIN_TOKEN_LENGTH = 32


def check_token(expected: str) -> None:
    if not expected or len(expected) < MIN_TOKEN_LENGTH:
        raise ValueError(
            f"The engine needs a session token of at least {MIN_TOKEN_LENGTH} characters."
        )


def presented_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()


def is_authorised(request: Request, expected: str) -> bool:
    presented = presented_token(request)
    return presented is not None and hmac.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    )


def unauthorised_response() -> JSONResponse:
    return error_response(
        401,
        "This request is missing or carrying the wrong session token.",
        technical="Send 'Authorization: Bearer <token>' with the token the shell passed in.",
        headers={"WWW-Authenticate": "Bearer"},
    )
