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


# Columns added after the original schema shipped; existing databases are
# upgraded in place with ALTER TABLE (SQLite supports ADD COLUMN).
_BUSINESS_UPGRADE_COLUMNS: dict[str, str] = {
    "website_analysis": "JSON",
    "social_links": "JSON",
    "recent_activity": "DATETIME",
    "is_duplicate": "BOOLEAN",
    "duplicate_of": "VARCHAR(36)",
    "deduped_at": "DATETIME",
}


async def _ensure_columns(db: Database) -> None:
    """Add missing columns to existing tables (no-op on fresh schemas)."""
    async with db.engine.begin() as conn:
        for table, columns in (("businesses", _BUSINESS_UPGRADE_COLUMNS),):
            existing = {
                row[1]
                for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
            }
            for column, ddl in columns.items():
                if column in existing:
                    continue
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        # create_all skips indexes on existing tables; recreate them explicitly
        for index in (
            "CREATE INDEX IF NOT EXISTS ix_businesses_duplicate ON businesses (is_duplicate)",
        ):
            await conn.execute(text(index))


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
    await _ensure_columns(db)
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
