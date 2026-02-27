"""Email rotation constants and API-key email generation helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.ui import state as _st

# ---------------------------------------------------------------------------
# Environment aliases and endpoint mappings
# ---------------------------------------------------------------------------
EMAIL_ENV_ALIASES = {"development": "dev", "production": "prod"}
EMAIL_PUBLIC_ENDPOINTS = {
    "local": "http://localhost:6011",
    "dev": "https://dev-api.va.gov/vanotify",
    "perf": "https://sandbox-api.va.gov/vanotify",
    "staging": "https://staging-api.va.gov/vanotify",
    "prod": "https://api.va.gov/vanotify",
}
EMAIL_PRIVATE_ENDPOINTS = {
    "local": "http://priv-localhost:6011",
    "dev": "https://dev.api.notifications.va.gov",
    "perf": "https://perf.api.notifications.va.gov",
    "staging": "https://staging.api.notifications.va.gov",
    "prod": "https://api.notifications.va.gov",
}
UUID_SECRET_TYPE = "uuid"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def _normalize_email_env(env: str) -> str:
    return EMAIL_ENV_ALIASES.get(env, env)


def _format_email_env_label(env: str) -> str:
    normalized = _normalize_email_env(env)
    return "Production" if normalized in {"prod", "production"} else normalized.title()


def _resolve_email_endpoints(env: str) -> tuple[str, str]:
    normalized = _normalize_email_env(env)
    public_url = EMAIL_PUBLIC_ENDPOINTS.get(normalized)
    private_url = EMAIL_PRIVATE_ENDPOINTS.get(normalized)
    if public_url and private_url:
        return public_url, private_url
    fallback = _st.config.api_hosts.get(env) or _st.config.api_hosts.get(normalized)
    if fallback:
        fallback = fallback.rstrip("/")
        return fallback, fallback
    raise ValueError(f"No email endpoints configured for environment {env}")


def _format_expiry_date(value: Optional[str]) -> str:
    if not value:
        return "unknown"
    return value.split("T", 1)[0]


def _select_latest_key(keys: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    matches = [key for key in keys if key.get("name") == name]
    if not matches:
        raise ValueError(f"No keys found with name: {name}")
    if len(matches) == 1:
        return matches[0]
    return max(matches, key=lambda key: key.get("created_at") or "")


def _build_key_email(
    key_secret: str,
    created_key: Dict[str, Any],
    env: str,
    service_name: str,
    service_id: str,
) -> str:
    env_label = _format_email_env_label(env)
    public_url, private_url = _resolve_email_endpoints(env)
    expiry_date = _format_expiry_date(created_key.get("expiry_date"))
    rotation_date = datetime.now(timezone.utc).date() + timedelta(days=30)
    key_name = created_key.get("name") or ""
    key_id = created_key.get("id") or ""
    return (
        "\nHello,\n\n"
        "Please see the details below regarding your key(s) for the VA Notify API.\n\n"
        "Action items:\n"
        "1. Please confirm receipt of this email.\n"
        "2. Please confirm when you have implemented the new key(s) in your application.\n\n"
        f"{env_label} Details\n"
        f"Key Secret: {key_secret}\n"
        f"Expiration Date: {expiry_date}\n"
        f"Key Name: {key_name}\n"
        f"Key ID: {key_id}\n\n"
        f"{env_label} Service\n"
        f"Service Name: {service_name}\n"
        f"Service ID: {service_id}\n\n"
        f"{env_label} VA Notify Endpoints:\n"
        "You would use this endpoint for email POST within the VA Network:\n"
        f"{private_url}/v2/notifications/email\n"
        "or if outside the VA Network:\n"
        f"{public_url}/v2/notifications/email\n\n"
        "If you need anything else, please don't hesitate to reach out - contact us via email "
        "oitoctovanotify@va.gov or Slack #va-notify-public channel!\n\n"
        "--- Only include for API key rotation notices ---\n"
        f"Your current keys will expire in 30 days ({rotation_date}).\n"
    )
