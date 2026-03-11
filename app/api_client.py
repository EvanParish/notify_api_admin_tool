from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
import jwt
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

# Retry decorator for transient network errors
http_retry = retry(
    retry=retry_if_exception_type(
        (
            httpx.ReadError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
        )
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class NotificationAPI:
    async def get_services(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_templates(self, service_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_api_keys(self, service_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def create_api_key(
        self,
        service_id: str,
        name: str,
        key_type: str,
        secret_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def update_api_key_expiry(
        self, service_id: str, key_id: str, expiry_date: str
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def revoke_api_key(self, service_id: str, key_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    async def get_sms_senders(self, service_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def create_sms_sender(
        self,
        service_id: str,
        sms_sender: str,
        description: str,
        provider_id: str,
        is_default: bool = False,
        inbound_number_id: str | None = None,
        rate_limit: int | None = None,
        rate_limit_interval: int | None = None,
        sms_sender_specifics: dict | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def update_sms_sender(
        self,
        service_id: str,
        sms_sender_id: str,
        sms_sender: str | None = None,
        description: str | None = None,
        provider_id: str | None = None,
        is_default: bool | None = None,
        inbound_number_id: str | None = None,
        rate_limit: int | None = None,
        rate_limit_interval: int | None = None,
        sms_sender_specifics: dict | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def get_users(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_provider_details(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def update_provider_detail(
        self,
        provider_id: str,
        priority: int | None = None,
        active: bool | None = None,
        load_balancing_weight: int | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def get_communication_items(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def update_communication_item(
        self,
        item_id: str,
        name: str | None = None,
        default_send_indicator: bool | None = None,
        va_profile_item_id: int | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def get_inbound_numbers(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def send_notification(
        self,
        template_id: str,
        recipient: str,
        personalisation: Dict[str, Any],
        api_key: str,
        service_id: str,
        template_type: str,
        sms_sender_id: str | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def healthcheck(self) -> bool:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


class HttpNotificationAPI(NotificationAPI):
    def __init__(
        self,
        base_url: str,
        basic_username: Optional[str] = None,
        basic_password: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout)
        self._basic_auth: Optional[httpx.Auth] = None
        if basic_username and basic_password:
            self._basic_auth = httpx.BasicAuth(basic_username, basic_password)

    @http_retry
    async def get_services(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(f"{self.base_url}/service", auth=self._basic_auth)
        resp.raise_for_status()
        return resp.json().get("data", [])

    @http_retry
    async def get_templates(self, service_id: str) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/service/{service_id}/template", auth=self._basic_auth
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    @http_retry
    async def get_api_keys(self, service_id: str) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/service/{service_id}/api-keys", auth=self._basic_auth
        )
        resp.raise_for_status()
        return resp.json().get("apiKeys", [])

    @http_retry
    async def create_api_key(
        self,
        service_id: str,
        name: str,
        key_type: str,
        secret_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"name": name, "key_type": key_type}
        if secret_type:
            payload["secret_type"] = secret_type
        resp = await self.client.post(
            f"{self.base_url}/service/{service_id}/api-key",
            json=payload,
            auth=self._basic_auth,
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry
    async def update_api_key_expiry(
        self, service_id: str, key_id: str, expiry_date: str
    ) -> Dict[str, Any]:
        payload = {"expiry_date": expiry_date}
        resp = await self.client.post(
            f"{self.base_url}/service/{service_id}/api-key/{key_id}",
            json=payload,
            auth=self._basic_auth,
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry
    async def revoke_api_key(self, service_id: str, key_id: str) -> Dict[str, Any]:
        resp = await self.client.post(
            f"{self.base_url}/service/{service_id}/api-key/revoke/{key_id}",
            auth=self._basic_auth,
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry
    async def get_sms_senders(self, service_id: str) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/service/{service_id}/sms-sender", auth=self._basic_auth
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return payload.get("sms_senders") or payload.get("data") or []

    @http_retry
    async def create_sms_sender(
        self,
        service_id: str,
        sms_sender: str,
        description: str,
        provider_id: str,
        is_default: bool = False,
        inbound_number_id: str | None = None,
        rate_limit: int | None = None,
        rate_limit_interval: int | None = None,
        sms_sender_specifics: dict | None = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "sms_sender": sms_sender,
            "description": description,
            "provider_id": provider_id,
            "is_default": is_default,
        }
        if inbound_number_id is not None:
            payload["inbound_number_id"] = inbound_number_id
        if rate_limit is not None:
            payload["rate_limit"] = rate_limit
        if rate_limit_interval is not None:
            payload["rate_limit_interval"] = rate_limit_interval
        if sms_sender_specifics is not None:
            payload["sms_sender_specifics"] = sms_sender_specifics
        resp = await self.client.post(
            f"{self.base_url}/service/{service_id}/sms-sender",
            json=payload,
            auth=self._basic_auth,
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry
    async def update_sms_sender(
        self,
        service_id: str,
        sms_sender_id: str,
        sms_sender: str | None = None,
        description: str | None = None,
        provider_id: str | None = None,
        is_default: bool | None = None,
        inbound_number_id: str | None = None,
        rate_limit: int | None = None,
        rate_limit_interval: int | None = None,
        sms_sender_specifics: dict | None = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if sms_sender is not None:
            payload["sms_sender"] = sms_sender
        if description is not None:
            payload["description"] = description
        if provider_id is not None:
            payload["provider_id"] = provider_id
        if is_default is not None:
            payload["is_default"] = is_default
        if inbound_number_id is not None:
            payload["inbound_number_id"] = inbound_number_id
        if rate_limit is not None:
            payload["rate_limit"] = rate_limit
        if rate_limit_interval is not None:
            payload["rate_limit_interval"] = rate_limit_interval
        if sms_sender_specifics is not None:
            payload["sms_sender_specifics"] = sms_sender_specifics
        resp = await self.client.post(
            f"{self.base_url}/service/{service_id}/sms-sender/{sms_sender_id}",
            json=payload,
            auth=self._basic_auth,
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry
    async def get_users(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(f"{self.base_url}/user", auth=self._basic_auth)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return payload.get("data") or []

    @http_retry
    async def get_provider_details(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/provider-details", auth=self._basic_auth
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return payload.get("provider_details") or payload.get("data") or []

    @http_retry
    async def update_provider_detail(
        self,
        provider_id: str,
        priority: int | None = None,
        active: bool | None = None,
        load_balancing_weight: int | None = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if priority is not None:
            payload["priority"] = priority
        if active is not None:
            payload["active"] = active
        if load_balancing_weight is not None:
            payload["load_balancing_weight"] = load_balancing_weight
        resp = await self.client.post(
            f"{self.base_url}/provider-details/{provider_id}",
            json=payload,
            auth=self._basic_auth,
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry
    async def get_communication_items(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/communication-item", auth=self._basic_auth
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return payload.get("data") or []

    @http_retry
    async def update_communication_item(
        self,
        item_id: str,
        name: str | None = None,
        default_send_indicator: bool | None = None,
        va_profile_item_id: int | None = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if default_send_indicator is not None:
            payload["default_send_indicator"] = default_send_indicator
        if va_profile_item_id is not None:
            payload["va_profile_item_id"] = va_profile_item_id
        resp = await self.client.patch(
            f"{self.base_url}/communication-item/{item_id}",
            json=payload,
            auth=self._basic_auth,
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry
    async def get_inbound_numbers(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/inbound-number", auth=self._basic_auth
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return payload.get("data") or []

    @http_retry
    async def send_notification(
        self,
        template_id: str,
        recipient: str,
        personalisation: Dict[str, Any],
        api_key: str,
        service_id: str,
        template_type: str,
        sms_sender_id: str | None = None,
    ) -> Dict[str, Any]:
        token = self._make_jwt(service_id, api_key)
        headers = {"Authorization": f"Bearer {token}"}
        payload: Dict[str, Any] = {
            "template_id": template_id,
            "personalisation": personalisation,
        }
        if template_type == "email":
            payload["email_address"] = recipient
            endpoint = "/v2/notifications/email"
        else:
            payload["phone_number"] = recipient
            endpoint = "/v2/notifications/sms"
            if sms_sender_id:
                payload["sms_sender_id"] = sms_sender_id

        resp = await self.client.post(
            f"{self.base_url}{endpoint}", json=payload, headers=headers
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry
    async def healthcheck(self) -> bool:
        try:
            resp = await self.client.get(
                f"{self.base_url}/_status", auth=self._basic_auth
            )
            resp.raise_for_status()
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        await self.client.aclose()

    def _make_jwt(self, service_id: str, secret: str) -> str:
        now = int(time.time())
        payload = {"iss": service_id, "iat": now}
        token = jwt.encode(
            payload, secret, algorithm="HS256", headers={"typ": "JWT", "alg": "HS256"}
        )
        return token


class MockNotificationAPI(NotificationAPI):
    def __init__(self) -> None:
        self._sleep = 0.1

    async def get_services(self) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "id": "svc-1",
                "name": "Test Service",
                "active": True,
                "restricted": False,
                "limit": 1000,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
            }
        ]

    async def get_templates(self, service_id: str) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "id": "tmpl-email-1",
                "service": service_id,
                "name": "Welcome Email",
                "type": "email",
                "content": "Hello ((first_name)), welcome to our service.",
                "subject": "Welcome, ((first_name))!",
                "version": 1,
            },
            {
                "id": "tmpl-sms-1",
                "service": service_id,
                "name": "Alert SMS",
                "type": "sms",
                "content": "Hi ((name)), your code is ((code)).",
                "subject": None,
                "version": 1,
            },
        ]

    async def get_api_keys(self, service_id: str) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "id": "key-1",
                "name": "Demo Key",
                "expiry_date": None,
                "created_by": "user-1",
            }
        ]

    async def create_api_key(
        self,
        service_id: str,
        name: str,
        key_type: str,
        secret_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        await asyncio.sleep(self._sleep)
        return {"data": "secret_api_key_1234567890abcdef"}

    async def update_api_key_expiry(
        self, service_id: str, key_id: str, expiry_date: str
    ) -> Dict[str, Any]:
        await asyncio.sleep(self._sleep)
        return {
            "id": key_id,
            "service_id": service_id,
            "expiry_date": expiry_date,
        }

    async def revoke_api_key(self, service_id: str, key_id: str) -> Dict[str, Any]:
        await asyncio.sleep(self._sleep)
        return {"id": key_id, "service_id": service_id, "revoked": True}

    async def get_sms_senders(self, service_id: str) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "id": "sms-1",
                "service_id": service_id,
                "sms_sender": "+15551234567",
                "is_default": True,
                "archived": False,
                "description": "Default SMS sender",
                "provider_id": "provider-1",
                "provider_name": "Pinpoint",
            }
        ]

    async def create_sms_sender(
        self,
        service_id: str,
        sms_sender: str,
        description: str,
        provider_id: str,
        is_default: bool = False,
        inbound_number_id: str | None = None,
        rate_limit: int | None = None,
        rate_limit_interval: int | None = None,
        sms_sender_specifics: dict | None = None,
    ) -> Dict[str, Any]:
        await asyncio.sleep(self._sleep)
        return {
            "id": "sms-new-123",
            "service_id": service_id,
            "sms_sender": sms_sender,
            "description": description,
            "provider_id": provider_id,
            "is_default": is_default,
            "archived": False,
            "inbound_number_id": inbound_number_id,
            "rate_limit": rate_limit,
            "rate_limit_interval": rate_limit_interval,
            "sms_sender_specifics": sms_sender_specifics,
        }

    async def update_sms_sender(
        self,
        service_id: str,
        sms_sender_id: str,
        sms_sender: str | None = None,
        description: str | None = None,
        provider_id: str | None = None,
        is_default: bool | None = None,
        inbound_number_id: str | None = None,
        rate_limit: int | None = None,
        rate_limit_interval: int | None = None,
        sms_sender_specifics: dict | None = None,
    ) -> Dict[str, Any]:
        await asyncio.sleep(self._sleep)
        return {
            "id": sms_sender_id,
            "service_id": service_id,
            "sms_sender": sms_sender or "+15551234567",
            "description": description,
            "provider_id": provider_id,
            "is_default": is_default,
            "archived": False,
        }

    async def get_users(self) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "additional_information": {},
                "auth_type": "email_auth",
                "blocked": False,
                "current_session_id": None,
                "email_address": "demo.user@example.com",
                "failed_login_count": 0,
                "id": "user-1",
                "identity_provider_user_id": None,
                "logged_in_at": None,
                "mobile_number": None,
                "name": "Demo User",
                "organisations": [],
                "password_changed_at": None,
                "permissions": {},
                "platform_admin": False,
                "services": [],
                "state": "active",
            }
        ]

    async def get_provider_details(self) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "active": False,
                "created_by_name": "Filip Fafara",
                "current_month_billable_sms": 0,
                "display_name": "Govdelivery",
                "id": "5fa26210-93a5-4f10-bf6a-00f2a14a9b4b",
                "identifier": "govdelivery",
                "load_balancing_weight": 0,
                "notification_type": "email",
                "priority": 5,
                "supports_international": False,
                "updated_at": "Mon, 16 Aug 2021 19:38:10 GMT",
            },
            {
                "active": True,
                "created_by_name": "Filip Fafara",
                "current_month_billable_sms": 0,
                "display_name": "AWS SES",
                "id": "4b7d3f9a-ab42-4795-b2ce-28e7c2e2d3f7",
                "identifier": "ses",
                "load_balancing_weight": 100,
                "notification_type": "email",
                "priority": 10,
                "supports_international": False,
                "updated_at": "Mon, 16 Aug 2021 19:39:12 GMT",
            },
        ]

    async def update_provider_detail(
        self,
        provider_id: str,
        priority: int | None = None,
        active: bool | None = None,
        load_balancing_weight: int | None = None,
    ) -> Dict[str, Any]:
        await asyncio.sleep(self._sleep)
        return {
            "id": provider_id,
            "priority": priority,
            "active": active,
            "load_balancing_weight": load_balancing_weight,
        }

    async def get_communication_items(self) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "default_send_indicator": True,
                "id": "00dfc28c-e229-4cc3-b691-8ffadaba1c72",
                "name": "Board of Veterans' Appeals hearing reminder",
                "va_profile_item_id": 1,
            },
            {
                "default_send_indicator": True,
                "id": "8bc5e318-b316-49aa-8cb1-68e48fc3b086",
                "name": "COVID-19 Updates",
                "va_profile_item_id": 2,
            },
        ]

    async def update_communication_item(
        self,
        item_id: str,
        name: str | None = None,
        default_send_indicator: bool | None = None,
        va_profile_item_id: int | None = None,
    ) -> Dict[str, Any]:
        await asyncio.sleep(self._sleep)
        return {
            "id": item_id,
            "name": name,
            "default_send_indicator": default_send_indicator,
            "va_profile_item_id": va_profile_item_id,
        }

    async def get_inbound_numbers(self) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "id": "inbound-1",
                "number": "+18337021549",
                "provider": "pinpoint",
                "active": True,
                "self_managed": False,
                "service": {
                    "id": "svc-1",
                    "name": "Test Service",
                },
                "auth_parameter": None,
                "url_endpoint": None,
            },
            {
                "id": "inbound-2",
                "number": "+16506288615",
                "provider": "pinpoint",
                "active": True,
                "self_managed": False,
                "service": None,
                "auth_parameter": "/dev/test/parameter",
                "url_endpoint": "https://staging.api.vetext.va.gov/api/vetext/pub/inbound-message/aws",
            },
        ]

    async def send_notification(
        self,
        template_id: str,
        recipient: str,
        personalisation: Dict[str, Any],
        api_key: str,
        service_id: str,
        template_type: str,
        sms_sender_id: str | None = None,
    ) -> Dict[str, Any]:
        await asyncio.sleep(self._sleep)
        result: Dict[str, Any] = {
            "id": "mock-notification-123",
            "template_id": template_id,
            "recipient": recipient,
            "personalisation": personalisation,
            "service_id": service_id,
            "type": template_type,
            "status": "sent",
        }
        if sms_sender_id:
            result["sms_sender_id"] = sms_sender_id
        return result

    async def healthcheck(self) -> bool:
        await asyncio.sleep(self._sleep)
        return True
