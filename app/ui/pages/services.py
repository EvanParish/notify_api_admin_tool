from __future__ import annotations

from typing import Any, Dict, List, Optional

from nicegui import ui

from app.repository import list_services
from app.ui import state as _st
from app.ui.helpers import (
    add_copyable_slots,
    format_environment,
    make_sortable,
    refresh_if_needed,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import get_view_environment, refresh_status_badge
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


async def handle_service_search(value: Optional[str]) -> None:
    _st.service_search_query = (value or "").strip().lower()
    await refresh_if_needed(services_table)


async def handle_service_search_event(e) -> None:
    await handle_service_search(getattr(e, "value", None))


@ui.page("/services")
async def services_page() -> None:
    status_badge, sync_label, refresh_button, dark_mode, theme_button = build_shell(
        on_view_env_change=lambda: refresh_if_needed(services_table)
    )
    await ensure_theme_preference(dark_mode, theme_button)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    async def page_sync_services():  # pragma: no cover
        if await handle_entity_sync(
            ["sync_services"], status_badge, sync_label, "services"
        ):
            await refresh_if_needed(services_table)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Services").classes("text-lg font-semibold")
        service_search = (
            ui.input(label="Search by Service ID or Name")
            .props("clearable")
            .classes("w-full md:w-1/2")
        )
        service_search.on_value_change(handle_service_search_event)
        ui.button("Sync Services", on_click=page_sync_services)
        await services_table()


@ui.refreshable
async def services_table() -> None:
    rows = await list_services(get_view_environment())
    if _st.service_search_query:
        rows = [
            row
            for row in rows
            if _st.service_search_query in (row.id or "").lower()
            or _st.service_search_query in (row.name or "").lower()
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
                {
                    "name": "message_limit",
                    "label": "Msg Limit",
                    "field": "message_limit",
                },
                {"name": "rate_limit", "label": "Rate Limit", "field": "rate_limit"},
                {
                    "name": "research_mode",
                    "label": "Research",
                    "field": "research_mode",
                },
                {"name": "count_as_live", "label": "Live", "field": "count_as_live"},
                {"name": "permissions", "label": "Permissions", "field": "permissions"},
            ]
        ),
        rows=table_rows,
        pagination={"rowsPerPage": 10},
    )
    table.props("row-key=id").classes("w-full")
    add_copyable_slots(table, table_rows)
