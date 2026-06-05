from __future__ import annotations

from typing import Any

from nicegui import ui

from app.repository import list_service_callbacks, list_services
from app.ui.helpers import (
    add_copyable_slots,
    add_export_button,
    add_service_context_menu,
    build_service_name_map,
    format_environment,
    format_service_label,
    make_row_key,
    make_sortable,
    refresh_if_needed,
    resolve_service_name,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import (
    PAGE_RESPONSE_TIMEOUT,
    get_view_environment,
    refresh_status_badge,
)
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


def _format_statuses(statuses: list | None) -> str:
    if not statuses:
        return ""
    return ", ".join(str(s) for s in statuses)


@ui.page("/service-callbacks", response_timeout=PAGE_RESPONSE_TIMEOUT)
async def service_callbacks_page() -> None:
    callback_search_query = ""

    async def refresh_service_options() -> None:  # pragma: no cover
        options = {svc.id: format_service_label(svc) for svc in await list_services(get_view_environment())}
        service_select.set_options(options)
        if service_select.value:
            service_select.value = [v for v in service_select.value if v in options]

    async def handle_view_env_change() -> None:  # pragma: no cover
        await refresh_service_options()
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
        ui.label("Service Callbacks").classes("text-lg font-semibold")

        filter_row = ui.row().classes("gap-2 w-full")
        with filter_row:
            callback_search = (
                ui.input(label="Search by URL, ID, Service ID, Type, or Channel")
                .props("clearable")
                .classes("w-full md:w-1/2")
            )
        _services = await list_services(get_view_environment())
        service_options = {svc.id: format_service_label(svc) for svc in _services}
        service_select = (
            ui.select(
                service_options,
                label="Filter by Service",
                with_input=True,
                multiple=True,
            )
            .props("clearable use-chips")
            .classes("w-full md:w-1/2")
        )

        async def handle_sync_callbacks() -> None:  # pragma: no cover
            if await handle_entity_sync(
                ["sync_service_callbacks"],
                status_badge,
                sync_label,
                "service callbacks",
                pre_sync=["sync_services"],
            ):
                render_table.refresh()

        async def handle_search_event(e) -> None:  # pragma: no cover
            nonlocal callback_search_query
            callback_search_query = (getattr(e, "value", None) or "").strip().lower()
            await refresh_if_needed(render_table)

        @ui.refreshable
        async def render_table() -> None:  # pragma: no cover
            service_name_map = build_service_name_map(await list_services(get_view_environment()))
            selected_services = service_select.value or []
            callbacks = await list_service_callbacks(selected_services or None, environment=get_view_environment())
            if callback_search_query:
                callbacks = [
                    cb
                    for cb in callbacks
                    if callback_search_query in (cb.url or "").lower()
                    or callback_search_query in (cb.id or "").lower()
                    or callback_search_query in (cb.service_id or "").lower()
                    or callback_search_query in (service_name_map.get(cb.service_id or "", "")).lower()
                    or callback_search_query in (cb.callback_type or "").lower()
                    or callback_search_query in (cb.callback_channel or "").lower()
                ]
            columns = [
                {"name": "id", "label": "ID", "field": "id"},
                {"name": "environment", "label": "Environment", "field": "environment"},
                {"name": "service_id", "label": "Service", "field": "service_name"},
                {"name": "url", "label": "URL", "field": "url"},
                {"name": "callback_type", "label": "Type", "field": "callback_type"},
                {"name": "callback_channel", "label": "Channel", "field": "callback_channel"},
                {
                    "name": "notification_statuses",
                    "label": "Notification Statuses",
                    "field": "notification_statuses",
                },
                {
                    "name": "include_provider_payload",
                    "label": "Include Provider Payload",
                    "field": "include_provider_payload",
                },
                {"name": "created_at", "label": "Created At", "field": "created_at"},
                {"name": "updated_at", "label": "Updated At", "field": "updated_at"},
            ]
            table_rows: list[dict[str, Any]] = [
                {
                    "_row_key": make_row_key(cb.id, cb.environment),
                    "id": cb.id,
                    "environment": format_environment(cb.environment),
                    "environment_value": cb.environment,
                    "service_id": cb.service_id,
                    "service_name": resolve_service_name(cb.service_id, service_name_map),
                    "_full_service_name": service_name_map.get(cb.service_id or "", cb.service_id or ""),
                    "url": cb.url,
                    "callback_type": cb.callback_type,
                    "callback_channel": cb.callback_channel,
                    "notification_statuses": _format_statuses(cb.notification_statuses),
                    "include_provider_payload": cb.include_provider_payload,
                    "created_at": cb.created_at,
                    "updated_at": cb.updated_at,
                }
                for cb in callbacks
            ]
            with ui.row().classes("w-full items-center"):
                ui.button("Sync Service Callbacks", on_click=handle_sync_callbacks)
                ui.space()
                add_export_button(table_rows, columns, "service_callbacks.csv")
            table = ui.table(
                columns=make_sortable(columns),
                rows=table_rows,
                selection="single",
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=_row_key").classes("w-full")
            add_copyable_slots(table, table_rows)
            add_service_context_menu(table, column_name="service_id")

        service_select.on_value_change(lambda _: render_table.refresh())
        callback_search.on_value_change(handle_search_event)
        await render_table()
