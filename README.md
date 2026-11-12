# VA Notify Admin Dashboard

An admin dashboard for the [VA Notification API](https://github.com/department-of-veterans-affairs/notification-api). Provides a web UI for managing services, templates, API keys, users, providers, and sending test notifications across multiple VA environments (dev, perf, staging, prod).

Built with Python 3.13, [NiceGUI](https://nicegui.io/), SQLite, and SQLAlchemy.

<img width="1238" height="819" alt="_admin_dashboard_vanotify" src="https://github.com/user-attachments/assets/2e741d4e-e0c6-446d-abdd-d58dcbb7216f" />

## Prerequisites

- **Python 3.13** and [**uv**](https://docs.astral.sh/uv/)
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

`.env.example` sets `USE_MOCK_API=false`, which connects to the real API and needs the credentials described in [First-Run Configuration](#first-run-configuration). To explore the UI first with sample data and no credentials, set:

```env
USE_MOCK_API=true
```

### 2. Run the app

**Direct:**

```bash
uv sync  # exact versions from uv.lock (add --no-dev to skip test tools)
uv run python main.py
# Open http://localhost:8080
```

**Docker (published image, no checkout needed):**

Each release publishes an image to GHCR for `linux/amd64` and `linux/arm64` (Apple Silicon). Download the compose file and env template, set `MASTER_KEY` in `.env`, and start it:

```bash
curl -fsSLO https://raw.githubusercontent.com/EvanParish/notify_api_admin_tool/main/docker-compose.ghcr.yml
curl -fsSL -o .env https://raw.githubusercontent.com/EvanParish/notify_api_admin_tool/main/.env.example
docker compose -f docker-compose.ghcr.yml up -d
# Open http://localhost:8080
```

This runs the newest release. To pin one, set `APP_VERSION=1.2.3` in `.env`. To upgrade, run `docker compose -f docker-compose.ghcr.yml pull` and then `up -d` again. Your data stays in `./data`.

**Docker (build from source):**

```bash
docker compose up --build
# Open http://localhost:8080
```

The SQLite database persists in `data/app.db` (mounted as a volume in Docker). Docker runs the app as your host user (see `APP_UID`/`APP_GID` below), so files under `data/` stay readable from the host.

## Versions and What's New

The running version appears next to the title in the header. Click it, or **What's New** in the sidebar, to see the release notes from [`CHANGELOG.md`](CHANGELOG.md). Every release is also listed on the [GitHub Releases page](https://github.com/EvanParish/notify_api_admin_tool/releases).

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MASTER_KEY` | **Yes** | — | Encryption key for stored secrets (use a long random string) |
| `USE_MOCK_API` | No | `true` (`.env.example` sets `false`) | `true` uses built-in sample data; `false` connects to the real VA Notify API |
| `API_PUBLIC_HOSTS` | No | See below | JSON object mapping environment names to API base URLs |
| `NON_PRODUCTION_ENVIRONMENTS` | No | `dev,development,local,test,perf,sandbox,staging,stage` | Comma-separated environment names that are **not** production. See [Production safety](#production-safety) |
| `DATABASE_PATH` | No | `data/app.db` | Path to the SQLite database file |
| `PORT` | No | `8080` | Port the web UI listens on |
| `REQUEST_TIMEOUT` | No | `30` | Seconds before an API request times out (clamped to 1–300). Raise it if syncs hit read timeouts |
| `MAX_CONCURRENCY` | No | `25` | Maximum concurrent API calls during sync (clamped to 1–100) |
| `CONTAINER_HOST` | No | — | Set by `docker-compose.yml`; remaps `localhost` URLs so the container can reach the host |
| `APP_UID` / `APP_GID` | No | `1000` | Docker only: the user/group the app runs as and that owns `data/`. Set them if `id -u` / `id -g` differ |
| `NICEGUI_STORAGE_PATH` | No | `.nicegui` | Where the theme preference is stored. `docker-compose.yml` points it into `data/` |

Default `API_PUBLIC_HOSTS`:

```json
{
  "development": "https://dev-api.va.gov/vanotify",
  "perf": "https://sandbox-api.va.gov/vanotify",
  "staging": "https://staging-api.va.gov/vanotify",
  "production": "https://api.va.gov/vanotify"
}
```

### Production safety

Destructive changes, such as editing a service's permissions, ask for more confirmation in production. **Any environment whose name is not in `NON_PRODUCTION_ENVIRONMENTS` is treated as production.** This includes `localhost` and other tunnelled URLs, because an SSH or `kubectl port-forward` tunnel to production also answers on `localhost`, so the app never guesses from the URL. Names are matched exactly, ignoring case and surrounding spaces, so `development-2` counts as production until you add it to the list.

## First-Run Configuration

When `USE_MOCK_API=false`, the app requires credentials to communicate with the VA Notification API. After starting the app for the first time:

### 1. Set Admin Auth Credentials

Navigate to **Settings** (`/settings`) → **Global Admin Auth**.

For each environment you want to use, enter:

- **Username** — your platform admin **user ID (UUID)** from the Notification API (e.g. `a8d82fd1-a306-4073-8506-dc02f9a2855c`)
- **Password** — that user's login password

Click **Save Admin Auth**. Credentials are Fernet-encrypted and stored in the local database.

Synced user identity fields (`users.name`, `users.email_address`) are also encrypted at rest in SQLite and decrypted at runtime for UI display, search, and CSV export.

> Each environment (dev, staging, prod, etc.) has its own set of credentials. You only need to configure the environments you plan to use.

These credentials are used for HTTP Basic Auth on all admin API calls (listing services, syncing templates, managing API keys, etc.).

### 2. Sync Data

Once credentials are configured, use the **Sync** buttons on resource pages (Services, Templates, etc.) or **Refresh All Data** in the header to pull data from the API into the local cache. The **View Envs** selector in the header picks which environments the tables show.

### 3. Add API Keys (for Sending Notifications)

To send test notifications, you need a service API key:

1. Go to **API Keys** (`/api-keys`) or **Create API Key** (`/api-key-service`)
2. Create or select an API key for your target service
3. The key secret is encrypted and stored locally in the `local_api_keys` table

The key secret is used to sign JWT tokens (HS256) for `POST /v2/notifications/email` and `POST /v2/notifications/sms` requests.

### Mock Mode

When `USE_MOCK_API=true` (default), the app uses a mock API client with sample data. No credentials are needed — useful for exploring the UI or local development.

## CSV Exports

Each table's **Export CSV** button writes the full database record for every row matching the current filters, in the table's current sort order. Columns are named after the database columns, with resolved names (e.g. `Service Name`, `Created By Email`) alongside raw IDs.

Format:

* UTF-8 with a BOM (Excel-safe), RFC 4180 quoting, exactly one physical line per record.
* Inside text cells: `\` → `\\`, line break → `\n`, tab → `\t`, other Unicode line breaks (LS, PS, NEL, VT, FF, FS/GS/RS) → `\uXXXX`. CRLF/CR are normalized to LF first.
* Cells starting with `=`, `+`, `-` or `@` are prefixed with `'` to block spreadsheet formula injection.
* JSON columns are compact, key-sorted JSON.
* Secrets are never exported (`key_secret`, `current_session_id`, `auth_parameter`, and any column whose name matches the secret pattern in `app/ui/helpers.py`). `tests/test_main.py::EXPORT_COLUMN_REVIEW` must classify every model column, so a new column fails CI until reviewed.
* Exports with personal data (users, API keys, templates, provider details) ask for confirmation first. The files are unencrypted. Store them only in approved locations and delete them when done.

To restore the original text when re-importing:

```python
import pandas as pd
from app.ui.helpers import unescape_csv_text

df = pd.read_csv("templates.csv", encoding="utf-8-sig", dtype=str, keep_default_na=False)
df["Content"] = df["Content"].map(unescape_csv_text)
# Cells that were formula-escaped: strip the leading "'" if the value starts with '=, '+, '- or '@.
```

## Development

Ruff and pytest are configured in `pyproject.toml`.

### Testing

```bash
# Run all tests (about 15 seconds)
uv run pytest tests/

# Run with coverage; the suite must stay at 100%
uv run pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=100

# Run a specific test file
uv run pytest tests/test_utils.py -v
```

Add tests with every change. Tests use `asyncio_mode = auto`, so async test functions are collected automatically. Each test gets its own temporary SQLite database and never touches `data/app.db`. See [`tests/README.md`](tests/README.md) for fixtures and conventions.

### Linting

```bash
uv run ruff check .    # includes import sorting (I) and modern-syntax (UP) rules
uv run ruff format .
```

`uv run ruff check --fix .` fixes most findings automatically.

### Commit messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/). The changelog is generated from them, so write each one as a changelog line for the team:

```text
feat(services): show the SMS sender count
fix(sync): retry on read timeouts
fix(security): redact bearer tokens from error messages
feat(config)!: rename API_HOSTS to API_PUBLIC_HOSTS
```

`feat` bumps the minor version and `fix` the patch version. `!` marks a breaking change, which bumps the major version; explain the required action in a `BREAKING CHANGE:` footer. `refactor`, `test`, `style`, `ci`, and `chore` are left out of the changelog. The full table is in [`.github/copilot-instructions.md`](.github/copilot-instructions.md#commit-messages-conventional-commits).

### Releases

Versions follow [Semantic Versioning](https://semver.org/). The version lives only in `pyproject.toml`. To cut a release from an up-to-date, clean `main`:

```bash
scripts/release.sh          # or: scripts/release.sh 1.4.0 to pick the version yourself
```

The script works out the next version from the commits since the last tag, updates `pyproject.toml` and `uv.lock`, adds a section to `CHANGELOG.md`, runs lint and tests, and prints the commit, tag, and push commands. It warns about commits that don't follow the convention; add anything important from those to `CHANGELOG.md` by hand. Review and edit the new section (it becomes the release notes), then run the printed commands.

Pushing the `vX.Y.Z` tag runs [`.github/workflows/release.yml`](.github/workflows/release.yml), which:

1. runs lint and the full test suite at 100% coverage, and checks that the tag, `pyproject.toml`, and `CHANGELOG.md` agree,
2. builds and pushes `ghcr.io/evanparish/notify_api_admin_tool` with tags `X.Y.Z`, `X.Y`, `X`, and `latest`,
3. creates the GitHub Release, using that version's changelog section as the notes.

If any step fails, nothing after it is published. Fix the problem, delete the tag (`git push --delete origin vX.Y.Z && git tag -d vX.Y.Z`), and tag again.

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for detailed documentation of the module layout, database schema, sync engine, authentication model, and UI structure.
