"""SQLAlchemy connection management with safe transaction handling."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

import config


LOGGER = logging.getLogger(__name__)
_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""


def utc_now() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def get_database_url() -> str:
    """Return the configured SQLAlchemy database URL."""
    url = getattr(config, "DATABASE_URL", "") or ""
    if url:
        return url
    if getattr(config, "APP_ENV", "development") == "test":
        return "sqlite+pysqlite:///:memory:"
    raise RuntimeError("DATABASE_URL is required for production database access.")


def get_engine() -> Engine:
    """Return a process-wide SQLAlchemy engine with connection pooling."""
    global _ENGINE, _SESSION_FACTORY
    if _ENGINE is None:
        database_url = get_database_url()
        connect_args: dict[str, Any] = {}
        engine_kwargs: dict[str, Any] = {
            "pool_pre_ping": True,
            "future": True,
        }
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            engine_kwargs["pool_pre_ping"] = False
            if database_url.endswith(":memory:"):
                engine_kwargs["poolclass"] = StaticPool
        else:
            engine_kwargs.update(
                {
                    "pool_size": int(getattr(config, "DB_POOL_SIZE", 5)),
                    "max_overflow": int(getattr(config, "DB_MAX_OVERFLOW", 10)),
                    "pool_recycle": 1800,
                }
            )
        _ENGINE = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
        _SESSION_FACTORY = sessionmaker(bind=_ENGINE, expire_on_commit=False, future=True)
    return _ENGINE


def reset_engine() -> None:
    """Dispose the cached engine; used by tests when DATABASE_URL changes."""
    global _ENGINE, _SESSION_FACTORY
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _SESSION_FACTORY = None


def create_all_for_local_tests() -> None:
    """Create ORM tables for test/local fallback databases."""
    from database import models  # noqa: F401

    Base.metadata.create_all(get_engine())


@contextmanager
def db_session() -> Iterator[Session]:
    """Yield a SQLAlchemy session with commit/rollback handling."""
    get_engine()
    if _SESSION_FACTORY is None:
        raise RuntimeError("Database session factory was not initialized.")
    session = _SESSION_FACTORY()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        LOGGER.exception("Database transaction failed.")
        raise
    finally:
        session.close()


def check_database() -> bool:
    """Return whether a lightweight database connection succeeds."""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
