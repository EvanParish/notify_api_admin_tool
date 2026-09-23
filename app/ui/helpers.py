"""Pure UI helper utilities.

These functions have no dependency on ``app.ui.state`` globals — they accept
all needed data as parameters or access only NiceGUI/stdlib APIs.
"""

from __future__ import annotations

import base64
import csv
import inspect
import io
import json
import logging
import re
from typing import Any, Dict, List, Optional

from nicegui import ui

from app.repository import _is_expired

from app.ui.artifacts import SEND_RESULTS_DIR, write_json_artifact  # noqa: F401
from app.ui.state import safe_notify

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric card
# ---------------------------------------------------------------------------
def metric_card(title: str, value: int) -> None:
    with ui.card().classes("flex-1 min-w-[240px]"):
        ui.label(title).classes("text-sm text-gray-600 dark:text-slate-300")
        ui.label(str(value)).classes("text-3xl font-bold")


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------
async def refresh_if_needed(refreshable) -> None:
    result = refreshable.refresh()
    if inspect.isawaitable(result):
        await result


def make_sortable(columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{**column, "sortable": True} for column in columns]


def make_row_key(entity_id: Any, environment: Optional[str]) -> str:
    """Create a unique row key combining id and environment for Quasar tables."""
    return f"{entity_id or ''}:{environment or ''}"


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------
# Leading characters Excel, LibreOffice Calc, and Google Sheets treat as the start of a
# formula. Every value in an export is API-controlled text (service names, ids, and now
# the permissions column), so an unescaped cell is remote code execution in the reviewer's
# spreadsheet, not just a rendering oddity.
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _escape_csv_cell(value: Any) -> Any:
    """Neutralize spreadsheet formula injection by prefixing a single quote.

    Only ``str`` values are touched. The quote is what every spreadsheet reads as "the
    rest of this cell is literal text", and it is the standard mitigation; the cost is
    that a genuinely negative number arriving as the string ``-5`` exports as ``'-5``.
    That trade is correct for this tool: no column here is numeric, and a mangled cell is
    recoverable while an executed formula is not.
    """
    if isinstance(value, str) and value.startswith(CSV_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def rows_to_csv(rows: List[Dict[str, Any]], columns: List[Dict[str, Any]]) -> str:
    """Convert table rows to CSV string using column definitions."""
    output = io.StringIO()
    # Get field names from columns, excluding internal fields like _row_key
    fields = [col["field"] for col in columns if not col["field"].startswith("_")]
    labels = [col["label"] for col in columns if not col["field"].startswith("_")]

    writer = csv.writer(output)
    writer.writerow(labels)
    for row in rows:
        writer.writerow([_escape_csv_cell(row.get(field, "")) for field in fields])

    return output.getvalue()


def download_csv(csv_content: str, filename: str) -> None:
    """Trigger a CSV file download in the browser."""
    b64 = base64.b64encode(csv_content.encode("utf-8")).decode("utf-8")
    ui.run_javascript(
        f"""
        const link = document.createElement('a');
        link.href = 'data:text/csv;base64,{b64}';
        link.download = '{filename}';
        link.click();
        """
    )
    safe_notify(f"Exported {filename}", color="green")


def add_export_button(
    rows: List[Dict[str, Any]],
    columns: List[Dict[str, Any]],
    filename: str,
) -> ui.button:
    """Add an export CSV button that downloads the current table data."""

    def handle_export():
        csv_content = rows_to_csv(rows, columns)
        download_csv(csv_content, filename)

    return ui.button("Export CSV", icon="download", on_click=handle_export).props("flat dense")


# ---------------------------------------------------------------------------
# Copyable cell slots
# ---------------------------------------------------------------------------
COPYABLE_FIELDS = (
    "id",
    "created_by",
    "email_address",
    "key_name",
    "name",
    "number",
    "service_id",
    "sms_sender",
)
COPYABLE_CELL_SLOT = """
<q-td :props="props">
  <span
    class="cursor-pointer text-primary"
    title="Click to copy"
    @click="$parent.$emit('cell-copy', props.row['_full_' + props.col.field] || props.value)"
  >{{ props.value }}</span>
</q-td>
"""


def copy_to_clipboard(text: Any) -> None:
    """Copy *text* to the client clipboard, or report that there was nothing to copy.

    The early return must stay ahead of ``run_javascript``: a null cell (e.g. the
    ``service_id`` of an unassigned inbound number) would otherwise overwrite whatever
    the user already had on their clipboard with an empty string, and then claim
    success.  ``str(text)`` is checked rather than *text* itself so that ``0`` still
    copies as ``"0"``.
    """
    value = "" if text is None else str(text)
    if not value:
        safe_notify("Nothing to copy", color="warning")
        return
    ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(value)})")
    safe_notify(f'Copied "{value}" to clipboard!', color="green")


def get_copyable_fields(rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return []
    return [field for field in COPYABLE_FIELDS if field in rows[0]]


def add_copyable_slots(table, rows: List[Dict[str, Any]]) -> None:
    copyable_fields = get_copyable_fields(rows)
    for field in copyable_fields:
        table.add_slot(f"body-cell-{field}", COPYABLE_CELL_SLOT)
    if copyable_fields:
        table.on("cell-copy", lambda e: copy_to_clipboard(e.args))


# ---------------------------------------------------------------------------
# Service-name context menu (right-click → Copy Name / Copy ID)
# ---------------------------------------------------------------------------
_SERVICE_CONTEXT_MENU_SLOT = """
<q-td :props="props">
  <span
    class="cursor-pointer text-primary"
    title="Click to copy · Right-click for options"
    @click="$parent.$emit('cell-copy', props.row['_full_' + props.col.field] || props.value)"
  >{{ props.value }}</span>
  <q-menu context-menu>
    <q-list dense style="min-width: 180px">
      <q-item clickable v-close-popup
        @click="$parent.$emit('svc-ctx-copy', props.row['_full_' + props.col.field] || props.value)">
        <q-item-section side><q-icon name="badge" size="xs" /></q-item-section>
        <q-item-section>Copy Service Name</q-item-section>
      </q-item>
      <q-item clickable v-close-popup
        @click="$parent.$emit('svc-ctx-copy', props.row['__id_field__'])">
        <q-item-section side><q-icon name="fingerprint" size="xs" /></q-item-section>
        <q-item-section>Copy Service ID</q-item-section>
      </q-item>
    </q-list>
  </q-menu>
</q-td>
"""


def add_service_context_menu(table, *, column_name: str, id_field: str = "service_id") -> None:
    """Add a right-click context menu to a service-name column.

    The menu offers *Copy Service Name* and *Copy Service ID*.
    Left-click-to-copy behaviour is preserved.
    """
    slot_html = _SERVICE_CONTEXT_MENU_SLOT.replace("__id_field__", id_field)
    table.add_slot(f"body-cell-{column_name}", slot_html)
    table.on("svc-ctx-copy", lambda e: copy_to_clipboard(e.args))


# ---------------------------------------------------------------------------
# Communication-item context menu (right-click → Copy ID / Name / Number)
# ---------------------------------------------------------------------------
_COMM_ITEM_CONTEXT_MENU_SLOT = """
<q-td :props="props">
  <span
    class="cursor-pointer text-primary"
    title="Click to copy · Right-click for options"
    @click="$parent.$emit('cell-copy', props.value)"
  >{{ props.value }}</span>
  <q-menu context-menu>
    <q-list dense style="min-width: 200px">
      <q-item clickable v-close-popup
        @click="$parent.$emit('comm-ctx-copy', props.row['_comm_item_id'])">
        <q-item-section side><q-icon name="fingerprint" size="xs" /></q-item-section>
        <q-item-section>Copy Com Item ID</q-item-section>
      </q-item>
      <q-item clickable v-close-popup
        @click="$parent.$emit('comm-ctx-copy', props.row['_comm_item_name'])">
        <q-item-section side><q-icon name="badge" size="xs" /></q-item-section>
        <q-item-section>Copy Com Item Name</q-item-section>
      </q-item>
      <q-item clickable v-close-popup
        @click="$parent.$emit('comm-ctx-copy', props.row['_comm_item_va_profile_item_id'])">
        <q-item-section side><q-icon name="tag" size="xs" /></q-item-section>
        <q-item-section>Copy Com Item Number</q-item-section>
      </q-item>
    </q-list>
  </q-menu>
</q-td>
"""


def add_comm_item_context_menu(table, *, column_name: str) -> None:
    """Add a right-click context menu to a communication-item column.

    The menu offers *Copy Com Item ID*, *Copy Com Item Name*, and
    *Copy Com Item Number* (va_profile_item_id).
    Left-click-to-copy behaviour is preserved.
    """
    table.add_slot(f"body-cell-{column_name}", _COMM_ITEM_CONTEXT_MENU_SLOT)
    table.on("comm-ctx-copy", lambda e: copy_to_clipboard(e.args))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def format_environment(value: Optional[str]) -> str:
    return value or "unknown"


def format_service_label(service) -> str:
    return f"{service.name} ({format_environment(service.environment)})"


def build_service_name_map(services) -> Dict[str, str]:
    """Build a {service_id: service_name} lookup from a list of Service objects."""
    return {svc.id: svc.name for svc in services}


def build_user_email_map(users) -> Dict[str, str]:
    """Build a {user_id: email_address} lookup from a list of User objects."""
    return {user.id: user.email_address for user in users if user.email_address}


def resolve_user_email(user_id: str | None, email_map: Dict[str, str]) -> str:
    """Look up a user email by ID.  Falls back to *user_id* when unknown."""
    if not user_id:
        return ""
    return email_map.get(user_id) or user_id


def build_api_key_map(keys) -> Dict[str, Any]:
    """Build a {api_key_id: ApiKey} lookup from a list of ApiKey objects."""
    return {key.id: key for key in keys}


def local_key_status(api_key) -> str:
    """Describe the remote state of a stored local key secret.

    Returns "" when the local key is unlinked or its remote row is not in the local
    cache, since in that case nothing is known rather than the key being healthy.
    """
    if api_key is None:
        return ""
    if getattr(api_key, "revoked", False):
        return "Revoked"
    if _is_expired(getattr(api_key, "expiry_date", None)):
        return "Expired"
    return "Active"


def format_local_key_label(key_name: str, status: str) -> str:
    """Annotate a key-selector option when the stored secret is no longer usable."""
    if status == "Revoked":
        return f"{key_name} (revoked)"
    if status == "Expired":
        return f"{key_name} (expired)"
    return key_name


def build_local_key_options(local_keys, api_key_map: Dict[str, Any]) -> Dict[int, str]:
    """Build the {local_row_id: label} option map for a stored-key selector.

    Keys whose remote counterpart is revoked or expired stay selectable but are
    labelled, so the send failure is explained before it happens rather than after.
    """
    return {
        key.id: format_local_key_label(key.key_name, local_key_status(api_key_map.get(key.api_key_id)))
        for key in local_keys
    }


def truncate_service_name(name: str | None, limit: int = 21) -> str:
    """Truncate a service name to *limit* characters, appending '…' if needed."""
    if not name:
        return ""
    return name[: limit - 1] + "…" if len(name) > limit else name


def resolve_service_name(
    service_id: str | None,
    name_map: Dict[str, str],
    limit: int = 21,
) -> str:
    """Look up a service name by ID and truncate.  Falls back to *service_id*."""
    if not service_id:
        return ""
    name = name_map.get(service_id)
    if name is None:
        return service_id
    return truncate_service_name(name, limit)


def with_option(
    options: Dict[str, str],
    value: str | None,
    label: str | None = None,
) -> Dict[str, str]:
    """Return *options* guaranteed to contain *value* as a key.

    NiceGUI's ``ui.select`` silently discards an assigned value that is absent from
    its options.  When a cached lookup table is stale, the select blanks itself and
    the field is then omitted from the update payload -- the caller reports success
    while the edit quietly does nothing.  Injecting the current value as an option
    keeps it selectable and submittable.

    The input dict is not mutated.  An existing key keeps its original label.

    This is the single-value primitive that :func:`set_options_preserving` wraps; that
    wrapper is the only production caller.  Multi-select callers must not use it -- the
    ``value in options`` test raises ``TypeError`` on an unhashable list.
    """
    if not value or value in options:
        return dict(options)
    return {**options, value: label or f"{value} (not synced)"}


def set_options_preserving(select, options, value, label=None) -> None:
    """Point *select* at *options*, keeping *value* selected even if the cache is stale.

    Options and value are applied in a single ``set_options`` call so the two cannot be
    sequenced wrongly.  Assigning ``value`` separately, after options that lack it, would
    silently null the selection -- the field would then be omitted from the update payload
    while the UI still reported success.
    """
    select.set_options(with_option(options, value, label), value=value or None)


def sms_provider_identifier_options(providers) -> Dict[str, str]:
    """Build select options for fields storing a provider *identifier* string.

    ``InboundNumber.provider`` is a plain string column upstream with no foreign key,
    so these options are keyed by ``ProviderDetail.identifier`` (e.g. ``"pinpoint"``).
    This is deliberately different from ``SmsSender.provider_id``, which is a UUID and
    keys by ``ProviderDetail.id``.

    Only SMS providers are included.  Rows with no identifier are omitted -- there is
    nothing submittable for them.
    """
    return {
        p.identifier: f"{p.display_name or p.identifier} ({p.identifier})"
        for p in providers
        if p.notification_type == "sms" and p.identifier
    }


def truncate_text(value: Optional[str], limit: int = 50) -> Optional[str]:
    if not value:
        return value
    return value[:limit] + "..." if len(value) > limit else value


# ---------------------------------------------------------------------------
# Personalisation / recipient helpers
# ---------------------------------------------------------------------------
def find_missing_personalisation(personalisation: Dict[str, Any]) -> Optional[str]:
    for key, value in personalisation.items():
        if value is None or str(value).strip() == "":
            return key
    return None


def parse_recipients(value: str) -> List[str]:
    if not value:
        return []
    parts = re.split(r"[;,]", value)
    return [part.strip() for part in parts if part.strip()]


# ---------------------------------------------------------------------------
# Send result persistence
# ---------------------------------------------------------------------------
# Re-exported from app.ui.artifacts, which owns it so app.ui.state can import it
# without a cycle through this module.


def write_send_results(prefix: str, payload: Dict[str, Any], directory: str = SEND_RESULTS_DIR) -> str:
    """Write a send/bulk-send result payload to a timestamped JSON file.

    Returns the path of the written file.
    """
    return write_json_artifact(prefix, payload, directory)
