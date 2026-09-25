from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from typing import Type

from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select

from .crypto import EncryptionManager
from .db import Base, get_session
from .models import (
    ApiKey,
    CommunicationItem,
    InboundNumber,
    LocalApiKey,
    ProviderDetail,
    Service,
    ServiceCallback,
    Setting,
    SmsSender,
    Template,
    User,
)

logger = logging.getLogger(__name__)


def _is_expired(expiry_date: str | None) -> bool:
    """Return True if *expiry_date* is a past ISO-8601 timestamp."""
    if not expiry_date:
        return False
    try:
        parsed = datetime.fromisoformat(expiry_date)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed < datetime.now(timezone.utc)
    except ValueError:
        return False


class DbSaltProvider:
    """SaltProvider implementation backed by the settings table."""

    async def get_salt(self) -> bytes | None:
        value = await get_setting("encryption_salt")
        return base64.urlsafe_b64decode(value) if value else None

    async def store_salt(self, salt: bytes) -> None:
        await set_setting("encryption_salt", base64.urlsafe_b64encode(salt).decode())


def _is_archived_value(value: str | None) -> bool:
    return bool(value) and value.lower().startswith("_archive")


def _is_archived(*values: str | None) -> bool:
    return any(_is_archived_value(value) for value in values)


def _is_encrypted_value(value: str | None) -> bool:
    if not value:
        return False
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        return False
    return len(decoded) >= 57 and decoded[0] == 0x80


async def get_setting(key: str) -> str | None:
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


async def get_secure_setting(key: str, encryption: EncryptionManager) -> str | None:
    value = await get_setting(key)
    if value is None:
        return None
    return await encryption.decrypt(value)


async def set_secure_setting(key: str, value: str, encryption: EncryptionManager) -> None:
    encrypted = await encryption.encrypt(value)
    await set_setting(key, encrypted)


# Sentinel used by the UI's "Filter by Service" select to mean "no service assigned".
# Lives here so the select option key and the query clause cannot drift apart.
UNASSIGNED_SERVICE_FILTER = "__unassigned__"


def _env_filter(column, environments: list[str] | None):
    """Build environment filter clause for queries."""
    if not environments:
        return None  # No filter needed - show all
    # Include rows matching any selected environment OR with null environment
    return or_(column.in_(environments), column.is_(None))


def _service_filter(column, service_ids: str | list[str] | None):
    """Build service ID filter clause for queries."""
    if not service_ids:
        return None
    ids = [service_ids] if isinstance(service_ids, str) else service_ids
    return column.in_(ids)


def _service_filter_with_unassigned(column, service_ids: str | list[str] | None):
    """Like :func:`_service_filter`, but maps ``UNASSIGNED_SERVICE_FILTER`` to ``column IS NULL``.

    Real service IDs and the sentinel compose as OR, so selecting one service plus
    "unassigned" returns both sets.  Empty and ``None`` entries are ignored rather than
    emitting ``IN ('')``, which would match nothing.

    Only valid for columns whose archived-service clause also admits NULL -- currently
    ``InboundNumber.service_id`` and ``ApiKey.service_id``.  The other three list
    functions use a bare ``IN (_active_service_ids)``, so a sentinel passed there would
    always return zero rows.
    """
    ids = [service_ids] if isinstance(service_ids, str) else list(service_ids or [])
    disjuncts = []
    real_ids = [sid for sid in ids if sid and sid != UNASSIGNED_SERVICE_FILTER]
    if real_ids:
        disjuncts.append(column.in_(real_ids))
    if UNASSIGNED_SERVICE_FILTER in ids:
        disjuncts.append(column.is_(None))
    return or_(*disjuncts) if disjuncts else None


def _active_service_ids(environments: list[str] | None = None):
    """Subquery returning service IDs that are not archived.

    The ``_`` is escaped because SQL ``LIKE`` treats a bare underscore as "any single
    character", which would also exclude names like "Zarchive Test".  The prefix is
    meant literally, matching :func:`_is_archived_value`'s ``startswith("_archive")``.
    """
    subq = select(Service.id).where(func.lower(Service.name).not_like(r"\_archive%", escape="\\"))
    env_clause = _env_filter(Service.environment, environments)
    if env_clause is not None:
        subq = subq.where(env_clause)
    return subq


async def list_services(
    environment: str | list[str] | None = None,
) -> list[Service]:
    async with get_session() as session:
        query = select(Service)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(Service.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        return [row for row in rows if not _is_archived(row.id, row.name)]


async def update_service(
    service_id: str,
    message_limit: int | None = None,
    rate_limit: int | None = None,
    environment: str | None = None,
) -> bool:
    async with get_session() as session:
        query = select(Service).where(Service.id == service_id)
        if environment:
            query = query.where(Service.environment == environment)
        result = await session.execute(query)
        service = result.scalars().first()
        if not service:
            return False
        if message_limit is not None:
            service.message_limit = message_limit
        if rate_limit is not None:
            service.rate_limit = rate_limit
        await session.commit()
        return True


async def update_service_permissions(service_id: str, permissions: list[str], environment: str) -> bool:
    """Write the verified permission set to the local cache.

    ``environment`` is required, unlike ``update_service``: a permission write must never
    land on a row belonging to a different environment.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Service).where(Service.id == service_id, Service.environment == environment)
        )
        service = result.scalars().first()
        if not service:
            return False
        service.permissions = json.dumps(list(permissions))
        await session.commit()
        return True


async def list_templates(
    service_id: str | list[str] | None = None,
    template_type: str | None = None,
    environment: str | list[str] | None = None,
) -> list[Template]:
    async with get_session() as session:
        query = select(Template)
        svc_clause = _service_filter(Template.service_id, service_id)
        if svc_clause is not None:
            query = query.where(svc_clause)
        if template_type:
            query = query.where(Template.template_type == template_type)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(Template.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        query = query.where(Template.service_id.in_(_active_service_ids(envs)))
        rows = list((await session.execute(query)).scalars().all())
        return [row for row in rows if not _is_archived(row.id, row.name)]


async def list_local_keys(
    service_id: str | None = None,
    environment: str | list[str] | None = None,
) -> list[LocalApiKey]:
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
    api_key_id: str | None = None,
) -> int:
    """Store an encrypted key secret and return the new row's id.

    *api_key_id* is optional because the create flow only learns the remote id after
    a subsequent sync, and the Settings page manual-entry form never learns it at all.
    """
    encrypted_secret = await encryption.encrypt(key_secret)
    async with get_session() as session:
        record = LocalApiKey(
            service_id=service_id,
            environment=environment,
            key_name=key_name,
            key_secret=encrypted_secret,
            key_type=key_type,
            api_key_id=api_key_id,
        )
        session.add(record)
        await session.commit()
        return record.id


async def link_local_key(local_row_id: int, api_key_id: str) -> bool:
    """Point an existing local key row at a remote ``ApiKey``.

    Returns False when the row is gone, so callers can warn rather than raise: the
    secret is already stored by this point and must not be lost to a failed link.
    """
    async with get_session() as session:
        record = await session.get(LocalApiKey, local_row_id)
        if record is None:
            return False
        record.api_key_id = api_key_id
        await session.commit()
        return True


async def backfill_local_key_api_ids() -> int:
    """Link unlinked local keys to their remote key by name, and return the count.

    Only an unambiguous match is linked.  Unlike the create flow, which can assume the
    newest key with a given name is the one it just made, this runs long after the
    fact with no tiebreaker available, so two remote keys sharing a name leave the
    local row unlinked rather than pointing a secret at the wrong key.
    """
    async with get_session() as session:
        unlinked = list(
            (await session.execute(select(LocalApiKey).where(LocalApiKey.api_key_id.is_(None)))).scalars().all()
        )
        linked = 0
        for row in unlinked:
            matches = list(
                (
                    await session.execute(
                        select(ApiKey).where(
                            ApiKey.service_id == row.service_id,
                            ApiKey.environment == row.environment,
                            ApiKey.name == row.key_name,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(matches) != 1:
                continue
            row.api_key_id = matches[0].id
            linked += 1
        if linked:
            await session.commit()
        return linked


async def resolve_local_key(encryption: EncryptionManager, key_id: int) -> str:
    async with get_session() as session:
        result = await session.execute(select(LocalApiKey).where(LocalApiKey.id == key_id))
        record = result.scalar_one()
        return await encryption.decrypt(record.key_secret)


async def list_api_keys(
    service_id: str | list[str] | None = None,
    environment: str | list[str] | None = None,
) -> list[ApiKey]:
    async with get_session() as session:
        query = select(ApiKey)
        svc_clause = _service_filter(ApiKey.service_id, service_id)
        if svc_clause is not None:
            query = query.where(svc_clause)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(ApiKey.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        query = query.where(or_(ApiKey.service_id.in_(_active_service_ids(envs)), ApiKey.service_id.is_(None)))
        rows = list((await session.execute(query)).scalars().all())
        return [row for row in rows if not _is_archived(row.id, row.name)]


async def count_templates_by_service(
    environment: str | list[str] | None = None,
) -> dict[tuple[str, str], int]:
    """Count templates grouped by (service_id, environment)."""
    async with get_session() as session:
        query = select(Template.service_id, Template.environment, func.count(Template.id)).group_by(
            Template.service_id, Template.environment
        )
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(Template.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = (await session.execute(query)).all()
        return {(sid, env): cnt for sid, env, cnt in rows}


async def count_active_api_keys_by_service(
    environment: str | list[str] | None = None,
) -> dict[tuple[str, str], int]:
    """Count active (non-revoked, non-expired) API keys grouped by (service_id, environment)."""
    now = datetime.now(timezone.utc).isoformat()
    async with get_session() as session:
        query = (
            select(ApiKey.service_id, ApiKey.environment, func.count(ApiKey.id))
            .where(ApiKey.revoked == False)  # noqa: E712
            .where(or_(ApiKey.expiry_date.is_(None), ApiKey.expiry_date > now))
            .group_by(ApiKey.service_id, ApiKey.environment)
        )
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(ApiKey.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = (await session.execute(query)).all()
        return {(sid, env): cnt for sid, env, cnt in rows if sid is not None and env is not None}


async def count_sms_senders_by_service(
    environment: str | list[str] | None = None,
) -> dict[tuple[str, str], int]:
    """Count non-archived SMS senders grouped by (service_id, environment).

    Archived means ``SmsSender.archived`` is true. This deliberately differs from
    ``list_sms_senders``, which applies the ``_archive`` name-prefix rule instead.
    """
    async with get_session() as session:
        query = (
            select(SmsSender.service_id, SmsSender.environment, func.count(SmsSender.id))
            .where(SmsSender.archived == False)  # noqa: E712
            .group_by(SmsSender.service_id, SmsSender.environment)
        )
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(SmsSender.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = (await session.execute(query)).all()
        return {(sid, env): cnt for sid, env, cnt in rows if sid is not None and env is not None}


async def update_api_key_expiry(
    service_id: str,
    key_id: str,
    expiry_date: str,
    environment: str | None = None,
) -> bool:
    async with get_session() as session:
        query = select(ApiKey).where(ApiKey.id == key_id, ApiKey.service_id == service_id)
        if environment:
            query = query.where(or_(ApiKey.environment == environment, ApiKey.environment.is_(None)))
        result = await session.execute(query)
        record = result.scalar_one_or_none()
        if not record:
            return False
        record.expiry_date = expiry_date
        await session.commit()
        return True


async def mark_api_key_revoked(service_id: str, key_id: str, environment: str | None = None) -> bool:
    async with get_session() as session:
        query = select(ApiKey).where(ApiKey.id == key_id, ApiKey.service_id == service_id)
        if environment:
            query = query.where(or_(ApiKey.environment == environment, ApiKey.environment.is_(None)))
        result = await session.execute(query)
        record = result.scalar_one_or_none()
        if not record:
            return False
        record.revoked = True
        if not _is_expired(record.expiry_date):
            record.expiry_date = datetime.now(timezone.utc).isoformat()
        await session.commit()
        return True


async def mark_stale_api_keys_revoked(
    remote_key_ids: list[str],
    environment: str,
    service_id: str,
) -> int:
    """Mark local API keys as revoked if they were not returned by the remote API.

    Keys that are already revoked are left unchanged.
    Returns the number of keys newly marked as revoked.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with get_session() as session:
        query = select(ApiKey).where(
            ApiKey.service_id == service_id,
            ApiKey.environment == environment,
            ApiKey.revoked == False,  # noqa: E712
        )
        if remote_key_ids:
            query = query.where(ApiKey.id.not_in(remote_key_ids))
        rows = (await session.execute(query)).scalars().all()
        for row in rows:
            row.revoked = True
            if not _is_expired(row.expiry_date):
                row.expiry_date = now
        await session.commit()
        return len(rows)


async def list_sms_senders(
    service_id: str | list[str] | None = None,
    environment: str | list[str] | None = None,
) -> list[SmsSender]:
    async with get_session() as session:
        query = select(SmsSender)
        svc_clause = _service_filter(SmsSender.service_id, service_id)
        if svc_clause is not None:
            query = query.where(svc_clause)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(SmsSender.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        query = query.where(SmsSender.service_id.in_(_active_service_ids(envs)))
        rows = list((await session.execute(query)).scalars().all())
        return [row for row in rows if not _is_archived(row.id, row.sms_sender, row.description)]


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
    environment: str | list[str] | None = None,
) -> list[ProviderDetail]:
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
    environment: str | list[str] | None = None,
) -> list[CommunicationItem]:
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


# ---------------------------------------------------------------------------
# User list cache
#
# ``list_users`` decrypts ``name`` and ``email_address`` for every row, and the
# API Keys page calls it on every table render to resolve ``created_by`` to an
# email address.  Results are memoised for 12 hours or until the users table is
# written, whichever comes first.
#
# ``time.monotonic`` rather than wall clock: an NTP step or DST change must not
# be able to extend or collapse the TTL.
# ---------------------------------------------------------------------------
_USER_CACHE_TTL_SECONDS = 12 * 60 * 60
_user_cache: dict[tuple, tuple[float, list[User]]] = {}


def _user_cache_key(envs: list[str] | None, encryption: EncryptionManager | None) -> tuple:
    """Build the cache key for a ``list_users`` call.

    Environments are sorted so ``["dev", "prod"]`` and ``["prod", "dev"]`` share one
    entry.  ``encryption is None`` is part of the key so that a caller without an
    ``EncryptionManager`` still raises on encrypted data instead of being served the
    decrypted rows an encryption-enabled caller put in the cache.
    """
    return (tuple(sorted(envs)) if envs else (), encryption is None)


def _invalidate_user_cache() -> None:
    """Drop every cached user list.

    Clear-all rather than per-environment: an entry keyed ``("dev", "prod")`` holds
    rows that a dev-only write invalidates.
    """
    _user_cache.clear()


async def list_users(
    environment: str | list[str] | None = None,
    encryption: EncryptionManager | None = None,
) -> list[User]:
    envs = [environment] if isinstance(environment, str) else environment
    cache_key = _user_cache_key(envs, encryption)
    cached = _user_cache.get(cache_key)
    if cached is not None and cached[0] > time.monotonic():
        # Shallow copy: a caller sorting or clearing the list in place must not be
        # able to corrupt the cached entry.
        return list(cached[1])

    async with get_session() as session:
        query = select(User)
        env_clause = _env_filter(User.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        rows = list((await session.execute(query)).scalars().all())
        visible_rows: list[User] = []
        for row in rows:
            has_encrypted_identity = _is_encrypted_value(row.name) or _is_encrypted_value(row.email_address)
            if encryption is None and has_encrypted_identity:
                raise ValueError("EncryptionManager is required to list users when encrypted identity data is present")
            if encryption is not None:
                plaintext_fields: list[str] = []
                if row.name and not _is_encrypted_value(row.name):
                    plaintext_fields.append("name")
                if row.email_address and not _is_encrypted_value(row.email_address):
                    plaintext_fields.append("email_address")
                if plaintext_fields:
                    fields = ", ".join(plaintext_fields)
                    raise ValueError(
                        "Plaintext user identity data detected "
                        f"(user_id={row.id}, environment={row.environment}, fields={fields}); "
                        "migration is required."
                    )
                if _is_encrypted_value(row.name):
                    row.name = await encryption.decrypt(row.name)
                if _is_encrypted_value(row.email_address):
                    row.email_address = await encryption.decrypt(row.email_address)
            if not (row.email_address or "").lower().startswith("_archived"):
                visible_rows.append(row)
        # Reached only on success: the ValueError paths above must not be cached.
        _user_cache[cache_key] = (time.monotonic() + _USER_CACHE_TTL_SECONDS, visible_rows)
        return list(visible_rows)


async def list_inbound_numbers(
    service_id: str | list[str] | None = None,
    environment: str | list[str] | None = None,
) -> list[InboundNumber]:
    """List cached inbound numbers, optionally filtered by service and environment.

    ``service_id`` accepts a single ID, a list of IDs, or ``UNASSIGNED_SERVICE_FILTER``
    to select numbers with no service.  The sentinel composes with real IDs as OR.

    Note that the two are not a partition of all rows: ``_active_service_ids`` is
    environment-scoped, so a number pointing at a service cached only in another
    environment is excluded regardless of the filter.
    """
    async with get_session() as session:
        query = select(InboundNumber)
        svc_clause = _service_filter_with_unassigned(InboundNumber.service_id, service_id)
        if svc_clause is not None:
            query = query.where(svc_clause)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(InboundNumber.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        query = query.where(
            or_(InboundNumber.service_id.in_(_active_service_ids(envs)), InboundNumber.service_id.is_(None))
        )
        rows = list((await session.execute(query)).scalars().all())
        return rows


async def list_service_callbacks(
    service_id: str | list[str] | None = None,
    environment: str | list[str] | None = None,
) -> list[ServiceCallback]:
    async with get_session() as session:
        query = select(ServiceCallback)
        svc_clause = _service_filter(ServiceCallback.service_id, service_id)
        if svc_clause is not None:
            query = query.where(svc_clause)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(ServiceCallback.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)
        query = query.where(ServiceCallback.service_id.in_(_active_service_ids(envs)))
        rows = list((await session.execute(query)).scalars().all())
        return rows


async def delete_service_callback(callback_id: str, environment: str) -> bool:
    """Delete a locally cached service callback.

    Returns True when a row was removed, so the UI can warn that the cache was already
    out of sync rather than reporting a failure.
    """
    async with get_session() as session:
        stmt = delete(ServiceCallback).where(
            ServiceCallback.id == callback_id,
            ServiceCallback.environment == environment,
        )
        result = await session.execute(stmt)
        await session.commit()
        return bool(result.rowcount)


async def prune_service_callbacks(service_id: str, environment: str, keep_ids: list[str]) -> int:
    """Remove cached callbacks for a service that the remote API no longer returns.

    An empty *keep_ids* prunes every cached row for that service and environment, which is
    the correct response to a successful but empty list response. Returns the row count
    removed.
    """
    async with get_session() as session:
        stmt = delete(ServiceCallback).where(
            ServiceCallback.service_id == service_id,
            ServiceCallback.environment == environment,
        )
        if keep_ids:
            stmt = stmt.where(ServiceCallback.id.not_in(keep_ids))
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount


async def update_inbound_number(
    inbound_number_id: str,
    number: str | None = None,
    provider: str | None = None,
    active: bool | None = None,
    auth_parameter: str | None = None,
    self_managed: bool | None = None,
    url_endpoint: str | None = None,
    service_id: str | None = None,
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
        if service_id is not None:
            record.service_id = service_id
        await session.commit()
        return True


# ---------------------------------------------------------------------------
# Bulk upsert functions (used by SyncManager)
# ---------------------------------------------------------------------------


# Map of table names to model classes for clearing data
CLEARABLE_TABLES: dict[str, Type[Base]] = {
    "services": Service,
    "templates": Template,
    "api_keys": ApiKey,
    "sms_senders": SmsSender,
    "users": User,
    "provider_details": ProviderDetail,
    "communication_items": CommunicationItem,
    "inbound_numbers": InboundNumber,
    "service_callbacks": ServiceCallback,
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


async def list_service_ids(environment: str | None = None) -> list[str]:
    """Return all service IDs, optionally filtered by environment."""
    async with get_session() as session:
        query = select(Service.id)
        if environment:
            query = query.where(Service.environment == environment)
        return list((await session.execute(query)).scalars().all())


async def list_service_environments(service_id: str) -> list[str]:
    """Return all environments where a service with the given ID exists."""
    async with get_session() as session:
        query = select(Service.environment).where(Service.id == service_id)
        return list((await session.execute(query)).scalars().all())


async def get_service_by_name(name: str, environment: str) -> Service | None:
    """Return a service by name and environment, or None if not found."""
    async with get_session() as session:
        query = select(Service).where(Service.name == name, Service.environment == environment)
        return (await session.execute(query)).scalar_one_or_none()


async def list_environments_for_service_name(name: str) -> list[str]:
    """Return all environments where a service with the given name exists."""
    async with get_session() as session:
        query = select(Service.environment).where(Service.name == name)
        return list((await session.execute(query)).scalars().all())


async def upsert_services(raw: list[dict], environment: str) -> None:
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


async def upsert_templates(raw: list[dict], environment: str, fallback_service_id: str | None = None) -> None:
    async with get_session() as session:
        for tmpl in raw:
            record = Template(
                id=tmpl.get("id"),
                environment=environment,
                service_id=tmpl.get("service") or tmpl.get("service_id") or fallback_service_id,
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
                communication_item_id=tmpl.get("communication_item_id"),
            )
            await session.merge(record)
        await session.commit()


async def upsert_api_keys(raw: list[dict], environment: str, service_id: str) -> None:
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
                last_used_at=key.get("last_used_at"),
                revoked=key.get("revoked", False),
                version=key.get("version"),
            )
            await session.merge(record)
        await session.commit()


async def upsert_sms_senders(raw: list[dict], environment: str, fallback_service_id: str) -> None:
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


async def upsert_users(
    raw: list[dict],
    environment: str,
    encryption: EncryptionManager | None = None,
) -> None:
    if encryption is None:
        raise ValueError("EncryptionManager is required for upsert_users to protect user identity fields")

    async with get_session() as session:
        for user in raw:
            email = (user.get("email_address") or "").lower()
            if email.startswith("_archived"):
                continue
            email_address = user.get("email_address")
            name = user.get("name")
            if email_address is not None:
                email_address = await encryption.encrypt(email_address)
            if name is not None:
                name = await encryption.encrypt(name)
            record = User(
                id=user.get("id"),
                environment=environment,
                email_address=email_address,
                name=name,
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
    _invalidate_user_cache()


async def migrate_plaintext_users_to_encrypted(
    encryption: EncryptionManager,
    environment: str | list[str] | None = None,
) -> int:
    async with get_session() as session:
        query = select(User)
        envs = [environment] if isinstance(environment, str) else environment
        env_clause = _env_filter(User.environment, envs)
        if env_clause is not None:
            query = query.where(env_clause)

        rows = list((await session.execute(query)).scalars().all())
        migrated = 0
        for row in rows:
            updated = False
            if row.name and not _is_encrypted_value(row.name):
                row.name = await encryption.encrypt(row.name)
                updated = True
            if row.email_address and not _is_encrypted_value(row.email_address):
                row.email_address = await encryption.encrypt(row.email_address)
                updated = True
            if updated:
                migrated += 1
        if migrated:
            await session.commit()
            _invalidate_user_cache()
        return migrated


async def upsert_provider_details(raw: list[dict], environment: str) -> None:
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


async def upsert_communication_items(raw: list[dict], environment: str) -> None:
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


async def upsert_inbound_numbers(raw: list[dict], environment: str) -> None:
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
                auth_parameter=item.get("auth_parameter"),
                url_endpoint=item.get("url_endpoint"),
            )
            await session.merge(record)
        await session.commit()


async def upsert_service_callbacks(raw: list[dict], environment: str, service_id: str) -> None:
    async with get_session() as session:
        for item in raw:
            callback_id = item.get("id")
            if not callback_id:
                logger.warning(
                    "Skipping service callback without an id for service %s in %s",
                    service_id,
                    environment,
                )
                continue
            record = ServiceCallback(
                id=callback_id,
                environment=environment,
                service_id=item.get("service_id") or service_id,
                url=item.get("url"),
                callback_type=item.get("callback_type"),
                callback_channel=item.get("callback_channel"),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                updated_by_id=item.get("updated_by_id"),
                notification_statuses=item.get("notification_statuses"),
                include_provider_payload=item.get("include_provider_payload", False),
            )
            await session.merge(record)
        await session.commit()
