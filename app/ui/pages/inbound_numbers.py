from __future__ import annotations

from typing import Any, Dict, List

from nicegui import ui

from app.repository import list_inbound_numbers, list_services
from app.ui.helpers import (
    add_copyable_slots,
    format_environment,
    format_service_label,
    make_sortable,
    refresh_if_needed,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import get_view_environment, refresh_status_badge
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


@ui.page("/inbound-numbers")
async def inbound_numbers_page() -> None:
    inbound_search_query = ""

    async def refresh_service_options() -> None:  # pragma: no cover
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
        }
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None

    async def handle_view_env_change() -> None:  # pragma: no cover
        await refresh_service_options()
        await refresh_if_needed(render_table)

    status_badge, sync_label, refresh_button, dark_mode, theme_button = build_shell(
        on_view_env_change=handle_view_env_change
    )
    await ensure_theme_preference(dark_mode, theme_button)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    async def page_sync_inbound_numbers():  # pragma: no cover
        await handle_entity_sync(
            ["sync_inbound_numbers"], status_badge, sync_label, "inbound numbers"
        )

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Inbound Numbers").classes("text-lg font-semibold")

        filter_row = ui.row().classes("gap-2 w-full")
        with filter_row:
            inbound_search = (
                ui.input(label="Search by Number or ID")
                .props("clearable")
                .classes("w-full md:w-1/2")
            )
        service_options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(get_view_environment())
        }
        service_select = (
            ui.select(service_options, label="Filter by Service", with_input=True)
            .props("clearable")
            .classes("w-full md:w-1/2")
        )

        async def handle_sync_inbound() -> None:  # pragma: no cover
            await page_sync_inbound_numbers()
            render_table.refresh()

        async def handle_inbound_search_event(e) -> None:  # pragma: no cover
            nonlocal inbound_search_query
            inbound_search_query = (getattr(e, "value", None) or "").strip().lower()
            await refresh_if_needed(render_table)

        @ui.refreshable
        async def render_table() -> None:  # pragma: no cover
            selected_service = service_select.value
            numbers = await list_inbound_numbers(
                selected_service, environment=get_view_environment()
            )
            if inbound_search_query:
                numbers = [
                    n
                    for n in numbers
                    if inbound_search_query in (n.number or "").lower()
                    or inbound_search_query in (n.id or "").lower()
                ]
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": n.id,
                    "environment": format_environment(n.environment),
                    "number": n.number,
                    "provider": n.provider,
                    "active": n.active,
                    "self_managed": n.self_managed,
                    "service_id": n.service_id,
                    "service_name": n.service_name,
                    "auth_parameter": n.auth_parameter,
                    "url_endpoint": n.url_endpoint,
                }
                for n in numbers
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
                        {"name": "number", "label": "Number", "field": "number"},
                        {
                            "name": "provider",
                            "label": "Provider",
                            "field": "provider",
                        },
                        {"name": "active", "label": "Active", "field": "active"},
                        {
                            "name": "self_managed",
                            "label": "Self Managed",
                            "field": "self_managed",
                        },
                        {
                            "name": "service_id",
                            "label": "Service ID",
                            "field": "service_id",
                        },
                        {
                            "name": "service_name",
                            "label": "Service Name",
                            "field": "service_name",
                        },
                        {
                            "name": "auth_parameter",
                            "label": "Auth Parameter",
                            "field": "auth_parameter",
                        },
                        {
                            "name": "url_endpoint",
                            "label": "URL Endpoint",
                            "field": "url_endpoint",
                        },
                    ]
                ),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=id").classes("w-full")
            add_copyable_slots(table, table_rows)

        service_select.on_value_change(lambda _: render_table.refresh())
        inbound_search.on_value_change(handle_inbound_search_event)
        ui.button("Sync Inbound Numbers", on_click=handle_sync_inbound)
        await render_table()
