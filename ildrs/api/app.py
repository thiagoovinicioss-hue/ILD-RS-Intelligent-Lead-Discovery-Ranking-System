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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ildrs import __version__
from ildrs.api.context import AppContext
from ildrs.api.routes import businesses, discovery, jobs, leads, outreach, system
from ildrs.api.routes import config as config_route
from ildrs.config import get_settings
from ildrs.jobs.definitions import register_periodic_jobs
from ildrs.jobs.scheduler import Scheduler, next_run_time
from ildrs.notifications.notifier import Notifier
from ildrs.observability.logging import configure_logging
from ildrs.outreach.monitoring import ResponseMonitor
from ildrs.outreach.review import ReviewWorkflow
from ildrs.outreach.workflow import OutreachWorkflow
from ildrs.pipeline.orchestrator import Orchestrator
from ildrs.sources.registry import create_source
from ildrs.storage.bootstrap import init as init_schema
from ildrs.storage.database import Database

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"


def create_app() -> FastAPI:
    configure_logging()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()

        db = Database()
        db.connect()
        await init_schema(db)

        source = create_source(settings.source)
        notifier = Notifier(db)
        orchestrator = Orchestrator(db, source, notifier)
        outreach = OutreachWorkflow(db)
        review = ReviewWorkflow(db, notifier)
        monitor = ResponseMonitor(db, notifier)
        scheduler = Scheduler()

        register_periodic_jobs(scheduler, orchestrator, review=review, monitor=monitor)
        await scheduler.start()

        context = AppContext(
            db=db,
            source=source,
            notifier=notifier,
            orchestrator=orchestrator,
            outreach=outreach,
            review=review,
            monitor=monitor,
            scheduler=scheduler,
        )
        app.state.context = context
        app.state._next_verify = next_run_time(
            settings.verify_interval_hours * 3600, scheduler.started_at
        ).isoformat()

        await notifier.send("info", "System started", f"ILD-RS v{__version__} API online.")

        try:
            yield
        finally:
            await _shutdown(context)

    app = FastAPI(
        title="ILD-RS",
        description="Intelligent Lead Discovery & Ranking System — API",
        version=__version__,
        lifespan=lifespan,
    )

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
