from __future__ import annotations

from typing import Any, Dict, List

from nicegui import ui

from app.repository import list_provider_details
from app.ui.helpers import (
    add_copyable_slots,
    format_environment,
    make_row_key,
    make_sortable,
    refresh_if_needed,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import get_view_environment, refresh_status_badge
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


@ui.page("/provider-details")
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

        async def handle_sync_provider_details() -> None:  # pragma: no cover
            if await handle_entity_sync(
                ["sync_provider_details"], status_badge, sync_label, "provider details"
            ):
                render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            providers = await list_provider_details(get_view_environment())
            table_rows: List[Dict[str, Any]] = [
                {
                    "_row_key": make_row_key(provider.id, provider.environment),
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
                        {
                            "name": "id",
                            "label": "ID",
                            "field": "id",
                        },
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
                        {
                            "name": "priority",
                            "label": "Priority",
                            "field": "priority",
                        },
                        {
                            "name": "load_balancing_weight",
                            "label": "Weight",
                            "field": "load_balancing_weight",
                        },
                        {
                            "name": "active",
                            "label": "Active",
                            "field": "active",
                        },
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
            table.props("row-key=_row_key").classes("w-full")
            add_copyable_slots(table, table_rows)

        ui.button("Sync Provider Details", on_click=handle_sync_provider_details)
        await render_table()
