from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from nicegui import ui

from app.repository import (
    list_local_keys,
    list_services,
    list_templates,
    list_users,
    resolve_local_key,
)
from app.ui import state as _st
from app.ui.helpers import (
    find_missing_personalisation,
    format_service_label,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import build_api_client, refresh_status_badge
from app.ui.sync_handlers import handle_full_sync
from app.utils import extract_placeholders


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

    status_badge, sync_label, refresh_button, dark_mode, theme_button = build_shell(
        on_view_env_change=refresh_service_options
    )
    await ensure_theme_preference(dark_mode, theme_button)

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
        personalisation_controls: Dict[str, Any] = {}

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
