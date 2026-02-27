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
async def test_handle_entity_sync_disabled(initialized_db, mock_config):
    """Returns False when sync is disabled for the environment."""

    original_config, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.state = _SyncTestState(environment="staging")
    badge, label = _make_mock_badges()

    try:
        with patch("app.ui.state.ui.notify"):
            result = await handle_entity_sync(
                ["sync_services"], badge, label, "services"
            )
            assert result is False
            assert "Sync disabled" in label.text
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_auth_missing(initialized_db, mock_config):
    """Returns False and skips build_api_client when auth is missing."""

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
        ):
            result = await handle_entity_sync(
                ["sync_services"], badge, label, "services"
            )
            assert result is False
            mock_build.assert_not_called()
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_unauthorized(initialized_db, mock_config):
    """Returns False and calls handle_unauthorized on 401."""

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
            assert "Unauthorized" in label.text
    finally:
        _st.config, _st.state = original_config, original_state


@pytest.mark.asyncio
async def test_handle_entity_sync_reraises_non_401(initialized_db, mock_config):
    """Re-raises HTTPStatusError for non-401 status codes."""

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
            patch(
                "app.ui.sync_handlers.SyncManager",
                return_value=MagicMock(sync_users=AsyncMock(side_effect=exc)),
            ),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await handle_entity_sync(["sync_users"], badge, label, "users")
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
    """Progress callback updates state.sync_message and sync_label.text."""

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
            assert "Syncing item 1/3" in captured_messages
    finally:
        _st.config, _st.state = original_config, original_state
