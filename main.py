from __future__ import annotations

import inspect
import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

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
    list_services,
    list_templates,
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

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Starting sync..."
    await manager.sync_all(progress=progress)
    sync_label.text = "Sync complete"
    await refresh_tables()
    await refresh_status_badge(status_badge)


async def handle_services_sync(status_badge, sync_label) -> None:
    if not await ensure_sync_enabled(sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Syncing services..."
    await manager.sync_services(progress=progress)
    sync_label.text = "Sync complete"
    await refresh_if_needed(services_table)
    await refresh_status_badge(status_badge)


async def handle_templates_sync(status_badge, sync_label) -> None:
    if not await ensure_sync_enabled(sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Syncing services..."
    await manager.sync_services(progress=progress)
    sync_label.text = "Syncing templates..."
    await manager.sync_templates(progress=progress)
    sync_label.text = "Sync complete"
    await refresh_status_badge(status_badge)


async def handle_api_keys_sync(status_badge, sync_label) -> None:
    if not await ensure_sync_enabled(sync_label):
        return

    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency, environment=state.environment)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Syncing services..."
    await manager.sync_services(progress=progress)
    sync_label.text = "Syncing API keys..."
    await manager.sync_api_keys(progress=progress)
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
        ui.link("Services", "/services")
        ui.link("Templates", "/templates")
        ui.link("API Keys", "/api-keys")
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


def format_environment(value: Optional[str]) -> str:
    return value or "unknown"


def format_service_label(service) -> str:
    return f"{service.name} ({format_environment(service.environment)})"


def get_view_environment() -> Optional[str]:
    return None if state.view_environment in {"all", None, ""} else state.view_environment


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
        ui.button("Sync Services", on_click=page_sync_services)
        await services_table()


@ui.refreshable
async def services_table() -> None:
    rows = await list_services(get_view_environment())
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
    ui.table(
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
    ).props("row-key=id").classes("w-full")


@ui.page("/templates")
async def templates_page() -> None:
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
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": row.id,
                    "environment": format_environment(row.environment),
                    "service_id": row.service_id,
                    "name": row.name,
                    "template_type": row.template_type,
                    "version": row.version,
                    "archived": row.archived,
                    "hidden": row.hidden,
                    "updated_at": row.updated_at[:10]
                    if row.updated_at
                    else None,  # Show date only
                    "subject": row.subject,
                    "content": row.content[:50] + "..."
                    if row.content and len(row.content) > 50
                    else row.content,
                }
                for row in rows
            ]
            ui.table(
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
            ).props("row-key=id").classes("w-full")

        service_select.on_value_change(lambda _: render_table.refresh())
        type_select.on_value_change(lambda _: render_table.refresh())
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
            ui.table(
                columns=make_sortable(
                    [
                    {"name": "id", "label": "ID", "field": "id"},
                    {"name": "environment", "label": "Environment", "field": "environment"},
                    {
                        "name": "service_id",
                        "label": "Service ID",
                        "field": "service_id",
                    },
                    {"name": "name", "label": "Name", "field": "name"},
                    {"name": "key_type", "label": "Type", "field": "key_type"},
                    {"name": "expiry_date", "label": "Expires", "field": "expiry_date"},
                    {
                        "name": "created_by",
                        "label": "Created By",
                        "field": "created_by",
                    },
                    {"name": "created_at", "label": "Created", "field": "created_at"},
                    {"name": "revoked", "label": "Revoked", "field": "revoked"},
                    {"name": "version", "label": "Version", "field": "version"},
                    ]
                ),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            ).props("row-key=id").classes("w-full")

        service_select.on_value_change(lambda _: render_table.refresh())
        expires_from.on_value_change(lambda _: render_table.refresh())
        expires_to.on_value_change(lambda _: render_table.refresh())
        ui.button("Sync API Keys", on_click=handle_sync_keys)
        await render_table()


@ui.page("/send")
async def send_page() -> None:
    async def refresh_service_options() -> None:
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
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
        for svc in await list_services(get_view_environment())
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

        async def load_keys() -> None:
            selected_service = service_select.value
            keys = await list_local_keys(selected_service)
            key_select.set_options({k.id: k.key_name for k in keys})

        async def load_templates() -> None:
            selected_service = service_select.value
            t_type = type_toggle.value
            templates = await list_templates(
                selected_service, t_type, environment=get_view_environment()
            )
            template_select.set_options({t.id: t.name for t in templates})

        async def handle_template_change() -> None:
            personalisation_area.clear()
            personalisation_controls.clear()
            selected_id = template_select.value
            templates = await list_templates(
                service_select.value, type_toggle.value, environment=get_view_environment()
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

            personalisation: Dict[str, Any] = {}
            for key, control in personalisation_controls.items():
                personalisation[key] = control.value or ""
            for key, val in personalisation.items():
                if val == "":
                    ui.notify(f"Personalisation field '{key}' is empty", color="red")
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

        async def handle_type_change(_=None) -> None:
            await load_templates()

        async def handle_template_select(_=None) -> None:
            await handle_template_change()

        async def handle_env_change(e) -> None:
            state.environment = e.value
            await refresh_status_badge(status_badge)

        service_select.on_value_change(handle_service_change)
        type_toggle.on_value_change(handle_type_change)
        template_select.on_value_change(handle_template_select)
        env_select.on_value_change(handle_env_change)
        ui.button("Send Notification", on_click=handle_send, color="primary")

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
    ui.table(
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
    ).props("row-key=id").classes("w-full")


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="VA Notify Admin", port=8080, reload=True, storage_secret=config.master_key)
