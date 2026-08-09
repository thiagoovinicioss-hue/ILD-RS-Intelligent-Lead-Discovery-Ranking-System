"""Async SQLAlchemy engine/session management."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ildrs.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def create_engine(url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    db_url = url or settings.database_url

    kwargs: dict = {}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}

    engine = create_async_engine(db_url, **kwargs)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        if db_url.startswith("sqlite"):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Database:
    """Owns engine + session factory for the application lifecycle."""

    def __init__(self, url: str | None = None) -> None:
        self.engine: AsyncEngine | None = None
        self.sessions: async_sessionmaker[AsyncSession] | None = None
        self._url = url

    def connect(self) -> None:
        if self.engine is None:
            self.engine = create_engine(self._url)
        self.sessions = session_factory(self.engine)

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self.sessions is None:
            raise RuntimeError("database not connected")
        async with self.sessions() as session:
            yield session

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
        self.engine = None
        self.sessions = None

    @property
    def is_connected(self) -> bool:
        return self.engine is not None
