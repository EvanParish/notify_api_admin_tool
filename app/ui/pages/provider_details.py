from __future__ import annotations

from typing import Any

import httpx
from nicegui import ui

from app.repository import list_provider_details, update_provider_detail
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


@ui.page("/provider-details", response_timeout=PAGE_RESPONSE_TIMEOUT)
async def provider_details_page() -> None:
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
        ui.label("Provider Details").classes("text-lg font-semibold")

        selected_provider: dict[str, Any] = {}

        with ui.dialog() as manage_dialog, ui.card().classes("p-6 w-full max-w-lg"):
            ui.label("Manage Provider").classes("text-md font-semibold")
            selected_provider_label = ui.label("")
            priority_input = ui.number(label="Priority", min=0).classes("w-full")
            weight_input = ui.number(label="Load Balancing Weight", min=0).classes("w-full")
            active_checkbox = ui.checkbox("Active")
            with ui.row().classes("gap-2"):
                update_button = ui.button("Update Provider", color="primary")
                ui.button("Close", on_click=manage_dialog.close, color="gray")

        def resolve_selected_provider() -> dict[str, Any] | None:  # pragma: no cover
            return selected_provider if selected_provider.get("id") else None

        def resolve_selected_environment(
            provider: dict[str, Any],
        ) -> str | None:  # pragma: no cover
            env_value = provider.get("environment_value") or provider.get("environment")
            if not env_value or env_value == "unknown":
                return None
            return env_value

        def update_manage_fields(
            provider: dict[str, Any] | None,
        ) -> None:  # pragma: no cover
            if not provider:
                selected_provider_label.text = "No provider selected."
                priority_input.value = None
                weight_input.value = None
                active_checkbox.value = False
                return
            provider_id = provider.get("id")
            display_name = provider.get("name") or provider.get("display_name") or ""
            identifier = provider.get("identifier") or ""
            selected_provider_label.text = f"Selected: {display_name} ({identifier}) - ID {provider_id}"
            priority_input.value = provider.get("priority")
            weight_input.value = provider.get("load_balancing_weight")
            active_checkbox.value = bool(provider.get("active"))

        async def handle_open_manage_dialog() -> None:  # pragma: no cover
            provider = resolve_selected_provider()
            if not provider:
                ui.notify("Select a provider from the table first", color="red")
                return
            update_manage_fields(provider)
            manage_dialog.open()

        async def handle_update_provider() -> None:  # pragma: no cover
            provider = resolve_selected_provider()
            if not provider:
                ui.notify("Select a provider first", color="red")
                return
            environment = resolve_selected_environment(provider)
            provider_id = provider.get("id")
            if not (environment and provider_id):
                ui.notify("Selected provider is missing required details", color="red")
                return
            priority = int(priority_input.value) if priority_input.value is not None else None
            weight = int(weight_input.value) if weight_input.value is not None else None
            active = active_checkbox.value
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)
            try:
                await api.update_provider_detail(
                    provider_id,
                    priority=priority,
                    active=active,
                    load_balancing_weight=weight,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response and exc.response.status_code == 401:
                    handle_unauthorized(sync_label, environment)
                    return
                ui.notify(str(exc), color="red")
                return
            updated = await update_provider_detail(
                provider_id,
                priority=priority,
                active=active,
                load_balancing_weight=weight,
                environment=environment,
            )
            if updated:
                ui.notify("Provider updated", color="green")
            else:
                ui.notify(
                    "Provider updated, but cache is missing. Run sync to refresh.",
                    color="warning",
                )
            selected_provider["priority"] = priority
            selected_provider["load_balancing_weight"] = weight
            selected_provider["active"] = active
            update_manage_fields(resolve_selected_provider())
            manage_dialog.close()
            await refresh_if_needed(render_table)

        update_button.on_click(handle_update_provider)

        async def handle_sync_provider_details() -> None:  # pragma: no cover
            if await handle_entity_sync(["sync_provider_details"], status_badge, sync_label, "provider details"):
                render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            selected_provider.clear()
            update_manage_fields(None)
            providers = await list_provider_details(get_view_environment())
            columns = [
                {"name": "id", "label": "ID", "field": "id"},
                {"name": "environment", "label": "Environment", "field": "environment"},
                {"name": "name", "label": "Display Name", "field": "name"},
                {"name": "identifier", "label": "Identifier", "field": "identifier"},
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
                {"name": "updated_at", "label": "Updated", "field": "updated_at"},
            ]
            table_rows: list[dict[str, Any]] = [
                {
                    "_row_key": make_row_key(provider.id, provider.environment),
                    "id": provider.id,
                    "environment": format_environment(provider.environment),
                    "environment_value": provider.environment,
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
            with ui.row().classes("w-full items-center"):
                ui.button("Sync Provider Details", on_click=handle_sync_provider_details)
                ui.button(
                    "Manage Selected Provider",
                    on_click=handle_open_manage_dialog,
                    color="primary",
                )
                ui.space()
                add_export_button(table_rows, columns, "provider_details.csv")

            def handle_row_select(e) -> None:  # pragma: no cover
                if e.selection:
                    clicked_key = e.selection[0].get("_row_key")
                    current_key = selected_provider.get("_row_key")
                    if clicked_key == current_key:
                        selected_provider.clear()
                        table.selected = []
                        update_manage_fields(None)
                        return
                    selected_provider.clear()
                    selected_provider.update(e.selection[0])
                else:
                    selected_provider.clear()
                update_manage_fields(resolve_selected_provider())

            table = ui.table(
                columns=make_sortable(columns),
                rows=table_rows,
                selection="single",
                on_select=handle_row_select,
                pagination={"rowsPerPage": 9},
            )
            table.props("row-key=_row_key").classes("w-full")
            add_copyable_slots(table, table_rows)

        await render_table()
