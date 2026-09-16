# Notification API Admin Dashboard — Architecture

Python 3.13 · NiceGUI · SQLite · SQLAlchemy · Pydantic
Target API: [VA Notification API](https://github.com/department-of-veterans-affairs/notification-api)

## 1. System Architecture & Tech Stack

* **Frontend/UI:** NiceGUI
   * Layout: App Shell — left sidebar navigation, top status bar with API health indicator, main content area.
   * State: `AppState` dataclass in `app/ui/state.py` plus NiceGUI bindings and SQLite-cached data.
* **Local Database:** SQLite via async SQLAlchemy ORM (`aiosqlite` driver).
   * Purpose: Local cache of remote API resources and persistence of user configuration (auth credentials, API keys).
* **Security & Encryption:**
   * Library: `cryptography` (Fernet symmetric encryption).
   * Scope: API key secrets, Basic Auth credentials, and synced user identity fields (`users.name`, `users.email_address`) are encrypted at rest in SQLite.
   * Key derivation: PBKDF2-HMAC from `MASTER_KEY` env var. Salt stored in the `settings` table via `DbSaltProvider`.
* **Concurrency:**
   * All network and database I/O is async (`asyncio`).
   * `SyncManager` uses `asyncio.Semaphore` (default 25) to cap concurrent API calls.

### Module Layout

```
main.py                     Entry point — head HTML, imports pages, calls ui.run()
app/
├── api_client.py            NotificationAPI base + HttpNotificationAPI + MockNotificationAPI
├── config.py                Pydantic AppConfig loaded from environment / .env
├── crypto.py                EncryptionManager (Fernet + PBKDF2), SaltProvider protocol
├── db.py                    Async SQLAlchemy engine, session factory, create_all()
├── models.py                ORM models (10 tables, most with composite environment key)
├── repository.py            Async CRUD, upsert functions, DbSaltProvider, user identity encryption helpers
├── sync.py                  SyncManager — pulls remote API data into local cache
└── ui/
    ├── state.py             AppState, module globals (config, encryption), startup/shutdown
    ├── shell.py             build_shell() — sidebar, header, theme toggle
    ├── helpers.py           Reusable UI utilities (metric cards, CSV export, copy-to-clipboard)
    ├── sync_handlers.py     handle_entity_sync(), handle_full_sync()
    ├── email_helpers.py     API key email generation (new service, rotation, forced rotation)
    ├── callback_helpers.py  Service callback validation, payload building
    ├── http_errors.py       API error normalization (extract_error_message, format_http_error)
    ├── permission_helpers.py Service permission classification, diffing, confirmation tiers, audit records
    ├── artifacts.py         Timestamped JSON artifact write/atomic-rewrite (NiceGUI-free)
    └── pages/               One file per @ui.page route (13 pages)
```

## 2. Authentication & Configuration

Two distinct auth layers:

### A. Global Admin Auth (Basic Auth)
* **Usage:** Fetching administrative data — listing services, templates, users, providers, etc.
* **Routes:** `GET /service`, `GET /service/{id}/template`, etc.
* **Storage:** Username and password per environment, Fernet-encrypted in the `settings` table.
* **Validation:** `ensure_admin_auth(env)` checks credentials exist before sync operations.

### B. Service-Level Auth (Bearer Token / JWT)
* **Usage:** Sending notifications.
* **Routes:** `POST /v2/notifications/email`, `POST /v2/notifications/sms`.
* **Mechanism:** JWT signed with HS256 using a service's API secret.
   * Payload: `{"iss": service_id, "iat": current_time}`
   * Headers: `{"typ": "JWT", "alg": "HS256"}`
* **Storage:** When API keys are created via the API, the returned secret is Fernet-encrypted and stored in the `local_api_keys` table.

## 3. Database Schema (Local Cache)

All tables are defined in `app/models.py`. Most entities use composite primary keys of `(id, environment)` to store data from multiple VA environments in one database.

### Core Cached Tables

* **services** — `(id, environment)` PK
   * `name`, `active`, `restricted`
   * `message_limit`, `rate_limit`, `research_mode`, `count_as_live`, `prefix_sms`
   * `email_from`, `permissions` (JSON text), `organisation_type`, `crown`
   * `go_live_at`, `created_by`

* **templates** — `(id)` PK, `environment` indexed
   * `service_id`, `name`, `template_type` (enum: email, sms), `content`, `subject`, `version`
   * `archived`, `hidden`, `process_type`, `created_at`, `updated_at`, `created_by`, `reply_to_email`

* **api_keys** — `(id)` PK, `environment` indexed
   * `service_id`, `name`, `key_type`, `expiry_date`, `created_by`
   * `created_at`, `last_used_at`, `revoked`, `version`

* **sms_senders** — `(id)` PK, `environment` indexed
   * `service_id`, `sms_sender`, `is_default`, `archived`, `description`
   * `provider_id`, `provider_name`, `inbound_number_id`
   * `rate_limit`, `rate_limit_interval`, `sms_sender_specifics` (JSON)
   * `created_at`, `updated_at`

* **users** — `(id, environment)` PK
   * `email_address`, `name`, `state`, `platform_admin`, `blocked`, `auth_type`
   * `mobile_number`, `failed_login_count`, `logged_in_at`, `password_changed_at`
   * `current_session_id`, `identity_provider_user_id`
   * `additional_information` (JSON), `permissions` (JSON), `services` (JSON), `organisations` (JSON)
   * Note: `email_address` and `name` are encrypted at rest and decrypted at runtime for display/search/export.

* **provider_details** — `(id, environment)` PK
   * `active`, `created_by_name`, `current_month_billable_sms`, `display_name`
   * `identifier`, `load_balancing_weight`, `notification_type`, `priority`
   * `supports_international`, `updated_at`

* **communication_items** — `(id, environment)` PK
   * `name`, `va_profile_item_id`, `default_send_indicator`

* **inbound_numbers** — `(id, environment)` PK
   * `number`, `provider`, `active`, `self_managed`
   * `service_id`, `auth_parameter`, `url_endpoint`

* **service_callbacks** — `(id, environment)` PK
   * `service_id`, `url`, `callback_type`, `callback_channel`
   * `created_at`, `updated_at`, `updated_by_id`
   * `notification_statuses` (JSON), `include_provider_payload`

### Local-Only Tables

* **local_api_keys**
   * `id` (auto-increment PK), `service_id`, `environment`
   * `key_name`, `key_secret` (Fernet-encrypted), `key_type` (enum: normal, team, test)

* **settings** — key-value store
   * `key` (PK), `value` (Text), `updated_at` (DateTime)
   * Stores: base URLs, encrypted auth credentials, encryption salt

## 4. UI/UX

### A. Global Layout

* **Sidebar Navigation:**
   * **Overview:** Dashboard (`/`)
   * **Notifications:** Send (`/send`), Bulk Send (`/bulk-send`)
   * **Resources:** Services (`/services`), Templates (`/templates`), API Keys (`/api-keys`), Create API Key (`/api-key-service`), Users (`/users`), SMS Senders (`/sms-senders`), Inbound Numbers (`/inbound-numbers`), Communication Items (`/communication-items`), Provider Details (`/provider-details`)
   * **Configuration:** Settings (`/settings`)

* **Status Bar:**
   * API health badge (online/offline per environment)
   * Sync status label
   * Multi-environment view selector
   * Per-environment sync enable/disable checkboxes
   * Dark/light theme toggle

### B. Send Notification Page (`/send`)
Visual client for the notification API:
* **Environment** dropdown — switches API base URL.
* **Service** dropdown — searchable, filters available API keys and templates.
* **API Key** dropdown — lists `local_api_keys` for the selected service + environment.
* **Template** selection — filter by email/SMS type, shows templates for selected service.
* **Dynamic personalisation** — parses template content for `((variable))` placeholders and renders input fields.
* **Recipients** — email address or phone number (comma/semicolon-separated for multiple).
* **Execute** — sends notification, displays JSON response.

### C. Bulk Send Page (`/bulk-send`)
* Upload CSV of recipients with personalisation columns.
* Select service, template, and API key.
* Sends notifications in batch.

### D. Resource Pages
All resource pages follow a consistent datagrid pattern:
* `ui.table` with sortable columns and pagination.
* Client-side search/filter (no API call required).
* "Sync" button fetches from remote API into SQLite cache.
* CSV export button.
* Click-to-copy on ID and name fields.
* Environment filter when viewing multi-environment data.

Service Callbacks (`/service-callbacks`) additionally supports create, edit, and delete against the remote API:
* Bearer tokens are **write-only** — they are sent to the API on create/update and are never cached locally. The `service_callbacks` table has no bearer token column, and the API never returns the stored value, so the edit dialog cannot pre-fill it. A blank token on edit means "keep the existing token".
* `callback_type` and `callback_channel` are **immutable after creation**. The edit dialog does not send either field; changing one requires deleting the callback and recreating it.
* A service may hold at most one callback per type and one per channel, so the create dialog only offers the types and channels not already in use.

### E. Settings Page (`/settings`)
* **Admin Auth:** Username/password per environment (saved encrypted).
* **Local API Keys:** Add/manage keys with encrypted secret storage.
* **Data Management:** Clear cached table data.

## 5. Functional Logic

### 1. Sync Engine (`app/sync.py`)
* `SyncManager` is instantiated per environment with a `NotificationAPI` client and an `EncryptionManager`.
* `sync_all()` runs all entity syncs in sequence: services → templates → api_keys → sms_senders → users → communication_items → provider_details → inbound_numbers → service_callbacks.
* Per-service syncs (templates, api_keys, sms_senders) run in parallel, gated by `asyncio.Semaphore(max_concurrency)` (default 25).
* Single-entity syncs (users, providers, etc.) are single API calls without concurrency gating.
* `sync_users` requires encryption context; it runs `migrate_plaintext_users_to_encrypted` before syncing to convert any legacy plaintext rows, then writes new rows encrypted.
* `SyncResult` tracks success/error counts with structured `SyncError` objects.
* `sync_service_callbacks` prunes stale cached rows after each successful per-service fetch, so callbacks deleted out-of-band do not linger in the cache. Pruning runs on the success path **only** — the 404 branch is deliberately skipped, because a 404 means the service is missing or inaccessible, not that it has zero callbacks.
* `handle_entity_sync()` in `sync_handlers.py` orchestrates multi-environment syncs in parallel via `asyncio.gather()`.
* On startup, `state.py` calls `migrate_plaintext_users_to_encrypted` so existing plaintext rows in local storage are encrypted before any sync or UI read occurs.

### 2. Notification Sender Logic
* **Token generation:** PyJWT with HS256 — `{"iss": service_id, "iat": now}`.
* **Payload:**
  ```json
  {
    "template_id": "uuid",
    "email_address": "user@example.com",
    "personalisation": {
      "dynamic_field_1": "value",
      "dynamic_field_2": "value"
    }
  }
  ```
* SMS notifications use `phone_number` instead of `email_address`, and optionally include `sms_sender_id`.

### 3. Error Handling
* Personalisation fields validated non-empty before sending.
* API key expiry checked before sending.
* HTTP errors handled gracefully: 400 (validation), 403 (bad key), 429 (rate limit).
* `ui.notify()` for transient errors; dialogs for critical setup issues.
* HTTP client uses retry decorator (3 attempts, exponential backoff) for connection errors.

### 4. API Client (`app/api_client.py`)
* `NotificationAPI` abstract base defines the interface (~20 methods).
* `HttpNotificationAPI` implements all methods with `httpx.AsyncClient`, Basic Auth, and retry logic.
* `MockNotificationAPI` returns hardcoded test data with simulated latency (for development without a live API, enabled via `USE_MOCK_API=true`).
* Service callback writes: `create_service_callback(service_id, payload)` (`POST /service/{id}/callback`), `update_service_callback(service_id, callback_id, payload)` (`POST /service/{id}/callback/{callback_id}` — the API uses POST here, not PUT or PATCH), and `delete_service_callback(service_id, callback_id)` (`DELETE`, returns 204 with an empty body, so the response is never parsed as JSON).
* `create_service_callback` is decorated with `@http_retry_connect_only` rather than `@http_retry`. Creates are not idempotent, so only connect-phase failures — which provably never reached the server — are retried; read errors and read timeouts are excluded because the request may have succeeded server-side and a retry would surface as a misleading 409 from the unique constraint. `update_service_callback` and `delete_service_callback` use the standard `@http_retry`.

### 5. Service Callback Helpers and Repository Functions

* `app/ui/callback_helpers.py` — pure helpers for service callback validation (`validate_create`, `validate_update`), payload building (`build_create_payload`, `build_update_payload`), and status-control state (`edit_statuses_control_state`, `create_statuses_default`). It imports no NiceGUI, so every function is directly unit-testable. It also mirrors notification-api's `SERVICE_CALLBACK_TYPES`, `CALLBACK_CHANNEL_TYPES`, and `NOTIFICATION_STATUS_TYPES_COMPLETED` as module constants. It re-exports `extract_error_message` and `format_http_error` from `app/ui/http_errors.py` for back-compat with existing call sites.
* `app/ui/http_errors.py` — API error normalization (`extract_error_message`, `format_http_error`), collapsing notification-api's four distinct error body shapes (jsonschema, marshmallow, conflict, generic) into one readable message. It imports no NiceGUI, so every function is directly unit-testable and it is usable from other pure helper modules. Error normalization redacts any message keyed on or mentioning `bearer_token`, so a submitted credential echoed back by a validation error never reaches `ui.notify` or the log file.
* `repository.delete_service_callback(callback_id: str, environment: str) -> bool` — removes a cached callback. The returned bool reports whether a row actually existed, letting the UI warn that the cache was already out of sync rather than reporting a failure.
* `repository.prune_service_callbacks(service_id: str, environment: str, keep_ids: list[str]) -> int` — removes cached callbacks for a service/environment that the remote API no longer returns, and returns the number of rows removed. An empty `keep_ids` prunes every cached row for that service, which is the correct response to a successful but empty list response.

### 6. Service Permission Editing

* `app/ui/permission_helpers.py` — pure helpers for the Services page permission editor. Imports no NiceGUI, so every decision is directly unit-testable; the dialog closures in `app/ui/pages/services.py` are `# pragma: no cover` wiring that only calls into it.
* Everything here follows from one fact: **`POST /service/{id}` with a `permissions` array REPLACES the service's entire permission set.** It is not additive — any value omitted is removed, and an empty array removes all of them. `api_client.update_service_permissions(service_id, permissions)` is a separate method from `update_service` precisely because `update_service` uses `None`-means-omit, and `permissions=[]` (remove everything) is falsy but not `None`; any `if permissions:` guard would silently no-op the most destructive operation in the tool. It carries `@http_retry_connect_only` for the same reason as `create_service_callback`.
* `build_permission_options()` returns the UNION of the documented permissions and whatever the live read returned. An unrecognized value renders enabled and flagged rather than hidden, so a permission this tool does not know about can never be silently deleted by a submit.
* `is_protected_environment(env_name, non_production_environments)` decides whether an environment counts as production. **Any environment not on the configured non-production allowlist is treated as production**, so tunnels, typos, and newly added environments all fail closed into the maximum-friction path. The allowlist is `AppConfig.non_production_environments`, populated from `NON_PRODUCTION_ENVIRONMENTS` (comma-separated, trimmed, lowercased; default `dev,development,local,test,perf,sandbox,staging,stage`, covering both naming conventions in this repo). A blank or `None` name returns `True`. Matching is exact, not substring — `development-2` is production.
* Production-ness is **declared, never inferred**. An earlier version read the base URL, and that could not work: VA engineers reach GovCloud production through SSH, `kubectl port-forward`, or SSM tunnels, so the production API answers on `localhost` and is indistinguishable by hostname from local development. That heuristic returned `False` for a tunnelled production target, which silently deleted the entire CRITICAL gate — no red banner, no typed challenge, no acknowledgements, no final dialog — and stamped the audit record `"protected": false`. `use_mock` is deliberately not an input either: with an allowlist, mock mode stays low-friction for `development` while an environment named `production` keeps the CRITICAL path rehearsable.
* `state.get_raw_base_url()` no longer feeds risk classification. It survives because `build_api_client()` needs the pre-remap URL: `config._remap_host` rewrites `localhost`/`127.0.0.1` to `CONTAINER_HOST`, and that remap must be applied by the caller opening the connection rather than baked into the stored setting.
* `normalize_permissions(values, *, strict=False)` raises `TypeError` naming the offending element when `strict=True`. READ paths use it — `build_permission_options()` and `permissions_equal()`. Filtering a non-string element out of a live read is fail-open in the worst way: `["email", "sms", 0]` yields `before = ("email", "sms")`, so the `0` appears in no diff entry, earns no acknowledgement, and is destroyed by the replace-everything POST with *less* friction than a visible change. The default stays lenient for the write path, where the values are our own checkbox values.
* `classify_change()` returns a `ConfirmationTier`. Production removals reach `CRITICAL`, which the dialog gates behind `is_challenge_satisfied()` — an exact typed service name plus one acknowledgement per removed permission. Full tier matrix: `docs/superpowers/specs/2026-09-10-service-permissions-update-design.md`.
* `build_audit_payload()` / `write_permission_audit()` record the pre-change permission set to `data/permission_changes/` **before** the update is sent, so the rollback value survives a crash or an ambiguous 500, then rewrite it in place with the verified outcome. The record's `error` field is passed through `http_errors.redact_error_message`. Scope of that guarantee, precisely: the structured fields are credential-free by construction, and the free-text `error` field is redacted for the names in `SENSITIVE_ERROR_FIELDS`. It is not a general secret scrubber — a credential echoed under a name outside that tuple would still reach disk, which is why the tuple is deliberately wider than what notification-api is known to echo today.
* `app/ui/artifacts.py` — `write_json_artifact()` and `rewrite_json_artifact()`. The rewrite is atomic (temp file in the same directory, then `os.replace`), because truncate-then-write would destroy the very record it was updating if the write failed. Imports no NiceGUI, which is why `permission_helpers` can use it. `helpers.write_send_results()` delegates here. Both writers produce **0600 files in a 0700 directory**: `data/send_response/` holds plaintext recipient email addresses and personalisation payloads, which is PII. The directory mode is applied with an explicit `chmod` as well as `makedirs(mode=0o700)`, because `exist_ok=True` will not tighten a directory an earlier version already created at 0755. The file is created with `O_EXCL`, so a same-microsecond timestamp collision raises `FileExistsError` rather than truncating an existing audit record; there is deliberately no retry.

## 6. Running Environment

### Direct
```bash
pip install -r requirements.txt
python main.py
# NiceGUI serves on http://localhost:8080
```

### Docker
```bash
docker compose up --build
# Accessible at http://localhost:8080
```

* **Dockerfile:** Python 3.13-slim, installs dependencies, exposes port 8080.
* **docker-compose.yml:** Loads `.env`, maps `host.docker.internal` for API access from container, persists `data/` volume for SQLite database.
* **`docker-entrypoint.sh` starts as root, chowns `/app/data` to `APP_UID:APP_GID` (default 1000), then `exec setpriv`s down to it.** The app never runs as root, and everything under the bind mount stays host-readable. This exists because neither naive option works: a root container writes root-owned `0700`/`0600` artifacts that the developer cannot open, while a container pinned to the host uid via compose `user:` cannot write at all when the Docker daemon has created a missing bind-mount source as `root:root`. Starting as root and dropping handles every case — missing `data/`, one created by an earlier root container, a host uid that is not 1000, and subdirectories left by either — with no manual `chown`. `setpriv` ships with `python:3.13-slim`, so no extra package is needed. Do not add a `USER` directive or a compose `user:`; either prevents the chown. If the container user *is* overridden, the entrypoint detects it, skips both steps, and lets the startup check report whatever is unwritable.
* **Startup writability check.** `state.check_required_paths_writable()` runs before `create_all()` and fails with the offending paths, their owner uid, the process uid, and the remediation. It is the backstop for the cases the entrypoint cannot fix — a read-only mount, an overridden container user, or a wrong `APP_UID`. Without it the same condition surfaces later as a `PermissionError` from SQLite or NiceGUI internals, nowhere near its cause.
* `write_json_artifact` tightens the artifact directory on a best-effort basis: if `chmod` fails because the directory is owned by another user, it logs a warning and still writes the record. Aborting would lose the rollback artifact, which is worse than a listable directory — the file contents are 0600 either way, so only filenames are exposed.
* **`NICEGUI_STORAGE_PATH=/app/data/.nicegui`** is set in compose for the same reason. NiceGUI persists `app.storage.user` (used here only for the theme preference, `app/ui/shell.py:107-120`) to a directory it creates relative to the working directory — `/app/.nicegui`. `/app` is built as root, so a container running as the host uid gets `PermissionError` there. Redirecting it into the bind mount also makes the preference survive `up --build`, which the default location does not.

### Environment Variables (`.env`)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MASTER_KEY` | Yes | — | Encryption key for Fernet-encrypted secrets |
| `USE_MOCK_API` | No | `true` | Use mock API client (no live API needed) |
| `API_PUBLIC_HOSTS` | No | See below | JSON dict of environment → API URL |
| `DATABASE_PATH` | No | `data/app.db` | SQLite database file path |
| `CONTAINER_HOST` | No | — | Set by docker-compose for host networking |
| `APP_UID` / `APP_GID` | No | `1000` | uid:gid `docker-entrypoint.sh` chowns `data/` to and drops privileges to |
| `NICEGUI_STORAGE_PATH` | No | `.nicegui` | Where NiceGUI persists `app.storage.user`; compose points it into `data/` |

Default `API_PUBLIC_HOSTS`:
```json
{
  "development": "https://dev-notify.va.gov",
  "perf": "https://sandbox-api.va.gov/vanotify",
  "staging": "https://staging-notify.va.gov",
  "production": "https://api.notifications.va.gov"
}
```
