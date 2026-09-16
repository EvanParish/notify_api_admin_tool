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
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nicegui import ui

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
def rows_to_csv(rows: List[Dict[str, Any]], columns: List[Dict[str, Any]]) -> str:
    """Convert table rows to CSV string using column definitions."""
    output = io.StringIO()
    # Get field names from columns, excluding internal fields like _row_key
    fields = [col["field"] for col in columns if not col["field"].startswith("_")]
    labels = [col["label"] for col in columns if not col["field"].startswith("_")]

    writer = csv.writer(output)
    writer.writerow(labels)
    for row in rows:
        writer.writerow([row.get(field, "") for field in fields])

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
SEND_RESULTS_DIR = "data/send_response"


def write_send_results(prefix: str, payload: Dict[str, Any], directory: str = SEND_RESULTS_DIR) -> str:
    """Write a send/bulk-send result payload to a timestamped JSON file.

    Returns the path of the written file.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    os.makedirs(directory, exist_ok=True)
    file_path = os.path.join(directory, f"{prefix}_{timestamp}.json")
    with open(file_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    return file_path
