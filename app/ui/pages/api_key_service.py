from __future__ import annotations

from typing import Any

import httpx
from nicegui import ui

from app.repository import (
    get_service_by_name,
    list_api_keys,
    list_environments_for_service_name,
    list_local_keys,
    list_services,
)
from app.ui import state as _st
from app.ui.email_helpers import (
    UUID_SECRET_TYPE,
    EmailTemplate,
    _build_multi_env_key_email,
    _normalize_email_env,
    _select_latest_key,
)
from app.ui.helpers import (
    copy_to_clipboard,
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


def _build_key_name_for_env(env: str, prefix: str, uuid_key: bool, test_key: bool) -> str:
    """Build the key name for a specific environment."""
    env_token = _normalize_email_env(env).lower()
    uuid_part = "uuid-" if uuid_key else ""
    test_part = "test-" if test_key else ""
    return f"{env_token}-{prefix}-{uuid_part}{test_part}key"


@ui.page("/api-key-service")
async def api_key_emails_page() -> None:
    status_badge, sync_label, refresh_button, dark_mode, theme_button = build_shell()
    await ensure_theme_preference(dark_mode, theme_button)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    async def sync_api_keys(environments: list[str] | None = None):  # pragma: no cover
        await handle_entity_sync(
            ["sync_api_keys"],
            status_badge,
            sync_label,
            "API keys",
            pre_sync=["sync_services"],
            environments=environments,
        )

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    # Maps service name -> first service object (for display purposes)
    service_by_name: dict[str, Any] = {}
    env_checkboxes: dict[str, ui.checkbox] = {}
    available_envs: set[str] = set()
    selected_service_name: str = ""

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("API Key Email Generator").classes("text-lg font-semibold")
        ui.markdown("Generate API keys for a service across one or more environments and get email-ready content.")

        with ui.card().classes("p-6 w-full"):
            service_select = (
                ui.select({}, label="Service (by name)", with_input=True).props("clearable").classes("w-full md:w-1/2")
            )
            ui.label("Environments").classes("text-sm font-medium mt-4")
            env_checkbox_container = ui.row().classes("gap-4 flex-wrap")
            env_status_label = ui.label("Select a service to see available environments").classes(
                "text-sm text-gray-500 dark:text-slate-400"
            )
            key_prefix = ui.input(label="Key Name Prefix").props("clearable").classes("w-full md:w-1/2 mt-4")
            with ui.row().classes("gap-4 items-center"):
                uuid_checkbox = ui.checkbox("Vets-api (UUID) key", value=False)
                test_checkbox = ui.checkbox("Test key (non-sending)", value=False)
            ui.label("Email Template").classes("text-sm font-medium mt-4")
            template_select = ui.radio(
                {
                    EmailTemplate.NEW_SERVICE.value: "New Service (includes API endpoints)",
                    EmailTemplate.KEY_ROTATION.value: "Key Rotation (key details only)",
                },
                value=EmailTemplate.NEW_SERVICE.value,
            ).props("inline")
            key_name_preview = ui.label("Generated key names will appear here.").classes(
                "text-sm text-gray-600 dark:text-slate-300"
            )
            key_name_conflict = ui.label("").classes("text-sm text-red-600 dark:text-red-400")
            key_name_conflict.visible = False
            generate_button = ui.button("Generate API Key Email", color="green")
            progress_label = ui.label("").classes("text-sm text-blue-600 dark:text-blue-400")
            progress_label.visible = False

        with ui.card().classes("p-6 w-full"):
            ui.label("Generated Email Content").classes("text-md font-semibold")
            ui.label(
                "This generated email content will NOT be shown again after you leave this page. Copy it now."
            ).classes("text-sm text-red-600 dark:text-red-400")
            output_area = ui.textarea(label="Email Content").props("readonly").classes("w-full")
            copy_button = ui.button("Copy Email Content")

    def get_selected_envs() -> list[str]:
        """Get list of selected environments in config order."""
        return [env for env in _st.config.api_hosts if env in env_checkboxes and env_checkboxes[env].value]

    async def refresh_service_options() -> None:
        """Load services and deduplicate by name."""
        services = await list_services()
        service_by_name.clear()
        # Deduplicate by name - keep first occurrence
        for svc in services:
            if svc.name not in service_by_name:
                service_by_name[svc.name] = svc
        options = {name: name for name in sorted(service_by_name.keys())}
        service_select.set_options(options)
        if service_select.value not in options:
            service_select.value = None

    async def refresh_env_checkboxes() -> None:
        """Refresh environment checkboxes based on selected service name."""
        nonlocal available_envs, selected_service_name
        env_checkbox_container.clear()
        env_checkboxes.clear()
        available_envs = set()

        service_name = service_select.value
        if not service_name:
            selected_service_name = ""
            env_status_label.text = "Select a service to see available environments"
            env_status_label.visible = True
            return

        selected_service_name = service_name
        service_envs = await list_environments_for_service_name(service_name)
        available_envs = set(service_envs)

        if not available_envs:
            env_status_label.text = "No environments found for this service"
            env_status_label.visible = True
            return

        env_status_label.visible = False
        with env_checkbox_container:
            for env in _st.config.api_hosts:
                is_available = env in available_envs
                cb = ui.checkbox(
                    env.title(),
                    value=is_available and env == _st.state.environment,
                ).props("disable" if not is_available else "")
                if not is_available:
                    cb.classes("opacity-50")
                else:
                    cb.on_value_change(lambda _: handle_input_change())
                env_checkboxes[env] = cb

    def build_key_names() -> dict[str, str]:
        """Build key names for all selected environments."""
        prefix = (key_prefix.value or "").strip().lower()
        if not prefix:
            return {}
        return {
            env: _build_key_name_for_env(env, prefix, uuid_checkbox.value, test_checkbox.value)
            for env in get_selected_envs()
        }

    async def update_key_name_preview() -> None:  # pragma: no cover
        key_names = build_key_names()
        if key_names:
            names_display = ", ".join(f"{env.title()}: {name}" for env, name in key_names.items())
            key_name_preview.text = f"Generated key names: {names_display}"
        else:
            key_name_preview.text = "Generated key names will appear here."
            key_name_conflict.visible = False
            return

        # Check for conflicts
        conflicts = await check_conflicts()
        if conflicts:
            key_name_conflict.text = f"⚠ Key name conflicts: {', '.join(conflicts)}"
            key_name_conflict.visible = True
        else:
            key_name_conflict.visible = False

    async def check_conflicts() -> list[str]:
        """Check for key name conflicts across selected environments."""
        conflicts = []
        key_names = build_key_names()
        service_name = service_select.value
        if not service_name or not key_names:
            return conflicts

        for env, key_name in key_names.items():
            # Look up the service ID for this environment
            service = await get_service_by_name(service_name, env)
            if not service:
                continue
            existing_api_keys = await list_api_keys(service_id=service.id, environment=env)
            existing_local_keys = await list_local_keys(service_id=service.id, environment=env)
            if any(k.name == key_name and not k.revoked for k in existing_api_keys) or any(
                k.key_name == key_name for k in existing_local_keys
            ):
                conflicts.append(f"{env.title()}: {key_name}")
        return conflicts

    async def handle_input_change(_=None) -> None:  # pragma: no cover
        """Handle changes to key prefix, checkboxes, or other inputs."""
        await update_key_name_preview()

    async def handle_service_change(_=None) -> None:  # pragma: no cover
        await refresh_env_checkboxes()
        await update_key_name_preview()

    async def handle_generate() -> None:  # pragma: no cover
        service_name = service_select.value
        selected_envs = get_selected_envs()
        key_names = build_key_names()
        prefix = (key_prefix.value or "").strip()

        if not service_name:
            ui.notify("Please select a service", color="red")
            return
        if not selected_envs:
            ui.notify("Please select at least one environment", color="red")
            return
        if not prefix:
            ui.notify("Please enter a key name prefix", color="red")
            return

        conflicts = await check_conflicts()
        if conflicts:
            ui.notify(
                f"Key name conflicts found: {', '.join(conflicts)}",
                color="red",
            )
            return

        key_type = "test" if test_checkbox.value else "normal"
        secret_type = UUID_SECRET_TYPE if uuid_checkbox.value else None
        env_keys: list[dict[str, Any]] = []
        failed_envs: list[str] = []

        generate_button.disable()
        progress_label.visible = True

        try:
            for i, env in enumerate(selected_envs, 1):
                key_name = key_names[env]
                progress_label.text = f"Creating key in {env.title()}... ({i}/{len(selected_envs)})"

                # Look up the service ID for this environment by name
                service = await get_service_by_name(service_name, env)
                if not service:
                    failed_envs.append(f"{env} (service not found)")
                    continue

                if not await ensure_admin_auth(env, sync_label):
                    failed_envs.append(f"{env} (auth failed)")
                    continue

                api = await build_api_client(env)
                try:
                    payload = await api.create_api_key(service.id, key_name, key_type, secret_type)
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    failed_envs.append(f"{env} (offline/unreachable)")
                    continue
                except httpx.HTTPStatusError as exc:
                    if exc.response and exc.response.status_code == 401:
                        handle_unauthorized(sync_label, env)
                        failed_envs.append(f"{env} (unauthorized)")
                        continue
                    failed_envs.append(f"{env} ({exc.response.status_code})")
                    continue

                try:
                    secret = _extract_api_key_secret(payload)
                except ValueError as exc:
                    failed_envs.append(f"{env} ({exc})")
                    continue

                try:
                    keys = await api.get_api_keys(service.id)
                    created_key = _select_latest_key(keys, key_name)
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    failed_envs.append(f"{env} (offline during fetch)")
                    continue
                except (httpx.HTTPStatusError, ValueError) as exc:
                    failed_envs.append(f"{env} (fetch failed: {exc})")
                    continue

                env_keys.append(
                    {
                        "env": env,
                        "secret": secret,
                        "created_key": created_key,
                        "service_id": service.id,
                    }
                )

            progress_label.visible = False

            if not env_keys:
                ui.notify("Failed to create keys in all environments", color="red")
                return

            # Get selected template type
            template = EmailTemplate(template_select.value)
            output_area.value = _build_multi_env_key_email(env_keys, service_name, template=template)

            if failed_envs:
                ui.notify(
                    f"Keys created in {len(env_keys)} env(s). Failed: {', '.join(failed_envs)}",
                    color="warning",
                )
            else:
                ui.notify(
                    f"Keys created in {len(env_keys)} environment(s)",
                    color="green",
                )

            await sync_api_keys([item["env"] for item in env_keys])

        finally:
            generate_button.enable()
            progress_label.visible = False

    def handle_copy_output() -> None:  # pragma: no cover
        if not output_area.value:
            safe_notify("Generate email content first.", color="warning")
            return
        copy_to_clipboard(output_area.value)

    key_prefix.on_value_change(handle_input_change)
    uuid_checkbox.on_value_change(handle_input_change)
    test_checkbox.on_value_change(handle_input_change)
    service_select.on_value_change(handle_service_change)
    generate_button.on_click(handle_generate)
    copy_button.on_click(handle_copy_output)

    await refresh_service_options()
