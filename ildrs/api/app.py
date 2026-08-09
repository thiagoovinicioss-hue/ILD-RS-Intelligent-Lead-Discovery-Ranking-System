"""FastAPI application factory.

Wires the whole backend together:
- database (connect, schema bootstrap, graceful close)
- source adapter, notifier, orchestrator, outreach workflow
- periodic job scheduler
- REST routers under /api/v1
- static frontend (served from ``frontend/`` when present)
- consistent error envelope: {"detail": {"code", "message", "context"?}}
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ildrs import __version__
from ildrs.api.context import AppContext
from ildrs.api.routes import businesses, discovery, jobs, leads, outreach, system
from ildrs.api.routes import config as config_route
from ildrs.config import get_settings
from ildrs.jobs.scheduler import next_run_time
from ildrs.observability.logging import configure_logging
from ildrs.runtime import build_runtime

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"


def create_app(context: AppContext | None = None) -> FastAPI:
    """Build the FastAPI app.

    When ``context`` is provided (CLI ``run``/``serve`` boot their own
    runtime and print a readiness banner first), the context is reused
    as-is — the scheduler is not started a second time. Otherwise a full
    runtime is built and owned by the app's lifespan.
    """
    configure_logging()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()

        if context is not None:
            ctx = context
        else:
            ctx = await build_runtime(settings)

        app.state.context = ctx
        app.state._next_verify = next_run_time(
            settings.verify_interval_hours * 3600, ctx.scheduler.started_at
        ).isoformat()

        if context is None:
            await ctx.notifier.send("info", "System started", f"ILD-RS v{__version__} API online.")

        try:
            yield
        finally:
            await _shutdown(ctx)

    app = FastAPI(
        title="ILD-RS",
        description="Intelligent Lead Discovery & Ranking System — API",
        version=__version__,
        lifespan=lifespan,
    )

    _configure_cors(app)

    app.include_router(system.router)
    app.include_router(businesses.router)
    app.include_router(discovery.router)
    app.include_router(leads.router)
    app.include_router(jobs.router)
    app.include_router(outreach.router)
    app.include_router(config_route.router)

    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)

    _mount_frontend(app)
    return app


def _configure_cors(app: FastAPI) -> None:
    origins = get_settings().cors_origins_list
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )


async def _shutdown(context: AppContext) -> None:
    # Cancel API-spawned background jobs first (bounded wait).
    pending = [t for t in context.background_tasks if not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        with contextlib.suppress(BaseException):
            await asyncio.wait(pending, timeout=5.0)

    with contextlib.suppress(Exception):
        await context.notifier.send("info", "System stopped", "ILD-RS API shutting down.")

    with contextlib.suppress(Exception):
        await context.scheduler.stop(timeout=10.0)

    with contextlib.suppress(Exception):
        await context.db.close()


def _mount_frontend(app: FastAPI) -> None:
    if FRONTEND_DIR.is_dir():
        # Mounted last so /api routes take precedence.
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
        return

    @app.get("/")
    async def root():
        return {
            "service": "ILD-RS",
            "version": __version__,
            "docs": "/docs",
            "api": "/api/v1/system/status",
            "frontend": "not_built",
        }


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": _status_code(exc.status_code), "message": str(exc.detail)}},
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "validation_error",
                "message": "request validation failed",
                "context": exc.errors(),
            }
        },
    )


def _status_code(code: int) -> str:
    import http

    reason = http.HTTPStatus(code).phrase.lower().replace(" ", "_")
    return reason or f"http_{code}"


app = create_app()
