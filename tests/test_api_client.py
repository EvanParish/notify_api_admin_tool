import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from app.api_client import NotificationAPI, HttpNotificationAPI, MockNotificationAPI


@pytest.mark.asyncio
async def test_mock_api_get_services():
    api = MockNotificationAPI()
    services = await api.get_services()

    assert len(services) == 1
    assert services[0]["id"] == "svc-1"
    assert services[0]["name"] == "Test Service"
    assert services[0]["active"] is True


@pytest.mark.asyncio
async def test_mock_api_get_templates():
    api = MockNotificationAPI()
    templates = await api.get_templates("svc-1")

    assert len(templates) == 2
    email_template = next(t for t in templates if t["type"] == "email")
    sms_template = next(t for t in templates if t["type"] == "sms")

    assert email_template["id"] == "tmpl-email-1"
    assert email_template["name"] == "Welcome Email"
    assert email_template["subject"] == "Welcome, ((first_name))!"

    assert sms_template["id"] == "tmpl-sms-1"
    assert sms_template["name"] == "Alert SMS"


@pytest.mark.asyncio
async def test_mock_api_get_api_keys():
    api = MockNotificationAPI()
    keys = await api.get_api_keys("svc-1")

    assert len(keys) == 1
    assert keys[0]["id"] == "key-1"
    assert keys[0]["name"] == "Demo Key"


@pytest.mark.asyncio
async def test_mock_api_create_api_key():
    api = MockNotificationAPI()
    result = await api.create_api_key("svc-1", "New Key", "normal")

    assert result["data"] == "secret_api_key_1234567890abcdef"


@pytest.mark.asyncio
async def test_mock_api_update_api_key_expiry():
    api = MockNotificationAPI()
    result = await api.update_api_key_expiry("svc-1", "key-1", "2026-04-08")

    assert result["id"] == "key-1"
    assert result["expiry_date"] == "2026-04-08"


@pytest.mark.asyncio
async def test_mock_api_revoke_api_key():
    api = MockNotificationAPI()
    result = await api.revoke_api_key("svc-1", "key-1")

    assert result["id"] == "key-1"
    assert result["revoked"] is True


@pytest.mark.asyncio
async def test_mock_api_send_notification():
    api = MockNotificationAPI()
    result = await api.send_notification(
        template_id="tmpl-1",
        recipient="user@example.com",
        personalisation={"name": "John"},
        api_key="test-key",
        service_id="svc-1",
        template_type="email",
    )

    assert result["id"] == "mock-notification-123"
    assert result["template_id"] == "tmpl-1"
    assert result["recipient"] == "user@example.com"
    assert result["personalisation"] == {"name": "John"}
    assert result["status"] == "sent"


@pytest.mark.asyncio
async def test_mock_api_healthcheck():
    api = MockNotificationAPI()
    result = await api.healthcheck()
    assert result is True


def test_http_api_initialization():
    api = HttpNotificationAPI(
        base_url="https://api.example.com/",
        basic_username="user",
        basic_password="pass",
        timeout=15.0,
    )

    assert api.base_url == "https://api.example.com"
    assert api._basic_auth is not None
    assert api.client.timeout.read == 15.0


def test_http_api_initialization_no_auth():
    api = HttpNotificationAPI(base_url="https://api.example.com")
    assert api._basic_auth is None


@pytest.mark.asyncio
async def test_http_api_get_services():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "svc-1", "name": "Service"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response) as mock_get:
        services = await api.get_services()

        mock_get.assert_called_once_with("https://api.example.com/service", auth=None)
        assert len(services) == 1
        assert services[0]["id"] == "svc-1"


@pytest.mark.asyncio
async def test_http_api_get_templates():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "tmpl-1", "name": "Template"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response) as mock_get:
        templates = await api.get_templates("svc-1")

        mock_get.assert_called_once_with(
            "https://api.example.com/service/svc-1/template", auth=None
        )
        assert len(templates) == 1


@pytest.mark.asyncio
async def test_http_api_get_api_keys():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"apiKeys": [{"id": "key-1"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response) as mock_get:
        keys = await api.get_api_keys("svc-1")

        mock_get.assert_called_once_with(
            "https://api.example.com/service/svc-1/api-keys", auth=None
        )
        assert len(keys) == 1


@pytest.mark.asyncio
async def test_http_api_create_api_key():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": "secret_api_key_1234567890abcdef"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.create_api_key("svc-1", "New Key", "normal")

        mock_post.assert_called_once_with(
            "https://api.example.com/service/svc-1/api-key",
            json={"name": "New Key", "key_type": "normal"},
            auth=None,
        )
        assert result["data"] == "secret_api_key_1234567890abcdef"


@pytest.mark.asyncio
async def test_http_api_update_api_key_expiry():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"id": "key-1"}}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.update_api_key_expiry("svc-1", "key-1", "2026-04-08")

        mock_post.assert_called_once_with(
            "https://api.example.com/service/svc-1/api-key/key-1",
            json={"expiry_date": "2026-04-08"},
            auth=None,
        )
        assert result["data"]["id"] == "key-1"


@pytest.mark.asyncio
async def test_http_api_revoke_api_key():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"id": "key-1"}}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.revoke_api_key("svc-1", "key-1")

        mock_post.assert_called_once_with(
            "https://api.example.com/service/svc-1/api-key/revoke/key-1",
            auth=None,
        )
        assert result["data"]["id"] == "key-1"


@pytest.mark.asyncio
async def test_http_api_send_notification_email():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "notif-1"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.send_notification(
            template_id="tmpl-1",
            recipient="user@example.com",
            personalisation={"name": "John"},
            api_key="secret-key",
            service_id="svc-1",
            template_type="email",
        )

        assert result["id"] == "notif-1"
        assert mock_post.called
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://api.example.com/v2/notifications/email"
        payload = call_args[1]["json"]
        assert payload["template_id"] == "tmpl-1"
        assert payload["email_address"] == "user@example.com"
        assert payload["personalisation"] == {"name": "John"}


@pytest.mark.asyncio
async def test_http_api_send_notification_sms():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "notif-2"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.send_notification(
            template_id="tmpl-2",
            recipient="1234567890",
            personalisation={"code": "1234"},
            api_key="secret-key",
            service_id="svc-1",
            template_type="sms",
        )

        assert result["id"] == "notif-2"
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://api.example.com/v2/notifications/sms"
        payload = call_args[1]["json"]
        assert payload["phone_number"] == "1234567890"


@pytest.mark.asyncio
async def test_http_api_healthcheck_success():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.healthcheck()
        assert result is True


@pytest.mark.asyncio
async def test_http_api_healthcheck_failure():
    api = HttpNotificationAPI("https://api.example.com")

    with patch.object(api.client, "get", side_effect=Exception("Connection failed")):
        result = await api.healthcheck()
        assert result is False


def test_http_api_make_jwt():
    api = HttpNotificationAPI("https://api.example.com")

    service_id = "test-service-123"
    secret = "test-secret-key"

    token = api._make_jwt(service_id, secret)

    assert isinstance(token, str)
    assert len(token) > 0

    # Decode and verify
    import jwt

    decoded = jwt.decode(token, secret, algorithms=["HS256"])
    assert decoded["iss"] == service_id
    assert "iat" in decoded


def test_http_api_make_jwt_timing():
    api = HttpNotificationAPI("https://api.example.com")

    before = int(time.time())
    token = api._make_jwt("svc-1", "secret")
    after = int(time.time())

    import jwt

    decoded = jwt.decode(token, "secret", algorithms=["HS256"])

    assert before <= decoded["iat"] <= after


@pytest.mark.asyncio
async def test_http_api_with_basic_auth():
    api = HttpNotificationAPI(
        base_url="https://api.example.com",
        basic_username="admin",
        basic_password="pass123",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": []}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response) as mock_get:
        await api.get_services()

        call_args = mock_get.call_args
        assert call_args[1]["auth"] is not None
        assert isinstance(call_args[1]["auth"], httpx.BasicAuth)


# --- Base class NotImplementedError tests ---


@pytest.mark.asyncio
async def test_base_api_get_services_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.get_services()


@pytest.mark.asyncio
async def test_base_api_get_templates_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.get_templates("svc-1")


@pytest.mark.asyncio
async def test_base_api_get_api_keys_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.get_api_keys("svc-1")


@pytest.mark.asyncio
async def test_base_api_create_api_key_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.create_api_key("svc-1", "name", "normal")


@pytest.mark.asyncio
async def test_base_api_update_api_key_expiry_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.update_api_key_expiry("svc-1", "key-1", "2026-01-01")


@pytest.mark.asyncio
async def test_base_api_revoke_api_key_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.revoke_api_key("svc-1", "key-1")


@pytest.mark.asyncio
async def test_base_api_get_sms_senders_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.get_sms_senders("svc-1")


@pytest.mark.asyncio
async def test_base_api_get_users_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.get_users()


@pytest.mark.asyncio
async def test_base_api_get_provider_details_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.get_provider_details()


@pytest.mark.asyncio
async def test_base_api_get_communication_items_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.get_communication_items()


@pytest.mark.asyncio
async def test_base_api_send_notification_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.send_notification("t", "r", {}, "k", "s", "email")


@pytest.mark.asyncio
async def test_base_api_healthcheck_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.healthcheck()


@pytest.mark.asyncio
async def test_base_api_aclose_noop():
    api = NotificationAPI()
    await api.aclose()  # Should not raise


# --- create_api_key with secret_type ---


@pytest.mark.asyncio
async def test_http_api_create_api_key_with_secret_type():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": "secret_key"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.create_api_key("svc-1", "Key", "normal", secret_type="app")

        mock_post.assert_called_once_with(
            "https://api.example.com/service/svc-1/api-key",
            json={"name": "Key", "key_type": "normal", "secret_type": "app"},
            auth=None,
        )
        assert result["data"] == "secret_key"


# --- get_sms_senders response formats ---


@pytest.mark.asyncio
async def test_http_api_get_sms_senders_direct_list():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = [{"id": "s1"}]
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_sms_senders("svc-1")
        assert result == [{"id": "s1"}]


@pytest.mark.asyncio
async def test_http_api_get_sms_senders_sms_senders_key():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"sms_senders": [{"id": "s2"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_sms_senders("svc-1")
        assert result == [{"id": "s2"}]


@pytest.mark.asyncio
async def test_http_api_get_sms_senders_data_key():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "s3"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_sms_senders("svc-1")
        assert result == [{"id": "s3"}]


# --- get_users response formats ---


@pytest.mark.asyncio
async def test_http_api_get_users_direct_list():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = [{"id": "u1"}]
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_users()
        assert result == [{"id": "u1"}]


@pytest.mark.asyncio
async def test_http_api_get_users_data_key():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "u2"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_users()
        assert result == [{"id": "u2"}]


# --- get_provider_details response formats ---


@pytest.mark.asyncio
async def test_http_api_get_provider_details_direct_list():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = [{"id": "p1"}]
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_provider_details()
        assert result == [{"id": "p1"}]


@pytest.mark.asyncio
async def test_http_api_get_provider_details_provider_details_key():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"provider_details": [{"id": "p2"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_provider_details()
        assert result == [{"id": "p2"}]


@pytest.mark.asyncio
async def test_http_api_get_provider_details_data_key():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "p3"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_provider_details()
        assert result == [{"id": "p3"}]


# --- get_communication_items response formats ---


@pytest.mark.asyncio
async def test_http_api_get_communication_items_direct_list():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = [{"id": "c1"}]
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_communication_items()
        assert result == [{"id": "c1"}]


@pytest.mark.asyncio
async def test_http_api_get_communication_items_data_key():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "c2"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_communication_items()
        assert result == [{"id": "c2"}]


# --- aclose ---


@pytest.mark.asyncio
async def test_http_api_aclose():
    api = HttpNotificationAPI("https://api.example.com")

    with patch.object(api.client, "aclose", new_callable=AsyncMock) as mock_aclose:
        await api.aclose()
        mock_aclose.assert_called_once()
