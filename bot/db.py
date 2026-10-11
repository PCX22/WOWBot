"""Conexión a SQLite."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .models import Base


def make_engine(path: Path | str) -> AsyncEngine:
    url = "sqlite+aiosqlite:///:memory:" if str(path) == ":memory:" else f"sqlite+aiosqlite:///{Path(path).as_posix()}"
    engine = create_async_engine(url)

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


async def init_db(engine: AsyncEngine) -> async_sessionmaker:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)
