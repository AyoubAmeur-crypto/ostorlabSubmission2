"""Database engine and sessions (SQLAlchemy 2.0, sync, psycopg 3).

One engine per process, created lazily from settings. Code that needs a
session uses :func:`session_scope`::

    from app.core.db import session_scope

    with session_scope() as session:
        session.add(thing)
    # committed here; rolled back if the block raised

Rules (docs/architecture.md, "Conventions"):

* Functions that take a ``session`` argument never commit; they ``flush()`` if
  they need ids. The caller that opened the scope owns the transaction.
* ``expire_on_commit`` is off, so objects stay readable after the scope closes
  (handy for logging and API responses) but are not refreshed automatically.
* Long-running loops (worker, scheduler) open one short scope per unit of
  work, never one scope for the whole process.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def make_engine(url: str | None = None, **kwargs: object) -> Engine:
    settings = get_settings()
    options: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_pool_size,
        "echo": settings.db_echo,
    }
    options.update(kwargs)
    return create_engine(url or settings.database_url, **options)


def get_engine() -> Engine:
    """The process-wide engine (created on first use)."""
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _factory


def set_sessionmaker(factory: sessionmaker[Session] | None) -> sessionmaker[Session] | None:
    """Swap the session factory and return the previous one.

    Tests use this to bind every session the code under test opens to one
    connection inside an outer transaction (see tests/conftest.py). Passing
    ``None`` resets to the default factory on next use.
    """
    global _factory
    previous = _factory
    _factory = factory
    return previous


def dispose_engine() -> None:
    """Close pooled connections and forget the engine (e.g. after fork)."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _factory = None


def new_session() -> Session:
    """A bare session; prefer :func:`session_scope` unless you manage commits yourself."""
    return get_sessionmaker()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error, always close."""
    session = new_session()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def ping() -> bool:
    """True when the database answers ``SELECT 1``. Used by /healthz."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health check reports, never raises
        return False
