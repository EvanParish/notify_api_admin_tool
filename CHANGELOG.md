# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries are generated from [Conventional Commits](https://www.conventionalcommits.org/)
by `scripts/release.sh`. Releases before 1.0.0 were not tracked.

## [1.1.0](https://github.com/EvanParish/notify_api_admin_tool/releases/tag/v1.1.0) - 2026-11-13

### Added

- **Mock API indicator:** when `USE_MOCK_API` is on, the header shows an orange **MOCK API** badge (hover it for details), and the API status badge reads **Mock** instead of **Online**, so sample data is never mistaken for a live environment.

### Changed

- **Shorter environment names:** the **View Envs** selector, the **Sync Settings** menu, and its notifications use short names (`dev`, `stag`, `prod`). The **Sync Settings** menu also has more padding and a tidier vertical layout.

### Documentation

- The README leads with running the published image and is shorter.
- `.env.example` is simpler and lists every setting.

## [1.0.0](https://github.com/EvanParish/notify_api_admin_tool/releases/tag/v1.0.0) - 2026-11-10

First versioned release. It covers everything the team has been using so far.

### Added

- **Multi-environment dashboard:** view and sync dev, perf, staging, and production side by side. The **View Envs** selector picks which environments tables show, and **Sync Settings** controls which ones can be synced.
- **Resource pages:** Services, Templates, API Keys, Users, SMS Senders, Inbound Numbers, Service Callbacks, Communication Items, and Provider Details, each with search, filters, sorting, and click-to-copy IDs.
- **Services:** active key, template, and non-archived SMS sender counts; editable message and rate limits; and a service permissions editor with tiered confirmation that asks for more in production.
- **API keys:** create keys across environments, update and revoke them, filter by creator, optionally show revoked keys, see last-used dates, and see the creator's email. Local keys link to their remote key.
- **Create API Key page:** generates the key hand-off email, with rotation templates.
- **SMS senders and inbound numbers:** create and edit them, with service and provider picked from dropdowns. The first SMS sender on a service becomes the default.
- **Service callbacks:** create, edit, and delete them.
- **Communication items:** edit them. The Templates table shows each template's communication item.
- **Send Notification:** email and SMS with template preview, personalisation checks, and multiple recipients. Results of multi-recipient sends are saved to `data/send_response/`.
- **Bulk Send:** progress tracking and a confirmation warning.
- **CSV export** on every table: the full database record for each filtered row, in the table's sort order, Excel-safe and guarded against formula injection.
- **Sync:** a progress bar that names the phase in flight, retries with backoff, and a credential check before syncing.
- **Settings page:** API base URLs, encrypted admin credentials, and clearing the local cache.
- Dark mode, the app logo, and a configurable port (`PORT`).
- Docker and Docker Compose support. The container runs as your host user so `data/` stays readable.

### Security

- Admin credentials, API key secrets, and synced user names and emails are encrypted at rest with a key derived from `MASTER_KEY`.
- Inbound number auth parameters are never sent to the browser, and secrets are never included in CSV exports.
- Error messages that mention credential fields are redacted before being shown or logged.
- Any environment not listed in `NON_PRODUCTION_ENVIRONMENTS` is treated as production, including `localhost` tunnels.
