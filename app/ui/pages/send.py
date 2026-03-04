from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from nicegui import ui

from app.repository import (
    list_local_keys,
    list_services,
    list_templates,
    resolve_local_key,
)
from app.ui import state as _st
from app.ui.helpers import (
    find_missing_personalisation,
    format_service_label,
    parse_recipients,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import build_api_client, refresh_status_badge
from app.ui.sync_handlers import handle_full_sync
from app.utils import extract_placeholders, validate_recipient


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
