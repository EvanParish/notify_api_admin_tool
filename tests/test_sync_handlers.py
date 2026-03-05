"""Tests for app.ui.sync_handlers — the generic handle_entity_sync function."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ui import state as _st
from app.ui.sync_handlers import handle_entity_sync


@dataclass
class _SyncTestState:
    environment: str = "development"
    view_environments: list = field(default_factory=list)
    api_status: str = "unknown"
    sync_message: str = ""
    dev_only_mode: bool = True
    enabled_sync_environments: set = field(default_factory=lambda: {"development"})


def _make_mock_badges():
    badge = MagicMock()
    badge.props = MagicMock()
    label = MagicMock()
    label.text = ""
    return badge, label


@pytest.mark.asyncio
async def test_handle_entity_sync_success(initialized_db, mock_config):
    """Calls the specified SyncManager method and returns True."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True
    _st.state = _SyncTestState()
    badge, label = _make_mock_badges()

    try:
        with patch.object(_st, "refresh_status_badge", new_callable=AsyncMock):
            result = await handle_entity_sync(
                ["sync_services"], badge, label, "services"
            )
            assert result is True
            assert label.text == "Sync complete"
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_no_envs_enabled(initialized_db, mock_config):
    """Returns False when no environments are enabled for sync."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.state = _SyncTestState(enabled_sync_environments=set())
    badge, label = _make_mock_badges()

    try:
        result = await handle_entity_sync(["sync_services"], badge, label, "services")
        assert result is False
        assert "No environments enabled" in label.text
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_auth_missing(initialized_db, mock_config):
    """Returns False and skips build_api_client when auth is missing for all envs."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.state = _SyncTestState()
    badge, label = _make_mock_badges()

    try:
        with (
            patch.object(
                _st,
                "ensure_admin_auth",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(_st, "build_api_client", new_callable=AsyncMock) as mock_build,
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
        ):
            result = await handle_entity_sync(
                ["sync_services"], badge, label, "services"
            )
            # Returns False because all envs failed auth
            assert result is False
            mock_build.assert_not_called()
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_unauthorized(initialized_db, mock_config):
    """Returns False when 401 error occurs for all environments."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True
    _st.state = _SyncTestState()
    badge, label = _make_mock_badges()

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.is_client_error = True
    exc = httpx.HTTPStatusError(
        "Unauthorized", request=MagicMock(), response=mock_response
    )

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock),
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
            patch("app.ui.state.safe_notify"),
            patch(
                "app.ui.sync_handlers.SyncManager",
                return_value=MagicMock(sync_services=AsyncMock(side_effect=exc)),
            ),
        ):
            result = await handle_entity_sync(
                ["sync_services"], badge, label, "services"
            )
            assert result is False
            assert "failed" in label.text
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_reraises_non_401(initialized_db, mock_config):
    """Non-401 HTTPStatusError is caught but marked as failed."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True
    _st.state = _SyncTestState()
    badge, label = _make_mock_badges()

    mock_response = MagicMock()
    mock_response.status_code = 500
    exc = httpx.HTTPStatusError(
        "Server Error", request=MagicMock(), response=mock_response
    )

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock),
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
            patch(
                "app.ui.sync_handlers.SyncManager",
                return_value=MagicMock(sync_users=AsyncMock(side_effect=exc)),
            ),
        ):
            # Exceptions are caught by asyncio.gather with return_exceptions
            await handle_entity_sync(["sync_users"], badge, label, "users")
            # Should mark as failed but not crash
            assert "failed" in label.text
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_with_pre_sync(initialized_db, mock_config):
    """Calls pre_sync methods before main sync methods."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True
    _st.state = _SyncTestState()
    badge, label = _make_mock_badges()

    call_order = []
    mock_manager = MagicMock()
    mock_manager.sync_services = AsyncMock(
        side_effect=lambda **kw: call_order.append("sync_services")
    )
    mock_manager.sync_templates = AsyncMock(
        side_effect=lambda **kw: call_order.append("sync_templates")
    )

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock),
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
            patch("app.ui.sync_handlers.SyncManager", return_value=mock_manager),
        ):
            result = await handle_entity_sync(
                ["sync_templates"],
                badge,
                label,
                "templates",
                pre_sync=["sync_services"],
            )
            assert result is True
            assert call_order == ["sync_services", "sync_templates"]
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_progress_callback(initialized_db, mock_config):
    """Progress callback updates state.sync_message and sync_label.text with env prefix."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True
    _st.state = _SyncTestState()
    badge, label = _make_mock_badges()

    captured_messages = []

    async def fake_sync(progress=None):
        if progress:
            await progress("Syncing item 1/3")
            captured_messages.append(_st.state.sync_message)

    mock_manager = MagicMock()
    mock_manager.sync_users = fake_sync

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock),
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
            patch("app.ui.sync_handlers.SyncManager", return_value=mock_manager),
        ):
            await handle_entity_sync(["sync_users"], badge, label, "users")
            # Progress messages now include [environment] prefix
            assert any("Syncing item 1/3" in msg for msg in captured_messages)
            assert any("[development]" in msg for msg in captured_messages)
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_multiple_envs(initialized_db, mock_config):
    """Syncs all enabled environments in parallel."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True
    _st.state = _SyncTestState(enabled_sync_environments={"dev", "staging"})
    badge, label = _make_mock_badges()

    synced_envs = []

    def make_manager(api, concurrency, environment):
        manager = MagicMock()
        manager.sync_services = AsyncMock(
            side_effect=lambda **kw: synced_envs.append(environment)
        )
        return manager

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock),
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
            patch("app.ui.sync_handlers.SyncManager", side_effect=make_manager),
        ):
            result = await handle_entity_sync(
                ["sync_services"], badge, label, "services"
            )
            assert result is True
            assert set(synced_envs) == {"dev", "staging"}
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_explicit_environments(initialized_db, mock_config):
    """Syncs only explicitly specified environments, ignoring enabled_sync_environments."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True
    # Set enabled to dev and staging, but we'll only sync "prod"
    _st.state = _SyncTestState(enabled_sync_environments={"dev", "staging"})
    badge, label = _make_mock_badges()

    synced_envs = []

    def make_manager(api, concurrency, environment):
        manager = MagicMock()
        manager.sync_services = AsyncMock(
            side_effect=lambda **kw: synced_envs.append(environment)
        )
        return manager

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock),
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
            patch("app.ui.sync_handlers.SyncManager", side_effect=make_manager),
        ):
            result = await handle_entity_sync(
                ["sync_services"], badge, label, "services", environments=["prod"]
            )
            assert result is True
            # Should only sync prod, not dev/staging
            assert synced_envs == ["prod"]
    finally:
        _st.config, _st.state = original_config, original_state
