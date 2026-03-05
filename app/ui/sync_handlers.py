from __future__ import annotations

import asyncio

import httpx

from app.sync import SyncManager
from app.ui import state as _st


async def _sync_for_environment(
    environment: str,
    method_names: list[str],
    sync_label,
    pre_sync: list[str] | None = None,
) -> bool:
    """Sync methods for a single environment. Returns True on success."""
    if not await _st.ensure_admin_auth(environment, sync_label):
        return False

    api = await _st.build_api_client(environment)
    manager = SyncManager(api, _st.config.max_concurrency, environment=environment)

    async def progress(msg: str):
        _st.state.sync_message = f"[{environment}] {msg}"
        sync_label.text = f"[{environment}] {msg}"

    try:
        if pre_sync:
            for method in pre_sync:
                await getattr(manager, method)(progress=progress)
        for method in method_names:
            await getattr(manager, method)(progress=progress)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            _st.handle_unauthorized(sync_label, environment)
            return False
        raise
    return True


async def handle_entity_sync(
    method_names: list[str],
    status_badge,
    sync_label,
    label: str,
    pre_sync: list[str] | None = None,
    environments: list[str] | None = None,
) -> bool:
    """Sync entities across environments.

    Args:
        method_names: SyncManager method names to call (e.g. ``["sync_users"]``).
        status_badge: NiceGUI badge widget for API status.
        sync_label: NiceGUI label widget for sync progress text.
        label: Human-readable label for progress messages (e.g. ``"users"``).
        pre_sync: Optional SyncManager methods to call first
            (e.g. ``["sync_services"]`` before syncing templates).
        environments: Optional list of specific environments to sync.
            If None, syncs all enabled environments.
    """
    if environments is not None:
        envs = environments
    else:
        envs = list(_st.state.enabled_sync_environments)

    if not envs:
        sync_label.text = "No environments enabled for sync"
        return False

    sync_label.text = f"Syncing {label} for {len(envs)} environment(s)..."
    results = await asyncio.gather(
        *[
            _sync_for_environment(env, method_names, sync_label, pre_sync)
            for env in envs
        ],
        return_exceptions=True,
    )

    success_count = sum(1 for r in results if r is True)
    fail_count = len(envs) - success_count
    if fail_count > 0:
        sync_label.text = f"Sync complete: {success_count} ok, {fail_count} failed"
    else:
        sync_label.text = "Sync complete"
    await _st.refresh_status_badge(status_badge)
    return success_count > 0


async def handle_full_sync(status_badge, sync_label) -> bool:
    """Run a full sync of all entities across all enabled environments.

    Returns True if at least one sync succeeded, False otherwise.
    """
    return await handle_entity_sync(["sync_all"], status_badge, sync_label, "all data")
