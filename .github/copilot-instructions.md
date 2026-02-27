# Copilot Instructions

## Build & Run

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app (NiceGUI web UI on port 8080)
python main.py

# Run with Docker
docker compose up --build
```

## Testing

Testing must be run before and after making changes to ensure everything is working correctly. 
Add tests for new features and bug fixes, and run the full test suite to catch any regressions.
After each change, ensure test coverage is at 100% to maintain confidence in the codebase.

```bash
# Run all tests
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_utils.py -v

# Run a single test function
python -m pytest tests/test_utils.py::test_extract_placeholders_empty -v

# Run tests matching a pattern
python -m pytest tests/ -k "encryption" -v

# Run with coverage
python -m pytest tests/ --cov=app --cov-report=term-missing
```

pytest is configured with `asyncio_mode = auto` — async test functions are collected automatically without needing `@pytest.mark.asyncio`.

## Linting

Linting is configured with Ruff, using the default settings. Run the following commands to check for linting errors and automatically fix them:

```bash
ruff check .
ruff format .
```

## Architecture

This is an admin dashboard for the [VA Notification API](https://github.com/department-of-veterans-affairs/notification-api). It provides a NiceGUI web UI for managing services, templates, API keys, users, providers, and sending test notifications across multiple VA environments (dev, perf, staging, prod).

### Core layers

- **`main.py`** (~2950 lines) — The entire NiceGUI UI: pages, components, event handlers, and app lifecycle. All UI code lives in this single file.
- **`app/ui/sync_handlers.py`** — Generic `handle_entity_sync()` function that consolidates all per-entity sync handlers. Uses late import of `main` to access globals (will be cleaned up in later phases).
- **`app/api_client.py`** — `NotificationAPI` base class with `HttpNotificationAPI` (real) and `MockNotificationAPI` (dev/test) implementations. HTTP client uses `httpx`. Notification sending uses JWT auth (HS256, signed with service API secret).
- **`app/sync.py`** — `SyncManager` pulls data from the remote API into the local SQLite cache. Uses `asyncio.Semaphore` for concurrency control. Syncs per-environment.
- **`app/models.py`** — SQLAlchemy ORM models. Most entities use composite keys of `(id, environment)` to store data from multiple environments in one database.
- **`app/repository.py`** — Async CRUD functions using `get_session()` context manager. Archived records (names starting with `_archive`) are filtered out automatically.
- **`app/db.py`** — Async SQLAlchemy engine setup with `aiosqlite`. Module-level globals `engine` and `SessionLocal` are initialized via `init_engine()`.
- **`app/crypto.py`** — `EncryptionManager` uses Fernet encryption with PBKDF2-derived key from `MASTER_KEY` env var. Salt is stored in the `settings` table.
- **`app/config.py`** — Pydantic `AppConfig` loaded from environment variables via `python-dotenv`.

### Multi-environment design

The app manages data across multiple VA API environments simultaneously. Most models include an `environment` column as part of their composite primary key. The `SyncManager` is instantiated per-environment, and the UI allows switching which environment to view/sync.

### Authentication model

Two auth layers exist:
1. **Basic Auth** — For admin API calls (listing services, templates, etc.). Credentials stored encrypted in the `settings` table.
2. **JWT Bearer** — For sending notifications. Signed with a service's API secret from `local_api_keys`. Secrets are Fernet-encrypted at rest.

## Key Conventions

- **Async everywhere**: All database and API operations are async. Tests use `asyncio_mode = auto`.
- **Database isolation in tests**: The `conftest.py` `isolate_database` fixture automatically gives each test its own temporary SQLite database by swapping the module-level `db.engine` and `db.SessionLocal`. Use the `initialized_db` fixture when tests need tables created.
- **Environment variables**: Configured via `.env` file (see `.env.example`). `MASTER_KEY` is required. `USE_MOCK_API=true` enables the mock client for development without a live API.
- **`API_PUBLIC_HOSTS`**: JSON dict mapping environment names to base URLs (e.g., `{"dev": "http://localhost:6011", "staging": "https://staging-api.va.gov/vanotify"}`).
