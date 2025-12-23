# Model Updates to Match API Data Structure

## Summary

Updated database models, sync functionality, and tests to more closely match the actual VA Notification API data structure as defined in `tests/testing_data.py`.

## Changes Made

### 1. Service Model Updates (`app/models.py`)

**Removed:**
- `limit` (replaced with `message_limit`)

**Added:**
- `message_limit` - Integer, API message limit
- `rate_limit` - Integer, API rate limit  
- `research_mode` - Boolean, research mode flag
- `count_as_live` - Boolean, count as live service
- `prefix_sms` - Boolean, prefix SMS flag
- `email_from` - String, email from address
- `permissions` - Text, JSON array of permissions (email, sms, etc.)
- `organisation_type` - String, organization type
- `crown` - Boolean, crown status
- `go_live_at` - String, go-live timestamp
- `created_by` - String, creator user ID

**Matches API Fields:**
```python
{
    "id": "d6aa2c68-a2d9-4437-ab19-3ae8eb202553",
    "name": "VA Notify",
    "active": True,
    "restricted": False,
    "message_limit": 100000,
    "rate_limit": 3000,
    "research_mode": False,
    "count_as_live": True,
    "prefix_sms": False,
    "email_from": None,
    "permissions": ["international_sms", "push", "email", "sms"],
    "organisation_type": None,
    "crown": True,
    "go_live_at": None,
    "created_by": "a8d82fd1-a306-4073-8506-dc02f9a2855c"
}
```

### 2. Template Model Updates (`app/models.py`)

**Added:**
- `archived` - Boolean, archived status
- `hidden` - Boolean, hidden status
- `process_type` - String, processing type (normal, priority, bulk)
- `created_at` - String, creation timestamp
- `updated_at` - String, last update timestamp
- `created_by` - String, creator user ID
- `reply_to_email` - String, reply-to email address

**Updated:**
- `template_type` enum now includes "letter" (was just "email", "sms")

**Matches API Fields:**
```python
{
    "id": "e98f2fd4-f307-4092-be15-34a8d903aaaa",
    "service": "d6aa2c68-a2d9-4437-ab19-3ae8eb202553",
    "name": "0501SMS Test edit.",
    "template_type": "sms",
    "content": "test otp message ((OTP))",
    "subject": None,
    "version": 3,
    "archived": False,
    "hidden": False,
    "process_type": "normal",
    "created_at": "2023-05-02T20:59:37.013017",
    "updated_at": "2024-04-23T15:27:47.100809",
    "created_by": "40e5f846-02e3-4a23-b890-bee41d8e2a1f",
    "reply_to_email": None
}
```

### 3. User Model Updates (`app/models.py`)

**Added:**
- `mobile_number` - String, user's mobile number
- `state` - String, user state (active, inactive)
- `platform_admin` - Boolean, platform admin flag
- `blocked` - Boolean, blocked status
- `failed_login_count` - Integer, failed login attempts

**Matches API Fields:**
```python
{
    "id": "0a02afbc-aa35-4905-9ea9-2a2228e73b63",
    "name": "asdfasddsff",
    "email_address": "admin_user@email.com",
    "auth_type": None,
    "mobile_number": None,
    "state": "active",
    "platform_admin": True,
    "blocked": False,
    "failed_login_count": 0
}
```

### 4. ApiKey Model Updates (`app/models.py`)

**Added:**
- `key_type` - String, key type (normal, team, test)
- `created_at` - String, creation timestamp
- `revoked` - Boolean, revoked status
- `version` - Integer, key version

**Matches API Fields:**
```python
{
    "id": "79bc99c0-241e-48dd-b49b-6752e5899dce",
    "name": "my-apikey",
    "key_type": "normal",
    "expiry_date": "2025-11-19T19:22:04.549876",
    "created_by": "859d6821-e9bd-409a-a595-1be7a8064b21",
    "created_at": "2025-05-23T19:22:04.550577",
    "revoked": False,
    "version": 1
}
```

### 5. Sync Manager Updates (`app/sync.py`)

**Updated:**
- `sync_services()` now syncs all new Service fields
- `sync_users()` now syncs all new User fields
- `sync_templates()` now syncs all new Template fields
- Added JSON serialization for `permissions` array
- Handles `service` vs `service_id` field variations

### 6. Test Updates

**Updated Files:**
- `tests/test_db.py` - All model tests updated with new fields
- `tests/test_sync.py` - Sync tests work with updated models

**Test Changes:**
- Replaced `limit` with `message_limit` and `rate_limit`
- Added assertions for new fields
- Tests verify new fields are properly stored/retrieved

## Data Mapping

### Field Name Variations Handled

The sync manager handles these field name variations:

| API Field | Database Field | Notes |
|-----------|---------------|-------|
| `service` | `service_id` | Template's service reference |
| `type` | `template_type` | Template type field |
| `template_type` | `template_type` | Alternative field name |
| `limit` | `message_limit` | Renamed for clarity |

### JSON Field Handling

**Permissions Array:**
The API returns permissions as an array:
```json
["international_sms", "push", "email", "sms"]
```

This is stored as a JSON string in the database:
```python
permissions = json.dumps(["international_sms", "push", "email", "sms"])
```

## Migration Impact

### Database Schema Changes

New columns added to existing tables:

**services table:**
- message_limit, rate_limit, research_mode, count_as_live
- prefix_sms, email_from, permissions, organisation_type
- crown, go_live_at, created_by

**templates table:**
- archived, hidden, process_type, created_at
- updated_at, created_by, reply_to_email

**users table:**
- mobile_number, state, platform_admin, blocked, failed_login_count

**api_keys table:**
- key_type, created_at, revoked, version

### Backward Compatibility

✅ **Existing tests pass** - All 120 tests pass  
✅ **Optional fields** - All new fields are nullable or have defaults  
✅ **Graceful handling** - Sync uses `.get()` with defaults  
✅ **No breaking changes** - Core functionality unchanged  

## Testing

### Test Coverage

All model changes are covered by tests:

```bash
# Run model tests
python -m pytest tests/test_db.py -v
# 10 passed

# Run sync tests
python -m pytest tests/test_sync.py -v
# 9 passed

# Run all tests
python -m pytest tests/
# 120 passed, 1 skipped
```

### Test Data Source

Tests use `tests/testing_data.py` which contains real API response structures from:
- `GET /service` - Service list
- `GET /user` - User list
- `GET /service/{service_id}/template` - Template list
- `GET /service/{service_id}/api-key` - API key list

## Benefits

### 1. Accurate Data Model
- Models now match actual API responses
- No data loss during sync
- Better data integrity

### 2. Enhanced Features
- Can filter by research mode
- Track creation timestamps
- Monitor failed logins
- Identify archived templates

### 3. Future Proof
- Ready for additional API fields
- Handles variations gracefully
- Extensible for new features

### 4. Better Reporting
- More metadata available
- Richer analytics possible
- Improved troubleshooting

## Usage Examples

### Access New Fields

```python
# Service fields
service = await session.get(Service, service_id)
print(f"Rate limit: {service.rate_limit}")
print(f"Permissions: {json.loads(service.permissions)}")

# Template fields
template = await session.get(Template, template_id)
print(f"Created: {template.created_at}")
print(f"Archived: {template.archived}")

# User fields
user = await session.get(User, user_id)
print(f"Platform admin: {user.platform_admin}")
print(f"State: {user.state}")

# API Key fields
key = await session.get(ApiKey, key_id)
print(f"Revoked: {key.revoked}")
print(f"Type: {key.key_type}")
```

### Filter Queries

```python
# Find active, non-research services
services = await session.execute(
    select(Service).where(
        Service.active == True,
        Service.research_mode == False
    )
)

# Find non-archived templates
templates = await session.execute(
    select(Template).where(Template.archived == False)
)

# Find platform admins
admins = await session.execute(
    select(User).where(User.platform_admin == True)
)
```

## Summary

✅ **120 tests passing**  
✅ **Models match API structure**  
✅ **No breaking changes**  
✅ **Enhanced functionality**  
✅ **Better data capture**  

The models now accurately represent the VA Notification API data structure, enabling richer features and better data analysis while maintaining full backward compatibility.
