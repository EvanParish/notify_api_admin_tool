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
   * Scope: API key secrets and Basic Auth credentials are encrypted at rest in SQLite.
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
├── repository.py            Async CRUD, upsert functions, DbSaltProvider
├── sync.py                  SyncManager — pulls remote API data into local cache
└── ui/
    ├── state.py             AppState, module globals (config, encryption), startup/shutdown
    ├── shell.py             build_shell() — sidebar, header, theme toggle
    ├── helpers.py           Reusable UI utilities (metric cards, CSV export, copy-to-clipboard)
    ├── sync_handlers.py     handle_entity_sync(), handle_full_sync()
    ├── email_helpers.py     API key email generation (new service, rotation, forced rotation)
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
   * `created_at`, `revoked`, `version`

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

* **provider_details** — `(id, environment)` PK
   * `active`, `created_by_name`, `current_month_billable_sms`, `display_name`
   * `identifier`, `load_balancing_weight`, `notification_type`, `priority`
   * `supports_international`, `updated_at`

* **communication_items** — `(id, environment)` PK
   * `name`, `va_profile_item_id`, `default_send_indicator`

* **inbound_numbers** — `(id, environment)` PK
   * `number`, `provider`, `active`, `self_managed`
   * `service_id`, `service_name`, `auth_parameter`, `url_endpoint`

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

### E. Settings Page (`/settings`)
* **Admin Auth:** Username/password per environment (saved encrypted).
* **Local API Keys:** Add/manage keys with encrypted secret storage.
* **Data Management:** Clear cached table data.

## 5. Functional Logic

### 1. Sync Engine (`app/sync.py`)
* `SyncManager` is instantiated per environment with a `NotificationAPI` client.
* `sync_all()` runs all entity syncs in sequence: services → templates → api_keys → sms_senders → users → communication_items → provider_details → inbound_numbers.
* Per-service syncs (templates, api_keys, sms_senders) run in parallel, gated by `asyncio.Semaphore(max_concurrency)` (default 25).
* Single-entity syncs (users, providers, etc.) are single API calls without concurrency gating.
* `SyncResult` tracks success/error counts with structured `SyncError` objects.
* `handle_entity_sync()` in `sync_handlers.py` orchestrates multi-environment syncs in parallel via `asyncio.gather()`.

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

### Environment Variables (`.env`)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MASTER_KEY` | Yes | — | Encryption key for Fernet-encrypted secrets |
| `USE_MOCK_API` | No | `true` | Use mock API client (no live API needed) |
| `API_PUBLIC_HOSTS` | No | See below | JSON dict of environment → API URL |
| `DATABASE_PATH` | No | `data/app.db` | SQLite database file path |
| `CONTAINER_HOST` | No | — | Set by docker-compose for host networking |

Default `API_PUBLIC_HOSTS`:
```json
{
  "development": "https://dev-notify.va.gov",
  "perf": "https://sandbox-api.va.gov/vanotify",
  "staging": "https://staging-notify.va.gov",
  "production": "https://api.notifications.va.gov"
}
```
