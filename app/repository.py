from __future__ import annotations

from typing import List, Optional

from sqlalchemy import or_, select

from .crypto import EncryptionManager
from .db import get_session
from .models import ApiKey, LocalApiKey, Service, Setting, Template


async def get_setting(key: str) -> Optional[str]:
    async with get_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()
        return setting.value if setting else None


async def set_setting(key: str, value: str) -> None:
    async with get_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            setting = Setting(key=key, value=value)
            session.add(setting)
        await session.commit()


async def get_secure_setting(key: str, encryption: EncryptionManager) -> Optional[str]:
    value = await get_setting(key)
    if value is None:
        return None
    return await encryption.decrypt(value)


async def set_secure_setting(key: str, value: str, encryption: EncryptionManager) -> None:
    encrypted = await encryption.encrypt(value)
    await set_setting(key, encrypted)


async def list_services(environment: Optional[str] = None) -> List[Service]:
    async with get_session() as session:
        query = select(Service)
        if environment:
            query = query.where(or_(Service.environment == environment, Service.environment.is_(None)))
        return list((await session.execute(query)).scalars().all())


async def list_templates(
    service_id: Optional[str] = None,
    template_type: Optional[str] = None,
    environment: Optional[str] = None,
) -> List[Template]:
    async with get_session() as session:
        query = select(Template)
        if service_id:
            query = query.where(Template.service_id == service_id)
        if template_type:
            query = query.where(Template.template_type == template_type)
        if environment:
            query = query.where(
                or_(Template.environment == environment, Template.environment.is_(None))
            )
        return list((await session.execute(query)).scalars().all())


async def list_local_keys(service_id: Optional[str] = None) -> List[LocalApiKey]:
    async with get_session() as session:
        query = select(LocalApiKey)
        if service_id:
            query = query.where(LocalApiKey.service_id == service_id)
        return list((await session.execute(query)).scalars().all())


async def add_local_key(
    encryption: EncryptionManager,
    service_id: str,
    key_name: str,
    key_secret: str,
    key_type: str,
) -> None:
    encrypted_secret = await encryption.encrypt(key_secret)
    async with get_session() as session:
        record = LocalApiKey(
            service_id=service_id,
            key_name=key_name,
            key_secret=encrypted_secret,
            key_type=key_type,
        )
        session.add(record)
        await session.commit()


async def resolve_local_key(encryption: EncryptionManager, key_id: int) -> str:
    async with get_session() as session:
        result = await session.execute(select(LocalApiKey).where(LocalApiKey.id == key_id))
        record = result.scalar_one()
        return await encryption.decrypt(record.key_secret)


async def list_api_keys(
    service_id: Optional[str] = None, environment: Optional[str] = None
) -> List[ApiKey]:
    async with get_session() as session:
        query = select(ApiKey)
        if service_id:
            query = query.where(ApiKey.service_id == service_id)
        if environment:
            query = query.where(or_(ApiKey.environment == environment, ApiKey.environment.is_(None)))
        return list((await session.execute(query)).scalars().all())
