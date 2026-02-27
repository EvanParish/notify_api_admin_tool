# Database Migrations

## Automatic Schema Creation

The database schema is created automatically on first run. No manual migration steps are needed.

If you have an old database with schema errors, delete it and let the app recreate it on startup.

## Migration History

| Date | Change | Status |
|------|--------|--------|
| 2025-01-24 | Added `service_id` to `api_keys` table | ✓ Incorporated into ORM models |
| 2026-02-17 | Added `environment` to `local_api_keys` table | ✓ Incorporated into ORM models |
