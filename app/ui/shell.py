"""Shell chrome: sidebar navigation, header, theme, and NiceGUI monkey-patches."""

from __future__ import annotations

import gzip
import inspect
import logging
import sys
from typing import Dict

from nicegui import app, ui
from nicegui.client import Client

from app.ui import state as _st

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NiceGUI monkey-patches (executed at import time)
# ---------------------------------------------------------------------------
_original_unraisablehook = sys.unraisablehook


def _suppress_gzip_close_error(unraisable: sys.UnraisableHookArgs) -> None:
    """Silence harmless GZipMiddleware errors on client disconnect."""
    if (
        isinstance(unraisable.exc_value, ValueError)
        and "I/O operation on closed file" in str(unraisable.exc_value)
        and isinstance(unraisable.object, gzip.GzipFile)
    ):
        return
    _original_unraisablehook(unraisable)


sys.unraisablehook = _suppress_gzip_close_error

_original_client_delete = Client.delete


def _safe_client_delete(self) -> None:
    try:
        _original_client_delete(self)
    except KeyError:
        logger.warning("NiceGUI client already deleted: %s", self.id)
        self._deleted = True


Client.delete = _safe_client_delete


# ---------------------------------------------------------------------------
# Theme helpers
# ---------------------------------------------------------------------------
def set_theme_preference(is_dark: bool) -> None:
    app.storage.user["theme"] = "dark" if is_dark else "light"


def toggle_theme(dark_mode, theme_button) -> None:
    dark_mode.toggle()
    set_theme_preference(dark_mode.value)
    theme_button.props(f"icon={'light_mode' if dark_mode.value else 'dark_mode'}")


async def ensure_theme_preference(dark_mode, theme_button=None) -> None:
    stored_theme = app.storage.user.get("theme")
    if stored_theme not in {"light", "dark"}:
        stored_theme = "light"
        app.storage.user["theme"] = stored_theme
    dark_mode.value = stored_theme == "dark"
    if theme_button:
        theme_button.props(f"icon={'light_mode' if dark_mode.value else 'dark_mode'}")


# ---------------------------------------------------------------------------
# Shell builder
# ---------------------------------------------------------------------------
def _nav_link(icon: str, label: str, href: str) -> None:
    """Create a styled navigation link with icon."""
    with ui.link(target=href).classes(
        "flex items-center gap-3 px-4 py-2.5 rounded-lg no-underline "
        "text-slate-600 dark:text-slate-300 "
        "hover:bg-slate-200 dark:hover:bg-slate-700 hover:text-slate-900 dark:hover:text-white "
        "transition-colors duration-150"
    ):
        ui.icon(icon).classes("text-lg")
        ui.label(label).classes("text-sm font-medium")


def _nav_section(title: str) -> None:
    """Create a section header in the navigation."""
    ui.label(title).classes(
        "text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500 "
        "px-4 pt-4 pb-2"
    )


def build_shell(on_view_env_change=None) -> tuple:
    """Build sidebar + header chrome.  Returns (status_badge, sync_label, refresh_button, dark_mode, theme_button)."""
    drawer = (
        ui.left_drawer(value=True)
        .props("show-if-above bordered width=240")
        .classes(
            "bg-slate-100 dark:bg-slate-900 border-r border-slate-200 dark:border-slate-700"
        )
    )
    with drawer:
        with ui.column().classes("w-full py-4 gap-0.5"):
            _nav_section("Overview")
            _nav_link("dashboard", "Dashboard", "/")

            _nav_section("Notifications")
            _nav_link("send", "Send Notification", "/send")
            _nav_link("dynamic_feed", "Bulk Send", "/bulk-send")

            _nav_section("Resources")
            _nav_link("business", "Services", "/services")
            _nav_link("description", "Templates", "/templates")
            _nav_link("vpn_key", "API Keys", "/api-keys")
            _nav_link("add_circle", "Create API Key", "/api-key-service")
            _nav_link("people", "Users", "/users")
            _nav_link("sms", "SMS Senders", "/sms-senders")
            _nav_link("forum", "Communication Items", "/communication-items")
            _nav_link("call", "Inbound Numbers", "/inbound-numbers")
            _nav_link("cloud", "Provider Details", "/provider-details")

            _nav_section("Configuration")
            _nav_link("settings", "Settings", "/settings")

    dark_mode = ui.dark_mode()
    with ui.header().classes(
        "items-center justify-between bg-gray-200 dark:bg-slate-800"
    ):
        with ui.row().classes("items-center gap-3"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense")
            ui.label("Notification Admin Dashboard").classes(
                "text-xl font-medium text-slate-900 dark:text-white"
            )
        with ui.row().classes("items-center gap-4"):
            status_badge = ui.badge("API Status: Unknown", color="gray")
            sync_label = ui.label("").classes("text-slate-900 dark:text-white")
            env_options = {"all": "All"}
            env_options.update({env: env.title() for env in _st.config.api_hosts})
            env_select = ui.select(
                env_options, value=_st.state.view_environment, label="View Env"
            ).classes("w-36")
            sync_env_select = ui.select(
                {env: env.title() for env in _st.config.api_hosts},
                value=_st.state.environment,
                label="Sync Env",
            ).classes("w-32")
            refresh_button = ui.button("Refresh All Data")
            theme_button = ui.button(icon="dark_mode").props("flat round dense")
            theme_button.on_click(lambda: toggle_theme(dark_mode, theme_button))
            if on_view_env_change:

                async def handle_env_change(e):  # pragma: no cover
                    _st.state.view_environment = e.value
                    result = on_view_env_change()
                    if inspect.isawaitable(result):
                        await result

                env_select.on_value_change(handle_env_change)  # pragma: no cover
            else:
                env_select.on_value_change(
                    lambda e: setattr(_st.state, "view_environment", e.value)
                )

            async def handle_sync_env_change(e):  # pragma: no cover
                _st.state.environment = e.value
                await _st.refresh_status_badge(status_badge)
                ui.notify(f"Switched to {e.value} environment", color="info")

            sync_env_select.on_value_change(handle_sync_env_change)

            with ui.dropdown_button("Sync Settings", auto_close=False).props("flat"):
                ui.label("Allowed sync environments").classes("text-sm mb-2")
                env_checkboxes: Dict[str, ui.checkbox] = {}
                for env in _st.config.api_hosts:
                    is_enabled = env in _st.state.enabled_sync_environments
                    checkbox = ui.checkbox(env.title(), value=is_enabled)
                    env_checkboxes[env] = checkbox

                    def make_handler(environment):  # pragma: no cover
                        def handler(e):
                            if e.value:
                                _st.state.enabled_sync_environments.add(environment)
                                ui.notify(
                                    f"Syncing enabled for {environment}",
                                    color="positive",
                                )
                            else:
                                _st.state.enabled_sync_environments.discard(environment)
                                ui.notify(
                                    f"Syncing disabled for {environment}", color="info"
                                )

                        return handler

                    checkbox.on_value_change(make_handler(env))
    return status_badge, sync_label, refresh_button, dark_mode, theme_button
