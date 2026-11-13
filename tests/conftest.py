import asyncio
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from app.config import AppConfig
from app.crypto import EncryptionManager
from app.repository import DbSaltProvider

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from tenacity import wait_none  # noqa: E402

from app.api_client import HttpNotificationAPI, MockNotificationAPI  # noqa: E402
from app.db import create_all, init_engine  # noqa: E402


@event.listens_for(Engine, "connect")
def _fast_test_sqlite(dbapi_connection, _record):
    """Skip fsync for throwaway test databases.

    Every test gets a fresh SQLite file, and without this each commit (including the
    DDL in ``create_all``) waits on a disk flush. That was most of the suite's runtime.
    Durability is irrelevant here; transactions and rollback still behave normally.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA synchronous=OFF")
    cursor.execute("PRAGMA journal_mode=MEMORY")
    cursor.close()


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch):
    """Retry tests check *that* a call is retried, not how long the backoff lasts.

    Tenacity copies the decorator's settings on every call, so swapping ``wait`` on the
    shared retry object removes the real 1s/2s sleeps while keeping the attempt count
    and retry predicate under test.
    """
    for attr in vars(HttpNotificationAPI).values():
        retrying = getattr(attr, "retry", None)
        if retrying is not None and hasattr(retrying, "wait"):
            monkeypatch.setattr(retrying, "wait", wait_none())


@pytest.fixture(autouse=True)
def no_mock_api_latency(monkeypatch):
    """``MockNotificationAPI`` sleeps 0.1s per call to feel realistic in the UI; tests
    don't need it. Tests that set ``_sleep`` themselves still can."""
    original_init = MockNotificationAPI.__init__

    def fast_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._sleep = 0

    monkeypatch.setattr(MockNotificationAPI, "__init__", fast_init)


def _aiosqlite_worker_threads() -> set[threading.Thread]:
    return {t for t in threading.enumerate() if "_connection_worker_thread" in t.name and t.is_alive()}


@pytest.fixture(scope="function", autouse=True)
async def isolate_database(tmp_path):
    """
    Ensure each test uses a completely isolated temporary database.
    This fixture runs automatically for all tests.

    It also fails a test that leaves an aiosqlite connection open. Such a connection
    outlives the test's event loop and later raises "Event loop is closed" in its worker
    thread, which pytest blames on whatever unrelated test is running at the time.
    A test that calls ``init_engine`` itself must dispose that engine.
    """
    from app import db

    threads_before = _aiosqlite_worker_threads()
    # Save original state
    original_engine = db.engine
    original_session_local = db.SessionLocal

    # Create a unique test database
    test_db_path = tmp_path / f"test_{os.getpid()}_{id(tmp_path)}.db"

    # Initialize with test database
    init_engine(str(test_db_path))

    yield str(test_db_path)

    # Clean up: dispose the engine properly
    if db.engine is not None:
        try:
            await db.engine.dispose()
        except Exception:
            pass

    # Restore original state (or set to None for tests)
    db.engine = original_engine
    db.SessionLocal = original_session_local

    leaked = _aiosqlite_worker_threads() - threads_before
    for thread in leaked:
        thread.join(timeout=1)  # a disposed connection's thread may still be shutting down
    leaked = {t for t in leaked if t.is_alive()}
    if leaked:
        pytest.fail(
            f"Test left {len(leaked)} aiosqlite connection(s) open; dispose any engine it creates.",
            pytrace=False,
        )


@pytest.fixture(scope="function")
def temp_db(isolate_database):
    """Create a temporary database for testing (uses isolate_database)."""
    return isolate_database


@pytest.fixture(scope="session")
def schema_template(tmp_path_factory):
    """Build the schema once per session with the real ``create_all``.

    ``initialized_db`` copies this file instead of re-running DDL for every test.
    Tests that exercise ``create_all`` or migrations themselves call them directly.
    """
    from app import db

    path = tmp_path_factory.mktemp("schema") / "template.db"
    saved = (db.engine, db.SessionLocal)

    async def build():
        init_engine(str(path))
        try:
            await create_all()
        finally:
            await db.engine.dispose()

    asyncio.run(build())
    db.engine, db.SessionLocal = saved
    return path


@pytest.fixture(scope="function")
def initialized_db(temp_db, schema_template):
    """A temporary database with every table created.

    ``isolate_database`` has created the engine but not connected yet, so the template
    is copied into place before SQLite first opens the file.
    """
    shutil.copyfile(schema_template, temp_db)
    return temp_db


@pytest.fixture
def mock_config():
    """Create a mock config for testing."""
    return AppConfig(
        master_key="test-key-for-main",
        api_hosts={
            "development": "http://dev.test.com",
            "staging": "http://staging.test.com",
        },
        use_mock_api=True,
        database_path=":memory:",
        max_concurrency=5,
    )


@pytest.fixture
def mock_encryption(mock_config):
    """Create a mock encryption manager."""
    return EncryptionManager(mock_config.master_key, salt_provider=DbSaltProvider())


@pytest.fixture(scope="function", autouse=True)
def reset_mock_permission_store():
    """MockNotificationAPI keeps permissions in a class-level store so the multi-call
    permission flow is coherent in mock mode. Clear it so tests cannot leak into each other.
    """
    from app.api_client import MockNotificationAPI

    MockNotificationAPI.reset_permission_store()
    yield
    MockNotificationAPI.reset_permission_store()


@pytest.fixture(scope="function", autouse=True)
def reset_user_cache():
    """``list_users`` memoises decrypted rows for 12 hours.  Each test gets a fresh
    temporary database, so a surviving entry from a previous test would be served
    against the wrong data.  Clear it on both sides of the test.
    """
    from app import repository

    repository._invalidate_user_cache()
    yield
    repository._invalidate_user_cache()
