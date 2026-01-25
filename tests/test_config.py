import json
import os
import pytest
from unittest.mock import patch
from app.config import AppConfig, load_config, _parse_bool


def test_parse_bool_true_values():
    assert _parse_bool("1", False) is True
    assert _parse_bool("true", False) is True
    assert _parse_bool("TRUE", False) is True
    assert _parse_bool("yes", False) is True
    assert _parse_bool("YES", False) is True
    assert _parse_bool("y", False) is True
    assert _parse_bool("Y", False) is True


def test_parse_bool_false_values():
    assert _parse_bool("0", True) is False
    assert _parse_bool("false", True) is False
    assert _parse_bool("no", True) is False
    assert _parse_bool("", True) is False


def test_parse_bool_none():
    assert _parse_bool(None, True) is True
    assert _parse_bool(None, False) is False


def test_app_config_default_values():
    config = AppConfig(master_key="test-key-123")
    assert config.use_mock_api is True
    assert config.database_path == "data/app.db"
    assert config.max_concurrency == 25
    assert config.api_hosts == {}


def test_app_config_api_hosts_from_dict():
    config = AppConfig(
        master_key="test-key",
        api_hosts={"dev": "http://dev.example.com", "prod": "http://prod.example.com"}
    )
    assert config.api_hosts == {"dev": "http://dev.example.com", "prod": "http://prod.example.com"}


def test_app_config_api_hosts_from_json_string():
    json_str = json.dumps({"staging": "http://staging.test.com"})
    config = AppConfig(master_key="test-key", api_hosts=json_str)
    assert config.api_hosts == {"staging": "http://staging.test.com"}


def test_app_config_api_hosts_from_comma_separated():
    csv_str = "dev=http://dev.com, prod=http://prod.com"
    config = AppConfig(master_key="test-key", api_hosts=csv_str)
    assert config.api_hosts == {"dev": "http://dev.com", "prod": "http://prod.com"}


def test_app_config_api_hosts_empty():
    config = AppConfig(master_key="test-key", api_hosts="")
    assert config.api_hosts == {}


def test_app_config_max_concurrency_clamping():
    config_low = AppConfig(master_key="test-key", max_concurrency=0)
    assert config_low.max_concurrency == 1

    config_high = AppConfig(master_key="test-key", max_concurrency=200)
    assert config_high.max_concurrency == 100

    config_valid = AppConfig(master_key="test-key", max_concurrency=50)
    assert config_valid.max_concurrency == 50


def test_app_config_max_concurrency_invalid():
    config = AppConfig(master_key="test-key", max_concurrency="invalid")
    assert config.max_concurrency == 25


def test_load_config_missing_master_key():
    # Skip test if .env file exists that provides MASTER_KEY
    env_file = os.path.join(os.getcwd(), '.env')
    if os.path.exists(env_file):
        pytest.skip(".env file exists, cannot test missing MASTER_KEY")
    
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="MASTER_KEY is required"):
            load_config()


def test_load_config_success():
    env = {
        "MASTER_KEY": "test-master-key-123",
        "USE_MOCK_API": "true",
        "DATABASE_PATH": "test/db.db",
        "MAX_CONCURRENCY": "10"
    }
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.master_key == "test-master-key-123"
        assert config.use_mock_api is True
        assert config.database_path == "test/db.db"
        assert config.max_concurrency == 10


def test_load_config_with_custom_api_hosts():
    env = {
        "MASTER_KEY": "test-key",
        "API_PUBLIC_HOSTS": json.dumps({"custom": "http://custom.com"})
    }
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.api_hosts == {"custom": "http://custom.com"}


def test_load_config_has_api_hosts():
    env = {"MASTER_KEY": "test-key"}
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
        # Default config has development, staging, production OR the .env may have different names
        assert len(config.api_hosts) > 0
