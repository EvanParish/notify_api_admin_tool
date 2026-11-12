from __future__ import annotations

from nicegui import ui

from app.ui.shell import CHANGELOG_ROUTE, build_shell, ensure_theme_preference
from app.ui.state import PAGE_RESPONSE_TIMEOUT, refresh_status_badge
from app.ui.sync_handlers import handle_full_sync
from app.version import RELEASES_URL, get_release_notes, get_version


@ui.page(CHANGELOG_ROUTE, response_timeout=PAGE_RESPONSE_TIMEOUT)
async def changelog_page() -> None:
    status_badge, sync_label, refresh_button, dark_mode, theme_button = build_shell()
    await ensure_theme_preference(dark_mode, theme_button)

    # Nothing on this page reads the cache, so the header button only needs to sync.
    refresh_button.on_click(lambda: handle_full_sync(status_badge, sync_label))
    await refresh_status_badge(status_badge)

    with ui.column().classes("p-8 gap-4 w-full max-w-4xl"):
        with ui.row().classes("items-center gap-3"):
            ui.label("What's New").classes("text-lg font-semibold")
            ui.badge(f"Running v{get_version()}", color="primary").props("outline")
        notes = get_release_notes()
        if notes:
            ui.markdown(notes).classes("w-full changelog")
        else:
            ui.label("No changelog is available in this build.").classes("text-slate-500")
        ui.link("All releases on GitHub", RELEASES_URL, new_tab=True).classes("text-sm")
