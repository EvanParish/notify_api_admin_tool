from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import httpx
from nicegui import ui

from app.repository import (
    add_local_key,
    list_api_keys,
    list_services,
    mark_api_key_revoked,
    update_api_key_expiry,
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


def _parse_filter_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value.split("T", 1)[0])
    except ValueError:
        return None


def _matches_expiry_range(expiry_value: Optional[str], start_date: Optional[date], end_date: Optional[date]) -> bool:
    if not start_date and not end_date:
        return True
    expiry_date = _parse_filter_date(expiry_value)
    if not expiry_date:
        return False
    if start_date and expiry_date < start_date:
        return False
    if end_date and expiry_date > end_date:
        return False
    return True


def _extract_api_key_secret(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Unexpected API response when creating API key")
    data = payload.get("data")
    if isinstance(data, str) and data.strip():
        return data
    raise ValueError("API key secret missing in response; expected data string")


@ui.page("/api-keys")
async def api_keys_page() -> None:
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

    async def page_sync_api_keys(
        environment: str | None = None,
        service_ids: list[str] | None = None,
    ):  # pragma: no cover
        envs = [environment] if environment else None
        method_kwargs = {"sync_api_keys": {"service_ids": service_ids}} if service_ids else None
        await handle_entity_sync(
            ["sync_api_keys"],
            status_badge,
            sync_label,
            "API keys",
            pre_sync=["sync_services"],
            environments=envs,
            method_kwargs=method_kwargs,
        )

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("API Keys").classes("text-lg font-semibold")

        with ui.dialog() as create_dialog, ui.card().classes("p-6 w-full max-w-3xl"):
            ui.label("Create Personal API Key").classes("text-md font-semibold")
            create_env = ui.select(
                {env: env.title() for env in _st.config.api_hosts},
                value=_st.state.environment,
                label="Environment",
            ).classes("w-full md:w-1/2")
            create_service = (
                ui.select({}, label="Service", with_input=True).props("clearable").classes("w-full md:w-1/2")
            )
            create_name = ui.input(label="Key Name").props("clearable").classes("w-full md:w-1/2")
            create_type = ui.select(
                {"normal": "Normal", "team": "Team", "test": "Test"},
                value="normal",
                label="Key Type",
            ).classes("w-full md:w-1/2")
            with ui.row().classes("gap-2"):
                submit_button = ui.button("Create API Key", color="green")
                ui.button("Cancel", on_click=create_dialog.close, color="gray")

        async def refresh_create_service_options() -> None:  # pragma: no cover
            options = {svc.id: format_service_label(svc) for svc in await list_services(create_env.value)}
            create_service.set_options(options)
            if create_service.value not in options:
                create_service.value = None

        async def handle_create_env_change(_=None) -> None:  # pragma: no cover
            await refresh_create_service_options()

        async def handle_create_api_key() -> None:  # pragma: no cover
            environment = create_env.value
            service_id = create_service.value
            name = (create_name.value or "").strip()
            key_type = create_type.value
            if not (environment and service_id and name and key_type):
                ui.notify(
                    "Environment, service, name, and type are required",
                    color="red",
                )
                return
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)
            try:
                payload = await api.create_api_key(service_id, name, key_type)
            except httpx.HTTPStatusError as exc:
                if exc.response and exc.response.status_code == 401:
                    handle_unauthorized(sync_label, environment)
                    return
                raise
            try:
                secret = _extract_api_key_secret(payload)
            except ValueError as exc:
                ui.notify(str(exc), color="red")
                return
            data = payload.get("data") if isinstance(payload, dict) else None
            stored_name = name
            stored_type = (data.get("key_type") if isinstance(data, dict) else None) or key_type
            await add_local_key(
                _st.encryption,
                service_id,
                environment,
                stored_name,
                secret,
                stored_type,
            )
            ui.notify("API key created and stored locally", color="green")
            from app.ui.pages.settings_page import render_local_keys

            await refresh_if_needed(render_local_keys)
            create_dialog.close()
            await page_sync_api_keys(environment, service_ids=[service_id])
            await refresh_if_needed(render_table)

        async def handle_open_create_dialog() -> None:  # pragma: no cover
            create_env.value = _st.state.environment
            await refresh_create_service_options()
            create_dialog.open()

        create_env.on_value_change(handle_create_env_change)
        submit_button.on_click(handle_create_api_key)

        selected_api_key: Dict[str, Any] = {}
        pending_revoke: Dict[str, str] = {}

        with ui.dialog() as manage_dialog, ui.card().classes("p-6 w-full max-w-lg"):
            ui.label("Manage API Key").classes("text-md font-semibold")
            selected_key_label = ui.label("")
            expiry_input = ui.input(label="Expiry Date").props("clearable type=date")
            with ui.row().classes("gap-2"):
                update_button = ui.button("Update Expiry", color="primary")
                revoke_button = ui.button("Revoke Key", color="negative")
                ui.button("Close", on_click=manage_dialog.close, color="gray")

        with ui.dialog() as revoke_dialog, ui.card():
            ui.label("Confirm API Key Revocation").classes("text-md font-semibold")
            revoke_message = ui.label("")
            with ui.row().classes("gap-2"):
                confirm_revoke_button = ui.button("Revoke Key", color="negative")
                ui.button("Cancel", on_click=revoke_dialog.close, color="gray")

        def resolve_selected_key() -> Optional[Dict[str, Any]]:  # pragma: no cover
            return selected_api_key if selected_api_key.get("id") else None

        def resolve_selected_environment(
            key: Dict[str, Any],
        ) -> Optional[str]:  # pragma: no cover
            env_value = key.get("environment_value") or key.get("environment")
            if not env_value or env_value == "unknown":
                return None
            return env_value

        def update_manage_fields(
            key: Optional[Dict[str, Any]],
        ) -> None:  # pragma: no cover
            if not key:
                selected_key_label.text = "No API key selected."
                expiry_input.value = ""
                return
            key_id = key.get("id")
            key_name = key.get("name") or ""
            service_id = key.get("service_id") or ""
            selected_key_label.text = f"Selected: {key_name} ({key_id}) - Service {service_id}"
            expiry_value = key.get("expiry_date") or ""
            expiry_input.value = expiry_value.split("T", 1)[0] if expiry_value else ""

        async def handle_open_manage_dialog() -> None:  # pragma: no cover
            key = resolve_selected_key()
            if not key:
                ui.notify("Select an API key from the table first", color="red")
                return
            update_manage_fields(key)
            manage_dialog.open()

        async def handle_update_expiry() -> None:  # pragma: no cover
            key = resolve_selected_key()
            expiry_date = (expiry_input.value or "").strip()
            if not key or not expiry_date:
                ui.notify("Select an API key and expiry date", color="red")
                return
            environment = resolve_selected_environment(key)
            service_id = key.get("service_id")
            key_id = key.get("id")
            if not (environment and service_id and key_id):
                ui.notify("Selected API key is missing required details", color="red")
                return
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)
            try:
                await api.update_api_key_expiry(service_id, key_id, expiry_date)
            except httpx.HTTPStatusError as exc:
                if exc.response and exc.response.status_code == 401:
                    handle_unauthorized(sync_label, environment)
                    return
                ui.notify(str(exc), color="red")
                return
            updated = await update_api_key_expiry(
                service_id,
                key_id,
                expiry_date,
                environment=environment,
            )
            if updated:
                ui.notify("API key expiry updated", color="green")
            else:
                ui.notify(
                    "API key updated, but cache is missing. Run sync to refresh.",
                    color="warning",
                )
            selected_api_key["expiry_date"] = expiry_date
            update_manage_fields(resolve_selected_key())
            manage_dialog.close()
            await refresh_if_needed(render_table)

        async def handle_revoke_request() -> None:  # pragma: no cover
            key = resolve_selected_key()
            if not key:
                ui.notify("Select an API key to revoke", color="red")
                return
            environment = resolve_selected_environment(key)
            service_id = key.get("service_id")
            key_id = key.get("id")
            if not (environment and service_id and key_id):
                ui.notify("Selected API key is missing required details", color="red")
                return
            key_name = key.get("name") or ""
            revoke_message.text = f"Revoke API key {key_name} ({key_id})?"
            pending_revoke.clear()
            pending_revoke.update(
                {
                    "environment": environment,
                    "service_id": service_id,
                    "key_id": key_id,
                }
            )
            revoke_dialog.open()

        async def handle_confirm_revoke() -> None:  # pragma: no cover
            revoke_dialog.close()
            if not pending_revoke:
                return
            environment = pending_revoke.get("environment")
            service_id = pending_revoke.get("service_id")
            key_id = pending_revoke.get("key_id")
            pending_revoke.clear()
            if not (environment and service_id and key_id):
                return
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)
            try:
                await api.revoke_api_key(service_id, key_id)
            except httpx.HTTPStatusError as exc:
                if exc.response and exc.response.status_code == 401:
                    handle_unauthorized(sync_label, environment)
                    return
                ui.notify(str(exc), color="red")
                return
            updated = await mark_api_key_revoked(
                service_id,
                key_id,
                environment=environment,
            )
            if updated:
                ui.notify("API key revoked", color="green")
            else:
                ui.notify(
                    "API key revoked, but cache is missing. Run sync to refresh.",
                    color="warning",
                )
            selected_api_key["revoked"] = True
            manage_dialog.close()
            await refresh_if_needed(render_table)

        update_button.on_click(handle_update_expiry)
        revoke_button.on_click(handle_revoke_request)
        confirm_revoke_button.on_click(handle_confirm_revoke)

        search_input = (
            ui.input(label="Search by ID, Service ID, Service Name, or Name")
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
        with ui.row().classes("gap-2"):
            expires_from = ui.input(label="Expires from").props("clearable type=date")
            expires_to = ui.input(label="Expires to").props("clearable type=date")

        async def handle_sync_keys() -> None:  # pragma: no cover
            await page_sync_api_keys()
            render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            selected_api_key.clear()
            update_manage_fields(None)
            selected_services = service_select.value or []
            start_date = _parse_filter_date(expires_from.value)
            end_date = _parse_filter_date(expires_to.value)
            search_term = (search_input.value or "").strip().lower()
            keys = await list_api_keys(selected_services or None, environment=get_view_environment())
            columns = [
                {"name": "id", "label": "ID", "field": "id"},
                {"name": "environment", "label": "Environment", "field": "environment"},
                {"name": "service_id", "label": "Service", "field": "service_name"},
                {"name": "name", "label": "Name", "field": "name"},
                {"name": "key_type", "label": "Type", "field": "key_type"},
                {"name": "expiry_date", "label": "Expires", "field": "expiry_date"},
                {"name": "revoked", "label": "Revoked", "field": "revoked"},
                {"name": "created_by", "label": "Created By", "field": "created_by"},
                {"name": "created_at", "label": "Created", "field": "created_at"},
                {"name": "version", "label": "Version", "field": "version"},
            ]
            table_rows: List[Dict[str, Any]] = [
                {
                    "_row_key": make_row_key(key.id, key.environment),
                    "id": key.id,
                    "environment": format_environment(key.environment),
                    "environment_value": key.environment,
                    "service_id": key.service_id,
                    "service_name": resolve_service_name(key.service_id, service_name_map),
                    "_full_service_name": service_name_map.get(key.service_id or "", key.service_id or ""),
                    "name": key.name,
                    "key_type": key.key_type,
                    "expiry_date": key.expiry_date,
                    "created_by": key.created_by,
                    "created_at": key.created_at[:10] if key.created_at else None,
                    "revoked": key.revoked,
                    "version": key.version,
                }
                for key in keys
                if _matches_expiry_range(key.expiry_date, start_date, end_date)
                and (
                    not search_term
                    or search_term in (key.id or "").lower()
                    or search_term in (key.service_id or "").lower()
                    or search_term in (service_name_map.get(key.service_id, "")).lower()
                    or search_term in (key.name or "").lower()
                )
            ]
            with ui.row().classes("w-full items-center"):
                ui.button("Sync API Keys", on_click=handle_sync_keys)
                ui.button(
                    "Manage Selected Key",
                    on_click=handle_open_manage_dialog,
                    color="primary",
                )
                create_button = ui.button("Create Personal API Key", color="green")
                create_button.on_click(handle_open_create_dialog)
                ui.space()
                add_export_button(table_rows, columns, "api_keys.csv")

            def handle_row_select(e) -> None:  # pragma: no cover
                if e.selection:
                    clicked_key = e.selection[0].get("_row_key")
                    current_key = selected_api_key.get("_row_key")
                    if clicked_key == current_key:
                        selected_api_key.clear()
                        table.selected = []
                        update_manage_fields(None)
                        return
                    selected_api_key.clear()
                    selected_api_key.update(e.selection[0])
                else:
                    selected_api_key.clear()
                update_manage_fields(resolve_selected_key())

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

        search_input.on_value_change(lambda _: render_table.refresh())
        service_select.on_value_change(lambda _: render_table.refresh())
        expires_from.on_value_change(lambda _: render_table.refresh())
        expires_to.on_value_change(lambda _: render_table.refresh())
        await render_table()
