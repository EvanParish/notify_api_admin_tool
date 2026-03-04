from __future__ import annotations

from typing import Any, Dict, List, Optional

from nicegui import ui

from app.repository import list_users
from app.ui.helpers import (
    add_copyable_slots,
    format_environment,
    make_sortable,
    refresh_if_needed,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import get_view_environment, refresh_status_badge
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync


@ui.page("/users")
async def users_page() -> None:
    search_query = ""

    async def handle_view_env_change() -> None:  # pragma: no cover
        await refresh_if_needed(render_table)

    status_badge, sync_label, refresh_button, dark_mode = build_shell(
        on_view_env_change=handle_view_env_change
    )
    await ensure_theme_preference(dark_mode)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Users").classes("text-lg font-semibold")
        filter_row = ui.row().classes("gap-2 w-full")
        with filter_row:
            search_input = (
                ui.input(label="Search by ID, Name, or Email")
                .props("clearable")
                .classes("w-full md:w-1/2")
            )
            state_select = (
                ui.select({}, label="Filter by State", with_input=True)
                .props("clearable")
                .classes("w-full md:w-1/2")
            )

        async def handle_sync_users() -> None:  # pragma: no cover
            if await handle_entity_sync(
                ["sync_users"], status_badge, sync_label, "users"
            ):
                render_table.refresh()

        @ui.refreshable
        async def render_table() -> None:  # pragma: no cover
            users = await list_users(get_view_environment())

            def normalize_state(value: Optional[str]) -> str:
                return (value or "").strip().lower()

            state_values = sorted(
                {
                    normalize_state(user.state)
                    for user in users
                    if normalize_state(user.state)
                }
            )
            state_options = {state: state.title() for state in state_values}
            state_select.set_options(state_options)
            if state_select.value and state_select.value not in state_options:
                state_select.value = None
            selected_state = state_select.value
            if selected_state:
                users = [
                    user
                    for user in users
                    if normalize_state(user.state) == selected_state
                ]
            if search_query:
                users = [
                    user
                    for user in users
                    if search_query in (user.id or "").lower()
                    or search_query in (user.name or "").lower()
                    or search_query in (user.email_address or "").lower()
                ]
            table_rows: List[Dict[str, Any]] = [
                {
                    "id": user.id,
                    "environment": format_environment(user.environment),
                    "email_address": user.email_address,
                    "name": user.name,
                    "state": user.state,
                    "platform_admin": user.platform_admin,
                    "blocked": user.blocked,
                    "auth_type": user.auth_type,
                    "mobile_number": user.mobile_number,
                    "failed_login_count": user.failed_login_count,
                    "logged_in_at": user.logged_in_at,
                    "password_changed_at": user.password_changed_at,
                    "services_count": len(user.services or []),
                    "organisations_count": len(user.organisations or []),
                }
                for user in users
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
                            "name": "email_address",
                            "label": "Email",
                            "field": "email_address",
                        },
                        {"name": "name", "label": "Name", "field": "name"},
                        {"name": "state", "label": "State", "field": "state"},
                        {
                            "name": "platform_admin",
                            "label": "Platform Admin",
                            "field": "platform_admin",
                        },
                        {"name": "blocked", "label": "Blocked", "field": "blocked"},
                        {"name": "auth_type", "label": "Auth", "field": "auth_type"},
                        {
                            "name": "mobile_number",
                            "label": "Mobile",
                            "field": "mobile_number",
                        },
                        {
                            "name": "failed_login_count",
                            "label": "Failed Logins",
                            "field": "failed_login_count",
                        },
                        {
                            "name": "logged_in_at",
                            "label": "Logged In",
                            "field": "logged_in_at",
                        },
                        {
                            "name": "password_changed_at",
                            "label": "Password Changed",
                            "field": "password_changed_at",
                        },
                        {
                            "name": "services_count",
                            "label": "Services",
                            "field": "services_count",
                        },
                        {
                            "name": "organisations_count",
                            "label": "Orgs",
                            "field": "organisations_count",
                        },
                    ]
                ),
                rows=table_rows,
                pagination={"rowsPerPage": 10},
            )
            table.props("row-key=id").classes("w-full")
            add_copyable_slots(table, table_rows)

        async def handle_search_event(e) -> None:  # pragma: no cover
            nonlocal search_query
            search_query = (getattr(e, "value", None) or "").strip().lower()
            await refresh_if_needed(render_table)

        ui.button("Sync Users", on_click=handle_sync_users)
        search_input.on_value_change(handle_search_event)
        state_select.on_value_change(lambda _: render_table.refresh())
        await render_table()
