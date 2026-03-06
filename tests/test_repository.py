import pytest
from sqlalchemy import select
from app.repository import (
    get_setting,
    set_setting,
    get_secure_setting,
    set_secure_setting,
    list_services,
    list_templates,
    list_local_keys,
    add_local_key,
    resolve_local_key,
    list_api_keys,
    list_inbound_numbers,
    list_sms_senders,
    list_provider_details,
    list_communication_items,
    list_users,
    update_api_key_expiry,
    mark_api_key_revoked,
    update_provider_detail,
    update_communication_item,
)
from app.crypto import EncryptionManager
from app.repository import DbSaltProvider
from app.models import (
    Service,
    Template,
    LocalApiKey,
    ApiKey,
    InboundNumber,
    Setting,
    SmsSender,
    ProviderDetail,
    CommunicationItem,
    User,
)
from app.db import get_session


@pytest.mark.asyncio
async def test_get_setting_not_exists(initialized_db):
    result = await get_setting("nonexistent_key")
    assert result is None


@pytest.mark.asyncio
async def test_set_and_get_setting(initialized_db):
    await set_setting("test_key", "test_value")
    result = await get_setting("test_key")
    assert result == "test_value"


@pytest.mark.asyncio
async def test_set_setting_update_existing(initialized_db):
    await set_setting("key1", "value1")
    await set_setting("key1", "value2")

    result = await get_setting("key1")
    assert result == "value2"

    # Verify only one record exists
    async with get_session() as session:
        records = (
            (await session.execute(select(Setting).where(Setting.key == "key1")))
            .scalars()
            .all()
        )
        assert len(records) == 1


@pytest.mark.asyncio
async def test_get_secure_setting_not_exists(initialized_db):
    encryption = EncryptionManager("test-key", salt_provider=DbSaltProvider())
    result = await get_secure_setting("nonexistent", encryption)
    assert result is None


@pytest.mark.asyncio
async def test_set_and_get_secure_setting(initialized_db):
    encryption = EncryptionManager("test-master-key", salt_provider=DbSaltProvider())

    await set_secure_setting("secret_key", "secret_value", encryption)
    result = await get_secure_setting("secret_key", encryption)

    assert result == "secret_value"

    # Verify it's stored encrypted
    raw_value = await get_setting("secret_key")
    assert raw_value != "secret_value"


@pytest.mark.asyncio
async def test_list_services_empty(initialized_db):
    services = await list_services()
    assert services == []


@pytest.mark.asyncio
async def test_list_services(initialized_db):
    async with get_session() as session:
        session.add(
            Service(id="svc-1", name="Service 1", active=True, restricted=False)
        )
        session.add(
            Service(id="svc-2", name="Service 2", active=False, restricted=True)
        )
        await session.commit()

    services = await list_services()
    assert len(services) == 2
    assert {s.id for s in services} == {"svc-1", "svc-2"}


@pytest.mark.asyncio
async def test_list_templates_all(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service", active=True, restricted=False))
        session.add(
            Template(
                id="t1",
                service_id="svc-1",
                name="T1",
                template_type="email",
                content="C1",
                version=1,
            )
        )
        session.add(
            Template(
                id="t2",
                service_id="svc-1",
                name="T2",
                template_type="sms",
                content="C2",
                version=1,
            )
        )
        await session.commit()

    templates = await list_templates()
    assert len(templates) == 2


@pytest.mark.asyncio
async def test_list_templates_by_service(initialized_db):
    async with get_session() as session:
        session.add(
            Service(id="svc-1", name="Service 1", active=True, restricted=False)
        )
        session.add(
            Service(id="svc-2", name="Service 2", active=True, restricted=False)
        )
        session.add(
            Template(
                id="t1",
                service_id="svc-1",
                name="T1",
                template_type="email",
                content="C1",
                version=1,
            )
        )
        session.add(
            Template(
                id="t2",
                service_id="svc-2",
                name="T2",
                template_type="email",
                content="C2",
                version=1,
            )
        )
        await session.commit()

    templates = await list_templates(service_id="svc-1")
    assert len(templates) == 1
    assert templates[0].id == "t1"


@pytest.mark.asyncio
async def test_list_templates_by_type(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service", active=True, restricted=False))
        session.add(
            Template(
                id="t1",
                service_id="svc-1",
                name="T1",
                template_type="email",
                content="C1",
                version=1,
            )
        )
        session.add(
            Template(
                id="t2",
                service_id="svc-1",
                name="T2",
                template_type="sms",
                content="C2",
                version=1,
            )
        )
        await session.commit()

    templates = await list_templates(template_type="email")
    assert len(templates) == 1
    assert templates[0].id == "t1"
    assert templates[0].template_type == "email"


@pytest.mark.asyncio
async def test_list_templates_by_service_and_type(initialized_db):
    async with get_session() as session:
        session.add(
            Service(id="svc-1", name="Service 1", active=True, restricted=False)
        )
        session.add(
            Service(id="svc-2", name="Service 2", active=True, restricted=False)
        )
        session.add(
            Template(
                id="t1",
                service_id="svc-1",
                name="T1",
                template_type="email",
                content="C1",
                version=1,
            )
        )
        session.add(
            Template(
                id="t2",
                service_id="svc-1",
                name="T2",
                template_type="sms",
                content="C2",
                version=1,
            )
        )
        session.add(
            Template(
                id="t3",
                service_id="svc-2",
                name="T3",
                template_type="email",
                content="C3",
                version=1,
            )
        )
        await session.commit()

    templates = await list_templates(service_id="svc-1", template_type="email")
    assert len(templates) == 1
    assert templates[0].id == "t1"


@pytest.mark.asyncio
async def test_list_local_keys_empty(initialized_db):
    keys = await list_local_keys()
    assert keys == []


@pytest.mark.asyncio
async def test_list_local_keys_all(initialized_db):
    async with get_session() as session:
        session.add(
            LocalApiKey(
                service_id="svc-1",
                environment="dev",
                key_name="Key1",
                key_secret="enc1",
                key_type="normal",
            )
        )
        session.add(
            LocalApiKey(
                service_id="svc-2",
                environment="prod",
                key_name="Key2",
                key_secret="enc2",
                key_type="test",
            )
        )
        await session.commit()

    keys = await list_local_keys()
    assert len(keys) == 2


@pytest.mark.asyncio
async def test_list_local_keys_by_service(initialized_db):
    async with get_session() as session:
        session.add(
            LocalApiKey(
                service_id="svc-1",
                environment="dev",
                key_name="Key1",
                key_secret="enc1",
                key_type="normal",
            )
        )
        session.add(
            LocalApiKey(
                service_id="svc-1",
                environment="prod",
                key_name="Key2",
                key_secret="enc2",
                key_type="test",
            )
        )
        await session.commit()

    keys = await list_local_keys(service_id="svc-1", environment="dev")
    assert len(keys) == 1
    assert keys[0].key_name == "Key1"


@pytest.mark.asyncio
async def test_add_local_key(initialized_db):
    encryption = EncryptionManager("test-key", salt_provider=DbSaltProvider())

    await add_local_key(
        encryption=encryption,
        service_id="svc-1",
        environment="dev",
        key_name="Test Key",
        key_secret="my-secret-123",
        key_type="normal",
    )

    keys = await list_local_keys(service_id="svc-1", environment="dev")
    assert len(keys) == 1
    assert keys[0].key_name == "Test Key"
    assert keys[0].key_type == "normal"
    assert keys[0].key_secret != "my-secret-123"  # Should be encrypted


@pytest.mark.asyncio
async def test_resolve_local_key(initialized_db):
    encryption = EncryptionManager("test-key", salt_provider=DbSaltProvider())

    await add_local_key(
        encryption=encryption,
        service_id="svc-1",
        environment="dev",
        key_name="Test Key",
        key_secret="my-secret-456",
        key_type="test",
    )

    keys = await list_local_keys()
    key_id = keys[0].id

    decrypted = await resolve_local_key(encryption, key_id)
    assert decrypted == "my-secret-456"


@pytest.mark.asyncio
async def test_list_api_keys_empty(initialized_db):
    keys = await list_api_keys()
    assert keys == []


@pytest.mark.asyncio
async def test_list_api_keys(initialized_db):
    async with get_session() as session:
        session.add(
            ApiKey(
                id="key-1", name="Key 1", expiry_date="2025-12-31", created_by="user-1"
            )
        )
        session.add(
            ApiKey(id="key-2", name="Key 2", expiry_date=None, created_by="user-2")
        )
        await session.commit()

    keys = await list_api_keys()
    assert len(keys) == 2
    assert {k.id for k in keys} == {"key-1", "key-2"}


@pytest.mark.asyncio
async def test_update_api_key_expiry(initialized_db):
    async with get_session() as session:
        session.add(
            ApiKey(
                id="key-1",
                service_id="svc-1",
                environment="dev",
                name="Key 1",
                expiry_date="2025-12-31",
            )
        )
        await session.commit()

    updated = await update_api_key_expiry(
        service_id="svc-1",
        key_id="key-1",
        expiry_date="2026-04-08",
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (
            await session.execute(select(ApiKey).where(ApiKey.id == "key-1"))
        ).scalar_one()
        assert record.expiry_date == "2026-04-08"


@pytest.mark.asyncio
async def test_mark_api_key_revoked(initialized_db):
    async with get_session() as session:
        session.add(
            ApiKey(
                id="key-1",
                service_id="svc-1",
                environment="dev",
                name="Key 1",
                revoked=False,
            )
        )
        await session.commit()

    updated = await mark_api_key_revoked(
        service_id="svc-1", key_id="key-1", environment="dev"
    )
    assert updated is True

    async with get_session() as session:
        record = (
            await session.execute(select(ApiKey).where(ApiKey.id == "key-1"))
        ).scalar_one()
        assert record.revoked is True


@pytest.mark.asyncio
async def test_multiple_operations(initialized_db):
    encryption = EncryptionManager("master-key", salt_provider=DbSaltProvider())

    # Add services
    async with get_session() as session:
        session.add(
            Service(id="svc-1", name="Service 1", active=True, restricted=False)
        )
        await session.commit()

    # Add local key
    await add_local_key(encryption, "svc-1", "dev", "Key1", "secret1", "normal")

    # Add template
    async with get_session() as session:
        session.add(
            Template(
                id="tmpl-1",
                service_id="svc-1",
                name="Template 1",
                template_type="email",
                content="Hello",
                version=1,
            )
        )
        await session.commit()

    # Verify all
    services = await list_services()
    templates = await list_templates(service_id="svc-1")
    keys = await list_local_keys(service_id="svc-1", environment="dev")

    assert len(services) == 1
    assert len(templates) == 1
    assert len(keys) == 1


# --- Additional coverage tests ---


@pytest.mark.asyncio
async def test_list_services_with_environment(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Svc1", active=True, environment="dev"))
        session.add(Service(id="svc-2", name="Svc2", active=True, environment="prod"))
        await session.commit()

    services = await list_services(environment="dev")
    assert len(services) == 1
    assert services[0].id == "svc-1"


@pytest.mark.asyncio
async def test_list_services_with_multiple_environments(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Svc1", active=True, environment="dev"))
        session.add(
            Service(id="svc-2", name="Svc2", active=True, environment="staging")
        )
        session.add(Service(id="svc-3", name="Svc3", active=True, environment="prod"))
        await session.commit()

    # Test filtering with multiple environments
    services = await list_services(environment=["dev", "staging"])
    assert len(services) == 2
    ids = {s.id for s in services}
    assert ids == {"svc-1", "svc-2"}

    # Test with empty list (should return all)
    services = await list_services(environment=[])
    assert len(services) == 3

    # Test with None (should return all)
    services = await list_services(environment=None)
    assert len(services) == 3


@pytest.mark.asyncio
async def test_list_templates_with_environment(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Svc", active=True, environment="dev"))
        session.add(
            Template(
                id="t1",
                service_id="svc-1",
                name="T1",
                template_type="email",
                content="C",
                version=1,
                environment="dev",
            )
        )
        session.add(
            Template(
                id="t2",
                service_id="svc-1",
                name="T2",
                template_type="email",
                content="C",
                version=1,
                environment="prod",
            )
        )
        await session.commit()

    templates = await list_templates(environment="dev")
    assert len(templates) == 1
    assert templates[0].id == "t1"


@pytest.mark.asyncio
async def test_list_api_keys_with_environment(initialized_db):
    async with get_session() as session:
        session.add(ApiKey(id="k1", name="K1", environment="dev", service_id="svc-1"))
        session.add(ApiKey(id="k2", name="K2", environment="prod", service_id="svc-1"))
        await session.commit()

    keys = await list_api_keys(environment="dev")
    assert len(keys) == 1
    assert keys[0].id == "k1"


@pytest.mark.asyncio
async def test_update_api_key_expiry_not_found(initialized_db):
    result = await update_api_key_expiry(
        service_id="nonexistent", key_id="nonexistent", expiry_date="2026-01-01"
    )
    assert result is False


@pytest.mark.asyncio
async def test_mark_api_key_revoked_not_found(initialized_db):
    result = await mark_api_key_revoked(service_id="nonexistent", key_id="nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_list_sms_senders_empty(initialized_db):
    senders = await list_sms_senders()
    assert senders == []


@pytest.mark.asyncio
async def test_list_sms_senders_with_data(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Svc", active=True, environment="dev"))
        session.add(
            SmsSender(
                id="s1",
                environment="dev",
                service_id="svc-1",
                sms_sender="+1555",
                is_default=True,
                archived=False,
            )
        )
        session.add(
            SmsSender(
                id="s2",
                environment="prod",
                service_id="svc-1",
                sms_sender="+1666",
                is_default=False,
                archived=False,
            )
        )
        await session.commit()

    senders = await list_sms_senders(service_id="svc-1", environment="dev")
    assert len(senders) == 1
    assert senders[0].id == "s1"


@pytest.mark.asyncio
async def test_list_provider_details_empty(initialized_db):
    providers = await list_provider_details()
    assert providers == []


@pytest.mark.asyncio
async def test_list_provider_details_with_environment(initialized_db):
    async with get_session() as session:
        session.add(
            ProviderDetail(
                id="p1",
                environment="dev",
                active=True,
                display_name="P1",
                identifier="p1",
                notification_type="email",
            )
        )
        session.add(
            ProviderDetail(
                id="p2",
                environment="prod",
                active=True,
                display_name="P2",
                identifier="p2",
                notification_type="sms",
            )
        )
        await session.commit()

    providers = await list_provider_details(environment="dev")
    assert len(providers) == 1
    assert providers[0].id == "p1"


@pytest.mark.asyncio
async def test_list_communication_items_empty(initialized_db):
    items = await list_communication_items()
    assert items == []


@pytest.mark.asyncio
async def test_list_communication_items_with_environment(initialized_db):
    async with get_session() as session:
        session.add(
            CommunicationItem(
                id="c1",
                environment="dev",
                name="Item1",
                va_profile_item_id=1,
                default_send_indicator=True,
            )
        )
        session.add(
            CommunicationItem(
                id="c2",
                environment="prod",
                name="Item2",
                va_profile_item_id=2,
                default_send_indicator=False,
            )
        )
        await session.commit()

    items = await list_communication_items(environment="dev")
    assert len(items) == 1
    assert items[0].id == "c1"


@pytest.mark.asyncio
async def test_list_users_empty(initialized_db):
    users = await list_users()
    assert users == []


@pytest.mark.asyncio
async def test_list_users_with_environment(initialized_db):
    async with get_session() as session:
        session.add(
            User(
                id="u1", environment="dev", email_address="user1@test.com", name="User1"
            )
        )
        session.add(
            User(
                id="u2",
                environment="prod",
                email_address="user2@test.com",
                name="User2",
            )
        )
        session.add(
            User(
                id="u3",
                environment="dev",
                email_address="_archived@test.com",
                name="Archived",
            )
        )
        await session.commit()

    users = await list_users(environment="dev")
    assert len(users) == 1
    assert users[0].id == "u1"


@pytest.mark.asyncio
async def test_list_api_keys_by_service_id(initialized_db):
    async with get_session() as session:
        session.add(ApiKey(id="key-1", service_id="svc-1", name="Key 1"))
        session.add(ApiKey(id="key-2", service_id="svc-2", name="Key 2"))
        await session.commit()

    keys = await list_api_keys(service_id="svc-1")
    assert len(keys) == 1
    assert keys[0].id == "key-1"


@pytest.mark.asyncio
async def test_list_inbound_numbers_by_service_id(initialized_db):
    async with get_session() as session:
        session.add(
            InboundNumber(
                id="n1", environment="dev", number="+1111", service_id="svc-1"
            )
        )
        session.add(
            InboundNumber(
                id="n2", environment="dev", number="+2222", service_id="svc-2"
            )
        )
        await session.commit()

    numbers = await list_inbound_numbers(service_id="svc-1")
    assert len(numbers) == 1
    assert numbers[0].id == "n1"


@pytest.mark.asyncio
async def test_list_inbound_numbers_by_environment(initialized_db):
    async with get_session() as session:
        session.add(InboundNumber(id="n1", environment="dev", number="+1111"))
        session.add(InboundNumber(id="n2", environment="staging", number="+2222"))
        await session.commit()

    numbers = await list_inbound_numbers(environment="dev")
    assert len(numbers) == 1
    assert numbers[0].id == "n1"


@pytest.mark.asyncio
async def test_update_provider_detail(initialized_db):
    async with get_session() as session:
        session.add(
            ProviderDetail(
                id="prov-1",
                environment="dev",
                active=False,
                priority=5,
                load_balancing_weight=10,
            )
        )
        await session.commit()

    updated = await update_provider_detail(
        provider_id="prov-1",
        priority=10,
        active=True,
        load_balancing_weight=50,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (
            await session.execute(
                select(ProviderDetail).where(ProviderDetail.id == "prov-1")
            )
        ).scalar_one()
        assert record.priority == 10
        assert record.active is True
        assert record.load_balancing_weight == 50


@pytest.mark.asyncio
async def test_update_provider_detail_not_found(initialized_db):
    result = await update_provider_detail(
        provider_id="nonexistent",
        priority=10,
        environment="dev",
    )
    assert result is False


@pytest.mark.asyncio
async def test_update_provider_detail_partial_update(initialized_db):
    async with get_session() as session:
        session.add(
            ProviderDetail(
                id="prov-2",
                environment="dev",
                active=True,
                priority=5,
                load_balancing_weight=100,
            )
        )
        await session.commit()

    # Only update priority
    updated = await update_provider_detail(
        provider_id="prov-2",
        priority=15,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (
            await session.execute(
                select(ProviderDetail).where(ProviderDetail.id == "prov-2")
            )
        ).scalar_one()
        assert record.priority == 15
        assert record.active is True  # Unchanged
        assert record.load_balancing_weight == 100  # Unchanged


@pytest.mark.asyncio
async def test_update_communication_item(initialized_db):
    async with get_session() as session:
        session.add(
            CommunicationItem(
                id="comm-1",
                environment="dev",
                name="Test Item",
                va_profile_item_id=5,
                default_send_indicator=False,
            )
        )
        await session.commit()

    updated = await update_communication_item(
        item_id="comm-1",
        name="Updated Item",
        default_send_indicator=True,
        va_profile_item_id=10,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (
            await session.execute(
                select(CommunicationItem).where(CommunicationItem.id == "comm-1")
            )
        ).scalar_one()
        assert record.name == "Updated Item"
        assert record.default_send_indicator is True
        assert record.va_profile_item_id == 10


@pytest.mark.asyncio
async def test_update_communication_item_not_found(initialized_db):
    result = await update_communication_item(
        item_id="nonexistent",
        name="Test",
        environment="dev",
    )
    assert result is False


@pytest.mark.asyncio
async def test_update_communication_item_partial_update(initialized_db):
    async with get_session() as session:
        session.add(
            CommunicationItem(
                id="comm-2",
                environment="dev",
                name="Original Name",
                va_profile_item_id=20,
                default_send_indicator=True,
            )
        )
        await session.commit()

    # Only update name
    updated = await update_communication_item(
        item_id="comm-2",
        name="New Name",
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (
            await session.execute(
                select(CommunicationItem).where(CommunicationItem.id == "comm-2")
            )
        ).scalar_one()
        assert record.name == "New Name"
        assert record.va_profile_item_id == 20  # Unchanged
        assert record.default_send_indicator is True  # Unchanged
