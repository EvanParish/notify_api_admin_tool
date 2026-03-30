from __future__ import annotations

from typing import Any

import httpx
from nicegui import ui

from app.repository import list_communication_items, update_communication_item
from app.ui.helpers import (
    add_copyable_slots,
    add_export_button,
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


@ui.page("/communication-items", response_timeout=PAGE_RESPONSE_TIMEOUT)
async def communication_items_page() -> None:
    async def handle_view_env_change() -> None:  # pragma: no cover
        await refresh_if_needed(render_table)

    status_badge, sync_label, refresh_button, dark_mode, theme_button = build_shell(
        on_view_env_change=handle_view_env_change
    )
    await ensure_theme_preference(dark_mode, theme_button)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Communication Items").classes("text-lg font-semibold")

        selected_item: dict[str, Any] = {}

        with ui.dialog() as manage_dialog, ui.card().classes("p-6 w-full max-w-lg"):
            ui.label("Manage Communication Item").classes("text-md font-semibold")
            selected_item_label = ui.label("")
            name_input = ui.input(label="Name").classes("w-full")
            va_profile_id_input = ui.number(label="VA Profile Item ID", min=1).classes("w-full")
            default_send_checkbox = ui.checkbox("Default Send Indicator")
            with ui.row().classes("gap-2"):
                update_button = ui.button("Update Item", color="primary")
                ui.button("Close", on_click=manage_dialog.close, color="gray")

        def resolve_selected_item() -> dict[str, Any] | None:  # pragma: no cover
            return selected_item if selected_item.get("id") else None

        def resolve_selected_environment(
            item: dict[str, Any],
        ) -> str | None:  # pragma: no cover
            env_value = item.get("environment_value") or item.get("environment")
            if not env_value or env_value == "unknown":
                return None
            return env_value

        def update_manage_fields(
            item: dict[str, Any] | None,
        ) -> None:  # pragma: no cover
            if not item:
                selected_item_label.text = "No item selected."
                name_input.value = ""
                va_profile_id_input.value = None
                default_send_checkbox.value = False
                return
            item_id = item.get("id")
            item_name = item.get("name") or ""
            selected_item_label.text = f"Selected: {item_name} - ID {item_id}"
            name_input.value = item_name
            va_profile_id_input.value = item.get("va_profile_item_id")
            default_send_checkbox.value = bool(item.get("default_send_indicator"))

        async def handle_open_manage_dialog() -> None:  # pragma: no cover
            item = resolve_selected_item()
            if not item:
                ui.notify("Select a communication item from the table first", color="red")
                return
            update_manage_fields(item)
            manage_dialog.open()

        async def handle_update_item() -> None:  # pragma: no cover
            item = resolve_selected_item()
            if not item:
                ui.notify("Select a communication item first", color="red")
                return
            environment = resolve_selected_environment(item)
            item_id = item.get("id")
            if not (environment and item_id):
                ui.notify("Selected item is missing required details", color="red")
                return
            name = (name_input.value or "").strip() or None
            va_profile_id = int(va_profile_id_input.value) if va_profile_id_input.value is not None else None
            default_send = default_send_checkbox.value
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)
            try:
                await api.update_communication_item(
                    item_id,
                    name=name,
                    default_send_indicator=default_send,
                    va_profile_item_id=va_profile_id,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response and exc.response.status_code == 401:
                    handle_unauthorized(sync_label, environment)
                    return
                ui.notify(str(exc), color="red")
                return
            updated = await update_communication_item(
                item_id,
                name=name,
                default_send_indicator=default_send,
                va_profile_item_id=va_profile_id,
                environment=environment,
            )
            if updated:
                ui.notify("Communication item updated", color="green")
            else:
                ui.notify(
                    "Item updated, but cache is missing. Run sync to refresh.",
                    color="warning",
                )
            if name is not None:
                selected_item["name"] = name
            if va_profile_id is not None:
                selected_item["va_profile_item_id"] = va_profile_id
            selected_item["default_send_indicator"] = default_send
            update_manage_fields(resolve_selected_item())
            manage_dialog.close()
            await refresh_if_needed(render_table)

        update_button.on_click(handle_update_item)

        async def handle_sync_communication_items() -> None:  # pragma: no cover
            if await handle_entity_sync(
                ["sync_communication_items"],
                status_badge,
                sync_label,
                "communication items",
            ):
                render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            selected_item.clear()
            update_manage_fields(None)
            items = await list_communication_items(get_view_environment())
            columns = [
                {"name": "id", "label": "ID", "field": "id"},
                {"name": "environment", "label": "Environment", "field": "environment"},
                {"name": "name", "label": "Name", "field": "name"},
                {
                    "name": "va_profile_item_id",
                    "label": "VA Profile Item ID",
                    "field": "va_profile_item_id",
                },
                {
                    "name": "default_send_indicator",
                    "label": "Default Send",
                    "field": "default_send_indicator",
                },
            ]
            table_rows: list[dict[str, Any]] = [
                {
                    "_row_key": make_row_key(item.id, item.environment),
                    "id": item.id,
                    "environment": format_environment(item.environment),
                    "environment_value": item.environment,
                    "name": item.name,
                    "va_profile_item_id": item.va_profile_item_id,
                    "default_send_indicator": item.default_send_indicator,
                }
                for item in items
            ]
            with ui.row().classes("w-full items-center"):
                ui.button("Sync Communication Items", on_click=handle_sync_communication_items)
                ui.button(
                    "Manage Selected Item",
                    on_click=handle_open_manage_dialog,
                    color="primary",
                )
                ui.space()
                add_export_button(table_rows, columns, "communication_items.csv")

            def handle_row_select(e) -> None:  # pragma: no cover
                if e.selection:
                    clicked_key = e.selection[0].get("_row_key")
                    current_key = selected_item.get("_row_key")
                    if clicked_key == current_key:
                        selected_item.clear()
                        table.selected = []
                        update_manage_fields(None)
                        return
                    selected_item.clear()
                    selected_item.update(e.selection[0])
                else:
                    selected_item.clear()
                update_manage_fields(resolve_selected_item())

            table = ui.table(
                columns=make_sortable(columns),
                rows=table_rows,
                selection="single",
                on_select=handle_row_select,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=_row_key").classes("w-full")
            add_copyable_slots(table, table_rows)

        await render_table()
