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

This repo uses python 3.13+ features, so ensure your environment is set up with Python 3.10 or higher to avoid syntax errors and take advantage of the latest language features.

## Architecture

This is an admin dashboard for the [VA Notification API](https://github.com/department-of-veterans-affairs/notification-api). It provides a NiceGUI web UI for managing services, templates, API keys, users, providers, and sending test notifications across multiple VA environments (dev, perf, staging, prod).

### Core layers

- **`main.py`** (~83 lines) — Application entry point: CSS/JS head HTML, imports `app.ui.pages` to register routes, and calls `ui.run()`.
- **`app/ui/pages/`** — All page modules. Each file contains one `@ui.page` route:
  - `dashboard.py` — Dashboard with metrics (`/`)
  - `services.py` — Services page with search filter (`/services`)
  - `templates.py` — Templates page with search/type/service filters (`/templates`)
  - `users.py` — Users page with search/state filters (`/users`)
  - `sms_senders.py` — SMS Senders page (`/sms-senders`)
  - `provider_details.py` — Provider Details page (`/provider-details`)
  - `comm_items.py` — Communication Items page (`/communication-items`)
  - `inbound_numbers.py` — Inbound Numbers page (`/inbound-numbers`)
  - `api_keys.py` — API Keys page with create/revoke dialogs (`/api-keys`)
  - `api_key_service.py` — API Key Email Generator (`/api-key-service`)
  - `send.py` — Send Notification page (`/send`)
  - `bulk_send.py` — Bulk Send Notification page (`/bulk-send`)
  - `settings_page.py` — Settings page with URL/auth/key forms (`/settings`)
  - `__init__.py` — Imports all page modules to trigger `@ui.page` registration
- **`app/ui/state.py`** — Application state globals (`config`, `encryption`, `state`), `AppState` dataclass, `build_api_client()`, auth helpers, startup/shutdown handlers.
- **`app/ui/helpers.py`** — Reusable UI utilities: `metric_card`, `make_sortable`, copyable slots, formatting functions, `parse_recipients`.
- **`app/ui/email_helpers.py`** — Email rotation constants and `_build_key_email()` for API key email generation.
- **`app/ui/shell.py`** — Monkey-patches for NiceGUI edge cases, theme helpers, and `build_shell()` sidebar/header builder.
- **`app/ui/sync_handlers.py`** — Generic `handle_entity_sync()` and `handle_full_sync()` that consolidate all sync dispatch.
- **`app/api_client.py`** — `NotificationAPI` base class with `HttpNotificationAPI` (real) and `MockNotificationAPI` (dev/test) implementations. HTTP client uses `httpx`. Notification sending uses JWT auth (HS256, signed with service API secret).
- **`app/sync.py`** — `SyncManager` pulls data from the remote API into the local SQLite cache. Uses `asyncio.Semaphore` for concurrency control. Syncs per-environment. Delegates all DB writes to `repository.py` upsert functions.
- **`app/models.py`** — SQLAlchemy ORM models. Most entities use composite keys of `(id, environment)` to store data from multiple environments in one database.
- **`app/repository.py`** — Async CRUD functions using `get_session()` context manager. Includes bulk `upsert_*` functions for sync and `list_service_ids()`. Archived records (names starting with `_archive`) are filtered out automatically.
- **`app/db.py`** — Async SQLAlchemy engine setup with `aiosqlite`. Module-level globals `engine` and `SessionLocal` are initialized via `init_engine()`.
- **`app/crypto.py`** — `EncryptionManager` uses Fernet encryption with PBKDF2-derived key from `MASTER_KEY` env var. Accepts a `SaltProvider` protocol for salt storage (decoupled from DB). `DbSaltProvider` in `repository.py` provides the DB-backed implementation.
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
