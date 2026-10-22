import logging
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers import alerts, auth, health, internal, listings, notifications, sessions, snapshots

logger = logging.getLogger("ai_shopping_copilot")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Shopping Copilot Backend",
        description="Backend API for AI Shopping Copilot Chrome Extension",
        version="0.2.0",
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
        formatted_messages = []
        for err in exc.errors():
            loc = " -> ".join(str(l) for l in err.get("loc", []))
            msg = err.get("msg", "invalid")
            formatted_messages.append(f"{loc}: {msg}" if loc else msg)

        message_str = "; ".join(formatted_messages) if formatted_messages else "Request validation failed"
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "validation_error",
                    "message": message_str,
                }
            },
        )

    @app.exception_handler(Exception)
    def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled server exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An internal server error occurred.",
                }
            },
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

    return app


app = create_app()
