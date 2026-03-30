from __future__ import annotations

from typing import Any, Dict, List, Optional

from nicegui import ui

from app.repository import (
    CLEARABLE_TABLES,
    add_local_key,
    clear_table_data,
    get_secure_setting,
    get_setting,
    list_local_keys,
    list_services,
    set_secure_setting,
    set_setting,
)
from app.ui import state as _st
from app.ui.helpers import (
    add_copyable_slots,
    add_export_button,
    format_service_label,
    make_row_key,
    make_sortable,
    refresh_if_needed,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import PAGE_RESPONSE_TIMEOUT, get_view_environment, refresh_status_badge
from app.ui.sync_handlers import handle_full_sync


@ui.page("/settings", response_timeout=PAGE_RESPONSE_TIMEOUT)
async def settings_page() -> None:
    key_environment = None
    key_service = None

    async def refresh_service_options() -> None:  # pragma: no cover
        env_value = key_environment.value if key_environment else get_view_environment()
        options = {svc.id: format_service_label(svc) for svc in await list_services(env_value)}
        if not key_service:
            return
        key_service.set_options(options)
        if key_service.value not in options:
            key_service.value = None

    status_badge, sync_label, refresh_button, dark_mode, theme_button = build_shell(
        on_view_env_change=refresh_service_options
    )
    await ensure_theme_preference(dark_mode, theme_button)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Settings").classes("text-lg font-semibold")

        with ui.card().classes("p-6 w-full"):
            ui.label("API Configuration").classes("text-md font-semibold")
            rows = []
            for env in _st.config.api_hosts:
                current_url = await get_setting(f"base_url_{env}") or _st.config.api_hosts.get(env)
                rows.append((env, current_url))
            inputs: Dict[str, ui.input] = {}
            for env, url in rows:
                inputs[env] = (
                    ui.input(label=f"{env.title()} Base URL", value=url).props("clearable").classes("w-full md:w-1/2")
                )

            async def handle_save_urls() -> None:  # pragma: no cover
                await save_base_urls(inputs)

            ui.button(
                "Save Base URLs",
                on_click=handle_save_urls,
            )

        with ui.card().classes("p-6 w-full"):
            ui.label("Global Admin Auth (per environment)").classes("text-md font-semibold")
            auth_inputs: Dict[str, Dict[str, ui.input]] = {}
            for env in _st.config.api_hosts:
                user_val = await get_secure_setting(f"basic_username_{env}", _st.encryption) or ""
                pass_val = await get_secure_setting(f"basic_password_{env}", _st.encryption) or ""
                auth_inputs[env] = {
                    "user": ui.input(label=f"{env.title()} Username", value=user_val)
                    .props("clearable")
                    .classes("w-full md:w-1/2"),
                    "pass": ui.input(label=f"{env.title()} Password", value=pass_val, password=True)
                    .props("clearable")
                    .classes("w-full md:w-1/2"),
                }

            async def handle_save_auth() -> None:  # pragma: no cover
                await save_admin_auth(auth_inputs)

            ui.button(
                "Save Admin Auth",
                on_click=handle_save_auth,
            )

        with ui.card().classes("p-6 w-full"):
            ui.label("Local API Keys").classes("text-md font-semibold")
            env_options = {env: env.title() for env in _st.config.api_hosts}
            key_environment = ui.select(env_options, value=_st.state.environment, label="Environment").classes(
                "w-full md:w-1/2"
            )
            service_options = {svc.id: format_service_label(svc) for svc in await list_services(key_environment.value)}
            key_service = ui.select(service_options, label="Service", with_input=True).classes("w-full md:w-1/2")
            key_name = ui.input(label="Key Name").props("clearable").classes("w-full md:w-1/2")
            key_secret = ui.input(label="Key Secret", password=True).props("clearable").classes("w-full md:w-1/2")
            key_type = ui.select({"normal": "Normal", "team": "Team", "test": "Test"}, value="normal").classes(
                "w-full md:w-1/2"
            )

            async def handle_add_key() -> None:  # pragma: no cover
                await save_local_key(
                    key_environment.value,
                    key_service.value,
                    key_name.value,
                    key_secret.value,
                    key_type.value,
                )

            async def handle_key_environment_change(_=None) -> None:  # pragma: no cover
                await refresh_service_options()

            ui.button(
                "Add Key",
                on_click=handle_add_key,
            )
            key_environment.on_value_change(handle_key_environment_change)
            ui.label("Stored Keys")
            await render_local_keys()

        with ui.card().classes("p-6 w-full"):
            ui.label("Clear Cached Data").classes("text-md font-semibold")
            ui.label("Remove synced data from the local database by table and environment.").classes(
                "text-sm text-gray-600"
            )

            table_options = {name: name.replace("_", " ").title() for name in CLEARABLE_TABLES}
            clear_table_select = (
                ui.select(table_options, label="Table", value=None).props("clearable").classes("w-full md:w-1/3")
            )

            env_options_with_all = {"": "All Environments"}
            env_options_with_all.update({env: env.title() for env in _st.config.api_hosts})
            clear_env_select = ui.select(env_options_with_all, label="Environment", value="").classes("w-full md:w-1/3")

            with ui.dialog() as confirm_clear_dialog, ui.card().classes("p-6"):
                ui.label("Confirm Data Deletion").classes("text-md font-semibold")
                confirm_clear_message = ui.label("")
                with ui.row().classes("gap-2"):
                    confirm_clear_button = ui.button("Delete Data", color="negative")
                    ui.button("Cancel", on_click=confirm_clear_dialog.close, color="gray")

            pending_clear: Dict[str, Any] = {}

            async def handle_clear_request() -> None:  # pragma: no cover
                table_name = clear_table_select.value
                if not table_name:
                    ui.notify("Select a table to clear", color="red")
                    return
                environment = clear_env_select.value or None
                env_label = environment.title() if environment else "all environments"
                table_label = table_name.replace("_", " ")
                confirm_clear_message.text = (
                    f"Are you sure you want to delete all {table_label} data from {env_label}? This cannot be undone."
                )
                pending_clear.clear()
                pending_clear["table"] = table_name
                pending_clear["environment"] = environment
                confirm_clear_dialog.open()

            async def handle_confirm_clear() -> None:  # pragma: no cover
                confirm_clear_dialog.close()
                if not pending_clear:
                    return
                table_name = pending_clear.get("table")
                environment = pending_clear.get("environment")
                pending_clear.clear()
                if not table_name:
                    return
                try:
                    deleted = await clear_table_data(table_name, environment)
                    env_label = environment.title() if environment else "all environments"
                    ui.notify(
                        f"Deleted {deleted} rows from {table_name} ({env_label})",
                        color="green",
                    )
                    if table_name == "local_api_keys":
                        await refresh_if_needed(render_local_keys)
                except ValueError as exc:
                    ui.notify(str(exc), color="red")

            ui.button("Clear Data", on_click=handle_clear_request, color="negative")
            confirm_clear_button.on_click(handle_confirm_clear)


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
            await set_secure_setting(f"basic_username_{env}", user, _st.encryption)
            await set_secure_setting(f"basic_password_{env}", password, _st.encryption)
    ui.notify("Admin credentials saved", color="green")


async def save_local_key(
    environment: Optional[str],
    service_id: Optional[str],
    name: str,
    secret: str,
    key_type: str,
) -> None:
    if not (environment and service_id and name and secret):
        ui.notify("Environment, service, name, and secret are required", color="red")
        return
    await add_local_key(_st.encryption, service_id, environment, name, secret, key_type)
    ui.notify("Key saved", color="green")
    await refresh_if_needed(render_local_keys)


@ui.refreshable
async def render_local_keys() -> None:
    keys = await list_local_keys()
    columns = [
        {"name": "id", "label": "ID", "field": "id"},
        {"name": "service_id", "label": "Service", "field": "service_id"},
        {"name": "environment", "label": "Environment", "field": "environment"},
        {"name": "key_name", "label": "Name", "field": "key_name"},
        {"name": "key_type", "label": "Type", "field": "key_type"},
    ]
    rows: List[Dict[str, Any]] = [
        {
            "_row_key": make_row_key(k.id, k.environment),
            "id": k.id,
            "service_id": k.service_id,
            "environment": k.environment,
            "key_name": k.key_name,
            "key_type": k.key_type,
        }
        for k in keys
    ]
    with ui.row().classes("w-full justify-end"):
        add_export_button(rows, columns, "local_api_keys.csv")
    table = ui.table(
        columns=make_sortable(columns),
        rows=rows,
        pagination={"rowsPerPage": 5},
    )
    table.props("row-key=_row_key").classes("w-full")
    add_copyable_slots(table, rows)
