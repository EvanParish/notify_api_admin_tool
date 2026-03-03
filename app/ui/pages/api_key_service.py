from __future__ import annotations

from typing import Any, Dict

import httpx
from nicegui import ui

from app.repository import list_api_keys, list_local_keys, list_services
from app.ui import state as _st
from app.ui.email_helpers import (
    UUID_SECRET_TYPE,
    _build_key_email,
    _normalize_email_env,
    _select_latest_key,
)
from app.ui.helpers import (
    copy_to_clipboard,
    format_service_label,
)
from app.ui.pages.api_keys import _extract_api_key_secret
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import (
    build_api_client,
    ensure_admin_auth,
    handle_unauthorized,
    refresh_status_badge,
    safe_notify,
)
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


@ui.page("/api-key-service")
async def api_key_emails_page() -> None:
    status_badge, sync_label, refresh_button, dark_mode = build_shell()
    await ensure_theme_preference(dark_mode)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    async def sync_api_keys():  # pragma: no cover
        await handle_entity_sync(
            ["sync_api_keys"],
            status_badge,
            sync_label,
            "API keys",
            pre_sync=["sync_services"],
        )

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
        await sync_api_keys()

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
