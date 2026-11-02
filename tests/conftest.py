"""Shared pytest fixtures.

Database
    The test database (DATABASE_URL, ``tidewright_test`` under ``make test``)
    is created if needed, wiped and migrated to head once per session with
    the real Alembic migrations.

    ``db_session``  (default choice) one connection, one outer transaction
                    rolled back after the test. Every session the code under
                    test opens through ``app.core.db.session_scope()`` is bound
                    to the same connection, so commits inside the code become
                    savepoints and nothing leaks between tests.
    ``committed_db`` for tests that need several real connections (SKIP
                    LOCKED claims, concurrent workers). Data is committed for
                    real and every table is truncated afterwards. Mark such
                    tests ``@pytest.mark.committed``. ``committed_session``
                    gives a session on that engine for building rows.

Time
    ``frozen_clock`` freezes ``app.core.clock.now()`` at the world's "today"
    (2026-10-14 10:00 +01:00, i.e. 09:00 UTC) and lets the test move it.

Data
    ``factory`` builds rows with sensible defaults (see tests/factories.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core import clock, db
from app.core.config import REPO_ROOT, get_settings
from app.models import Base
from tests.factories import TODAY, Factory


def _assert_test_database(url: str) -> None:
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        pytest.exit(
            f"refusing to run tests against database {name!r}: the name must end in _test "
            "(use `make test`, which points DATABASE_URL at tidewright_test)",
            returncode=2,
        )


def _create_database_if_missing(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


def run_migrations(connection: Connection, revision: str = "head") -> None:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.attributes["connection"] = connection
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, revision)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = get_settings().database_url
    _assert_test_database(url)
    _create_database_if_missing(url)
    eng = db.make_engine(url, pool_size=5, max_overflow=5)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        run_migrations(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def db_connection(engine: Engine) -> Iterator[Connection]:
    conn = engine.connect()
    outer = conn.begin()
    try:
        yield conn
    finally:
        if outer.is_active:
            outer.rollback()
        conn.close()


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    factory = sessionmaker(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    previous = db.set_sessionmaker(factory)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        db.set_sessionmaker(previous)


@pytest.fixture
def committed_db(engine: Engine) -> Iterator[Engine]:
    """Real commits across connections; all tables truncated afterwards."""
    previous = db.set_sessionmaker(sessionmaker(bind=engine, expire_on_commit=False))
    try:
        yield engine
    finally:
        db.set_sessionmaker(previous)
        tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def committed_session(committed_db: Engine) -> Iterator[Session]:
    """A plain session on the real engine; the test commits explicitly.
    Pair with ``Factory(committed_session)`` to build committed rows."""
    session = Session(bind=committed_db, expire_on_commit=False)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def frozen_clock() -> Iterator[clock.FrozenClock]:
    with clock.frozen(TODAY) as fc:
        yield fc


@pytest.fixture
def factory(db_session: Session) -> Factory:
    return Factory(db_session)


@pytest.fixture
def today() -> datetime:
    return TODAY
