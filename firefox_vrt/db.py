"""Async SQLAlchemy engine + session factory + FastAPI dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy declarative base used by every model."""


_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def configure(database_url: str) -> None:
    """Initialize the engine + session factory. Idempotent within a process."""
    global _engine, _session_factory
    if _engine is not None:
        return
    _engine = create_async_engine(database_url, future=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def create_all() -> None:
    """Create tables for any model imported under `Base.metadata`."""
    assert _engine is not None, "configure() must be called first"
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session per request."""
    assert _session_factory is not None, "configure() must be called first"
    async with _session_factory() as session:
        yield session


def session_factory() -> async_sessionmaker[AsyncSession]:
    """For background tasks that need their own session outside a request."""
    assert _session_factory is not None, "configure() must be called first"
    return _session_factory


__all__ = ["Base", "configure", "create_all", "get_db", "session_factory"]
