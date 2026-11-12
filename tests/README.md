# Tests

Run everything from the repository root. pytest is configured in `pyproject.toml` (`[tool.pytest.ini_options]`).

```bash
uv run pytest tests/                                     # full suite, about 15 seconds
uv run pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=100
uv run pytest tests/test_utils.py -v                     # one file
uv run pytest tests/test_utils.py::test_extract_placeholders_empty -v
uv run pytest tests/ -k "encryption" -v                  # by name pattern
uv run pytest tests/ --cov=app --cov-report=html         # browse htmlcov/index.html
```

**Coverage must stay at 100%.** Add tests with every feature and bug fix. Use `# pragma: no cover` only for code that can't run under tests, such as NiceGUI handlers that need a live browser session. If code can't be reached at all, delete it instead of excluding it.

## Layout

| File | Covers |
|------|--------|
| `test_api_client.py` | `HttpNotificationAPI` requests and retries, and `MockNotificationAPI` |
| `test_artifacts.py` | Timestamped JSON artifacts, file permissions, and atomic rewrites |
| `test_callback_helpers.py` | Service callback validation and payload building |
| `test_config.py` | `AppConfig` and `load_config()` environment parsing and defaults |
| `test_crypto.py` | `EncryptionManager` and salt handling |
| `test_database_isolation.py` | Proves tests never touch `data/app.db` |
| `test_db.py` | Engine setup, `create_all`, and column migrations |
| `test_main.py` | UI pages (including What's New), shell, helpers (including CSV export), state, and app wiring |
| `test_permission_helpers.py` | Service permission diffing, confirmation tiers, and production detection |
| `test_progress_widgets.py` | Sync progress bar and label updates |
| `test_repository.py` | CRUD, upserts, filters, and user field encryption |
| `test_sync.py` | `SyncManager` pulling each entity into the cache |
| `test_sync_handlers.py` | `handle_entity_sync` and `handle_full_sync` |
| `test_sync_progress.py` | `SyncProgress` counting and throttling |
| `test_utils.py` | Placeholder extraction and recipient validation |
| `test_version.py` | App version and release notes, and that `CHANGELOG.md`, `pyproject.toml`, and the Dockerfile agree |
| `testing_data.py` | Sample API payloads used by the sync tests |

## Fixtures (`conftest.py`)

These run automatically for every test:

- **`isolate_database`** points the app at a fresh SQLite file under pytest's `tmp_path` and restores the original engine afterwards.
- **`no_retry_backoff`** removes tenacity's real 1s/2s waits. Retry tests still check attempt counts and which errors are retried.
- **`no_mock_api_latency`** zeroes `MockNotificationAPI`'s 0.1s simulated latency.
- **`reset_mock_permission_store`** and **`reset_user_cache`** clear class-level and module-level caches between tests.

These you request yourself:

- **`initialized_db`** gives a database with every table created. It copies a schema built once per session by the real `create_all`. Tests of `create_all` or migrations call them directly instead.
- **`mock_config`** is an `AppConfig` with `development` and `staging` hosts and mock mode on.
- **`mock_encryption`** is an `EncryptionManager` using the test salt provider.

Test databases skip disk flushes (`PRAGMA synchronous=OFF`). Durability doesn't matter for throwaway files, and this is most of what keeps the suite fast.

`app/ui/state.py` also checks `PYTEST_CURRENT_TEST`. During tests it skips opening `data/app.db` and treats admin credentials as present. Tests that need the "missing credentials" path set it to an empty string with `patch.dict`.

## Testing UI pages

NiceGUI widgets need a running server, so page tests replace them with mocks. Helpers in `test_main.py`:

- **`mock_page_ui(mod_path)`** patches every `ui.*` factory a page module uses, so the page function can run start to finish.
- **`_record_page(mod_path)`** does the same, but records each widget it creates. Look widgets up with `by_label()` / `by_text()`, set `.value`, and call the handlers a page registered through `on_value_change` (kept in `.handlers`).
- **`_capture_page_export(...)`** runs a page and captures what it passed to `add_export_button`, so a test can build the real CSV.
- **`SharedTestState`** is a lightweight stand-in for `AppState`. Save and restore `_st.config` / `_st.state` around any test that replaces them.

`EXPORT_COLUMN_REVIEW` in `test_main.py` classifies every model column as exported or denied. Adding a column to `app/models.py` fails the suite until the column is added there, which keeps secrets out of CSV exports by review rather than by naming luck.
