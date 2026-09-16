from __future__ import annotations

from typing import Any

import httpx
from nicegui import ui

from app.repository import (
    UNASSIGNED_SERVICE_FILTER,
    list_inbound_numbers,
    list_provider_details,
    list_services,
    update_inbound_number,
)
from app.ui import state as _st
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
    set_options_preserving,
    sms_provider_identifier_options,
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

# Two spellings on purpose.  The filter option must read as a *control* among options
# shaped "Name (env)"; the table cell must read as *field empty*, matching this repo's
# existing "(not synced)" convention.  Do not collapse them into one string.
UNASSIGNED_CELL_LABEL = "(unassigned)"
UNASSIGNED_FILTER_LABEL = "— Unassigned —"


def build_service_filter_options(services) -> dict[str, str]:
    """Filter-by-Service options with the unassigned sentinel pinned first."""
    return {
        UNASSIGNED_SERVICE_FILTER: UNASSIGNED_FILTER_LABEL,
        **{svc.id: format_service_label(svc) for svc in services},
    }


def full_service_name(service_id: str | None, name_map: dict[str, str]) -> str | None:
    """Untruncated Service-column value for an inbound number.

    Returns the unassigned label when there is no service, the cached name when known,
    or ``None`` on a cache miss so ``with_option`` can append its "(not synced)" suffix.

    Deliberately diverges from :func:`service_cell_label` on a cache miss only: this
    feeds the edit dialog and the clipboard, which need the ``None`` as a signal, while
    the table cell must render something and falls back to the raw ID.
    """
    if not service_id:
        return UNASSIGNED_CELL_LABEL
    return name_map.get(service_id)


def service_cell_label(service_id: str | None, name_map: dict[str, str]) -> str:
    """Truncated Service-column text.  Only a missing service reads as unassigned.

    Gates on ``service_id`` rather than on ``resolve_service_name``'s return value,
    because that helper also returns "" for a cached service with an empty name --
    treating that as unassigned would state something false about an assigned number.

    Deliberately diverges from :func:`full_service_name` on a cache miss only: that one
    returns ``None``, this one falls back to the raw ID because a cell must render
    something.  Keep the two agreeing on every other input.
    """
    if not service_id:
        return UNASSIGNED_CELL_LABEL
    return resolve_service_name(service_id, name_map)


def matches_inbound_search(number, query: str, name_map: dict[str, str]) -> bool:
    """Free-text match for the inbound numbers table.

    Matches the underlying model values, not the rendered row -- the rendered service
    name is truncated to 21 characters, so matching it would break search for any
    longer name.  The unassigned label is matched only for rows that genuinely have
    no service, so a service actually named "...unassigned..." matches via its name
    instead.
    """
    if not query:
        return True
    return (
        query in (number.number or "").lower()
        or query in (number.id or "").lower()
        or query in (number.service_id or "").lower()
        or query in (name_map.get(number.service_id or "", "")).lower()
        or (not number.service_id and query in UNASSIGNED_CELL_LABEL.lower())
    )


@ui.page("/inbound-numbers", response_timeout=PAGE_RESPONSE_TIMEOUT)
async def inbound_numbers_page() -> None:
    inbound_search_query = ""

    async def refresh_service_options() -> None:  # pragma: no cover
        # Not set_options_preserving: this select is multiple=True, so its value is a
        # list, and with_option's `value in options` raises TypeError on an unhashable
        # list.  Keep the set_options + filter-the-list form.
        options = build_service_filter_options(await list_services(get_view_environment()))
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
            create_number = ui.input(label="Number (e.g., +12025551212)").props("clearable").classes("w-full")
            create_provider = ui.select({}, label="Provider", with_input=True).classes("w-full")
            create_provider_hint = ui.label("No SMS providers cached for this environment. Run a sync first.").classes(
                "text-xs text-red-500"
            )
            create_service = ui.select({}, label="Service", with_input=True).classes("w-full")
            create_service_hint = ui.label("No services cached for this environment. Run a sync first.").classes(
                "text-xs text-red-500"
            )
            create_active = ui.checkbox("Active", value=True)
            create_self_managed = ui.checkbox("Self Managed")
            create_auth_parameter = ui.input(label="Auth Parameter").props("clearable").classes("w-full")
            create_url_endpoint = ui.input(label="URL Endpoint").props("clearable").classes("w-full")
            ui.label("URL Endpoint is required when Self Managed is checked").classes("text-xs text-gray-500")
            with ui.row().classes("gap-2"):
                create_submit_button = ui.button("Create Inbound Number", color="green")
                ui.button("Cancel", on_click=create_dialog.close, color="gray")

        async def refresh_create_provider_options() -> None:  # pragma: no cover
            options = sms_provider_identifier_options(await list_provider_details(create_env.value))
            create_provider.set_options(options)
            if create_provider.value not in options:
                create_provider.value = None
            create_provider_hint.set_visibility(not options)

        async def refresh_create_service_options() -> None:  # pragma: no cover
            options = {svc.id: format_service_label(svc) for svc in await list_services(create_env.value)}
            create_service.set_options(options)
            if create_service.value not in options:
                create_service.value = None
            create_service_hint.set_visibility(not options)

        async def handle_create_env_change(_=None) -> None:  # pragma: no cover
            await refresh_create_provider_options()
            await refresh_create_service_options()

        async def handle_create_inbound_number() -> None:  # pragma: no cover
            environment = create_env.value
            number = (create_number.value or "").strip()
            provider = create_provider.value
            service_id = create_service.value or None
            active = create_active.value
            self_managed = create_self_managed.value
            auth_parameter = (create_auth_parameter.value or "").strip() or None
            url_endpoint = (create_url_endpoint.value or "").strip() or None
            # Service is required by this tool but not by the API, whose create schema
            # only requires number and provider.  Numbers created elsewhere can still
            # arrive unassigned, which is why the table keeps its "(unassigned)" state.
            if not (environment and number and provider and service_id):
                ui.notify(
                    "Environment, number, provider, and service are required",
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
                    service_id=service_id,
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
            create_provider.value = None
            create_service.value = None
            create_active.value = True
            create_self_managed.value = False
            create_auth_parameter.value = ""
            create_url_endpoint.value = ""
            await refresh_create_provider_options()
            await refresh_create_service_options()
            create_dialog.open()

        create_env.on_value_change(handle_create_env_change)
        create_submit_button.on_click(handle_create_inbound_number)

        # Edit Inbound Number dialog
        selected_number: dict[str, Any] = {}

        with ui.dialog() as edit_dialog, ui.card().classes("p-6 w-full max-w-lg"):
            ui.label("Edit Inbound Number").classes("text-md font-semibold")
            selected_number_label = ui.label("")
            # None of these inputs are clearable.  handle_update_inbound_number maps ""
            # to None and both update paths skip a None, so clearing a field would
            # report success while changing nothing.  There is no blank state to submit
            # either: the API's update schema sets additionalProperties False and types
            # every property as a string, so a null is rejected outright.
            edit_number = ui.input(label="Number").classes("w-full")
            edit_provider = ui.select({}, label="Provider", with_input=True).classes("w-full")
            # Deliberately NOT clearable: the API has no unassign path for inbound
            # numbers, so the UI must not offer a state it cannot submit.
            edit_service = ui.select({}, label="Service", with_input=True).classes("w-full")
            edit_active = ui.checkbox("Active")
            edit_self_managed = ui.checkbox("Self Managed")
            edit_auth_parameter = ui.input(label="Auth Parameter").classes("w-full")
            edit_url_endpoint = ui.input(label="URL Endpoint").classes("w-full")
            ui.label("URL Endpoint is required when Self Managed is checked").classes("text-xs text-gray-500")
            ui.label(
                "Fields cannot be blanked here; the API treats an empty field as unchanged. "
                "A service cannot be unassigned. Close without updating to cancel a change."
            ).classes("text-xs text-gray-500")
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
                edit_provider.value = None
                edit_service.value = None
                edit_active.value = True
                edit_self_managed.value = False
                edit_auth_parameter.value = ""
                edit_url_endpoint.value = ""
                return
            num_id = num.get("id")
            number_val = num.get("number") or ""
            selected_number_label.text = f"Selected: {number_val} ({num_id})"
            edit_number.value = number_val
            edit_active.value = bool(num.get("active"))
            edit_self_managed.value = bool(num.get("self_managed"))
            edit_auth_parameter.value = num.get("auth_parameter") or ""
            edit_url_endpoint.value = num.get("url_endpoint") or ""

        async def handle_open_edit_dialog() -> None:  # pragma: no cover
            num = resolve_selected_number()
            if not num:
                ui.notify("Select an inbound number from the table first", color="red")
                return
            environment = resolve_selected_environment(num)
            provider_options: dict[str, str] = {}
            service_options_for_edit: dict[str, str] = {}
            if environment:
                provider_options = sms_provider_identifier_options(await list_provider_details(environment))
                service_options_for_edit = {
                    svc.id: format_service_label(svc) for svc in await list_services(environment)
                }
            set_options_preserving(edit_provider, provider_options, num.get("provider"))
            set_options_preserving(
                edit_service,
                service_options_for_edit,
                num.get("service_id"),
                num.get("_full_service_name"),
            )
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
                ui.notify("Selected inbound number is missing required details", color="red")
                return
            number_val = (edit_number.value or "").strip() or None
            provider = edit_provider.value or None
            service_id = edit_service.value or None
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
                    service_id=service_id,
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
                service_id=service_id,
                environment=environment,
            )
            if updated:
                ui.notify("Inbound number updated", color="green")
            else:
                ui.notify(
                    "Inbound number updated, but cache is missing. Run sync to refresh.",
                    color="warning",
                )
            # No local write-back here.  render_table opens with selected_number.clear()
            # followed by update_edit_fields(None), so any value written to the dict or
            # to the dialog inputs at this point is discarded before it can be read.
            edit_dialog.close()
            await refresh_if_needed(render_table)

        edit_update_button.on_click(handle_update_inbound_number)

        filter_row = ui.row().classes("gap-2 w-full")
        with filter_row:
            inbound_search = (
                ui.input(label="Search by Number, ID, Service ID, or Service Name")
                .props("clearable")
                .classes("w-full md:w-1/2")
            )
        _services = await list_services(get_view_environment())
        service_options = build_service_filter_options(_services)
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
            service_name_map = build_service_name_map(await list_services(get_view_environment()))
            selected_services = service_select.value or []
            numbers = await list_inbound_numbers(selected_services or None, environment=get_view_environment())
            if inbound_search_query:
                numbers = [n for n in numbers if matches_inbound_search(n, inbound_search_query, service_name_map)]
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
                {"name": "service_id", "label": "Service", "field": "service_name"},
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
                    "service_name": service_cell_label(n.service_id, service_name_map),
                    # A None here cannot produce an empty clipboard: COPYABLE_CELL_SLOT
                    # coalesces `_full_<field> || props.value`, so a null falls through
                    # to the rendered cell text.
                    "_full_service_name": full_service_name(n.service_id, service_name_map),
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
                # The "(unassigned)" literal reaching the CSV is deliberate: the export
                # should match what is on screen.
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
            add_service_context_menu(table, column_name="service_id")

        service_select.on_value_change(lambda _: render_table.refresh())
        inbound_search.on_value_change(handle_inbound_search_event)
        await render_table()
