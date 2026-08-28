"""Pure helpers for service callback validation, payload building, and error parsing.

Mirrors the constants in notification-api ``app/constants.py``. Deliberately imports
no NiceGUI so every function here is directly unit-testable.
"""

from __future__ import annotations

from typing import Any

import httpx

# Mirrors SERVICE_CALLBACK_TYPES in notification-api app/constants.py
CALLBACK_TYPES = ("delivery_status", "complaint", "inbound_sms")

# Mirrors CALLBACK_CHANNEL_TYPES in notification-api app/constants.py
CALLBACK_CHANNELS = ("webhook", "queue")

# Mirrors NOTIFICATION_STATUS_TYPES_COMPLETED in notification-api app/constants.py.
# This is the ONLY set the callback endpoints accept -- deliberately narrower than the
# full notification lifecycle (see CallbackNotificationStatus in data/openapi.yaml).
COMPLETED_NOTIFICATION_STATUSES = (
    "sent",
    "delivered",
    "failed",
    "temporary-failure",
    "permanent-failure",
    "returned-letter",
    "cancelled",
)

DELIVERY_STATUS_CALLBACK_TYPE = "delivery_status"
WEBHOOK_CHANNEL = "webhook"
BEARER_TOKEN_MIN_LENGTH = 10

# Sentinel written by syncs that predate environment tracking. It is not a real
# environment, so it can never be used to build an API client.
UNKNOWN_ENVIRONMENT = "unknown"

# Field names whose validation errors may echo the submitted value back to us.
# notification-api formats jsonschema errors as "{field} {value} {reason}", which would
# put a live credential into ui.notify and, via safe_notify, into the log file.
SENSITIVE_ERROR_FIELDS = ("bearer_token",)


def _redact_error_message(message: str) -> str:
    """Replace any validation message that could contain a secret value.

    The substring match is intentionally broad: jsonschema does not always lead with the
    field name (``{'bearer_token': '...'} is not valid under any of the given schemas``),
    so anchoring on a prefix would let a live credential through. Over-redacting a
    harmless message is strictly preferable to leaking a credential into ui.notify and,
    via safe_notify, into the log file.
    """
    for field in SENSITIVE_ERROR_FIELDS:
        if field in message:
            return f"{field} is invalid"
    return message


def format_statuses(statuses: list[Any] | None) -> str:
    """Render a notification status list for table display.

    ``notification_statuses`` is a JSON column, so a row could hold a bare string. Return
    it as-is rather than joining it character by character.
    """
    if not statuses:
        return ""
    if isinstance(statuses, str):
        return statuses
    return ", ".join(str(s) for s in statuses)


def resolve_row_environment(row: dict[str, Any] | None) -> str | None:
    """Return a usable environment for a table row, or None when it is not known.

    Rows synced before environments were tracked carry the ``"unknown"`` sentinel, which
    cannot be used to build an API client. ``environment_value`` holds the raw column and
    takes precedence over ``environment``, which is the display-formatted variant.
    """
    if not row:
        return None
    env_value = row.get("environment_value") or row.get("environment")
    if not env_value or env_value == UNKNOWN_ENVIRONMENT:
        return None
    return env_value


def edit_statuses_control_state(callback_type: str | None, update_checked: bool) -> tuple[bool, bool]:
    """Return ``(checkbox_enabled, select_enabled)`` for the edit dialog's status controls.

    Statuses are only meaningful for delivery_status callbacks, and the select is only
    live once the user has explicitly opted into changing them.
    """
    if callback_type != DELIVERY_STATUS_CALLBACK_TYPE:
        return False, False
    return True, bool(update_checked)


def create_statuses_default(callback_type: str | None, current: list[str] | None) -> tuple[bool, list[str]]:
    """Return ``(select_enabled, value)`` for the create dialog's status multi-select.

    An empty selection on a delivery_status callback means "all statuses", so the select
    is pre-populated to make that visible rather than surprising.
    """
    if callback_type != DELIVERY_STATUS_CALLBACK_TYPE:
        return False, []
    return True, list(current) if current else list(COMPLETED_NOTIFICATION_STATUSES)


def _field(row: Any, name: str) -> Any:
    """Read *name* from an ORM row or a plain dict."""
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def available_callback_options(existing: list[Any]) -> tuple[list[str], list[str]]:
    """Return the callback types and channels still creatable for a service.

    notification-api's ``check_existing_callback`` rejects a new callback whose type OR
    channel matches an existing one, so both dimensions are filtered independently.
    """
    used_types = {_field(row, "callback_type") for row in existing}
    used_channels = {_field(row, "callback_channel") for row in existing}
    types = [t for t in CALLBACK_TYPES if t not in used_types]
    channels = [c for c in CALLBACK_CHANNELS if c not in used_channels]
    return types, channels


def _validate_url(url: str | None) -> str | None:
    value = (url or "").strip()
    if not value:
        return "URL is required"
    if not value.startswith("https://"):
        return "URL must start with https://"
    return None


def _validate_statuses(callback_type: str | None, notification_statuses: list[str] | None) -> str | None:
    statuses = notification_statuses or []
    if statuses and callback_type != DELIVERY_STATUS_CALLBACK_TYPE:
        return "Notification statuses are only valid for delivery_status callbacks"
    for status in statuses:
        if status not in COMPLETED_NOTIFICATION_STATUSES:
            return f"Invalid notification status: {status}"
    return None


def validate_create(
    url: str | None,
    callback_type: str | None,
    callback_channel: str | None,
    bearer_token: str | None,
    notification_statuses: list[str] | None,
) -> str | None:
    """Return an error message for an invalid create request, or None when valid."""
    error = _validate_url(url)
    if error:
        return error
    if callback_type not in CALLBACK_TYPES:
        return "Callback type is required"
    if callback_channel not in CALLBACK_CHANNELS:
        return "Callback channel is required"
    token = (bearer_token or "").strip()
    if callback_channel == WEBHOOK_CHANNEL:
        if not token:
            return "Bearer token is required for webhook callbacks"
        if len(token) < BEARER_TOKEN_MIN_LENGTH:
            return f"Bearer token must be at least {BEARER_TOKEN_MIN_LENGTH} characters"
    elif token and len(token) < BEARER_TOKEN_MIN_LENGTH:
        return f"Bearer token must be at least {BEARER_TOKEN_MIN_LENGTH} characters"
    return _validate_statuses(callback_type, notification_statuses)


def validate_update(
    url: str | None,
    bearer_token: str | None,
    callback_type: str | None,
    notification_statuses: list[str] | None,
) -> str | None:
    """Return an error message for an invalid update request, or None when valid.

    A blank bearer token is valid and means "keep the existing token" -- the API never
    returns the stored value, so there is nothing to pre-fill or compare against.
    """
    error = _validate_url(url)
    if error:
        return error
    token = (bearer_token or "").strip()
    if token and len(token) < BEARER_TOKEN_MIN_LENGTH:
        return f"Bearer token must be at least {BEARER_TOKEN_MIN_LENGTH} characters"
    return _validate_statuses(callback_type, notification_statuses)


def _resolve_statuses(notification_statuses: list[str] | None) -> list[str]:
    """Expand an empty selection to every completed status.

    Matches ``ServiceCallback.__init__``, which defaults ``notification_statuses`` to the
    full set when a delivery_status callback is created without one. ``__init__`` does not
    run on update, so we always send the list explicitly to keep both paths identical.
    """
    return list(notification_statuses) if notification_statuses else list(COMPLETED_NOTIFICATION_STATUSES)


def build_create_payload(
    *,
    url: str | None,
    callback_type: str | None,
    callback_channel: str | None,
    bearer_token: str | None,
    notification_statuses: list[str] | None,
    include_provider_payload: bool | None,
) -> dict[str, Any]:
    """Build the POST body for creating a service callback.

    Assumes :func:`validate_create` has already passed; does not re-validate.

    Keyword-only: the parameters include adjacent same-typed arguments, and a transposed
    pair would silently produce a wrong request body against a live API.
    """
    payload: dict[str, Any] = {
        "url": (url or "").strip(),
        "callback_type": callback_type,
        "callback_channel": callback_channel,
        "include_provider_payload": bool(include_provider_payload),
    }
    token = (bearer_token or "").strip()
    if token:
        payload["bearer_token"] = token
    if callback_type == DELIVERY_STATUS_CALLBACK_TYPE:
        payload["notification_statuses"] = _resolve_statuses(notification_statuses)
    return payload


def build_update_payload(
    *,
    url: str | None,
    bearer_token: str | None,
    callback_type: str | None,
    notification_statuses: list[str] | None,
    include_provider_payload: bool | None,
    update_statuses: bool,
) -> dict[str, Any]:
    """Build the POST body for updating a service callback.

    Assumes :func:`validate_update` has already passed; does not re-validate.

    Keyword-only: the parameters include adjacent same-typed arguments, and a transposed
    pair would silently produce a wrong request body against a live API.

    Never emits ``callback_type`` or ``callback_channel``. Sending ``callback_channel:
    webhook`` would force the caller to resend ``bearer_token`` (marshmallow
    ``validates_schema``), and sending ``callback_type`` can trigger an unhandled 500 on
    unique-constraint collision. Always emits ``url``, which satisfies the update schema's
    ``anyOf`` requirement.
    """
    payload: dict[str, Any] = {
        "url": (url or "").strip(),
        "include_provider_payload": bool(include_provider_payload),
    }
    token = (bearer_token or "").strip()
    if token:
        payload["bearer_token"] = token
    if update_statuses and callback_type == DELIVERY_STATUS_CALLBACK_TYPE:
        payload["notification_statuses"] = _resolve_statuses(notification_statuses)
    return payload


def extract_error_message(status_code: int, body: Any) -> str:
    """Normalize notification-api's four error body shapes into one readable string.

    jsonschema 400 : {"status_code": 400, "errors": [{"error": ..., "message": ...}]}
    marshmallow 400: {"result": "error", "message": {"field": ["msg"]}}
    conflict 409   : {"message": "A webhook callback already exists for this service"}
    generic        : {"result": "error", "message": "No result found"}
    """
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            messages = [
                _redact_error_message(str(e.get("message"))) for e in errors if isinstance(e, dict) and e.get("message")
            ]
            if messages:
                return "; ".join(messages)
        message = body.get("message")
        if isinstance(message, dict):
            parts = []
            for field, value in message.items():
                # Redact on the field KEY, not the message text. Marshmallow keys its errors
                # by field name, so a bearer_token entry is redacted while a safe schema-level
                # message that merely mentions the word ("Callback channel webhook should have
                # bearer_token") keeps its diagnostic value. Marshmallow does not echo the
                # submitted value today, but that is upstream behavior we do not control.
                if field in SENSITIVE_ERROR_FIELDS:
                    parts.append(f"{field} is invalid")
                    continue
                text = "; ".join(str(v) for v in value) if isinstance(value, list) else str(value)
                parts.append(f"{field}: {text}")
            if parts:
                return "; ".join(parts)
        elif isinstance(message, str) and message:
            return message
    return f"HTTP {status_code}"


def format_http_error(exc: httpx.HTTPStatusError) -> str:
    """Render an ``httpx.HTTPStatusError`` using the API's error body when available."""
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        body = response.json()
    except Exception:
        # Deliberately broad: ``httpx.Response.json()`` can raise ``json.JSONDecodeError``,
        # ``UnicodeDecodeError``, or ``httpx.ResponseNotRead`` (a ``RuntimeError``). An
        # unparseable body must never mask the status code we already have.
        body = None
    return extract_error_message(response.status_code, body)
