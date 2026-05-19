from datetime import datetime

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
    mark_stale_api_keys_revoked,
    update_provider_detail,
    update_service,
    update_sms_sender,
    update_inbound_number,
    update_communication_item,
    clear_table_data,
    count_active_api_keys_by_service,
    _is_expired,
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
        records = (await session.execute(select(Setting).where(Setting.key == "key1"))).scalars().all()
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
        session.add(Service(id="svc-1", name="Service 1", active=True, restricted=False))
        session.add(Service(id="svc-2", name="Service 2", active=False, restricted=True))
        await session.commit()

    services = await list_services()
    assert len(services) == 2
    assert {s.id for s in services} == {"svc-1", "svc-2"}


@pytest.mark.asyncio
async def test_list_service_environments_empty(initialized_db):
    from app.repository import list_service_environments

    envs = await list_service_environments("nonexistent-service")
    assert envs == []


@pytest.mark.asyncio
async def test_list_service_environments(initialized_db):
    from app.repository import list_service_environments

    async with get_session() as session:
        session.add(
            Service(
                id="svc-1",
                name="Service 1",
                environment="dev",
                active=True,
                restricted=False,
            )
        )
        session.add(
            Service(
                id="svc-1",
                name="Service 1",
                environment="staging",
                active=True,
                restricted=False,
            )
        )
        session.add(
            Service(
                id="svc-1",
                name="Service 1",
                environment="prod",
                active=True,
                restricted=False,
            )
        )
        session.add(
            Service(
                id="svc-2",
                name="Service 2",
                environment="dev",
                active=True,
                restricted=False,
            )
        )
        await session.commit()

    envs = await list_service_environments("svc-1")
    assert set(envs) == {"dev", "staging", "prod"}

    envs2 = await list_service_environments("svc-2")
    assert envs2 == ["dev"]


@pytest.mark.asyncio
async def test_get_service_by_name(initialized_db):
    from app.repository import get_service_by_name

    async with get_session() as session:
        session.add(
            Service(
                id="svc-dev-1",
                name="My Service",
                environment="dev",
                active=True,
                restricted=False,
            )
        )
        session.add(
            Service(
                id="svc-staging-2",
                name="My Service",
                environment="staging",
                active=True,
                restricted=False,
            )
        )
        await session.commit()

    svc_dev = await get_service_by_name("My Service", "dev")
    assert svc_dev is not None
    assert svc_dev.id == "svc-dev-1"
    assert svc_dev.environment == "dev"

    svc_staging = await get_service_by_name("My Service", "staging")
    assert svc_staging is not None
    assert svc_staging.id == "svc-staging-2"

    svc_prod = await get_service_by_name("My Service", "prod")
    assert svc_prod is None

    svc_missing = await get_service_by_name("Nonexistent", "dev")
    assert svc_missing is None


@pytest.mark.asyncio
async def test_list_environments_for_service_name(initialized_db):
    from app.repository import list_environments_for_service_name

    async with get_session() as session:
        session.add(
            Service(
                id="svc-dev-1",
                name="Shared Service",
                environment="dev",
                active=True,
                restricted=False,
            )
        )
        session.add(
            Service(
                id="svc-staging-2",
                name="Shared Service",
                environment="staging",
                active=True,
                restricted=False,
            )
        )
        session.add(
            Service(
                id="svc-prod-3",
                name="Shared Service",
                environment="prod",
                active=True,
                restricted=False,
            )
        )
        session.add(
            Service(
                id="svc-other",
                name="Other Service",
                environment="dev",
                active=True,
                restricted=False,
            )
        )
        await session.commit()

    envs = await list_environments_for_service_name("Shared Service")
    assert set(envs) == {"dev", "staging", "prod"}

    envs2 = await list_environments_for_service_name("Other Service")
    assert envs2 == ["dev"]

    envs3 = await list_environments_for_service_name("Nonexistent")
    assert envs3 == []


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
        session.add(Service(id="svc-1", name="Service 1", active=True, restricted=False))
        session.add(Service(id="svc-2", name="Service 2", active=True, restricted=False))
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
        session.add(Service(id="svc-1", name="Service 1", active=True, restricted=False))
        session.add(Service(id="svc-2", name="Service 2", active=True, restricted=False))
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
        session.add(ApiKey(id="key-1", name="Key 1", expiry_date="2025-12-31", created_by="user-1"))
        session.add(ApiKey(id="key-2", name="Key 2", expiry_date=None, created_by="user-2"))
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
        record = (await session.execute(select(ApiKey).where(ApiKey.id == "key-1"))).scalar_one()
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

    updated = await mark_api_key_revoked(service_id="svc-1", key_id="key-1", environment="dev")
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(ApiKey).where(ApiKey.id == "key-1"))).scalar_one()
        assert record.revoked is True
        assert record.expiry_date is not None
        expiry = datetime.fromisoformat(record.expiry_date)
        assert expiry.tzinfo is not None


@pytest.mark.asyncio
async def test_multiple_operations(initialized_db):
    encryption = EncryptionManager("master-key", salt_provider=DbSaltProvider())

    # Add services
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service 1", active=True, restricted=False))
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
        session.add(Service(id="svc-2", name="Svc2", active=True, environment="staging"))
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
    result = await update_api_key_expiry(service_id="nonexistent", key_id="nonexistent", expiry_date="2026-01-01")
    assert result is False


@pytest.mark.asyncio
async def test_mark_api_key_revoked_not_found(initialized_db):
    result = await mark_api_key_revoked(service_id="nonexistent", key_id="nonexistent")
    assert result is False


def test_is_expired_none():
    assert _is_expired(None) is False


def test_is_expired_empty_string():
    assert _is_expired("") is False


def test_is_expired_past_date():
    assert _is_expired("2020-01-01T00:00:00+00:00") is True


def test_is_expired_future_date():
    assert _is_expired("2099-12-31T23:59:59+00:00") is False


def test_is_expired_invalid_string():
    assert _is_expired("not-a-date") is False


def test_is_expired_naive_past_date():
    """Offset-naive timestamps are treated as UTC."""
    assert _is_expired("2020-01-01T00:00:00") is True


def test_is_expired_naive_future_date():
    assert _is_expired("2099-12-31T23:59:59") is False


@pytest.mark.asyncio
async def test_mark_api_key_revoked_preserves_past_expiry(initialized_db):
    """A key that is already expired keeps its original expiry_date."""
    past_expiry = "2020-06-01T00:00:00+00:00"
    async with get_session() as session:
        session.add(
            ApiKey(
                id="key-exp",
                service_id="svc-1",
                environment="dev",
                name="Expired Key",
                revoked=False,
                expiry_date=past_expiry,
            )
        )
        await session.commit()

    updated = await mark_api_key_revoked(service_id="svc-1", key_id="key-exp", environment="dev")
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(ApiKey).where(ApiKey.id == "key-exp"))).scalar_one()
        assert record.revoked is True
        assert record.expiry_date == past_expiry


@pytest.mark.asyncio
async def test_mark_api_key_revoked_updates_future_expiry(initialized_db):
    """A key with a future expiry gets its expiry_date overwritten to now."""
    future_expiry = "2099-12-31T23:59:59+00:00"
    async with get_session() as session:
        session.add(
            ApiKey(
                id="key-fut",
                service_id="svc-1",
                environment="dev",
                name="Future Key",
                revoked=False,
                expiry_date=future_expiry,
            )
        )
        await session.commit()

    updated = await mark_api_key_revoked(service_id="svc-1", key_id="key-fut", environment="dev")
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(ApiKey).where(ApiKey.id == "key-fut"))).scalar_one()
        assert record.revoked is True
        assert record.expiry_date != future_expiry
        expiry = datetime.fromisoformat(record.expiry_date)
        assert expiry.tzinfo is not None


@pytest.mark.asyncio
async def test_mark_api_key_revoked_sets_expiry_when_none(initialized_db):
    """A key with no expiry_date gets one set on revocation."""
    async with get_session() as session:
        session.add(
            ApiKey(
                id="key-none",
                service_id="svc-1",
                environment="dev",
                name="No Expiry Key",
                revoked=False,
                expiry_date=None,
            )
        )
        await session.commit()

    updated = await mark_api_key_revoked(service_id="svc-1", key_id="key-none", environment="dev")
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(ApiKey).where(ApiKey.id == "key-none"))).scalar_one()
        assert record.revoked is True
        assert record.expiry_date is not None
        expiry = datetime.fromisoformat(record.expiry_date)
        assert expiry.tzinfo is not None


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
        session.add(User(id="u1", environment="dev", email_address="user1@test.com", name="User1"))
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
        session.add(InboundNumber(id="n1", environment="dev", number="+1111", service_id="svc-1"))
        session.add(InboundNumber(id="n2", environment="dev", number="+2222", service_id="svc-2"))
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
async def test_update_inbound_number(initialized_db):
    async with get_session() as session:
        session.add(
            InboundNumber(
                id="n1",
                environment="dev",
                number="+15551234567",
                provider="pinpoint",
                active=True,
                self_managed=False,
            )
        )
        await session.commit()

    updated = await update_inbound_number(
        inbound_number_id="n1",
        number="+15559876543",
        active=False,
        url_endpoint="https://example.com/callback",
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(InboundNumber).where(InboundNumber.id == "n1"))).scalar_one()
        assert record.number == "+15559876543"
        assert record.active is False
        assert record.url_endpoint == "https://example.com/callback"


@pytest.mark.asyncio
async def test_update_inbound_number_not_found(initialized_db):
    result = await update_inbound_number(
        inbound_number_id="nonexistent",
        number="+15551111111",
        environment="dev",
    )
    assert result is False


@pytest.mark.asyncio
async def test_update_inbound_number_partial_update(initialized_db):
    async with get_session() as session:
        session.add(
            InboundNumber(
                id="n2",
                environment="dev",
                number="+15551234567",
                provider="pinpoint",
                active=True,
                self_managed=False,
            )
        )
        await session.commit()

    # Only update active
    updated = await update_inbound_number(
        inbound_number_id="n2",
        active=False,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(InboundNumber).where(InboundNumber.id == "n2"))).scalar_one()
        assert record.active is False
        assert record.number == "+15551234567"  # Unchanged
        assert record.provider == "pinpoint"  # Unchanged


@pytest.mark.asyncio
async def test_update_service(initialized_db):
    async with get_session() as session:
        session.add(
            Service(
                id="svc-1",
                environment="dev",
                name="Test Service",
                active=True,
                restricted=False,
                message_limit=1000,
                rate_limit=50,
                research_mode=False,
                count_as_live=True,
                prefix_sms=False,
            )
        )
        await session.commit()

    updated = await update_service(
        service_id="svc-1",
        message_limit=5000,
        rate_limit=100,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(Service).where(Service.id == "svc-1"))).scalar_one()
        assert record.message_limit == 5000
        assert record.rate_limit == 100
        assert record.name == "Test Service"  # Unchanged
        assert record.active is True  # Unchanged


@pytest.mark.asyncio
async def test_update_service_not_found(initialized_db):
    result = await update_service(
        service_id="nonexistent",
        message_limit=5000,
        environment="dev",
    )
    assert result is False


@pytest.mark.asyncio
async def test_update_service_partial_message_limit(initialized_db):
    async with get_session() as session:
        session.add(
            Service(
                id="svc-2",
                environment="dev",
                name="Partial Test",
                active=True,
                restricted=False,
                message_limit=1000,
                rate_limit=50,
                research_mode=False,
                count_as_live=True,
                prefix_sms=False,
            )
        )
        await session.commit()

    updated = await update_service(
        service_id="svc-2",
        message_limit=3000,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(Service).where(Service.id == "svc-2"))).scalar_one()
        assert record.message_limit == 3000
        assert record.rate_limit == 50  # Unchanged


@pytest.mark.asyncio
async def test_update_service_partial_rate_limit(initialized_db):
    async with get_session() as session:
        session.add(
            Service(
                id="svc-3",
                environment="dev",
                name="Rate Test",
                active=True,
                restricted=False,
                message_limit=1000,
                rate_limit=50,
                research_mode=False,
                count_as_live=True,
                prefix_sms=False,
            )
        )
        await session.commit()

    updated = await update_service(
        service_id="svc-3",
        rate_limit=200,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(Service).where(Service.id == "svc-3"))).scalar_one()
        assert record.message_limit == 1000  # Unchanged
        assert record.rate_limit == 200


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
        record = (await session.execute(select(ProviderDetail).where(ProviderDetail.id == "prov-1"))).scalar_one()
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
        record = (await session.execute(select(ProviderDetail).where(ProviderDetail.id == "prov-2"))).scalar_one()
        assert record.priority == 15
        assert record.active is True  # Unchanged
        assert record.load_balancing_weight == 100  # Unchanged


@pytest.mark.asyncio
async def test_update_sms_sender(initialized_db):
    async with get_session() as session:
        session.add(
            SmsSender(
                id="sms-1",
                environment="dev",
                service_id="svc-1",
                sms_sender="+15551234567",
                is_default=False,
                description="Original",
                rate_limit=100,
            )
        )
        await session.commit()

    updated = await update_sms_sender(
        sms_sender_id="sms-1",
        sms_sender="+15559876543",
        description="Updated",
        is_default=True,
        rate_limit=200,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(SmsSender).where(SmsSender.id == "sms-1"))).scalar_one()
        assert record.sms_sender == "+15559876543"
        assert record.description == "Updated"
        assert record.is_default is True
        assert record.rate_limit == 200


@pytest.mark.asyncio
async def test_update_sms_sender_not_found(initialized_db):
    result = await update_sms_sender(
        sms_sender_id="nonexistent",
        sms_sender="+15551111111",
        environment="dev",
    )
    assert result is False


@pytest.mark.asyncio
async def test_update_sms_sender_partial_update(initialized_db):
    async with get_session() as session:
        session.add(
            SmsSender(
                id="sms-2",
                environment="dev",
                service_id="svc-1",
                sms_sender="+15551234567",
                is_default=False,
                description="Original description",
                rate_limit=100,
            )
        )
        await session.commit()

    # Only update is_default
    updated = await update_sms_sender(
        sms_sender_id="sms-2",
        is_default=True,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(SmsSender).where(SmsSender.id == "sms-2"))).scalar_one()
        assert record.is_default is True
        assert record.sms_sender == "+15551234567"  # Unchanged
        assert record.description == "Original description"  # Unchanged
        assert record.rate_limit == 100  # Unchanged


@pytest.mark.asyncio
async def test_update_sms_sender_with_specifics(initialized_db):
    async with get_session() as session:
        session.add(
            SmsSender(
                id="sms-3",
                environment="dev",
                service_id="svc-1",
                sms_sender="+15551234567",
                is_default=False,
                description="Original",
            )
        )
        await session.commit()

    sender_specifics = {"messaging_service_sid": "MG0000000000000000000000"}
    updated = await update_sms_sender(
        sms_sender_id="sms-3",
        sms_sender_specifics=sender_specifics,
        environment="dev",
    )
    assert updated is True

    async with get_session() as session:
        record = (await session.execute(select(SmsSender).where(SmsSender.id == "sms-3"))).scalar_one()
        assert record.sms_sender_specifics == sender_specifics


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
        record = (await session.execute(select(CommunicationItem).where(CommunicationItem.id == "comm-1"))).scalar_one()
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
        record = (await session.execute(select(CommunicationItem).where(CommunicationItem.id == "comm-2"))).scalar_one()
        assert record.name == "New Name"
        assert record.va_profile_item_id == 20  # Unchanged
        assert record.default_send_indicator is True  # Unchanged


@pytest.mark.asyncio
async def test_clear_table_data_all_environments(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service 1", environment="dev"))
        session.add(Service(id="svc-2", name="Service 2", environment="staging"))
        session.add(Service(id="svc-3", name="Service 3", environment="dev"))
        await session.commit()

    # Clear all services
    deleted = await clear_table_data("services")
    assert deleted == 3

    services = await list_services()
    assert len(services) == 0


@pytest.mark.asyncio
async def test_clear_table_data_by_environment(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service 1", environment="dev"))
        session.add(Service(id="svc-2", name="Service 2", environment="staging"))
        session.add(Service(id="svc-3", name="Service 3", environment="dev"))
        await session.commit()

    # Clear only dev services
    deleted = await clear_table_data("services", "dev")
    assert deleted == 2

    services = await list_services()
    assert len(services) == 1
    assert services[0].environment == "staging"


@pytest.mark.asyncio
async def test_clear_table_data_unknown_table(initialized_db):
    with pytest.raises(ValueError) as exc_info:
        await clear_table_data("nonexistent_table")
    assert "Unknown table" in str(exc_info.value)


@pytest.mark.asyncio
async def test_clear_table_data_users(initialized_db):
    async with get_session() as session:
        session.add(User(id="u1", environment="dev", name="User 1"))
        session.add(User(id="u2", environment="prod", name="User 2"))
        await session.commit()

    deleted = await clear_table_data("users", "dev")
    assert deleted == 1

    users = await list_users()
    assert len(users) == 1
    assert users[0].id == "u2"


@pytest.mark.asyncio
async def test_clear_table_data_local_api_keys(initialized_db):
    enc = EncryptionManager("test-key", salt_provider=DbSaltProvider())

    await add_local_key(enc, "svc-1", "dev", "Key1", "secret1", "normal")
    await add_local_key(enc, "svc-1", "staging", "Key2", "secret2", "normal")

    deleted = await clear_table_data("local_api_keys", "dev")
    assert deleted == 1

    keys = await list_local_keys()
    assert len(keys) == 1
    assert keys[0].environment == "staging"


# Multi-select service filtering tests


@pytest.mark.asyncio
async def test_list_templates_by_multiple_services(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", environment="dev", name="Service 1"))
        session.add(Service(id="svc-2", environment="dev", name="Service 2"))
        session.add(Service(id="svc-3", environment="dev", name="Service 3"))
        session.add(
            Template(
                id="t1",
                environment="dev",
                name="T1",
                service_id="svc-1",
                template_type="email",
                content="Hello",
            )
        )
        session.add(
            Template(
                id="t2",
                environment="dev",
                name="T2",
                service_id="svc-2",
                template_type="sms",
                content="Hi",
            )
        )
        session.add(
            Template(
                id="t3",
                environment="dev",
                name="T3",
                service_id="svc-3",
                template_type="email",
                content="Hey",
            )
        )
        await session.commit()

    templates = await list_templates(service_id=["svc-1", "svc-2"])
    assert len(templates) == 2
    assert {t.id for t in templates} == {"t1", "t2"}


@pytest.mark.asyncio
async def test_list_api_keys_by_multiple_services(initialized_db):
    async with get_session() as session:
        session.add(ApiKey(id="key-1", service_id="svc-1", name="Key 1"))
        session.add(ApiKey(id="key-2", service_id="svc-2", name="Key 2"))
        session.add(ApiKey(id="key-3", service_id="svc-3", name="Key 3"))
        await session.commit()

    keys = await list_api_keys(service_id=["svc-1", "svc-3"])
    assert len(keys) == 2
    assert {k.id for k in keys} == {"key-1", "key-3"}


@pytest.mark.asyncio
async def test_list_sms_senders_by_multiple_services(initialized_db):
    async with get_session() as session:
        session.add(SmsSender(id="s1", environment="dev", service_id="svc-1", sms_sender="+111"))
        session.add(SmsSender(id="s2", environment="dev", service_id="svc-2", sms_sender="+222"))
        session.add(SmsSender(id="s3", environment="dev", service_id="svc-3", sms_sender="+333"))
        await session.commit()

    senders = await list_sms_senders(service_id=["svc-2", "svc-3"])
    assert len(senders) == 2
    assert {s.id for s in senders} == {"s2", "s3"}


@pytest.mark.asyncio
async def test_list_inbound_numbers_by_multiple_services(initialized_db):
    async with get_session() as session:
        session.add(InboundNumber(id="n1", environment="dev", number="+1111", service_id="svc-1"))
        session.add(InboundNumber(id="n2", environment="dev", number="+2222", service_id="svc-2"))
        session.add(InboundNumber(id="n3", environment="dev", number="+3333", service_id="svc-3"))
        await session.commit()

    numbers = await list_inbound_numbers(service_id=["svc-1", "svc-2"])
    assert len(numbers) == 2
    assert {n.id for n in numbers} == {"n1", "n2"}


@pytest.mark.asyncio
async def test_list_templates_empty_list_returns_all(initialized_db):
    """Empty list should return all templates (no filter)."""
    async with get_session() as session:
        session.add(
            Template(
                id="t1",
                environment="dev",
                name="T1",
                service_id="svc-1",
                template_type="email",
                content="Hello",
            )
        )
        session.add(
            Template(
                id="t2",
                environment="dev",
                name="T2",
                service_id="svc-2",
                template_type="sms",
                content="Hi",
            )
        )
        await session.commit()

    # Empty list should behave like None
    templates = await list_templates(service_id=[])
    assert len(templates) == 2


@pytest.mark.asyncio
async def test_list_api_keys_single_service_as_list(initialized_db):
    """Single service ID as list should work the same as string."""
    async with get_session() as session:
        session.add(ApiKey(id="key-1", service_id="svc-1", name="Key 1"))
        session.add(ApiKey(id="key-2", service_id="svc-2", name="Key 2"))
        await session.commit()

    keys = await list_api_keys(service_id=["svc-1"])
    assert len(keys) == 1
    assert keys[0].id == "key-1"


@pytest.mark.asyncio
async def test_count_active_api_keys_empty(initialized_db):
    """No API keys should return empty dict."""
    counts = await count_active_api_keys_by_service()
    assert counts == {}


@pytest.mark.asyncio
async def test_count_active_api_keys_mixed(initialized_db):
    """Test correct counts with active, revoked, and expired keys."""
    async with get_session() as session:
        session.add(Service(id="svc-1", environment="dev", name="Service 1"))
        session.add(Service(id="svc-2", environment="dev", name="Service 2"))
        # svc-1: 2 active keys (1 no expiry, 1 future expiry)
        session.add(
            ApiKey(
                id="k1", service_id="svc-1", environment="dev", name="Active no expiry", revoked=False, expiry_date=None
            )
        )
        session.add(
            ApiKey(
                id="k2",
                service_id="svc-1",
                environment="dev",
                name="Active future",
                revoked=False,
                expiry_date="2099-12-31T00:00:00+00:00",
            )
        )
        # svc-1: 1 revoked key (should not count)
        session.add(
            ApiKey(id="k3", service_id="svc-1", environment="dev", name="Revoked", revoked=True, expiry_date=None)
        )
        # svc-1: 1 expired key (should not count)
        session.add(
            ApiKey(
                id="k4",
                service_id="svc-1",
                environment="dev",
                name="Expired",
                revoked=False,
                expiry_date="2020-01-01T00:00:00+00:00",
            )
        )
        # svc-2: 1 active key
        session.add(
            ApiKey(id="k5", service_id="svc-2", environment="dev", name="Active", revoked=False, expiry_date=None)
        )
        await session.commit()

    counts = await count_active_api_keys_by_service()
    assert counts[("svc-1", "dev")] == 2
    assert counts[("svc-2", "dev")] == 1


@pytest.mark.asyncio
async def test_count_active_api_keys_environment_filter(initialized_db):
    """Test filtering by environment."""
    async with get_session() as session:
        session.add(
            ApiKey(id="k1", service_id="svc-1", environment="dev", name="Dev key", revoked=False, expiry_date=None)
        )
        session.add(
            ApiKey(
                id="k2", service_id="svc-1", environment="staging", name="Staging key", revoked=False, expiry_date=None
            )
        )
        await session.commit()

    counts = await count_active_api_keys_by_service(environment="dev")
    assert counts == {("svc-1", "dev"): 1}


@pytest.mark.asyncio
async def test_count_active_api_keys_null_expiry_is_active(initialized_db):
    """Keys with NULL expiry_date should count as active."""
    async with get_session() as session:
        session.add(
            ApiKey(id="k1", service_id="svc-1", environment="dev", name="No expiry", revoked=False, expiry_date=None)
        )
        await session.commit()

    counts = await count_active_api_keys_by_service()
    assert counts[("svc-1", "dev")] == 1


@pytest.mark.asyncio
async def test_count_active_api_keys_all_revoked(initialized_db):
    """Service with only revoked keys should not appear in counts."""
    async with get_session() as session:
        session.add(
            ApiKey(id="k1", service_id="svc-1", environment="dev", name="Revoked", revoked=True, expiry_date=None)
        )
        await session.commit()

    counts = await count_active_api_keys_by_service()
    assert ("svc-1", "dev") not in counts


@pytest.mark.asyncio
async def test_count_active_api_keys_multiple_environments(initialized_db):
    """Test filtering by multiple environments."""
    async with get_session() as session:
        session.add(ApiKey(id="k1", service_id="svc-1", environment="dev", name="Dev", revoked=False))
        session.add(ApiKey(id="k2", service_id="svc-1", environment="staging", name="Staging", revoked=False))
        session.add(ApiKey(id="k3", service_id="svc-1", environment="prod", name="Prod", revoked=False))
        await session.commit()

    counts = await count_active_api_keys_by_service(environment=["dev", "staging"])
    assert ("svc-1", "dev") in counts
    assert ("svc-1", "staging") in counts
    assert ("svc-1", "prod") not in counts


@pytest.mark.asyncio
async def test_mark_stale_api_keys_revoked_marks_missing(initialized_db):
    """Keys not in the remote set are marked revoked with expiry_date set."""
    async with get_session() as session:
        session.add(ApiKey(id="k1", service_id="svc-1", environment="dev", name="Key1", revoked=False))
        session.add(ApiKey(id="k2", service_id="svc-1", environment="dev", name="Key2", revoked=False))
        session.add(ApiKey(id="k3", service_id="svc-1", environment="dev", name="Key3", revoked=False))
        await session.commit()

    count = await mark_stale_api_keys_revoked(["k1"], "dev", "svc-1")
    assert count == 2

    async with get_session() as session:
        k1 = (await session.execute(select(ApiKey).where(ApiKey.id == "k1"))).scalar_one()
        assert k1.revoked is False
        assert k1.expiry_date is None

        for kid in ("k2", "k3"):
            row = (await session.execute(select(ApiKey).where(ApiKey.id == kid))).scalar_one()
            assert row.revoked is True
            assert row.expiry_date is not None
            expiry = datetime.fromisoformat(row.expiry_date)
            assert expiry.tzinfo is not None


@pytest.mark.asyncio
async def test_mark_stale_api_keys_revoked_skips_already_revoked(initialized_db):
    """Already-revoked keys are left unchanged."""
    original_expiry = "2024-06-01T00:00:00+00:00"
    async with get_session() as session:
        session.add(
            ApiKey(
                id="k1",
                service_id="svc-1",
                environment="dev",
                name="Key1",
                revoked=True,
                expiry_date=original_expiry,
            )
        )
        await session.commit()

    count = await mark_stale_api_keys_revoked([], "dev", "svc-1")
    assert count == 0

    async with get_session() as session:
        row = (await session.execute(select(ApiKey).where(ApiKey.id == "k1"))).scalar_one()
        assert row.revoked is True
        assert row.expiry_date == original_expiry


@pytest.mark.asyncio
async def test_mark_stale_api_keys_revoked_empty_remote_list(initialized_db):
    """An empty remote list marks all non-revoked keys as revoked."""
    async with get_session() as session:
        session.add(ApiKey(id="k1", service_id="svc-1", environment="dev", name="Key1", revoked=False))
        session.add(ApiKey(id="k2", service_id="svc-1", environment="dev", name="Key2", revoked=False))
        await session.commit()

    count = await mark_stale_api_keys_revoked([], "dev", "svc-1")
    assert count == 2


@pytest.mark.asyncio
async def test_mark_stale_api_keys_revoked_no_local_keys(initialized_db):
    """Returns 0 when there are no local keys for the service."""
    count = await mark_stale_api_keys_revoked(["k1"], "dev", "svc-1")
    assert count == 0


@pytest.mark.asyncio
async def test_mark_stale_api_keys_revoked_environment_filter(initialized_db):
    """Only keys in the target environment are affected."""
    async with get_session() as session:
        session.add(ApiKey(id="k1", service_id="svc-1", environment="dev", name="Dev Key", revoked=False))
        session.add(ApiKey(id="k2", service_id="svc-1", environment="staging", name="Staging Key", revoked=False))
        await session.commit()

    count = await mark_stale_api_keys_revoked([], "dev", "svc-1")
    assert count == 1

    async with get_session() as session:
        dev_key = (await session.execute(select(ApiKey).where(ApiKey.id == "k1"))).scalar_one()
        assert dev_key.revoked is True

        staging_key = (await session.execute(select(ApiKey).where(ApiKey.id == "k2"))).scalar_one()
        assert staging_key.revoked is False


@pytest.mark.asyncio
async def test_mark_stale_api_keys_revoked_service_filter(initialized_db):
    """Only keys for the target service are affected."""
    async with get_session() as session:
        session.add(ApiKey(id="k1", service_id="svc-1", environment="dev", name="Svc1 Key", revoked=False))
        session.add(ApiKey(id="k2", service_id="svc-2", environment="dev", name="Svc2 Key", revoked=False))
        await session.commit()

    count = await mark_stale_api_keys_revoked([], "dev", "svc-1")
    assert count == 1

    async with get_session() as session:
        svc1_key = (await session.execute(select(ApiKey).where(ApiKey.id == "k1"))).scalar_one()
        assert svc1_key.revoked is True

        svc2_key = (await session.execute(select(ApiKey).where(ApiKey.id == "k2"))).scalar_one()
        assert svc2_key.revoked is False


@pytest.mark.asyncio
async def test_mark_stale_api_keys_revoked_preserves_past_expiry(initialized_db):
    """Stale key with a past expiry_date keeps its original value."""
    past_expiry = "2020-01-01T00:00:00+00:00"
    async with get_session() as session:
        session.add(
            ApiKey(
                id="k1",
                service_id="svc-1",
                environment="dev",
                name="Expired Key",
                revoked=False,
                expiry_date=past_expiry,
            )
        )
        await session.commit()

    count = await mark_stale_api_keys_revoked([], "dev", "svc-1")
    assert count == 1

    async with get_session() as session:
        row = (await session.execute(select(ApiKey).where(ApiKey.id == "k1"))).scalar_one()
        assert row.revoked is True
        assert row.expiry_date == past_expiry


@pytest.mark.asyncio
async def test_mark_stale_api_keys_revoked_updates_future_expiry(initialized_db):
    """Stale key with a future expiry_date gets it overwritten to now."""
    future_expiry = "2099-12-31T23:59:59+00:00"
    async with get_session() as session:
        session.add(
            ApiKey(
                id="k1",
                service_id="svc-1",
                environment="dev",
                name="Future Key",
                revoked=False,
                expiry_date=future_expiry,
            )
        )
        await session.commit()

    count = await mark_stale_api_keys_revoked([], "dev", "svc-1")
    assert count == 1

    async with get_session() as session:
        row = (await session.execute(select(ApiKey).where(ApiKey.id == "k1"))).scalar_one()
        assert row.revoked is True
        assert row.expiry_date != future_expiry
        expiry = datetime.fromisoformat(row.expiry_date)
        assert expiry.tzinfo is not None
