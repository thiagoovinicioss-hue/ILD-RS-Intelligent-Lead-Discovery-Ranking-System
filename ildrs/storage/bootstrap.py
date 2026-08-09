"""Schema bootstrap and version tracking."""

from __future__ import annotations

from sqlalchemy import text

from ildrs import __version__
from ildrs.storage.database import Database
from ildrs.storage.models import (
    AppMetaRow,
)


def schema_version_key() -> str:
    return "schema_version"


async def create_schema(db: Database) -> None:
    """Create tables if missing. Safe to run repeatedly."""
    from ildrs.storage.models import Base

    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema(db: Database) -> None:
    """Drop all tables (destructive — CLI only)."""
    from ildrs.storage.models import Base

    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def init(db: Database) -> None:
    """Ensure schema exists and record app version."""
    await create_schema(db)
    async with db.session() as session:
        row = await session.get(AppMetaRow, schema_version_key())
        if row is None:
            session.add(AppMetaRow(key=schema_version_key(), value=__version__))
            await session.commit()
        else:
            row.value = __version__
            await session.commit()


async def reset(db: Database) -> None:
    """Drop + recreate schema (destructive — CLI only)."""
    await drop_schema(db)
    await init(db)


def table_counts_query() -> str:
    return ";".join(
        f"SELECT '{t}' AS table_name, COUNT(*) AS n FROM {t}"
        for t in ("businesses", "leads", "outreach", "historical_outcomes", "jobs", "notifications")
    )


async def database_counts(db: Database) -> dict[str, int]:
    """Row counts per table, for health/status surfaces."""
    counts: dict[str, int] = {}
    async with db.session() as session:
        for t in (
            "businesses",
            "leads",
            "outreach",
            "historical_outcomes",
            "jobs",
            "notifications",
        ):
            result = await session.execute(text(f"SELECT COUNT(*) FROM {t}"))
            counts[t] = result.scalar_one()
    return counts


async def record_event(db: Database, key: str, value: str) -> None:
    async with db.session() as session:
        row = await session.get(AppMetaRow, key)
        if row is None:
            session.add(AppMetaRow(key=key, value=value))
        else:
            row.value = value
        await session.commit()
