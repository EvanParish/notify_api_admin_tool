from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from nicegui import ui

from app.repository import count_active_api_keys_by_service, list_services, update_service
from app.ui import state as _st
from app.ui.helpers import (
    add_copyable_slots,
    add_export_button,
    add_service_context_menu,
    format_environment,
    make_row_key,
    make_sortable,
    refresh_if_needed,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import (
    PAGE_RESPONSE_TIMEOUT,
    build_api_client,
    ensure_admin_auth,
    get_view_environment,
    handle_unauthorized,
    refresh_status_badge,
)
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


async def handle_service_search(value: Optional[str]) -> None:
    _st.service_search_query = (value or "").strip().lower()
    await refresh_if_needed(services_table)


async def handle_service_search_event(e) -> None:
    await handle_service_search(getattr(e, "value", None))


@ui.page("/services", response_timeout=PAGE_RESPONSE_TIMEOUT)
async def services_page() -> None:
    status_badge, sync_label, refresh_button, dark_mode, theme_button = build_shell(
        on_view_env_change=lambda: refresh_if_needed(services_table)
    )
    await ensure_theme_preference(dark_mode, theme_button)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    async def page_sync_services():  # pragma: no cover
        if await handle_entity_sync(["sync_services"], status_badge, sync_label, "services"):
            await refresh_if_needed(services_table)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    selected_service: dict[str, Any] = {}

    with ui.dialog() as edit_dialog, ui.card().classes("p-6 w-full max-w-lg"):
        ui.label("Edit Service Limits").classes("text-md font-semibold")
        selected_service_label = ui.label("")
        edit_message_limit = ui.number(label="Message Limit", min=0, precision=0).classes("w-full")
        edit_rate_limit = ui.number(label="Rate Limit", min=0, precision=0).classes("w-full")
        with ui.row().classes("gap-2"):
            edit_update_button = ui.button("Update Limits", color="primary")
            ui.button("Close", on_click=edit_dialog.close, color="gray")

    def update_edit_fields(svc: dict[str, Any] | None) -> None:  # pragma: no cover
        if not svc:
            selected_service_label.text = "No service selected."
            edit_message_limit.value = None
            edit_rate_limit.value = None
            return
        name = svc.get("name") or svc.get("id")
        selected_service_label.text = f"Selected: {name} ({svc.get('id')})"
        edit_message_limit.value = svc.get("message_limit")
        edit_rate_limit.value = svc.get("rate_limit")

    async def handle_open_edit_dialog() -> None:  # pragma: no cover
        svc = selected_service if selected_service.get("id") else None
        if not svc:
            ui.notify("Select a service from the table first", color="red")
            return
        update_edit_fields(svc)
        edit_dialog.open()

    async def handle_update_service() -> None:  # pragma: no cover
        svc = selected_service if selected_service.get("id") else None
        if not svc:
            ui.notify("Select a service first", color="red")
            return
        service_id = svc.get("id")
        environment = svc.get("environment_value")
        if not (service_id and environment):
            ui.notify("Selected service is missing required details", color="red")
            return
        message_limit = int(edit_message_limit.value) if edit_message_limit.value is not None else None
        rate_limit = int(edit_rate_limit.value) if edit_rate_limit.value is not None else None
        if not await ensure_admin_auth(environment, sync_label):
            return
        api = await build_api_client(environment)
        try:
            await api.update_service(
                service_id=service_id,
                message_limit=message_limit,
                rate_limit=rate_limit,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response and exc.response.status_code == 401:
                handle_unauthorized(sync_label, environment)
                return
            ui.notify(f"Failed to update service: {exc}", color="red")
            return
        except Exception as exc:
            ui.notify(f"Error updating service: {exc}", color="red")
            return
        updated = await update_service(
            service_id=service_id,
            message_limit=message_limit,
            rate_limit=rate_limit,
            environment=environment,
        )
        if updated:
            ui.notify("Service limits updated", color="green")
        else:
            ui.notify(
                "Service limits updated, but cache is missing. Run sync to refresh.",
                color="warning",
            )
        selected_service["message_limit"] = message_limit
        selected_service["rate_limit"] = rate_limit
        update_edit_fields(selected_service if selected_service.get("id") else None)
        edit_dialog.close()
        await refresh_if_needed(services_table)

    edit_update_button.on_click(handle_update_service)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Services").classes("text-lg font-semibold")
        service_search = ui.input(label="Search by Service ID or Name").props("clearable").classes("w-full md:w-1/2")
        service_search.on_value_change(handle_service_search_event)
        await services_table(page_sync_services, selected_service, handle_open_edit_dialog)


@ui.refreshable
async def services_table(sync_callback, selected_service=None, on_edit_click=None) -> None:
    if selected_service is not None:
        selected_service.clear()
    view_env = get_view_environment()
    rows = await list_services(view_env)
    active_key_counts = await count_active_api_keys_by_service(view_env)
    if _st.service_search_query:
        rows = [
            row
            for row in rows
            if _st.service_search_query in (row.id or "").lower()
            or _st.service_search_query in (row.name or "").lower()
        ]
    columns = [
        {"name": "id", "label": "ID", "field": "id"},
        {"name": "environment", "label": "Environment", "field": "environment"},
        {"name": "name", "label": "Name", "field": "name"},
        {"name": "active_keys", "label": "Active Keys", "field": "active_keys"},
        {"name": "active", "label": "Active", "field": "active"},
        {"name": "restricted", "label": "Restricted", "field": "restricted"},
        {"name": "message_limit", "label": "Msg Limit", "field": "message_limit"},
        {"name": "rate_limit", "label": "Rate Limit", "field": "rate_limit"},
        {"name": "research_mode", "label": "Research", "field": "research_mode"},
        {"name": "count_as_live", "label": "Live", "field": "count_as_live"},
        {"name": "permissions", "label": "Permissions", "field": "permissions"},
    ]
    table_rows: List[Dict[str, Any]] = [
        {
            "_row_key": make_row_key(row.id, row.environment),
            "id": row.id,
            "environment": format_environment(row.environment),
            "environment_value": row.environment,
            "name": row.name,
            "active_keys": active_key_counts.get((row.id, row.environment), 0),
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
    with ui.row().classes("w-full items-center"):
        ui.button("Sync Services", on_click=sync_callback)
        if on_edit_click:
            ui.button("Edit Service Limits", on_click=on_edit_click, color="primary")
        ui.space()
        add_export_button(table_rows, columns, "services.csv")

    if selected_service is not None:

        def handle_row_select(e) -> None:  # pragma: no cover
            if e.selection:
                clicked_key = e.selection[0].get("_row_key")
                current_key = selected_service.get("_row_key")
                if clicked_key == current_key:
                    selected_service.clear()
                    table.selected = []
                    return
                selected_service.clear()
                selected_service.update(e.selection[0])
            else:
                selected_service.clear()

        table = ui.table(
            columns=make_sortable(columns),
            rows=table_rows,
            selection="single",
            on_select=handle_row_select,
            pagination={"rowsPerPage": 10},
        )
    else:
        table = ui.table(
            columns=make_sortable(columns),
            rows=table_rows,
            pagination={"rowsPerPage": 10},
        )
    table.props("row-key=_row_key").classes("w-full")
    add_copyable_slots(table, table_rows)
    add_service_context_menu(table, column_name="name", id_field="id")
