from __future__ import annotations

from typing import Any, Dict, List

from nicegui import ui

from app.repository import list_communication_items
from app.ui.helpers import (
    add_copyable_slots,
    format_environment,
    make_sortable,
    refresh_if_needed,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import get_view_environment, refresh_status_badge
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


@ui.page("/communication-items")
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
            items = await list_communication_items(get_view_environment())
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": item.id,
                    "environment": format_environment(item.environment),
                    "name": item.name,
                    "va_profile_item_id": item.va_profile_item_id,
                    "default_send_indicator": item.default_send_indicator,
                }
                for item in items
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
                ),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=id").classes("w-full")
            add_copyable_slots(table, table_rows)

        ui.button("Sync Communication Items", on_click=handle_sync_communication_items)
        await render_table()
