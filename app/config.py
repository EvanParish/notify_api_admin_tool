import json
import os
from typing import Dict

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


class AppConfig(BaseModel):
    api_hosts: Dict[str, str] = Field(default_factory=dict)
    master_key: str
    use_mock_api: bool = True
    database_path: str = "data/app.db"
    max_concurrency: int = 25
    container_host: str | None = None

    @field_validator("api_hosts", mode="before")
    @classmethod
    def parse_api_hosts(cls, value):
        if not value:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                hosts: Dict[str, str] = {}
                for part in value.split(","):
                    if "=" in part:
                        env, url = part.split("=", 1)
                        hosts[env.strip()] = url.strip()
                return hosts
        return {}

    @field_validator("max_concurrency", mode="before")
    @classmethod
    def clamp_concurrency(cls, value):
        try:
            number = int(value)
        except Exception:
            return 25
        return max(1, min(number, 100))


def load_config() -> AppConfig:
    load_dotenv()
    api_hosts_raw = os.getenv(
        "API_PUBLIC_HOSTS",
        json.dumps(
            {
                "development": "https://dev-notify.va.gov",
                "perf": "https://sandbox-api.va.gov/vanotify",
                "staging": "https://staging-notify.va.gov",
                "production": "https://api.notifications.va.gov",
            }
        ),
    )
    master_key = os.getenv("MASTER_KEY")
    if not master_key:
        raise RuntimeError("MASTER_KEY is required in the environment for encryption")

    config = AppConfig(
        api_hosts=api_hosts_raw,
        master_key=master_key,
        use_mock_api=_parse_bool(os.getenv("USE_MOCK_API"), True),
        database_path=os.getenv("DATABASE_PATH", "data/app.db"),
        max_concurrency=os.getenv("MAX_CONCURRENCY", "25"),
        container_host=os.getenv("CONTAINER_HOST"),
    )

    # When running in Docker, remap localhost URLs to reach the host machine.
    if config.container_host:
        config.api_hosts = {env: _remap_host(url, config.container_host) for env, url in config.api_hosts.items()}

    return config


def _remap_host(url: str, container_host: str) -> str:
    """Replace localhost/127.0.0.1 with *container_host* in a URL."""
    return url.replace("localhost", container_host).replace("127.0.0.1", container_host)
