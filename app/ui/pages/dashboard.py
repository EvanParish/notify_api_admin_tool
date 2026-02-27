from __future__ import annotations

import asyncio

from nicegui import ui

from app.repository import (
    list_api_keys,
    list_inbound_numbers,
    list_provider_details,
    list_services,
    list_sms_senders,
    list_templates,
    list_users,
)
from app.ui.helpers import metric_card, refresh_if_needed
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import get_view_environment, refresh_status_badge
from app.ui.sync_handlers import handle_full_sync


@ui.page("/")
async def dashboard_page() -> None:
    @ui.refreshable
    async def render_dashboard() -> None:
        view_env = get_view_environment()
        (
            services,
            templates,
            api_keys,
            users,
            sms_senders,
            provider_details,
            inbound_numbers,
        ) = await asyncio.gather(
            list_services(view_env),
            list_templates(environment=view_env),
            list_api_keys(environment=view_env),
            list_users(view_env),
            list_sms_senders(environment=view_env),
            list_provider_details(view_env),
            list_inbound_numbers(environment=view_env),
        )
        with ui.column().classes("p-8 gap-6 w-full max-w-none"):
            ui.label("Dashboard").classes("text-lg font-semibold")
            with ui.row().classes("gap-4 w-full flex-wrap"):
                metric_card("Services", len(services))
                metric_card("Templates", len(templates))
                metric_card("API Keys", len(api_keys))
                metric_card("Users", len(users))
                metric_card("SMS Senders", len(sms_senders))
                metric_card("Provider Details", len(provider_details))
                metric_card("Inbound Numbers", len(inbound_numbers))
            ui.markdown(
                "This dashboard caches services, templates, API keys, users, SMS senders, provider details, and local API keys. Use the left navigation to manage data and send notifications."
            )

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=lambda: refresh_if_needed(render_dashboard)
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)
        await refresh_if_needed(render_dashboard)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)
    await render_dashboard()
