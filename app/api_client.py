from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx
import jwt


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

    async def get_sms_senders(self, service_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_users(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_provider_details(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_communication_items(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def send_notification(
        self,
        template_id: str,
        recipient: str,
        personalisation: Dict[str, Any],
        api_key: str,
        service_id: str,
        template_type: str,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def healthcheck(self) -> bool:
        raise NotImplementedError


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

    async def get_services(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(f"{self.base_url}/service", auth=self._basic_auth)
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def get_templates(self, service_id: str) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/service/{service_id}/template", auth=self._basic_auth
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def get_api_keys(self, service_id: str) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/service/{service_id}/api-keys", auth=self._basic_auth
        )
        resp.raise_for_status()
        return resp.json().get("apiKeys", [])

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

    async def get_sms_senders(self, service_id: str) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/service/{service_id}/sms-sender", auth=self._basic_auth
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return payload.get("sms_senders") or payload.get("data") or []

    async def get_users(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(f"{self.base_url}/user", auth=self._basic_auth)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return payload.get("data") or []

    async def get_provider_details(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/provider-details", auth=self._basic_auth
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return payload.get("provider_details") or payload.get("data") or []

    async def get_communication_items(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(
            f"{self.base_url}/communication-item", auth=self._basic_auth
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return payload.get("data") or []

    async def send_notification(
        self,
        template_id: str,
        recipient: str,
        personalisation: Dict[str, Any],
        api_key: str,
        service_id: str,
        template_type: str,
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

        resp = await self.client.post(
            f"{self.base_url}{endpoint}", json=payload, headers=headers
        )
        resp.raise_for_status()
        return resp.json()

    async def healthcheck(self) -> bool:
        try:
            resp = await self.client.get(
                f"{self.base_url}/_status", auth=self._basic_auth
            )
            resp.raise_for_status()
            return True
        except Exception:
            return False

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

    async def get_sms_senders(self, service_id: str) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "id": "sms-1",
                "service_id": service_id,
                "sms_sender": "+15551234567",
                "is_default": True,
                "archived": False,
            }
        ]

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

    async def send_notification(
        self,
        template_id: str,
        recipient: str,
        personalisation: Dict[str, Any],
        api_key: str,
        service_id: str,
        template_type: str,
    ) -> Dict[str, Any]:
        await asyncio.sleep(self._sleep)
        return {
            "id": "mock-notification-123",
            "template_id": template_id,
            "recipient": recipient,
            "personalisation": personalisation,
            "service_id": service_id,
            "type": template_type,
            "status": "sent",
        }

    async def healthcheck(self) -> bool:
        await asyncio.sleep(self._sleep)
        return True
