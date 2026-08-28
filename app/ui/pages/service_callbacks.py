from __future__ import annotations

from typing import Any

import httpx
from nicegui import ui

from app.repository import (
    delete_service_callback,
    list_service_callbacks,
    list_services,
    upsert_service_callbacks,
)
from app.ui import state as _st
from app.ui.callback_helpers import (
    COMPLETED_NOTIFICATION_STATUSES,
    available_callback_options,
    build_create_payload,
    build_update_payload,
    create_statuses_default,
    edit_statuses_control_state,
    format_http_error,
    format_statuses,
    resolve_row_environment,
    validate_create,
    validate_update,
)
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
    build_api_client,
    ensure_admin_auth,
    get_view_environment,
    handle_unauthorized,
    refresh_status_badge,
)
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync

# Shown whenever a cached row carries no usable environment. Used both before opening a
# dialog and at the post-open guards so the user always sees the same remedy.
UNKNOWN_ENVIRONMENT_MESSAGE = "This callback has no known environment. Run Sync Service Callbacks first."

CREATE_HINT_DEFAULT = (
    "Type and channel options are filtered from cached data. If an option you expect "
    "is missing, close this and run Sync Service Callbacks to refresh."
)
CREATE_HINT_NO_SERVICE = "Select a service to see the available callback types and channels."
CREATE_HINT_SATURATED = (
    "This service already has a callback for every available type or channel. "
    "Delete an existing callback first, or run Sync Service Callbacks if you "
    "believe the cache is stale."
)


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

        selected_callback: dict[str, Any] = {}
        pending_delete: dict[str, str] = {}
        status_options = list(COMPLETED_NOTIFICATION_STATUSES)

        # ------------------------------------------------------------------
        # Create dialog
        # ------------------------------------------------------------------
        with ui.dialog() as create_dialog, ui.card().classes("p-6 w-full max-w-3xl"):
            ui.label("Add Service Callback").classes("text-md font-semibold")
            create_env = ui.select(
                {env: env.title() for env in _st.config.api_hosts},
                value=_st.state.environment,
                label="Environment",
            ).classes("w-full md:w-1/2")
            create_service = (
                ui.select({}, label="Service", with_input=True).props("clearable").classes("w-full md:w-1/2")
            )
            create_type = ui.select({}, label="Callback Type").classes("w-full md:w-1/2")
            create_channel = ui.select({}, label="Callback Channel").classes("w-full md:w-1/2")
            create_url = ui.input(label="URL (https)").props("clearable").classes("w-full")
            create_token = ui.input(label="Bearer Token", password=True).props("clearable").classes("w-full")
            ui.label("Required for webhook callbacks. Minimum 10 characters.").classes("text-xs text-gray-500")
            create_statuses = (
                ui.select(status_options, label="Notification Statuses", multiple=True)
                .props("clearable use-chips")
                .classes("w-full")
            )
            ui.label("Leave empty to receive all statuses.").classes("text-xs text-gray-500")
            create_include_payload = ui.checkbox("Include provider payload")
            create_hint = ui.label(CREATE_HINT_DEFAULT).classes("text-xs text-gray-500")
            with ui.row().classes("gap-2"):
                create_submit_button = ui.button("Create Callback", color="green")
                create_cancel_button = ui.button("Cancel", color="gray")

        def clear_create_token() -> None:  # pragma: no cover
            """Blank the bearer token field.

            The value lives in the browser DOM and in this element's server-side props
            until it is overwritten, so every dismissal path has to clear it explicitly.
            """
            create_token.value = ""

        def handle_create_cancel() -> None:  # pragma: no cover
            clear_create_token()
            create_dialog.close()

        create_cancel_button.on_click(handle_create_cancel)
        # Quasar QDialog emits "hide" on every dismissal, including ESC and backdrop
        # click, which never reach the Cancel handler. args=[] because the handler needs
        # nothing from the event and there is no point serializing a DOM event for it.
        create_dialog.on("hide", clear_create_token, args=[])

        async def refresh_create_service_options() -> None:  # pragma: no cover
            options = {svc.id: format_service_label(svc) for svc in await list_services(create_env.value)}
            create_service.set_options(options)
            if create_service.value not in options:
                create_service.value = None

        def update_create_status_state() -> None:  # pragma: no cover
            enabled, value = create_statuses_default(create_type.value, create_statuses.value)
            create_statuses.value = value
            if enabled:
                create_statuses.enable()
            else:
                create_statuses.disable()

        async def refresh_create_type_and_channel() -> None:  # pragma: no cover
            service_id = create_service.value
            if not service_id:
                create_type.set_options({})
                create_channel.set_options({})
                create_type.value = None
                create_channel.value = None
                create_hint.text = CREATE_HINT_NO_SERVICE
                create_submit_button.disable()
                return
            existing = await list_service_callbacks([service_id], environment=create_env.value)
            types, channels = available_callback_options(existing)
            create_type.set_options({t: t for t in types})
            create_channel.set_options({c: c for c in channels})
            if create_type.value not in types:
                create_type.value = None
            if create_channel.value not in channels:
                create_channel.value = None
            if not types or not channels:
                create_hint.text = CREATE_HINT_SATURATED
                create_submit_button.disable()
            else:
                create_hint.text = CREATE_HINT_DEFAULT
                create_submit_button.enable()
            update_create_status_state()

        async def handle_create_env_change(_=None) -> None:  # pragma: no cover
            await refresh_create_service_options()
            await refresh_create_type_and_channel()

        async def handle_create_service_change(_=None) -> None:  # pragma: no cover
            await refresh_create_type_and_channel()

        def handle_create_type_change(_=None) -> None:  # pragma: no cover
            update_create_status_state()

        async def handle_open_create_dialog() -> None:  # pragma: no cover
            create_env.value = _st.state.environment
            create_service.value = None
            create_type.value = None
            create_channel.value = None
            create_url.value = ""
            clear_create_token()
            create_statuses.value = list(status_options)
            create_include_payload.value = False
            await refresh_create_service_options()
            await refresh_create_type_and_channel()
            create_dialog.open()

        async def handle_create_callback() -> None:  # pragma: no cover
            # Disabled for the whole handler: a double click would fire a second POST and
            # the 409 it returns would report a failure for a callback that WAS created.
            create_submit_button.disable()
            try:
                environment = create_env.value
                service_id = create_service.value
                if not (environment and service_id):
                    ui.notify("Environment and service are required", color="red")
                    return
                error = validate_create(
                    url=create_url.value,
                    callback_type=create_type.value,
                    callback_channel=create_channel.value,
                    bearer_token=create_token.value,
                    notification_statuses=create_statuses.value or [],
                )
                if error:
                    ui.notify(error, color="red")
                    return
                payload = build_create_payload(
                    url=create_url.value,
                    callback_type=create_type.value,
                    callback_channel=create_channel.value,
                    bearer_token=create_token.value,
                    notification_statuses=create_statuses.value or [],
                    include_provider_payload=create_include_payload.value,
                )
                if not await ensure_admin_auth(environment, sync_label):
                    return
                api = await build_api_client(environment)
                try:
                    created = await api.create_service_callback(service_id, payload)
                except httpx.HTTPStatusError as exc:
                    if exc.response is not None and exc.response.status_code == 401:
                        handle_unauthorized(sync_label, environment)
                        return
                    ui.notify(f"Failed to create callback: {format_http_error(exc)}", color="red")
                    return
                except Exception as exc:
                    ui.notify(f"Error creating callback: {exc}", color="red")
                    return
                clear_create_token()
                await upsert_service_callbacks([created], environment, service_id)
                ui.notify("Callback created", color="green")
                create_dialog.close()
                await refresh_if_needed(render_table)
            finally:
                # Re-enabled on every path, including early validation returns and
                # exceptions. The saturated-service gate is re-applied by
                # refresh_create_type_and_channel the next time the dialog opens.
                create_submit_button.enable()

        create_env.on_value_change(handle_create_env_change)
        create_service.on_value_change(handle_create_service_change)
        create_type.on_value_change(handle_create_type_change)
        create_submit_button.on_click(handle_create_callback)

        # ------------------------------------------------------------------
        # Edit dialog
        # ------------------------------------------------------------------
        with ui.dialog() as edit_dialog, ui.card().classes("p-6 w-full max-w-3xl"):
            ui.label("Edit Service Callback").classes("text-md font-semibold")
            edit_summary_label = ui.label("")
            edit_url = ui.input(label="URL (https)").props("clearable").classes("w-full")
            edit_token = (
                ui.input(label="Bearer Token - leave blank to keep the existing token", password=True)
                .props("clearable")
                .classes("w-full")
            )
            ui.label("The API never returns the stored token, so it cannot be shown.").classes("text-xs text-gray-500")
            edit_update_statuses = ui.checkbox("Update notification statuses")
            edit_statuses = (
                ui.select(status_options, label="Notification Statuses", multiple=True)
                .props("clearable use-chips")
                .classes("w-full")
            )
            ui.label("Leave empty to receive all statuses.").classes("text-xs text-gray-500")
            edit_include_payload = ui.checkbox("Include provider payload")
            ui.label("Type and channel cannot be changed. Delete and recreate instead.").classes(
                "text-xs text-gray-500"
            )
            with ui.row().classes("gap-2"):
                edit_submit_button = ui.button("Update Callback", color="primary")
                edit_cancel_button = ui.button("Cancel", color="gray")

        def clear_edit_token() -> None:  # pragma: no cover
            """Blank the bearer token field on every dismissal path.

            A cancelled edit otherwise leaves a freshly typed credential live in the
            browser DOM and in this element's server-side props.
            """
            edit_token.value = ""

        def handle_edit_cancel() -> None:  # pragma: no cover
            clear_edit_token()
            edit_dialog.close()

        edit_cancel_button.on_click(handle_edit_cancel)
        # Quasar QDialog emits "hide" on every dismissal, including ESC and backdrop
        # click, which never reach the Cancel handler. args=[] because the handler needs
        # nothing from the event and there is no point serializing a DOM event for it.
        edit_dialog.on("hide", clear_edit_token, args=[])

        def resolve_selected_callback() -> dict[str, Any] | None:  # pragma: no cover
            return selected_callback if selected_callback.get("id") else None

        def update_edit_status_state() -> None:  # pragma: no cover
            callback = resolve_selected_callback() or {}
            checkbox_enabled, select_enabled = edit_statuses_control_state(
                callback.get("callback_type"), bool(edit_update_statuses.value)
            )
            if checkbox_enabled:
                edit_update_statuses.enable()
            else:
                edit_update_statuses.value = False
                edit_update_statuses.disable()
                edit_statuses.value = []
            if select_enabled:
                edit_statuses.enable()
            else:
                edit_statuses.disable()

        def update_edit_fields(callback: dict[str, Any] | None) -> None:  # pragma: no cover
            if not callback:
                edit_summary_label.text = "No callback selected."
                edit_url.value = ""
                clear_edit_token()
                edit_update_statuses.value = False
                edit_statuses.value = []
                edit_include_payload.value = False
                update_edit_status_state()
                return
            # Environment, service, ID, type, and channel are display-only: type and channel
            # are immutable by design, so changing either means delete and recreate.
            edit_summary_label.text = (
                f"Selected: {callback.get('callback_type')} / {callback.get('callback_channel')} "
                f"for {callback.get('_full_service_name')} ({callback.get('id')}) "
                f"in {callback.get('environment')}"
            )
            edit_url.value = callback.get("url") or ""
            clear_edit_token()
            edit_update_statuses.value = False
            edit_statuses.value = list(callback.get("notification_statuses_value") or [])
            edit_include_payload.value = bool(callback.get("include_provider_payload"))
            update_edit_status_state()

        def handle_edit_statuses_toggle(_=None) -> None:  # pragma: no cover
            update_edit_status_state()

        async def handle_open_edit_dialog() -> None:  # pragma: no cover
            callback = resolve_selected_callback()
            if not callback:
                ui.notify("Select a callback from the table first", color="red")
                return
            # Checked before opening: otherwise the user types a URL and a fresh bearer
            # token, submits, and only then learns the row is unusable.
            if not resolve_row_environment(callback):
                ui.notify(UNKNOWN_ENVIRONMENT_MESSAGE, color="red")
                return
            update_edit_fields(callback)
            edit_dialog.open()

        async def handle_update_callback() -> None:  # pragma: no cover
            # Disabled for the whole handler so a double click cannot fire two PUTs.
            edit_submit_button.disable()
            try:
                callback = resolve_selected_callback()
                if not callback:
                    ui.notify("Select a callback first", color="red")
                    return
                environment = resolve_row_environment(callback)
                if not environment:
                    ui.notify(UNKNOWN_ENVIRONMENT_MESSAGE, color="red")
                    return
                callback_id = callback.get("id")
                service_id = callback.get("service_id")
                if not (callback_id and service_id):
                    ui.notify("Selected callback is missing required details", color="red")
                    return
                callback_type = callback.get("callback_type")
                error = validate_update(
                    url=edit_url.value,
                    bearer_token=edit_token.value,
                    callback_type=callback_type,
                    notification_statuses=(edit_statuses.value or []) if edit_update_statuses.value else [],
                )
                if error:
                    ui.notify(error, color="red")
                    return
                payload = build_update_payload(
                    url=edit_url.value,
                    bearer_token=edit_token.value,
                    callback_type=callback_type,
                    notification_statuses=edit_statuses.value or [],
                    include_provider_payload=edit_include_payload.value,
                    update_statuses=bool(edit_update_statuses.value),
                )
                if not await ensure_admin_auth(environment, sync_label):
                    return
                api = await build_api_client(environment)
                try:
                    updated = await api.update_service_callback(service_id, callback_id, payload)
                except httpx.HTTPStatusError as exc:
                    if exc.response is not None and exc.response.status_code == 401:
                        handle_unauthorized(sync_label, environment)
                        return
                    ui.notify(f"Failed to update callback: {format_http_error(exc)}", color="red")
                    return
                except Exception as exc:
                    ui.notify(f"Error updating callback: {exc}", color="red")
                    return
                clear_edit_token()
                await upsert_service_callbacks([updated], environment, service_id)
                ui.notify("Callback updated", color="green")
                edit_dialog.close()
                await refresh_if_needed(render_table)
            finally:
                edit_submit_button.enable()

        edit_update_statuses.on_value_change(handle_edit_statuses_toggle)
        edit_submit_button.on_click(handle_update_callback)

        # ------------------------------------------------------------------
        # Delete confirmation dialog
        # ------------------------------------------------------------------
        with ui.dialog() as delete_dialog, ui.card().classes("p-6"):
            ui.label("Confirm Callback Deletion").classes("text-md font-semibold")
            delete_message = ui.label("")
            with ui.row().classes("gap-2"):
                confirm_delete_button = ui.button("Delete Callback", color="negative")
                delete_cancel_button = ui.button("Cancel", color="gray")

        def clear_pending_delete() -> None:  # pragma: no cover
            """Drop the staged deletion target.

            Nothing reachable today reads a stale ``pending_delete``, but leaving a live
            target behind is a trap for any future path that opens this dialog without
            going through ``handle_delete_request``.
            """
            pending_delete.clear()

        def handle_delete_cancel() -> None:  # pragma: no cover
            clear_pending_delete()
            delete_dialog.close()

        delete_cancel_button.on_click(handle_delete_cancel)
        # Quasar QDialog emits "hide" on every dismissal, including ESC and backdrop
        # click, which never reach the Cancel handler. args=[] because the handler needs
        # nothing from the event and there is no point serializing a DOM event for it.
        delete_dialog.on("hide", clear_pending_delete, args=[])

        async def handle_delete_request() -> None:  # pragma: no cover
            callback = resolve_selected_callback()
            if not callback:
                ui.notify("Select a callback to delete", color="red")
                return
            environment = resolve_row_environment(callback)
            if not environment:
                ui.notify(UNKNOWN_ENVIRONMENT_MESSAGE, color="red")
                return
            callback_id = callback.get("id")
            service_id = callback.get("service_id")
            if not (callback_id and service_id):
                ui.notify("Selected callback is missing required details", color="red")
                return
            delete_message.text = (
                f"Delete the {callback.get('callback_type')} / {callback.get('callback_channel')} "
                f"callback for {callback.get('_full_service_name')} ({callback.get('url')})? "
                "This cannot be undone."
            )
            pending_delete.clear()
            pending_delete.update({"environment": environment, "service_id": service_id, "callback_id": callback_id})
            delete_dialog.open()

        async def handle_confirm_delete() -> None:  # pragma: no cover
            # Disabled for the whole handler. The dialog closes before the await, so
            # without this a second confirm could be staged mid-flight and the 404 it
            # gets back would report a failure for a row that WAS deleted.
            confirm_delete_button.disable()
            try:
                delete_dialog.close()
                if not pending_delete:
                    return
                environment = pending_delete.get("environment")
                service_id = pending_delete.get("service_id")
                callback_id = pending_delete.get("callback_id")
                pending_delete.clear()
                if not (environment and service_id and callback_id):
                    return
                if not await ensure_admin_auth(environment, sync_label):
                    return
                # Cleared before the await, not after: the row is already gone as far as
                # the UI is concerned, so a second delete cannot be staged against it.
                selected_callback.clear()
                update_edit_fields(None)
                api = await build_api_client(environment)
                try:
                    await api.delete_service_callback(service_id, callback_id)
                except httpx.HTTPStatusError as exc:
                    if exc.response is not None and exc.response.status_code == 401:
                        handle_unauthorized(sync_label, environment)
                        return
                    ui.notify(f"Failed to delete callback: {format_http_error(exc)}", color="red")
                    return
                except Exception as exc:
                    ui.notify(f"Error deleting callback: {exc}", color="red")
                    return
                removed = await delete_service_callback(callback_id, environment)
                if removed:
                    ui.notify("Callback deleted", color="green")
                else:
                    ui.notify(
                        "Callback deleted, but cache is missing. Run sync to refresh.",
                        color="warning",
                    )
                await refresh_if_needed(render_table)
            finally:
                confirm_delete_button.enable()

        confirm_delete_button.on_click(handle_confirm_delete)

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
            selected_callback.clear()
            update_edit_fields(None)
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
                    "notification_statuses": format_statuses(cb.notification_statuses),
                    # Raw list for the edit dialog's multi-select. Absent from ``columns``,
                    # so it is not rendered as a table column and is excluded from the CSV
                    # export (``rows_to_csv`` filters by ``columns``). It is NOT hidden from
                    # the client: ``ui.table`` serializes every key of every row dict to the
                    # browser, so this pattern must never be used for sensitive values. It is
                    # safe here only because it duplicates the already-displayed
                    # ``notification_statuses`` string.
                    "notification_statuses_value": cb.notification_statuses or [],
                    "include_provider_payload": cb.include_provider_payload,
                    "created_at": cb.created_at,
                    "updated_at": cb.updated_at,
                }
                for cb in callbacks
            ]
            with ui.row().classes("w-full items-center"):
                ui.button("Sync Service Callbacks", on_click=handle_sync_callbacks)
                ui.button("Edit Selected Callback", on_click=handle_open_edit_dialog, color="primary")
                ui.button("Delete Selected Callback", on_click=handle_delete_request, color="negative")
                ui.button("Add Callback", on_click=handle_open_create_dialog, color="green")
                ui.space()
                add_export_button(table_rows, columns, "service_callbacks.csv")

            def handle_row_select(e) -> None:  # pragma: no cover
                if e.selection:
                    clicked_key = e.selection[0].get("_row_key")
                    if clicked_key == selected_callback.get("_row_key"):
                        selected_callback.clear()
                        table.selected = []
                        update_edit_fields(None)
                        return
                    selected_callback.clear()
                    selected_callback.update(e.selection[0])
                else:
                    selected_callback.clear()
                update_edit_fields(resolve_selected_callback())

            table = ui.table(
                columns=make_sortable(columns),
                rows=table_rows,
                selection="single",
                on_select=handle_row_select,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=_row_key").classes("w-full")
            add_copyable_slots(table, table_rows)
            add_service_context_menu(table, column_name="service_id")

        service_select.on_value_change(lambda _: render_table.refresh())
        callback_search.on_value_change(handle_search_event)
        await render_table()
