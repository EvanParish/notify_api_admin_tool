import json
import os
import pytest
from unittest.mock import patch
from app.config import (
    DEFAULT_NON_PRODUCTION_ENVIRONMENTS,
    AppConfig,
    _parse_bool,
    _remap_host,
    load_config,
)


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
    assert config.request_timeout == 30.0
    assert config.port == 8080
    assert config.api_hosts == {}


def test_app_config_api_hosts_from_dict():
    config = AppConfig(
        master_key="test-key",
        api_hosts={"dev": "http://dev.example.com", "prod": "http://prod.example.com"},
    )
    assert config.api_hosts == {
        "dev": "http://dev.example.com",
        "prod": "http://prod.example.com",
    }


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


def test_app_config_request_timeout_clamping():
    assert AppConfig(master_key="test-key", request_timeout=0).request_timeout == 1.0
    assert AppConfig(master_key="test-key", request_timeout=500).request_timeout == 300.0
    assert AppConfig(master_key="test-key", request_timeout=45).request_timeout == 45.0


def test_app_config_request_timeout_invalid():
    assert AppConfig(master_key="test-key", request_timeout="nope").request_timeout == 30.0


def test_load_config_request_timeout_from_env():
    env = {"MASTER_KEY": "test-key", "REQUEST_TIMEOUT": "60"}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.request_timeout == 60.0


def test_load_config_missing_master_key():
    # Skip test if .env file exists that provides MASTER_KEY
    env_file = os.path.join(os.getcwd(), ".env")
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
        "MAX_CONCURRENCY": "10",
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
        "API_PUBLIC_HOSTS": json.dumps({"custom": "http://custom.com"}),
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


def test_app_config_api_hosts_non_string_non_dict():
    config = AppConfig(master_key="test-key", api_hosts=12345)
    assert config.api_hosts == {}


def test_load_config_missing_master_key_patched():
    with patch("app.config.load_dotenv"):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="MASTER_KEY is required"):
                load_config()


def test_load_config_container_host_remaps_localhost():
    env = {
        "MASTER_KEY": "test-key",
        "API_PUBLIC_HOSTS": json.dumps(
            {
                "local": "http://localhost:6011",
                "dev": "https://dev-api.va.gov/vanotify",
            }
        ),
        "CONTAINER_HOST": "host.docker.internal",
    }
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.api_hosts["local"] == "http://host.docker.internal:6011"
        assert config.api_hosts["dev"] == "https://dev-api.va.gov/vanotify"


def test_load_config_container_host_remaps_127():
    env = {
        "MASTER_KEY": "test-key",
        "API_PUBLIC_HOSTS": json.dumps({"local": "http://127.0.0.1:6011"}),
        "CONTAINER_HOST": "host.docker.internal",
    }
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.api_hosts["local"] == "http://host.docker.internal:6011"


def test_load_config_no_container_host_keeps_localhost():
    env = {
        "MASTER_KEY": "test-key",
        "API_PUBLIC_HOSTS": json.dumps({"local": "http://localhost:6011"}),
    }
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.api_hosts["local"] == "http://localhost:6011"


def test_load_config_stores_container_host():
    env = {
        "MASTER_KEY": "test-key",
        "CONTAINER_HOST": "host.docker.internal",
    }
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.container_host == "host.docker.internal"


def test_load_config_container_host_none_by_default():
    env = {"MASTER_KEY": "test-key"}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.container_host is None


def test_remap_host_localhost():
    assert _remap_host("http://localhost:6011", "host.docker.internal") == "http://host.docker.internal:6011"


def test_remap_host_127():
    assert _remap_host("http://127.0.0.1:6011", "host.docker.internal") == "http://host.docker.internal:6011"


def test_remap_host_no_match():
    assert _remap_host("https://api.va.gov/vanotify", "host.docker.internal") == "https://api.va.gov/vanotify"


def test_app_config_port_custom():
    config = AppConfig(master_key="test-key", port=3000)
    assert config.port == 3000


def test_app_config_port_from_string():
    config = AppConfig(master_key="test-key", port="9090")
    assert config.port == 9090


def test_app_config_port_clamped_low():
    config = AppConfig(master_key="test-key", port=0)
    assert config.port == 1


def test_app_config_port_clamped_high():
    config = AppConfig(master_key="test-key", port=70000)
    assert config.port == 65535


def test_app_config_port_invalid():
    config = AppConfig(master_key="test-key", port="notanumber")
    assert config.port == 8080


def test_load_config_with_port():
    env = {"MASTER_KEY": "test-key", "PORT": "9090"}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.port == 9090


def test_load_config_default_port():
    env = {"MASTER_KEY": "test-key"}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.port == 8080


# ---------------------------------------------------------------------------
# NON_PRODUCTION_ENVIRONMENTS
#
# The allowlist replaced a URL-sniffing heuristic that could not see through an SSH or
# `kubectl port-forward` tunnel to GovCloud production. Anything NOT parsed into this set
# is treated as production, so parsing bugs fail closed by construction — but a parsing
# bug that ADDS a name (a stray blank, a case mismatch) fails open, which is what these
# tests pin.
# ---------------------------------------------------------------------------
def test_default_non_production_environments_covers_both_naming_conventions():
    # load_config()'s built-in API_PUBLIC_HOSTS defaults use development/production while
    # .env.example uses dev/prod. Both spellings must be non-production or a stock install
    # gets maximum friction on its dev environment.
    assert DEFAULT_NON_PRODUCTION_ENVIRONMENTS == frozenset(
        {"dev", "development", "local", "test", "perf", "sandbox", "staging", "stage"}
    )
    assert "prod" not in DEFAULT_NON_PRODUCTION_ENVIRONMENTS
    assert "production" not in DEFAULT_NON_PRODUCTION_ENVIRONMENTS


def test_non_production_environments_defaults_when_the_field_is_absent():
    config = AppConfig(master_key="test-key")
    assert config.non_production_environments == set(DEFAULT_NON_PRODUCTION_ENVIRONMENTS)


def test_load_config_non_production_environments_defaults_when_the_var_is_absent():
    env = {"MASTER_KEY": "test-key"}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.non_production_environments == set(DEFAULT_NON_PRODUCTION_ENVIRONMENTS)


def test_load_config_non_production_environments_from_csv_with_spaces():
    env = {"MASTER_KEY": "test-key", "NON_PRODUCTION_ENVIRONMENTS": " dev ,  qa,staging "}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.non_production_environments == {"dev", "qa", "staging"}


def test_load_config_non_production_environments_single_value():
    env = {"MASTER_KEY": "test-key", "NON_PRODUCTION_ENVIRONMENTS": "sandbox"}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.non_production_environments == {"sandbox"}


def test_load_config_non_production_environments_is_lowercased():
    env = {"MASTER_KEY": "test-key", "NON_PRODUCTION_ENVIRONMENTS": "DEV,Staging,pErF"}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.non_production_environments == {"dev", "staging", "perf"}


def test_load_config_non_production_environments_empty_string_protects_everything():
    # Deliberately NOT a fallback to the default: someone who writes
    # NON_PRODUCTION_ENVIRONMENTS= is asking for maximum friction everywhere, and that is
    # the safe direction to resolve the ambiguity in.
    env = {"MASTER_KEY": "test-key", "NON_PRODUCTION_ENVIRONMENTS": ""}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.non_production_environments == set()


def test_load_config_non_production_environments_blank_entries_are_dropped():
    env = {"MASTER_KEY": "test-key", "NON_PRODUCTION_ENVIRONMENTS": "dev,, ,  ,qa,"}
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        config = load_config()
    # A "" entry would match a blank environment name if the membership test ever ran
    # before the blank guard, so blanks must never enter the set.
    assert config.non_production_environments == {"dev", "qa"}


def test_non_production_environments_accepts_a_collection():
    config = AppConfig(master_key="test-key", non_production_environments=["Dev", " QA "])
    assert config.non_production_environments == {"dev", "qa"}


def test_non_production_environments_falls_back_on_an_unusable_type():
    config = AppConfig(master_key="test-key", non_production_environments=17)
    assert config.non_production_environments == set(DEFAULT_NON_PRODUCTION_ENVIRONMENTS)
