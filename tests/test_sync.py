import pytest
from sqlalchemy import select

from app.db import create_all, get_session, init_engine
from app.models import Service, Template, User
from app.sync import SyncManager
from app.api_client import NotificationAPI

import tests.testing_data as testing_data


class FakeAPI(NotificationAPI):
    def __init__(self, services=None, templates=None, users=None):
        self.services_data = services or testing_data.service_data["data"]
        self.templates_data = templates or testing_data.template_data["data"]
        self.users_data = users or []
        self._template_generator = None

    async def get_services(self):
        return self.services_data

    async def get_templates(self, service_id: str):
        # If template_generator is set, use it to generate unique templates per service
        if self._template_generator:
            return self._template_generator(service_id)
        return self.templates_data

    async def get_api_keys(self, service_id: str):
        return []

    async def get_sms_senders(self, service_id: str):
        return []

    async def get_users(self):
        return self.users_data

    async def get_provider_details(self):
        return []

    async def get_communication_items(self):
        return []

    async def get_inbound_numbers(self):
        return []

    async def send_notification(self, *args, **kwargs):
        return {}

    async def healthcheck(self):
        return True


@pytest.fixture(scope="function")
def setup_db(tmp_path):
    db_file = tmp_path / "test.db"
    init_engine(str(db_file))
    yield


@pytest.mark.asyncio
async def test_sync_services_templates(setup_db):
    await create_all()
    api = FakeAPI()
    sync = SyncManager(api, max_concurrency=5)

    await sync.sync_services()
    async with get_session() as session:
        services = (await session.execute(select(Service))).scalars().all()
        assert len(services) == 1
        svc = services[0]
        assert svc.id == "d6aa2c68-a2d9-4437-ab19-3ae8eb202553"
        assert svc.name == "VA Notify"
        assert svc.active is True
        assert svc.restricted is False

    await sync.sync_templates()
    async with get_session() as session:
        templates = (await session.execute(select(Template))).scalars().all()
        assert len(templates) == 2
        ids = {t.id for t in templates}
        assert {
            "e98f2fd4-f307-4092-be15-34a8d903aaaa",
            "aef3658a-1c78-443a-9c74-688ee96f18be",
        } == ids
        email_template = next(t for t in templates if t.template_type == "email")
        assert email_template.subject == "Test ((title))"
        assert email_template.service_id == "d6aa2c68-a2d9-4437-ab19-3ae8eb202553"


@pytest.mark.asyncio
async def test_sync_all(setup_db):
    await create_all()
    api = FakeAPI()
    sync = SyncManager(api, max_concurrency=10)

    await sync.sync_all()

    async with get_session() as session:
        services = (await session.execute(select(Service))).scalars().all()
        templates = (await session.execute(select(Template))).scalars().all()

        assert len(services) >= 1
        assert len(templates) >= 2


@pytest.mark.asyncio
async def test_sync_with_progress_callback(setup_db):
    await create_all()
    api = FakeAPI()
    sync = SyncManager(api, max_concurrency=5)

    messages = []

    async def progress(msg: str):
        messages.append(msg)

    await sync.sync_services(progress=progress)
    await sync.sync_templates(progress=progress)

    assert "Syncing services" in messages
    assert any("Templates for" in msg for msg in messages)


@pytest.mark.asyncio
async def test_sync_empty_data_no_errors(setup_db):
    """Test that syncing empty data arrays doesn't cause errors."""
    await create_all()

    api = FakeAPI(services=[], templates=[])
    sync = SyncManager(api)

    # These should complete without raising exceptions
    await sync.sync_services()
    # If we got here without exceptions, test passes
    assert True


@pytest.mark.asyncio
async def test_sync_services_merge_behavior(setup_db):
    await create_all()

    # First sync
    api1 = FakeAPI(
        services=[
            {
                "id": "svc-1",
                "name": "Service Original",
                "active": True,
                "restricted": False,
            }
        ]
    )
    sync1 = SyncManager(api1)
    await sync1.sync_services()

    # Second sync with updated data
    api2 = FakeAPI(
        services=[
            {
                "id": "svc-1",
                "name": "Service Updated",
                "active": False,
                "restricted": True,
            }
        ]
    )
    sync2 = SyncManager(api2)
    await sync2.sync_services()

    async with get_session() as session:
        services = (await session.execute(select(Service))).scalars().all()
        assert len(services) == 1
        svc = services[0]
        assert svc.name == "Service Updated"
        assert svc.active is False
        assert svc.restricted is True


@pytest.mark.asyncio
async def test_sync_templates_concurrency(setup_db):
    await create_all()

    # Add multiple services
    async with get_session() as session:
        for i in range(5):
            session.add(Service(id=f"svc-{i}", name=f"Service {i}", active=True, restricted=False))
        await session.commit()

    # Create API with a generator that returns unique templates per service
    api = FakeAPI()
    api._template_generator = lambda svc_id: [
        {
            "id": f"tmpl-{svc_id}-{i}",
            "service": svc_id,
            "name": f"Template {i}",
            "type": "email",
            "content": "Content",
            "version": 1,
        }
        for i in range(3)
    ]
    sync = SyncManager(api, max_concurrency=2)

    await sync.sync_templates()

    async with get_session() as session:
        templates = (await session.execute(select(Template))).scalars().all()
        # 5 services * 3 templates each = 15 templates
        assert len(templates) == 15


@pytest.mark.asyncio
async def test_sync_all_with_progress(setup_db):
    await create_all()
    api = FakeAPI()
    sync = SyncManager(api)

    messages = []

    async def track_progress(msg: str):
        messages.append(msg)

    await sync.sync_all(progress=track_progress)

    assert len(messages) > 0


@pytest.mark.asyncio
async def test_sync_users_filters_archived(setup_db):
    await create_all()
    api = FakeAPI(
        users=[
            {
                "id": "u-1",
                "email_address": "_archived_user@example.com",
                "name": "Archived",
            },
            {"id": "u-2", "email_address": "active.user@example.com", "name": "Active"},
        ]
    )
    sync = SyncManager(api)

    await sync.sync_users()

    async with get_session() as session:
        users = (await session.execute(select(User))).scalars().all()
        assert len(users) == 1
        assert users[0].id == "u-2"


@pytest.mark.asyncio
async def test_sync_templates_for_service_direct(setup_db):
    await create_all()

    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service", active=True, restricted=False))
        await session.commit()

    api = FakeAPI(
        templates=[
            {
                "id": "t1",
                "service_id": "svc-1",
                "name": "T1",
                "type": "email",
                "content": "C",
                "subject": "S",
                "version": 1,
            }
        ]
    )
    sync = SyncManager(api)

    messages = []

    async def progress(msg: str):
        messages.append(msg)

    await sync._sync_templates_for_service("svc-1", progress)

    assert any("Templates for svc-1" in msg for msg in messages)

    async with get_session() as session:
        templates = (await session.execute(select(Template))).scalars().all()
        assert len(templates) == 1


@pytest.mark.asyncio
async def test_sync_templates_handles_different_field_names(setup_db):
    await create_all()

    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service", active=True, restricted=False))
        await session.commit()

    # Test template data with different field name variations
    api = FakeAPI(
        templates=[
            {
                "id": "t1",
                "service": "svc-1",
                "name": "T1",
                "type": "email",
                "content": "C",
                "version": 1,
            },
            {
                "id": "t2",
                "service_id": "svc-1",
                "name": "T2",
                "template_type": "sms",
                "content": "C",
                "version": 1,
            },
        ]
    )
    sync = SyncManager(api)

    await sync.sync_templates()

    async with get_session() as session:
        templates = (await session.execute(select(Template))).scalars().all()
        assert len(templates) == 2
        email_tmpl = next(t for t in templates if t.id == "t1")
        sms_tmpl = next(t for t in templates if t.id == "t2")
        assert email_tmpl.template_type == "email"
        assert sms_tmpl.template_type == "sms"


@pytest.mark.asyncio
async def test_sync_services_with_empty_permissions_list(initialized_db):
    """Test that services with empty permissions list are handled correctly."""
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.repository import list_services

    # Create a mock API that returns a service with empty permissions list
    mock_api = MockNotificationAPI()

    # Override get_services to return service with empty list
    async def get_services_with_empty_perms():
        return [
            {
                "id": "test-service-1",
                "name": "Test Service",
                "permissions": [],  # Empty list should be converted to JSON
                "active": True,
                "restricted": False,
                "research_mode": False,
                "count_as_live": True,
            }
        ]

    mock_api.get_services = get_services_with_empty_perms

    manager = SyncManager(mock_api, max_concurrency=5)
    await manager.sync_services()

    # Verify the service was stored correctly
    services = await list_services()
    assert len(services) == 1
    assert services[0].id == "test-service-1"
    assert services[0].permissions == "[]"  # Should be JSON string


@pytest.mark.asyncio
async def test_sync_api_keys_handles_404(initialized_db):
    """Test that sync handles 404 errors gracefully when service has no API keys."""
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.models import Service
    from app.db import get_session
    import httpx

    # Create a service
    async with get_session() as session:
        service = Service(
            id="test-service-no-keys",
            name="Service Without Keys",
            active=True,
        )
        session.add(service)
        await session.commit()

    # Create mock API that raises 404 for get_api_keys
    mock_api = MockNotificationAPI()

    async def raise_404(service_id: str):
        raise httpx.HTTPStatusError("Client error '404 NOT FOUND'", request=None, response=None)

    mock_api.get_api_keys = raise_404

    # This should not raise an exception
    manager = SyncManager(mock_api, max_concurrency=5)
    await manager.sync_api_keys()  # Should complete without error

    # Verify no API keys were added (which is expected)
    from app.repository import list_api_keys

    keys = await list_api_keys()
    assert len(keys) == 0


@pytest.mark.asyncio
async def test_sync_api_keys_handles_404_with_progress(initialized_db):
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.models import Service
    from app.db import get_session

    async with get_session() as session:
        session.add(Service(id="svc-404", name="No Keys Service", active=True))
        await session.commit()

    mock_api = MockNotificationAPI()

    async def raise_404(service_id):
        raise Exception("Client error '404 NOT FOUND'")

    mock_api.get_api_keys = raise_404

    messages = []

    async def progress(msg):
        messages.append(msg)

    manager = SyncManager(mock_api, max_concurrency=5)
    await manager.sync_api_keys(progress=progress)

    assert any("No API keys" in msg for msg in messages)


@pytest.mark.asyncio
async def test_sync_api_keys_records_non_404_error(initialized_db):
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.models import Service
    from app.db import get_session

    async with get_session() as session:
        session.add(Service(id="svc-err", name="Error Service", active=True))
        await session.commit()

    mock_api = MockNotificationAPI()

    async def raise_server_error(service_id):
        raise Exception("Internal Server Error 500")

    mock_api.get_api_keys = raise_server_error

    manager = SyncManager(mock_api, max_concurrency=5)
    result = await manager.sync_api_keys()
    assert result.error_count == 1
    assert len(result.errors) == 1
    assert "Internal Server Error 500" in str(result.errors[0])


@pytest.mark.asyncio
async def test_sync_api_keys_with_service_ids_filter(initialized_db):
    """Test that sync_api_keys respects service_ids filter."""
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.models import Service
    from app.db import get_session

    async with get_session() as session:
        session.add(Service(id="svc-a", name="Service A", active=True))
        session.add(Service(id="svc-b", name="Service B", active=True))
        await session.commit()

    mock_api = MockNotificationAPI()
    synced_services = []

    async def track_get_api_keys(service_id):
        synced_services.append(service_id)
        return []

    mock_api.get_api_keys = track_get_api_keys

    manager = SyncManager(mock_api, max_concurrency=5)
    await manager.sync_api_keys(service_ids=["svc-a"])

    # Only svc-a should have been synced
    assert synced_services == ["svc-a"]


@pytest.mark.asyncio
async def test_sync_sms_senders_handles_404(initialized_db):
    """Test that sync handles 404 errors gracefully for SMS senders."""
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.models import Service
    from app.db import get_session

    async with get_session() as session:
        session.add(Service(id="svc-no-sms", name="No SMS Service", active=True))
        await session.commit()

    mock_api = MockNotificationAPI()

    async def raise_404(service_id):
        raise Exception("Client error '404 NOT FOUND'")

    mock_api.get_sms_senders = raise_404

    messages = []

    async def progress(msg):
        messages.append(msg)

    manager = SyncManager(mock_api, max_concurrency=5)
    result = await manager.sync_sms_senders(progress=progress)

    # 404 should be treated as success (no SMS senders)
    assert result.error_count == 0
    assert result.success_count == 1
    assert any("No SMS senders" in msg for msg in messages)


@pytest.mark.asyncio
async def test_sync_sms_senders_records_non_404_error(initialized_db):
    """Test that sync records non-404 errors for SMS senders."""
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.models import Service
    from app.db import get_session
    import httpx

    async with get_session() as session:
        session.add(Service(id="svc-err-sms", name="Error SMS Service", active=True))
        await session.commit()

    mock_api = MockNotificationAPI()

    mock_response = type(
        "MockResponse",
        (),
        {"status_code": 500, "json": lambda: {"message": "Internal error"}},
    )()

    async def raise_500(service_id):
        raise httpx.HTTPStatusError("Server Error", request=None, response=mock_response)

    mock_api.get_sms_senders = raise_500

    manager = SyncManager(mock_api, max_concurrency=5)
    result = await manager.sync_sms_senders()

    assert result.error_count == 1
    assert result.success_count == 0
    assert len(result.errors) == 1
    assert result.errors[0].status_code == 500
    assert result.errors[0].entity == "sms_senders"
    assert result.errors[0].service_id == "svc-err-sms"


@pytest.mark.asyncio
async def test_sync_result_string_representation(initialized_db):
    """Test SyncError string representation includes status code."""
    from app.sync import SyncError

    error = SyncError(
        entity="templates",
        message="Not authorized",
        status_code=401,
        service_id="svc-123",
    )
    error_str = str(error)
    assert "templates" in error_str
    assert "svc-123" in error_str
    assert "HTTP 401" in error_str
    assert "Not authorized" in error_str


@pytest.mark.asyncio
async def test_sync_extracts_error_message_from_json_response(initialized_db):
    """Test that error message is extracted from JSON response body."""
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    import httpx

    mock_api = MockNotificationAPI()

    class MockResponse:
        status_code = 400

        def json(self):
            return {"message": "Invalid service configuration"}

    async def raise_with_json():
        raise httpx.HTTPStatusError("Bad Request", request=None, response=MockResponse())

    mock_api.get_services = raise_with_json

    manager = SyncManager(mock_api, max_concurrency=5)
    result = await manager.sync_services()

    assert result.error_count == 1
    assert "Invalid service configuration" in str(result.errors[0])


@pytest.mark.asyncio
async def test_sync_api_keys_marks_stale_keys_revoked(initialized_db):
    """Keys in local storage but not returned by the API are marked revoked."""
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.models import Service, ApiKey
    from app.db import get_session

    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service 1", active=True))
        session.add(ApiKey(id="k1", service_id="svc-1", environment="dev", name="Active", revoked=False))
        session.add(ApiKey(id="k2", service_id="svc-1", environment="dev", name="Stale", revoked=False))
        await session.commit()

    mock_api = MockNotificationAPI()

    async def return_only_k1(service_id):
        return [{"id": "k1", "name": "Active", "key_type": "normal", "revoked": False}]

    mock_api.get_api_keys = return_only_k1

    manager = SyncManager(mock_api, max_concurrency=5, environment="dev")
    result = await manager.sync_api_keys(service_ids=["svc-1"])
    assert result.error_count == 0

    async with get_session() as session:
        k1 = (await session.execute(select(ApiKey).where(ApiKey.id == "k1"))).scalar_one()
        assert k1.revoked is False

        k2 = (await session.execute(select(ApiKey).where(ApiKey.id == "k2"))).scalar_one()
        assert k2.revoked is True
        assert k2.expiry_date is not None


@pytest.mark.asyncio
async def test_sync_api_keys_404_does_not_revoke(initialized_db):
    """404 errors should not trigger revocation of local keys."""
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.models import Service, ApiKey
    from app.db import get_session

    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service 1", active=True))
        session.add(ApiKey(id="k1", service_id="svc-1", environment="dev", name="Key1", revoked=False))
        await session.commit()

    mock_api = MockNotificationAPI()

    async def raise_404(service_id):
        raise Exception("Client error '404 NOT FOUND'")

    mock_api.get_api_keys = raise_404

    manager = SyncManager(mock_api, max_concurrency=5, environment="dev")
    await manager.sync_api_keys(service_ids=["svc-1"])

    async with get_session() as session:
        k1 = (await session.execute(select(ApiKey).where(ApiKey.id == "k1"))).scalar_one()
        assert k1.revoked is False
        assert k1.expiry_date is None


@pytest.mark.asyncio
async def test_sync_api_keys_upserts_returned_keys_normally(initialized_db):
    """Keys returned by the API are upserted normally and not marked revoked."""
    from app.sync import SyncManager
    from app.api_client import MockNotificationAPI
    from app.models import Service, ApiKey
    from app.db import get_session

    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service 1", active=True))
        await session.commit()

    mock_api = MockNotificationAPI()

    async def return_keys(service_id):
        return [
            {"id": "k1", "name": "Key1", "key_type": "normal", "revoked": False},
            {"id": "k2", "name": "Key2", "key_type": "team", "revoked": False},
        ]

    mock_api.get_api_keys = return_keys

    manager = SyncManager(mock_api, max_concurrency=5, environment="dev")
    result = await manager.sync_api_keys(service_ids=["svc-1"])
    assert result.error_count == 0

    async with get_session() as session:
        keys = (await session.execute(select(ApiKey))).scalars().all()
        assert len(keys) == 2
        for key in keys:
            assert key.revoked is False
