"""Pure UI helper utilities.

These functions have no dependency on ``app.ui.state`` globals — they accept
all needed data as parameters or access only NiceGUI/stdlib APIs.
"""

from __future__ import annotations

import csv
import functools
import inspect
import io
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from nicegui import ui
from sqlalchemy import inspect as sa_inspect

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


_CSV_CONTROL_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
# Everything else an editor, JS engine, or ``str.splitlines`` treats as a line break:
# VT, FF, FS/GS/RS, NEL, LINE SEPARATOR, PARAGRAPH SEPARATOR. These arrive in template
# content pasted from Word/web pages. ``csv.writer`` does not quote them, so left raw
# they split a record in VSCode and in any splitlines()-based reader.
# Encoded as ``\uXXXX`` so they still round-trip.
_CSV_UNICODE_BREAKS = "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
_CSV_CONTROL_RE = re.compile("[\\\\\n\r\t" + _CSV_UNICODE_BREAKS + "]")
_CSV_UNESCAPE_RE = re.compile(r"\\(?:([\\nrt])|u([0-9a-fA-F]{4}))")
_CSV_UNESCAPES = {"\\": "\\", "n": "\n", "r": "\r", "t": "\t"}


def _escape_char(ch: str) -> str:
    return _CSV_CONTROL_ESCAPES.get(ch) or f"\\u{ord(ch):04x}"


def _unescape_match(m: "re.Match[str]") -> str:
    return _CSV_UNESCAPES[m.group(1)] if m.group(1) else chr(int(m.group(2), 16))


def escape_csv_text(value: str) -> str:
    """Encode a string so it occupies one physical CSV line.

    CRLF and lone CR are normalized to LF first, then ``\\`` -> ``\\\\``, LF -> ``\\n``,
    tab -> ``\\t``, and other Unicode line breaks (LS, PS, NEL, VT, FF, FS/GS/RS) ->
    ``\\uXXXX``. Backslash is escaped in the same pass, so content that already contains
    a literal ``\\n`` or ``\\u2028`` round-trips exactly through ``unescape_csv_text``.
    """
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return _CSV_CONTROL_RE.sub(lambda m: _escape_char(m.group(0)), value)


def unescape_csv_text(value: str) -> str:
    """Inverse of ``escape_csv_text`` (for anyone re-importing an export)."""
    return _CSV_UNESCAPE_RE.sub(_unescape_match, value)


def _escape_csv_cell(value: Any) -> Any:
    """Make a string cell single-line, then neutralize formula injection.

    Newline/tab escaping runs first, so a leading tab or CR (which some spreadsheets
    also treat as a formula trigger) becomes literal ``\\t``/``\\n`` text.

    Formula injection is neutralized by prefixing a single quote.

    Only ``str`` values are touched. The quote is what every spreadsheet reads as "the
    rest of this cell is literal text", and it is the standard mitigation; the cost is
    that a genuinely negative number arriving as the string ``-5`` exports as ``'-5``.
    That trade is correct for this tool: no column here is numeric, and a mangled cell is
    recoverable while an executed formula is not.
    """
    if not isinstance(value, str):
        return value
    value = escape_csv_text(value)
    if value.startswith(CSV_FORMULA_PREFIXES):
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


# Columns that must never leave the app in an export. Matching is by exact name OR by
# substring pattern, so a new column such as ``webhook_token`` is caught without an edit
# here. This is a denylist and therefore fails open for a secret column whose name gives
# no hint; ``tests`` pin the known sensitive columns so a rename is noticed.
EXPORT_DENYLIST_COLUMNS = frozenset(
    {
        "key_secret",  # local_api_keys: usable API secret (Fernet ciphertext at rest)
        "current_session_id",  # users: live session identifier
        "auth_parameter",  # inbound_numbers: provider auth credential reference
    }
)
EXPORT_DENYLIST_PATTERN = re.compile(
    r"(secret|token|password(?!_changed)|passwd|bearer|session_id|auth_param|private_key|credential)",
    re.IGNORECASE,
)


def is_export_denied(column: str) -> bool:
    return column in EXPORT_DENYLIST_COLUMNS or bool(EXPORT_DENYLIST_PATTERN.search(column))


def _export_value(value: Any) -> Any:
    """Normalize a raw DB value for CSV: JSON-encode containers, keep scalars as-is."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)
    if value is None:
        return ""
    return value


def model_to_export_dict(obj: Any, **extra: Any) -> Dict[str, Any]:
    """Return every mapped DB column of an ORM instance, minus denylisted columns.

    ``extra`` adds resolved/derived fields (e.g. ``service_name``) after the raw columns.
    Denylisted names are also stripped from ``extra`` so a caller cannot bypass the list.
    """
    data: Dict[str, Any] = {}
    state = sa_inspect(obj, raiseerr=False)
    if state is not None:
        keys = [attr.key for attr in state.mapper.column_attrs]
    else:  # non-ORM object (e.g. test doubles): use its public attributes
        keys = [k for k in vars(obj) if not k.startswith("_")]
    for key in keys:
        if not is_export_denied(key):
            data[key] = getattr(obj, key)
    for key, value in extra.items():
        if not is_export_denied(key):
            data[key] = value
    return data


_HEADER_ACRONYMS = {"id": "ID", "url": "URL", "sms": "SMS", "api": "API", "va": "VA"}


def humanize_column(key: str) -> str:
    """``created_by_email`` -> ``Created By Email``; ``service_id`` -> ``Service ID``."""
    return " ".join(_HEADER_ACRONYMS.get(part, part.capitalize()) for part in key.split("_") if part)


def dicts_to_csv(rows: List[Dict[str, Any]]) -> str:
    """Convert full export dicts to CSV. Header is the ordered union of all keys,
    humanized (see ``unescape_csv_text`` for decoding cell values on re-import)."""
    fields: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen and not key.startswith("_") and not is_export_denied(key):
                seen.add(key)
                fields.append(key)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([humanize_column(f) for f in fields])
    for row in rows:
        writer.writerow([_escape_csv_cell(_export_value(row.get(field))) for field in fields])
    return output.getvalue()


def _quasar_compare(a: Any, b: Any) -> int:
    """Mirror Quasar QTable's default sortMethod (ascending)."""
    if a is None and b is None:
        return 0  # Quasar returns -1 here (inconsistent comparator); 0 keeps the sort stable
    if a is None:
        return -1
    if b is None:
        return 1
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool):
        return (a > b) - (a < b)
    if isinstance(a, bool) and isinstance(b, bool):
        return int(a) - int(b)
    sa, sb = str(a).lower(), str(b).lower()
    return (sa > sb) - (sa < sb)


def table_sort_order(
    rows: List[Dict[str, Any]], columns: List[Dict[str, Any]], pagination: Optional[Dict[str, Any]]
) -> List[int]:
    """Return row indices in the order the client table currently shows them."""
    indices = list(range(len(rows)))
    sort_by = (pagination or {}).get("sortBy")
    if not sort_by:
        return indices
    col = next((c for c in columns if c.get("name") == sort_by), None)
    if col is None:
        return indices
    field = col["field"]
    direction = -1 if (pagination or {}).get("descending") else 1
    return sorted(
        indices,
        key=functools.cmp_to_key(lambda i, j: direction * _quasar_compare(rows[i].get(field), rows[j].get(field))),
    )


def download_csv(csv_content: str, filename: str) -> None:
    """Trigger a CSV file download in the browser.

    A UTF-8 BOM is prepended: without it Excel on Windows decodes the file in the
    legacy ANSI code page and mangles smart quotes/em-dashes common in templates.
    LibreOffice, Sheets, VSCode and ``pandas.read_csv(encoding="utf-8-sig")`` accept it.

    Sent as raw bytes via ``ui.download`` (binary websocket frame): no base64 inflation,
    no data URL length limits, and no JavaScript string interpolation.
    """
    ui.download(("\ufeff" + csv_content).encode("utf-8"), filename, "text/csv;charset=utf-8")
    safe_notify(f"Exported {filename}", color="green")


def add_export_button(
    rows: List[Dict[str, Any]],
    columns: List[Dict[str, Any]],
    filename: str,
    export_rows: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    table_ref: Optional[Callable[[], Any]] = None,
    sensitive_notice: Optional[str] = None,
) -> ui.button:
    """Add an export CSV button.

    ``export_rows``: zero-arg callable returning full DB records (``model_to_export_dict``),
    parallel to ``rows``. Called only on export, so full records and decrypted PII are not
    built or held in memory for every page render.
    The CSV then has every non-denylisted column untruncated; otherwise it falls back to
    the displayed columns. ``export_rows`` stays server-side.

    ``table_ref``: zero-arg callable returning the ``ui.table`` (late-bound, since the
    table is usually created after the button). Its synced ``pagination`` sort is applied.

    ``sensitive_notice``: if set, the user must confirm before plaintext PII is written.
    """

    def build_csv() -> str:
        table = table_ref() if table_ref else None
        pagination = getattr(table, "pagination", None) if table is not None else None
        order = table_sort_order(rows, columns, pagination if isinstance(pagination, dict) else None)
        if export_rows is not None:
            full = export_rows()
            if len(full) != len(rows):  # not parallel: cannot map the sort safely
                logger.warning("export_rows/rows length mismatch for %s; exporting unsorted", filename)
                return dicts_to_csv(full)
            return dicts_to_csv([full[i] for i in order])
        return rows_to_csv([rows[i] for i in order], columns)

    def do_export() -> None:
        download_csv(build_csv(), filename)

    def handle_export() -> None:
        if not sensitive_notice:
            do_export()
            return
        with ui.dialog() as dialog, ui.card():
            ui.label("This export contains sensitive data").classes("text-lg font-bold")
            ui.label(sensitive_notice)
            ui.label("The file is unencrypted. Store it only in approved locations and delete it when done.").classes(
                "text-sm"
            )
            with ui.row():

                def confirm() -> None:
                    dialog.close()
                    do_export()

                ui.button("Export", on_click=confirm, color="warning")
                ui.button("Cancel", on_click=dialog.close)
        dialog.open()

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
