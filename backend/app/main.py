import json
import logging
import math
import re
import uuid
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.deps import create_ai_provider
from app.routers import (
    alerts,
    auth,
    health,
    internal,
    listings,
    notifications,
    sessions,
    snapshots,
)

logger = logging.getLogger("ai_shopping_copilot")


class BoundedJSONRequests:
    """Bound JSON before route parsing, including streams without Content-Length."""

    max_bytes = 262_144

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()
        content_type = (
            dict(scope.get("headers", []))
            .get(b"content-type", b"")
            .split(b";", 1)[0]
            .strip()
            .lower()
        )
        audio = (
            scope.get("method") == "POST"
            and re.fullmatch(r"/v1/ai/conversations/[a-zA-Z0-9_.:-]{1,100}/audio", scope["path"])
            and content_type in {b"audio/wav", b"audio/webm", b"audio/ogg", b"audio/mpeg"}
        )
        limit = 5_000_000 if audio else self.max_bytes
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": "Request body is too large.",
                        }
                    },
                )
                return await response(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if body and not audio:
            headers = dict(scope.get("headers", []))
            content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
            if content_type != b"application/json":
                response = JSONResponse(
                    status_code=415,
                    content={
                        "error": {
                            "code": "UNSUPPORTED_MEDIA_TYPE",
                            "message": "Expected application/json.",
                        }
                    },
                )
                return await response(scope, receive, send)
            try:
                parsed = json.loads(
                    body, object_pairs_hook=self._unique, parse_constant=self._invalid
                )
                self._depth(parsed)
            except (ValueError, RecursionError, UnicodeError):
                response = JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "validation_error",
                            "message": "Request validation failed",
                        }
                    },
                )
                return await response(scope, receive, send)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def private_response(message):
            if message["type"] == "http.response.start" and scope["path"].startswith("/v1/"):
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"
                ]
                message = {**message, "headers": headers + [(b"cache-control", b"no-store")]}
            await send(message)

        await self.app(scope, bounded_receive, private_response)

    @staticmethod
    def _invalid(value):
        raise ValueError("INVALID_JSON")

    @staticmethod
    def _unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("INVALID_JSON")
            result[key] = value
        return result

    @classmethod
    def _depth(cls, value, depth=0):
        if depth > 32:
            raise ValueError("INVALID_JSON")
        if isinstance(value, dict):
            for key, child in value.items():
                cls._depth(key, depth + 1)
                cls._depth(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                cls._depth(child, depth + 1)
        elif isinstance(value, str):
            value.encode("utf-8")
            if "\x00" in value:
                raise ValueError("INVALID_JSON")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("INVALID_JSON")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        from app.services.auth_service import AuthService

        app.state.auth_service = AuthService()
        provider = create_ai_provider()
        app.state.ai_provider = provider
        try:
            async with AsyncExitStack() as stack:
                if get_settings().AI_RUNTIME_ENABLED:
                    from app.ai.runtime import configured_runtime
                    from app.services.runtime_service import RuntimeService

                    runtime = await stack.enter_async_context(configured_runtime(get_settings()))
                    app.state.runtime_service = RuntimeService(runtime, get_settings())
                yield
        finally:
            close = getattr(provider, "close", None)
            if close is not None:
                close()

    app = FastAPI(
        title="AI Shopping Copilot Backend",
        description="Backend API for AI Shopping Copilot Chrome Extension",
        version="0.3.0",
        lifespan=lifespan,
    )
    app.add_middleware(BoundedJSONRequests)
    if get_settings().CORS_ALLOWED_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(get_settings().CORS_ALLOWED_ORIGINS),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Event-ID", "X-Expected-Version"],
            expose_headers=["Idempotent-Replay", "X-Conversation-Version", "X-Request-ID"],
        )

    # 1. Custom Error Handlers (Consistent format: {"error": {"code": "...", "message": "..."}})
    @app.exception_handler(HTTPException)
    def http_exception_handler(request: Request, exc: HTTPException):
        code_mapping = {
            status.HTTP_400_BAD_REQUEST: "bad_request",
            status.HTTP_401_UNAUTHORIZED: "unauthorized",
            status.HTTP_403_FORBIDDEN: "forbidden",
            status.HTTP_404_NOT_FOUND: "not_found",
            status.HTTP_409_CONFLICT: "conflict",
            status.HTTP_422_UNPROCESSABLE_CONTENT: "validation_error",
            status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
            status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_error",
            status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
        }

        if isinstance(exc.detail, dict) and "code" in exc.detail:
            error_code = exc.detail["code"]
            message = exc.detail.get("message", "")
        else:
            custom_code = exc.headers.get("X-Error-Code") if exc.headers else None
            error_code = custom_code or code_mapping.get(exc.status_code, "error")
            message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": error_code,
                    "message": message,
                }
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                }
            },
        )

    @app.exception_handler(Exception)
    def unhandled_exception_handler(request: Request, exc: Exception):
        # Database/provider exception strings can contain SQL parameters and user
        # text. Log only a correlation ID and exception class, never its payload.
        request_id = uuid.uuid4().hex
        logger.error(
            "Unhandled server exception request_id=%s type=%s", request_id, type(exc).__name__
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An internal server error occurred.",
                }
            },
            headers={"X-Request-ID": request_id},
        )

    # 2. Register Routers
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(sessions.router)
    app.include_router(snapshots.router)
    app.include_router(listings.router)
    app.include_router(alerts.router)
    app.include_router(notifications.router)
    app.include_router(internal.router)
    if get_settings().AI_RUNTIME_ENABLED:
        from app.routers.runtime import router

        app.include_router(router)

    return app


app = create_app()
