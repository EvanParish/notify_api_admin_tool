import json
import os
from typing import Dict, Set

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# Environment names that are NOT production. Anything absent from this set is treated as
# production by ``permission_helpers.is_protected_environment``, so a newly added
# environment, a typo, or a tunnelled GovCloud target all fail closed into the
# maximum-friction path. Production-ness is DECLARED here, never inferred from a URL: a
# port-forwarded production API is reached on localhost and no hostname heuristic can see
# through the tunnel.
#
# Both naming conventions live in this repo: load_config()'s built-in defaults use
# development/production while .env.example uses dev/prod, so both spellings are listed.
DEFAULT_NON_PRODUCTION_ENVIRONMENTS = frozenset(
    {"dev", "development", "local", "test", "perf", "sandbox", "staging", "stage"}
)


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
    request_timeout: float = 30.0
    port: int = 8080
    container_host: str | None = None
    non_production_environments: Set[str] = Field(default_factory=lambda: set(DEFAULT_NON_PRODUCTION_ENVIRONMENTS))

    @field_validator("non_production_environments", mode="before")
    @classmethod
    def parse_non_production_environments(cls, value):
        """Comma-separated env names, trimmed and lowercased, blanks ignored.

        ``None`` (the var is absent) yields the default set. An explicitly empty or
        all-blank string yields an EMPTY set, which marks every environment production.
        That distinction is deliberate and it fails in the safe direction: someone who
        writes ``NON_PRODUCTION_ENVIRONMENTS=`` gets maximum friction everywhere, not a
        silent fallback to a permissive default they did not ask for.
        """
        if value is None:
            return set(DEFAULT_NON_PRODUCTION_ENVIRONMENTS)
        if isinstance(value, str):
            return {part.strip().lower() for part in value.split(",") if part.strip()}
        if isinstance(value, (set, frozenset, list, tuple)):
            return {str(part).strip().lower() for part in value if str(part).strip()}
        return set(DEFAULT_NON_PRODUCTION_ENVIRONMENTS)

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

    @field_validator("request_timeout", mode="before")
    @classmethod
    def clamp_timeout(cls, value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 30.0
        return max(1.0, min(number, 300.0))

    @field_validator("port", mode="before")
    @classmethod
    def validate_port(cls, value):
        try:
            number = int(value)
        except Exception:
            return 8080
        return max(1, min(number, 65535))


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
        request_timeout=os.getenv("REQUEST_TIMEOUT", "30"),
        port=os.getenv("PORT", "8080"),
        container_host=os.getenv("CONTAINER_HOST"),
        non_production_environments=os.getenv("NON_PRODUCTION_ENVIRONMENTS"),
    )

    # When running in Docker, remap localhost URLs to reach the host machine.
    if config.container_host:
        config.api_hosts = {env: _remap_host(url, config.container_host) for env, url in config.api_hosts.items()}

    return config


def _remap_host(url: str, container_host: str) -> str:
    """Replace localhost/127.0.0.1 with *container_host* in a URL."""
    return url.replace("localhost", container_host).replace("127.0.0.1", container_host)
