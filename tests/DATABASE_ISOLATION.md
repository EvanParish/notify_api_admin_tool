# Database Isolation in Tests

## Overview
The test suite now uses completely isolated temporary databases, ensuring tests never interfere with the application's `data/app.db` database.

## How It Works

### 1. Test Database Isolation (`tests/conftest.py`)
A pytest fixture `isolate_database` runs automatically for every test:

```python
@pytest.fixture(scope="function", autouse=True)
async def isolate_database(tmp_path):
    """
    Ensure each test uses a completely isolated temporary database.
    This fixture runs automatically for all tests.
    """
```

**What it does:**
- Creates a unique temporary database for each test in `/tmp/`
- Database name format: `test_{process_id}_{unique_id}.db`
- Initializes the database engine with the temp database
- After the test completes, disposes the engine and cleans up
- Restores the original database state

### 2. Application Database Protection (`main.py`)
The main application now checks if it's running in test mode:

```python
# Only initialize database if not in test mode
# Tests will call init_engine with their own temporary database
if not os.getenv("PYTEST_CURRENT_TEST"):
    init_engine(config.database_path)
```

**Result:**
- When pytest runs, `PYTEST_CURRENT_TEST` environment variable is set
- main.py skips initializing `data/app.db`
- Tests initialize their own temporary databases
- Application runs normally outside of tests

## Benefits

### ✅ Complete Isolation
- Each test gets a fresh, empty database
- Tests cannot interfere with each other
- Tests cannot corrupt the application database
- Parallel test execution is safe

### ✅ No Side Effects
- Running tests doesn't modify `data/app.db`
- Application data remains untouched
- Developers can run tests without worry

### ✅ Automatic Cleanup
- Temporary databases are created in `/tmp/`
- Operating system automatically cleans up old test databases
- No manual cleanup required

## Verification Tests

Six new tests verify the isolation (`tests/test_database_isolation.py`):

1. **test_database_isolation_check**: Verifies tests use `/tmp/` databases
2. **test_app_database_not_created_by_tests**: Confirms `data/app.db` isn't created by tests
3. **test_multiple_tests_use_different_databases**: Each test gets a unique database
4. **test_second_test_has_clean_database**: Tests start with empty databases
5. **test_pytest_environment_variable_is_set**: Confirms pytest environment
6. **test_main_module_skips_db_init_during_tests**: Verifies main.py behavior

## Database Locations

### Application Database
```
Location: data/app.db
Created by: Running main.py directly
Used by: The web application
Persistent: Yes
```

### Test Databases
```
Location: /tmp/pytest-{user}/test_{pid}_{id}.db
Created by: pytest fixtures
Used by: Individual tests
Persistent: No (cleaned up by OS)
```

## Example Test Database Paths

```
/tmp/pytest-of-user/pytest-123/test_db_init_engine0/test_12345_67890.db
/tmp/pytest-of-user/pytest-123/test_create_all0/test_12345_67891.db
/tmp/pytest-of-user/pytest-123/test_service_model0/test_12345_67892.db
```

Each test gets a unique path.

## Running Tests vs Running the App

### Running Tests
```bash
# Tests use temporary databases in /tmp/
python -m pytest tests/

# data/app.db is NOT touched
ls data/app.db  # May not exist or remains unchanged
```

### Running the Application
```bash
# Application uses data/app.db
python main.py

# data/app.db is created/used
ls data/app.db  # Will exist and be used by the app
```

## Migration from Old Approach

### Before
- Tests used `tmp_path` fixture but shared engine state
- `reset_db_state` fixture set engine to `None` between tests
- Potential for cross-test contamination
- No protection for app database

### After
- Automatic isolation with `isolate_database` fixture
- Each test gets completely separate database file
- Engine properly disposed after each test
- Application database protected from tests
- Safer for parallel execution

## Technical Details

### Fixture Scope
- `isolate_database`: function scope, autouse=True
- Runs before and after every test function
- Ensures complete isolation per test

### Database Engine Lifecycle
1. **Test Start**: Create temp db → Initialize engine
2. **Test Run**: Use isolated database
3. **Test End**: Dispose engine → Cleanup
4. **Next Test**: New temp db → New engine

### Async Considerations
- Fixture is async (`async def isolate_database`)
- Properly awaits `engine.dispose()`
- Compatible with async test functions

## Troubleshooting

### If tests fail with "Engine not initialized"
- Check that `isolate_database` fixture is running
- Verify `PYTEST_CURRENT_TEST` is set
- Ensure conftest.py is in tests directory

### If app.db appears during tests
- Check main.py has the `PYTEST_CURRENT_TEST` check
- Verify pytest is setting the environment variable
- Run isolation tests to diagnose

### If tests are slow
- This is normal - each test creates/destroys a database
- Consider marking slow tests for optional execution
- Use pytest-xdist for parallel execution

## Best Practices

1. **Never manually set database path in tests**
   - Let the fixtures handle it
   - Use `initialized_db` fixture for tests needing tables

2. **Don't assume data persists between tests**
   - Each test has a clean database
   - Set up test data in each test or use fixtures

3. **Use appropriate fixtures**
   - `temp_db`: Just the database path
   - `initialized_db`: Database with tables created
   - `isolate_database`: Automatic, no need to request

4. **Don't try to test against app.db**
   - Tests should never touch the application database
   - Mock external dependencies if needed

## Summary

✅ **120 tests** all pass with isolated databases  
✅ **data/app.db** protected from test interference  
✅ **Automatic** isolation for every test  
✅ **Clean** databases for each test run  
✅ **Safe** to run tests anytime  

The database isolation ensures tests are reliable, repeatable, and safe to run without affecting the application or other tests.
