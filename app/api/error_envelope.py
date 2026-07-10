"""Shared aidream-compatible error envelope for HTTP error responses.

aidream's global exception envelope (aidream/api/errors.py) rewrites every
HTTPException into a TOP-LEVEL body::

    {"error": "<code>", "message": "<human text>", "details": {...}}

while a raw FastAPI deployment nests the same data under ``{"detail": ...}``.
The frontend stream runner (matrx-frontend run-ai-stream.ts) reads BOTH
shapes, but the envelope is the production contract — so matrx-local's AI +
media surfaces emit it too, byte-compatible, via the helpers here.

Three entry points:

* ``envelope_response(status_code, code, message, details)`` — build a
  JSONResponse carrying the envelope directly.
* ``install_envelope_handlers(app)`` — app-level exception handlers that
  rewrite ``HTTPException`` (including ``detail={"code": ..., "message":
  ...}`` dicts, aidream's router convention) and request-validation errors
  into the envelope. Mount once per FastAPI (sub-)app. Used by the /ai
  surface (app/api/ai_routes.py) where the envelope IS the contract.
* ``EnvelopeRoute`` — an ``APIRoute`` subclass for routers whose existing
  error bodies are already a client contract (the media-gen routes): error
  responses gain the envelope keys ADDITIVELY, preserving every original
  key, so legacy consumers keep working. Success responses are untouched.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.responses import Response


def envelope_body(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The canonical top-level error body."""
    return {"error": code, "message": message, "details": details or {}}


def envelope_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=envelope_body(code, message, details)
    )


_DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    410: "gone",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def http_exception_to_envelope(exc: HTTPException) -> JSONResponse:
    """Rewrite an HTTPException into the aidream envelope (strict shape).

    aidream's routers raise ``HTTPException(detail={"code": ..., "message":
    ...})`` — the code becomes the top-level ``error`` and the message the
    top-level ``message`` (extra detail keys ride in ``details``). A plain
    string detail becomes the message with a status-derived code.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or _DEFAULT_CODES.get(exc.status_code, "error"))
        message = str(detail.get("message") or detail.get("detail") or code)
        details = {k: v for k, v in detail.items() if k not in ("code", "message")}
    else:
        code = _DEFAULT_CODES.get(exc.status_code, "error")
        message = str(detail) if detail else code
        details = {}
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope_body(code, message, details),
        headers=exc.headers,
    )


def install_envelope_handlers(app: FastAPI) -> None:
    """Install envelope-shaped handlers for HTTPException + 422 validation."""

    @app.exception_handler(HTTPException)
    async def _http_exc(_req: Request, exc: HTTPException) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return http_exception_to_envelope(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(  # pyright: ignore[reportUnusedFunction]
        _req: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return envelope_response(
            422,
            "validation_error",
            "Request body failed validation.",
            {"errors": exc.errors()},
        )


# ---------------------------------------------------------------------------
# Additive envelope route class (legacy-preserving)
# ---------------------------------------------------------------------------


def _envelope_http_exception_additive(exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or _DEFAULT_CODES.get(exc.status_code, "error"))
        message = str(detail.get("message") or detail.get("detail") or code)
        extras = {k: v for k, v in detail.items() if k not in ("code", "message")}
        body = {**detail, **envelope_body(code, message, extras)}
    else:
        code = _DEFAULT_CODES.get(exc.status_code, "error")
        message = str(detail) if detail else code
        # Keep FastAPI's default {"detail": ...} alongside the envelope so
        # legacy consumers reading .detail keep working.
        body = {"detail": detail, **envelope_body(code, message)}
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


def _envelope_json_error_additive(response: Response) -> Response:
    if response.status_code < 400:
        return response
    raw = getattr(response, "body", b"")
    if not raw:
        return response
    try:
        parsed = json.loads(raw)
    except Exception:
        return response
    if not isinstance(parsed, dict):
        return response
    if "error" in parsed and "message" in parsed and "details" in parsed:
        return response  # already enveloped
    code = str(
        parsed.get("code")
        or parsed.get("error")
        or _DEFAULT_CODES.get(response.status_code, "error")
    )
    message = str(
        parsed.get("message") or parsed.get("detail") or parsed.get("error") or code
    )
    details = {
        k: v
        for k, v in parsed.items()
        if k not in ("code", "message", "detail", "error")
    }
    merged = {**parsed, **envelope_body(code, message, details)}
    return JSONResponse(
        status_code=response.status_code,
        content=merged,
        headers={
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-type")
        },
    )


class EnvelopeRoute(APIRoute):
    """``APIRoute`` that ADDS the aidream envelope to error responses.

    For routers whose existing error bodies are a client contract (the media
    routes' ``{"detail": ..., "needs_download": true}`` shapes, read by the
    desktop UI): every JSON error response — a raised ``HTTPException`` OR a
    returned ``JSONResponse(status_code>=400)`` — gains top-level ``error``
    / ``message`` / ``details`` keys while EVERY original key is preserved.
    Additive alignment, zero legacy breakage; success responses untouched.

    Usage::

        router = APIRouter(prefix="/image-gen", route_class=EnvelopeRoute)
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except HTTPException as exc:
                return _envelope_http_exception_additive(exc)
            return _envelope_json_error_additive(response)

        return handler
