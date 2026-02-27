from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from .api_client import NotificationAPI
from .repository import (
    list_service_ids,
    upsert_api_keys,
    upsert_communication_items,
    upsert_inbound_numbers,
    upsert_provider_details,
    upsert_services,
    upsert_sms_senders,
    upsert_templates,
    upsert_users,
)

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
        await self.sync_users(progress)
        await self.sync_communication_items(progress)
        await self.sync_provider_details(progress)
        await self.sync_inbound_numbers(progress)

    async def sync_services(self, progress: ProgressCallback = None) -> None:
        if progress:
            await progress("Syncing services")
        services = await self.api.get_services()
        await upsert_services(services, self.environment)

    async def sync_templates(self, progress: ProgressCallback = None) -> None:
        service_ids = await list_service_ids(self.environment)
        tasks = [self._sync_templates_for_service(sid, progress) for sid in service_ids]
        await asyncio.gather(*tasks)

    async def _sync_templates_for_service(
        self, service_id: str, progress: ProgressCallback
    ) -> None:
        async with self._semaphore:
            if progress:
                await progress(f"Templates for {service_id}")
            templates = await self.api.get_templates(service_id)
            await upsert_templates(templates, self.environment, service_id)

    async def sync_api_keys(self, progress: ProgressCallback = None) -> None:
        service_ids = await list_service_ids(self.environment)
        tasks = [self._sync_api_keys_for_service(sid, progress) for sid in service_ids]
        await asyncio.gather(*tasks)

    async def _sync_api_keys_for_service(
        self, service_id: str, progress: ProgressCallback
    ) -> None:
        async with self._semaphore:
            if progress:
                await progress(f"API keys for {service_id}")
            try:
                api_keys = await self.api.get_api_keys(service_id)
            except Exception as e:
                if "404" in str(e) or "NOT FOUND" in str(e):
                    if progress:
                        await progress(f"No API keys for {service_id}")
                    return
                raise
            await upsert_api_keys(api_keys, self.environment, service_id)

    async def sync_sms_senders(self, progress: ProgressCallback = None) -> None:
        service_ids = await list_service_ids(self.environment)
        tasks = [
            self._sync_sms_senders_for_service(sid, progress) for sid in service_ids
        ]
        await asyncio.gather(*tasks)

    async def _sync_sms_senders_for_service(
        self, service_id: str, progress: ProgressCallback
    ) -> None:
        async with self._semaphore:
            if progress:
                await progress(f"SMS senders for {service_id}")
            sms_senders = await self.api.get_sms_senders(service_id)
            await upsert_sms_senders(sms_senders, self.environment, service_id)

    async def sync_users(self, progress: ProgressCallback = None) -> None:
        if progress:
            await progress("Syncing users")
        users = await self.api.get_users()
        await upsert_users(users, self.environment)

    async def sync_provider_details(self, progress: ProgressCallback = None) -> None:
        if progress:
            await progress("Syncing provider details")
        provider_details = await self.api.get_provider_details()
        await upsert_provider_details(provider_details, self.environment)

    async def sync_communication_items(self, progress: ProgressCallback = None) -> None:
        if progress:
            await progress("Syncing communication items")
        communication_items = await self.api.get_communication_items()
        await upsert_communication_items(communication_items, self.environment)

    async def sync_inbound_numbers(self, progress: ProgressCallback = None) -> None:
        if progress:
            await progress("Syncing inbound numbers")
        inbound_numbers = await self.api.get_inbound_numbers()
        await upsert_inbound_numbers(inbound_numbers, self.environment)
