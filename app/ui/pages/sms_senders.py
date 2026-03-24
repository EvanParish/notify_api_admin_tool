from __future__ import annotations

import json
from typing import Any

import httpx
from nicegui import ui

from app.repository import (
    list_provider_details,
    list_services,
    list_sms_senders,
    update_sms_sender,
)
from app.ui import state as _st
from app.ui.helpers import (
    add_copyable_slots,
    add_export_button,
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
    build_api_client,
    ensure_admin_auth,
    get_view_environment,
    handle_unauthorized,
    refresh_status_badge,
)
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


@ui.page("/sms-senders")
async def sms_senders_page() -> None:
    sms_sender_search_query = ""

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

    async def page_sync_sms_senders(
        environment: str | None = None,
    ):  # pragma: no cover
        envs = [environment] if environment else None
        await handle_entity_sync(
            ["sync_sms_senders"],
            status_badge,
            sync_label,
            "SMS senders",
            pre_sync=["sync_services"],
            environments=envs,
        )

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("SMS Senders").classes("text-lg font-semibold")

        # Create SMS Sender dialog
        with ui.dialog() as create_dialog, ui.card().classes("p-6 w-full max-w-3xl"):
            ui.label("Add SMS Sender").classes("text-md font-semibold")
            create_env = ui.select(
                {env: env.title() for env in _st.config.api_hosts},
                value=_st.state.environment,
                label="Environment",
            ).classes("w-full md:w-1/2")
            create_service = (
                ui.select({}, label="Service", with_input=True).props("clearable").classes("w-full md:w-1/2")
            )
            create_sms_sender = (
                ui.input(label="SMS Sender (phone number)").props("clearable").classes("w-full md:w-1/2")
            )
            create_description = ui.input(label="Description").props("clearable").classes("w-full md:w-1/2")
            create_provider = (
                ui.select({}, label="Provider", with_input=True).props("clearable").classes("w-full md:w-1/2")
            )
            create_is_default = ui.checkbox("Set as default")
            create_rate_limit = ui.number(label="Rate Limit", min=1).classes("w-full md:w-1/2")
            create_rate_limit_interval = ui.number(label="Rate Limit Interval (seconds)", min=1).classes(
                "w-full md:w-1/2"
            )
            create_sender_specifics = ui.textarea(label="Sender Specifics (JSON)").props("clearable").classes("w-full")
            ui.label('e.g., {"messaging_service_sid": "MG000..."}').classes("text-xs text-gray-500")
            with ui.row().classes("gap-2"):
                create_submit_button = ui.button("Create SMS Sender", color="green")
                ui.button("Cancel", on_click=create_dialog.close, color="gray")

        async def refresh_create_service_options() -> None:  # pragma: no cover
            options = {svc.id: format_service_label(svc) for svc in await list_services(create_env.value)}
            create_service.set_options(options)
            if create_service.value not in options:
                create_service.value = None

        async def refresh_create_provider_options() -> None:  # pragma: no cover
            providers = await list_provider_details(create_env.value)
            sms_providers = [p for p in providers if p.notification_type == "sms"]
            options = {p.id: f"{p.display_name} ({p.identifier})" for p in sms_providers}
            create_provider.set_options(options)
            if create_provider.value not in options:
                create_provider.value = None

        async def handle_create_env_change(_=None) -> None:  # pragma: no cover
            await refresh_create_service_options()
            await refresh_create_provider_options()

        async def handle_create_sms_sender() -> None:  # pragma: no cover
            environment = create_env.value
            service_id = create_service.value
            sms_sender = (create_sms_sender.value or "").strip()
            description = (create_description.value or "").strip()
            provider_id = create_provider.value
            is_default = create_is_default.value
            rate_limit = int(create_rate_limit.value) if create_rate_limit.value is not None else None
            rate_limit_interval = (
                int(create_rate_limit_interval.value) if create_rate_limit_interval.value is not None else None
            )
            sender_specifics_raw = (create_sender_specifics.value or "").strip()
            sender_specifics: dict | None = None
            if sender_specifics_raw:
                try:
                    sender_specifics = json.loads(sender_specifics_raw)
                    if not isinstance(sender_specifics, dict):
                        ui.notify("Sender Specifics must be a JSON object", color="red")
                        return
                except json.JSONDecodeError as exc:
                    ui.notify(f"Invalid JSON for Sender Specifics: {exc}", color="red")
                    return
            if not (environment and service_id and sms_sender and description and provider_id):
                ui.notify(
                    "Environment, service, SMS sender, description, and provider are required",
                    color="red",
                )
                return
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)
            try:
                await api.create_sms_sender(
                    service_id=service_id,
                    sms_sender=sms_sender,
                    description=description,
                    provider_id=provider_id,
                    is_default=is_default,
                    rate_limit=rate_limit,
                    rate_limit_interval=rate_limit_interval,
                    sms_sender_specifics=sender_specifics,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response and exc.response.status_code == 401:
                    handle_unauthorized(sync_label, environment)
                    return
                ui.notify(f"Failed to create SMS sender: {exc}", color="red")
                return
            except Exception as exc:
                ui.notify(f"Error creating SMS sender: {exc}", color="red")
                return
            ui.notify("SMS sender created", color="green")
            create_dialog.close()
            await page_sync_sms_senders(environment)
            await refresh_if_needed(render_table)

        async def handle_open_create_dialog() -> None:  # pragma: no cover
            create_env.value = _st.state.environment
            create_sms_sender.value = ""
            create_description.value = ""
            create_is_default.value = False
            create_rate_limit.value = None
            create_rate_limit_interval.value = None
            create_sender_specifics.value = ""
            await refresh_create_service_options()
            await refresh_create_provider_options()
            create_dialog.open()

        create_env.on_value_change(handle_create_env_change)
        create_submit_button.on_click(handle_create_sms_sender)

        # Edit SMS Sender dialog
        selected_sender: dict[str, Any] = {}

        with ui.dialog() as edit_dialog, ui.card().classes("p-6 w-full max-w-lg"):
            ui.label("Edit SMS Sender").classes("text-md font-semibold")
            selected_sender_label = ui.label("")
            edit_sms_sender = ui.input(label="SMS Sender (phone number)").props("clearable").classes("w-full")
            edit_description = ui.input(label="Description").props("clearable").classes("w-full")
            edit_provider = ui.select({}, label="Provider", with_input=True).props("clearable").classes("w-full")
            edit_is_default = ui.checkbox("Set as default")
            edit_rate_limit = ui.number(label="Rate Limit", min=1).classes("w-full")
            edit_rate_limit_interval = ui.number(label="Rate Limit Interval (seconds)", min=1).classes("w-full")
            edit_sender_specifics = ui.textarea(label="Sender Specifics (JSON)").props("clearable").classes("w-full")
            ui.label('e.g., {"messaging_service_sid": "MG000..."}').classes("text-xs text-gray-500")
            with ui.row().classes("gap-2"):
                edit_update_button = ui.button("Update SMS Sender", color="primary")
                ui.button("Close", on_click=edit_dialog.close, color="gray")

        def resolve_selected_sender() -> dict[str, Any] | None:  # pragma: no cover
            return selected_sender if selected_sender.get("id") else None

        def resolve_selected_environment(
            sender: dict[str, Any],
        ) -> str | None:  # pragma: no cover
            env_value = sender.get("environment_value") or sender.get("environment")
            if not env_value or env_value == "unknown":
                return None
            return env_value

        def update_edit_fields(
            sender: dict[str, Any] | None,
        ) -> None:  # pragma: no cover
            if not sender:
                selected_sender_label.text = "No SMS sender selected."
                edit_sms_sender.value = ""
                edit_description.value = ""
                edit_provider.value = None
                edit_is_default.value = False
                edit_rate_limit.value = None
                edit_rate_limit_interval.value = None
                edit_sender_specifics.value = ""
                return
            sender_id = sender.get("id")
            sms_sender_val = sender.get("sms_sender") or ""
            selected_sender_label.text = f"Selected: {sms_sender_val} ({sender_id})"
            edit_sms_sender.value = sms_sender_val
            edit_description.value = sender.get("description") or ""
            edit_provider.value = sender.get("provider_id")
            edit_is_default.value = bool(sender.get("is_default"))
            edit_rate_limit.value = sender.get("rate_limit")
            edit_rate_limit_interval.value = sender.get("rate_limit_interval")
            specifics = sender.get("sms_sender_specifics")
            if specifics and isinstance(specifics, dict):
                edit_sender_specifics.value = json.dumps(specifics, indent=2)
            else:
                edit_sender_specifics.value = ""

        async def handle_open_edit_dialog() -> None:  # pragma: no cover
            sender = resolve_selected_sender()
            if not sender:
                ui.notify("Select an SMS sender from the table first", color="red")
                return
            environment = resolve_selected_environment(sender)
            if environment:
                providers = await list_provider_details(environment)
                sms_providers = [p for p in providers if p.notification_type == "sms"]
                options = {p.id: f"{p.display_name} ({p.identifier})" for p in sms_providers}
                edit_provider.set_options(options)
            update_edit_fields(sender)
            edit_dialog.open()

        async def handle_update_sms_sender() -> None:  # pragma: no cover
            sender = resolve_selected_sender()
            if not sender:
                ui.notify("Select an SMS sender first", color="red")
                return
            environment = resolve_selected_environment(sender)
            sender_id = sender.get("id")
            service_id = sender.get("service_id")
            if not (environment and sender_id and service_id):
                ui.notify("Selected SMS sender is missing required details", color="red")
                return
            sms_sender_val = (edit_sms_sender.value or "").strip() or None
            description = (edit_description.value or "").strip() or None
            provider_id = edit_provider.value
            is_default = edit_is_default.value
            rate_limit = int(edit_rate_limit.value) if edit_rate_limit.value is not None else None
            rate_limit_interval = (
                int(edit_rate_limit_interval.value) if edit_rate_limit_interval.value is not None else None
            )
            sender_specifics_raw = (edit_sender_specifics.value or "").strip()
            sender_specifics: dict | None = None
            if sender_specifics_raw:
                try:
                    sender_specifics = json.loads(sender_specifics_raw)
                    if not isinstance(sender_specifics, dict):
                        ui.notify("Sender Specifics must be a JSON object", color="red")
                        return
                except json.JSONDecodeError as exc:
                    ui.notify(f"Invalid JSON for Sender Specifics: {exc}", color="red")
                    return
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)
            try:
                await api.update_sms_sender(
                    service_id=service_id,
                    sms_sender_id=sender_id,
                    sms_sender=sms_sender_val,
                    description=description,
                    provider_id=provider_id,
                    is_default=is_default,
                    rate_limit=rate_limit,
                    rate_limit_interval=rate_limit_interval,
                    sms_sender_specifics=sender_specifics,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response and exc.response.status_code == 401:
                    handle_unauthorized(sync_label, environment)
                    return
                ui.notify(f"Failed to update SMS sender: {exc}", color="red")
                return
            except Exception as exc:
                ui.notify(f"Error updating SMS sender: {exc}", color="red")
                return
            updated = await update_sms_sender(
                sms_sender_id=sender_id,
                sms_sender=sms_sender_val,
                description=description,
                provider_id=provider_id,
                is_default=is_default,
                rate_limit=rate_limit,
                rate_limit_interval=str(rate_limit_interval) if rate_limit_interval else None,
                sms_sender_specifics=sender_specifics,
                environment=environment,
            )
            if updated:
                ui.notify("SMS sender updated", color="green")
            else:
                ui.notify(
                    "SMS sender updated, but cache is missing. Run sync to refresh.",
                    color="warning",
                )
            selected_sender["sms_sender"] = sms_sender_val
            selected_sender["description"] = description
            selected_sender["provider_id"] = provider_id
            selected_sender["is_default"] = is_default
            selected_sender["rate_limit"] = rate_limit
            selected_sender["rate_limit_interval"] = rate_limit_interval
            selected_sender["sms_sender_specifics"] = sender_specifics
            update_edit_fields(resolve_selected_sender())
            edit_dialog.close()
            await refresh_if_needed(render_table)

        edit_update_button.on_click(handle_update_sms_sender)

        filter_row = ui.row().classes("gap-2 w-full")
        with filter_row:
            sms_sender_search = (
                ui.input(label="Search by SMS Sender, ID, Service ID, or Service Name")
                .props("clearable")
                .classes("w-full md:w-1/2")
            )
        _services = await list_services(get_view_environment())
        service_options = {svc.id: format_service_label(svc) for svc in _services}
        service_name_map = build_service_name_map(_services)
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

        async def handle_sync_senders() -> None:  # pragma: no cover
            await page_sync_sms_senders()
            render_table.refresh()

        async def handle_sms_sender_search_event(e) -> None:  # pragma: no cover
            nonlocal sms_sender_search_query
            sms_sender_search_query = (getattr(e, "value", None) or "").strip().lower()
            await refresh_if_needed(render_table)

        @ui.refreshable
        async def render_table() -> None:  # pragma: no cover
            selected_sender.clear()
            update_edit_fields(None)
            selected_services = service_select.value or []
            senders = await list_sms_senders(selected_services or None, environment=get_view_environment())
            if sms_sender_search_query:
                senders = [
                    sender
                    for sender in senders
                    if sms_sender_search_query in (sender.sms_sender or "").lower()
                    or sms_sender_search_query in (sender.id or "").lower()
                    or sms_sender_search_query in (sender.service_id or "").lower()
                    or sms_sender_search_query in (service_name_map.get(sender.service_id, "")).lower()
                ]
            columns = [
                {"name": "id", "label": "ID", "field": "id"},
                {"name": "environment", "label": "Environment", "field": "environment"},
                {
                    "name": "service_id",
                    "label": "Service",
                    "field": "service_name",
                },
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
                {
                    "name": "sms_sender_specifics",
                    "label": "Sender Specifics",
                    "field": "sms_sender_specifics_display",
                },
            ]

            def format_sender_specifics(specifics: dict | None) -> str:
                if not specifics or not isinstance(specifics, dict) or len(specifics) == 0:
                    return ""
                return json.dumps(specifics, separators=(",", ":"))

            table_rows: list[dict[str, Any]] = [
                {
                    "_row_key": make_row_key(sender.id, sender.environment),
                    "id": sender.id,
                    "environment": format_environment(sender.environment),
                    "environment_value": sender.environment,
                    "service_id": sender.service_id,
                    "service_name": resolve_service_name(sender.service_id, service_name_map),
                    "_full_service_name": service_name_map.get(sender.service_id, sender.service_id),
                    "sms_sender": sender.sms_sender,
                    "is_default": sender.is_default,
                    "archived": sender.archived,
                    "description": sender.description,
                    "provider_id": sender.provider_id,
                    "provider_name": sender.provider_name,
                    "rate_limit": sender.rate_limit,
                    "rate_limit_interval": sender.rate_limit_interval,
                    "sms_sender_specifics": sender.sms_sender_specifics,
                    "sms_sender_specifics_display": format_sender_specifics(sender.sms_sender_specifics),
                    "created_at": sender.created_at[:10] if sender.created_at else None,
                    "updated_at": sender.updated_at[:10] if sender.updated_at else None,
                }
                for sender in senders
            ]
            with ui.row().classes("w-full items-center"):
                ui.button("Sync SMS Senders", on_click=handle_sync_senders)
                ui.button(
                    "Edit Selected Sender",
                    on_click=handle_open_edit_dialog,
                    color="primary",
                )
                ui.button(
                    "Add SMS Sender",
                    on_click=handle_open_create_dialog,
                    color="green",
                )
                ui.space()
                add_export_button(table_rows, columns, "sms_senders.csv")

            def handle_row_select(e) -> None:  # pragma: no cover
                if e.selection:
                    clicked_key = e.selection[0].get("_row_key")
                    current_key = selected_sender.get("_row_key")
                    if clicked_key == current_key:
                        selected_sender.clear()
                        table.selected = []
                        update_edit_fields(None)
                        return
                    selected_sender.clear()
                    selected_sender.update(e.selection[0])
                else:
                    selected_sender.clear()
                update_edit_fields(resolve_selected_sender())

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
        sms_sender_search.on_value_change(handle_sms_sender_search_event)
        await render_table()
