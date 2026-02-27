from __future__ import annotations

import httpx

from app.sync import SyncManager
from app.ui import state as _st


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
    if not await _st.ensure_sync_enabled(sync_label):
        return False
    if not await _st.ensure_admin_auth(_st.state.environment, sync_label):
        return False

    api = await _st.build_api_client(_st.state.environment)
    manager = SyncManager(
        api, _st.config.max_concurrency, environment=_st.state.environment
    )

    async def progress(msg: str):
        _st.state.sync_message = msg
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
            _st.handle_unauthorized(sync_label, _st.state.environment)
            return False
        raise
    sync_label.text = "Sync complete"
    await _st.refresh_status_badge(status_badge)
    return True


async def handle_full_sync(status_badge, sync_label) -> bool:
    """Run a full sync of all entities.

    Returns True if sync succeeded, False otherwise.
    """
    return await handle_entity_sync(["sync_all"], status_badge, sync_label, "all data")
