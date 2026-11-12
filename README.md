# VA Notify Admin Dashboard

A web UI for the [VA Notification API](https://github.com/department-of-veterans-affairs/notification-api): manage services, templates, API keys, users, and providers, and send test notifications across dev, perf, staging, and prod.

Built with Python 3.13, [NiceGUI](https://nicegui.io/), SQLite, and SQLAlchemy.

<img width="1238" height="819" alt="_admin_dashboard_vanotify" src="https://github.com/user-attachments/assets/2e741d4e-e0c6-446d-abdd-d58dcbb7216f" />

## Run the Latest Release

Needs only [Docker](https://docs.docker.com/get-docker/). The image is public and built for amd64 and arm64 (Apple Silicon).

1. Download the compose file and env template:

   ```bash
   mkdir notify-admin && cd notify-admin
   curl -fsSLO https://raw.githubusercontent.com/EvanParish/notify_api_admin_tool/main/docker-compose.ghcr.yml
   curl -fsSL -o .env https://raw.githubusercontent.com/EvanParish/notify_api_admin_tool/main/.env.example
   ```

2. In `.env`, set `MASTER_KEY` to a long random string. Don't change it later; it decrypts your stored credentials. Add `USE_MOCK_API=true` to try sample data.

3. Start it, then open http://localhost:8080 and do the [First-Run Configuration](#first-run-configuration):

   ```bash
   docker compose -f docker-compose.ghcr.yml up -d
   ```

- **Upgrade:** `docker compose -f docker-compose.ghcr.yml pull && docker compose -f docker-compose.ghcr.yml up -d`
- **Pin a version:** `APP_VERSION=1.0.0` in `.env` (default `latest`).
- **Data** lives in `./data`. Back it up with `.env`.

## Run from Source

Needs Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env    # set MASTER_KEY; USE_MOCK_API=true for sample data
uv sync
uv run python main.py   # http://localhost:8080
```

Or with Docker: `docker compose up --build`. It bind-mounts `app/` and `main.py`, so code changes apply without a rebuild, and runs as your host user so `data/` stays readable.

## What's New

The header shows the running version. Click it, or **What's New** in the sidebar, for the [changelog](CHANGELOG.md). All versions are on the [Releases page](https://github.com/EvanParish/notify_api_admin_tool/releases).

## First-Run Configuration

Skip this in mock mode.

1. **Settings → Global Admin Auth:** for each environment you use, enter your platform admin user ID (a UUID) and password, then **Save Admin Auth**.
2. **Sync:** click **Refresh All Data** in the header, or **Sync** on a page. **View Envs** picks which environments the tables show.
3. **To send notifications:** create or select a service key on **API Keys** or **Create API Key**.

Credentials, key secrets, and synced user names and emails are encrypted at rest with `MASTER_KEY`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MASTER_KEY` | **required** | Encryption key for stored secrets |
| `USE_MOCK_API` | `true` (`.env.example`: `false`) | `true` uses built-in sample data instead of the real API |
| `API_PUBLIC_HOSTS` | see below | JSON map of environment name to API base URL |
| `NON_PRODUCTION_ENVIRONMENTS` | `dev,development,local,test,perf,sandbox,staging,stage` | Environments **not** treated as production. See [Production safety](#production-safety) |
| `DATABASE_PATH` | `data/app.db` | SQLite file |
| `PORT` | `8080` | Web UI port |
| `REQUEST_TIMEOUT` | `30` | API timeout in seconds (1–300). Raise it if syncs time out |
| `MAX_CONCURRENCY` | `25` | Concurrent API calls during sync (1–100) |
| `APP_VERSION` | `latest` | Release image to run (`docker-compose.ghcr.yml`) |
| `APP_UID` / `APP_GID` | `1000` | Docker: user/group that runs the app and owns `data/`. Set if `id -u` / `id -g` differ |
| `CONTAINER_HOST` | — | Set by compose so `localhost` URLs reach the host |
| `NICEGUI_STORAGE_PATH` | `.nicegui` | Theme preference storage. Compose puts it in `data/` |

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

Risky changes, like editing service permissions, need extra confirmation in production. **Any environment not named in `NON_PRODUCTION_ENVIRONMENTS` counts as production**, including `localhost`, since a tunnel to production also answers there. Names match exactly (case-insensitive), so add `development-2` yourself if it isn't production.

## CSV Exports

**Export CSV** writes the full database record for each filtered row, in the table's sort order, with resolved names (e.g. `Service Name`) next to raw IDs.

- UTF-8 with BOM, RFC 4180 quoting, one line per record.
- In text cells: `\` → `\\`, line break → `\n`, tab → `\t`, other Unicode line breaks → `\uXXXX`.
- Cells starting with `=`, `+`, `-`, or `@` get a leading `'` to block formula injection.
- JSON columns are compact and key-sorted.
- Secrets are never exported. Every model column must be classified in `tests/test_main.py::EXPORT_COLUMN_REVIEW`, so new columns fail tests until reviewed.
- Exports with personal data ask for confirmation. Files are unencrypted; keep them in approved locations and delete them when done.

To restore the original text:

```python
import pandas as pd
from app.ui.helpers import unescape_csv_text

df = pd.read_csv("templates.csv", encoding="utf-8-sig", dtype=str, keep_default_na=False)
df["Content"] = df["Content"].map(unescape_csv_text)
# Formula-escaped cells: strip the leading "'" before =, +, -, or @.
```

## Development

```bash
uv run pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=100   # must stay at 100%
uv run ruff check --fix .
uv run ruff format .
```

Add tests with every change. Tests use their own temporary database. See [`tests/README.md`](tests/README.md) for fixtures and [`docs/architecture.md`](docs/architecture.md) for the design.

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/). Each one becomes a changelog line:

```text
feat(services): show the SMS sender count     # minor bump, "Added"
fix(sync): retry on read timeouts             # patch bump, "Fixed"
fix(security): redact tokens in errors        # patch bump, "Security"
feat(config)!: rename API_HOSTS               # major bump; add a BREAKING CHANGE: footer
```

`refactor`, `test`, `style`, `ci`, and `chore` are left out of the changelog. Full table: [`.github/copilot-instructions.md`](.github/copilot-instructions.md#commit-messages-conventional-commits).

### Releases

From a clean, up-to-date `main`:

1. Run `scripts/release.sh` (or `scripts/release.sh 1.4.0` to choose the version). It bumps the version, adds a `CHANGELOG.md` section, and runs the checks.
2. Review and edit the new changelog section. It becomes the release notes.
3. Run the commit, tag, and push commands it prints.

The tag triggers [`release.yml`](.github/workflows/release.yml): tests, then the image (`X.Y.Z`, `X.Y`, `X`, `latest`), then the GitHub Release. If a step fails, nothing after it publishes; fix it, then delete and re-push the tag (`git push --delete origin vX.Y.Z && git tag -d vX.Y.Z`).
