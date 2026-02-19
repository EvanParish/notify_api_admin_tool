import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


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
