from __future__ import annotations

import base64
import json
from typing import Dict, List, Optional, Type, Union

from sqlalchemy import delete, or_, select

from .crypto import EncryptionManager
from .db import Base, get_session
from .models import (
    ApiKey,
    CommunicationItem,
    InboundNumber,
    LocalApiKey,
    ProviderDetail,
    Service,
    Setting,
    SmsSender,
    Template,
    User,
)


class DbSaltProvider:
    """SaltProvider implementation backed by the settings table."""

    async def get_salt(self) -> Optional[bytes]:
        value = await get_setting("encryption_salt")
        return base64.urlsafe_b64decode(value) if value else None

    async def store_salt(self, salt: bytes) -> None:
        await set_setting("encryption_salt", base64.urlsafe_b64encode(salt).decode())


def _is_archived_value(value: Optional[str]) -> bool:
    return bool(value) and value.lower().startswith("_archive")


def _is_archived(*values: Optional[str]) -> bool:
    return any(_is_archived_value(value) for value in values)


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


async def set_secure_setting(
    key: str, value: str, encryption: EncryptionManager
) -> None:
    encrypted = await encryption.encrypt(value)
    await set_setting(key, encrypted)


def _env_filter(column, environments: Optional[List[str]]):
    """Build environment filter clause for queries."""
    if not environments:
        return None  # No filter needed - show all
    # Include rows matching any selected environment OR with null environment
    return or_(column.in_(environments), column.is_(None))


async def list_services(
    environment: Optional[Union[str, List[str]]] = None,
) -> List[Service]:
    async with get_session() as session:
        query = select(Service)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(Service.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return [row for row in rows if not _is_archived(row.id, row.name)]


async def list_templates(
    service_id: Optional[str] = None,
    template_type: Optional[str] = None,
    environment: Optional[Union[str, List[str]]] = None,
) -> List[Template]:
    async with get_session() as session:
        query = select(Template)
        if service_id:
            query = query.where(Template.service_id == service_id)
        if template_type:
            query = query.where(Template.template_type == template_type)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(Template.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return [row for row in rows if not _is_archived(row.id, row.name)]


async def list_local_keys(
    service_id: Optional[str] = None,
    environment: Optional[Union[str, List[str]]] = None,
) -> List[LocalApiKey]:
    async with get_session() as session:
        query = select(LocalApiKey)
        if service_id:
            query = query.where(LocalApiKey.service_id == service_id)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(LocalApiKey.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return [row for row in rows if not _is_archived(str(row.id), row.key_name)]


async def add_local_key(
    encryption: EncryptionManager,
    service_id: str,
    environment: str,
    key_name: str,
    key_secret: str,
    key_type: str,
) -> None:
    encrypted_secret = await encryption.encrypt(key_secret)
    async with get_session() as session:
        record = LocalApiKey(
            service_id=service_id,
            environment=environment,
            key_name=key_name,
            key_secret=encrypted_secret,
            key_type=key_type,
        )
        session.add(record)
        await session.commit()


async def resolve_local_key(encryption: EncryptionManager, key_id: int) -> str:
    async with get_session() as session:
        result = await session.execute(
            select(LocalApiKey).where(LocalApiKey.id == key_id)
        )
        record = result.scalar_one()
        return await encryption.decrypt(record.key_secret)


async def list_api_keys(
    service_id: Optional[str] = None,
    environment: Optional[Union[str, List[str]]] = None,
) -> List[ApiKey]:
    async with get_session() as session:
        query = select(ApiKey)
        if service_id:
            query = query.where(ApiKey.service_id == service_id)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(ApiKey.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return [row for row in rows if not _is_archived(row.id, row.name)]


async def update_api_key_expiry(
    service_id: str,
    key_id: str,
    expiry_date: str,
    environment: Optional[str] = None,
) -> bool:
    async with get_session() as session:
        query = select(ApiKey).where(
            ApiKey.id == key_id, ApiKey.service_id == service_id
        )
        if environment:
            query = query.where(
                or_(ApiKey.environment == environment, ApiKey.environment.is_(None))
            )
        result = await session.execute(query)
        record = result.scalar_one_or_none()
        if not record:
            return False
        record.expiry_date = expiry_date
        await session.commit()
        return True


async def mark_api_key_revoked(
    service_id: str, key_id: str, environment: Optional[str] = None
) -> bool:
    async with get_session() as session:
        query = select(ApiKey).where(
            ApiKey.id == key_id, ApiKey.service_id == service_id
        )
        if environment:
            query = query.where(
                or_(ApiKey.environment == environment, ApiKey.environment.is_(None))
            )
        result = await session.execute(query)
        record = result.scalar_one_or_none()
        if not record:
            return False
        record.revoked = True
        await session.commit()
        return True


async def list_sms_senders(
    service_id: Optional[str] = None,
    environment: Optional[Union[str, List[str]]] = None,
) -> List[SmsSender]:
    async with get_session() as session:
        query = select(SmsSender)
        if service_id:
            query = query.where(SmsSender.service_id == service_id)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(SmsSender.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return [
            row
            for row in rows
            if not _is_archived(row.id, row.sms_sender, row.description)
        ]


async def update_sms_sender(
    sms_sender_id: str,
    service_id: str | None = None,
    sms_sender: str | None = None,
    description: str | None = None,
    provider_id: str | None = None,
    is_default: bool | None = None,
    rate_limit: int | None = None,
    rate_limit_interval: str | None = None,
    sms_sender_specifics: dict | None = None,
    environment: str | None = None,
) -> bool:
    async with get_session() as session:
        query = select(SmsSender).where(SmsSender.id == sms_sender_id)
        if environment:
            query = query.where(SmsSender.environment == environment)
        result = await session.execute(query)
        sender = result.scalars().first()
        if not sender:
            return False
        if sms_sender is not None:
            sender.sms_sender = sms_sender
        if description is not None:
            sender.description = description
        if provider_id is not None:
            sender.provider_id = provider_id
        if is_default is not None:
            sender.is_default = is_default
        if rate_limit is not None:
            sender.rate_limit = rate_limit
        if rate_limit_interval is not None:
            sender.rate_limit_interval = rate_limit_interval
        if sms_sender_specifics is not None:
            sender.sms_sender_specifics = sms_sender_specifics
        await session.commit()
        return True


async def list_provider_details(
    environment: Optional[Union[str, List[str]]] = None,
) -> List[ProviderDetail]:
    async with get_session() as session:
        query = select(ProviderDetail)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(ProviderDetail.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return rows


async def update_provider_detail(
    provider_id: str,
    priority: int | None = None,
    active: bool | None = None,
    load_balancing_weight: int | None = None,
    environment: str | None = None,
) -> bool:
    async with get_session() as session:
        query = select(ProviderDetail).where(ProviderDetail.id == provider_id)
        if environment:
            query = query.where(ProviderDetail.environment == environment)
        result = await session.execute(query)
        provider = result.scalars().first()
        if not provider:
            return False
        if priority is not None:
            provider.priority = priority
        if active is not None:
            provider.active = active
        if load_balancing_weight is not None:
            provider.load_balancing_weight = load_balancing_weight
        await session.commit()
        return True


async def list_communication_items(
    environment: Optional[Union[str, List[str]]] = None,
) -> List[CommunicationItem]:
    async with get_session() as session:
        query = select(CommunicationItem)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(CommunicationItem.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return rows


async def update_communication_item(
    item_id: str,
    name: str | None = None,
    default_send_indicator: bool | None = None,
    va_profile_item_id: int | None = None,
    environment: str | None = None,
) -> bool:
    async with get_session() as session:
        query = select(CommunicationItem).where(CommunicationItem.id == item_id)
        if environment:
            query = query.where(CommunicationItem.environment == environment)
        result = await session.execute(query)
        item = result.scalars().first()
        if not item:
            return False
        if name is not None:
            item.name = name
        if default_send_indicator is not None:
            item.default_send_indicator = default_send_indicator
        if va_profile_item_id is not None:
            item.va_profile_item_id = va_profile_item_id
        await session.commit()
        return True


async def list_users(
    environment: Optional[Union[str, List[str]]] = None,
) -> List[User]:
    async with get_session() as session:
        query = select(User)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(User.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return [
            row
            for row in rows
            if not (row.email_address or "").lower().startswith("_archived")
        ]


async def list_inbound_numbers(
    service_id: Optional[str] = None,
    environment: Optional[Union[str, List[str]]] = None,
) -> List[InboundNumber]:
    async with get_session() as session:
        query = select(InboundNumber)
        if service_id:
            query = query.where(InboundNumber.service_id == service_id)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(InboundNumber.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return rows


async def update_inbound_number(
    inbound_number_id: str,
    number: str | None = None,
    provider: str | None = None,
    active: bool | None = None,
    auth_parameter: str | None = None,
    self_managed: bool | None = None,
    url_endpoint: str | None = None,
    environment: str | None = None,
) -> bool:
    async with get_session() as session:
        query = select(InboundNumber).where(InboundNumber.id == inbound_number_id)
        if environment:
            query = query.where(InboundNumber.environment == environment)
        result = await session.execute(query)
        record = result.scalars().first()
        if not record:
            return False
        if number is not None:
            record.number = number
        if provider is not None:
            record.provider = provider
        if active is not None:
            record.active = active
        if auth_parameter is not None:
            record.auth_parameter = auth_parameter
        if self_managed is not None:
            record.self_managed = self_managed
        if url_endpoint is not None:
            record.url_endpoint = url_endpoint
        await session.commit()
        return True


# ---------------------------------------------------------------------------
# Bulk upsert functions (used by SyncManager)
# ---------------------------------------------------------------------------


# Map of table names to model classes for clearing data
CLEARABLE_TABLES: Dict[str, Type[Base]] = {
    "services": Service,
    "templates": Template,
    "api_keys": ApiKey,
    "sms_senders": SmsSender,
    "users": User,
    "provider_details": ProviderDetail,
    "communication_items": CommunicationItem,
    "inbound_numbers": InboundNumber,
    "local_api_keys": LocalApiKey,
}


async def clear_table_data(table_name: str, environment: str | None = None) -> int:
    """Clear data from a table, optionally filtered by environment.

    Returns the number of rows deleted.
    """
    model = CLEARABLE_TABLES.get(table_name)
    if not model:
        raise ValueError(f"Unknown table: {table_name}")

    async with get_session() as session:
        stmt = delete(model)
        if environment and hasattr(model, "environment"):
            stmt = stmt.where(model.environment == environment)
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount


async def list_service_ids(environment: Optional[str] = None) -> List[str]:
    """Return all service IDs, optionally filtered by environment."""
    async with get_session() as session:
        query = select(Service.id)
        if environment:
            query = query.where(Service.environment == environment)
        return list((await session.execute(query)).scalars().all())


async def upsert_services(raw: List[Dict], environment: str) -> None:
    async with get_session() as session:
        for svc in raw:
            permissions = svc.get("permissions")
            if isinstance(permissions, list):
                permissions = json.dumps(permissions)
            record = Service(
                id=svc.get("id"),
                environment=environment,
                name=svc.get("name", ""),
                active=svc.get("active", True),
                restricted=svc.get("restricted", False),
                message_limit=svc.get("message_limit"),
                rate_limit=svc.get("rate_limit"),
                research_mode=svc.get("research_mode", False),
                count_as_live=svc.get("count_as_live", True),
                prefix_sms=svc.get("prefix_sms", False),
                email_from=svc.get("email_from"),
                permissions=permissions,
                organisation_type=svc.get("organisation_type"),
                crown=svc.get("crown"),
                go_live_at=svc.get("go_live_at"),
                created_by=svc.get("created_by"),
            )
            await session.merge(record)
        await session.commit()


async def upsert_templates(
    raw: List[Dict], environment: str, fallback_service_id: Optional[str] = None
) -> None:
    async with get_session() as session:
        for tmpl in raw:
            record = Template(
                id=tmpl.get("id"),
                environment=environment,
                service_id=tmpl.get("service")
                or tmpl.get("service_id")
                or fallback_service_id,
                name=tmpl.get("name", ""),
                template_type=tmpl.get("type") or tmpl.get("template_type"),
                content=tmpl.get("content", ""),
                subject=tmpl.get("subject"),
                version=tmpl.get("version"),
                archived=tmpl.get("archived", False),
                hidden=tmpl.get("hidden", False),
                process_type=tmpl.get("process_type"),
                created_at=tmpl.get("created_at"),
                updated_at=tmpl.get("updated_at"),
                created_by=tmpl.get("created_by"),
                reply_to_email=tmpl.get("reply_to_email"),
            )
            await session.merge(record)
        await session.commit()


async def upsert_api_keys(raw: List[Dict], environment: str, service_id: str) -> None:
    async with get_session() as session:
        for key in raw:
            record = ApiKey(
                id=key.get("id"),
                environment=environment,
                service_id=service_id,
                name=key.get("name", ""),
                key_type=key.get("key_type"),
                expiry_date=key.get("expiry_date"),
                created_by=key.get("created_by"),
                created_at=key.get("created_at"),
                revoked=key.get("revoked", False),
                version=key.get("version"),
            )
            await session.merge(record)
        await session.commit()


async def upsert_sms_senders(
    raw: List[Dict], environment: str, fallback_service_id: str
) -> None:
    async with get_session() as session:
        for sender in raw:
            record = SmsSender(
                id=sender.get("id"),
                environment=environment,
                service_id=sender.get("service_id") or fallback_service_id,
                sms_sender=sender.get("sms_sender", ""),
                is_default=sender.get("is_default", False),
                archived=sender.get("archived", False),
                description=sender.get("description"),
                provider_id=sender.get("provider_id"),
                provider_name=sender.get("provider_name"),
                inbound_number_id=sender.get("inbound_number_id"),
                rate_limit=sender.get("rate_limit"),
                rate_limit_interval=sender.get("rate_limit_interval"),
                sms_sender_specifics=sender.get("sms_sender_specifics"),
                created_at=sender.get("created_at"),
                updated_at=sender.get("updated_at"),
            )
            await session.merge(record)
        await session.commit()


async def upsert_users(raw: List[Dict], environment: str) -> None:
    async with get_session() as session:
        for user in raw:
            email = (user.get("email_address") or "").lower()
            if email.startswith("_archived"):
                continue
            record = User(
                id=user.get("id"),
                environment=environment,
                email_address=user.get("email_address"),
                name=user.get("name"),
                state=user.get("state"),
                platform_admin=user.get("platform_admin", False),
                blocked=user.get("blocked", False),
                auth_type=user.get("auth_type"),
                mobile_number=user.get("mobile_number"),
                failed_login_count=user.get("failed_login_count"),
                logged_in_at=user.get("logged_in_at"),
                password_changed_at=user.get("password_changed_at"),
                current_session_id=user.get("current_session_id"),
                identity_provider_user_id=user.get("identity_provider_user_id"),
                additional_information=user.get("additional_information"),
                permissions=user.get("permissions"),
                services=user.get("services"),
                organisations=user.get("organisations"),
            )
            await session.merge(record)
        await session.commit()


async def upsert_provider_details(raw: List[Dict], environment: str) -> None:
    async with get_session() as session:
        for provider in raw:
            record = ProviderDetail(
                id=provider.get("id"),
                environment=environment,
                active=provider.get("active", False),
                created_by_name=provider.get("created_by_name"),
                current_month_billable_sms=provider.get("current_month_billable_sms"),
                display_name=provider.get("display_name"),
                identifier=provider.get("identifier"),
                load_balancing_weight=provider.get("load_balancing_weight"),
                notification_type=provider.get("notification_type"),
                priority=provider.get("priority"),
                supports_international=provider.get("supports_international"),
                updated_at=provider.get("updated_at"),
            )
            await session.merge(record)
        await session.commit()


async def upsert_communication_items(raw: List[Dict], environment: str) -> None:
    async with get_session() as session:
        for item in raw:
            record = CommunicationItem(
                id=item.get("id"),
                environment=environment,
                name=item.get("name", ""),
                va_profile_item_id=item.get("va_profile_item_id"),
                default_send_indicator=item.get("default_send_indicator", False),
            )
            await session.merge(record)
        await session.commit()


async def upsert_inbound_numbers(raw: List[Dict], environment: str) -> None:
    async with get_session() as session:
        for item in raw:
            service = item.get("service") or {}
            record = InboundNumber(
                id=item.get("id"),
                environment=environment,
                number=item.get("number", ""),
                provider=item.get("provider"),
                active=item.get("active", True),
                self_managed=item.get("self_managed", False),
                service_id=service.get("id") if service else None,
                service_name=service.get("name") if service else None,
                auth_parameter=item.get("auth_parameter"),
                url_endpoint=item.get("url_endpoint"),
            )
            await session.merge(record)
        await session.commit()
