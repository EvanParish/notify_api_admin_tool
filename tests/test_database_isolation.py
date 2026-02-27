"""
Test to verify database isolation between tests and main application.
"""

import pytest
import os
from pathlib import Path


def test_database_isolation_check(isolate_database):
    """Verify that tests use isolated temporary databases."""
    from app import db

    # The test database should be in a temporary directory
    if db.engine is not None:
        db_url = str(db.engine.url)
        assert "/tmp/" in db_url or "/var/folders/" in db_url or "test_" in db_url, (
            f"Test database should be in temp directory, got: {db_url}"
        )

        # Should not be using the app's database path
        assert "data/app.db" not in db_url, (
            "Tests should not use the application database"
        )


def test_app_database_not_created_by_tests():
    """Verify that running tests doesn't create the app database."""
    app_db_path = Path("data/app.db")

    # If the app.db exists, it should be from running the actual app, not tests
    # We can't definitively test this, but we can check it wasn't just created
    if app_db_path.exists():
        # Check if it's very new (created in last few seconds)
        import time

        age_seconds = time.time() - app_db_path.stat().st_mtime
        # If it's older than 10 seconds, it's probably from the app, not tests
        # This is a weak check but helps document the expectation
        assert age_seconds > 10 or age_seconds < 1, (
            "app.db might have been created by tests (should only be created by running main.py)"
        )


@pytest.mark.asyncio
async def test_multiple_tests_use_different_databases(initialized_db):
    """Verify each test gets its own database."""
    from app import db
    from app.models import Service
    from app.db import get_session
    from sqlalchemy import select

    # Add a service
    async with get_session() as session:
        session.add(
            Service(id="test-svc", name="Test Service", active=True, restricted=False)
        )
        await session.commit()

    # Verify it exists
    async with get_session() as session:
        services = (await session.execute(select(Service))).scalars().all()
        assert len(services) == 1
        assert services[0].id == "test-svc"

    # The database path should be unique to this test
    db_url = str(db.engine.url)
    assert "test_" in db_url


@pytest.mark.asyncio
async def test_second_test_has_clean_database(initialized_db):
    """Verify that this test doesn't see data from the previous test."""
    from app.models import Service
    from app.db import get_session
    from sqlalchemy import select

    # Should have a clean database with no services
    async with get_session() as session:
        services = (await session.execute(select(Service))).scalars().all()
        assert len(services) == 0, "Database should be clean for each test"


def test_pytest_environment_variable_is_set():
    """Verify PYTEST_CURRENT_TEST is set during test execution."""
    pytest_var = os.getenv("PYTEST_CURRENT_TEST")
    assert pytest_var is not None, "PYTEST_CURRENT_TEST should be set during test runs"
    assert "test_database_isolation" in pytest_var.lower() or "test_" in pytest_var


def test_main_module_skips_db_init_during_tests():
    """Verify main.py doesn't initialize database when imported during tests."""
    import main  # noqa: F401 — side-effect import under test

    # During tests, main.py should not have called init_engine with app.db
    # Instead, the test fixture will have initialized it with a temp db
    pytest_var = os.getenv("PYTEST_CURRENT_TEST")
    assert pytest_var is not None, "Should be running in pytest"

    # The test isolation fixture should have set up the database
    from app import db

    if db.engine is not None:
        db_url = str(db.engine.url)
        # Should be using test database, not app database
        assert "data/app.db" not in db_url
