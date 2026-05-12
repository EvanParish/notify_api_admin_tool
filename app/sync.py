from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import httpx

from .api_client import NotificationAPI
from .repository import (
    list_service_ids,
    mark_stale_api_keys_revoked,
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


@dataclass
class SyncError:
    """Represents an error that occurred during sync."""

    entity: str
    message: str
    status_code: int | None = None
    service_id: str | None = None

    def __str__(self) -> str:
        parts = [self.entity]
        if self.service_id:
            parts.append(f"({self.service_id})")
        parts.append("-")
        if self.status_code:
            parts.append(f"HTTP {self.status_code}:")
        parts.append(self.message)
        return " ".join(parts)


@dataclass
class SyncResult:
    """Aggregated result of a sync operation."""

    success_count: int = 0
    error_count: int = 0
    errors: list[SyncError] = field(default_factory=list)

    def add_error(self, error: SyncError) -> None:
        self.errors.append(error)
        self.error_count += 1

    def add_success(self) -> None:
        self.success_count += 1

    def merge(self, other: "SyncResult") -> None:
        self.success_count += other.success_count
        self.error_count += other.error_count
        self.errors.extend(other.errors)


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
        self.last_result: SyncResult | None = None

    def _extract_status_code(self, exc: Exception) -> int | None:
        """Extract HTTP status code from an exception if available."""
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code if exc.response else None
        return None

    def _extract_error_message(self, exc: Exception) -> str:
        """Extract a clean error message from an exception."""
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                body = exc.response.json() if exc.response else {}
                if "message" in body:
                    return str(body["message"])
                if "error" in body:
                    return str(body["error"])
            except Exception:
                pass
        return str(exc)

    async def sync_all(self, progress: ProgressCallback = None) -> SyncResult:
        result = SyncResult()
        for method in [
            self.sync_services,
            self.sync_templates,
            self.sync_api_keys,
            self.sync_sms_senders,
            self.sync_users,
            self.sync_communication_items,
            self.sync_provider_details,
            self.sync_inbound_numbers,
        ]:
            sub_result = await method(progress)
            result.merge(sub_result)
        self.last_result = result
        return result

    async def sync_services(self, progress: ProgressCallback = None) -> SyncResult:
        result = SyncResult()
        if progress:
            await progress("Syncing services")
        try:
            services = await self.api.get_services()
            await upsert_services(services, self.environment)
            result.add_success()
        except Exception as exc:
            result.add_error(
                SyncError(
                    entity="services",
                    message=self._extract_error_message(exc),
                    status_code=self._extract_status_code(exc),
                )
            )
            if progress:
                await progress(f"Error syncing services: {result.errors[-1]}")
        self.last_result = result
        return result

    async def sync_templates(self, progress: ProgressCallback = None) -> SyncResult:
        result = SyncResult()
        service_ids = await list_service_ids(self.environment)
        tasks = [self._sync_templates_for_service(sid, progress) for sid in service_ids]
        sub_results = await asyncio.gather(*tasks)
        for sub_result in sub_results:
            result.merge(sub_result)
        self.last_result = result
        return result

    async def _sync_templates_for_service(self, service_id: str, progress: ProgressCallback) -> SyncResult:
        result = SyncResult()
        async with self._semaphore:
            if progress:
                await progress(f"Templates for {service_id}")
            try:
                templates = await self.api.get_templates(service_id)
                await upsert_templates(templates, self.environment, service_id)
                result.add_success()
            except Exception as exc:
                result.add_error(
                    SyncError(
                        entity="templates",
                        message=self._extract_error_message(exc),
                        status_code=self._extract_status_code(exc),
                        service_id=service_id,
                    )
                )
                if progress:
                    await progress(f"Error syncing templates: {result.errors[-1]}")
        return result

    async def sync_api_keys(
        self,
        progress: ProgressCallback = None,
        service_ids: list[str] | None = None,
    ) -> SyncResult:
        result = SyncResult()
        if service_ids is None:
            service_ids = await list_service_ids(self.environment)
        tasks = [self._sync_api_keys_for_service(sid, progress) for sid in service_ids]
        sub_results = await asyncio.gather(*tasks)
        for sub_result in sub_results:
            result.merge(sub_result)
        self.last_result = result
        return result

    async def _sync_api_keys_for_service(self, service_id: str, progress: ProgressCallback) -> SyncResult:
        result = SyncResult()
        async with self._semaphore:
            if progress:
                await progress(f"API keys for {service_id}")
            try:
                api_keys = await self.api.get_api_keys(service_id)
                remote_key_ids = [k["id"] for k in api_keys]
                await mark_stale_api_keys_revoked(remote_key_ids, self.environment, service_id)
                await upsert_api_keys(api_keys, self.environment, service_id)
                result.add_success()
            except Exception as exc:
                status_code = self._extract_status_code(exc)
                if status_code == 404 or "404" in str(exc) or "NOT FOUND" in str(exc):
                    if progress:
                        await progress(f"No API keys for {service_id}")
                    result.add_success()
                else:
                    result.add_error(
                        SyncError(
                            entity="api_keys",
                            message=self._extract_error_message(exc),
                            status_code=status_code,
                            service_id=service_id,
                        )
                    )
                    if progress:
                        await progress(f"Error syncing API keys: {result.errors[-1]}")
        return result

    async def sync_sms_senders(self, progress: ProgressCallback = None) -> SyncResult:
        result = SyncResult()
        service_ids = await list_service_ids(self.environment)
        tasks = [self._sync_sms_senders_for_service(sid, progress) for sid in service_ids]
        sub_results = await asyncio.gather(*tasks)
        for sub_result in sub_results:
            result.merge(sub_result)
        self.last_result = result
        return result

    async def _sync_sms_senders_for_service(self, service_id: str, progress: ProgressCallback) -> SyncResult:
        result = SyncResult()
        async with self._semaphore:
            if progress:
                await progress(f"SMS senders for {service_id}")
            try:
                sms_senders = await self.api.get_sms_senders(service_id)
                await upsert_sms_senders(sms_senders, self.environment, service_id)
                result.add_success()
            except Exception as exc:
                status_code = self._extract_status_code(exc)
                if status_code == 404 or "404" in str(exc) or "NOT FOUND" in str(exc):
                    if progress:
                        await progress(f"No SMS senders for {service_id}")
                    result.add_success()
                else:
                    result.add_error(
                        SyncError(
                            entity="sms_senders",
                            message=self._extract_error_message(exc),
                            status_code=status_code,
                            service_id=service_id,
                        )
                    )
                    if progress:
                        await progress(f"Error syncing SMS senders: {result.errors[-1]}")
        return result

    async def sync_users(self, progress: ProgressCallback = None) -> SyncResult:
        result = SyncResult()
        if progress:
            await progress("Syncing users")
        try:
            users = await self.api.get_users()
            await upsert_users(users, self.environment)
            result.add_success()
        except Exception as exc:
            result.add_error(
                SyncError(
                    entity="users",
                    message=self._extract_error_message(exc),
                    status_code=self._extract_status_code(exc),
                )
            )
            if progress:
                await progress(f"Error syncing users: {result.errors[-1]}")
        self.last_result = result
        return result

    async def sync_provider_details(self, progress: ProgressCallback = None) -> SyncResult:
        result = SyncResult()
        if progress:
            await progress("Syncing provider details")
        try:
            provider_details = await self.api.get_provider_details()
            await upsert_provider_details(provider_details, self.environment)
            result.add_success()
        except Exception as exc:
            result.add_error(
                SyncError(
                    entity="provider_details",
                    message=self._extract_error_message(exc),
                    status_code=self._extract_status_code(exc),
                )
            )
            if progress:
                await progress(f"Error syncing provider details: {result.errors[-1]}")
        self.last_result = result
        return result

    async def sync_communication_items(self, progress: ProgressCallback = None) -> SyncResult:
        result = SyncResult()
        if progress:
            await progress("Syncing communication items")
        try:
            communication_items = await self.api.get_communication_items()
            await upsert_communication_items(communication_items, self.environment)
            result.add_success()
        except Exception as exc:
            result.add_error(
                SyncError(
                    entity="communication_items",
                    message=self._extract_error_message(exc),
                    status_code=self._extract_status_code(exc),
                )
            )
            if progress:
                await progress(f"Error syncing communication items: {result.errors[-1]}")
        self.last_result = result
        return result

    async def sync_inbound_numbers(self, progress: ProgressCallback = None) -> SyncResult:
        result = SyncResult()
        if progress:
            await progress("Syncing inbound numbers")
        try:
            inbound_numbers = await self.api.get_inbound_numbers()
            await upsert_inbound_numbers(inbound_numbers, self.environment)
            result.add_success()
        except Exception as exc:
            result.add_error(
                SyncError(
                    entity="inbound_numbers",
                    message=self._extract_error_message(exc),
                    status_code=self._extract_status_code(exc),
                )
            )
            if progress:
                await progress(f"Error syncing inbound numbers: {result.errors[-1]}")
        self.last_result = result
        return result
