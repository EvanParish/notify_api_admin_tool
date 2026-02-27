from __future__ import annotations

import httpx

from app.sync import SyncManager


async def handle_entity_sync(
    method_names: list[str],
    status_badge,
    sync_label,
    label: str,
    pre_sync: list[str] | None = None,
) -> bool:
    """Generic sync handler that replaces per-entity copy-paste handlers.

    Args:
        method_names: SyncManager method names to call (e.g. ``["sync_users"]``).
        status_badge: NiceGUI badge widget for API status.
        sync_label: NiceGUI label widget for sync progress text.
        label: Human-readable label for progress messages (e.g. ``"users"``).
        pre_sync: Optional SyncManager methods to call first
            (e.g. ``["sync_services"]`` before syncing templates).
    """
    # Late import to avoid circular dependency with main.py.
    # Phase 2 will move these globals to app/ui/state.py, eliminating
    # the need for this late import.
    import main

    if not await main.ensure_sync_enabled(sync_label):
        return False
    if not await main.ensure_admin_auth(main.state.environment, sync_label):
        return False

    api = await main.build_api_client(main.state.environment)
    manager = SyncManager(
        api, main.config.max_concurrency, environment=main.state.environment
    )

    async def progress(msg: str):
        main.state.sync_message = msg
        sync_label.text = msg

    sync_label.text = f"Syncing {label}..."
    try:
        if pre_sync:
            for method in pre_sync:
                await getattr(manager, method)(progress=progress)
        for method in method_names:
            await getattr(manager, method)(progress=progress)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            main.handle_unauthorized(sync_label, main.state.environment)
            return False
        raise
    sync_label.text = "Sync complete"
    await main.refresh_status_badge(status_badge)
    return True
