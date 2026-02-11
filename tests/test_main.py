"""
Tests for main.py

Note: NiceGUI UI components cannot be easily tested without a running server.
These tests focus on the business logic functions that can be tested in isolation.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from dataclasses import dataclass

from app.api_client import MockNotificationAPI, HttpNotificationAPI
from app.config import AppConfig
from app.crypto import EncryptionManager


# Import the functions we want to test
# We'll need to mock the config and other global state
@pytest.fixture
def mock_config():
    """Create a mock config for testing."""
    return AppConfig(
        master_key="test-key-for-main",
        api_hosts={"development": "http://dev.test.com", "staging": "http://staging.test.com"},
        use_mock_api=True,
        database_path=":memory:",
        max_concurrency=5
    )


@pytest.fixture
def mock_encryption(mock_config):
    """Create a mock encryption manager."""
    return EncryptionManager(mock_config.master_key)


@pytest.mark.asyncio
async def test_ensure_default_hosts(initialized_db, mock_config):
    """Test that default hosts are set if they don't exist."""
    from app.repository import get_setting, set_setting
    
    # Import the function under test
    import main
    
    # Temporarily replace the config
    original_config = main.config
    main.config = mock_config
    
    try:
        # Clear any existing settings first
        from sqlalchemy import delete
        from app.models import Setting
        from app.db import get_session
        async with get_session() as session:
            await session.execute(delete(Setting))
            await session.commit()
        
        # Call the function
        await main.ensure_default_hosts()
        
        # Verify settings were created
        for env, url in mock_config.api_hosts.items():
            setting_value = await get_setting(f"base_url_{env}")
            assert setting_value == url
    finally:
        main.config = original_config


@pytest.mark.asyncio
async def test_ensure_default_hosts_existing(initialized_db, mock_config):
    """Test that existing host settings are not overwritten."""
    from app.repository import get_setting, set_setting
    
    import main
    original_config = main.config
    main.config = mock_config
    
    try:
        # Set an existing value
        await set_setting("base_url_development", "http://custom.test.com")
        
        # Call the function
        await main.ensure_default_hosts()
        
        # Verify the existing value was not overwritten
        setting_value = await get_setting("base_url_development")
        assert setting_value == "http://custom.test.com"
        
        # But staging should be set to default
        staging_value = await get_setting("base_url_staging")
        assert staging_value == mock_config.api_hosts["staging"]
    finally:
        main.config = original_config


@pytest.mark.asyncio
async def test_build_api_client_mock(initialized_db, mock_config):
    """Test building an API client with mock enabled."""
    import main
    original_config = main.config
    main.config = mock_config
    main.config.use_mock_api = True
    
    try:
        api = await main.build_api_client("development")
        assert isinstance(api, MockNotificationAPI)
    finally:
        main.config = original_config


@pytest.mark.asyncio
async def test_build_api_client_http(initialized_db, mock_config, mock_encryption):
    """Test building an HTTP API client."""
    from app.repository import set_setting, set_secure_setting
    
    import main
    original_config = main.config
    original_encryption = main.encryption
    main.config = mock_config
    main.config.use_mock_api = False
    main.encryption = mock_encryption
    
    try:
        # Set up required settings
        await set_setting("base_url_development", "http://api.test.com")
        await set_secure_setting("basic_username_development", "testuser", mock_encryption)
        await set_secure_setting("basic_password_development", "testpass", mock_encryption)
        
        api = await main.build_api_client("development")
        assert isinstance(api, HttpNotificationAPI)
        assert api.base_url == "http://api.test.com"
    finally:
        main.config = original_config
        main.encryption = original_encryption


@pytest.mark.asyncio
async def test_build_api_client_missing_url(initialized_db, mock_config):
    """Test that build_api_client raises error when URL is missing."""
    import main
    original_config = main.config
    main.config = AppConfig(
        master_key="test-key",
        api_hosts={},
        use_mock_api=False,
        database_path=":memory:",
        max_concurrency=5
    )
    
    try:
        with pytest.raises(RuntimeError, match="Base URL missing"):
            await main.build_api_client("nonexistent")
    finally:
        main.config = original_config


@pytest.mark.asyncio
async def test_refresh_status_badge(initialized_db, mock_config):
    """Test refreshing the status badge."""
    import main
    original_config = main.config
    original_state = main.state
    main.config = mock_config
    main.config.use_mock_api = True
    
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
    
    main.state = TestState(environment="development")
    
    try:
        await main.refresh_status_badge(mock_badge)
        
        # MockNotificationAPI always returns True for healthcheck
        assert main.state.api_status == "online"
        assert mock_badge.text == "API Status: Online"
        mock_badge.props.assert_called_once_with("color=green")
    finally:
        main.config = original_config
        main.state = original_state


@pytest.mark.asyncio
async def test_refresh_status_badge_offline(initialized_db, mock_config):
    """Test refreshing the status badge when API is offline."""
    import main
    original_config = main.config
    original_state = main.state
    
    mock_config.use_mock_api = False
    main.config = mock_config
    
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
    
    main.state = TestState(environment="development")
    
    try:
        # Mock build_api_client to return an API that fails healthcheck
        with patch.object(main, 'build_api_client') as mock_build:
            mock_api = AsyncMock()
            mock_api.healthcheck = AsyncMock(return_value=False)
            mock_build.return_value = mock_api
            
            await main.refresh_status_badge(mock_badge)
            
            assert main.state.api_status == "offline"
            assert mock_badge.text == "API Status: Offline"
            mock_badge.props.assert_called_once_with("color=red")
    finally:
        main.config = original_config
        main.state = original_state


@pytest.mark.asyncio
async def test_handle_full_sync(initialized_db, mock_config):
    """Test the full sync handler."""
    import main
    original_config = main.config
    original_state = main.state
    main.config = mock_config
    main.config.use_mock_api = True
    
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
    
    main.state = TestState(environment="development")
    
    mock_status_badge = MagicMock()
    mock_status_badge.text = ""
    mock_status_badge.props = MagicMock()
    
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""
    
    try:
        # Mock refresh_tables since it depends on UI refreshables
        with patch.object(main, 'refresh_tables', new_callable=AsyncMock) as mock_refresh:
            await main.handle_full_sync(mock_status_badge, mock_sync_label)
            
            # Verify sync messages were set
            assert mock_sync_label.text == "Sync complete"
            
            # Verify refresh_tables was called
            mock_refresh.assert_called_once()
    finally:
        main.config = original_config
        main.state = original_state


@pytest.mark.asyncio
async def test_save_base_urls(initialized_db):
    """Test saving base URLs."""
    from app.repository import get_setting
    import main
    
    # Create mock inputs
    mock_inputs = {
        "dev": MagicMock(value="http://dev.new.com"),
        "prod": MagicMock(value="http://prod.new.com"),
        "empty": MagicMock(value=""),
    }
    
    # Mock ui.notify to avoid NiceGUI context issues
    with patch('main.ui.notify'):
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
    import main
    
    original_encryption = main.encryption
    main.encryption = mock_encryption
    
    try:
        # Create mock auth inputs
        mock_auth_inputs = {
            "dev": {
                "user": MagicMock(value="devuser"),
                "pass": MagicMock(value="devpass")
            },
            "prod": {
                "user": MagicMock(value=""),
                "pass": MagicMock(value="prodpass")
            }
        }
        
        # Mock ui.notify to avoid NiceGUI context issues
        with patch('main.ui.notify'):
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
        main.encryption = original_encryption


@pytest.mark.asyncio
async def test_save_local_key_success(initialized_db, mock_encryption):
    """Test saving a local API key."""
    from app.repository import list_local_keys
    import main
    
    original_encryption = main.encryption
    main.encryption = mock_encryption
    
    try:
        # Mock ui.notify and render_local_keys.refresh()
        with patch('main.ui.notify'), \
             patch.object(main, 'render_local_keys', create=True) as mock_render:
            mock_render.refresh = AsyncMock()
            
            await main.save_local_key("svc-1", "Test Key", "secret123", "normal")
            
            # Verify key was saved
            keys = await list_local_keys(service_id="svc-1")
            assert len(keys) == 1
            assert keys[0].key_name == "Test Key"
            assert keys[0].key_type == "normal"
    finally:
        main.encryption = original_encryption


@pytest.mark.asyncio
async def test_save_local_key_missing_params(initialized_db, mock_encryption):
    """Test that save_local_key rejects missing parameters."""
    import main
    
    original_encryption = main.encryption
    main.encryption = mock_encryption
    
    try:
        # Mock ui.notify and render_local_keys.refresh()
        with patch('main.ui.notify'), \
             patch.object(main, 'render_local_keys', create=True) as mock_render:
            mock_render.refresh = AsyncMock()
            
            # Should not raise error, just notify user
            await main.save_local_key(None, "name", "secret", "normal")
            await main.save_local_key("svc-1", "", "secret", "normal")
            await main.save_local_key("svc-1", "name", "", "normal")
            
            # Verify no keys were saved
            from app.repository import list_local_keys
            keys = await list_local_keys()
            assert len(keys) == 0
    finally:
        main.encryption = original_encryption


def test_metric_card():
    """Test the metric_card helper function."""
    import main
    
    # This function creates UI elements, so we just verify it doesn't crash
    # when called (we can't easily test the UI output without NiceGUI running)
    with patch('main.ui.card') as mock_card:
        mock_card.return_value.__enter__ = Mock(return_value=None)
        mock_card.return_value.__exit__ = Mock(return_value=None)
        
        with patch('main.ui.label'):
            main.metric_card("Test Title", 42)
            
            # Verify card was created
            assert mock_card.called


def test_app_state_creation():
    """Test AppState dataclass creation."""
    from main import AppState
    
    state = AppState(environment="test")
    assert state.environment == "test"
    assert state.api_status == "unknown"
    assert state.sync_message == ""
    
    state2 = AppState(environment="prod", api_status="online", sync_message="Syncing...")
    assert state2.environment == "prod"
    assert state2.api_status == "online"
    assert state2.sync_message == "Syncing..."


@pytest.mark.asyncio
async def test_startup_function(initialized_db, mock_config):
    """Test the startup function by calling ensure_default_hosts directly."""
    import main
    
    original_config = main.config
    main.config = mock_config
    
    try:
        # The startup function is decorated, so test its components directly
        # Test create_all
        await main.create_all()
        
        # Test ensure_default_hosts
        await main.ensure_default_hosts()
        
        # Verify ensure_default_hosts worked (settings should exist)
        from app.repository import get_setting
        for env in mock_config.api_hosts.keys():
            setting = await get_setting(f"base_url_{env}")
            assert setting is not None
    finally:
        main.config = original_config


def test_build_shell():
    """Test build_shell creates the UI structure."""
    import main
    
    # Mock all UI components to avoid NiceGUI context issues
    with patch('main.ui.left_drawer') as mock_drawer, \
         patch('main.ui.header') as mock_header, \
         patch('main.ui.row') as mock_row, \
         patch('main.ui.badge') as mock_badge, \
         patch('main.ui.label') as mock_label, \
         patch('main.ui.button') as mock_button, \
         patch('main.ui.link'):
        
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
        
        # Call build_shell
        result = main.build_shell()
        
        # Verify it returns a tuple
        assert isinstance(result, tuple)
        assert len(result) == 3  # status_badge, sync_label, refresh_button


@pytest.mark.asyncio  
async def test_refresh_tables():
    """Test refresh_tables function."""
    import main
    
    # Mock the table refresh functions
    with patch.object(main, 'services_table', create=True) as mock_services:
        
        mock_services.refresh = AsyncMock()
        
        await main.refresh_tables()
        
        mock_services.refresh.assert_called_once()


def test_module_level_initialization():
    """Test that module-level objects are initialized correctly."""
    import main
    
    # Verify global objects exist
    assert main.config is not None
    assert isinstance(main.config, AppConfig)
    
    assert main.encryption is not None
    assert isinstance(main.encryption, EncryptionManager)
    
    assert main.state is not None
    assert hasattr(main.state, 'environment')
    assert hasattr(main.state, 'api_status')
    assert hasattr(main.state, 'sync_message')


@pytest.mark.asyncio
async def test_integration_full_workflow(initialized_db, mock_config, mock_encryption):
    """Integration test simulating a full workflow."""
    import main
    from app.repository import get_setting, set_setting, list_services
    
    original_config = main.config
    original_encryption = main.encryption
    main.config = mock_config
    main.encryption = mock_encryption
    
    try:
        # 1. Initialize default hosts
        await main.ensure_default_hosts()
        
        # 2. Verify hosts were set
        for env in mock_config.api_hosts.keys():
            url = await get_setting(f"base_url_{env}")
            assert url == mock_config.api_hosts[env]
        
        # 3. Build API client
        api = await main.build_api_client("development")
        assert api is not None
        
        # 4. Test healthcheck would work
        result = await api.healthcheck()
        assert result is True  # MockNotificationAPI always returns True
        
        # Integration test passes
        assert True
    finally:
        main.config = original_config
        main.encryption = original_encryption


@pytest.mark.asyncio
async def test_handle_full_sync_dev_only_mode_blocks_staging(initialized_db, mock_config):
    """Test that enabled_sync_environments blocks syncing non-enabled environments."""
    import main
    original_config = main.config
    original_state = main.state
    main.config = mock_config
    main.config.use_mock_api = True
    
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
    main.state = TestState(environment="staging")
    
    mock_status_badge = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""
    
    try:
        with patch.object(main, 'refresh_tables', new_callable=AsyncMock) as mock_refresh:
            with patch('main.ui.notify') as mock_notify:
                await main.handle_full_sync(mock_status_badge, mock_sync_label)
                
                # Verify sync was blocked
                assert mock_sync_label.text == "Sync disabled for staging"
                mock_notify.assert_called_once()
                # Refresh should not have been called
                mock_refresh.assert_not_called()
    finally:
        main.config = original_config
        main.state = original_state


@pytest.mark.asyncio
async def test_handle_full_sync_dev_only_mode_allows_dev(initialized_db, mock_config):
    """Test that enabled_sync_environments allows syncing enabled environments."""
    import main
    original_config = main.config
    original_state = main.state
    main.config = mock_config
    main.config.use_mock_api = True
    
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
    
    main.state = TestState(environment="development")
    
    mock_status_badge = MagicMock()
    mock_status_badge.props = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""
    
    try:
        with patch.object(main, 'refresh_tables', new_callable=AsyncMock) as mock_refresh:
            await main.handle_full_sync(mock_status_badge, mock_sync_label)
            
            # Verify sync completed
            assert mock_sync_label.text == "Sync complete"
            # Refresh should have been called
            mock_refresh.assert_called_once()
    finally:
        main.config = original_config
        main.state = original_state


@pytest.mark.asyncio
async def test_handle_full_sync_dev_only_mode_disabled(initialized_db, mock_config):
    """Test that adding environments to enabled_sync_environments allows syncing them."""
    import main
    original_config = main.config
    original_state = main.state
    main.config = mock_config
    main.config.use_mock_api = True
    
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
    main.state = TestState(environment="staging")
    main.state.enabled_sync_environments.add("staging")
    
    mock_status_badge = MagicMock()
    mock_status_badge.props = MagicMock()
    mock_sync_label = MagicMock()
    mock_sync_label.text = ""
    
    try:
        with patch.object(main, 'refresh_tables', new_callable=AsyncMock) as mock_refresh:
            await main.handle_full_sync(mock_status_badge, mock_sync_label)
            
            # Verify sync completed
            assert mock_sync_label.text == "Sync complete"
            # Refresh should have been called
            mock_refresh.assert_called_once()
    finally:
        main.config = original_config
        main.state = original_state
