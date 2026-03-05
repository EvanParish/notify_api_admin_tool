from __future__ import annotations

from typing import Any, Dict, List

from nicegui import ui

from app.repository import list_services, list_sms_senders
from app.ui.helpers import (
    add_copyable_slots,
    add_export_button,
    format_environment,
    format_service_label,
    make_row_key,
    make_sortable,
    refresh_if_needed,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import get_view_environment, refresh_status_badge
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


@ui.page("/sms-senders")
async def sms_senders_page() -> None:
    sms_sender_search_query = ""

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

    async def page_sync_sms_senders():  # pragma: no cover
        await handle_entity_sync(
            ["sync_sms_senders"],
            status_badge,
            sync_label,
            "SMS senders",
            pre_sync=["sync_services"],
        )

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("SMS Senders").classes("text-lg font-semibold")

        filter_row = ui.row().classes("gap-2 w-full")
        with filter_row:
            sms_sender_search = (
                ui.input(label="Search by SMS Sender or ID")
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

        async def handle_sync_senders() -> None:  # pragma: no cover
            await page_sync_sms_senders()
            render_table.refresh()

        async def handle_sms_sender_search_event(e) -> None:  # pragma: no cover
            nonlocal sms_sender_search_query
            sms_sender_search_query = (getattr(e, "value", None) or "").strip().lower()
            await refresh_if_needed(render_table)

        @ui.refreshable
        async def render_table() -> None:  # pragma: no cover
            selected_service = service_select.value
            senders = await list_sms_senders(
                selected_service, environment=get_view_environment()
            )
            if sms_sender_search_query:
                senders = [
                    sender
                    for sender in senders
                    if sms_sender_search_query in (sender.sms_sender or "").lower()
                    or sms_sender_search_query in (sender.id or "").lower()
                ]
            columns = [
                {"name": "id", "label": "ID", "field": "id"},
                {"name": "environment", "label": "Environment", "field": "environment"},
                {"name": "service_id", "label": "Service", "field": "service_id"},
                {"name": "sms_sender", "label": "SMS Sender", "field": "sms_sender"},
                {"name": "is_default", "label": "Default", "field": "is_default"},
                {"name": "archived", "label": "Archived", "field": "archived"},
                {"name": "description", "label": "Description", "field": "description"},
                {
                    "name": "provider_name",
                    "label": "Provider",
                    "field": "provider_name",
                },
                {"name": "rate_limit", "label": "Rate Limit", "field": "rate_limit"},
                {
                    "name": "rate_limit_interval",
                    "label": "Rate Interval",
                    "field": "rate_limit_interval",
                },
                {"name": "created_at", "label": "Created", "field": "created_at"},
                {"name": "updated_at", "label": "Updated", "field": "updated_at"},
            ]
            table_rows: List[Dict[str, Any]] = [
                {
                    "_row_key": make_row_key(sender.id, sender.environment),
                    "id": sender.id,
                    "environment": format_environment(sender.environment),
                    "service_id": sender.service_id,
                    "sms_sender": sender.sms_sender,
                    "is_default": sender.is_default,
                    "archived": sender.archived,
                    "description": sender.description,
                    "provider_name": sender.provider_name,
                    "rate_limit": sender.rate_limit,
                    "rate_limit_interval": sender.rate_limit_interval,
                    "created_at": sender.created_at[:10] if sender.created_at else None,
                    "updated_at": sender.updated_at[:10] if sender.updated_at else None,
                }
                for sender in senders
            ]
            with ui.row().classes("w-full items-center"):
                ui.button("Sync SMS Senders", on_click=handle_sync_senders)
                ui.space()
                add_export_button(table_rows, columns, "sms_senders.csv")
            table = ui.table(
                columns=make_sortable(columns),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=_row_key").classes("w-full")
            add_copyable_slots(table, table_rows)

        service_select.on_value_change(lambda _: render_table.refresh())
        sms_sender_search.on_value_change(handle_sms_sender_search_event)
        await render_table()
