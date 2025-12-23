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
                "staging": "https://staging-notify.va.gov",
                "production": "https://api.notifications.va.gov",
            }
        ),
    )
    master_key = os.getenv("master_key")
    if not master_key:
        raise RuntimeError("master_key is required in the environment for encryption")

    return AppConfig(
        api_hosts=api_hosts_raw,
        master_key=master_key,
        use_mock_api=_parse_bool(os.getenv("USE_MOCK_API"), True),
        database_path=os.getenv("DATABASE_PATH", "data/app.db"),
        max_concurrency=os.getenv("MAX_CONCURRENCY", "25"),
    )
