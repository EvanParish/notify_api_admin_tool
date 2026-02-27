from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import httpx
from nicegui import ui
from nicegui.elements.input import Input

from app.repository import (
    add_local_key,
    get_secure_setting,
    get_setting,
    list_api_keys,
    list_local_keys,
    list_services,
    list_templates,
    list_users,
    mark_api_key_revoked,
    resolve_local_key,
    set_secure_setting,
    set_setting,
    update_api_key_expiry,
)
from app.ui import state as _st
from app.ui.email_helpers import (
    UUID_SECRET_TYPE,
    _build_key_email,
    _normalize_email_env,
    _select_latest_key,
)
from app.ui.helpers import (
    add_copyable_slots,
    copy_to_clipboard,
    find_missing_personalisation,
    format_environment,
    format_service_label,
    make_sortable,
    parse_recipients,
    refresh_if_needed,
)
from app.ui.shell import (
    build_shell,
    ensure_theme_preference,
)
from app.ui.state import (
    build_api_client,
    ensure_admin_auth,
    get_view_environment,
    handle_unauthorized,
    refresh_status_badge,
    safe_notify,
)
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync
from app.utils import extract_placeholders, validate_recipient

# Ensure `import main` resolves to this module even when run as __main__.
sys.modules.setdefault("main", sys.modules[__name__])

logger = logging.getLogger(__name__)

ui.add_head_html(
    """
        <meta name="color-scheme" content="dark light">
        <style>
            html, body, #q-app, .q-layout, .q-page-container {
                background-color: #0b0f14 !important;
                color-scheme: dark;
            }
            body.body--light, body.body--light #q-app, body.body--light .q-layout, body.body--light .q-page-container {
                background-color: #f8fafc !important;
                color-scheme: light;
            }
            body.body--dark, body.body--dark #q-app, body.body--dark .q-layout, body.body--dark .q-page-container {
                background-color: #0b0f14 !important;
                color-scheme: dark;
            }
        </style>
        <script>
            document.documentElement.style.backgroundColor = '#0b0f14';
            document.documentElement.classList.add('body--dark');
            if (document.body) {
                document.body.style.backgroundColor = '#0b0f14';
                document.body.classList.add('body--dark');
            } else {
                document.addEventListener('DOMContentLoaded', () => {
                    document.body.style.backgroundColor = '#0b0f14';
                    document.body.classList.add('body--dark');
                });
            }
            window.copyTableCellText = async (text) => {
                const value = String(text ?? '');
                try {
                    if (navigator?.clipboard?.writeText) {
                        await navigator.clipboard.writeText(value);
                        return true;
                    }
                } catch (error) {
                    console.warn('Clipboard API copy failed; using fallback.', error);
                }
                if (!document?.body) return false;
                const textarea = document.createElement('textarea');
                textarea.value = value;
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                try {
                    return document.execCommand('copy');
                } finally {
                    document.body.removeChild(textarea);
                }
            };
        </script>
        """
)

import app.ui.pages  # noqa: F401, E402 — triggers @ui.page registration


def _parse_filter_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value.split("T", 1)[0])
    except ValueError:
        return None


def _matches_expiry_range(
    expiry_value: Optional[str], start_date: Optional[date], end_date: Optional[date]
) -> bool:
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

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=handle_view_env_change
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    async def page_sync_api_keys():  # pragma: no cover
        await handle_entity_sync(
            ["sync_api_keys"],
            status_badge,
            sync_label,
            "API keys",
            pre_sync=["sync_services"],
        )

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("API Keys").classes("text-lg font-semibold")

        create_button = ui.button("Create Personal API Key", color="green")

        with ui.dialog() as create_dialog, ui.card().classes("p-6 w-full max-w-3xl"):
            ui.label("Create Personal API Key").classes("text-md font-semibold")
            create_env = ui.select(
                {env: env.title() for env in _st.config.api_hosts},
                value=_st.state.environment,
                label="Environment",
            ).classes("w-full md:w-1/2")
            create_service = (
                ui.select({}, label="Service", with_input=True)
                .props("clearable")
                .classes("w-full md:w-1/2")
            )
            create_name = (
                ui.input(label="Key Name").props("clearable").classes("w-full md:w-1/2")
            )
            create_type = ui.select(
                {"normal": "Normal", "team": "Team", "test": "Test"},
                value="normal",
                label="Key Type",
            ).classes("w-full md:w-1/2")
            with ui.row().classes("gap-2"):
                submit_button = ui.button("Create API Key", color="green")
                ui.button("Cancel", on_click=create_dialog.close, color="gray")

        async def refresh_create_service_options() -> None:  # pragma: no cover
            options = {
                svc.id: format_service_label(svc)
                for svc in await list_services(create_env.value)
            }
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
            stored_type = (
                data.get("key_type") if isinstance(data, dict) else None
            ) or key_type
            await add_local_key(
                _st.encryption,
                service_id,
                environment,
                stored_name,
                secret,
                stored_type,
            )
            ui.notify("API key created and stored locally", color="green")
            await refresh_if_needed(render_local_keys)
            create_dialog.close()

        async def handle_open_create_dialog() -> None:  # pragma: no cover
            create_env.value = _st.state.environment
            await refresh_create_service_options()
            create_dialog.open()

        create_env.on_value_change(handle_create_env_change)
        create_button.on_click(handle_open_create_dialog)
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
            selected_key_label.text = (
                f"Selected: {key_name} ({key_id}) - Service {service_id}"
            )
            expiry_value = key.get("expiry_date") or ""
            expiry_input.value = expiry_value.split("T", 1)[0] if expiry_value else ""

        def handle_table_selection(e) -> None:  # pragma: no cover
            selected_api_key.clear()
            if e.selection:
                selected_api_key.update(e.selection[0])
            update_manage_fields(resolve_selected_key())

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
            await refresh_if_needed(render_table)

        update_button.on_click(handle_update_expiry)
        revoke_button.on_click(handle_revoke_request)
        confirm_revoke_button.on_click(handle_confirm_revoke)

        search_input = (
            ui.input(label="Search by ID, Service ID, or Name")
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
        expires_from = ui.input(label="Expires from").props("clearable type=date")
        expires_to = ui.input(label="Expires to").props("clearable type=date")

        async def handle_sync_keys() -> None:  # pragma: no cover
            await page_sync_api_keys()
            render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:
            selected_api_key.clear()
            update_manage_fields(None)
            selected_service = service_select.value
            start_date = _parse_filter_date(expires_from.value)
            end_date = _parse_filter_date(expires_to.value)
            search_term = (search_input.value or "").strip().lower()
            keys = await list_api_keys(
                selected_service, environment=get_view_environment()
            )
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": key.id,
                    "environment": format_environment(key.environment),
                    "environment_value": key.environment,
                    "service_id": key.service_id,
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
                    or search_term in (key.name or "").lower()
                )
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
                        {
                            "name": "service_id",
                            "label": "Service ID",
                            "field": "service_id",
                        },
                        {"name": "name", "label": "Name", "field": "name"},
                        {"name": "key_type", "label": "Type", "field": "key_type"},
                        {
                            "name": "expiry_date",
                            "label": "Expires",
                            "field": "expiry_date",
                        },
                        {
                            "name": "created_by",
                            "label": "Created By",
                            "field": "created_by",
                        },
                        {
                            "name": "created_at",
                            "label": "Created",
                            "field": "created_at",
                        },
                        {"name": "revoked", "label": "Revoked", "field": "revoked"},
                        {"name": "version", "label": "Version", "field": "version"},
                    ]
                ),
                rows=table_rows,
                selection="single",
                on_select=handle_table_selection,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=id").classes("w-full")
            add_copyable_slots(table, table_rows)

        search_input.on_value_change(lambda _: render_table.refresh())
        service_select.on_value_change(lambda _: render_table.refresh())
        expires_from.on_value_change(lambda _: render_table.refresh())
        expires_to.on_value_change(lambda _: render_table.refresh())
        with ui.row().classes("gap-2"):
            ui.button("Sync API Keys", on_click=handle_sync_keys)
            ui.button(
                "Manage Selected Key",
                on_click=handle_open_manage_dialog,
                color="primary",
            )
        await render_table()


@ui.page("/api-key-service")
async def api_key_emails_page() -> None:
    status_badge, sync_label, refresh_button, dark_mode = build_shell()
    await ensure_theme_preference(dark_mode)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    service_lookup: Dict[str, Any] = {}

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("API Key Email Generator").classes("text-lg font-semibold")
        ui.markdown(
            "Generate a new API key and copy the email-ready content for sending."
        )

        with ui.card().classes("p-6 w-full"):
            env_options = {env: env.title() for env in _st.config.api_hosts}
            env_select = ui.select(
                env_options, value=_st.state.environment, label="Environment"
            ).classes("w-full md:w-1/2")
            service_select = (
                ui.select({}, label="Service", with_input=True)
                .props("clearable")
                .classes("w-full md:w-1/2")
            )
            key_prefix = (
                ui.input(label="Key Name Prefix")
                .props("clearable")
                .classes("w-full md:w-1/2")
            )
            with ui.row().classes("gap-4 items-center"):
                uuid_checkbox = ui.checkbox("Vets-api (UUID) key", value=False)
                test_checkbox = ui.checkbox("Test key (non-sending)", value=False)
            key_name_preview = ui.label("Generated key name will appear here.").classes(
                "text-sm text-gray-600 dark:text-slate-300"
            )
            key_name_conflict = ui.label("").classes(
                "text-sm text-red-600 dark:text-red-400"
            )
            key_name_conflict.visible = False
            generate_button = ui.button("Generate API Key Email", color="green")

        with ui.card().classes("p-6 w-full"):
            ui.label("Generated Email Content").classes("text-md font-semibold")
            ui.label(
                "This generated email content will NOT be shown again after you leave this page. "
                "Copy it now."
            ).classes("text-sm text-red-600 dark:text-red-400")
            output_area = (
                ui.textarea(label="Email Content").props("readonly").classes("w-full")
            )
            copy_button = ui.button("Copy Email Content")

    async def refresh_service_options() -> None:
        env_value = env_select.value
        services = await list_services(env_value) if env_value else []
        service_lookup.clear()
        service_lookup.update({svc.id: svc for svc in services})
        options = {svc.id: format_service_label(svc) for svc in services}
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None

    def build_key_name() -> str:  # pragma: no cover
        environment = env_select.value
        prefix = (key_prefix.value or "").strip().lower()
        if not environment or not prefix:
            return ""
        env_token = _normalize_email_env(environment).lower()
        uuid_part = "uuid-" if uuid_checkbox.value else ""
        test_part = "test-" if test_checkbox.value else ""
        return f"{env_token}-{prefix}-{uuid_part}{test_part}key"

    async def update_key_name_preview() -> None:  # pragma: no cover
        key_name = build_key_name()
        if key_name:
            key_name_preview.text = f"Generated key name: {key_name}"
        else:
            key_name_preview.text = "Generated key name will appear here."
        service_id = service_select.value
        environment = env_select.value
        if key_name and service_id and environment:
            existing_api_keys = await list_api_keys(
                service_id=service_id, environment=environment
            )
            existing_local_keys = await list_local_keys(
                service_id=service_id, environment=environment
            )
            if any(
                k.name == key_name and not k.revoked for k in existing_api_keys
            ) or any(k.key_name == key_name for k in existing_local_keys):
                key_name_conflict.text = (
                    f"⚠ A key named '{key_name}' already exists for this service"
                )
                key_name_conflict.visible = True
                return
        key_name_conflict.visible = False

    async def handle_env_change(_=None) -> None:  # pragma: no cover
        await refresh_service_options()
        await update_key_name_preview()

    async def handle_generate() -> None:  # pragma: no cover
        environment = env_select.value
        service_id = service_select.value
        key_name = build_key_name()
        if not (environment and service_id and key_name):
            ui.notify(
                "Environment, service, and key name prefix are required",
                color="red",
            )
            return
        existing_api_keys = await list_api_keys(
            service_id=service_id, environment=environment
        )
        existing_local_keys = await list_local_keys(
            service_id=service_id, environment=environment
        )
        if any(k.name == key_name and not k.revoked for k in existing_api_keys) or any(
            k.key_name == key_name for k in existing_local_keys
        ):
            ui.notify(
                f"A key named '{key_name}' already exists for this service",
                color="red",
            )
            return
        if not await ensure_admin_auth(environment, sync_label):
            return
        api = await build_api_client(environment)
        key_type = "test" if test_checkbox.value else "normal"
        secret_type = UUID_SECRET_TYPE if uuid_checkbox.value else None
        try:
            payload = await api.create_api_key(
                service_id, key_name, key_type, secret_type
            )
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
        try:
            keys = await api.get_api_keys(service_id)
        except httpx.HTTPStatusError as exc:
            if exc.response and exc.response.status_code == 401:
                handle_unauthorized(sync_label, environment)
                return
            raise
        try:
            created_key = _select_latest_key(keys, key_name)
        except ValueError as exc:
            ui.notify(str(exc), color="red")
            return
        service = service_lookup.get(service_id)
        if not service:
            ui.notify(
                "Service details not found; sync services and try again.",
                color="red",
            )
            return
        output_area.value = _build_key_email(
            secret, created_key, environment, service.name, service_id
        )
        ui.notify("Email content generated", color="green")

    def handle_copy_output() -> None:  # pragma: no cover
        if not output_area.value:
            safe_notify("Generate email content first.", color="warning")
            return
        copy_to_clipboard(output_area.value)

    env_select.on_value_change(handle_env_change)
    key_prefix.on_value_change(lambda _: update_key_name_preview())
    uuid_checkbox.on_value_change(lambda _: update_key_name_preview())
    test_checkbox.on_value_change(lambda _: update_key_name_preview())
    service_select.on_value_change(lambda _: update_key_name_preview())
    generate_button.on_click(handle_generate)
    copy_button.on_click(handle_copy_output)

    await refresh_service_options()
    await update_key_name_preview()


@ui.page("/send")
async def send_page() -> None:
    placeholder_pattern = re.compile(r"\(\((.*?)\)\)")

    async def refresh_service_options() -> None:  # pragma: no cover
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(_st.state.environment)
        }
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None
        await handle_service_change()

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=refresh_service_options
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    service_options = {
        svc.id: format_service_label(svc)
        for svc in await list_services(_st.state.environment)
    }
    env_options = list(_st.config.api_hosts.keys())

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Send Notification").classes("text-lg font-semibold")
        env_select = ui.select(
            env_options, value=_st.state.environment, label="Environment"
        ).classes("w-full md:w-1/2")
        service_select = (
            ui.select(service_options, label="Service", with_input=True)
            .props("clearable")
            .classes("w-full md:w-1/2")
        )
        key_select = (
            ui.select({}, label="API Key").props("clearable").classes("w-full md:w-1/2")
        )
        type_toggle = ui.toggle({"email": "Email", "sms": "SMS"}, value="email")
        template_select = (
            ui.select({}, label="Template", with_input=True)
            .props("clearable")
            .classes("w-full md:w-1/2")
        )
        recipient_input = (
            ui.input(
                label="Recipients (comma separated)",
                placeholder="email1@example.com, email2@example.com",
            )
            .props("clearable")
            .classes("w-full md:w-1/2")
        )
        personalisation_area = ui.column().classes("w-full md:w-1/2")
        response_log = ui.code("", language="json").classes(
            "w-full bg-gray-50 dark:bg-slate-900"
        )
        personalisation_controls: Dict[str, Input] = {}

        def render_preview_text(
            content: str, personalisation: Dict[str, str]
        ) -> str:  # pragma: no cover
            if not content:
                return ""

            def replace(match: re.Match) -> str:
                key = match.group(1).strip()
                value = personalisation.get(key, "")
                return value if value else match.group(0)

            return placeholder_pattern.sub(replace, content)

        def build_personalisation() -> Dict[str, str]:  # pragma: no cover
            return {
                key: control.value or ""
                for key, control in personalisation_controls.items()
            }

        async def load_keys() -> None:
            selected_service = service_select.value
            keys = await list_local_keys(selected_service, env_select.value)
            key_select.set_options({k.id: k.key_name for k in keys})

        async def load_templates() -> None:
            selected_service = service_select.value
            t_type = type_toggle.value
            templates = await list_templates(
                selected_service, t_type, environment=_st.state.environment
            )
            options = {t.id: t.name for t in templates}
            template_select.set_options(options)
            if template_select.value not in options:
                template_select.value = None

        async def handle_template_change() -> None:  # pragma: no cover
            personalisation_area.clear()
            personalisation_controls.clear()
            selected_id = template_select.value
            templates = await list_templates(
                service_select.value,
                type_toggle.value,
                environment=_st.state.environment,
            )
            tmpl = next((t for t in templates if t.id == selected_id), None)
            if not tmpl:
                return
            placeholders = extract_placeholders(
                (tmpl.subject or "") + " " + (tmpl.content or "")
            )
            with personalisation_area:
                for name in placeholders:
                    personalisation_controls[name] = (
                        ui.input(label=name, placeholder=name)
                        .props("clearable")
                        .classes("w-full md:w-1/2")
                    )
                    personalisation_controls[name].on_value_change(update_preview)
            await update_preview()

        async def update_preview(_=None) -> None:  # pragma: no cover
            selected_id = template_select.value
            if not selected_id:
                preview_subject.text = ""
                preview_body.text = "Select a template to see the preview."
                return
            templates = await list_templates(
                service_select.value,
                type_toggle.value,
                environment=_st.state.environment,
            )
            tmpl = next((t for t in templates if t.id == selected_id), None)
            if not tmpl:
                preview_subject.text = ""
                preview_body.text = "Select a template to see the preview."
                return
            personalisation = build_personalisation()
            subject = render_preview_text(tmpl.subject or "", personalisation)
            content = render_preview_text(tmpl.content or "", personalisation)
            preview_subject.text = f"Subject: {subject}" if subject else ""
            preview_body.text = content or ""

        async def handle_send() -> None:  # pragma: no cover
            selected_env = env_select.value
            selected_service = service_select.value
            selected_key = key_select.value
            selected_template = template_select.value
            t_type = type_toggle.value
            recipient_value = recipient_input.value or ""
            if not (
                selected_env and selected_service and selected_key and selected_template
            ):
                ui.notify(
                    "Environment, service, key, and template are required", color="red"
                )
                return
            recipients = parse_recipients(recipient_value)
            if not recipients:
                ui.notify("At least one recipient is required", color="red")
                return
            invalid_recipients = [
                recipient
                for recipient in recipients
                if not validate_recipient(t_type, recipient)
            ]
            if invalid_recipients:
                sample = ", ".join(invalid_recipients[:3])
                suffix = "..." if len(invalid_recipients) > 3 else ""
                ui.notify(f"Invalid recipients: {sample}{suffix}", color="red")
                return

            personalisation = build_personalisation()
            missing_key = find_missing_personalisation(personalisation)
            if missing_key:
                ui.notify(
                    f"Personalisation field '{missing_key}' is empty", color="red"
                )
                return

            try:
                api_key_secret = await resolve_local_key(_st.encryption, selected_key)
                api = await build_api_client(selected_env)
                semaphore = asyncio.Semaphore(_st.config.max_concurrency)

                async def send_to_recipient(recipient: str, index: int):
                    async with semaphore:
                        try:
                            result = await api.send_notification(
                                template_id=selected_template,
                                recipient=recipient,
                                personalisation=personalisation,
                                api_key=api_key_secret,
                                service_id=selected_service,
                                template_type=t_type,
                            )
                            return index, {
                                "recipient": recipient,
                                "status": "sent",
                                "response": result,
                            }
                        except Exception as exc:
                            return index, {
                                "recipient": recipient,
                                "status": "error",
                                "error": str(exc),
                            }

                tasks = [
                    asyncio.create_task(send_to_recipient(recipient, idx))
                    for idx, recipient in enumerate(recipients)
                ]
                results: List[Optional[Dict[str, Any]]] = [None] * len(tasks)
                sent_count = 0
                error_count = 0
                for task in asyncio.as_completed(tasks):
                    index, result = await task
                    results[index] = result
                    if result.get("status") == "sent":
                        sent_count += 1
                    elif result.get("status") == "error":
                        error_count += 1
                final_results = [r for r in results if r is not None]
                response_log.set_content(
                    json.dumps(
                        {
                            "total": len(recipients),
                            "sent": sent_count,
                            "errors": error_count,
                            "results": final_results,
                        },
                        indent=2,
                    )
                )
                if error_count:
                    ui.notify(
                        f"Sent {sent_count} with {error_count} errors",
                        color="warning",
                    )
                else:
                    ui.notify("Notification sent", color="green")
            except Exception as exc:
                ui.notify(f"Error: {exc}", color="red")

        async def handle_service_change(_=None) -> None:
            await load_keys()
            await load_templates()
            await update_preview()

        async def handle_type_change(_=None) -> None:  # pragma: no cover
            await load_templates()
            await update_preview()

        async def handle_template_select(_=None) -> None:  # pragma: no cover
            await handle_template_change()

        async def handle_env_change(e) -> None:  # pragma: no cover
            _st.state.environment = e.value
            await refresh_status_badge(status_badge)
            await refresh_service_options()

        service_select.on_value_change(handle_service_change)
        type_toggle.on_value_change(handle_type_change)
        template_select.on_value_change(handle_template_select)
        env_select.on_value_change(handle_env_change)
        ui.button("Send Notification", on_click=handle_send, color="primary")
        with ui.card().classes("p-6 w-full"):
            ui.label("Preview").classes("text-md font-semibold")
            preview_subject = ui.label("").classes("text-sm font-medium")
            preview_body = ui.label("").classes("text-sm whitespace-pre-wrap")

        await handle_service_change()


@ui.page("/bulk-send")
async def bulk_send_page() -> None:
    placeholder_pattern = re.compile(r"\(\((.*?)\)\)")

    async def refresh_service_options() -> None:  # pragma: no cover
        options = {
            svc.id: format_service_label(svc)
            for svc in await list_services(_st.state.environment)
        }
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None
        await handle_service_change()

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=refresh_service_options
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    service_options = {
        svc.id: format_service_label(svc)
        for svc in await list_services(_st.state.environment)
    }
    env_options = list(_st.config.api_hosts.keys())

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Bulk Send Notification").classes("text-lg font-semibold")
        env_select = ui.select(
            env_options, value=_st.state.environment, label="Environment"
        ).classes("w-full md:w-1/2")
        service_select = (
            ui.select(service_options, label="Service", with_input=True)
            .props("clearable")
            .classes("w-full md:w-1/2")
        )
        key_select = (
            ui.select({}, label="API Key").props("clearable").classes("w-full md:w-1/2")
        )
        type_toggle = ui.toggle({"email": "Email", "sms": "SMS"}, value="email")
        template_select = (
            ui.select({}, label="Template", with_input=True)
            .props("clearable")
            .classes("w-full md:w-1/2")
        )
        personalisation_area = ui.column().classes("w-full md:w-1/2")
        response_log = ui.code("", language="json").classes(
            "w-full bg-gray-50 dark:bg-slate-900"
        )
        progress_label = ui.label("Bulk send progress: idle").classes("text-sm")
        progress_bar = ui.linear_progress(
            value=0, show_value=False, color="green"
        ).classes("w-full")
        personalisation_controls: Dict[str, Input] = {}

        def render_preview_text(
            content: str, personalisation: Dict[str, str]
        ) -> str:  # pragma: no cover
            if not content:
                return ""

            def replace(match: re.Match) -> str:
                key = match.group(1).strip()
                value = personalisation.get(key, "")
                return value if value else match.group(0)

            return placeholder_pattern.sub(replace, content)

        def build_personalisation() -> Dict[str, str]:  # pragma: no cover
            return {
                key: control.value or ""
                for key, control in personalisation_controls.items()
            }

        async def load_keys() -> None:
            selected_service = service_select.value
            keys = await list_local_keys(selected_service, env_select.value)
            key_select.set_options({k.id: k.key_name for k in keys})

        async def load_templates() -> None:
            selected_service = service_select.value
            t_type = type_toggle.value
            templates = await list_templates(
                selected_service, t_type, environment=_st.state.environment
            )
            options = {t.id: t.name for t in templates}
            template_select.set_options(options)
            if template_select.value not in options:
                template_select.value = None

        async def handle_template_change() -> None:  # pragma: no cover
            personalisation_area.clear()
            personalisation_controls.clear()
            selected_id = template_select.value
            templates = await list_templates(
                service_select.value,
                type_toggle.value,
                environment=_st.state.environment,
            )
            tmpl = next((t for t in templates if t.id == selected_id), None)
            if not tmpl:
                return
            placeholders = extract_placeholders(
                (tmpl.subject or "") + " " + (tmpl.content or "")
            )
            with personalisation_area:
                for name in placeholders:
                    personalisation_controls[name] = (
                        ui.input(label=name, placeholder=name)
                        .props("clearable")
                        .classes("w-full md:w-1/2")
                    )
                    personalisation_controls[name].on_value_change(update_preview)
            await update_preview()

        async def update_preview(_=None) -> None:  # pragma: no cover
            selected_id = template_select.value
            if not selected_id:
                preview_subject.text = ""
                preview_body.text = "Select a template to see the preview."
                return
            templates = await list_templates(
                service_select.value,
                type_toggle.value,
                environment=_st.state.environment,
            )
            tmpl = next((t for t in templates if t.id == selected_id), None)
            if not tmpl:
                preview_subject.text = ""
                preview_body.text = "Select a template to see the preview."
                return
            personalisation = build_personalisation()
            subject = render_preview_text(tmpl.subject or "", personalisation)
            content = render_preview_text(tmpl.content or "", personalisation)
            preview_subject.text = f"Subject: {subject}" if subject else ""
            preview_body.text = content or ""

        async def perform_bulk_send() -> None:  # pragma: no cover
            selected_env = env_select.value
            selected_service = service_select.value
            selected_key = key_select.value
            selected_template = template_select.value
            t_type = type_toggle.value
            if not (
                selected_env and selected_service and selected_key and selected_template
            ):
                ui.notify(
                    "Environment, service, key, and template are required", color="red"
                )
                return

            personalisation = build_personalisation()
            missing_key = find_missing_personalisation(personalisation)
            if missing_key:
                ui.notify(
                    f"Personalisation field '{missing_key}' is empty", color="red"
                )
                return

            users = await list_users(selected_env)
            active_users = [
                user for user in users if user.state == "active" and not user.blocked
            ]
            if not active_users:
                ui.notify("No active users found", color="warning")
                return

            total_users = len(active_users)
            completed = 0
            sent_count = 0
            skipped_count = 0
            error_count = 0

            def progress_percent() -> int:
                if not total_users:
                    return 0
                return min(100, int(round((completed / total_users) * 100)))

            progress_bar.value = 0
            progress_label.text = "Sending 0%"

            try:
                api_key_secret = await resolve_local_key(_st.encryption, selected_key)
                api = await build_api_client(selected_env)
                semaphore = asyncio.Semaphore(_st.config.max_concurrency)

                async def send_for_user(user, index: int):
                    recipient = (
                        user.email_address if t_type == "email" else user.mobile_number
                    )
                    if not recipient:
                        return index, {
                            "user_id": user.id,
                            "recipient": recipient,
                            "status": "skipped",
                            "reason": "missing recipient",
                        }
                    async with semaphore:
                        try:
                            result = await api.send_notification(
                                template_id=selected_template,
                                recipient=recipient,
                                personalisation=personalisation,
                                api_key=api_key_secret,
                                service_id=selected_service,
                                template_type=t_type,
                            )
                            return index, {
                                "user_id": user.id,
                                "recipient": recipient,
                                "status": "sent",
                                "response": result,
                            }
                        except Exception as exc:
                            return index, {
                                "user_id": user.id,
                                "recipient": recipient,
                                "status": "error",
                                "error": str(exc),
                            }

                tasks = [
                    asyncio.create_task(send_for_user(user, idx))
                    for idx, user in enumerate(active_users)
                ]
                results: List[Optional[Dict[str, Any]]] = [None] * len(tasks)
                for task in asyncio.as_completed(tasks):
                    index, result = await task
                    results[index] = result
                    completed += 1
                    status = result.get("status")
                    if status == "sent":
                        sent_count += 1
                    elif status == "skipped":
                        skipped_count += 1
                    elif status == "error":
                        error_count += 1
                    progress_bar.value = completed / total_users
                    percent = progress_percent()
                    progress_label.text = (
                        f"Sending {percent}% "
                        f"(sent {sent_count}, skipped {skipped_count}, errors {error_count})"
                    )
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                file_path = os.path.join(
                    "data", f"bulk_send_responses_{timestamp}.json"
                )
                final_results = [r for r in results if r is not None]
                output = {
                    "environment": selected_env,
                    "service_id": selected_service,
                    "template_id": selected_template,
                    "template_type": t_type,
                    "total_users": len(active_users),
                    "sent": sent_count,
                    "skipped": skipped_count,
                    "errors": error_count,
                    "results": final_results,
                }
                with open(file_path, "w", encoding="utf-8") as handle:
                    json.dump(output, handle, indent=2)
                response_log.set_content(
                    json.dumps(
                        {
                            "file": file_path,
                            "total": len(active_users),
                            "sent": sent_count,
                            "skipped": skipped_count,
                            "errors": error_count,
                        },
                        indent=2,
                    )
                )
                progress_bar.value = 1
                progress_label.text = (
                    f"Complete: 100% (sent {sent_count}, "
                    f"skipped {skipped_count}, errors {error_count})"
                )
                ui.notify("Bulk send complete", color="green")
            except Exception as exc:
                progress_bar.value = 0
                progress_label.text = f"Bulk send failed at {progress_percent()}%"
                ui.notify(f"Error: {exc}", color="red")

        async def handle_bulk_send() -> None:  # pragma: no cover
            selected_env = env_select.value
            selected_service = service_select.value
            selected_key = key_select.value
            selected_template = template_select.value
            if not (
                selected_env and selected_service and selected_key and selected_template
            ):
                ui.notify(
                    "Environment, service, key, and template are required", color="red"
                )
                return
            missing_key = find_missing_personalisation(build_personalisation())
            if missing_key:
                ui.notify(
                    f"Personalisation field '{missing_key}' is empty", color="red"
                )
                return
            confirm_message.text = (
                "You are about to send to ALL active users of the platform "
                f"({selected_env})."
            )
            confirm_dialog.open()

        async def handle_confirm_send() -> None:  # pragma: no cover
            confirm_dialog.close()
            await perform_bulk_send()

        async def handle_service_change(_=None) -> None:
            await load_keys()
            await load_templates()
            await update_preview()

        async def handle_type_change(_=None) -> None:  # pragma: no cover
            await load_templates()
            await update_preview()

        async def handle_template_select(_=None) -> None:  # pragma: no cover
            await handle_template_change()

        async def handle_env_change(e) -> None:  # pragma: no cover
            _st.state.environment = e.value
            await refresh_status_badge(status_badge)
            await refresh_service_options()

        service_select.on_value_change(handle_service_change)
        type_toggle.on_value_change(handle_type_change)
        template_select.on_value_change(handle_template_select)
        env_select.on_value_change(handle_env_change)
        ui.button("Bulk Send Notification", on_click=handle_bulk_send, color="primary")
        with ui.dialog() as confirm_dialog, ui.card():
            ui.label("Confirm Bulk Send").classes("text-md font-semibold")
            confirm_message = ui.label("")
            with ui.row().classes("gap-2"):
                ui.button("Send to all", on_click=handle_confirm_send, color="primary")
                ui.button("Cancel", on_click=confirm_dialog.close, color="gray")
        with ui.card().classes("p-6 w-full"):
            ui.label("Preview").classes("text-md font-semibold")
            preview_subject = ui.label("").classes("text-sm font-medium")
            preview_body = ui.label("").classes("text-sm whitespace-pre-wrap")

        await handle_service_change()


@ui.page("/settings")
async def settings_page() -> None:
    key_environment = None
    key_service = None

    async def refresh_service_options() -> None:  # pragma: no cover
        env_value = key_environment.value if key_environment else get_view_environment()
        options = {
            svc.id: format_service_label(svc) for svc in await list_services(env_value)
        }
        if not key_service:
            return
        key_service.set_options(options)
        if key_service.value not in options:
            key_service.value = None

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=refresh_service_options
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Settings").classes("text-lg font-semibold")

        with ui.card().classes("p-6 w-full"):
            ui.label("API Configuration").classes("text-md font-semibold")
            rows = []
            for env in _st.config.api_hosts:
                current_url = await get_setting(
                    f"base_url_{env}"
                ) or _st.config.api_hosts.get(env)
                rows.append((env, current_url))
            inputs: Dict[str, ui.input] = {}
            for env, url in rows:
                inputs[env] = (
                    ui.input(label=f"{env.title()} Base URL", value=url)
                    .props("clearable")
                    .classes("w-full md:w-1/2")
                )

            async def handle_save_urls() -> None:  # pragma: no cover
                await save_base_urls(inputs)

            ui.button(
                "Save Base URLs",
                on_click=handle_save_urls,
            )

        with ui.card().classes("p-6 w-full"):
            ui.label("Global Admin Auth (per environment)").classes(
                "text-md font-semibold"
            )
            auth_inputs: Dict[str, Dict[str, ui.input]] = {}
            for env in _st.config.api_hosts:
                user_val = (
                    await get_secure_setting(f"basic_username_{env}", _st.encryption)
                    or ""
                )
                pass_val = (
                    await get_secure_setting(f"basic_password_{env}", _st.encryption)
                    or ""
                )
                auth_inputs[env] = {
                    "user": ui.input(label=f"{env.title()} Username", value=user_val)
                    .props("clearable")
                    .classes("w-full md:w-1/2"),
                    "pass": ui.input(
                        label=f"{env.title()} Password", value=pass_val, password=True
                    )
                    .props("clearable")
                    .classes("w-full md:w-1/2"),
                }

            async def handle_save_auth() -> None:  # pragma: no cover
                await save_admin_auth(auth_inputs)

            ui.button(
                "Save Admin Auth",
                on_click=handle_save_auth,
            )

        with ui.card().classes("p-6 w-full"):
            ui.label("Local API Keys").classes("text-md font-semibold")
            env_options = {env: env.title() for env in _st.config.api_hosts}
            key_environment = ui.select(
                env_options, value=_st.state.environment, label="Environment"
            ).classes("w-full md:w-1/2")
            service_options = {
                svc.id: format_service_label(svc)
                for svc in await list_services(key_environment.value)
            }
            key_service = ui.select(
                service_options, label="Service", with_input=True
            ).classes("w-full md:w-1/2")
            key_name = (
                ui.input(label="Key Name").props("clearable").classes("w-full md:w-1/2")
            )
            key_secret = (
                ui.input(label="Key Secret", password=True)
                .props("clearable")
                .classes("w-full md:w-1/2")
            )
            key_type = ui.select(
                {"normal": "Normal", "team": "Team", "test": "Test"}, value="normal"
            ).classes("w-full md:w-1/2")

            async def handle_add_key() -> None:  # pragma: no cover
                await save_local_key(
                    key_environment.value,
                    key_service.value,
                    key_name.value,
                    key_secret.value,
                    key_type.value,
                )

            async def handle_key_environment_change(_=None) -> None:  # pragma: no cover
                await refresh_service_options()

            ui.button(
                "Add Key",
                on_click=handle_add_key,
            )
            key_environment.on_value_change(handle_key_environment_change)
            ui.label("Stored Keys")
            await render_local_keys()


async def save_base_urls(inputs: Dict[str, ui.input]) -> None:
    for env, control in inputs.items():
        if control.value:
            await set_setting(f"base_url_{env}", control.value)
    ui.notify("Base URLs saved", color="green")


async def save_admin_auth(auth_inputs: Dict[str, Dict[str, ui.input]]) -> None:
    for env, pair in auth_inputs.items():
        user = pair["user"].value or ""
        password = pair["pass"].value or ""
        if user and password:
            await set_secure_setting(f"basic_username_{env}", user, _st.encryption)
            await set_secure_setting(f"basic_password_{env}", password, _st.encryption)
    ui.notify("Admin credentials saved", color="green")


async def save_local_key(
    environment: Optional[str],
    service_id: Optional[str],
    name: str,
    secret: str,
    key_type: str,
) -> None:
    if not (environment and service_id and name and secret):
        ui.notify("Environment, service, name, and secret are required", color="red")
        return
    await add_local_key(_st.encryption, service_id, environment, name, secret, key_type)
    ui.notify("Key saved", color="green")
    await refresh_if_needed(render_local_keys)


@ui.refreshable
async def render_local_keys() -> None:
    keys = await list_local_keys()
    rows: List[Dict[str, Any]] = [
        {
            "id": k.id,
            "service_id": k.service_id,
            "environment": k.environment,
            "key_name": k.key_name,
            "key_type": k.key_type,
        }
        for k in keys
    ]
    table = ui.table(
        columns=make_sortable(
            [
                {"name": "id", "label": "ID", "field": "id"},
                {"name": "service_id", "label": "Service", "field": "service_id"},
                {"name": "environment", "label": "Environment", "field": "environment"},
                {"name": "key_name", "label": "Name", "field": "key_name"},
                {"name": "key_type", "label": "Type", "field": "key_type"},
            ]
        ),
        rows=rows,
        pagination={"rowsPerPage": 5},
    )
    table.props("row-key=id").classes("w-full")
    add_copyable_slots(table, rows)


if __name__ in {"__main__", "__mp_main__"}:  # pragma: no cover
    ui.run(
        title="VA Notify Admin",
        port=8080,
        reload=True,
        storage_secret=_st.config.master_key,
        show=False,
    )
