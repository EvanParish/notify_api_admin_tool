# VA Notify Admin Dashboard

An admin dashboard for the [VA Notification API](https://github.com/department-of-veterans-affairs/notification-api). Provides a web UI for managing services, templates, API keys, users, providers, and sending test notifications across multiple VA environments (dev, perf, staging, prod).

Built with Python 3.13, [NiceGUI](https://nicegui.io/), SQLite, and SQLAlchemy.

<img width="1238" height="819" alt="_admin_dashboard_vanotify" src="https://github.com/user-attachments/assets/2e741d4e-e0c6-446d-abdd-d58dcbb7216f" />

## Prerequisites

- **Python 3.13+**
- **Docker** (optional, for containerised setup)

## Quick Start

### 1. Configure environment

Copy the example env file and set your master key:

```bash
cp .env.example .env
```

Edit `.env` and set a secure random string for `MASTER_KEY`:

```env
MASTER_KEY=your-secure-random-string-here
```

> `MASTER_KEY` is **required** — it derives the encryption key used to protect stored credentials.

### 2. Run the app

**Direct:**

```bash
pip install -r requirements.txt
python main.py
# Open http://localhost:8080
```

**Docker:**

```bash
docker compose up --build
# Open http://localhost:8080
```

The SQLite database persists in `data/app.db` (mounted as a volume in Docker).

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MASTER_KEY` | **Yes** | — | Encryption key for stored secrets (use a long random string) |
| `USE_MOCK_API` | No | `true` | Set to `false` to connect to the real VA Notify API |
| `API_PUBLIC_HOSTS` | No | See below | JSON dict mapping environment names to API base URLs |
| `DATABASE_PATH` | No | `data/app.db` | Path to the SQLite database file |
| `CONTAINER_HOST` | No | — | Automatically set by `docker-compose.yml` for Docker networking |

Default `API_PUBLIC_HOSTS`:

```json
{
  "development": "https://dev-notify.va.gov",
  "perf": "https://sandbox-api.va.gov/vanotify",
  "staging": "https://staging-notify.va.gov",
  "production": "https://api.notifications.va.gov"
}
```

## First-Run Configuration

When `USE_MOCK_API=false`, the app requires credentials to communicate with the VA Notification API. After starting the app for the first time:

### 1. Set Admin Auth Credentials

Navigate to **Settings** (`/settings`) → **Global Admin Auth**.

For each environment you want to use, enter:

- **Username** — your platform admin **user ID (UUID)** from the Notification API (e.g. `a8d82fd1-a306-4073-8506-dc02f9a2855c`)
- **Password** — that user's login password

Click **Save Admin Auth**. Credentials are Fernet-encrypted and stored in the local database.

> Each environment (dev, staging, prod, etc.) has its own set of credentials. You only need to configure the environments you plan to use.

These credentials are used for HTTP Basic Auth on all admin API calls (listing services, syncing templates, managing API keys, etc.).

### 2. Sync Data

Once credentials are configured, use the **Sync** buttons on resource pages (Services, Templates, etc.) or the **Full Sync** button on the Dashboard to pull data from the API into the local cache.

### 3. Add API Keys (for Sending Notifications)

To send test notifications, you need a service API key:

1. Go to **API Keys** (`/api-keys`) or **Create API Key** (`/api-key-service`)
2. Create or select an API key for your target service
3. The key secret is encrypted and stored locally in the `local_api_keys` table

The key secret is used to sign JWT tokens (HS256) for `POST /v2/notifications/email` and `POST /v2/notifications/sms` requests.

### Mock Mode

When `USE_MOCK_API=true` (default), the app uses a mock API client with sample data. No credentials are needed — useful for exploring the UI or local development.

## Development

### Testing

```bash
# Run all tests
python -m pytest tests/

# Run with coverage
python -m pytest tests/ --cov=app --cov-report=term-missing

# Run a specific test file
python -m pytest tests/test_utils.py -v
```

Tests use `asyncio_mode = auto` — async test functions are collected automatically. Each test gets an isolated temporary SQLite database via the `isolate_database` fixture.

### Linting

```bash
ruff check .
ruff format .
```

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for detailed documentation of the module layout, database schema, sync engine, authentication model, and UI structure.
