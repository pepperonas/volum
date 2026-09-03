"""One error shape for every failure the engine returns.

Spec section 55 separates what a person reads from what a developer needs::

    {"error": {"message": "...", "technical": "...", "suggestions": ["..."]}}

The service already speaks in these terms (:class:`ServiceError`); this module
maps them onto HTTP and gives request-validation and unexpected failures the
same shape, so a client has exactly one thing to render.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from volum_core.service import ServiceError

log = logging.getLogger("volum.engine")

_STATUS_FOR_KIND = {
    "invalid": 400,
    "not_found": 404,
    "conflict": 409,
    "unavailable": 503,
}


def error_response(
    status: int,
    message: str,
    *,
    technical: str = "",
    suggestions: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "message": message,
            "technical": technical or None,
            "suggestions": suggestions or [],
        }
    }
    return JSONResponse(body, status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, error: ServiceError) -> JSONResponse:
        return error_response(
            _STATUS_FOR_KIND[error.kind],
            error.message,
            technical=error.technical,
            suggestions=error.suggestions,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', ()))}: {item.get('msg', '')}"
            for item in error.errors()
        )
        return error_response(422, "The request was not valid.", technical=detail)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, error: Exception) -> JSONResponse:
        # Logged in full, returned in outline. The traceback is for the log;
        # the person gets a sentence and the exception's name.
        log.exception("Unhandled error in the engine")
        return error_response(
            500,
            "VOLUM hit an unexpected error while handling this request.",
            technical=f"{type(error).__name__}: {error}",
            suggestions=["Check the engine log for the full traceback."],
        )
