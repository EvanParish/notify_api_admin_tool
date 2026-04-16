import logging
import os
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine: AsyncEngine | None = None
SessionLocal: async_sessionmaker | None = None


def init_engine(database_path: str) -> AsyncEngine:
    global engine, SessionLocal
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_async_engine(
        url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def create_all() -> None:
    if engine is None:
        raise RuntimeError("Engine not initialized")
    from . import models

    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    await _apply_migrations()


_MIGRATIONS: list[tuple[str, str, str]] = [
    ("templates", "communication_item_id", "VARCHAR"),
]


async def _apply_migrations() -> None:
    """Add missing columns to existing tables."""
    async with engine.begin() as conn:
        for table, column, col_type in _MIGRATIONS:
            cols = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in cols}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                logger.info("Migrated: added %s.%s", table, column)


@asynccontextmanager
async def get_session():
    if SessionLocal is None:
        raise RuntimeError("SessionLocal not initialized")
    async with SessionLocal() as session:
        yield session


async def dispose_engine() -> None:
    """Dispose the engine to clean up connections properly."""
    global engine
    if engine is not None:
        await engine.dispose()
