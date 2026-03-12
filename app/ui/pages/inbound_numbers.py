from __future__ import annotations

from typing import Any

import httpx
from nicegui import ui

from app.repository import list_inbound_numbers, list_services, update_inbound_number
from app.ui import state as _st
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
from app.ui.state import (
    build_api_client,
    ensure_admin_auth,
    get_view_environment,
    handle_unauthorized,
    refresh_status_badge,
)
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

    async def page_sync_inbound_numbers(
        environment: str | None = None,
    ):  # pragma: no cover
        envs = [environment] if environment else None
        await handle_entity_sync(
            ["sync_inbound_numbers"],
            status_badge,
            sync_label,
            "inbound numbers",
            environments=envs,
        )

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Inbound Numbers").classes("text-lg font-semibold")

        # Create Inbound Number dialog
        with ui.dialog() as create_dialog, ui.card().classes("p-6 w-full max-w-lg"):
            ui.label("Add Inbound Number").classes("text-md font-semibold")
            create_env = ui.select(
                {env: env.title() for env in _st.config.api_hosts},
                value=_st.state.environment,
                label="Environment",
            ).classes("w-full")
            create_number = (
                ui.input(label="Number (e.g., +12025551212)")
                .props("clearable")
                .classes("w-full")
            )
            create_provider = (
                ui.input(label="Provider (e.g., pinpoint)")
                .props("clearable")
                .classes("w-full")
            )
            create_active = ui.checkbox("Active", value=True)
            create_self_managed = ui.checkbox("Self Managed")
            create_auth_parameter = (
                ui.input(label="Auth Parameter").props("clearable").classes("w-full")
            )
            create_url_endpoint = (
                ui.input(label="URL Endpoint").props("clearable").classes("w-full")
            )
            ui.label("URL Endpoint is required when Self Managed is checked").classes(
                "text-xs text-gray-500"
            )
            with ui.row().classes("gap-2"):
                create_submit_button = ui.button("Create Inbound Number", color="green")
                ui.button("Cancel", on_click=create_dialog.close, color="gray")

        async def handle_create_inbound_number() -> None:  # pragma: no cover
            environment = create_env.value
            number = (create_number.value or "").strip()
            provider = (create_provider.value or "").strip()
            active = create_active.value
            self_managed = create_self_managed.value
            auth_parameter = (create_auth_parameter.value or "").strip() or None
            url_endpoint = (create_url_endpoint.value or "").strip() or None
            if not (environment and number and provider):
                ui.notify(
                    "Environment, number, and provider are required",
                    color="red",
                )
                return
            if self_managed and not url_endpoint:
                ui.notify(
                    "URL Endpoint is required when Self Managed is checked",
                    color="red",
                )
                return
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)
            try:
                await api.create_inbound_number(
                    number=number,
                    provider=provider,
                    active=active,
                    self_managed=self_managed,
                    auth_parameter=auth_parameter,
                    url_endpoint=url_endpoint,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response and exc.response.status_code == 401:
                    handle_unauthorized(sync_label, environment)
                    return
                ui.notify(f"Failed to create inbound number: {exc}", color="red")
                return
            except Exception as exc:
                ui.notify(f"Error creating inbound number: {exc}", color="red")
                return
            ui.notify("Inbound number created", color="green")
            create_dialog.close()
            await page_sync_inbound_numbers(environment)
            await refresh_if_needed(render_table)

        async def handle_open_create_dialog() -> None:  # pragma: no cover
            create_env.value = _st.state.environment
            create_number.value = ""
            create_provider.value = ""
            create_active.value = True
            create_self_managed.value = False
            create_auth_parameter.value = ""
            create_url_endpoint.value = ""
            create_dialog.open()

        create_submit_button.on_click(handle_create_inbound_number)

        # Edit Inbound Number dialog
        selected_number: dict[str, Any] = {}

        with ui.dialog() as edit_dialog, ui.card().classes("p-6 w-full max-w-lg"):
            ui.label("Edit Inbound Number").classes("text-md font-semibold")
            selected_number_label = ui.label("")
            edit_number = ui.input(label="Number").props("clearable").classes("w-full")
            edit_provider = (
                ui.input(label="Provider").props("clearable").classes("w-full")
            )
            edit_active = ui.checkbox("Active")
            edit_self_managed = ui.checkbox("Self Managed")
            edit_auth_parameter = (
                ui.input(label="Auth Parameter").props("clearable").classes("w-full")
            )
            edit_url_endpoint = (
                ui.input(label="URL Endpoint").props("clearable").classes("w-full")
            )
            ui.label("URL Endpoint is required when Self Managed is checked").classes(
                "text-xs text-gray-500"
            )
            with ui.row().classes("gap-2"):
                edit_update_button = ui.button("Update Inbound Number", color="primary")
                ui.button("Close", on_click=edit_dialog.close, color="gray")

        def resolve_selected_number() -> dict[str, Any] | None:  # pragma: no cover
            return selected_number if selected_number.get("id") else None

        def resolve_selected_environment(
            num: dict[str, Any],
        ) -> str | None:  # pragma: no cover
            env_value = num.get("environment_value") or num.get("environment")
            if not env_value or env_value == "unknown":
                return None
            return env_value

        def update_edit_fields(
            num: dict[str, Any] | None,
        ) -> None:  # pragma: no cover
            if not num:
                selected_number_label.text = "No inbound number selected."
                edit_number.value = ""
                edit_provider.value = ""
                edit_active.value = True
                edit_self_managed.value = False
                edit_auth_parameter.value = ""
                edit_url_endpoint.value = ""
                return
            num_id = num.get("id")
            number_val = num.get("number") or ""
            selected_number_label.text = f"Selected: {number_val} ({num_id})"
            edit_number.value = number_val
            edit_provider.value = num.get("provider") or ""
            edit_active.value = bool(num.get("active"))
            edit_self_managed.value = bool(num.get("self_managed"))
            edit_auth_parameter.value = num.get("auth_parameter") or ""
            edit_url_endpoint.value = num.get("url_endpoint") or ""

        async def handle_open_edit_dialog() -> None:  # pragma: no cover
            num = resolve_selected_number()
            if not num:
                ui.notify("Select an inbound number from the table first", color="red")
                return
            update_edit_fields(num)
            edit_dialog.open()

        async def handle_update_inbound_number() -> None:  # pragma: no cover
            num = resolve_selected_number()
            if not num:
                ui.notify("Select an inbound number first", color="red")
                return
            environment = resolve_selected_environment(num)
            num_id = num.get("id")
            if not (environment and num_id):
                ui.notify(
                    "Selected inbound number is missing required details", color="red"
                )
                return
            number_val = (edit_number.value or "").strip() or None
            provider = (edit_provider.value or "").strip() or None
            active = edit_active.value
            self_managed = edit_self_managed.value
            auth_parameter = (edit_auth_parameter.value or "").strip() or None
            url_endpoint = (edit_url_endpoint.value or "").strip() or None
            if self_managed and not url_endpoint:
                ui.notify(
                    "URL Endpoint is required when Self Managed is checked",
                    color="red",
                )
                return
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)
            try:
                await api.update_inbound_number(
                    inbound_number_id=num_id,
                    number=number_val,
                    provider=provider,
                    active=active,
                    self_managed=self_managed,
                    auth_parameter=auth_parameter,
                    url_endpoint=url_endpoint,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response and exc.response.status_code == 401:
                    handle_unauthorized(sync_label, environment)
                    return
                ui.notify(f"Failed to update inbound number: {exc}", color="red")
                return
            except Exception as exc:
                ui.notify(f"Error updating inbound number: {exc}", color="red")
                return
            updated = await update_inbound_number(
                inbound_number_id=num_id,
                number=number_val,
                provider=provider,
                active=active,
                self_managed=self_managed,
                auth_parameter=auth_parameter,
                url_endpoint=url_endpoint,
                environment=environment,
            )
            if updated:
                ui.notify("Inbound number updated", color="green")
            else:
                ui.notify(
                    "Inbound number updated, but cache is missing. Run sync to refresh.",
                    color="warning",
                )
            selected_number["number"] = number_val
            selected_number["provider"] = provider
            selected_number["active"] = active
            selected_number["self_managed"] = self_managed
            selected_number["auth_parameter"] = auth_parameter
            selected_number["url_endpoint"] = url_endpoint
            update_edit_fields(resolve_selected_number())
            edit_dialog.close()
            await refresh_if_needed(render_table)

        edit_update_button.on_click(handle_update_inbound_number)

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
            selected_number.clear()
            update_edit_fields(None)
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
            columns = [
                {"name": "id", "label": "ID", "field": "id"},
                {"name": "environment", "label": "Environment", "field": "environment"},
                {"name": "number", "label": "Number", "field": "number"},
                {"name": "provider", "label": "Provider", "field": "provider"},
                {"name": "active", "label": "Active", "field": "active"},
                {
                    "name": "self_managed",
                    "label": "Self Managed",
                    "field": "self_managed",
                },
                {"name": "service_id", "label": "Service ID", "field": "service_id"},
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
            table_rows: list[dict[str, Any]] = [
                {
                    "_row_key": make_row_key(n.id, n.environment),
                    "id": n.id,
                    "environment": format_environment(n.environment),
                    "environment_value": n.environment,
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

            def handle_row_select(e) -> None:  # pragma: no cover
                if e.selection:
                    clicked_key = e.selection[0].get("_row_key")
                    current_key = selected_number.get("_row_key")
                    if clicked_key == current_key:
                        selected_number.clear()
                        table.selected = []
                        update_edit_fields(None)
                        return
                    selected_number.clear()
                    selected_number.update(e.selection[0])
                else:
                    selected_number.clear()
                update_edit_fields(resolve_selected_number())

            with ui.row().classes("w-full items-center"):
                ui.button("Sync Inbound Numbers", on_click=handle_sync_inbound)
                ui.button(
                    "Edit Selected",
                    on_click=handle_open_edit_dialog,
                    color="primary",
                )
                ui.button(
                    "Add Inbound Number",
                    on_click=handle_open_create_dialog,
                    color="green",
                )
                ui.space()
                add_export_button(table_rows, columns, "inbound_numbers.csv")
            table = ui.table(
                columns=make_sortable(columns),
                rows=table_rows,
                selection="single",
                on_select=handle_row_select,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=_row_key").classes("w-full")
            add_copyable_slots(table, table_rows)

        service_select.on_value_change(lambda _: render_table.refresh())
        inbound_search.on_value_change(handle_inbound_search_event)
        await render_table()
