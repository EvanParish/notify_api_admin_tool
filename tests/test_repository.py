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
)
from app.crypto import EncryptionManager
from app.models import Service, Template, LocalApiKey, ApiKey, Setting
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
    encryption = EncryptionManager("test-key")
    result = await get_secure_setting("nonexistent", encryption)
    assert result is None


@pytest.mark.asyncio
async def test_set_and_get_secure_setting(initialized_db):
    encryption = EncryptionManager("test-master-key")
    
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
async def test_list_templates_all(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service", active=True, restricted=False))
        session.add(Template(id="t1", service_id="svc-1", name="T1", template_type="email", content="C1", version=1))
        session.add(Template(id="t2", service_id="svc-1", name="T2", template_type="sms", content="C2", version=1))
        await session.commit()
    
    templates = await list_templates()
    assert len(templates) == 2


@pytest.mark.asyncio
async def test_list_templates_by_service(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service 1", active=True, restricted=False))
        session.add(Service(id="svc-2", name="Service 2", active=True, restricted=False))
        session.add(Template(id="t1", service_id="svc-1", name="T1", template_type="email", content="C1", version=1))
        session.add(Template(id="t2", service_id="svc-2", name="T2", template_type="email", content="C2", version=1))
        await session.commit()
    
    templates = await list_templates(service_id="svc-1")
    assert len(templates) == 1
    assert templates[0].id == "t1"


@pytest.mark.asyncio
async def test_list_templates_by_type(initialized_db):
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service", active=True, restricted=False))
        session.add(Template(id="t1", service_id="svc-1", name="T1", template_type="email", content="C1", version=1))
        session.add(Template(id="t2", service_id="svc-1", name="T2", template_type="sms", content="C2", version=1))
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
        session.add(Template(id="t1", service_id="svc-1", name="T1", template_type="email", content="C1", version=1))
        session.add(Template(id="t2", service_id="svc-1", name="T2", template_type="sms", content="C2", version=1))
        session.add(Template(id="t3", service_id="svc-2", name="T3", template_type="email", content="C3", version=1))
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
        session.add(LocalApiKey(service_id="svc-1", environment="dev", key_name="Key1", key_secret="enc1", key_type="normal"))
        session.add(LocalApiKey(service_id="svc-2", environment="prod", key_name="Key2", key_secret="enc2", key_type="test"))
        await session.commit()
    
    keys = await list_local_keys()
    assert len(keys) == 2


@pytest.mark.asyncio
async def test_list_local_keys_by_service(initialized_db):
    async with get_session() as session:
        session.add(LocalApiKey(service_id="svc-1", environment="dev", key_name="Key1", key_secret="enc1", key_type="normal"))
        session.add(LocalApiKey(service_id="svc-1", environment="prod", key_name="Key2", key_secret="enc2", key_type="test"))
        await session.commit()
    
    keys = await list_local_keys(service_id="svc-1", environment="dev")
    assert len(keys) == 1
    assert keys[0].key_name == "Key1"


@pytest.mark.asyncio
async def test_add_local_key(initialized_db):
    encryption = EncryptionManager("test-key")
    
    await add_local_key(
        encryption=encryption,
        service_id="svc-1",
        environment="dev",
        key_name="Test Key",
        key_secret="my-secret-123",
        key_type="normal"
    )
    
    keys = await list_local_keys(service_id="svc-1", environment="dev")
    assert len(keys) == 1
    assert keys[0].key_name == "Test Key"
    assert keys[0].key_type == "normal"
    assert keys[0].key_secret != "my-secret-123"  # Should be encrypted


@pytest.mark.asyncio
async def test_resolve_local_key(initialized_db):
    encryption = EncryptionManager("test-key")
    
    await add_local_key(
        encryption=encryption,
        service_id="svc-1",
        environment="dev",
        key_name="Test Key",
        key_secret="my-secret-456",
        key_type="test"
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
async def test_multiple_operations(initialized_db):
    encryption = EncryptionManager("master-key")
    
    # Add services
    async with get_session() as session:
        session.add(Service(id="svc-1", name="Service 1", active=True, restricted=False))
        await session.commit()
    
    # Add local key
    await add_local_key(encryption, "svc-1", "dev", "Key1", "secret1", "normal")
    
    # Add template
    async with get_session() as session:
        session.add(Template(
            id="tmpl-1", service_id="svc-1", name="Template 1",
            template_type="email", content="Hello", version=1
        ))
        await session.commit()
    
    # Verify all
    services = await list_services()
    templates = await list_templates(service_id="svc-1")
    keys = await list_local_keys(service_id="svc-1", environment="dev")
    
    assert len(services) == 1
    assert len(templates) == 1
    assert len(keys) == 1
