from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import httpx
from nicegui import app, ui
from nicegui.client import Client
from nicegui.elements.input import Input

from app.api_client import HttpNotificationAPI, MockNotificationAPI, NotificationAPI
from app.config import AppConfig, load_config
from app.crypto import EncryptionManager
from app.db import create_all, init_engine, dispose_engine
from app.repository import (
    add_local_key,
    get_secure_setting,
    get_setting,
    list_api_keys,
    list_local_keys,
    list_provider_details,
    list_sms_senders,
    list_services,
    list_templates,
    list_users,
    resolve_local_key,
    set_secure_setting,
    set_setting,
)
from app.sync import SyncManager
from app.utils import extract_placeholders, validate_recipient

logger = logging.getLogger(__name__)
_original_client_delete = Client.delete


def _safe_client_delete(self) -> None:
    try:
        _original_client_delete(self)
    except KeyError:
        logger.warning("NiceGUI client already deleted: %s", self.id)
        self._deleted = True


Client.delete = _safe_client_delete


@dataclass
class AppState:
    environment: str
    view_environment: str = "all"
    api_status: str = "unknown"
    sync_message: str = ""
    dev_only_mode: bool = True
    enabled_sync_environments: set = None

    def __post_init__(self):
        if self.enabled_sync_environments is None:
            self.enabled_sync_environments = {"dev"}
        if not self.view_environment:
            self.view_environment = "all"


config: AppConfig = load_config()
encryption = EncryptionManager(config.master_key)
state = AppState(environment=next(iter(config.api_hosts.keys()), "dev"))
service_search_query = ""

ui.add_head_html(
        """
        <meta name="color-scheme" content="dark light">
        <style>
            html, body, #q-app, .q-layout, .q-page-container {
                background-color: #0b0f14 !important;
                color-scheme: dark;
            }
            body.body--light, body.body--light #q-app, body.body--light .q-layout, body.body--light .q-page-container {
                background-color: #f8fafc !important;
                color-scheme: light;
            }
            body.body--dark, body.body--dark #q-app, body.body--dark .q-layout, body.body--dark .q-page-container {
                background-color: #0b0f14 !important;
                color-scheme: dark;
            }
        </style>
        <script>
            document.documentElement.style.backgroundColor = '#0b0f14';
            document.documentElement.classList.add('body--dark');
            if (document.body) {
                document.body.style.backgroundColor = '#0b0f14';
                document.body.classList.add('body--dark');
            } else {
                document.addEventListener('DOMContentLoaded', () => {
                    document.body.style.backgroundColor = '#0b0f14';
                    document.body.classList.add('body--dark');
                });
            }
            window.copyTableCellText = async (text) => {
                const value = String(text ?? '');
                try {
                    if (navigator?.clipboard?.writeText) {
                        await navigator.clipboard.writeText(value);
                        return true;
                    }
                } catch (error) {
                    console.warn('Clipboard API copy failed; using fallback.', error);
                }
                if (!document?.body) return false;
                const textarea = document.createElement('textarea');
                textarea.value = value;
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                try {
                    return document.execCommand('copy');
                } finally {
                    document.body.removeChild(textarea);
                }
            };
        </script>
        """
)

# Only initialize database if not in test mode
# Tests will call init_engine with their own temporary database
if not os.getenv("PYTEST_CURRENT_TEST"):
    init_engine(config.database_path)


@app.on_startup
async def startup() -> None:
    await create_all()
    await ensure_default_hosts()


@app.on_shutdown
async def shutdown() -> None:
    await dispose_engine()


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
    basic_user = await get_secure_setting(f"basic_username_{env}", encryption)
    basic_pass = await get_secure_setting(f"basic_password_{env}", encryption)
    return HttpNotificationAPI(
        base_url=base_url, basic_username=basic_user, basic_password=basic_pass
    )


async def refresh_status_badge(badge) -> None:
    if not await has_admin_auth(state.environment):
        state.api_status = "auth missing"
        badge.text = "API Status: Auth Missing"
        badge.props("color=pink")
        return
    api = await build_api_client(state.environment)
    ok = await api.healthcheck()
    state.api_status = "online" if ok else "offline"
    badge.text = f"API Status: {state.api_status.title()}"
    badge.props(f"color={'green' if ok else 'red'}")


async def ensure_sync_enabled(sync_label) -> bool:
    if state.environment not in state.enabled_sync_environments:
        sync_label.text = f"Sync disabled for {state.environment}"
        ui.notify(
            f"Syncing is not enabled for {state.environment} environment",
            color="warning",
        )
        return False
    return True


async def handle_full_sync(status_badge, sync_label) -> None:
    # Check if current environment is enabled for syncing
    if not await ensure_sync_enabled(sync_label):
        return

    if not await ensure_admin_auth(state.environment, sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Starting sync..."
    try:
        await manager.sync_all(progress=progress)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            handle_unauthorized(sync_label, state.environment)
            return
        raise
    sync_label.text = "Sync complete"
    await refresh_tables()
    await refresh_status_badge(status_badge)


async def handle_services_sync(status_badge, sync_label) -> None:
    if not await ensure_sync_enabled(sync_label):
        return

    if not await ensure_admin_auth(state.environment, sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Syncing services..."
    try:
        await manager.sync_services(progress=progress)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            handle_unauthorized(sync_label, state.environment)
            return
        raise
    sync_label.text = "Sync complete"
    await refresh_if_needed(services_table)
    await refresh_status_badge(status_badge)


async def handle_templates_sync(status_badge, sync_label) -> None:
    if not await ensure_sync_enabled(sync_label):
        return

    if not await ensure_admin_auth(state.environment, sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Syncing services..."
    try:
        await manager.sync_services(progress=progress)
        sync_label.text = "Syncing templates..."
        await manager.sync_templates(progress=progress)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            handle_unauthorized(sync_label, state.environment)
            return
        raise
    sync_label.text = "Sync complete"
    await refresh_status_badge(status_badge)


async def handle_api_keys_sync(status_badge, sync_label) -> None:
    if not await ensure_sync_enabled(sync_label):
        return

    if not await ensure_admin_auth(state.environment, sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Syncing services..."
    try:
        await manager.sync_services(progress=progress)
        sync_label.text = "Syncing API keys..."
        await manager.sync_api_keys(progress=progress)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            handle_unauthorized(sync_label, state.environment)
            return
        raise
    sync_label.text = "Sync complete"
    await refresh_status_badge(status_badge)


async def handle_sms_senders_sync(status_badge, sync_label) -> None:
    if not await ensure_sync_enabled(sync_label):
        return

    if not await ensure_admin_auth(state.environment, sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Syncing services..."
    try:
        await manager.sync_services(progress=progress)
        sync_label.text = "Syncing SMS senders..."
        await manager.sync_sms_senders(progress=progress)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            handle_unauthorized(sync_label, state.environment)
            return
        raise
    sync_label.text = "Sync complete"
    await refresh_status_badge(status_badge)


async def handle_users_sync(status_badge, sync_label) -> None:
    if not await ensure_sync_enabled(sync_label):
        return

    if not await ensure_admin_auth(state.environment, sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Syncing users..."
    try:
        await manager.sync_users(progress=progress)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            handle_unauthorized(sync_label, state.environment)
            return
        raise
    sync_label.text = "Sync complete"
    await refresh_status_badge(status_badge)


async def handle_provider_details_sync(status_badge, sync_label) -> None:
    if not await ensure_sync_enabled(sync_label):
        return

    if not await ensure_admin_auth(state.environment, sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Syncing provider details..."
    try:
        await manager.sync_provider_details(progress=progress)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            handle_unauthorized(sync_label, state.environment)
            return
        raise
    sync_label.text = "Sync complete"
    await refresh_status_badge(status_badge)


async def refresh_tables() -> None:
    await refresh_if_needed(services_table)


def set_theme_preference(is_dark: bool) -> None:
    app.storage.user["theme"] = "dark" if is_dark else "light"


def toggle_theme(dark_mode) -> None:
    dark_mode.toggle()
    set_theme_preference(dark_mode.value)


async def ensure_theme_preference(dark_mode) -> None:
    stored_theme = app.storage.user.get("theme")
    if stored_theme not in {"light", "dark"}:
        stored_theme = "light"
        app.storage.user["theme"] = stored_theme
    dark_mode.value = stored_theme == "dark"


def build_shell(on_view_env_change=None) -> tuple:
    drawer = (
        ui.left_drawer(value=True)
        .props("show-if-above bordered")
        .classes("bg-slate-50 dark:bg-slate-900")
    )
    with drawer:
        ui.link("Dashboard", "/")
        ui.link("Send Notification", "/send")
        ui.link("Bulk Send", "/bulk-send")
        ui.link("Services", "/services")
        ui.link("Templates", "/templates")
        ui.link("API Keys", "/api-keys")
        ui.link("Users", "/users")
        ui.link("SMS Senders", "/sms-senders")
        ui.link("Provider Details", "/provider-details")
        ui.link("Settings", "/settings")

    dark_mode = ui.dark_mode()
    with ui.header().classes("items-center justify-between bg-gray-200 dark:bg-slate-800"):
        with ui.row().classes("items-center gap-3"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense")
            ui.label("Notification Admin Dashboard").classes(
                "text-xl font-medium text-slate-900 dark:text-white"
            )
        with ui.row().classes("items-center gap-4"):
            status_badge = ui.badge("API Status: Unknown", color="gray")
            sync_label = ui.label("").classes("text-slate-900 dark:text-white")
            env_options = {"all": "All"}
            env_options.update({env: env.title() for env in config.api_hosts})
            env_select = ui.select(
                env_options, value=state.view_environment, label="View Env"
            ).classes("w-36")
            sync_env_select = ui.select(
                {env: env.title() for env in config.api_hosts},
                value=state.environment,
                label="Sync Env",
            ).classes("w-32")
            refresh_button = ui.button("Refresh All Data")
            theme_button = ui.button(icon="dark_mode").props("flat round dense")
            theme_button.on_click(lambda: toggle_theme(dark_mode))
            if on_view_env_change:
                async def handle_env_change(e):
                    state.view_environment = e.value
                    result = on_view_env_change()
                    if inspect.isawaitable(result):
                        await result

                env_select.on_value_change(handle_env_change)
            else:
                env_select.on_value_change(lambda e: setattr(state, "view_environment", e.value))
            async def handle_sync_env_change(e):
                state.environment = e.value
                await refresh_status_badge(status_badge)
                ui.notify(f"Switched to {e.value} environment", color="info")

            sync_env_select.on_value_change(handle_sync_env_change)

            with ui.dropdown_button("Sync Settings", auto_close=False).props("flat"):
                ui.label("Allowed sync environments").classes("text-sm mb-2")
                env_checkboxes: Dict[str, ui.checkbox] = {}
                for env in config.api_hosts:
                    is_enabled = env in state.enabled_sync_environments
                    checkbox = ui.checkbox(env.title(), value=is_enabled)
                    env_checkboxes[env] = checkbox

                    def make_handler(environment):
                        def handler(e):
                            if e.value:
                                state.enabled_sync_environments.add(environment)
                                ui.notify(
                                    f"Syncing enabled for {environment}", color="positive"
                                )
                            else:
                                state.enabled_sync_environments.discard(environment)
                                ui.notify(
                                    f"Syncing disabled for {environment}", color="info"
                                )

                        return handler

                    checkbox.on_value_change(make_handler(env))
    return status_badge, sync_label, refresh_button, dark_mode


# Pages
@ui.page("/")
async def dashboard_page() -> None:
    @ui.refreshable
    async def render_dashboard() -> None:
        services = await list_services(get_view_environment())
        templates = await list_templates(environment=get_view_environment())
        with ui.column().classes("p-8 gap-6 w-full max-w-none"):
            ui.label("Dashboard").classes("text-lg font-semibold")
            with ui.row().classes("gap-4 w-full"):
                metric_card("Services", len(services))
                metric_card("Templates", len(templates))
            ui.markdown(
                "This dashboard caches services, templates, and local API keys. Use the left navigation to manage data and send notifications."
            )

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=lambda: refresh_if_needed(render_dashboard)
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)
    await render_dashboard()


def metric_card(title: str, value: int) -> None:
    with ui.card().classes("flex-1 min-w-[240px]"):
        ui.label(title).classes("text-sm text-gray-600 dark:text-slate-300")
        ui.label(str(value)).classes("text-3xl font-bold")


async def refresh_if_needed(refreshable) -> None:
    result = refreshable.refresh()
    if inspect.isawaitable(result):
        await result


def make_sortable(columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{**column, "sortable": True} for column in columns]


COPYABLE_FIELDS = ("id", "service_id", "name", "key_name", "sms_sender")
COPYABLE_CELL_SLOT = """
<q-td :props="props">
  <span
    class="cursor-pointer text-primary"
    title="Click to copy"
    @click="$parent.$emit('copy', props.value)"
  >{{ props.value }}</span>
</q-td>
"""


def copy_to_clipboard(text: Any) -> None:
    value = "" if text is None else str(text)
    ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(value)})")
    safe_notify(f'Copied "{value}" to clipboard!', color="green")


def get_copyable_fields(rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return []
    return [field for field in COPYABLE_FIELDS if field in rows[0]]


def add_copyable_slots(table, rows: List[Dict[str, Any]]) -> None:
    copyable_fields = get_copyable_fields(rows)
    for field in copyable_fields:
        table.add_slot(f"body-cell-{field}", COPYABLE_CELL_SLOT)
    if copyable_fields:
        table.on("copy", lambda e: copy_to_clipboard(e.args))


def format_environment(value: Optional[str]) -> str:
    return value or "unknown"


def format_service_label(service) -> str:
    return f"{service.name} ({format_environment(service.environment)})"


def truncate_text(value: Optional[str], limit: int = 50) -> Optional[str]:
    if not value:
        return value
    return value[:limit] + "..." if len(value) > limit else value


def get_view_environment() -> Optional[str]:
    return None if state.view_environment in {"all", None, ""} else state.view_environment


def safe_notify(message: str, color: str = "warning") -> None:
    try:
        ui.notify(message, color=color)
    except RuntimeError:
        logger.warning("UI notify skipped: %s", message)


def find_missing_personalisation(personalisation: Dict[str, Any]) -> Optional[str]:
    for key, value in personalisation.items():
        if value is None or str(value).strip() == "":
            return key
    return None


async def has_admin_auth(env: str) -> bool:
    if config.use_mock_api or os.getenv("PYTEST_CURRENT_TEST"):
        return True
    basic_user = await get_secure_setting(f"basic_username_{env}", encryption)
    basic_pass = await get_secure_setting(f"basic_password_{env}", encryption)
    return bool(basic_user and basic_pass)


async def ensure_admin_auth(env: str, sync_label) -> bool:
    if await has_admin_auth(env):
        return True
    message = (
        f"Missing admin auth for {env}. "
        "Set credentials in Settings > Global Admin Auth."
    )
    sync_label.text = message
    safe_notify(message, color="warning")
    return False


def handle_unauthorized(sync_label, env: str) -> None:
    message = f"Unauthorized for {env}. Check Global Admin Auth settings."
    sync_label.text = message
    safe_notify(message, color="warning")


def _parse_filter_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value.split("T", 1)[0])
    except ValueError:
        return None


def _matches_expiry_range(
    expiry_value: Optional[str], start_date: Optional[date], end_date: Optional[date]
) -> bool:
    if not start_date and not end_date:
        return True
    expiry_date = _parse_filter_date(expiry_value)
    if not expiry_date:
        return False
    if start_date and expiry_date < start_date:
        return False
    if end_date and expiry_date > end_date:
        return False
    return True


async def handle_service_search(value: Optional[str]) -> None:
    global service_search_query
    service_search_query = (value or "").strip().lower()
    await refresh_if_needed(services_table)


async def handle_service_search_event(e) -> None:
    await handle_service_search(getattr(e, "value", None))


@ui.page("/services")
async def services_page() -> None:
    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=lambda: refresh_if_needed(services_table)
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    async def page_sync_services():
        await handle_services_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Services").classes("text-lg font-semibold")
        service_search = ui.input(
            label="Search by Service ID or Name"
        ).classes("w-full md:w-1/2")
        service_search.on_value_change(handle_service_search_event)
        ui.button("Sync Services", on_click=page_sync_services)
        await services_table()


@ui.refreshable
async def services_table() -> None:
    rows = await list_services(get_view_environment())
    if service_search_query:
        rows = [
            row
            for row in rows
            if service_search_query in (row.id or "").lower()
            or service_search_query in (row.name or "").lower()
        ]
    table_rows: List[Dict[str, Any]] = [
        {
            "id": row.id,
            "environment": format_environment(row.environment),
            "name": row.name,
            "active": row.active,
            "restricted": row.restricted,
            "message_limit": row.message_limit,
            "rate_limit": row.rate_limit,
            "research_mode": row.research_mode,
            "count_as_live": row.count_as_live,
            "permissions": row.permissions[:50] + "..."
            if row.permissions and len(row.permissions) > 50
            else row.permissions,
        }
        for row in rows
    ]
    table = ui.table(
        columns=make_sortable(
            [
            {"name": "id", "label": "ID", "field": "id"},
            {"name": "environment", "label": "Environment", "field": "environment"},
            {"name": "name", "label": "Name", "field": "name"},
            {"name": "active", "label": "Active", "field": "active"},
            {"name": "restricted", "label": "Restricted", "field": "restricted"},
            {"name": "message_limit", "label": "Msg Limit", "field": "message_limit"},
            {"name": "rate_limit", "label": "Rate Limit", "field": "rate_limit"},
            {"name": "research_mode", "label": "Research", "field": "research_mode"},
            {"name": "count_as_live", "label": "Live", "field": "count_as_live"},
            {"name": "permissions", "label": "Permissions", "field": "permissions"},
            ]
        ),
        rows=table_rows,
        pagination={"rowsPerPage": 10},
    )
    table.props("row-key=id").classes("w-full")
    add_copyable_slots(table, table_rows)


@ui.page("/templates")
async def templates_page() -> None:
    template_search_query = ""

    async def refresh_service_options() -> None:
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
        }
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None

    async def handle_view_env_change() -> None:
        await refresh_service_options()
        await refresh_if_needed(render_table)

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=handle_view_env_change
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    async def page_sync_templates():
        await handle_templates_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Templates").classes("text-lg font-semibold")
        template_search = ui.input(
            label="Search by Template ID or Name"
        ).classes("w-full md:w-1/2")
        filter_row = ui.row().classes("gap-2")
        service_options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
        }
        type_options = {"email": "Email", "sms": "SMS"}
        service_select = ui.select(
            service_options, label="Service", with_input=True
        ).props("clearable")
        type_select = ui.select(type_options, label="Type", with_input=True).props(
            "clearable"
        )

        async def handle_sync_templates() -> None:
            await page_sync_templates()
            render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            selected_service = service_select.value
            selected_type = type_select.value
            rows = await list_templates(
                selected_service, selected_type, environment=get_view_environment()
            )
            if template_search_query:
                rows = [
                    row
                    for row in rows
                    if template_search_query in (row.id or "").lower()
                    or template_search_query in (row.name or "").lower()
                ]
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": row.id,
                    "environment": format_environment(row.environment),
                    "service_id": row.service_id,
                    "name": truncate_text(row.name),
                    "template_type": row.template_type,
                    "version": row.version,
                    "archived": row.archived,
                    "hidden": row.hidden,
                    "updated_at": row.updated_at[:10]
                    if row.updated_at
                    else None,  # Show date only
                    "subject": truncate_text(row.subject),
                    "content": truncate_text(row.content),
                }
                for row in rows
            ]
            table = ui.table(
                columns=make_sortable(
                    [
                    {"name": "id", "label": "ID", "field": "id"},
                    {"name": "environment", "label": "Environment", "field": "environment"},
                    {"name": "service_id", "label": "Service", "field": "service_id"},
                    {"name": "name", "label": "Name", "field": "name"},
                    {
                        "name": "template_type",
                        "label": "Type",
                        "field": "template_type",
                    },
                    {"name": "version", "label": "Version", "field": "version"},
                    {"name": "archived", "label": "Archived", "field": "archived"},
                    {"name": "hidden", "label": "Hidden", "field": "hidden"},
                    {"name": "updated_at", "label": "Updated", "field": "updated_at"},
                    {"name": "subject", "label": "Subject", "field": "subject"},
                    {"name": "content", "label": "Content", "field": "content"},
                    ]
                ),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
                )
            table.props("row-key=id").classes("w-full")
            add_copyable_slots(table, table_rows)

        async def handle_template_search_event(e) -> None:
            nonlocal template_search_query
            template_search_query = (getattr(e, "value", None) or "").strip().lower()
            await refresh_if_needed(render_table)

        service_select.on_value_change(lambda _: render_table.refresh())
        type_select.on_value_change(lambda _: render_table.refresh())
        template_search.on_value_change(handle_template_search_event)
        ui.button("Sync Templates", on_click=handle_sync_templates)
        await render_table()


@ui.page("/api-keys")
async def api_keys_page() -> None:
    async def refresh_service_options() -> None:
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
        }
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None

    async def handle_view_env_change() -> None:
        await refresh_service_options()
        await refresh_if_needed(render_table)

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=handle_view_env_change
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    async def page_sync_api_keys():
        await handle_api_keys_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("API Keys").classes("text-lg font-semibold")

        service_options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
        }
        service_select = ui.select(
            service_options, label="Filter by Service", with_input=True
        ).props("clearable")
        expires_from = ui.input(label="Expires from").props("clearable type=date")
        expires_to = ui.input(label="Expires to").props("clearable type=date")

        async def handle_sync_keys() -> None:
            await page_sync_api_keys()
            render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            selected_service = service_select.value
            start_date = _parse_filter_date(expires_from.value)
            end_date = _parse_filter_date(expires_to.value)
            keys = await list_api_keys(selected_service, environment=get_view_environment())
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": key.id,
                    "environment": format_environment(key.environment),
                    "service_id": key.service_id,
                    "name": key.name,
                    "key_type": key.key_type,
                    "expiry_date": key.expiry_date,
                    "created_by": key.created_by,
                    "created_at": key.created_at[:10] if key.created_at else None,
                    "revoked": key.revoked,
                    "version": key.version,
                }
                for key in keys
                if _matches_expiry_range(key.expiry_date, start_date, end_date)
            ]
            table = ui.table(
                columns=make_sortable(
                    [
                    {"name": "id", "label": "ID", "field": "id"},
                    {"name": "environment", "label": "Environment", "field": "environment"},
                    {"name": "service_id", "label": "Service ID", "field": "service_id"},
                    {"name": "name", "label": "Name", "field": "name"},
                    {"name": "key_type", "label": "Type", "field": "key_type"},
                    {"name": "expiry_date", "label": "Expires", "field": "expiry_date"},
                    {"name": "created_by", "label": "Created By", "field": "created_by"},
                    {"name": "created_at", "label": "Created", "field": "created_at"},
                    {"name": "revoked", "label": "Revoked", "field": "revoked"},
                    {"name": "version", "label": "Version", "field": "version"},
                    ]
                ),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=id").classes("w-full")
            add_copyable_slots(table, table_rows)

        service_select.on_value_change(lambda _: render_table.refresh())
        expires_from.on_value_change(lambda _: render_table.refresh())
        expires_to.on_value_change(lambda _: render_table.refresh())
        ui.button("Sync API Keys", on_click=handle_sync_keys)
        await render_table()


@ui.page("/users")
async def users_page() -> None:
    user_search_query = ""

    async def handle_view_env_change() -> None:
        await refresh_if_needed(render_table)

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=handle_view_env_change
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Users").classes("text-lg font-semibold")
        user_search = ui.input(
            label="Search by Name or Email"
        ).classes("w-full md:w-1/2")

        async def handle_sync_users() -> None:
            await handle_users_sync(status_badge, sync_label)
            render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            users = await list_users(get_view_environment())
            if user_search_query:
                users = [
                    user
                    for user in users
                    if user_search_query in (user.name or "").lower()
                    or user_search_query in (user.email_address or "").lower()
                ]
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": user.id,
                    "environment": format_environment(user.environment),
                    "email_address": user.email_address,
                    "name": user.name,
                    "state": user.state,
                    "platform_admin": user.platform_admin,
                    "blocked": user.blocked,
                    "auth_type": user.auth_type,
                    "mobile_number": user.mobile_number,
                    "failed_login_count": user.failed_login_count,
                    "logged_in_at": user.logged_in_at,
                    "password_changed_at": user.password_changed_at,
                    "services_count": len(user.services or []),
                    "organisations_count": len(user.organisations or []),
                }
                for user in users
            ]
            table = ui.table(
                columns=make_sortable(
                    [
                        {"name": "id", "label": "ID", "field": "id"},
                        {
                            "name": "environment",
                            "label": "Environment",
                            "field": "environment",
                        },
                        {
                            "name": "email_address",
                            "label": "Email",
                            "field": "email_address",
                        },
                        {"name": "name", "label": "Name", "field": "name"},
                        {"name": "state", "label": "State", "field": "state"},
                        {
                            "name": "platform_admin",
                            "label": "Platform Admin",
                            "field": "platform_admin",
                        },
                        {"name": "blocked", "label": "Blocked", "field": "blocked"},
                        {"name": "auth_type", "label": "Auth", "field": "auth_type"},
                        {
                            "name": "mobile_number",
                            "label": "Mobile",
                            "field": "mobile_number",
                        },
                        {
                            "name": "failed_login_count",
                            "label": "Failed Logins",
                            "field": "failed_login_count",
                        },
                        {
                            "name": "logged_in_at",
                            "label": "Logged In",
                            "field": "logged_in_at",
                        },
                        {
                            "name": "password_changed_at",
                            "label": "Password Changed",
                            "field": "password_changed_at",
                        },
                        {
                            "name": "services_count",
                            "label": "Services",
                            "field": "services_count",
                        },
                        {
                            "name": "organisations_count",
                            "label": "Orgs",
                            "field": "organisations_count",
                        },
                    ]
                ),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=id").classes("w-full")
            add_copyable_slots(table, table_rows)

        async def handle_user_search_event(e) -> None:
            nonlocal user_search_query
            user_search_query = (getattr(e, "value", None) or "").strip().lower()
            await refresh_if_needed(render_table)

        ui.button("Sync Users", on_click=handle_sync_users)
        user_search.on_value_change(handle_user_search_event)
        await render_table()


@ui.page("/sms-senders")
async def sms_senders_page() -> None:
    async def refresh_service_options() -> None:
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
        }
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None

    async def handle_view_env_change() -> None:
        await refresh_service_options()
        await refresh_if_needed(render_table)

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=handle_view_env_change
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    async def page_sync_sms_senders():
        await handle_sms_senders_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("SMS Senders").classes("text-lg font-semibold")

        service_options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
        }
        service_select = ui.select(
            service_options, label="Filter by Service", with_input=True
        ).props("clearable")

        async def handle_sync_senders() -> None:
            await page_sync_sms_senders()
            render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            selected_service = service_select.value
            senders = await list_sms_senders(
                selected_service, environment=get_view_environment()
            )
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": sender.id,
                    "environment": format_environment(sender.environment),
                    "service_id": sender.service_id,
                    "sms_sender": sender.sms_sender,
                    "is_default": sender.is_default,
                    "archived": sender.archived,
                    "description": sender.description,
                    "provider_name": sender.provider_name,
                    "rate_limit": sender.rate_limit,
                    "rate_limit_interval": sender.rate_limit_interval,
                    "created_at": sender.created_at[:10] if sender.created_at else None,
                    "updated_at": sender.updated_at[:10] if sender.updated_at else None,
                }
                for sender in senders
            ]
            table = ui.table(
                columns=make_sortable(
                    [
                        {"name": "id", "label": "ID", "field": "id"},
                        {"name": "environment", "label": "Environment", "field": "environment",},
                        {"name": "service_id", "label": "Service", "field": "service_id"},
                        {"name": "sms_sender", "label": "SMS Sender", "field": "sms_sender"},
                        {"name": "is_default", "label": "Default", "field": "is_default"},
                        {"name": "archived", "label": "Archived", "field": "archived"},
                        {"name": "description", "label": "Description", "field": "description"},
                        {"name": "provider_name", "label": "Provider", "field": "provider_name"},
                        {"name": "rate_limit", "label": "Rate Limit", "field": "rate_limit"},
                        {"name": "rate_limit_interval", "label": "Rate Interval", "field": "rate_limit_interval"},
                        {"name": "created_at", "label": "Created", "field": "created_at"},
                        {"name": "updated_at", "label": "Updated", "field": "updated_at"},
                    ]
                ),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=id").classes("w-full")
            add_copyable_slots(table, table_rows)

        service_select.on_value_change(lambda _: render_table.refresh())
        ui.button("Sync SMS Senders", on_click=handle_sync_senders)
        await render_table()


@ui.page("/provider-details")
async def provider_details_page() -> None:
    async def handle_view_env_change() -> None:
        await refresh_if_needed(render_table)

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=handle_view_env_change
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Provider Details").classes("text-lg font-semibold")

        async def handle_sync_provider_details() -> None:
            await handle_provider_details_sync(status_badge, sync_label)
            render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            providers = await list_provider_details(get_view_environment())
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": provider.id,
                    "environment": format_environment(provider.environment),
                    "name": provider.display_name,
                    "identifier": provider.identifier,
                    "notification_type": provider.notification_type,
                    "priority": provider.priority,
                    "load_balancing_weight": provider.load_balancing_weight,
                    "active": provider.active,
                    "supports_international": provider.supports_international,
                    "current_month_billable_sms": provider.current_month_billable_sms,
                    "created_by_name": provider.created_by_name,
                    "updated_at": provider.updated_at,
                }
                for provider in providers
            ]
            table = ui.table(
                columns=make_sortable(
                    [
                        {"name": "id", "label": "ID", "field": "id"},
                        {
                            "name": "environment",
                            "label": "Environment",
                            "field": "environment",
                        },
                        {
                            "name": "name",
                            "label": "Display Name",
                            "field": "name",
                        },
                        {
                            "name": "identifier",
                            "label": "Identifier",
                            "field": "identifier",
                        },
                        {
                            "name": "notification_type",
                            "label": "Type",
                            "field": "notification_type",
                        },
                        {"name": "priority", "label": "Priority", "field": "priority"},
                        {
                            "name": "load_balancing_weight",
                            "label": "Weight",
                            "field": "load_balancing_weight",
                        },
                        {"name": "active", "label": "Active", "field": "active"},
                        {
                            "name": "supports_international",
                            "label": "International",
                            "field": "supports_international",
                        },
                        {
                            "name": "current_month_billable_sms",
                            "label": "Billable SMS",
                            "field": "current_month_billable_sms",
                        },
                        {
                            "name": "created_by_name",
                            "label": "Created By",
                            "field": "created_by_name",
                        },
                        {
                            "name": "updated_at",
                            "label": "Updated",
                            "field": "updated_at",
                        },
                    ]
                ),
                rows=table_rows,
                pagination={"rowsPerPage": 9},
            )
            table.props("row-key=id").classes("w-full")
            add_copyable_slots(table, table_rows)

        ui.button("Sync Provider Details", on_click=handle_sync_provider_details)
        await render_table()


@ui.page("/send")
async def send_page() -> None:
    placeholder_pattern = re.compile(r"\(\((.*?)\)\)")

    async def refresh_service_options() -> None:
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(state.environment)
        }
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None
        await handle_service_change()

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=refresh_service_options
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    service_options = {
        svc.id: format_service_label(svc)
        for svc in await list_services(state.environment)
    }
    env_options = list(config.api_hosts.keys())

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Send Notification").classes("text-lg font-semibold")
        env_select = ui.select(
            env_options, value=state.environment, label="Environment"
        ).classes("w-full md:w-1/2")
        service_select = ui.select(
            service_options, label="Service", with_input=True
        ).props("clearable").classes("w-full md:w-1/2")
        key_select = ui.select({}, label="API Key").props("clearable").classes("w-full md:w-1/2")
        type_toggle = ui.toggle({"email": "Email", "sms": "SMS"}, value="email")
        template_select = ui.select({}, label="Template", with_input=True).props(
            "clearable"
        ).classes("w-full md:w-1/2")
        recipient_input = ui.input(label="Recipient").classes("w-full md:w-1/2")
        personalisation_area = ui.column().classes("w-full md:w-1/2")
        response_log = ui.code("", language="json").classes("w-full bg-gray-50 dark:bg-slate-900")
        personalisation_controls: Dict[str, Input] = {}

        def render_preview_text(content: str, personalisation: Dict[str, str]) -> str:
            if not content:
                return ""

            def replace(match: re.Match) -> str:
                key = match.group(1).strip()
                value = personalisation.get(key, "")
                return value if value else match.group(0)

            return placeholder_pattern.sub(replace, content)

        def build_personalisation() -> Dict[str, str]:
            return {
                key: control.value or ""
                for key, control in personalisation_controls.items()
            }

        async def load_keys() -> None:
            selected_service = service_select.value
            keys = await list_local_keys(selected_service)
            key_select.set_options({k.id: k.key_name for k in keys})

        async def load_templates() -> None:
            selected_service = service_select.value
            t_type = type_toggle.value
            templates = await list_templates(
                selected_service, t_type, environment=state.environment
            )
            options = {t.id: t.name for t in templates}
            template_select.set_options(options)
            if template_select.value not in options:
                template_select.value = None

        async def handle_template_change() -> None:
            personalisation_area.clear()
            personalisation_controls.clear()
            selected_id = template_select.value
            templates = await list_templates(
                service_select.value, type_toggle.value, environment=state.environment
            )
            tmpl = next((t for t in templates if t.id == selected_id), None)
            if not tmpl:
                return
            placeholders = extract_placeholders(
                (tmpl.subject or "") + " " + (tmpl.content or "")
            )
            with personalisation_area:
                for name in placeholders:
                    personalisation_controls[name] = ui.input(
                        label=name, placeholder=name
                    ).classes("w-full md:w-1/2")
                    personalisation_controls[name].on_value_change(update_preview)
            await update_preview()

        async def update_preview(_=None) -> None:
            selected_id = template_select.value
            if not selected_id:
                preview_subject.text = ""
                preview_body.text = "Select a template to see the preview."
                return
            templates = await list_templates(
                service_select.value, type_toggle.value, environment=state.environment
            )
            tmpl = next((t for t in templates if t.id == selected_id), None)
            if not tmpl:
                preview_subject.text = ""
                preview_body.text = "Select a template to see the preview."
                return
            personalisation = build_personalisation()
            subject = render_preview_text(tmpl.subject or "", personalisation)
            content = render_preview_text(tmpl.content or "", personalisation)
            preview_subject.text = f"Subject: {subject}" if subject else ""
            preview_body.text = content or ""

        async def handle_send() -> None:
            selected_env = env_select.value
            selected_service = service_select.value
            selected_key = key_select.value
            selected_template = template_select.value
            t_type = type_toggle.value
            recipient = recipient_input.value or ""
            if not (
                selected_env and selected_service and selected_key and selected_template
            ):
                ui.notify(
                    "Environment, service, key, and template are required", color="red"
                )
                return
            if not validate_recipient(t_type, recipient):
                ui.notify("Recipient format looks invalid", color="red")
                return

            personalisation = build_personalisation()
            missing_key = find_missing_personalisation(personalisation)
            if missing_key:
                ui.notify(
                    f"Personalisation field '{missing_key}' is empty", color="red"
                )
                return

            try:
                api_key_secret = await resolve_local_key(encryption, selected_key)
                api = await build_api_client(selected_env)
                result = await api.send_notification(
                    template_id=selected_template,
                    recipient=recipient,
                    personalisation=personalisation,
                    api_key=api_key_secret,
                    service_id=selected_service,
                    template_type=t_type,
                )
                response_log.set_content(json.dumps(result, indent=2))
                ui.notify("Notification sent", color="green")
            except Exception as exc:
                ui.notify(f"Error: {exc}", color="red")

        async def handle_service_change(_=None) -> None:
            await load_keys()
            await load_templates()
            await update_preview()

        async def handle_type_change(_=None) -> None:
            await load_templates()
            await update_preview()

        async def handle_template_select(_=None) -> None:
            await handle_template_change()

        async def handle_env_change(e) -> None:
            state.environment = e.value
            await refresh_status_badge(status_badge)
            await refresh_service_options()

        service_select.on_value_change(handle_service_change)
        type_toggle.on_value_change(handle_type_change)
        template_select.on_value_change(handle_template_select)
        env_select.on_value_change(handle_env_change)
        ui.button("Send Notification", on_click=handle_send, color="primary")
        with ui.card().classes("p-6 w-full"):
            ui.label("Preview").classes("text-md font-semibold")
            preview_subject = ui.label("").classes("text-sm font-medium")
            preview_body = ui.label("").classes("text-sm whitespace-pre-wrap")

        await handle_service_change()


@ui.page("/bulk-send")
async def bulk_send_page() -> None:
    placeholder_pattern = re.compile(r"\(\((.*?)\)\)")

    async def refresh_service_options() -> None:
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(state.environment)
        }
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None
        await handle_service_change()

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=refresh_service_options
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    service_options = {
        svc.id: format_service_label(svc)
        for svc in await list_services(state.environment)
    }
    env_options = list(config.api_hosts.keys())

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Bulk Send Notification").classes("text-lg font-semibold")
        env_select = ui.select(
            env_options, value=state.environment, label="Environment"
        ).classes("w-full md:w-1/2")
        service_select = ui.select(
            service_options, label="Service", with_input=True
        ).props("clearable").classes("w-full md:w-1/2")
        key_select = ui.select({}, label="API Key").props("clearable").classes("w-full md:w-1/2")
        type_toggle = ui.toggle({"email": "Email", "sms": "SMS"}, value="email")
        template_select = ui.select({}, label="Template", with_input=True).props(
            "clearable"
        ).classes("w-full md:w-1/2")
        personalisation_area = ui.column().classes("w-full md:w-1/2")
        response_log = ui.code("", language="json").classes("w-full bg-gray-50 dark:bg-slate-900")
        progress_label = ui.label("Bulk send progress: idle").classes("text-sm")
        progress_bar = ui.linear_progress(value=0).classes("w-full")
        personalisation_controls: Dict[str, Input] = {}

        def render_preview_text(content: str, personalisation: Dict[str, str]) -> str:
            if not content:
                return ""

            def replace(match: re.Match) -> str:
                key = match.group(1).strip()
                value = personalisation.get(key, "")
                return value if value else match.group(0)

            return placeholder_pattern.sub(replace, content)

        def build_personalisation() -> Dict[str, str]:
            return {
                key: control.value or ""
                for key, control in personalisation_controls.items()
            }

        async def load_keys() -> None:
            selected_service = service_select.value
            keys = await list_local_keys(selected_service)
            key_select.set_options({k.id: k.key_name for k in keys})

        async def load_templates() -> None:
            selected_service = service_select.value
            t_type = type_toggle.value
            templates = await list_templates(
                selected_service, t_type, environment=state.environment
            )
            options = {t.id: t.name for t in templates}
            template_select.set_options(options)
            if template_select.value not in options:
                template_select.value = None

        async def handle_template_change() -> None:
            personalisation_area.clear()
            personalisation_controls.clear()
            selected_id = template_select.value
            templates = await list_templates(
                service_select.value, type_toggle.value, environment=state.environment
            )
            tmpl = next((t for t in templates if t.id == selected_id), None)
            if not tmpl:
                return
            placeholders = extract_placeholders(
                (tmpl.subject or "") + " " + (tmpl.content or "")
            )
            with personalisation_area:
                for name in placeholders:
                    personalisation_controls[name] = ui.input(
                        label=name, placeholder=name
                    ).classes("w-full md:w-1/2")
                    personalisation_controls[name].on_value_change(update_preview)
            await update_preview()

        async def update_preview(_=None) -> None:
            selected_id = template_select.value
            if not selected_id:
                preview_subject.text = ""
                preview_body.text = "Select a template to see the preview."
                return
            templates = await list_templates(
                service_select.value, type_toggle.value, environment=state.environment
            )
            tmpl = next((t for t in templates if t.id == selected_id), None)
            if not tmpl:
                preview_subject.text = ""
                preview_body.text = "Select a template to see the preview."
                return
            personalisation = build_personalisation()
            subject = render_preview_text(tmpl.subject or "", personalisation)
            content = render_preview_text(tmpl.content or "", personalisation)
            preview_subject.text = f"Subject: {subject}" if subject else ""
            preview_body.text = content or ""

        async def perform_bulk_send() -> None:
            selected_env = env_select.value
            selected_service = service_select.value
            selected_key = key_select.value
            selected_template = template_select.value
            t_type = type_toggle.value
            if not (
                selected_env and selected_service and selected_key and selected_template
            ):
                ui.notify(
                    "Environment, service, key, and template are required", color="red"
                )
                return

            personalisation = build_personalisation()
            missing_key = find_missing_personalisation(personalisation)
            if missing_key:
                ui.notify(
                    f"Personalisation field '{missing_key}' is empty", color="red"
                )
                return

            users = await list_users(selected_env)
            active_users = [
                user for user in users if user.state == "active" and not user.blocked
            ]
            if not active_users:
                ui.notify("No active users found", color="warning")
                return

            total_users = len(active_users)
            completed = 0
            sent_count = 0
            skipped_count = 0
            error_count = 0
            progress_bar.value = 0
            progress_label.text = f"Sending 0/{total_users}"

            try:
                api_key_secret = await resolve_local_key(encryption, selected_key)
                api = await build_api_client(selected_env)
                semaphore = asyncio.Semaphore(config.max_concurrency)

                async def send_for_user(user, index: int):
                    recipient = (
                        user.email_address if t_type == "email" else user.mobile_number
                    )
                    if not recipient:
                        return index, {
                            "user_id": user.id,
                            "recipient": recipient,
                            "status": "skipped",
                            "reason": "missing recipient",
                        }
                    async with semaphore:
                        try:
                            result = await api.send_notification(
                                template_id=selected_template,
                                recipient=recipient,
                                personalisation=personalisation,
                                api_key=api_key_secret,
                                service_id=selected_service,
                                template_type=t_type,
                            )
                            return index, {
                                "user_id": user.id,
                                "recipient": recipient,
                                "status": "sent",
                                "response": result,
                            }
                        except Exception as exc:
                            return index, {
                                "user_id": user.id,
                                "recipient": recipient,
                                "status": "error",
                                "error": str(exc),
                            }

                tasks = [
                    asyncio.create_task(send_for_user(user, idx))
                    for idx, user in enumerate(active_users)
                ]
                results: List[Optional[Dict[str, Any]]] = [None] * len(tasks)
                for task in asyncio.as_completed(tasks):
                    index, result = await task
                    results[index] = result
                    completed += 1
                    status = result.get("status")
                    if status == "sent":
                        sent_count += 1
                    elif status == "skipped":
                        skipped_count += 1
                    elif status == "error":
                        error_count += 1
                    progress_bar.value = completed / total_users
                    progress_label.text = (
                        f"Sending {completed}/{total_users} "
                        f"(sent {sent_count}, skipped {skipped_count}, errors {error_count})"
                    )
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                file_path = os.path.join(
                    "data", f"bulk_send_responses_{timestamp}.json"
                )
                final_results = [r for r in results if r is not None]
                output = {
                    "environment": selected_env,
                    "service_id": selected_service,
                    "template_id": selected_template,
                    "template_type": t_type,
                    "total_users": len(active_users),
                    "results": final_results,
                }
                with open(file_path, "w", encoding="utf-8") as handle:
                    json.dump(output, handle, indent=2)
                response_log.set_content(
                    json.dumps(
                        {
                            "file": file_path,
                            "total": len(active_users),
                            "sent": sent_count,
                            "skipped": skipped_count,
                            "errors": error_count,
                        },
                        indent=2,
                    )
                )
                progress_bar.value = 1
                progress_label.text = (
                    f"Complete: sent {sent_count}, "
                    f"skipped {skipped_count}, errors {error_count}"
                )
                ui.notify("Bulk send complete", color="green")
            except Exception as exc:
                progress_bar.value = 0
                progress_label.text = (
                    f"Bulk send failed after {completed}/{total_users}"
                )
                ui.notify(f"Error: {exc}", color="red")

        async def handle_bulk_send() -> None:
            selected_env = env_select.value
            selected_service = service_select.value
            selected_key = key_select.value
            selected_template = template_select.value
            if not (
                selected_env and selected_service and selected_key and selected_template
            ):
                ui.notify(
                    "Environment, service, key, and template are required", color="red"
                )
                return
            missing_key = find_missing_personalisation(build_personalisation())
            if missing_key:
                ui.notify(
                    f"Personalisation field '{missing_key}' is empty", color="red"
                )
                return
            confirm_message.text = (
                "You are about to send to ALL active users of the platform "
                f"({selected_env})."
            )
            confirm_dialog.open()

        async def handle_confirm_send() -> None:
            confirm_dialog.close()
            await perform_bulk_send()

        async def handle_service_change(_=None) -> None:
            await load_keys()
            await load_templates()
            await update_preview()

        async def handle_type_change(_=None) -> None:
            await load_templates()
            await update_preview()

        async def handle_template_select(_=None) -> None:
            await handle_template_change()

        async def handle_env_change(e) -> None:
            state.environment = e.value
            await refresh_status_badge(status_badge)
            await refresh_service_options()

        service_select.on_value_change(handle_service_change)
        type_toggle.on_value_change(handle_type_change)
        template_select.on_value_change(handle_template_select)
        env_select.on_value_change(handle_env_change)
        ui.button("Bulk Send Notification", on_click=handle_bulk_send, color="primary")
        with ui.dialog() as confirm_dialog, ui.card():
            ui.label("Confirm Bulk Send").classes("text-md font-semibold")
            confirm_message = ui.label("")
            with ui.row().classes("gap-2"):
                ui.button("Send to all", on_click=handle_confirm_send, color="primary")
                ui.button("Cancel", on_click=confirm_dialog.close, color="gray")
        with ui.card().classes("p-6 w-full"):
            ui.label("Preview").classes("text-md font-semibold")
            preview_subject = ui.label("").classes("text-sm font-medium")
            preview_body = ui.label("").classes("text-sm whitespace-pre-wrap")

        await handle_service_change()


@ui.page("/settings")
async def settings_page() -> None:
    async def refresh_service_options() -> None:
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
        }
        key_service.set_options(options)
        if key_service.value not in options:
            key_service.value = None

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=refresh_service_options
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Settings").classes("text-lg font-semibold")

        with ui.card().classes("p-6 w-full"):
            ui.label("API Configuration").classes("text-md font-semibold")
            rows = []
            for env in config.api_hosts:
                current_url = await get_setting(
                    f"base_url_{env}"
                ) or config.api_hosts.get(env)
                rows.append((env, current_url))
            inputs: Dict[str, ui.input] = {}
            for env, url in rows:
                inputs[env] = ui.input(
                    label=f"{env.title()} Base URL", value=url
                ).classes("w-full md:w-1/2")

            async def handle_save_urls() -> None:
                await save_base_urls(inputs)

            ui.button(
                "Save Base URLs",
                on_click=handle_save_urls,
            )

        with ui.card().classes("p-6 w-full"):
            ui.label("Global Admin Auth (per environment)").classes(
                "text-md font-semibold"
            )
            auth_inputs: Dict[str, Dict[str, ui.input]] = {}
            for env in config.api_hosts:
                user_val = (
                    await get_secure_setting(f"basic_username_{env}", encryption) or ""
                )
                pass_val = (
                    await get_secure_setting(f"basic_password_{env}", encryption) or ""
                )
                auth_inputs[env] = {
                    "user": ui.input(
                        label=f"{env.title()} Username", value=user_val
                    ).classes("w-full md:w-1/2"),
                    "pass": ui.input(
                        label=f"{env.title()} Password", value=pass_val, password=True
                    ).classes("w-full md:w-1/2"),
                }

            async def handle_save_auth() -> None:
                await save_admin_auth(auth_inputs)

            ui.button(
                "Save Admin Auth",
                on_click=handle_save_auth,
            )

        with ui.card().classes("p-6 w-full"):
            ui.label("Local API Keys").classes("text-md font-semibold")
            service_options = {
                svc.id: format_service_label(svc)
                for svc in await list_services(get_view_environment())
            }
            key_service = ui.select(
                service_options, label="Service", with_input=True
            ).classes("w-full md:w-1/2")
            key_name = ui.input(label="Key Name").classes("w-full md:w-1/2")
            key_secret = ui.input(label="Key Secret", password=True).classes("w-full md:w-1/2")
            key_type = ui.select(
                {"normal": "Normal", "team": "Team", "test": "Test"}, value="normal"
            ).classes("w-full md:w-1/2")

            async def handle_add_key() -> None:
                await save_local_key(
                    key_service.value, key_name.value, key_secret.value, key_type.value
                )

            ui.button(
                "Add Key",
                on_click=handle_add_key,
            )
            ui.label("Stored Keys")
            await render_local_keys()


async def save_base_urls(inputs: Dict[str, ui.input]) -> None:
    for env, control in inputs.items():
        if control.value:
            await set_setting(f"base_url_{env}", control.value)
    ui.notify("Base URLs saved", color="green")


async def save_admin_auth(auth_inputs: Dict[str, Dict[str, ui.input]]) -> None:
    for env, pair in auth_inputs.items():
        user = pair["user"].value or ""
        password = pair["pass"].value or ""
        if user and password:
            await set_secure_setting(f"basic_username_{env}", user, encryption)
            await set_secure_setting(f"basic_password_{env}", password, encryption)
    ui.notify("Admin credentials saved", color="green")


async def save_local_key(
    service_id: Optional[str], name: str, secret: str, key_type: str
) -> None:
    if not (service_id and name and secret):
        ui.notify("Service, name, and secret are required", color="red")
        return
    await add_local_key(encryption, service_id, name, secret, key_type)
    ui.notify("Key saved", color="green")
    await refresh_if_needed(render_local_keys)


@ui.refreshable
async def render_local_keys() -> None:
    keys = await list_local_keys()
    rows: List[Dict[str, Any]] = [
        {
            "id": k.id,
            "service_id": k.service_id,
            "key_name": k.key_name,
            "key_type": k.key_type,
        }
        for k in keys
    ]
    table = ui.table(
        columns=make_sortable(
            [
            {"name": "id", "label": "ID", "field": "id"},
            {"name": "service_id", "label": "Service", "field": "service_id"},
            {"name": "key_name", "label": "Name", "field": "key_name"},
            {"name": "key_type", "label": "Type", "field": "key_type"},
            ]
        ),
        rows=rows,
        pagination={"rowsPerPage": 5},
    )
    table.props("row-key=id").classes("w-full")
    add_copyable_slots(table, rows)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="VA Notify Admin", port=8080, reload=True, storage_secret=config.master_key)
