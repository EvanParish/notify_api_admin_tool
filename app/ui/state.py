"""Application state, globals, and auth/sync helpers.

This module owns the canonical instances of ``config``, ``encryption``,
``state``, and ``_active_api_clients``.  Other modules should import them
via the module reference (``from app.ui import state as _st``) so that
test patching on ``app.ui.state.config`` etc. is visible everywhere.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

from nicegui import app, ui
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app.api_client import HttpNotificationAPI, MockNotificationAPI, NotificationAPI
from app.config import AppConfig, _remap_host, load_config
from app.crypto import EncryptionManager
from app.db import create_all, dispose_engine, init_engine
from app.repository import DbSaltProvider, get_secure_setting, get_setting, set_setting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page timeout
# ---------------------------------------------------------------------------
PAGE_RESPONSE_TIMEOUT: float = 10.0


# ---------------------------------------------------------------------------
# AppState
# ---------------------------------------------------------------------------
@dataclass
class AppState:
    environment: str
    view_environments: List[str] = field(default_factory=list)
    api_status: str = "unknown"
    sync_message: str = ""
    dev_only_mode: bool = True
    enabled_sync_environments: set = None

    def __post_init__(self):
        if self.enabled_sync_environments is None:
            self.enabled_sync_environments = {"dev"}
        if not self.view_environments:
            self.view_environments = []  # empty means "all"


# ---------------------------------------------------------------------------
# Module-level globals (canonical location)
# ---------------------------------------------------------------------------
config: AppConfig = load_config()
encryption: EncryptionManager = EncryptionManager(config.master_key, salt_provider=DbSaltProvider())
state: AppState = AppState(environment=next(iter(config.api_hosts.keys()), "dev"))
service_search_query: str = ""
_active_api_clients: list[NotificationAPI] = []


# ---------------------------------------------------------------------------
# Database init (skipped during pytest)
# ---------------------------------------------------------------------------
if not os.getenv("PYTEST_CURRENT_TEST"):
    init_engine(config.database_path)  # pragma: no cover


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------
@app.on_startup
async def startup() -> None:
    await create_all()
    await ensure_default_hosts()


@app.on_shutdown
async def shutdown() -> None:
    for c in _active_api_clients:
        await c.aclose()
    _active_api_clients.clear()
    await dispose_engine()


@app.exception_handler(TimeoutError)
async def handle_timeout_error(request: Request, exc: TimeoutError) -> HTMLResponse:
    """Return a friendly retry page instead of a 500 error on page-load timeouts."""
    logger.warning("Page timed out: %s %s – %s", request.method, request.url.path, exc)
    page_url = str(request.url)
    html = (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Page Load Timeout</title>"
        "<style>"
        "body{margin:0;min-height:100vh;display:flex;align-items:center;"
        "justify-content:center;background:#0b0f14;color:#e2e8f0;"
        "font-family:system-ui,sans-serif}"
        ".card{text-align:center;padding:2.5rem;border-radius:12px;"
        "background:#161b22;border:1px solid #30363d;max-width:480px}"
        "h1{font-size:1.5rem;margin:0 0 .75rem}"
        "p{color:#8b949e;margin:0 0 1.5rem;line-height:1.5}"
        "a{display:inline-block;padding:.625rem 1.5rem;background:#238636;"
        "color:#fff;text-decoration:none;border-radius:6px;font-weight:600}"
        "a:hover{background:#2ea043}"
        "</style></head><body><div class='card'>"
        "<h1>⏱️ Page Load Timeout</h1>"
        "<p>The page took too long to load. This is usually temporary "
        "&mdash; please try again.</p>"
        f'<a href="{page_url}">Retry</a>'
        "</div></body></html>"
    )
    return HTMLResponse(content=html, status_code=504)


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------
async def ensure_default_hosts() -> None:
    for env, url in config.api_hosts.items():
        existing = await get_setting(f"base_url_{env}")
        if not existing:
            await set_setting(f"base_url_{env}", url)


async def build_api_client(env: str) -> NotificationAPI:
    if config.use_mock_api:
        return MockNotificationAPI()
    base_url = await get_setting(f"base_url_{env}") or config.api_hosts.get(env)
    if not base_url:
        raise RuntimeError(f"Base URL missing for environment {env}")
    if config.container_host:
        base_url = _remap_host(base_url, config.container_host)
    basic_user = await get_secure_setting(f"basic_username_{env}", encryption)
    basic_pass = await get_secure_setting(f"basic_password_{env}", encryption)
    client = HttpNotificationAPI(base_url=base_url, basic_username=basic_user, basic_password=basic_pass)
    _active_api_clients.append(client)
    return client


async def has_admin_auth(env: str) -> bool:
    if config.use_mock_api or os.getenv("PYTEST_CURRENT_TEST"):
        return True
    basic_user = await get_secure_setting(f"basic_username_{env}", encryption)
    basic_pass = await get_secure_setting(f"basic_password_{env}", encryption)
    return bool(basic_user and basic_pass)


async def get_missing_credentials(env: str) -> list[str]:
    """Return list of missing credential fields for an environment."""
    if config.use_mock_api or os.getenv("PYTEST_CURRENT_TEST"):
        return []
    missing = []
    basic_user = await get_secure_setting(f"basic_username_{env}", encryption)
    basic_pass = await get_secure_setting(f"basic_password_{env}", encryption)
    if not basic_user:
        missing.append("username")
    if not basic_pass:
        missing.append("password")
    return missing


async def check_environments_credentials(
    environments: list[str],
) -> dict[str, list[str]]:
    """Check credentials for multiple environments.

    Returns a dict mapping environment names to lists of missing fields.
    Environments with no missing fields are omitted from the result.
    """
    missing_by_env: dict[str, list[str]] = {}
    for env in environments:
        missing = await get_missing_credentials(env)
        if missing:
            missing_by_env[env] = missing
    return missing_by_env


async def ensure_admin_auth(env: str, sync_label) -> bool:
    if await has_admin_auth(env):
        return True
    missing = await get_missing_credentials(env)
    if missing:
        fields = " and ".join(missing)
        message = f"Missing {fields} for {env}. Set credentials in Settings > Global Admin Auth."
    else:
        message = f"Missing admin auth for {env}. Set credentials in Settings > Global Admin Auth."
    sync_label.text = message
    safe_notify(message, color="warning")
    return False


def handle_unauthorized(sync_label, env: str) -> None:
    message = f"Unauthorized for {env}. Check Global Admin Auth settings."
    sync_label.text = message
    safe_notify(message, color="warning")


async def check_api_online(env: str) -> bool:
    """Check if the API for the given environment is reachable."""
    if config.use_mock_api:
        return True
    try:
        api = await build_api_client(env)
        return await api.healthcheck()
    except Exception:
        return False


async def refresh_status_badge(badge) -> None:
    # Show status for the first enabled sync environment
    envs = list(state.enabled_sync_environments)
    if not envs:
        badge.text = "No environments enabled"
        badge.props("color=gray")
        return
    env = envs[0]
    if not await has_admin_auth(env):
        state.api_status = "auth missing"
        badge.text = "API Status: Auth Missing"
        badge.props("color=pink")
        return
    api = await build_api_client(env)
    ok = await api.healthcheck()
    state.api_status = "online" if ok else "offline"
    badge.text = f"API Status: {state.api_status.title()}"
    badge.props(f"color={'green' if ok else 'red'}")


def get_view_environments() -> Optional[List[str]]:
    """Return selected environments for filtering, or None if all selected."""
    if not state.view_environments:
        return None  # empty list means "all"
    return state.view_environments


# Backwards compatibility alias
def get_view_environment() -> Optional[List[str]]:
    return get_view_environments()


def safe_notify(message: str, color: str = "warning") -> None:
    try:
        ui.notify(message, color=color)
    except RuntimeError:
        logger.warning("UI notify skipped: %s", message)
