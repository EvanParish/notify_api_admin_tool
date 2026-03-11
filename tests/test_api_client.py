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
async def test_mock_api_update_provider_detail():
    api = MockNotificationAPI()
    result = await api.update_provider_detail(
        provider_id="prov-1",
        priority=10,
        active=True,
        load_balancing_weight=50,
    )

    assert result["id"] == "prov-1"
    assert result["priority"] == 10
    assert result["active"] is True
    assert result["load_balancing_weight"] == 50


@pytest.mark.asyncio
async def test_mock_api_update_communication_item():
    api = MockNotificationAPI()
    result = await api.update_communication_item(
        item_id="comm-1",
        name="Test Item",
        default_send_indicator=True,
        va_profile_item_id=5,
    )

    assert result["id"] == "comm-1"
    assert result["name"] == "Test Item"
    assert result["default_send_indicator"] is True
    assert result["va_profile_item_id"] == 5


@pytest.mark.asyncio
async def test_mock_api_revoke_api_key():
    api = MockNotificationAPI()
    result = await api.revoke_api_key("svc-1", "key-1")

    assert result["id"] == "key-1"
    assert result["revoked"] is True


@pytest.mark.asyncio
async def test_mock_api_get_sms_senders():
    api = MockNotificationAPI()
    result = await api.get_sms_senders("svc-1")

    assert len(result) == 1
    assert result[0]["id"] == "sms-1"
    assert result[0]["sms_sender"] == "+15551234567"
    assert result[0]["is_default"] is True


@pytest.mark.asyncio
async def test_mock_api_create_sms_sender():
    api = MockNotificationAPI()
    result = await api.create_sms_sender(
        service_id="svc-1",
        sms_sender="+15559876543",
        description="New SMS sender",
        provider_id="provider-1",
        is_default=False,
        rate_limit=100,
    )

    assert result["id"] == "sms-new-123"
    assert result["sms_sender"] == "+15559876543"
    assert result["description"] == "New SMS sender"
    assert result["provider_id"] == "provider-1"
    assert result["is_default"] is False
    assert result["rate_limit"] == 100


@pytest.mark.asyncio
async def test_mock_api_update_sms_sender():
    api = MockNotificationAPI()
    result = await api.update_sms_sender(
        service_id="svc-1",
        sms_sender_id="sms-1",
        sms_sender="+15551111111",
        description="Updated description",
        is_default=True,
    )

    assert result["id"] == "sms-1"
    assert result["sms_sender"] == "+15551111111"
    assert result["description"] == "Updated description"
    assert result["is_default"] is True


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
    assert "sms_sender_id" not in result


@pytest.mark.asyncio
async def test_mock_api_send_notification_with_sms_sender():
    api = MockNotificationAPI()
    result = await api.send_notification(
        template_id="tmpl-2",
        recipient="1234567890",
        personalisation={"code": "ABC"},
        api_key="test-key",
        service_id="svc-1",
        template_type="sms",
        sms_sender_id="sender-456",
    )

    assert result["id"] == "mock-notification-123"
    assert result["template_id"] == "tmpl-2"
    assert result["recipient"] == "1234567890"
    assert result["sms_sender_id"] == "sender-456"
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
async def test_http_api_update_provider_detail():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "prov-1"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.update_provider_detail(
            "prov-1",
            priority=10,
            active=True,
            load_balancing_weight=50,
        )

        mock_post.assert_called_once_with(
            "https://api.example.com/provider-details/prov-1",
            json={"priority": 10, "active": True, "load_balancing_weight": 50},
            auth=None,
        )
        assert result["id"] == "prov-1"


@pytest.mark.asyncio
async def test_http_api_update_provider_detail_partial():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "prov-1"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.update_provider_detail("prov-1", priority=15)

        mock_post.assert_called_once_with(
            "https://api.example.com/provider-details/prov-1",
            json={"priority": 15},
            auth=None,
        )
        assert result["id"] == "prov-1"


@pytest.mark.asyncio
async def test_http_api_update_communication_item():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "comm-1"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "patch", return_value=mock_response) as mock_patch:
        result = await api.update_communication_item(
            "comm-1",
            name="Test Item",
            default_send_indicator=True,
            va_profile_item_id=5,
        )

        mock_patch.assert_called_once_with(
            "https://api.example.com/communication-item/comm-1",
            json={
                "name": "Test Item",
                "default_send_indicator": True,
                "va_profile_item_id": 5,
            },
            auth=None,
        )
        assert result["id"] == "comm-1"


@pytest.mark.asyncio
async def test_http_api_update_communication_item_partial():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "comm-1"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "patch", return_value=mock_response) as mock_patch:
        result = await api.update_communication_item("comm-1", name="Updated Name")

        mock_patch.assert_called_once_with(
            "https://api.example.com/communication-item/comm-1",
            json={"name": "Updated Name"},
            auth=None,
        )
        assert result["id"] == "comm-1"


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
        assert "sms_sender_id" not in payload


@pytest.mark.asyncio
async def test_http_api_send_notification_sms_with_sender():
    api = HttpNotificationAPI("https://api.example.com")

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "notif-3"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.send_notification(
            template_id="tmpl-2",
            recipient="1234567890",
            personalisation={"code": "1234"},
            api_key="secret-key",
            service_id="svc-1",
            template_type="sms",
            sms_sender_id="sender-123",
        )

        assert result["id"] == "notif-3"
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://api.example.com/v2/notifications/sms"
        payload = call_args[1]["json"]
        assert payload["phone_number"] == "1234567890"
        assert payload["sms_sender_id"] == "sender-123"


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
async def test_base_api_create_sms_sender_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.create_sms_sender("svc-1", "+155512345", "desc", "prov-1")


@pytest.mark.asyncio
async def test_base_api_update_sms_sender_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.update_sms_sender("svc-1", "sms-1", sms_sender="+155512345")


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


@pytest.mark.asyncio
async def test_http_api_create_sms_sender():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "sms-new", "sms_sender": "+15559876543"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.create_sms_sender(
            service_id="svc-1",
            sms_sender="+15559876543",
            description="Test sender",
            provider_id="prov-1",
            is_default=True,
            rate_limit=100,
            rate_limit_interval=60,
        )

        assert result["id"] == "sms-new"
        assert mock_post.called
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://api.example.com/service/svc-1/sms-sender"
        payload = call_args[1]["json"]
        assert payload["sms_sender"] == "+15559876543"
        assert payload["description"] == "Test sender"
        assert payload["provider_id"] == "prov-1"
        assert payload["is_default"] is True
        assert payload["rate_limit"] == 100
        assert payload["rate_limit_interval"] == 60


@pytest.mark.asyncio
async def test_http_api_create_sms_sender_minimal():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "sms-new"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.create_sms_sender(
            service_id="svc-1",
            sms_sender="+15559876543",
            description="Test sender",
            provider_id="prov-1",
        )

        assert result["id"] == "sms-new"
        payload = mock_post.call_args[1]["json"]
        assert "rate_limit" not in payload
        assert "rate_limit_interval" not in payload
        assert "inbound_number_id" not in payload


@pytest.mark.asyncio
async def test_http_api_update_sms_sender():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "sms-1", "sms_sender": "+15551111111"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        result = await api.update_sms_sender(
            service_id="svc-1",
            sms_sender_id="sms-1",
            sms_sender="+15551111111",
            description="Updated",
            is_default=True,
        )

        assert result["id"] == "sms-1"
        assert mock_post.called
        call_args = mock_post.call_args
        assert (
            call_args[0][0] == "https://api.example.com/service/svc-1/sms-sender/sms-1"
        )
        payload = call_args[1]["json"]
        assert payload["sms_sender"] == "+15551111111"
        assert payload["description"] == "Updated"
        assert payload["is_default"] is True


@pytest.mark.asyncio
async def test_http_api_update_sms_sender_partial():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "sms-1"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "post", return_value=mock_response) as mock_post:
        await api.update_sms_sender(
            service_id="svc-1",
            sms_sender_id="sms-1",
            is_default=False,
        )

        payload = mock_post.call_args[1]["json"]
        assert payload == {"is_default": False}
        assert "sms_sender" not in payload
        assert "description" not in payload


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


# --- get_inbound_numbers ---


@pytest.mark.asyncio
async def test_base_api_get_inbound_numbers_raises():
    api = NotificationAPI()
    with pytest.raises(NotImplementedError):
        await api.get_inbound_numbers()


@pytest.mark.asyncio
async def test_mock_api_get_inbound_numbers():
    api = MockNotificationAPI()
    numbers = await api.get_inbound_numbers()
    assert len(numbers) == 2
    assert numbers[0]["id"] == "inbound-1"
    assert numbers[0]["number"] == "+18337021549"
    assert numbers[1]["service"] is None


@pytest.mark.asyncio
async def test_http_api_get_inbound_numbers_direct_list():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = [{"id": "n1"}]
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_inbound_numbers()
        assert result == [{"id": "n1"}]


@pytest.mark.asyncio
async def test_http_api_get_inbound_numbers_data_key():
    api = HttpNotificationAPI("https://api.example.com")
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "n2"}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(api.client, "get", return_value=mock_response):
        result = await api.get_inbound_numbers()
        assert result == [{"id": "n2"}]


# --- aclose ---


@pytest.mark.asyncio
async def test_http_api_aclose():
    api = HttpNotificationAPI("https://api.example.com")

    with patch.object(api.client, "aclose", new_callable=AsyncMock) as mock_aclose:
        await api.aclose()
        mock_aclose.assert_called_once()


# --- Retry decorator tests ---


@pytest.mark.asyncio
async def test_http_api_retries_on_read_error():
    """Test that HTTP API retries on transient ReadError."""
    api = HttpNotificationAPI("https://api.example.com")

    call_count = 0

    async def fail_then_succeed(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ReadError("Connection reset")
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"id": "svc-1"}]}
        mock_response.raise_for_status = MagicMock()
        return mock_response

    with patch.object(api.client, "get", side_effect=fail_then_succeed):
        result = await api.get_services()

    assert call_count == 3
    assert result == [{"id": "svc-1"}]


@pytest.mark.asyncio
async def test_http_api_retries_on_connect_error():
    """Test that HTTP API retries on ConnectError."""
    api = HttpNotificationAPI("https://api.example.com")

    call_count = 0

    async def fail_then_succeed(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.ConnectError("Connection refused")
        mock_response = MagicMock()
        mock_response.json.return_value = {"apiKeys": [{"id": "key-1"}]}
        mock_response.raise_for_status = MagicMock()
        return mock_response

    with patch.object(api.client, "get", side_effect=fail_then_succeed):
        result = await api.get_api_keys("svc-1")

    assert call_count == 2
    assert result == [{"id": "key-1"}]


@pytest.mark.asyncio
async def test_http_api_exhausts_retries():
    """Test that HTTP API raises after exhausting retries."""
    api = HttpNotificationAPI("https://api.example.com")

    call_count = 0

    async def always_fail(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise httpx.ReadError("Connection permanently broken")

    with patch.object(api.client, "get", side_effect=always_fail):
        with pytest.raises(httpx.ReadError, match="Connection permanently broken"):
            await api.get_services()

    assert call_count == 3  # Default max retries


@pytest.mark.asyncio
async def test_http_api_no_retry_on_http_error():
    """Test that HTTP API does not retry on HTTP status errors."""
    api = HttpNotificationAPI("https://api.example.com")

    call_count = 0

    async def raise_http_error(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_request = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        raise httpx.HTTPStatusError(
            "Server Error", request=mock_request, response=mock_response
        )

    with patch.object(api.client, "get", side_effect=raise_http_error):
        with pytest.raises(httpx.HTTPStatusError):
            await api.get_services()

    assert call_count == 1  # No retries for HTTP errors
