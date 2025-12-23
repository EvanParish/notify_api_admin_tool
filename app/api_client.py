from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx
import jwt


class NotificationAPI:
    async def get_services(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_users(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_templates(self, service_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_api_keys(self, service_id: str) -> List[Dict[str, Any]]:
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

    async def get_users(self) -> List[Dict[str, Any]]:
        resp = await self.client.get(f"{self.base_url}/user", auth=self._basic_auth)
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
        return resp.json().get("data", [])

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

        resp = await self.client.post(f"{self.base_url}{endpoint}", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def healthcheck(self) -> bool:
        try:
            resp = await self.client.get(f"{self.base_url}/_status", auth=self._basic_auth)
            resp.raise_for_status()
            return True
        except Exception:
            return False

    def _make_jwt(self, service_id: str, secret: str) -> str:
        now = int(time.time())
        payload = {"iss": service_id, "iat": now}
        token = jwt.encode(payload, secret, algorithm="HS256", headers={"typ": "JWT", "alg": "HS256"})
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

    async def get_users(self) -> List[Dict[str, Any]]:
        await asyncio.sleep(self._sleep)
        return [
            {
                "id": "user-1",
                "name": "Demo User",
                "email_address": "demo@example.com",
                "auth_type": "basic",
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
