from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from nicegui import app, ui
from nicegui.elements.input import Input

from app.api_client import HttpNotificationAPI, MockNotificationAPI, NotificationAPI
from app.config import AppConfig, load_config
from app.crypto import EncryptionManager
from app.db import create_all, init_engine
from app.repository import (
    add_local_key,
    get_secure_setting,
    get_setting,
    list_local_keys,
    list_services,
    list_templates,
    list_users,
    resolve_local_key,
    set_secure_setting,
    set_setting,
)
from app.sync import SyncManager
from app.utils import extract_placeholders, validate_recipient


@dataclass
class AppState:
    environment: str
    api_status: str = "unknown"
    sync_message: str = ""


config: AppConfig = load_config()
encryption = EncryptionManager(config.master_key)
state = AppState(environment=next(iter(config.api_hosts.keys()), "development"))

# Only initialize database if not in test mode
# Tests will call init_engine with their own temporary database
if not os.getenv("PYTEST_CURRENT_TEST"):
    init_engine(config.database_path)



@app.on_startup
async def startup() -> None:
    await create_all()
    await ensure_default_hosts()


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
    return HttpNotificationAPI(base_url=base_url, basic_username=basic_user, basic_password=basic_pass)


async def refresh_status_badge(badge) -> None:
    api = await build_api_client(state.environment)
    ok = await api.healthcheck()
    state.api_status = "online" if ok else "offline"
    badge.text = f"API Status: {state.api_status.title()}"
    badge.props(f"color={'green' if ok else 'red'}")


async def handle_full_sync(status_badge, sync_label) -> None:
    api = await build_api_client(state.environment)
    manager = SyncManager(api, config.max_concurrency)

    async def progress(msg: str):
        state.sync_message = msg
        sync_label.text = msg

    sync_label.text = "Starting sync..."
    await manager.sync_all(progress=progress)
    sync_label.text = "Sync complete"
    await refresh_tables()
    await refresh_status_badge(status_badge)


async def refresh_tables() -> None:
    await services_table.refresh()
    await users_table.refresh()


def build_shell() -> tuple:
    drawer = ui.left_drawer(value=True).props("show-if-above bordered").classes("bg-slate-50")
    with drawer:
        ui.link("Dashboard", "/")
        ui.link("Send Notification", "/send")
        ui.link("Services", "/services")
        ui.link("Users", "/users")
        ui.link("Templates", "/templates")
        ui.link("Settings", "/settings")

    with ui.header().classes("items-center justify-between bg-gray-100"):
        with ui.row().classes("items-center gap-3"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense")
            ui.label("Notification Admin Dashboard").classes("text-xl font-medium")
        with ui.row().classes("items-center gap-4"):
            status_badge = ui.badge("API Status: Unknown", color="gray")
            sync_label = ui.label("")
            refresh_button = ui.button("Refresh All Data")
    return status_badge, sync_label, refresh_button


# Pages
@ui.page("/")
async def dashboard_page() -> None:
    status_badge, sync_label, refresh_button = build_shell()

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    services = await list_services()
    users = await list_users()
    templates = await list_templates()
    with ui.column().classes("p-4 gap-4"):
        ui.label("Dashboard").classes("text-lg font-semibold")
        with ui.row().classes("gap-4"):
            metric_card("Services", len(services))
            metric_card("Users", len(users))
            metric_card("Templates", len(templates))
        ui.markdown(
            "This dashboard caches services, templates, users, and local API keys. Use the left navigation to manage data and send notifications."
        )


def metric_card(title: str, value: int) -> None:
    with ui.card().classes("flex-1 min-w-[240px]"):
        ui.label(title).classes("text-sm text-gray-600")
        ui.label(str(value)).classes("text-3xl font-bold")


@ui.page("/services")
async def services_page() -> None:
    status_badge, sync_label, refresh_button = build_shell()

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-4 gap-4"):
        ui.label("Services").classes("text-lg font-semibold")
        ui.button("Sync Services", on_click=page_refresh)
        await services_table()


@ui.refreshable
async def services_table() -> None:
    rows = await list_services()
    table_rows: List[Dict[str, Any]] = [
        {
            "id": row.id,
            "name": row.name,
            "active": row.active,
            "restricted": row.restricted,
            "message_limit": row.message_limit,
            "rate_limit": row.rate_limit,
            "research_mode": row.research_mode,
            "count_as_live": row.count_as_live,
            "permissions": row.permissions[:50] + "..." if row.permissions and len(row.permissions) > 50 else row.permissions,
        }
        for row in rows
    ]
    ui.table(
        columns=[
            {"name": "id", "label": "ID", "field": "id"},
            {"name": "name", "label": "Name", "field": "name"},
            {"name": "active", "label": "Active", "field": "active"},
            {"name": "restricted", "label": "Restricted", "field": "restricted"},
            {"name": "message_limit", "label": "Msg Limit", "field": "message_limit"},
            {"name": "rate_limit", "label": "Rate Limit", "field": "rate_limit"},
            {"name": "research_mode", "label": "Research", "field": "research_mode"},
            {"name": "count_as_live", "label": "Live", "field": "count_as_live"},
            {"name": "permissions", "label": "Permissions", "field": "permissions"},
        ],
        rows=table_rows,
        pagination={"rowsPerPage": 10},
    ).props("row-key=id")


@ui.page("/users")
async def users_page() -> None:
    status_badge, sync_label, refresh_button = build_shell()

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-4 gap-4"):
        ui.label("Users").classes("text-lg font-semibold")
        ui.button("Sync Users", on_click=page_refresh)
        await users_table()


@ui.refreshable
async def users_table() -> None:
    rows = await list_users()
    table_rows: List[Dict[str, Any]] = [
        {
            "id": row.id,
            "name": row.name,
            "email_address": row.email_address,
            "state": row.state,
            "platform_admin": row.platform_admin,
            "blocked": row.blocked,
        }
        for row in rows
    ]
    ui.table(
        columns=[
            {"name": "id", "label": "ID", "field": "id"},
            {"name": "name", "label": "Name", "field": "name"},
            {"name": "email_address", "label": "Email", "field": "email_address"},
            {"name": "state", "label": "State", "field": "state"},
            {"name": "platform_admin", "label": "Admin", "field": "platform_admin"},
            {"name": "blocked", "label": "Blocked", "field": "blocked"},
        ],
        rows=table_rows,
        pagination={"rowsPerPage": 10},
    ).props("row-key=id")


@ui.page("/templates")
async def templates_page() -> None:
    status_badge, sync_label, refresh_button = build_shell()

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-4 gap-4"):
        ui.label("Templates").classes("text-lg font-semibold")
        filter_row = ui.row().classes("gap-2")
        service_options = {svc.id: svc.name for svc in await list_services()}
        type_options = {"email": "Email", "sms": "SMS", "letter": "Letter"}
        service_select = ui.select(service_options, label="Service", with_input=True).props("clearable")
        type_select = ui.select(type_options, label="Type", with_input=True).props("clearable")

        async def handle_sync_templates() -> None:
            await page_refresh()
            await render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            selected_service = service_select.value
            selected_type = type_select.value
            rows = await list_templates(selected_service, selected_type)
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": row.id,
                    "service_id": row.service_id,
                    "name": row.name,
                    "template_type": row.template_type,
                    "version": row.version,
                    "archived": row.archived,
                    "hidden": row.hidden,
                    "updated_at": row.updated_at[:10] if row.updated_at else None,  # Show date only
                    "subject": row.subject,
                    "content": row.content[:50] + "..." if row.content and len(row.content) > 50 else row.content,
                }
                for row in rows
            ]
            ui.table(
                columns=[
                    {"name": "id", "label": "ID", "field": "id"},
                    {"name": "service_id", "label": "Service", "field": "service_id"},
                    {"name": "name", "label": "Name", "field": "name"},
                    {"name": "template_type", "label": "Type", "field": "template_type"},
                    {"name": "version", "label": "Version", "field": "version"},
                    {"name": "archived", "label": "Archived", "field": "archived"},
                    {"name": "hidden", "label": "Hidden", "field": "hidden"},
                    {"name": "updated_at", "label": "Updated", "field": "updated_at"},
                    {"name": "subject", "label": "Subject", "field": "subject"},
                    {"name": "content", "label": "Content", "field": "content"},
                ],
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            ).props("row-key=id")

        service_select.on_value_change(lambda _: render_table.refresh())
        type_select.on_value_change(lambda _: render_table.refresh())
        ui.button("Sync Templates", on_click=handle_sync_templates)
        await render_table()


@ui.page("/send")
async def send_page() -> None:
    status_badge, sync_label, refresh_button = build_shell()

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    services = await list_services()
    service_options = {svc.id: svc.name for svc in services}
    env_options = list(config.api_hosts.keys())

    with ui.column().classes("p-4 gap-4"):
        ui.label("Send Notification").classes("text-lg font-semibold")
        env_select = ui.select(env_options, value=state.environment, label="Environment")
        service_select = ui.select(service_options, label="Service", with_input=True).props("clearable")
        key_select = ui.select({}, label="Authentication Source").props("clearable")
        type_toggle = ui.toggle({"email": "Email", "sms": "SMS"}, value="email")
        template_select = ui.select({}, label="Template", with_input=True).props("clearable")
        recipient_input = ui.input(label="Recipient")
        personalisation_area = ui.column()
        response_log = ui.code("", language="json").classes("w-full bg-gray-50")

        async def load_keys() -> None:
            selected_service = service_select.value
            keys = await list_local_keys(selected_service)
            key_select.set_options({k.id: k.key_name for k in keys})

        async def load_templates() -> None:
            selected_service = service_select.value
            t_type = type_toggle.value
            templates = await list_templates(selected_service, t_type)
            template_select.set_options({t.id: t.name for t in templates})

        async def handle_template_change() -> None:
            personalisation_area.clear()
            selected_id = template_select.value
            templates = await list_templates(service_select.value, type_toggle.value)
            tmpl = next((t for t in templates if t.id == selected_id), None)
            if not tmpl:
                return
            placeholders = extract_placeholders((tmpl.subject or "") + " " + (tmpl.content or ""))
            for name in placeholders:
                ui.input(label=name, placeholder=name)

        async def handle_send() -> None:
            selected_env = env_select.value
            selected_service = service_select.value
            selected_key = key_select.value
            selected_template = template_select.value
            t_type = type_toggle.value
            recipient = recipient_input.value or ""
            if not (selected_env and selected_service and selected_key and selected_template):
                ui.notify("Environment, service, key, and template are required", color="red")
                return
            if not validate_recipient(t_type, recipient):
                ui.notify("Recipient format looks invalid", color="red")
                return

            personalisation_inputs = [c for c in personalisation_area.children if isinstance(c, Input)]
            personalisation: Dict[str, Any] = {}
            for control in personalisation_inputs:
                personalisation[control.label] = control.value or ""
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
    status_badge, sync_label, refresh_button = build_shell()

    async def page_refresh():
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    env_options = list(config.api_hosts.keys())
    with ui.column().classes("p-4 gap-6"):
        ui.label("Settings").classes("text-lg font-semibold")

        with ui.card().classes("p-4 w-full"):
            ui.label("API Configuration").classes("text-md font-semibold")
            rows = []
            for env in env_options:
                current_url = await get_setting(f"base_url_{env}") or config.api_hosts.get(env)
                rows.append((env, current_url))
            inputs: Dict[str, ui.input] = {}
            for env, url in rows:
                inputs[env] = ui.input(label=f"{env.title()} Base URL", value=url)
            async def handle_save_urls() -> None:
                await save_base_urls(inputs)

            ui.button(
                "Save Base URLs",
                on_click=handle_save_urls,
            )

        with ui.card().classes("p-4 w-full"):
            ui.label("Global Admin Auth (per environment)").classes("text-md font-semibold")
            auth_inputs: Dict[str, Dict[str, ui.input]] = {}
            for env in env_options:
                user_val = await get_secure_setting(f"basic_username_{env}", encryption) or ""
                pass_val = await get_secure_setting(f"basic_password_{env}", encryption) or ""
                auth_inputs[env] = {
                    "user": ui.input(label=f"{env.title()} Username", value=user_val),
                    "pass": ui.input(label=f"{env.title()} Password", value=pass_val, password=True),
                }
            async def handle_save_auth() -> None:
                await save_admin_auth(auth_inputs)

            ui.button(
                "Save Admin Auth",
                on_click=handle_save_auth,
            )

        with ui.card().classes("p-4 w-full"):
            ui.label("Local API Keys").classes("text-md font-semibold")
            services = await list_services()
            service_options = {svc.id: svc.name for svc in services}
            key_service = ui.select(service_options, label="Service", with_input=True)
            key_name = ui.input(label="Key Name")
            key_secret = ui.input(label="Key Secret", password=True)
            key_type = ui.select({"normal": "Normal", "team": "Team", "test": "Test"}, value="normal")
            async def handle_add_key() -> None:
                await save_local_key(key_service.value, key_name.value, key_secret.value, key_type.value)

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


async def save_local_key(service_id: Optional[str], name: str, secret: str, key_type: str) -> None:
    if not (service_id and name and secret):
        ui.notify("Service, name, and secret are required", color="red")
        return
    await add_local_key(encryption, service_id, name, secret, key_type)
    ui.notify("Key saved", color="green")
    await render_local_keys.refresh()


@ui.refreshable
async def render_local_keys() -> None:
    keys = await list_local_keys()
    rows: List[Dict[str, Any]] = [
        {"id": k.id, "service_id": k.service_id, "key_name": k.key_name, "key_type": k.key_type}
        for k in keys
    ]
    ui.table(
        columns=[
            {"name": "id", "label": "ID", "field": "id"},
            {"name": "service_id", "label": "Service", "field": "service_id"},
            {"name": "key_name", "label": "Name", "field": "key_name"},
            {"name": "key_type", "label": "Type", "field": "key_type"},
        ],
        rows=rows,
        pagination={"rowsPerPage": 5},
    ).props("row-key=id")


if __name__ == "__main__":
    ui.run(title="VA Notify Admin", port=8080, reload=False)
