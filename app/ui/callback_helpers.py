"""Pure helpers for service callback validation and payload building.

Mirrors the constants in notification-api ``app/constants.py``. Deliberately imports
no NiceGUI so every function here is directly unit-testable.
"""

from __future__ import annotations

from typing import Any

# Re-exported for back-compat: these moved to app.ui.http_errors when the permissions
# dialog needed them, and existing production call sites still import them from here.
# This is a name binding, not a forwarding layer -- rebinding one of these names on this
# module does NOT change what http_errors executes, since its functions resolve their
# own module globals at call time. Patch app.ui.http_errors directly.
from app.ui.http_errors import (  # noqa: F401
    extract_error_message,
    format_http_error,
)

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
