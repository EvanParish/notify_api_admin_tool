"""Normalization of notification-api HTTP error bodies into readable strings.

Deliberately imports no NiceGUI so every function here is directly unit-testable.
"""

from __future__ import annotations

from typing import Any

import httpx

# Field names whose validation errors may echo the submitted value back to us.
# notification-api formats jsonschema errors as "{field} {value} {reason}", which would
# put a live credential into ui.notify and, via safe_notify, into the log file.
#
# Over-redaction costs nothing here -- the worst case is a slightly less specific error
# message -- while under-redaction writes a live credential to disk. This tuple IS the
# entire guarantee, so it is deliberately wider than the fields notification-api is known
# to echo today. Order matters: the more specific "bearer_token" precedes "token" so a
# bearer_token error is reported as such rather than as the vaguer "token is invalid".
SENSITIVE_ERROR_FIELDS = (
    "bearer_token",
    "api_key",
    "secret",
    "password",
    "token",
    "authorization",
    "auth_parameter",
)


def redact_error_message(message: str) -> str:
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
                redact_error_message(str(e.get("message"))) for e in errors if isinstance(e, dict) and e.get("message")
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
