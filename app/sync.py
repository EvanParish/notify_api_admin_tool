from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Optional

from sqlalchemy import select

from . import models
from .api_client import NotificationAPI
from .db import get_session

ProgressCallback = Optional[Callable[[str], Awaitable[None]]]


class SyncManager:
    def __init__(
        self,
        api: NotificationAPI,
        max_concurrency: int = 25,
        environment: Optional[str] = None,
    ) -> None:
        self.api = api
        self.max_concurrency = max_concurrency
        self.environment = environment or "unknown"
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def sync_all(self, progress: ProgressCallback = None) -> None:
        await self.sync_services(progress)
        await self.sync_templates(progress)
        await self.sync_api_keys(progress)
        await self.sync_sms_senders(progress)
        await self.sync_provider_details(progress)

    async def sync_services(self, progress: ProgressCallback = None) -> None:
        if progress:
            await progress("Syncing services")
        services = await self.api.get_services()
        async with get_session() as session:
            for svc in services:
                # Convert permissions list to JSON string (including empty lists)
                permissions = svc.get("permissions")
                if isinstance(permissions, list):
                    permissions = json.dumps(permissions)
                
                record = models.Service(
                    id=svc.get("id"),
                    environment=self.environment,
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

    async def sync_templates(self, progress: ProgressCallback = None) -> None:
        async with get_session() as session:
            query = select(models.Service.id)
            if self.environment:
                query = query.where(models.Service.environment == self.environment)
            service_rows = (await session.execute(query)).scalars().all()

        tasks = [self._sync_templates_for_service(sid, progress) for sid in service_rows]
        await asyncio.gather(*tasks)

    async def _sync_templates_for_service(self, service_id: str, progress: ProgressCallback) -> None:
        async with self._semaphore:
            if progress:
                await progress(f"Templates for {service_id}")
            templates = await self.api.get_templates(service_id)
            async with get_session() as session:
                for tmpl in templates:
                    record = models.Template(
                        id=tmpl.get("id"),
                        environment=self.environment,
                        service_id=tmpl.get("service") or tmpl.get("service_id") or service_id,
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

    async def sync_api_keys(self, progress: ProgressCallback = None) -> None:
        async with get_session() as session:
            query = select(models.Service.id)
            if self.environment:
                query = query.where(models.Service.environment == self.environment)
            service_rows = (await session.execute(query)).scalars().all()

        tasks = [self._sync_api_keys_for_service(sid, progress) for sid in service_rows]
        await asyncio.gather(*tasks)

    async def _sync_api_keys_for_service(self, service_id: str, progress: ProgressCallback) -> None:
        async with self._semaphore:
            if progress:
                await progress(f"API keys for {service_id}")
            try:
                api_keys = await self.api.get_api_keys(service_id)
            except Exception as e:
                # Some services may not have API keys endpoint or return 404
                # This is not an error - just skip and continue
                if "404" in str(e) or "NOT FOUND" in str(e):
                    if progress:
                        await progress(f"No API keys for {service_id}")
                    return
                # For other errors, re-raise
                raise
            
            async with get_session() as session:
                for key in api_keys:
                    record = models.ApiKey(
                        id=key.get("id"),
                        environment=self.environment,
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

    async def sync_sms_senders(self, progress: ProgressCallback = None) -> None:
        async with get_session() as session:
            query = select(models.Service.id)
            if self.environment:
                query = query.where(models.Service.environment == self.environment)
            service_rows = (await session.execute(query)).scalars().all()

        tasks = [self._sync_sms_senders_for_service(sid, progress) for sid in service_rows]
        await asyncio.gather(*tasks)

    async def _sync_sms_senders_for_service(
        self, service_id: str, progress: ProgressCallback
    ) -> None:
        async with self._semaphore:
            if progress:
                await progress(f"SMS senders for {service_id}")
            sms_senders = await self.api.get_sms_senders(service_id)
            async with get_session() as session:
                for sender in sms_senders:
                    record = models.SmsSender(
                        id=sender.get("id"),
                        environment=self.environment,
                        service_id=sender.get("service_id") or service_id,
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

    async def sync_provider_details(self, progress: ProgressCallback = None) -> None:
        if progress:
            await progress("Syncing provider details")
        provider_details = await self.api.get_provider_details()
        async with get_session() as session:
            for provider in provider_details:
                record = models.ProviderDetail(
                    id=provider.get("id"),
                    environment=self.environment,
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
