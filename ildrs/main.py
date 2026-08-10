"""ILD-RS command-line interface (Typer).

`ildrs run` and `ildrs serve` install SIGINT/SIGTERM handlers that stop jobs
gracefully, persist state, close the DB, cancel background tasks, print a
concise shutdown message, and exit with status 130 on interruption.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from typing import Any

import typer
import uvicorn

from ildrs import __version__
from ildrs.config import get_settings
from ildrs.domain.entities import OUTREACH_CHANNELS, OUTREACH_STATUSES
from ildrs.notifications.notifier import Notifier
from ildrs.outreach.workflow import OutreachWorkflow
from ildrs.pipeline.orchestrator import Orchestrator
from ildrs.rating.registry import create_model
from ildrs.runtime import boot, shutdown_report
from ildrs.sources.registry import create_source
from ildrs.storage.bootstrap import init as init_schema
from ildrs.storage.bootstrap import reset as reset_schema
from ildrs.storage.database import Database
from ildrs.terminal import (
    Painter,
    banner,
    panel,
    readiness_row,
    shutdown_summary,
    status_token,
)

app = typer.Typer(
    help="ILD-RS — Intelligent Lead Discovery & Ranking System.", no_args_is_help=True
)
leads_cmd = typer.Typer(help="Inspect and manage leads.", no_args_is_help=True)
outreach_cmd = typer.Typer(help="Drive outreach workflow.", no_args_is_help=True)
jobs_cmd = typer.Typer(help="Inspect pipeline jobs.", no_args_is_help=True)
config_cmd = typer.Typer(help="Configuration commands.", no_args_is_help=True)
db_cmd = typer.Typer(help="Database management.", no_args_is_help=True)
review_cmd = typer.Typer(help="Review outreach drafts before sending.", no_args_is_help=True)
app.add_typer(leads_cmd, name="leads")
app.add_typer(outreach_cmd, name="outreach")
app.add_typer(jobs_cmd, name="jobs")
app.add_typer(config_cmd, name="config")
app.add_typer(db_cmd, name="db")
app.add_typer(review_cmd, name="review")


# --------------------------------------------------------------------------
# run / serve (signal-aware)
# --------------------------------------------------------------------------

_PIPELINE_STAGES = ("discover", "collect", "analyze", "rate", "rank")


@app.command()
def run(
    pipeline: bool = typer.Option(
        False, "--pipeline", help="Run discover→collect→analyze→rate→rank before serving"
    ),
    host: str = typer.Option(None, help="Bind host (default: ILD_API_HOST)"),
    port: int = typer.Option(None, help="Bind port (default: ILD_API_PORT)"),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors"),
) -> None:
    """Boot, verify, optionally run the pipeline once, then serve API + scheduler.

    Ctrl+C triggers a graceful shutdown (jobs stopped, state persisted,
    database closed) and exits with status 130.
    """
    code = asyncio.run(
        _run_server(
            run_pipeline=pipeline,
            host=host,
            port=port,
            no_color=no_color,
        )
    )
    if code:
        sys.exit(code)


@app.command()
def serve(
    host: str = typer.Option(None, help="Bind host (default: ILD_API_HOST)"),
    port: int = typer.Option(None, help="Bind port (default: ILD_API_PORT)"),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors"),
) -> None:
    """Boot, verify, then run API + scheduler loop (long-running); Ctrl+C safe."""
    code = asyncio.run(_run_server(run_pipeline=False, host=host, port=port, no_color=no_color))
    if code:
        sys.exit(code)


async def _run_server(
    *,
    run_pipeline: bool,
    host: str | None,
    port: int | None,
    no_color: bool,
) -> int:
    """Shared body of `run` and `serve`. Returns the process exit code."""
    painter = Painter(enabled=not no_color)
    settings = get_settings()

    context, readiness = await boot()
    if context is None:
        _print_banner(painter, readiness)
        typer.echo("")
        typer.echo(painter.error("startup aborted — resolve the failures above and retry."))
        return 1

    # Own SIGINT/SIGTERM from the very start (before the pipeline and the API
    # boot) so an early Ctrl+C is always a graceful interruption. asyncio.run
    # installs its own SIGINT handler that cancels the main task and raises
    # KeyboardInterrupt; leaving it active during the pipeline or the server
    # startup would bypass the shutdown report below.
    cancel = asyncio.Event()
    holder: dict[str, Any] = {"server": None}
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        cancel.set()
        if holder["server"] is not None:
            holder["server"].interrupt()

    installed: list[signal.Signals] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _on_signal)
            installed.append(sig)

    try:
        if run_pipeline:
            for stage in _PIPELINE_STAGES:
                if cancel.is_set():
                    break
                result = await context.orchestrator.run_stage_guarded(stage, cancel=cancel)
                _print_stage_results([result])
                if result.get("status") != "completed":
                    typer.echo(painter.warn("pipeline did not complete; serving anyway."))

        _print_banner(painter, readiness)

        bind_host = host or settings.api_host
        bind_port = port or settings.api_port

        from ildrs.api.app import create_app

        config = uvicorn.Config(
            create_app(context=context),
            host=bind_host,
            port=bind_port,
            log_level=settings.log_level.lower(),
        )
        server = GracefulServer(config)
        holder["server"] = server
        if cancel.is_set():
            server.interrupt()

        interrupted = False
        try:
            await server.serve()
            interrupted = server.was_interrupted
        except asyncio.CancelledError:
            interrupted = True
        except Exception as exc:  # noqa: BLE001 - report instead of a traceback
            typer.echo(painter.error(f"server failed: {exc}"))
            return 1
    finally:
        for sig in installed:
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(sig)

    _print_shutdown(painter, context, interrupted=interrupted, host=bind_host, port=bind_port)
    return 130 if interrupted else 0


def _print_banner(painter: Painter, readiness) -> None:
    rows = [(key, status_token(state), detail) for key, state, detail in readiness.banner_rows()]
    typer.echo(banner(painter, rows))


def _print_shutdown(painter: Painter, context, *, interrupted: bool, host: str, port: int) -> None:
    items, exit_note = shutdown_report(context, interrupted=interrupted, host=host, port=port)
    typer.echo("")
    typer.echo(
        shutdown_summary(
            painter,
            header="ILD-RS · SHUTDOWN COMPLETE",
            items=items,
            exit_note=exit_note,
        )
    )


class GracefulServer(uvicorn.Server):
    """uvicorn.Server that skips installing its own signal handlers so the CLI
    owns SIGINT/SIGTERM and can report whether the shutdown was user-initiated."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.was_interrupted = False

    @contextlib.contextmanager
    def capture_signals(self):
        yield

    def interrupt(self) -> None:
        self.was_interrupted = True
        self.should_exit = True


# --------------------------------------------------------------------------
# single stages
# --------------------------------------------------------------------------


@app.command()
def discover(
    limit: int | None = typer.Option(None, help="Override ILD_DISCOVERY_LIMIT"),
    query: str | None = typer.Option(None, help="Search query (overrides ILD_DISCOVERY_QUERY)"),
    category: str | None = typer.Option(
        None, help="Category filter (overrides ILD_DISCOVERY_CATEGORIES, first token)"
    ),
    location: str | None = typer.Option(
        None, help="City/place to search (overrides ILD_DISCOVERY_LOCATION)"
    ),
    no_dedupe: bool = typer.Option(
        False, "--no-dedupe", help="Skip duplicate filtering against existing businesses"
    ),
) -> None:
    """Discover candidates from the configured source."""
    _run_single_stage(
        "discover",
        limit=limit,
        query=query,
        category=category,
        location=location,
        dedupe=not no_dedupe,
    )


@app.command()
def collect() -> None:
    """Enrich businesses with details."""
    _run_single_stage("collect")


@app.command()
def analyze() -> None:
    """Normalize + extract features + validate."""
    _run_single_stage("analyze")


@app.command()
def rate() -> None:
    """Compute ratings."""
    _run_single_stage("rate")


@app.command()
def rank() -> None:
    """Recompute lead ranking."""
    _run_single_stage("rank")


@app.command()
def verify() -> None:
    """Re-verify stale businesses."""
    _run_single_stage("verify")


@app.command()
def dedup(
    limit: int = typer.Option(100000, help="Max businesses to scan"),
) -> None:
    """Detect and flag duplicate businesses across the database.

    Conservative matching: identical normalized phone, or identical website
    domain with the same name, or identical normalized name + category.
    Non-canonical members are flagged with ``is_duplicate`` and linked to
    the canonical business.
    """
    from ildrs.normalization.deduplicator import summarize
    from ildrs.storage.repositories import (
        business_to_domain,
        clear_duplicate_flags,
        list_businesses,
        mark_duplicates,
    )

    async def main() -> dict:
        db = Database()
        db.connect()
        await init_schema(db)
        try:
            async with db.session() as session:
                rows = await list_businesses(session, limit=limit)
                businesses = [business_to_domain(r) for r in rows]
                clusters, duplicate_count = summarize(businesses, (r.id for r in rows))
                mapping = {
                    member_id: cluster.canonical_id
                    for cluster in clusters
                    for member_id in cluster.duplicate_ids
                }
                async with db.session() as session:
                    cleared = await clear_duplicate_flags(session)
                    marked = await mark_duplicates(session, mapping)
                    await session.commit()
            return {
                "scanned": len(rows),
                "clusters": len(clusters),
                "duplicates": duplicate_count,
                "flags_cleared": cleared,
                "flags_set": marked,
            }
        finally:
            await db.close()

    result = asyncio.run(main())
    typer.echo(
        f"✓ dedup: scanned={result['scanned']} clusters={result['clusters']} "
        f"duplicates={result['duplicates']} (flags {result['flags_cleared']}→{result['flags_set']})"
    )


@app.command()
def enrich_websites(
    limit: int = typer.Option(200, help="Max businesses to analyze"),
) -> None:
    """Fetch and analyze business websites (requires ILD_ENABLE_WEBSITE_ANALYSIS=true)."""
    from ildrs.pipeline.stages import _enrich_website
    from ildrs.storage.repositories import (
        business_to_domain,
        get_business,
        list_businesses,
        upsert_business,
    )

    settings = get_settings()
    if not settings.enable_website_analysis:
        typer.echo("website analysis is disabled (set ILD_ENABLE_WEBSITE_ANALYSIS=true)", err=True)
        sys.exit(1)

    async def main() -> dict:
        db = Database()
        db.connect()
        await init_schema(db)
        try:
            analyzed = 0
            errors = 0
            async with db.session() as session:
                rows = await list_businesses(session, limit=limit)
                ids = [r.id for r in rows]
            for business_id in ids:
                try:
                    async with db.session() as session:
                        row = await get_business(session, business_id)
                        if row is None:
                            continue
                        business = business_to_domain(row)
                    if not business.website:
                        continue
                    await _enrich_website(business)
                    async with db.session() as session:
                        await upsert_business(session, business)
                        await session.commit()
                    analyzed += 1
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    typer.echo(f"  ! {business_id}: {exc}", err=True)
            return {"analyzed": analyzed, "errors": errors}
        finally:
            await db.close()

    result = asyncio.run(main())
    typer.echo(f"✓ website enrichment: analyzed={result['analyzed']} errors={result['errors']}")


def _run_single_stage(
    stage: str,
    *,
    limit: int | None = None,
    query: str | None = None,
    category: str | None = None,
    location: str | None = None,
    dedupe: bool = True,
) -> None:
    settings = get_settings()

    async def main() -> dict:
        db = Database()
        db.connect()
        await init_schema(db)
        try:
            notifier = Notifier(db)
            orchestrator = Orchestrator(db, create_source(settings.source), notifier)
            kwargs: dict = {}
            if stage == "discover":
                discovery_query = _build_discovery_query(settings, limit, query, category, location)
                kwargs = {"query": discovery_query, "dedupe": dedupe}
            return await orchestrator.run_stage_guarded(stage, **kwargs)
        finally:
            await db.close()

    result = asyncio.run(main())
    if result.get("status") == "failed":
        typer.echo(f"✗ {stage} failed: {result.get('error')}", err=True)
        sys.exit(1)
    counts = result.get("counts", {})
    summary = ", ".join(f"{k}={v}" for k, v in counts.items()) or "done"
    typer.echo(f"✓ {stage}: {summary}")


def _build_discovery_query(
    settings: Any,
    limit: int | None,
    query: str | None,
    category: str | None,
    location: str | None,
):
    from ildrs.geocoding import geocode_place
    from ildrs.sources.base import DiscoveryQuery

    lat, lng = settings.discovery_location_coords or (None, None)
    location_label = location or settings.discovery_location
    if location_label:
        coords = geocode_place(location_label)
        if coords is not None:
            lat, lng = coords
        else:
            typer.echo(
                f"warning: could not geocode '{location_label}'; using discovery coords", err=True
            )

    categories = settings.discovery_categories.split(",") if settings.discovery_categories else []
    chosen_category = category or (categories[0] if categories else "")
    if category and category not in categories:
        categories.append(category)
    if category:
        categories = [category]

    return DiscoveryQuery(
        query=query or settings.discovery_query,
        category=chosen_category,
        keywords=categories,
        language=settings.google_places_language,
        region=settings.google_places_region,
        latitude=lat,
        longitude=lng,
        radius_m=settings.discovery_radius_m,
        limit=limit or settings.discovery_limit,
        page_size=min(20, limit or settings.discovery_limit),
    )


# --------------------------------------------------------------------------
# leads
# --------------------------------------------------------------------------


@leads_cmd.command("list")
def leads_list(
    status: str | None = typer.Option(None, help="Filter by lead status"),
    sort: str = typer.Option("rank", help="rank|rating|created"),
    limit: int = typer.Option(50, help="Max rows"),
) -> None:
    """List leads."""
    from ildrs.storage.repositories import lead_serialize, list_leads

    async def main() -> list[dict]:
        db = Database()
        db.connect()
        await init_schema(db)
        try:
            async with db.session() as session:
                rows = await list_leads(session, limit=limit, status=status, sort=sort)
                return [lead_serialize(r) for r in rows]
        finally:
            await db.close()

    items = asyncio.run(main())
    _print_rows(
        ["RANK", "STATUS", "RATING", "CONF", "MODEL", "EV", "ID"],
        [
            [
                it.get("rank"),
                it.get("status"),
                it.get("rating"),
                it.get("confidence"),
                f"{it.get('model')}@{it.get('model_version')}",
                _ev_preview(it.get("expected_value")),
                it.get("id"),
            ]
            for it in items
        ],
    )


def _ev_preview(ev: dict | None) -> str:
    """Short EV display: value, or the probability state when not ready."""
    if not ev:
        return "n/a"
    if ev.get("ready"):
        return f"{ev['expected_value']:.2f}"
    return ev.get("prob_state", "unknown")


@leads_cmd.command("show")
def leads_show(lead_id: str) -> None:
    """Show one lead with business + outreach detail."""
    from ildrs.storage.repositories import (
        business_serialize,
        get_lead,
        lead_serialize,
        outreach_for_lead,
    )

    async def main() -> dict | None:
        db = Database()
        db.connect()
        await init_schema(db)
        try:
            async with db.session() as session:
                row = await get_lead(session, lead_id)
                if row is None:
                    return None
                item = lead_serialize(row)
                item["business"] = business_serialize(row.business) if row.business else None
                item["outreach"] = [
                    {
                        "id": o.id,
                        "channel": o.channel,
                        "status": o.status,
                        "note": o.note,
                        "occurred_at": o.occurred_at.isoformat() if o.occurred_at else None,
                    }
                    for o in await outreach_for_lead(session, row.id)
                ]
                return item
        finally:
            await db.close()

    item = asyncio.run(main())
    if item is None:
        typer.echo(f"lead '{lead_id}' not found", err=True)
        sys.exit(1)
    typer.echo(_json(item, indent=2))


# --------------------------------------------------------------------------
# outreach
# --------------------------------------------------------------------------


@outreach_cmd.command("set")
def outreach_set(
    lead_id: str,
    status: str = typer.Option(..., help="New outreach status"),
    channel: str | None = typer.Option(None, help="Channel when opening a new attempt"),
    note: str = typer.Option("", help="Optional note"),
) -> None:
    """Set outreach status for a lead (optionally opening a new attempt)."""
    if status not in OUTREACH_STATUSES:
        typer.echo(f"invalid status '{status}'; use {list(OUTREACH_STATUSES)}", err=True)
        sys.exit(2)
    if channel is not None and channel not in OUTREACH_CHANNELS:
        typer.echo(f"invalid channel '{channel}'; use {list(OUTREACH_CHANNELS)}", err=True)
        sys.exit(2)

    async def main() -> str:
        db = Database()
        db.connect()
        await init_schema(db)
        try:
            workflow = OutreachWorkflow(db)
            if channel is not None:
                result = await workflow.open(lead_id=lead_id, channel=channel, note=note)
                if not result.ok:
                    return f"error: {result.error}"
                if status == "queued":
                    return f"opened {channel} outreach for lead '{lead_id}' (queued)"
                outreach_id = result.data["id"]
            else:
                from ildrs.storage.repositories import outreach_for_lead

                async with db.session() as session:
                    rows = await outreach_for_lead(session, lead_id)
                if not rows:
                    return (
                        f"error: no outreach record for lead '{lead_id}'; "
                        "pass --channel to open one"
                    )
                outreach_id = rows[0].id
            transition = await workflow.transition(outreach_id=outreach_id, status=status)
            if not transition.ok:
                return f"error: {transition.error}"
            return f"outreach '{outreach_id}' → {status}"
        finally:
            await db.close()

    message = asyncio.run(main())
    typer.echo(message)
    if message.startswith("error"):
        sys.exit(1)


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


@jobs_cmd.command("list")
def jobs_list(limit: int = typer.Option(20, help="Max rows")) -> None:
    """List recent pipeline jobs."""
    from ildrs.storage.repositories import job_serialize, list_jobs

    async def main() -> list[dict]:
        db = Database()
        db.connect()
        await init_schema(db)
        try:
            async with db.session() as session:
                return [job_serialize(r) for r in await list_jobs(session, limit=limit)]
        finally:
            await db.close()

    items = asyncio.run(main())
    _print_rows(
        ["STAGE", "STATUS", "ERROR", "COUNTS", "STARTED"],
        [
            [
                it.get("stage"),
                it.get("status"),
                it.get("error") or "",
                it.get("counts"),
                it.get("started_at"),
            ]
            for it in items
        ],
    )


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


@config_cmd.command("show")
def config_show() -> None:
    """Print effective non-secret configuration."""
    typer.echo(_json(get_settings().public_dict(), indent=2))


# --------------------------------------------------------------------------
# db
# --------------------------------------------------------------------------


@db_cmd.command("init")
def db_init() -> None:
    """Create schema."""

    async def main() -> None:
        db = Database()
        db.connect()
        try:
            await init_schema(db)
        finally:
            await db.close()

    asyncio.run(main())
    typer.echo("✓ schema initialized")


@db_cmd.command("reset")
def db_reset() -> None:
    """Drop + recreate schema (destructive)."""
    confirm = typer.confirm("This destroys all data. Continue?")
    if not confirm:
        typer.echo("aborted")
        raise typer.Exit()

    async def main() -> None:
        db = Database()
        db.connect()
        try:
            await reset_schema(db)
        finally:
            await db.close()

    asyncio.run(main())
    typer.echo("✓ schema reset")


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------


@app.command()
def health() -> None:
    """Connectivity + config sanity check."""
    settings = get_settings()
    problems: list[str] = []
    try:
        db = Database()
        db.connect()

        async def ping() -> bool:
            try:
                async with db.session() as session:
                    from sqlalchemy import text

                    result = await session.execute(text("SELECT 1"))
                    return result.scalar_one() == 1
            finally:
                await db.close()

        ok = asyncio.run(ping())
    except Exception as exc:  # noqa: BLE001
        ok = False
        problems.append(f"database: {exc}")

    source = settings.source
    if source == "google_places" and not settings.google_places_api_key:
        problems.append("google_places source configured but ILD_GOOGLE_PLACES_API_KEY is empty")
    try:
        create_source(source)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"source '{source}': {exc}")

    typer.echo(
        _json(
            {
                "ok": ok and not problems,
                "database": "ok" if ok else "error",
                "source": source,
                "problems": problems,
            },
            indent=2,
        )
    )
    if not ok or problems:
        sys.exit(1)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


@app.command()
def status(
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors"),
) -> None:
    """Live system snapshot: database, counts, model, review queue."""
    painter = Painter(enabled=not no_color)
    settings = get_settings()

    async def collect() -> dict:
        from ildrs.outreach.review import ReviewWorkflow
        from ildrs.storage import repositories as repo

        db = Database()
        db.connect()
        try:
            await init_schema(db)
            async with db.session() as session:
                data = {
                    "businesses": await repo.count_businesses(session),
                    "collected": await repo.count_collected(session),
                    "analyzed": await repo.count_analyzed(session),
                    "valid": await repo.count_valid_feature_vectors(session),
                    "leads": await repo.count_leads(session),
                    "high_quality": await repo.count_high_quality_leads(session),
                    "monitors": len(await repo.list_monitors(session)),
                    "last_verify": await repo.last_verification_time(session),
                    "review_pending": await ReviewWorkflow(db).count_pending(),
                }
                return data
        finally:
            await db.close()

    data = asyncio.run(collect())

    try:
        model = create_model(settings.rating_model)
        model_label = f"{model.name}@{model.version}"
    except Exception as exc:  # noqa: BLE001
        model_label = f"{settings.rating_model} (error: {exc})"

    rows = [
        ("SOURCE", status_token("ok"), settings.source),
        (
            "DATABASE",
            status_token("connected"),
            f"{data['businesses']} businesses",
        ),
        (
            "BUSINESSES",
            status_token("ok", label="COLLECTED"),
            f"{data['collected']}/{data['businesses']}",
        ),
        (
            "ANALYSIS",
            status_token("ok"),
            f"{data['analyzed']} analyzed · {data['valid']} valid vectors",
        ),
        (
            "LEADS",
            status_token("ok"),
            f"{data['leads']} rated · {data['high_quality']} high-value (≥70)",
        ),
        ("RATING MODEL", status_token("ok"), model_label),
        (
            "REVIEW QUEUE",
            status_token("ok"),
            f"{data['review_pending']} pending draft(s)",
        ),
        (
            "MONITORING",
            status_token("ok"),
            f"{data['monitors']} tracked send(s)",
        ),
        (
            "LAST VERIFY",
            status_token("ok", label="OK" if data["last_verify"] else "NEVER"),
            data["last_verify"] or "no verification yet",
        ),
    ]
    typer.echo(
        panel(
            painter,
            f"ILD-RS · STATUS v{__version__}",
            [readiness_row(painter, k, t, d) for k, t, d in rows],
        )
    )


# --------------------------------------------------------------------------
# review queue
# --------------------------------------------------------------------------


@review_cmd.command("list")
def review_list(
    limit: int = typer.Option(20, help="Max drafts to show"),
    all: bool = typer.Option(False, "--all", help="Include approved, sent, and rejected drafts"),
) -> None:
    """List outreach drafts in the review queue (pending by default)."""
    from ildrs.outreach.review import ReviewWorkflow

    async def main() -> list[dict]:
        db = Database()
        db.connect()
        try:
            await init_schema(db)
            workflow = ReviewWorkflow(db)
            if all:
                return await workflow.list_all(limit=limit)
            return await workflow.list_pending(limit=limit)
        finally:
            await db.close()

    items = asyncio.run(main())
    if not items:
        typer.echo("(review queue is empty)")
        return
    rows = [
        [
            item["id"][:8],
            item["business_name"] or item["lead_id"],
            f"{item['rating']:.0f}" if item["rating"] is not None else "-",
            item["channel"],
            item["review_status"],
            item["sent_status"],
            (item["created_at"] or "")[:19],
        ]
        for item in items
    ]
    _print_rows(
        ["ID", "BUSINESS", "RATING", "CHANNEL", "REVIEW", "SENT", "CREATED"],
        rows,
    )


@review_cmd.command("show")
def review_show(outreach_id: str) -> None:
    """Show a full draft: business context, message, and review history."""
    from ildrs.outreach.review import ReviewWorkflow

    async def main() -> dict | None:
        db = Database()
        db.connect()
        try:
            await init_schema(db)
            items = await ReviewWorkflow(db).list_all(limit=100000)
        finally:
            await db.close()
        return _resolve_review_id(items, outreach_id)

    item = asyncio.run(main())
    if item is None:
        typer.echo(f"outreach '{outreach_id}' not found", err=True)
        sys.exit(1)
    typer.echo(f"ID:        {item['id']}")
    typer.echo(f"LEAD:      {item['lead_id']}")
    typer.echo(f"BUSINESS:  {item['business_name']}")
    if item.get("business_website"):
        typer.echo(f"WEBSITE:   {item['business_website']}")
    if item.get("business_phone"):
        typer.echo(f"PHONE:     {item['business_phone']}")
    rating_line = (
        f"RATING:    {item['rating']:.1f} (confidence {item['confidence']:.2f})"
        if item["rating"] is not None
        else "RATING:    -"
    )
    typer.echo(rating_line)
    typer.echo(f"CHANNEL:   {item['channel']}")
    typer.echo(f"REVIEW:    {item['review_status']} · sent: {item['sent_status']}")
    typer.echo(f"CREATED:   {item['created_at']}")
    if item.get("note"):
        typer.echo(f"NOTE:      {item['note']}")
    typer.echo("")
    typer.echo("REASON:")
    typer.echo(item.get("reason") or "(none)")
    typer.echo("")
    typer.echo("MESSAGE:")
    typer.echo(item.get("message") or "(empty)")


@review_cmd.command("approve")
def review_approve(
    outreach_id: str,
    note: str = typer.Option("", help="Optional approval note"),
) -> None:
    """Approve a draft so it can be sent."""
    _review_decision("approve", outreach_id, note=note)


@review_cmd.command("reject")
def review_reject(
    outreach_id: str,
    note: str = typer.Option("", help="Why it was rejected (optional)"),
) -> None:
    """Reject a draft; it will never be sent."""
    _review_decision("reject", outreach_id, note=note)


@review_cmd.command("send")
def review_send(outreach_id: str) -> None:
    """Record that an approved draft was actually delivered."""
    _review_decision("send", outreach_id)


@review_cmd.command("prepare")
def review_prepare(
    lead_id: str,
    channel: str = typer.Option("", help="Channel (default: email)"),
) -> None:
    """Generate a verified-facts draft for a lead and queue it for review."""
    from ildrs.outreach.review import ReviewWorkflow

    async def main() -> dict:
        db = Database()
        db.connect()
        try:
            await init_schema(db)
            result = await ReviewWorkflow(db).prepare(lead_id=lead_id, channel=channel)
            if not result.ok:
                return {"ok": False, "error": result.error}
            data = result.data or {}
            return {"ok": True, "id": data.get("id", ""), "duplicate": bool(data.get("duplicate"))}
        finally:
            await db.close()

    outcome = asyncio.run(main())
    if not outcome["ok"]:
        typer.echo(f"prepare failed: {outcome['error']}", err=True)
        sys.exit(1)
    label = " (already pending)" if outcome["duplicate"] else ""
    typer.echo(f"prepared draft {outcome['id'][:8]}{label} — run `ildrs review list` to review it.")


def _resolve_review_id(items: list[dict], outreach_id: str) -> dict | None:
    """Resolve a (possibly abbreviated) review id to its full item."""
    matches = [item for item in items if item["id"].startswith(outreach_id)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        typer.echo(f"'{outreach_id}' is ambiguous; use the full id", err=True)
        sys.exit(1)
    return None


def _review_decision(action: str, outreach_id: str, *, note: str = "") -> None:
    from ildrs.outreach.review import ReviewWorkflow

    async def main() -> dict:
        db = Database()
        db.connect()
        try:
            await init_schema(db)
            workflow = ReviewWorkflow(db)
            items = await workflow.list_all(limit=100000)
            item = _resolve_review_id(items, outreach_id)
            if item is None:
                return {"ok": False, "error": f"outreach '{outreach_id}' not found"}
            full_id = item["id"]
            if action == "approve":
                result = await workflow.approve(outreach_id=full_id)
            elif action == "reject":
                result = await workflow.reject(outreach_id=full_id, note=note)
            else:
                result = await workflow.mark_sent(outreach_id=full_id)
            return {"ok": result.ok, "error": result.error, "data": result.data or {}}
        finally:
            await db.close()

    outcome = asyncio.run(main())
    if not outcome["ok"]:
        typer.echo(f"{action} failed: {outcome['error']}", err=True)
        sys.exit(1)
    data = outcome["data"]
    label = {"approve": "approved", "reject": "rejected", "send": "sent"}[action]
    parts = [
        f"review={data['review_status']}" if data.get("review_status") else "",
        f"sent={data['sent_status']}" if data.get("sent_status") else "",
    ]
    detail = " ".join(p for p in parts if p)
    typer.echo(f"{label} {outreach_id[:8]}" + (f"  {detail}" if detail else ""))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _print_stage_results(results: list[dict]) -> None:
    for result in results:
        stage = result.get("stage", "?")
        status = result.get("status", "?")
        counts = result.get("counts", {})
        summary = ", ".join(f"{k}={v}" for k, v in counts.items()) or ""
        typer.echo(f"[{status}] {stage}  {summary}")
    if not results:
        typer.echo("(no stages executed)")


def _print_rows(headers: list[str], rows: list[list[Any]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    typer.echo("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    typer.echo("  ".join("-" * w for w in widths))
    for row in rows:
        typer.echo("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))


def _json(value: Any, *, indent: int = 2) -> str:
    import json

    return json.dumps(value, indent=indent, default=str)


if __name__ == "__main__":
    app()
