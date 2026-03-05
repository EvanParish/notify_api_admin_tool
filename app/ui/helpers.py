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

    return ui.button("Export CSV", icon="download", on_click=handle_export).props(
        "flat dense"
    )


# ---------------------------------------------------------------------------
# Copyable cell slots
# ---------------------------------------------------------------------------
COPYABLE_FIELDS = (
    "id",
    "email_address",
    "key_name",
    "name",
    "service_id",
    "sms_sender",
)
COPYABLE_CELL_SLOT = """
<q-td :props="props">
  <span
    class="cursor-pointer text-primary"
    title="Click to copy"
    @click="$parent.$emit('copy', props.value)"
  >{{ props.value }}</span>
</q-td>
"""


def copy_to_clipboard(text: Any) -> None:
    value = "" if text is None else str(text)
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
        table.on("copy", lambda e: copy_to_clipboard(e.args))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def format_environment(value: Optional[str]) -> str:
    return value or "unknown"


def format_service_label(service) -> str:
    return f"{service.name} ({format_environment(service.environment)})"


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
