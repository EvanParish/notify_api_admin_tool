import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import init_engine, create_all


@pytest.fixture(scope="function", autouse=True)
async def isolate_database(tmp_path):
    """
    Ensure each test uses a completely isolated temporary database.
    This fixture runs automatically for all tests.
    """
    from app import db

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


@pytest.fixture(scope="function")
def temp_db(isolate_database):
    """Create a temporary database for testing (uses isolate_database)."""
    return isolate_database


@pytest.fixture(scope="function")
async def initialized_db(temp_db):
    """Create and initialize a temporary database with tables."""
    await create_all()
    return temp_db
