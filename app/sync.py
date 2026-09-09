from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import httpx

from .api_client import NotificationAPI
from .crypto import EncryptionManager
from .repository import (
    list_service_ids,
    mark_stale_api_keys_revoked,
    migrate_plaintext_users_to_encrypted,
    prune_service_callbacks,
    upsert_api_keys,
    upsert_communication_items,
    upsert_inbound_numbers,
    upsert_provider_details,
    upsert_service_callbacks,
    upsert_services,
    upsert_sms_senders,
    upsert_templates,
    upsert_users,
)

logger = logging.getLogger(__name__)

#: Interval between UI pushes. A full sync emits one update per service per
#: fan-out phase; without throttling a few hundred services floods the socket.
PROGRESS_THROTTLE_SECONDS = 0.1

TextCallback = Callable[[str], Awaitable[None]]
UpdateCallback = Callable[[int, int, str], Awaitable[None]]


@dataclass
class SyncProgress:
    """Tracks completed work units and pushes throttled updates to a sink.

    ``total`` is declared up front by whoever knows the work count, so the
    fraction ``done / total`` is monotonic. ``locked`` stops fan-out methods
    from re-declaring totals that :meth:`SyncManager.sync_all` already added.
    """

    on_update: UpdateCallback
    total: int = 0
    done: int = 0
    locked: bool = False
    time_fn: Callable[[], float] = time.monotonic
    _last_push: float | None = field(default=None, repr=False)

    @classmethod
    def from_callable(cls, fn: TextCallback) -> "SyncProgress":
        """Adapt a text-only sink that ignores counts."""

        async def on_update(done: int, total: int, msg: str) -> None:
            await fn(msg)

        return cls(on_update)

    @classmethod
    def coerce(cls, progress: "SyncProgress | TextCallback | None") -> "SyncProgress | None":
        """Normalize a caller-supplied progress argument."""
        if progress is None or isinstance(progress, cls):
            return progress
        return cls.from_callable(progress)

    def add_total(self, n: int) -> None:
        self.total += n

    async def step(self, msg: str) -> None:
        """Record one completed work unit and push."""
        if self.done < self.total:
            self.done += 1
        await self._push(msg)

    async def message(self, msg: str) -> None:
        """Push a status message without recording work."""
        await self._push(msg)

    async def _push(self, msg: str) -> None:
        now = self.time_fn()
        is_first = self._last_push is None
        is_complete = self.total > 0 and self.done >= self.total
        if not is_first and not is_complete and now - self._last_push < PROGRESS_THROTTLE_SECONDS:
            return
        self._last_push = now
        await self.on_update(self.done, self.total, msg)


ProgressCallback = Optional[SyncProgress]

#: Human-readable phase names shown next to the progress counter. Deliberately
#: coarse: per-service messages are meaningless under concurrent fan-out.
PHASE_SERVICES = "services"
PHASE_TEMPLATES = "templates"
PHASE_API_KEYS = "api keys"
PHASE_SMS_SENDERS = "sms senders"
PHASE_USERS = "users"
PHASE_COMMUNICATION_ITEMS = "communication items"
PHASE_PROVIDER_DETAILS = "provider details"
PHASE_INBOUND_NUMBERS = "inbound numbers"
PHASE_CALLBACKS = "callbacks"


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
        encryption: EncryptionManager | None = None,
    ) -> None:
        self.api = api
        self.max_concurrency = max_concurrency
        self.environment = environment or "unknown"
        self.encryption = encryption
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

    def _record_error(
        self,
        result: SyncResult,
        entity: str,
        exc: Exception | None = None,
        *,
        message: str | None = None,
        status_code: int | None = None,
        service_id: str | None = None,
    ) -> SyncError:
        """Build a SyncError, log it with service context, and record it on *result*."""
        if message is None:
            message = self._extract_error_message(exc) if exc is not None else "unknown error"
        if status_code is None and exc is not None:
            status_code = self._extract_status_code(exc)
        error = SyncError(
            entity=entity,
            message=message,
            status_code=status_code,
            service_id=service_id,
        )
        logger.warning(
            "Sync error env=%s entity=%s service=%s: %s",
            self.environment,
            entity,
            service_id or "-",
            error,
        )
        result.add_error(error)
        return error

    async def _begin(self, progress: "SyncProgress | None", phase: str, units: int = 1) -> None:
        """Declare work units (unless sync_all already did) and announce the phase."""
        if progress is None:
            return
        if not progress.locked:
            progress.add_total(units)
        await progress.message(phase)

    async def sync_all(self, progress: ProgressCallback = None) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()

        # The service count is only known after sync_services populates the
        # cache, so totals are declared in two phases. ``locked`` is set before
        # any sub-method runs so none of them re-declare their own totals.
        if progress:
            progress.add_total(1)
            progress.locked = True

        result.merge(await self.sync_services(progress))

        if progress:
            service_count = len(await list_service_ids(self.environment))
            # 4 fan-out phases over every service, plus the 4 remaining
            # constant-time phases. Final total is 4n + 5.
            progress.add_total(4 * service_count + 4)

        for method in [
            self.sync_templates,
            self.sync_api_keys,
            self.sync_sms_senders,
            self.sync_users,
            self.sync_communication_items,
            self.sync_provider_details,
            self.sync_inbound_numbers,
            self.sync_service_callbacks,
        ]:
            sub_result = await method(progress)
            result.merge(sub_result)
        self.last_result = result
        return result

    async def sync_services(self, progress: ProgressCallback = None) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()
        await self._begin(progress, PHASE_SERVICES)
        try:
            services = await self.api.get_services()
            await upsert_services(services, self.environment)
            result.add_success()
        except Exception as exc:
            self._record_error(result, "services", exc)
        if progress:
            await progress.step(PHASE_SERVICES)
        self.last_result = result
        return result

    async def sync_templates(self, progress: ProgressCallback = None) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()
        service_ids = await list_service_ids(self.environment)
        if progress and not progress.locked:
            progress.add_total(len(service_ids))
        tasks = [self._sync_templates_for_service(sid, progress) for sid in service_ids]
        sub_results = await asyncio.gather(*tasks)
        for sub_result in sub_results:
            result.merge(sub_result)
        self.last_result = result
        return result

    async def _sync_templates_for_service(self, service_id: str, progress: ProgressCallback) -> SyncResult:
        result = SyncResult()
        async with self._semaphore:
            try:
                templates = await self.api.get_templates(service_id)
                await upsert_templates(templates, self.environment, service_id)
                result.add_success()
            except Exception as exc:
                self._record_error(result, "templates", exc, service_id=service_id)
            if progress:
                await progress.step(PHASE_TEMPLATES)
        return result

    async def sync_api_keys(
        self,
        progress: ProgressCallback = None,
        service_ids: list[str] | None = None,
        include_revoked: bool = False,
    ) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()
        if service_ids is None:
            service_ids = await list_service_ids(self.environment)
        if progress and not progress.locked:
            progress.add_total(len(service_ids))
        tasks = [self._sync_api_keys_for_service(sid, progress, include_revoked) for sid in service_ids]
        sub_results = await asyncio.gather(*tasks)
        for sub_result in sub_results:
            result.merge(sub_result)
        self.last_result = result
        return result

    async def _sync_api_keys_for_service(
        self, service_id: str, progress: ProgressCallback, include_revoked: bool = False
    ) -> SyncResult:
        result = SyncResult()
        async with self._semaphore:
            try:
                api_keys = await self.api.get_api_keys(service_id, include_revoked)
                remote_key_ids = [k["id"] for k in api_keys]
                await mark_stale_api_keys_revoked(remote_key_ids, self.environment, service_id)
                await upsert_api_keys(api_keys, self.environment, service_id)
                result.add_success()
            except Exception as exc:
                status_code = self._extract_status_code(exc)
                if status_code == 404 or "404" in str(exc) or "NOT FOUND" in str(exc):
                    # A 404 means the service has no API keys, not a failure.
                    result.add_success()
                else:
                    self._record_error(
                        result, "api_keys", exc, status_code=status_code, service_id=service_id
                    )
            if progress:
                await progress.step(PHASE_API_KEYS)
        return result

    async def sync_sms_senders(self, progress: ProgressCallback = None) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()
        service_ids = await list_service_ids(self.environment)
        if progress and not progress.locked:
            progress.add_total(len(service_ids))
        tasks = [self._sync_sms_senders_for_service(sid, progress) for sid in service_ids]
        sub_results = await asyncio.gather(*tasks)
        for sub_result in sub_results:
            result.merge(sub_result)
        self.last_result = result
        return result

    async def _sync_sms_senders_for_service(self, service_id: str, progress: ProgressCallback) -> SyncResult:
        result = SyncResult()
        async with self._semaphore:
            try:
                sms_senders = await self.api.get_sms_senders(service_id)
                await upsert_sms_senders(sms_senders, self.environment, service_id)
                result.add_success()
            except Exception as exc:
                status_code = self._extract_status_code(exc)
                if status_code == 404 or "404" in str(exc) or "NOT FOUND" in str(exc):
                    # A 404 means the service has no SMS senders, not a failure.
                    result.add_success()
                else:
                    self._record_error(
                        result, "sms_senders", exc, status_code=status_code, service_id=service_id
                    )
            if progress:
                await progress.step(PHASE_SMS_SENDERS)
        return result

    async def sync_users(self, progress: ProgressCallback = None) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()
        await self._begin(progress, PHASE_USERS)
        if self.encryption is None:
            self._record_error(result, "users", message="EncryptionManager is required for sync_users")
            if progress:
                await progress.step(PHASE_USERS)
            self.last_result = result
            return result
        try:
            await migrate_plaintext_users_to_encrypted(encryption=self.encryption, environment=self.environment)
            users = await self.api.get_users()
            await upsert_users(users, self.environment, encryption=self.encryption)
            result.add_success()
        except Exception as exc:
            self._record_error(result, "users", exc)
        if progress:
            await progress.step(PHASE_USERS)
        self.last_result = result
        return result

    async def sync_provider_details(self, progress: ProgressCallback = None) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()
        await self._begin(progress, PHASE_PROVIDER_DETAILS)
        try:
            provider_details = await self.api.get_provider_details()
            await upsert_provider_details(provider_details, self.environment)
            result.add_success()
        except Exception as exc:
            self._record_error(result, "provider_details", exc)
        if progress:
            await progress.step(PHASE_PROVIDER_DETAILS)
        self.last_result = result
        return result

    async def sync_communication_items(self, progress: ProgressCallback = None) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()
        await self._begin(progress, PHASE_COMMUNICATION_ITEMS)
        try:
            communication_items = await self.api.get_communication_items()
            await upsert_communication_items(communication_items, self.environment)
            result.add_success()
        except Exception as exc:
            self._record_error(result, "communication_items", exc)
        if progress:
            await progress.step(PHASE_COMMUNICATION_ITEMS)
        self.last_result = result
        return result

    async def sync_inbound_numbers(self, progress: ProgressCallback = None) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()
        await self._begin(progress, PHASE_INBOUND_NUMBERS)
        try:
            inbound_numbers = await self.api.get_inbound_numbers()
            await upsert_inbound_numbers(inbound_numbers, self.environment)
            result.add_success()
        except Exception as exc:
            self._record_error(result, "inbound_numbers", exc)
        if progress:
            await progress.step(PHASE_INBOUND_NUMBERS)
        self.last_result = result
        return result

    async def sync_service_callbacks(self, progress: ProgressCallback = None) -> SyncResult:
        progress = SyncProgress.coerce(progress)
        result = SyncResult()
        service_ids = await list_service_ids(self.environment)
        if progress and not progress.locked:
            progress.add_total(len(service_ids))
        tasks = [self._sync_service_callbacks_for_service(sid, progress) for sid in service_ids]
        sub_results = await asyncio.gather(*tasks)
        for sub_result in sub_results:
            result.merge(sub_result)
        self.last_result = result
        return result

    async def _sync_service_callbacks_for_service(self, service_id: str, progress: ProgressCallback) -> SyncResult:
        result = SyncResult()
        async with self._semaphore:
            try:
                callbacks = await self.api.get_service_callbacks(service_id)
                await upsert_service_callbacks(callbacks, self.environment, service_id)
                # Callbacks deleted out-of-band must not linger in the cache. Only prune on
                # the success path: a 404 means the service is missing or inaccessible, not
                # that it has zero callbacks.
                await prune_service_callbacks(
                    service_id,
                    self.environment,
                    [c["id"] for c in callbacks if c.get("id")],
                )
                result.add_success()
            except Exception as exc:
                status_code = self._extract_status_code(exc)
                if status_code == 404 or "404" in str(exc) or "NOT FOUND" in str(exc):
                    # A 404 means the service is missing or inaccessible.
                    result.add_success()
                else:
                    self._record_error(
                        result, "service_callbacks", exc, status_code=status_code, service_id=service_id
                    )
            if progress:
                await progress.step(PHASE_CALLBACKS)
        return result
