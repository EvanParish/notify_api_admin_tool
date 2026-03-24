from __future__ import annotations

from typing import Any, Dict, List

from nicegui import ui

from app.repository import list_services, list_templates
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
    truncate_text,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import get_view_environment, refresh_status_badge
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


@ui.page("/templates")
async def templates_page() -> None:
    template_search_query = ""

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

    async def page_sync_templates():  # pragma: no cover
        await handle_entity_sync(
            ["sync_templates"],
            status_badge,
            sync_label,
            "templates",
            pre_sync=["sync_services"],
        )

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Templates").classes("text-lg font-semibold")
        template_search = (
            ui.input(label="Search by Template ID, Name, Service ID, or Service Name")
            .props("clearable")
            .classes("w-full md:w-1/2")
        )
        filter_row = ui.row().classes("gap-2")  # noqa: F841
        _services = await list_services(get_view_environment())
        service_options = {svc.id: format_service_label(svc) for svc in _services}
        service_name_map = build_service_name_map(_services)
        type_options = {"email": "Email", "sms": "SMS"}
        service_select = (
            ui.select(service_options, label="Service", with_input=True, multiple=True)
            .props("clearable use-chips")
            .classes("w-full md:w-1/2")
        )
        type_select = (
            ui.select(type_options, label="Type", with_input=True).props("clearable").classes("w-full md:w-1/2")
        )

        async def handle_sync_templates() -> None:  # pragma: no cover
            await page_sync_templates()
            render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:  # pragma: no cover
            selected_services = service_select.value or []
            selected_type = type_select.value
            rows = await list_templates(
                selected_services or None,
                selected_type,
                environment=get_view_environment(),
            )
            if template_search_query:
                rows = [
                    row
                    for row in rows
                    if template_search_query in (row.id or "").lower()
                    or template_search_query in (row.name or "").lower()
                    or template_search_query in (row.service_id or "").lower()
                    or template_search_query in (service_name_map.get(row.service_id, "")).lower()
                ]
            columns = [
                {"name": "id", "label": "ID", "field": "id"},
                {"name": "environment", "label": "Environment", "field": "environment"},
                {"name": "service_id", "label": "Service", "field": "service_name"},
                {"name": "name", "label": "Name", "field": "name"},
                {"name": "template_type", "label": "Type", "field": "template_type"},
                {"name": "version", "label": "Version", "field": "version"},
                {"name": "archived", "label": "Archived", "field": "archived"},
                {"name": "hidden", "label": "Hidden", "field": "hidden"},
                {"name": "updated_at", "label": "Updated", "field": "updated_at"},
                {"name": "subject", "label": "Subject", "field": "subject"},
                {"name": "content", "label": "Content", "field": "content"},
            ]
            table_rows: List[Dict[str, Any]] = [
                {
                    "_row_key": make_row_key(row.id, row.environment),
                    "id": row.id,
                    "environment": format_environment(row.environment),
                    "service_id": row.service_id,
                    "service_name": resolve_service_name(row.service_id, service_name_map),
                    "_full_service_name": service_name_map.get(row.service_id, row.service_id),
                    "name": truncate_text(row.name),
                    "_full_name": row.name,
                    "template_type": row.template_type,
                    "version": row.version,
                    "archived": row.archived,
                    "hidden": row.hidden,
                    "updated_at": row.updated_at[:10] if row.updated_at else None,  # Show date only
                    "subject": truncate_text(row.subject),
                    "content": truncate_text(row.content),
                }
                for row in rows
            ]
            with ui.row().classes("w-full items-center"):
                ui.button("Sync Templates", on_click=handle_sync_templates)
                ui.space()
                add_export_button(table_rows, columns, "templates.csv")
            table = ui.table(
                columns=make_sortable(columns),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=_row_key").classes("w-full")
            add_copyable_slots(table, table_rows)
            add_service_context_menu(table, column_name="service_id")

        async def handle_template_search_event(e) -> None:  # pragma: no cover
            nonlocal template_search_query
            template_search_query = (getattr(e, "value", None) or "").strip().lower()
            await refresh_if_needed(render_table)

        service_select.on_value_change(lambda _: render_table.refresh())
        type_select.on_value_change(lambda _: render_table.refresh())
        template_search.on_value_change(handle_template_search_event)
        await render_table()
