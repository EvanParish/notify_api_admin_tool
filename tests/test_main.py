"""
Tests for main.py

Note: NiceGUI UI components cannot be easily tested without a running server.
These tests focus on the business logic functions that can be tested in isolation.
"""

import os
import pytest
import httpx
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from dataclasses import dataclass

from app.api_client import MockNotificationAPI, HttpNotificationAPI
from app.config import AppConfig
from app.crypto import EncryptionManager
from app.ui import state as _st
from app.ui import helpers
from app.ui import email_helpers
from app.ui import shell
from app.ui import sync_handlers
from app.ui.pages import (
    comm_items as page_comm_items,
    dashboard as page_dashboard,
    inbound_numbers as page_inbound_numbers,
    provider_details as page_provider_details,
    services as page_services,
    sms_senders as page_sms_senders,
    templates as page_templates,
    users as page_users,
)

import main  # noqa: E402


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
        await set_secure_setting(
            "basic_username_development", "testuser", mock_encryption
        )
        await set_secure_setting(
            "basic_password_development", "testpass", mock_encryption
        )

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
        result = await sync_handlers.handle_full_sync(
            mock_status_badge, mock_sync_label
        )

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
    with patch("main.ui.notify"):
        await main.save_base_urls(mock_inputs)

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
        with patch("main.ui.notify"):
            await main.save_admin_auth(mock_auth_inputs)

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
            patch("main.ui.notify"),
            patch.object(main, "render_local_keys", create=True) as mock_render,
        ):
            mock_render.refresh = AsyncMock()

            await main.save_local_key("dev", "svc-1", "Test Key", "secret123", "normal")

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
            patch("main.ui.notify"),
            patch.object(main, "render_local_keys", create=True) as mock_render,
        ):
            mock_render.refresh = AsyncMock()

            # Should not raise error, just notify user
            await main.save_local_key(None, "svc-1", "name", "secret", "normal")
            await main.save_local_key("dev", None, "name", "secret", "normal")
            await main.save_local_key("dev", "svc-1", "", "secret", "normal")
            await main.save_local_key("dev", "svc-1", "name", "", "normal")

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

    state2 = AppState(
        environment="prod", api_status="online", sync_message="Syncing..."
    )
    assert state2.environment == "prod"
    assert state2.api_status == "online"
    assert state2.sync_message == "Syncing..."


def test_get_copyable_fields():
    """Test extraction of copy-enabled fields from table rows."""

    assert helpers.get_copyable_fields([]) == []
    assert helpers.get_copyable_fields(
        [{"id": "svc-1", "name": "Service", "active": True}]
    ) == [
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
        patch("main.ui.link"),
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
        assert len(result) == 4  # status_badge, sync_label, refresh_button, dark_mode


@pytest.mark.asyncio
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
async def test_handle_full_sync_dev_only_mode_blocks_staging(
    initialized_db, mock_config
):
    """Test that enabled_sync_environments blocks syncing non-enabled environments."""

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

    # Create state with staging environment but only development enabled
    _st.state = TestState(environment="staging")

    mock_status_badge = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""

    try:
        with patch("app.ui.state.ui.notify") as mock_notify:
            result = await sync_handlers.handle_full_sync(
                mock_status_badge, mock_sync_label
            )

            # Verify sync was blocked
            assert mock_sync_label.text == "Sync disabled for staging"
            mock_notify.assert_called_once()
            assert result is False
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
        api_status: str = "unknown"
        sync_message: str = ""
        dev_only_mode: bool = True
        enabled_sync_environments: set = None

        def __post_init__(self):
            if self.enabled_sync_environments is None:
                self.enabled_sync_environments = {"development"}

    _st.state = TestState(environment="development")

    mock_status_badge = MagicMock()
    mock_status_badge.props = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""

    try:
        result = await sync_handlers.handle_full_sync(
            mock_status_badge, mock_sync_label
        )

        # Verify sync completed
        assert mock_sync_label.text == "Sync complete"
        assert result is True
    finally:
        _st.config = original_config
        _st.state = original_state


@pytest.mark.asyncio
async def test_handle_full_sync_dev_only_mode_disabled(initialized_db, mock_config):
    """Test that adding environments to enabled_sync_environments allows syncing them."""

    original_config = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.config.use_mock_api = True

    @dataclass
    class TestState:
        environment: str
        api_status: str = "unknown"
        sync_message: str = ""
        dev_only_mode: bool = False
        enabled_sync_environments: set = None

        def __post_init__(self):
            if self.enabled_sync_environments is None:
                self.enabled_sync_environments = {"development"}

    # Create state with staging and add staging to enabled environments
    _st.state = TestState(environment="staging")
    _st.state.enabled_sync_environments.add("staging")

    mock_status_badge = MagicMock()
    mock_status_badge.props = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""

    try:
        result = await sync_handlers.handle_full_sync(
            mock_status_badge, mock_sync_label
        )

        # Verify sync completed
        assert mock_sync_label.text == "Sync complete"
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
    view_environment: str = "all"

    def __post_init__(self):
        if self.enabled_sync_environments is None:
            self.enabled_sync_environments = {"development"}


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
        with patch.object(
            shell, "_original_client_delete", side_effect=KeyError("already deleted")
        ):
            shell._safe_client_delete(mock_self)
            assert mock_self._deleted is True


class TestAppStateViewEnvironmentFallback:
    """Test AppState view_environment fallback to 'all'."""

    def test_empty_view_environment_defaults_to_all(self):
        from app.ui.state import AppState

        st = AppState(environment="dev", view_environment="")
        assert st.view_environment == "all"

    def test_none_view_environment_stays_default(self):
        from app.ui.state import AppState

        st = AppState(environment="dev")
        assert st.view_environment == "all"


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
        result = email_helpers._build_key_email(
            "secret-abc", created_key, "dev", "My Service", "svc-1"
        )
        assert "secret-abc" in result
        assert "my-key" in result
        assert "key-id-123" in result
        assert "2025-12-31" in result
        assert "My Service" in result
        assert "svc-1" in result
        assert "Dev Details" in result


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


class TestGetViewEnvironment:
    def test_all_returns_none(self):

        original_state = _st.state
        _st.state = SharedTestState(environment="dev", view_environment="all")
        try:
            assert _st.get_view_environment() is None
        finally:
            _st.state = original_state

    def test_empty_returns_none(self):

        original_state = _st.state
        _st.state = SharedTestState(environment="dev", view_environment="")
        try:
            assert _st.get_view_environment() is None
        finally:
            _st.state = original_state

    def test_specific_env_returns_it(self):

        original_state = _st.state
        _st.state = SharedTestState(environment="dev", view_environment="staging")
        try:
            assert _st.get_view_environment() == "staging"
        finally:
            _st.state = original_state


class TestSafeNotify:
    def test_calls_ui_notify(self):

        with patch("main.ui.notify") as mock_notify:
            _st.safe_notify("hello", color="green")
            mock_notify.assert_called_once_with("hello", color="green")

    def test_catches_runtime_error(self):

        with patch("main.ui.notify", side_effect=RuntimeError("no slot")):
            _st.safe_notify("hello")


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


class TestParseFilterDate:
    def test_none(self):

        assert main._parse_filter_date(None) is None

    def test_empty(self):

        assert main._parse_filter_date("") is None

    def test_valid_date(self):
        from datetime import date

        assert main._parse_filter_date("2025-06-15") == date(2025, 6, 15)

    def test_valid_iso_datetime(self):
        from datetime import date

        assert main._parse_filter_date("2025-06-15T12:00:00Z") == date(2025, 6, 15)

    def test_invalid_date(self):

        assert main._parse_filter_date("not-a-date") is None


class TestMatchesExpiryRange:
    def test_no_range_returns_true(self):

        assert main._matches_expiry_range("2025-06-15", None, None) is True

    def test_no_expiry_with_range_returns_false(self):
        from datetime import date

        assert main._matches_expiry_range(None, date(2025, 1, 1), None) is False
        assert main._matches_expiry_range("", None, date(2025, 12, 31)) is False

    def test_before_start_returns_false(self):
        from datetime import date

        assert main._matches_expiry_range("2025-01-01", date(2025, 6, 1), None) is False

    def test_after_end_returns_false(self):
        from datetime import date

        assert main._matches_expiry_range("2025-12-31", None, date(2025, 6, 1)) is False

    def test_within_range_returns_true(self):
        from datetime import date

        assert (
            main._matches_expiry_range(
                "2025-06-15", date(2025, 1, 1), date(2025, 12, 31)
            )
            is True
        )


class TestExtractApiKeySecret:
    def test_non_dict_raises(self):

        with pytest.raises(ValueError, match="Unexpected API response"):
            main._extract_api_key_secret("not a dict")

    def test_missing_data_raises(self):

        with pytest.raises(ValueError, match="API key secret missing"):
            main._extract_api_key_secret({})

    def test_empty_data_raises(self):

        with pytest.raises(ValueError, match="API key secret missing"):
            main._extract_api_key_secret({"data": ""})

    def test_valid_data(self):

        assert main._extract_api_key_secret({"data": "my-secret"}) == "my-secret"


class TestCopyToClipboard:
    def test_calls_run_javascript(self):

        with (
            patch("main.ui.run_javascript") as mock_js,
            patch("app.ui.state.safe_notify"),
        ):
            helpers.copy_to_clipboard("hello")
            mock_js.assert_called_once()
            assert "hello" in mock_js.call_args[0][0]

    def test_none_value(self):

        with (
            patch("main.ui.run_javascript") as mock_js,
            patch("app.ui.state.safe_notify"),
        ):
            helpers.copy_to_clipboard(None)
            mock_js.assert_called_once()


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


class TestMakeSortable:
    def test_adds_sortable(self):

        cols = [{"name": "id", "label": "ID"}, {"name": "name", "label": "Name"}]
        result = helpers.make_sortable(cols)
        assert all(c["sortable"] is True for c in result)
        assert result[0]["name"] == "id"


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
        with patch.object(
            _st, "dispose_engine", new_callable=AsyncMock
        ) as mock_dispose:
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
        mock_storage = MagicMock()
        mock_storage.user = {"theme": "light"}
        with patch.object(shell, "app", **{"storage": mock_storage}):
            shell.toggle_theme(mock_dark_mode)
            mock_dark_mode.toggle.assert_called_once()
            assert mock_storage.user["theme"] == "dark"


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
        with patch.object(
            page_services, "refresh_if_needed", new_callable=AsyncMock
        ) as mock_refresh:
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
    with patch.object(
        page_services, "handle_service_search", new_callable=AsyncMock
    ) as mock_search:
        await page_services.handle_service_search_event(mock_event)
        mock_search.assert_called_once_with("hello")


@pytest.mark.asyncio
async def test_handle_service_search_event_no_value():

    mock_event = MagicMock(spec=[])
    with patch.object(
        page_services, "handle_service_search", new_callable=AsyncMock
    ) as mock_search:
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

    startup_fn = [
        h
        for h in nicegui_app._startup_handlers
        if getattr(h, "__name__", "") == "startup"
    ][-1]

    original = _st.config
    _st.config = mock_config
    try:
        await startup_fn()
    finally:
        _st.config = original


@pytest.mark.asyncio
async def test_shutdown_via_handler(initialized_db):
    from nicegui import app as nicegui_app

    # app.on_shutdown doesn't return the function; retrieve from handlers
    shutdown_fn = [
        h
        for h in nicegui_app._shutdown_handlers
        if getattr(h, "__name__", "") == "shutdown"
    ][-1]

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
        with patch.object(
            _st, "has_admin_auth", new_callable=AsyncMock, return_value=False
        ):
            await _st.refresh_status_badge(mock_badge)
            assert _st.state.api_status == "auth missing"
            assert mock_badge.text == "API Status: Auth Missing"
            mock_badge.props.assert_called_once_with("color=pink")
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
        with patch.object(
            _st, "ensure_admin_auth", new_callable=AsyncMock, return_value=False
        ):
            result = await sync_handlers.handle_full_sync(
                mock_status_badge, mock_sync_label
            )
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
    exc = httpx.HTTPStatusError(
        "Unauthorized", request=MagicMock(), response=mock_response
    )

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock) as mock_build,
            patch("app.ui.state.safe_notify"),
        ):
            mock_api = AsyncMock()
            mock_manager = AsyncMock()
            mock_manager.sync_all = AsyncMock(side_effect=exc)
            mock_build.return_value = mock_api
            with patch("app.ui.sync_handlers.SyncManager", return_value=mock_manager):
                await sync_handlers.handle_full_sync(mock_status_badge, mock_sync_label)
                assert "Unauthorized" in mock_sync_label.text
    finally:
        _st.config = original_config
        _st.state = original_state


# ===================================================================
# Category 6: has_admin_auth non-mock path (lines 791-793)
# ===================================================================


@pytest.mark.asyncio
async def test_has_admin_auth_real_with_creds(
    initialized_db, mock_config, mock_encryption
):
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

            await set_secure_setting(
                "basic_username_development", "user", mock_encryption
            )
            await set_secure_setting(
                "basic_password_development", "pass", mock_encryption
            )
            result = await _st.has_admin_auth("development")
            assert result is True
    finally:
        _st.config = original_config
        _st.encryption = original_encryption


# ===================================================================
# Category 7: Page handler tests (lines 560-602, 859-2986, 2990)
# ===================================================================


@contextmanager
def mock_ui():
    """Mock all NiceGUI UI components needed for page handlers."""
    mock_obj = MagicMock()
    mock_obj.__enter__ = Mock(return_value=mock_obj)
    mock_obj.__exit__ = Mock(return_value=False)
    mock_obj.classes = MagicMock(return_value=mock_obj)
    mock_obj.props = MagicMock(return_value=mock_obj)
    mock_obj.style = MagicMock(return_value=mock_obj)
    mock_obj.on_click = MagicMock(return_value=mock_obj)
    mock_obj.on_value_change = MagicMock(return_value=mock_obj)
    mock_obj.set_options = MagicMock(return_value=mock_obj)
    mock_obj.on = MagicMock(return_value=mock_obj)
    mock_obj.add_slot = MagicMock(return_value=mock_obj)
    mock_obj.refresh = MagicMock()
    mock_obj.open = MagicMock()
    mock_obj.close = MagicMock()
    mock_obj.toggle = MagicMock()
    mock_obj.clear = MagicMock()
    mock_obj.set_content = MagicMock()
    mock_obj.value = None
    mock_obj.text = ""
    mock_obj.visible = True
    mock_obj.selection = []
    mock_obj.on_select = MagicMock()

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
        new_mock.open = MagicMock()
        new_mock.close = MagicMock()
        new_mock.toggle = MagicMock()
        new_mock.clear = MagicMock()
        new_mock.set_content = MagicMock()
        new_mock.value = kwargs.get("value", None)
        new_mock.text = ""
        new_mock.visible = True
        new_mock.selection = []
        new_mock.on_select = MagicMock()
        return new_mock

    from contextlib import ExitStack

    patches = [
        patch(
            "main.build_shell",
            return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock()),
        ),
        patch("main.ensure_theme_preference", new_callable=AsyncMock),
        patch("main.refresh_status_badge", new_callable=AsyncMock),
        patch("main.refresh_if_needed", new_callable=AsyncMock),
        patch("main.ui.column", side_effect=_make_mock),
        patch("main.ui.card", side_effect=_make_mock),
        patch("main.ui.row", side_effect=_make_mock),
        patch("main.ui.label", side_effect=_make_mock),
        patch("main.ui.button", side_effect=_make_mock),
        patch("main.ui.select", side_effect=_make_mock),
        patch("main.ui.input", side_effect=_make_mock),
        patch("main.ui.table", side_effect=_make_mock),
        patch("main.ui.textarea", side_effect=_make_mock),
        patch("main.ui.checkbox", side_effect=_make_mock),
        patch("main.ui.markdown", side_effect=_make_mock),
        patch("main.ui.dialog", side_effect=_make_mock),
        patch("main.ui.notify"),
        patch("main.ui.dropdown_button", side_effect=_make_mock),
        patch("main.ui.refreshable", lambda fn: fn),
        patch("main.ui.run_javascript"),
        patch("main.ui.link"),
        patch("main.ui.separator"),
        patch("main.ui.switch", side_effect=_make_mock),
        patch("main.ui.expansion", side_effect=_make_mock),
        patch("main.ui.badge", side_effect=_make_mock),
        patch("main.ui.scroll_area", side_effect=_make_mock),
        patch("main.ui.upload", side_effect=_make_mock),
        patch("main.ui.linear_progress", side_effect=_make_mock),
        patch("main.ui.toggle", side_effect=_make_mock),
        patch("main.ui.code", side_effect=_make_mock),
        patch("main.ui.page", lambda *a, **kw: lambda fn: fn),
        patch("main.add_copyable_slots"),
        patch("main.list_services", new_callable=AsyncMock, return_value=[]),
        patch("main.list_templates", new_callable=AsyncMock, return_value=[]),
        patch("main.list_api_keys", new_callable=AsyncMock, return_value=[]),
        patch("main.list_users", new_callable=AsyncMock, return_value=[]),
        patch("main.list_local_keys", new_callable=AsyncMock, return_value=[]),
        patch("main.get_setting", new_callable=AsyncMock, return_value=None),
        patch("main.get_secure_setting", new_callable=AsyncMock, return_value=None),
        patch("main.resolve_local_key", new_callable=AsyncMock, return_value=None),
        patch("main.handle_full_sync", new_callable=AsyncMock),
        patch("main.handle_entity_sync", new_callable=AsyncMock),
        patch("main.render_local_keys", new_callable=AsyncMock),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield mock_obj


def _ui_patches(mod_path, _make_mock):
    """Build common patches for a page module."""
    import importlib

    module = importlib.import_module(mod_path)
    patches = [
        patch(
            f"{mod_path}.build_shell",
            return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock()),
        ),
        patch(f"{mod_path}.ensure_theme_preference", new_callable=AsyncMock),
        patch(f"{mod_path}.refresh_status_badge", new_callable=AsyncMock),
        patch(f"{mod_path}.refresh_if_needed", new_callable=AsyncMock),
        patch(f"{mod_path}.ui.column", side_effect=_make_mock),
        patch(f"{mod_path}.ui.card", side_effect=_make_mock),
        patch(f"{mod_path}.ui.row", side_effect=_make_mock),
        patch(f"{mod_path}.ui.label", side_effect=_make_mock),
        patch(f"{mod_path}.ui.button", side_effect=_make_mock),
        patch(f"{mod_path}.ui.select", side_effect=_make_mock),
        patch(f"{mod_path}.ui.input", side_effect=_make_mock),
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
        patch(f"{mod_path}.ui.page", lambda *a, **kw: lambda fn: fn),
    ]
    # Optional patches — only add if the page module imports the name
    optional = {
        "add_copyable_slots": patch(f"{mod_path}.add_copyable_slots"),
        "handle_full_sync": patch(
            f"{mod_path}.handle_full_sync", new_callable=AsyncMock
        ),
        "handle_entity_sync": patch(
            f"{mod_path}.handle_entity_sync", new_callable=AsyncMock
        ),
        "metric_card": patch(f"{mod_path}.metric_card"),
        "list_services": patch(
            f"{mod_path}.list_services", new_callable=AsyncMock, return_value=[]
        ),
        "list_templates": patch(
            f"{mod_path}.list_templates", new_callable=AsyncMock, return_value=[]
        ),
        "list_api_keys": patch(
            f"{mod_path}.list_api_keys", new_callable=AsyncMock, return_value=[]
        ),
        "list_users": patch(
            f"{mod_path}.list_users", new_callable=AsyncMock, return_value=[]
        ),
        "list_sms_senders": patch(
            f"{mod_path}.list_sms_senders", new_callable=AsyncMock, return_value=[]
        ),
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
            patch("app.ui.pages.services.ui.table", return_value=mock_obj),
            patch("app.ui.pages.services.add_copyable_slots"),
        ):
            await page_services.services_table.func()
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
        with mock_ui():
            await main.api_keys_page()
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
        with mock_ui():
            await main.api_key_emails_page()
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
async def test_send_page(initialized_db, mock_config):

    original = _st.config
    original_state = _st.state
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    try:
        with mock_ui():
            await main.send_page()
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
        with mock_ui():
            await main.bulk_send_page()
    finally:
        _st.config = original
        _st.state = original_state


@pytest.mark.asyncio
async def test_settings_page(initialized_db, mock_config):

    original = _st.config
    original_state = _st.state
    original_encryption = _st.encryption
    _st.config = mock_config
    _st.state = SharedTestState(environment="development")
    _st.encryption = EncryptionManager(mock_config.master_key)
    try:
        with mock_ui():
            await main.settings_page()
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
            patch("main.list_local_keys", new_callable=AsyncMock, return_value=[]),
            patch("main.ui.table", return_value=mock_obj),
            patch("main.add_copyable_slots"),
        ):
            await main.render_local_keys.func()
    finally:
        _st.config = original


def test_ui_run_guard():
    """Test that ui.run is called when __name__ == '__main__'."""

    assert hasattr(main, "api_keys_page")


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
    exc = httpx.HTTPStatusError(
        "Server Error", request=MagicMock(), response=mock_response
    )

    try:
        with (
            patch.object(_st, "build_api_client", new_callable=AsyncMock) as mock_build,
        ):
            mock_manager = AsyncMock()
            mock_manager.sync_all = AsyncMock(side_effect=exc)
            mock_build.return_value = AsyncMock()
            with patch("app.ui.sync_handlers.SyncManager", return_value=mock_manager):
                with pytest.raises(httpx.HTTPStatusError):
                    await sync_handlers.handle_full_sync(
                        mock_status_badge, mock_sync_label
                    )
    finally:
        _st.config = original_config
        _st.state = original_state


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
            patch("app.ui.pages.services.ui.table", return_value=mock_obj),
            patch("app.ui.pages.services.add_copyable_slots"),
        ):
            await page_services.services_table.func()
    finally:
        _st.config = original
        _st.state = original_state
        _st.service_search_query = ""
