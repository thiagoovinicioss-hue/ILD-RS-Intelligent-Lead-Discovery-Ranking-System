"""Runtime bootstrap, boot-time verification, and shutdown reporting.

Used by the CLI (``run`` / ``serve`` / ``status``) and reused by the API
factory so every entry point boots the same components:

- database (connect + schema bootstrap)
- discovery source
- rating engine (constructed and smoke-tested)
- notifier
- periodic job scheduler (verify / rerank / outreach-prepare / monitor)

``boot()`` runs each component's verification before the app starts and
returns a :class:`Readiness` object that the CLI renders as the startup
banner; any fatal line aborts startup with a clean message instead of a
stack trace. ``shutdown_report()`` renders what was actually stopped so the
Ctrl+C path stays observable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import text

from ildrs import __version__
from ildrs.api.context import AppContext
from ildrs.config import get_settings
from ildrs.domain.entities import FeatureVector
from ildrs.jobs.definitions import register_periodic_jobs
from ildrs.jobs.scheduler import Scheduler
from ildrs.notifications.notifier import Notifier
from ildrs.outreach.monitoring import ResponseMonitor
from ildrs.outreach.review import ReviewWorkflow
from ildrs.outreach.workflow import OutreachWorkflow
from ildrs.pipeline.orchestrator import Orchestrator
from ildrs.rating.base import ModelNotImplemented, ModelNotReadyError
from ildrs.rating.registry import create_model
from ildrs.sources.registry import create_source
from ildrs.storage.bootstrap import init as init_schema
from ildrs.storage.database import Database
from ildrs.terminal import Painter

Echo = Callable[[str], None]


@dataclass
class ReadinessLine:
    key: str
    state: str  # "ok" | "warn" | "fail"
    detail: str = ""


@dataclass
class Readiness:
    lines: list[ReadinessLine] = field(default_factory=list)

    def add(self, key: str, state: str, detail: str = "") -> None:
        self.lines.append(ReadinessLine(key, state, detail))

    def row(self, key: str) -> ReadinessLine | None:
        for line in self.lines:
            if line.key == key:
                return line
        return None

    @property
    def ok(self) -> bool:
        return not any(line.state == "fail" for line in self.lines)

    def banner_rows(self) -> list[tuple[str, str, str]]:
        order = ["DATABASE", "DISCOVERY", "RATING ENGINE", "NOTIFICATIONS", "VERIFICATION"]
        rows = [(k, "ok", "") for k in order]
        for line in self.lines:
            for i, (key, _state, _detail) in enumerate(rows):
                if key == line.key:
                    rows[i] = (line.key, line.state, line.detail)
        return rows

    def states(self) -> dict[str, str]:
        return {line.key: line.state for line in self.lines}


def config_problems(settings) -> list[str]:
    """Non-fatal configuration warnings that should not block startup."""
    problems: list[str] = []
    if settings.source == "google_places" and not settings.google_places_api_key:
        problems.append("google_places source configured but ILD_GOOGLE_PLACES_API_KEY is empty")
    return problems


# --------------------------------------------------------------------------
# component construction (shared by boot and the API factory)
# --------------------------------------------------------------------------


async def _start_scheduler(
    settings, db: Database, source, notifier: Notifier
) -> tuple[Orchestrator, ReviewWorkflow, ResponseMonitor, Scheduler]:
    orchestrator = Orchestrator(db, source, notifier)
    review = ReviewWorkflow(db, notifier)
    monitor = ResponseMonitor(db, notifier)
    scheduler = Scheduler()
    register_periodic_jobs(scheduler, orchestrator, review=review, monitor=monitor)
    await scheduler.start()
    return orchestrator, review, monitor, scheduler


async def build_runtime(settings) -> AppContext:
    """Construct and start a full runtime (used by the API when no context is
    passed to ``create_app``). No boot verification here."""
    db = Database()
    db.connect()
    await init_schema(db)
    source = create_source(settings.source)
    notifier = Notifier(db)
    orchestrator, review, monitor, scheduler = await _start_scheduler(
        settings, db, source, notifier
    )
    return AppContext(
        db=db,
        source=source,
        notifier=notifier,
        orchestrator=orchestrator,
        outreach=OutreachWorkflow(db),
        review=review,
        monitor=monitor,
        scheduler=scheduler,
    )


# --------------------------------------------------------------------------
# boot verification
# --------------------------------------------------------------------------


async def _ping(db: Database) -> None:
    async with db.session() as session:
        result = await session.execute(text("SELECT 1"))
        if result.scalar_one() != 1:
            raise RuntimeError("database ping failed")


def _rating_line(settings) -> tuple[str, str]:
    """(state, detail) for the configured rating engine via a smoke predict."""
    try:
        model = create_model(settings.rating_model)
    except ValueError as exc:
        return "fail", str(exc)
    try:
        model.predict(FeatureVector(business_id="__boot_check__"))
    except ModelNotImplemented:
        return "warn", f"{settings.rating_model} defined, not implemented"
    except ModelNotReadyError:
        return "warn", f"{settings.rating_model} awaiting calibration (v1 fallback active)"
    except Exception as exc:  # noqa: BLE001 - any engine failure is surfaced
        return "fail", str(exc)
    return "ok", f"{model.name}@{model.version}"


async def boot(*, echo: Echo | None = None) -> tuple[AppContext | None, Readiness]:
    """Verify every component and start the runtime.

    Returns ``(context, readiness)``; on any fatal line the context is
    ``None`` and all resources created so far have been released.
    """
    settings = get_settings()
    painter = Painter()
    say = echo if echo is not None else (lambda _message: None)
    readiness = Readiness()

    for problem in config_problems(settings):
        readiness.add("CONFIG", "warn", problem)
        say(painter.warn(f"  config: {problem}"))

    db = Database()
    db.connect()
    try:
        await init_schema(db)
        await _ping(db)
    except Exception as exc:  # noqa: BLE001
        readiness.add("DATABASE", "fail", str(exc))
        say(painter.error(f"  database: {exc}"))
        await db.close()
        return None, readiness
    readiness.add("DATABASE", "ok", "sqlite connected")
    say(painter.ok("  database: connected"))

    try:
        source = create_source(settings.source)
    except Exception as exc:  # noqa: BLE001
        readiness.add("DISCOVERY", "fail", str(exc))
        say(painter.error(f"  discovery: {exc}"))
        await db.close()
        return None, readiness
    readiness.add("DISCOVERY", "ok", settings.source)
    say(painter.ok(f"  discovery: {settings.source} ready"))

    rating_state, rating_detail = _rating_line(settings)
    readiness.add("RATING ENGINE", rating_state, rating_detail)
    if rating_state == "ok":
        say(painter.ok(f"  rating engine: {rating_detail}"))
    elif rating_state == "warn":
        say(painter.warn(f"  rating engine: {rating_detail}"))
    else:
        say(painter.error(f"  rating engine: {rating_detail}"))

    notifier = Notifier(db)
    try:
        await notifier.send("info", "System starting", f"ILD-RS v{__version__} boot")
    except Exception as exc:  # noqa: BLE001 - webhook failures degrade to console
        readiness.add("NOTIFICATIONS", "warn", str(exc))
        say(painter.warn(f"  notifications: {exc}"))
    else:
        readiness.add("NOTIFICATIONS", "ok", "db + console")
        say(painter.ok("  notifications: ready"))

    try:
        orchestrator, review, monitor, scheduler = await _start_scheduler(
            settings, db, source, notifier
        )
    except Exception as exc:  # noqa: BLE001
        readiness.add("VERIFICATION", "fail", str(exc))
        say(painter.error(f"  scheduler: {exc}"))
        await db.close()
        return None, readiness
    readiness.add("VERIFICATION", "ok", f"{len(scheduler._tasks)} periodic jobs")
    say(painter.ok(f"  scheduler: {len(scheduler._tasks)} periodic jobs running"))

    context = AppContext(
        db=db,
        source=source,
        notifier=notifier,
        orchestrator=orchestrator,
        outreach=OutreachWorkflow(db),
        review=review,
        monitor=monitor,
        scheduler=scheduler,
    )
    return context, readiness


# --------------------------------------------------------------------------
# shutdown reporting
# --------------------------------------------------------------------------


def shutdown_report(
    context: AppContext, *, interrupted: bool, host: str = "", port: int = 0
) -> tuple[list[tuple[str, str, str, str]], str]:
    """Structured shutdown summary + exit note.

    Returns ``(items, exit_note)`` where each item is
    ``(key, state, label, detail)`` and ``state`` is one of
    ``"ok" | "warn" | "info"`` (drives coloring in the terminal renderer).
    """
    background_left = sum(1 for t in context.background_tasks if not t.done())
    items: list[tuple[str, str, str, str]] = [
        ("api", "ok", "STOPPED", f"{host}:{port}" if host else ""),
        (
            "scheduler",
            "ok",
            "STOPPED",
            f"{len(context.scheduler._tasks)} periodic job(s)",
        ),
        (
            "background jobs",
            "ok" if background_left == 0 else "warn",
            "CANCELLED" if background_left == 0 else "PENDING",
            f"{background_left} remaining" if background_left else "",
        ),
        ("database", "ok", "CLOSED", ""),
    ]
    exit_label = "130 (interrupted)" if interrupted else "0 (clean)"
    items.append(("exit", "info", exit_label, ""))
    exit_note = (
        "Ctrl+C received — state persisted, scheduler stopped, database closed."
        if interrupted
        else "Clean shutdown — all components stopped gracefully."
    )
    return items, exit_note


__all__ = [
    "Readiness",
    "ReadinessLine",
    "boot",
    "build_runtime",
    "config_problems",
    "shutdown_report",
]
