"""FastAPI entrypoint for the M1.1 server foundation."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .app_context import request_id_context
from .config import Settings, get_settings
from .logging import configure_logging

logger = logging.getLogger("veetee.server")


def _request_id(value: str | None) -> str:
    if value:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return str(uuid4())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    logger.info("server_started", extra={"context": {"environment": settings.environment}})
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        logger.info("server_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    app = FastAPI(title="Veetee Server API", version="0.1.0", lifespan=lifespan)
    app.state.settings = runtime_settings
    app.state.ready = False

    @app.middleware("http")
    async def correlation_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _request_id(request.headers.get("X-Veetee-Request-Id"))
        token = request_id_context.set(request_id)
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("unhandled_request_error")
                response = JSONResponse(
                    status_code=500,
                    content={
                        "code": "veetee_internal",
                        "message": "Internal server error",
                        "request_id": request_id,
                    },
                )
            response.headers["X-Veetee-Request-Id"] = request_id
            return response
        finally:
            request_id_context.reset(token)

    @app.get("/healthz", tags=["operations"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": runtime_settings.app_name}

    @app.get("/readyz", tags=["operations"])
    async def readyz() -> JSONResponse:
        if not runtime_settings.readiness_enabled:
            return JSONResponse(
                content={"status": "disabled", "service": runtime_settings.app_name}
            )
        if not app.state.ready:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(content={"status": "ready", "service": runtime_settings.app_name})

    return app


app = create_app()
