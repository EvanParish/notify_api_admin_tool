# Database Migrations

This document tracks database schema changes and migrations.

## Migration History

### 2025-01-24: Add service_id to api_keys table

**Purpose**: Link API keys to their parent services for better organization and filtering.

**Changes**:
- Added `service_id` column to `api_keys` table
- Column is nullable (TEXT) with foreign key reference to `services(id)`

**Migration Script**: `migrate_add_service_id_to_api_keys.py`

**To Run**:
```bash
python migrate_add_service_id_to_api_keys.py
```

**Status**: ✓ Completed

---

### 2026-02-17: Add environment to local_api_keys table

**Purpose**: Make local API keys environment-specific to avoid collisions for shared service IDs.

**Changes**:
- Added `environment` column to `local_api_keys` table
- Column is nullable (TEXT) to avoid breaking existing records

**Migration Script**: `migrate_add_environment_to_local_api_keys.py`

**To Run**:
```bash
python migrate_add_environment_to_local_api_keys.py
```

**Status**: ✓ Completed

---

## How to Apply Migrations

If you encounter database schema errors when starting the application:

1. Stop the application
2. Run the appropriate migration script(s)
3. Restart the application

### Automatic Schema Creation

For fresh installations (no existing database), the schema is created automatically on first run with all columns included.

### Existing Databases

For existing databases, migration scripts must be run manually to add new columns or make schema changes.

## Troubleshooting

### Error: "no such column: api_keys.service_id"

**Solution**: Run the migration script:
```bash
python migrate_add_service_id_to_api_keys.py
```

### Error: "no such column: local_api_keys.environment"

**Solution**: Run the migration script:
```bash
python migrate_add_environment_to_local_api_keys.py
```

### Migration Already Applied

If the migration has already been applied, the script will detect it and skip:
```
✓ Column 'service_id' already exists in api_keys table
No migration needed
```

## Future Migrations

When adding new columns or making schema changes:

1. Create a new migration script with descriptive name
2. Check if the change already exists before applying
3. Document the migration in this file
4. Test with both fresh and existing databases
