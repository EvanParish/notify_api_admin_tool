# Running Tests

This project has comprehensive test coverage (98%) for all core functionality.

## Quick Start

```bash
# Install dependencies (if not already installed)
pip install -r requirements.txt

# Run all tests
python -m pytest tests/

# Run tests with coverage report
python -m pytest tests/ --cov=app --cov-report=term-missing

# Run tests with HTML coverage report
python -m pytest tests/ --cov=app --cov-report=html
# Then open htmlcov/index.html in your browser
```

## Test Organization

```
tests/
├── conftest.py              # Shared fixtures
├── test_api_client.py       # API client tests (Mock & HTTP)
├── test_config.py           # Configuration loading tests
├── test_crypto.py           # Encryption/decryption tests
├── test_db.py               # Database and model tests
├── test_repository.py       # Repository/CRUD tests
├── test_sync.py             # Sync manager tests
└── test_utils.py            # Utility function tests
```

## Running Specific Tests

```bash
# Run a specific test file
python -m pytest tests/test_utils.py -v

# Run a specific test function
python -m pytest tests/test_utils.py::test_extract_placeholders_empty -v

# Run tests matching a pattern
python -m pytest tests/ -k "encryption" -v

# Run with verbose output
python -m pytest tests/ -vv

# Stop on first failure
python -m pytest tests/ -x

# Show local variables on failure
python -m pytest tests/ -l
```

## Test Coverage

Current coverage: **98%**

- app/utils.py: 100%
- app/crypto.py: 100%
- app/models.py: 100%
- app/repository.py: 100%
- app/sync.py: 100%
- app/db.py: 96%
- app/config.py: 96%
- app/api_client.py: 93%

See `TEST_COVERAGE_SUMMARY.md` for detailed coverage information.

## Continuous Integration

Tests can be easily integrated into CI/CD pipelines:

```yaml
# Example GitHub Actions
- name: Run tests
  run: |
    pip install -r requirements.txt
    pytest tests/ --cov=app --cov-report=xml
    
- name: Upload coverage
  uses: codecov/codecov-action@v3
  with:
    file: ./coverage.xml
```

## Troubleshooting

### Async Warnings
If you see warnings about unclosed database connections, these are expected with SQLite/aiosqlite during test teardown and don't affect test validity.

### Skipped Tests
One test (`test_load_config_missing_master_key`) is skipped when a `.env` file with `master_key` is present. This is intentional.

### Database Issues
If you encounter database locking issues, ensure no other processes are using the test databases. Tests use temporary databases that are automatically cleaned up.

## Test Data

The `test_data.py` file contains sample data used by sync tests. This data mimics the structure returned by the VA Notification API.
