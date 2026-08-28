from unittest.mock import MagicMock

import httpx
import pytest

from app.ui import callback_helpers as ch


def test_callback_types_match_api_constants():
    assert ch.CALLBACK_TYPES == ("delivery_status", "complaint", "inbound_sms")


def test_callback_channels_match_api_constants():
    assert ch.CALLBACK_CHANNELS == ("webhook", "queue")


def test_completed_statuses_match_api_constants():
    assert ch.COMPLETED_NOTIFICATION_STATUSES == (
        "sent",
        "delivered",
        "failed",
        "temporary-failure",
        "permanent-failure",
        "returned-letter",
        "cancelled",
    )


def test_bearer_token_min_length():
    assert ch.BEARER_TOKEN_MIN_LENGTH == 10


def test_format_statuses_none():
    assert ch.format_statuses(None) == ""


def test_format_statuses_empty():
    assert ch.format_statuses([]) == ""


def test_format_statuses_with_values():
    assert ch.format_statuses(["delivered", "failed"]) == "delivered, failed"


def test_format_statuses_coerces_non_strings():
    assert ch.format_statuses([1, "sent"]) == "1, sent"


def test_format_statuses_returns_string_value_unchanged():
    # notification_statuses is a JSON column; a row holding a bare string would otherwise
    # be iterated character by character and rendered as "s, e, n, t".
    assert ch.format_statuses("sent") == "sent"


class _FakeCallback:
    """Stands in for a ServiceCallback ORM row."""

    def __init__(self, callback_type=None, callback_channel=None):
        self.callback_type = callback_type
        self.callback_channel = callback_channel


def test_available_options_with_no_existing_callbacks():
    types, channels = ch.available_callback_options([])
    assert types == ["delivery_status", "complaint", "inbound_sms"]
    assert channels == ["webhook", "queue"]


def test_available_options_excludes_used_type_and_channel():
    existing = [_FakeCallback("delivery_status", "webhook")]
    types, channels = ch.available_callback_options(existing)
    assert types == ["complaint", "inbound_sms"]
    assert channels == ["queue"]


def test_available_options_saturated_service():
    existing = [
        _FakeCallback("delivery_status", "webhook"),
        _FakeCallback("complaint", "queue"),
    ]
    types, channels = ch.available_callback_options(existing)
    assert types == ["inbound_sms"]
    assert channels == []


def test_available_options_ignores_unknown_values():
    existing = [_FakeCallback("something_else", "carrier_pigeon")]
    types, channels = ch.available_callback_options(existing)
    assert types == ["delivery_status", "complaint", "inbound_sms"]
    assert channels == ["webhook", "queue"]


def test_available_options_tolerates_null_fields():
    existing = [_FakeCallback(None, None)]
    types, channels = ch.available_callback_options(existing)
    assert types == ["delivery_status", "complaint", "inbound_sms"]
    assert channels == ["webhook", "queue"]


def test_available_options_accepts_dict_rows():
    existing = [{"callback_type": "complaint", "callback_channel": "queue"}]
    types, channels = ch.available_callback_options(existing)
    assert types == ["delivery_status", "inbound_sms"]
    assert channels == ["webhook"]


VALID_TOKEN = "a-token-long-enough"


def test_validate_create_accepts_valid_webhook():
    assert (
        ch.validate_create(
            url="https://example.com/cb",
            callback_type="delivery_status",
            callback_channel="webhook",
            bearer_token=VALID_TOKEN,
            notification_statuses=["sent"],
        )
        is None
    )


def test_validate_create_accepts_queue_without_token():
    assert (
        ch.validate_create(
            url="https://sqs.example.com/q",
            callback_type="inbound_sms",
            callback_channel="queue",
            bearer_token="",
            notification_statuses=[],
        )
        is None
    )


def test_validate_create_requires_url():
    msg = ch.validate_create("", "delivery_status", "webhook", VALID_TOKEN, [])
    assert msg == "URL is required"


def test_validate_create_requires_https():
    msg = ch.validate_create("http://example.com", "delivery_status", "webhook", VALID_TOKEN, [])
    assert msg == "URL must start with https://"


def test_validate_create_requires_known_type():
    msg = ch.validate_create("https://e.com", "bogus", "webhook", VALID_TOKEN, [])
    assert msg == "Callback type is required"


def test_validate_create_requires_known_channel():
    msg = ch.validate_create("https://e.com", "delivery_status", "bogus", VALID_TOKEN, [])
    assert msg == "Callback channel is required"


def test_validate_create_requires_token_for_webhook():
    msg = ch.validate_create("https://e.com", "delivery_status", "webhook", "", [])
    assert msg == "Bearer token is required for webhook callbacks"


def test_validate_create_rejects_short_token():
    msg = ch.validate_create("https://e.com", "delivery_status", "webhook", "short", [])
    assert msg == "Bearer token must be at least 10 characters"


def test_validate_create_rejects_short_token_on_non_webhook_channel():
    # A queue callback does not require a token, but a supplied one must still be valid.
    msg = ch.validate_create("https://e.com", "delivery_status", "queue", "short", [])
    assert msg == "Bearer token must be at least 10 characters"


def test_validate_create_rejects_statuses_for_non_delivery_status():
    msg = ch.validate_create("https://e.com", "complaint", "queue", "", ["sent"])
    assert msg == "Notification statuses are only valid for delivery_status callbacks"


def test_validate_create_rejects_unknown_status():
    msg = ch.validate_create("https://e.com", "delivery_status", "queue", "", ["created"])
    assert msg == "Invalid notification status: created"


# NiceGUI hands back None from an untouched ui.select or a cleared ui.input, so every
# None below is a live runtime value rather than a theoretical one.


def test_validate_create_treats_none_url_as_missing():
    assert ch.validate_create(None, "delivery_status", "webhook", VALID_TOKEN, ["sent"]) == "URL is required"


def test_validate_create_accepts_none_bearer_token_on_queue():
    assert ch.validate_create("https://e.com", "complaint", "queue", None, []) is None


def test_validate_create_rejects_none_bearer_token_on_webhook():
    msg = ch.validate_create("https://e.com", "complaint", "webhook", None, [])
    assert msg == "Bearer token is required for webhook callbacks"


def test_validate_create_accepts_none_statuses():
    assert ch.validate_create("https://e.com", "delivery_status", "webhook", VALID_TOKEN, None) is None


def test_validate_update_treats_none_url_as_missing():
    assert ch.validate_update(None, VALID_TOKEN, "delivery_status", ["sent"]) == "URL is required"


def test_validate_update_accepts_none_bearer_token():
    assert ch.validate_update("https://e.com", None, "delivery_status", ["sent"]) is None


def test_validate_update_accepts_none_statuses():
    assert ch.validate_update("https://e.com", VALID_TOKEN, "delivery_status", None) is None


def test_validate_update_accepts_blank_token():
    assert ch.validate_update("https://e.com", "", "delivery_status", ["sent"]) is None


def test_validate_update_accepts_rotated_token():
    assert ch.validate_update("https://e.com", VALID_TOKEN, "delivery_status", []) is None


def test_validate_update_requires_url():
    assert ch.validate_update("", "", "delivery_status", []) == "URL is required"


def test_validate_update_requires_https():
    assert ch.validate_update("http://e.com", "", "delivery_status", []) == "URL must start with https://"


def test_validate_update_rejects_short_non_blank_token():
    msg = ch.validate_update("https://e.com", "short", "delivery_status", [])
    assert msg == "Bearer token must be at least 10 characters"


def test_validate_update_rejects_statuses_for_non_delivery_status():
    msg = ch.validate_update("https://e.com", "", "complaint", ["sent"])
    assert msg == "Notification statuses are only valid for delivery_status callbacks"


def test_validate_update_rejects_unknown_status():
    msg = ch.validate_update("https://e.com", "", "delivery_status", ["pending"])
    assert msg == "Invalid notification status: pending"


ALL_STATUSES = list(ch.COMPLETED_NOTIFICATION_STATUSES)


def test_build_create_payload_webhook_delivery_status():
    payload = ch.build_create_payload(
        url="https://example.com/cb",
        callback_type="delivery_status",
        callback_channel="webhook",
        bearer_token=VALID_TOKEN,
        notification_statuses=["sent", "delivered"],
        include_provider_payload=True,
    )
    assert payload == {
        "url": "https://example.com/cb",
        "callback_type": "delivery_status",
        "callback_channel": "webhook",
        "include_provider_payload": True,
        "bearer_token": VALID_TOKEN,
        "notification_statuses": ["sent", "delivered"],
    }


def test_build_create_payload_empty_statuses_expands_to_all():
    payload = ch.build_create_payload(
        url="https://e.com",
        callback_type="delivery_status",
        callback_channel="queue",
        bearer_token="",
        notification_statuses=[],
        include_provider_payload=False,
    )
    assert payload["notification_statuses"] == ALL_STATUSES


def test_build_create_payload_omits_statuses_for_non_delivery_status():
    payload = ch.build_create_payload(
        url="https://e.com",
        callback_type="complaint",
        callback_channel="queue",
        bearer_token="",
        notification_statuses=[],
        include_provider_payload=False,
    )
    assert "notification_statuses" not in payload


def test_build_create_payload_omits_blank_bearer_token():
    payload = ch.build_create_payload(
        url="https://e.com",
        callback_type="complaint",
        callback_channel="queue",
        bearer_token="   ",
        notification_statuses=[],
        include_provider_payload=False,
    )
    assert "bearer_token" not in payload


def test_build_create_payload_strips_url_and_token():
    payload = ch.build_create_payload(
        url="  https://e.com  ",
        callback_type="complaint",
        callback_channel="webhook",
        bearer_token=f"  {VALID_TOKEN}  ",
        notification_statuses=[],
        include_provider_payload=False,
    )
    assert payload["url"] == "https://e.com"
    assert payload["bearer_token"] == VALID_TOKEN


def test_build_create_payload_coerces_include_provider_payload_to_bool():
    payload = ch.build_create_payload(
        url="https://e.com",
        callback_type="complaint",
        callback_channel="queue",
        bearer_token="",
        notification_statuses=[],
        include_provider_payload=None,
    )
    assert payload["include_provider_payload"] is False


def test_build_create_payload_rejects_positional_arguments():
    # Six parameters including adjacent same-typed values; keyword-only prevents a silent
    # transposition producing a wrong request body against a live API.
    with pytest.raises(TypeError):
        ch.build_create_payload("https://e.com", "complaint", "queue", "", [], False)


def test_build_update_payload_never_sends_type_or_channel():
    payload = ch.build_update_payload(
        url="https://e.com",
        bearer_token="",
        callback_type="delivery_status",
        notification_statuses=["sent"],
        include_provider_payload=False,
        update_statuses=True,
    )
    assert "callback_type" not in payload
    assert "callback_channel" not in payload


def test_build_update_payload_omits_statuses_when_flag_false():
    payload = ch.build_update_payload(
        url="https://e.com",
        bearer_token="",
        callback_type="delivery_status",
        notification_statuses=["sent"],
        include_provider_payload=False,
        update_statuses=False,
    )
    assert payload == {"url": "https://e.com", "include_provider_payload": False}


def test_build_update_payload_empty_statuses_expands_to_all():
    payload = ch.build_update_payload(
        url="https://e.com",
        bearer_token="",
        callback_type="delivery_status",
        notification_statuses=[],
        include_provider_payload=False,
        update_statuses=True,
    )
    assert payload["notification_statuses"] == ALL_STATUSES


def test_build_update_payload_omits_statuses_for_non_delivery_status_even_when_flagged():
    payload = ch.build_update_payload(
        url="https://e.com",
        bearer_token="",
        callback_type="complaint",
        notification_statuses=["sent"],
        include_provider_payload=False,
        update_statuses=True,
    )
    assert "notification_statuses" not in payload


def test_build_update_payload_includes_non_blank_token():
    payload = ch.build_update_payload(
        url="https://e.com",
        bearer_token=VALID_TOKEN,
        callback_type="complaint",
        notification_statuses=[],
        include_provider_payload=True,
        update_statuses=False,
    )
    assert payload["bearer_token"] == VALID_TOKEN
    assert payload["include_provider_payload"] is True


def test_build_update_payload_omits_blank_token():
    payload = ch.build_update_payload(
        url="https://e.com",
        bearer_token="  ",
        callback_type="complaint",
        notification_statuses=[],
        include_provider_payload=False,
        update_statuses=False,
    )
    assert "bearer_token" not in payload


def test_build_create_payload_none_statuses_expands_to_all():
    payload = ch.build_create_payload(
        url="https://e.com",
        callback_type="delivery_status",
        callback_channel="queue",
        bearer_token="",
        notification_statuses=None,
        include_provider_payload=False,
    )
    assert payload["notification_statuses"] == ALL_STATUSES


def test_build_create_payload_omits_none_bearer_token():
    payload = ch.build_create_payload(
        url="https://e.com",
        callback_type="complaint",
        callback_channel="queue",
        bearer_token=None,
        notification_statuses=[],
        include_provider_payload=False,
    )
    assert "bearer_token" not in payload


def test_build_update_payload_none_statuses_expands_to_all():
    payload = ch.build_update_payload(
        url="https://e.com",
        bearer_token="",
        callback_type="delivery_status",
        notification_statuses=None,
        include_provider_payload=False,
        update_statuses=True,
    )
    assert payload["notification_statuses"] == ALL_STATUSES


def test_build_update_payload_omits_none_bearer_token():
    payload = ch.build_update_payload(
        url="https://e.com",
        bearer_token=None,
        callback_type="complaint",
        notification_statuses=[],
        include_provider_payload=False,
        update_statuses=False,
    )
    assert "bearer_token" not in payload


def test_build_update_payload_rejects_positional_update_statuses():
    # update_statuses and include_provider_payload are adjacent booleans with unrelated
    # meanings; keyword-only prevents a silent transposition.
    with pytest.raises(TypeError):
        ch.build_update_payload("https://e.com", "", "delivery_status", ["sent"], False, True)


def test_extract_jsonschema_error():
    body = {
        "status_code": 400,
        "errors": [{"error": "ValidationError", "message": "url is not a valid https url"}],
    }
    assert ch.extract_error_message(400, body) == "url is not a valid https url"


def test_extract_jsonschema_error_joins_multiple():
    body = {
        "status_code": 400,
        "errors": [
            {"error": "ValidationError", "message": "url is a required property"},
            {"error": "ValidationError", "message": "bearer_token too-short is too short"},
        ],
    }
    result = ch.extract_error_message(400, body)
    assert result == "url is a required property; bearer_token is invalid"


def test_extract_jsonschema_redacts_bearer_token_value():
    body = {
        "status_code": 400,
        "errors": [{"error": "ValidationError", "message": "bearer_token too-short is too short"}],
    }
    result = ch.extract_error_message(400, body)
    assert result == "bearer_token is invalid"
    assert "too-short" not in result


def test_extract_jsonschema_redacts_bearer_token_type_error():
    body = {
        "status_code": 400,
        "errors": [{"error": "ValidationError", "message": "bearer_token None is not of type string"}],
    }
    assert ch.extract_error_message(400, body) == "bearer_token is invalid"


def test_extract_jsonschema_redacts_bearer_token_mid_string():
    # jsonschema's anyOf/oneOf errors embed the whole instance, so the field name is not
    # at the start of the message and a prefix match would leak the value.
    body = {
        "status_code": 400,
        "errors": [
            {
                "error": "ValidationError",
                "message": "{'bearer_token': 'sekrit-value'} is not valid under any of the given schemas",
            }
        ],
    }
    result = ch.extract_error_message(400, body)
    assert result == "bearer_token is invalid"
    assert "sekrit-value" not in result


def test_extract_jsonschema_does_not_redact_non_sensitive_field():
    body = {
        "status_code": 400,
        "errors": [{"error": "ValidationError", "message": "url http://x is not a valid https url"}],
    }
    assert ch.extract_error_message(400, body) == "url http://x is not a valid https url"


def test_extract_jsonschema_redacts_only_the_sensitive_entry():
    body = {
        "status_code": 400,
        "errors": [
            {"error": "ValidationError", "message": "url http://x is not a valid https url"},
            {"error": "ValidationError", "message": "bearer_token hunter2 is too short"},
        ],
    }
    result = ch.extract_error_message(400, body)
    assert result == "url http://x is not a valid https url; bearer_token is invalid"
    assert "hunter2" not in result


def test_extract_marshmallow_bearer_token_entry_is_redacted():
    body = {"result": "error", "message": {"bearer_token": ["Invalid bearer token."]}}
    assert ch.extract_error_message(400, body) == "bearer_token is invalid"


def test_extract_marshmallow_bearer_token_entry_never_echoes_value():
    # Marshmallow does not echo the submitted value today, but that is upstream behavior
    # we do not control, so the redaction must not depend on it.
    body = {"result": "error", "message": {"bearer_token": ["'sekrit-value' is too short"]}}
    result = ch.extract_error_message(400, body)
    assert result == "bearer_token is invalid"
    assert "sekrit-value" not in result


def test_extract_marshmallow_schema_message_mentioning_bearer_token_is_kept():
    # Redaction keys off the field name, not the text, so this stays readable.
    body = {"result": "error", "message": {"_schema": ["Callback channel webhook should have bearer_token"]}}
    assert ch.extract_error_message(400, body) == "_schema: Callback channel webhook should have bearer_token"


def test_extract_marshmallow_multi_field_error():
    body = {"result": "error", "message": {"url": ["Invalid URL."], "bearer_token": ["Invalid bearer token."]}}
    result = ch.extract_error_message(400, body)
    assert "url: Invalid URL." in result
    assert "bearer_token is invalid" in result


def test_extract_conflict_error():
    body = {"message": "A webhook callback already exists for this service"}
    assert ch.extract_error_message(409, body) == "A webhook callback already exists for this service"


def test_extract_string_message_error():
    body = {"result": "error", "message": "No result found"}
    assert ch.extract_error_message(404, body) == "No result found"


def test_extract_falls_back_on_empty_body():
    assert ch.extract_error_message(500, None) == "HTTP 500"


def test_extract_falls_back_on_unrecognized_body():
    assert ch.extract_error_message(500, {"unexpected": "shape"}) == "HTTP 500"


def test_extract_falls_back_on_non_dict_body():
    assert ch.extract_error_message(502, "<html>bad gateway</html>") == "HTTP 502"


def test_extract_falls_back_on_empty_errors_list():
    assert ch.extract_error_message(400, {"status_code": 400, "errors": []}) == "HTTP 400"


def test_extract_marshmallow_scalar_value():
    # The dict branch's ternary takes the non-list path; line coverage hides this.
    body = {"result": "error", "message": {"url": "Invalid URL."}}
    assert ch.extract_error_message(400, body) == "url: Invalid URL."


def test_extract_falls_back_when_errors_have_no_message():
    assert ch.extract_error_message(400, {"errors": [{"error": "ValidationError"}]}) == "HTTP 400"


def test_extract_falls_back_on_empty_message_dict():
    assert ch.extract_error_message(400, {"message": {}}) == "HTTP 400"


def test_extract_falls_back_on_empty_string_message():
    assert ch.extract_error_message(400, {"message": ""}) == "HTTP 400"


def test_format_http_error_uses_parsed_body():
    response = MagicMock()
    response.status_code = 409
    response.json.return_value = {"message": "A queue callback already exists for this service"}
    exc = httpx.HTTPStatusError("boom", request=MagicMock(), response=response)
    assert ch.format_http_error(exc) == "A queue callback already exists for this service"


def test_format_http_error_handles_undecodable_body():
    response = MagicMock()
    response.status_code = 204
    response.json.side_effect = ValueError("no json")
    exc = httpx.HTTPStatusError("boom", request=MagicMock(), response=response)
    assert ch.format_http_error(exc) == "HTTP 204"


def test_format_http_error_handles_non_valueerror_from_json():
    # httpx.Response.json() can raise httpx.ResponseNotRead (a RuntimeError), not just
    # JSONDecodeError/UnicodeDecodeError -- so the broad except is load-bearing.
    response = MagicMock()
    response.status_code = 500
    response.json.side_effect = RuntimeError("stream not read")
    exc = httpx.HTTPStatusError("boom", request=MagicMock(), response=response)
    assert ch.format_http_error(exc) == "HTTP 500"


def test_format_http_error_handles_missing_response():
    exc = httpx.HTTPStatusError("boom", request=MagicMock(), response=None)
    assert ch.format_http_error(exc) == "boom"


# --- resolve_row_environment -------------------------------------------------------


def test_resolve_row_environment_prefers_environment_value():
    row = {"environment_value": "production", "environment": "Staging"}
    assert ch.resolve_row_environment(row) == "production"


def test_resolve_row_environment_falls_back_to_environment():
    assert ch.resolve_row_environment({"environment": "staging"}) == "staging"


def test_resolve_row_environment_rejects_unknown_in_environment_value():
    # The sentinel in environment_value must not fall through to environment: both keys
    # describe the same row, so a "known" display value would be a lie.
    row = {"environment_value": "unknown", "environment": "production"}
    assert ch.resolve_row_environment(row) is None


def test_resolve_row_environment_rejects_unknown_in_environment():
    assert ch.resolve_row_environment({"environment": "unknown"}) is None


def test_resolve_row_environment_rejects_unknown_in_both_keys():
    assert ch.resolve_row_environment({"environment_value": "unknown", "environment": "unknown"}) is None


def test_resolve_row_environment_empty_environment_value_falls_through():
    assert ch.resolve_row_environment({"environment_value": "", "environment": "development"}) == "development"


def test_resolve_row_environment_rejects_empty_string_in_both_keys():
    assert ch.resolve_row_environment({"environment_value": "", "environment": ""}) is None


def test_resolve_row_environment_rejects_none_values():
    assert ch.resolve_row_environment({"environment_value": None, "environment": None}) is None


def test_resolve_row_environment_missing_keys():
    assert ch.resolve_row_environment({"id": "cb-1"}) is None


def test_resolve_row_environment_empty_dict():
    assert ch.resolve_row_environment({}) is None


def test_resolve_row_environment_none_row():
    assert ch.resolve_row_environment(None) is None


def test_resolve_row_environment_unknown_sentinel_matches_constant():
    assert ch.UNKNOWN_ENVIRONMENT == "unknown"


# --- edit_statuses_control_state ---------------------------------------------------


def test_edit_statuses_control_state_delivery_status_checked():
    assert ch.edit_statuses_control_state("delivery_status", True) == (True, True)


def test_edit_statuses_control_state_delivery_status_unchecked():
    assert ch.edit_statuses_control_state("delivery_status", False) == (True, False)


def test_edit_statuses_control_state_complaint_checked():
    # Type gating wins: a stale checked box must not re-enable the select.
    assert ch.edit_statuses_control_state("complaint", True) == (False, False)


def test_edit_statuses_control_state_complaint_unchecked():
    assert ch.edit_statuses_control_state("complaint", False) == (False, False)


def test_edit_statuses_control_state_inbound_sms_checked():
    assert ch.edit_statuses_control_state("inbound_sms", True) == (False, False)


def test_edit_statuses_control_state_inbound_sms_unchecked():
    assert ch.edit_statuses_control_state("inbound_sms", False) == (False, False)


def test_edit_statuses_control_state_none_type_checked():
    assert ch.edit_statuses_control_state(None, True) == (False, False)


def test_edit_statuses_control_state_none_type_unchecked():
    assert ch.edit_statuses_control_state(None, False) == (False, False)


def test_edit_statuses_control_state_unknown_type():
    assert ch.edit_statuses_control_state("not_a_real_type", True) == (False, False)


def test_edit_statuses_control_state_coerces_truthy_checkbox_value():
    # NiceGUI checkboxes can hand back None before the first interaction.
    assert ch.edit_statuses_control_state("delivery_status", None) == (True, False)
    assert ch.edit_statuses_control_state("delivery_status", "yes") == (True, True)


def test_edit_statuses_control_state_returns_bools():
    checkbox_enabled, select_enabled = ch.edit_statuses_control_state("delivery_status", 1)
    assert checkbox_enabled is True
    assert select_enabled is True


# --- create_statuses_default -------------------------------------------------------


def test_create_statuses_default_delivery_status_empty_expands_to_all():
    enabled, value = ch.create_statuses_default("delivery_status", [])
    assert enabled is True
    assert value == list(ch.COMPLETED_NOTIFICATION_STATUSES)


def test_create_statuses_default_delivery_status_none_expands_to_all():
    enabled, value = ch.create_statuses_default("delivery_status", None)
    assert enabled is True
    assert value == list(ch.COMPLETED_NOTIFICATION_STATUSES)


def test_create_statuses_default_delivery_status_keeps_existing_selection():
    enabled, value = ch.create_statuses_default("delivery_status", ["delivered"])
    assert enabled is True
    assert value == ["delivered"]


def test_create_statuses_default_copies_the_current_list():
    # The caller assigns the result straight onto a NiceGUI element, so returning the
    # same list object would alias the widget's value to the caller's list.
    current = ["delivered"]
    _, value = ch.create_statuses_default("delivery_status", current)
    assert value is not current
    value.append("failed")
    assert current == ["delivered"]


def test_create_statuses_default_does_not_alias_the_module_constant():
    _, value = ch.create_statuses_default("delivery_status", [])
    value.clear()
    assert ch.COMPLETED_NOTIFICATION_STATUSES[0] == "sent"


def test_create_statuses_default_complaint_empty():
    assert ch.create_statuses_default("complaint", []) == (False, [])


def test_create_statuses_default_complaint_clears_populated_selection():
    # Statuses are invalid for a non-delivery_status callback, so a leftover selection
    # from a previous type has to be dropped, not preserved.
    assert ch.create_statuses_default("complaint", ["delivered"]) == (False, [])


def test_create_statuses_default_inbound_sms_empty():
    assert ch.create_statuses_default("inbound_sms", []) == (False, [])


def test_create_statuses_default_inbound_sms_populated():
    assert ch.create_statuses_default("inbound_sms", ["sent"]) == (False, [])


def test_create_statuses_default_none_type_empty():
    assert ch.create_statuses_default(None, []) == (False, [])


def test_create_statuses_default_none_type_none_current():
    assert ch.create_statuses_default(None, None) == (False, [])


def test_create_statuses_default_none_type_populated():
    assert ch.create_statuses_default(None, ["delivered"]) == (False, [])


def test_create_statuses_default_unknown_type():
    assert ch.create_statuses_default("not_a_real_type", ["delivered"]) == (False, [])
