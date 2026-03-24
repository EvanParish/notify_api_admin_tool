from __future__ import annotations

import asyncio

import httpx

from app.sync import SyncManager, SyncResult
from app.ui import state as _st


async def _sync_for_environment(
    environment: str,
    method_names: list[str],
    sync_label,
    pre_sync: list[str] | None = None,
    method_kwargs: dict[str, dict] | None = None,
) -> SyncResult:
    """Sync methods for a single environment. Returns SyncResult with details."""
    if not await _st.ensure_admin_auth(environment, sync_label):
        result = SyncResult()
        result.error_count = 1
        return result

    api = await _st.build_api_client(environment)
    manager = SyncManager(api, _st.config.max_concurrency, environment=environment)

    async def progress(msg: str):
        _st.state.sync_message = f"[{environment}] {msg}"
        sync_label.text = f"[{environment}] {msg}"

    combined_result = SyncResult()
    method_kwargs = method_kwargs or {}
    try:
        if pre_sync:
            for method in pre_sync:
                sub_result = await getattr(manager, method)(progress=progress)
                combined_result.merge(sub_result)
        for method in method_names:
            kwargs = method_kwargs.get(method, {})
            sub_result = await getattr(manager, method)(progress=progress, **kwargs)
            combined_result.merge(sub_result)
    except httpx.HTTPStatusError as exc:
        if exc.response and exc.response.status_code == 401:
            _st.handle_unauthorized(sync_label, environment)
            combined_result.error_count += 1
            return combined_result
        raise
    return combined_result


async def handle_entity_sync(
    method_names: list[str],
    status_badge,
    sync_label,
    label: str,
    pre_sync: list[str] | None = None,
    environments: list[str] | None = None,
    method_kwargs: dict[str, dict] | None = None,
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
        method_kwargs: Optional dict mapping method names to keyword arguments
            (e.g. ``{"sync_api_keys": {"service_ids": ["abc123"]}}``).
    """
    if environments is not None:
        envs = environments
    else:
        envs = list(_st.state.enabled_sync_environments)

    if not envs:
        sync_label.text = "No environments enabled for sync"
        return False

    # Pre-validate credentials for all selected environments
    missing_by_env = await _st.check_environments_credentials(envs)
    if missing_by_env:
        error_parts = []
        for env, missing_fields in missing_by_env.items():
            fields = " and ".join(missing_fields)
            error_parts.append(f"{env}: {fields}")
        message = (
            f"Missing credentials for: {', '.join(error_parts)}. Update Settings > Global Admin Auth before syncing."
        )
        sync_label.text = message
        _st.safe_notify(message, color="warning")
        return False

    sync_label.text = f"Syncing {label} for {len(envs)} environment(s)..."
    results = await asyncio.gather(
        *[_sync_for_environment(env, method_names, sync_label, pre_sync, method_kwargs) for env in envs],
        return_exceptions=True,
    )

    total_success = 0
    total_errors = 0
    all_errors: list[tuple[str, str]] = []

    for env, result in zip(envs, results):
        if isinstance(result, Exception):
            total_errors += 1
            status_code = None
            if isinstance(result, httpx.HTTPStatusError) and result.response:
                status_code = result.response.status_code
            error_msg = f"[{env}] HTTP {status_code}: {result}" if status_code else f"[{env}] {result}"
            all_errors.append((env, error_msg))
        elif isinstance(result, SyncResult):
            total_success += result.success_count
            total_errors += result.error_count
            for err in result.errors:
                all_errors.append((env, f"[{env}] {err}"))
        else:
            if result is True:
                total_success += 1
            else:
                total_errors += 1

    if all_errors:
        for env, error_msg in all_errors[:3]:
            _st.safe_notify(error_msg, color="negative")
        if len(all_errors) > 3:
            _st.safe_notify(f"... and {len(all_errors) - 3} more errors", color="negative")

    if total_errors > 0:
        sync_label.text = f"Sync complete: {total_success} ok, {total_errors} failed"
    else:
        sync_label.text = "Sync complete"
    await _st.refresh_status_badge(status_badge)
    return total_success > 0


async def handle_full_sync(status_badge, sync_label) -> bool:
    """Run a full sync of all entities across all enabled environments.

    Returns True if at least one sync succeeded, False otherwise.
    """
    return await handle_entity_sync(["sync_all"], status_badge, sync_label, "all data")
