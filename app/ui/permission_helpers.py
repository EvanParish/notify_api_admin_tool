"""Pure helpers for service permission editing: risk classification, diffing, and audit.

Mirrors the permission values documented in the vanotify-team runbook
``Support/runbooks/service-permissions-update.md``. Deliberately imports no NiceGUI so
every function here is directly unit-testable.

The single fact that shapes this whole module: ``POST /service/{id}`` with a
``permissions`` array REPLACES the service's entire permission set. It is not additive.
Any value omitted from the array is removed from the service.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from app.ui.artifacts import write_json_artifact
from app.ui.http_errors import redact_error_message

# Permission values documented by the runbook, in display order.
# The runbook's worked examples also use "letter", which is absent from its own
# valid-values table; the table is treated as authoritative. Values the API returns but
# that are missing here are preserved rather than dropped -- see build_permission_options.
KNOWN_PERMISSION_LABELS = {
    "email": "Email notifications",
    "sms": "SMS notifications",
    "push": "Push notifications",
    "international_sms": "International SMS",
    "inbound_sms": "Inbound SMS",
    "schedule_notifications": "Scheduled notifications",
}
KNOWN_PERMISSIONS = tuple(KNOWN_PERMISSION_LABELS)

# Accepted by the schema but not actually implemented by the API.
UNSUPPORTED_PERMISSIONS = frozenset({"schedule_notifications"})


def is_protected_environment(env_name: str | None, non_production_environments: Collection[str]) -> bool:
    """Return True when *env_name* should be treated as production.

    Production-ness is DECLARED, never inferred. An environment is non-production only if
    its name appears in *non_production_environments* (see
    ``AppConfig.non_production_environments``, populated from
    ``NON_PRODUCTION_ENVIRONMENTS``); everything else is production.

    This replaces an earlier heuristic that read the base URL. That heuristic could not
    work: VA engineers reach GovCloud production through SSH, ``kubectl port-forward``, or
    an SSM tunnel, so the production API answers on ``localhost``. A hostname cannot see
    through a tunnel, and every version of the heuristic failed OPEN -- silently deleting
    the entire CRITICAL confirmation gate for a live Veteran-facing service.

    Fails closed in every direction: an unknown name, a newly added environment, a typo,
    a blank name and ``None`` all return True. Matching is exact (after trimming and
    lowercasing), not substring, so ``development-2`` is production until someone says
    otherwise.

    ``use_mock`` is deliberately NOT a parameter. With an allowlist a default install in
    mock mode has ``development`` on the list and therefore stays low-friction, while an
    environment keyed ``production`` stays protected -- which keeps the CRITICAL path
    rehearsable in mock mode instead of unreachable.
    """
    name = (env_name or "").strip().lower()
    if not name:
        return True
    return name not in non_production_environments


@dataclass(frozen=True)
class PermissionOption:
    """One checkbox in the permission editor."""

    value: str
    label: str
    enabled: bool  # currently held by the service
    known: bool  # documented in KNOWN_PERMISSION_LABELS
    unsupported: bool  # accepted by the schema but not implemented by the API


def normalize_permissions(values: Sequence[Any] | None, *, strict: bool = False) -> list[str]:
    """Order-preserving dedupe of non-empty string permission values.

    Values are NOT lowercased. Permission names are case-sensitive, and normalizing the
    case of a value this tool does not recognize would corrupt it on the way back out.

    Callers must pass a re-iterable sequence, not a generator or a string. Several
    callers normalize the same input more than once, so an exhausted iterator would
    silently yield ``[]`` -- which reads as "no permissions" and would drop the whole set
    on submit. A ``str`` is the sharper hazard: it satisfies ``Sequence``, so
    ``normalize_permissions("email")`` would return ``['e', 'm', 'a', 'i', 'l']``.
    ``services.permissions`` is stored as a JSON string and :func:`format_permissions_display`
    consumes exactly that string, so the two are easy to confuse. Both cases raise rather
    than returning ``[]``: swallowing a bad input reproduces the same fail-open shape one
    layer up, where it would surface as a fully-populated but entirely wrong diff.

    *strict* controls what happens to an ELEMENT that this function would otherwise
    silently alter or discard: a non-string, a string that strips to nothing, and a string
    with surrounding whitespace. Duplicates are still collapsed in both modes -- that is
    the only alteration here that cannot destroy anything, because every consumer
    (:func:`diff_permissions`, :func:`permissions_equal`) is set-based.

    Use ``strict=True`` on every READ path. Altering an element there is fail-open in the
    worst possible way, and all three cases have the same shape. A live read of
    ``["email", "sms", 0]`` yields ``before = ("email", "sms")``, so the ``0`` appears in
    no diff entry, earns no acknowledgement, and is destroyed by the replace-everything
    POST -- with LESS friction than a change the operator can actually see. ``["email",
    ""]`` does the same via the empty-string branch. ``["email", " sms "]`` is subtler and
    worse: the snapshot holds ``"sms"``, the diff reports nothing changed, the POST sends
    ``["email", "sms"]``, the service's real ``" sms "`` is deleted and replaced, and the
    verification read normalizes the new value back to ``"sms"`` and reports success. The
    asymmetry gave the first case away: a non-list ``permissions`` hard-stopped loudly
    while a list containing garbage was quietly cleaned up.

    The default stays lenient for the WRITE path, where the values are our own checkbox
    values and there is nothing untrusted to surface.
    """
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("normalize_permissions requires a re-iterable, non-string sequence")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        # The API response is untrusted JSON, so a non-string here is possible and must
        # not reach the payload builder as a checkbox value.
        if not isinstance(value, str):
            if strict:
                raise TypeError(
                    f"permission value {value!r} is {type(value).__name__}, not str; "
                    "refusing to drop it silently"
                )
            continue
        text = value.strip()
        if not text:
            if strict:
                raise TypeError(
                    f"permission value {value!r} is empty or whitespace; refusing to drop it silently"
                )
            continue
        if strict and text != value:
            raise TypeError(
                f"permission value {value!r} has surrounding whitespace; refusing to rewrite it "
                f"to {text!r} silently"
            )
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


class ServiceReadError(Exception):
    """A live service read that must not be used to build, gate, or verify a change.

    Distinct from a transport failure. Every caller already has an ``except Exception``
    that reports "could not reach the API"; reporting a well-formed HTTP 200 carrying an
    unusable body under that message sends the operator to look at the network.
    """


# A service id is interpolated straight into ``{base}/service/{id}`` by every method on
# HttpNotificationAPI. It arrives from the local sync cache, which is populated verbatim
# from the API's own /service response, so it is API-controlled data reaching a URL.
# httpx resolves dot segments against the base URL, so an id of "../../organisation/x"
# does not 404 -- it silently retargets the request, and for the POST that means the
# permission body lands on an endpoint nobody chose.
#
# The pattern is a single safe URL path segment: no "/", no "%", no "?", no "#", no ".",
# no whitespace, and a leading alphanumeric so bare ".." and "-flag" cannot pass.
#
# Deliberately NOT ``uuid.UUID``, although every id in a real environment is a UUID.
# MockNotificationAPI issues "svc-1" (app/api_client.py:744), and a UUID check would make
# the permission editor unreachable in mock mode -- the one place the CRITICAL path can be
# rehearsed without touching a live service. A UUID check is a domain-shape check; this is
# the security check, and it is the security check that has to hold.
SERVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def is_safe_service_id(value: Any) -> bool:
    """Whether *value* is safe to interpolate into a URL path segment."""
    return isinstance(value, str) and SERVICE_ID_PATTERN.match(value) is not None


def validate_service_read(body: Any, service_id: str) -> list[str]:
    """Return the permission set from a live read of *service_id*, or raise.

    The single gate every read of a service must pass before its contents are allowed to
    influence anything. There are three live reads in the permission dialog -- open,
    pre-POST concurrency re-check, and post-POST verification -- and each one of them
    fails open in a different way when it trusts the body.

    ``"permissions" not in body`` is the reason this exists. ``live.get("permissions")``
    returns ``None`` for a missing key, ``normalize_permissions(None)`` returns ``[]``, and
    ``[]`` is indistinguishable from a service that genuinely holds nothing. A production
    service holding ``["email", "sms", "international_sms"]`` whose response omits the key
    presents as empty, so ticking one box computes ``removed=()`` -- tier ELEVATED, no
    challenge, no acknowledgements -- and the replace-everything POST destroys the other
    two. The verification read then agrees, because by then the service really does hold
    only ``email``, and the operator is shown a green "updated and verified" while the
    audit record's ``before`` -- the rollback value -- says ``[]``. Absence of the key is
    not knowledge of an empty set, and the two must never collapse into the same value.

    The id check was previously applied only at dialog open, the one read that cannot
    destroy anything. ``get_service`` returns ``{}`` for a non-dict body, so an unparseable
    verification response yields ``verified=[]``, and clearing every permission -- the most
    destructive operation this tool performs -- has ``diff.after == ()``. The two compare
    equal and the change is recorded ``outcome: "success"`` on a response nobody could
    parse. The same ``verified`` is written to the local cache, so a body belonging to
    another service overwrites this service's cached permissions.

    Normalization is ``strict=True`` because this is a read path: a non-string element
    dropped here appears in no diff entry, earns no acknowledgement, and is destroyed by
    the POST with less friction than a change the operator can see.
    """
    if not isinstance(body, dict):
        raise ServiceReadError(f"the API returned {type(body).__name__}, not a service object.")
    if str(body.get("id")) != str(service_id):
        raise ServiceReadError(f"the API returned service {body.get('id')} for a request for {service_id}.")
    if "permissions" not in body:
        raise ServiceReadError(
            "the response carries no 'permissions' key, so the service's current permission set is unknown."
        )
    raw = body["permissions"]
    try:
        return normalize_permissions(raw, strict=True)
    except TypeError as exc:
        raise ServiceReadError(
            f"the API returned a malformed permissions value (got {type(raw).__name__}): {exc}."
        ) from exc


def build_permission_options(live_permissions: Sequence[Any] | None) -> list[PermissionOption]:
    """Every known permission, plus any live value this tool does not recognize.

    The union matters: the update endpoint replaces the entire set, so a permission held
    by the service but absent from this list would be silently deleted on submit.
    Unrecognized values render enabled and flagged rather than being hidden or locked.

    Normalizes with ``strict=True``: the argument is a live read, and a non-string element
    dropped here would be omitted from the union and therefore from the POST -- the exact
    silent deletion the union exists to prevent.
    """
    live = normalize_permissions(live_permissions, strict=True)
    live_set = set(live)
    options = [
        PermissionOption(
            value=value,
            label=KNOWN_PERMISSION_LABELS[value],
            enabled=value in live_set,
            known=True,
            unsupported=value in UNSUPPORTED_PERMISSIONS,
        )
        for value in KNOWN_PERMISSIONS
    ]
    # Unknown values carry no label and no unsupported claim: this tool has no basis for
    # asserting anything about a value it does not document.
    options.extend(
        PermissionOption(value=value, label=value, enabled=True, known=False, unsupported=False)
        for value in live
        if value not in KNOWN_PERMISSION_LABELS
    )
    return options


def format_permission_option_label(option: PermissionOption) -> str:
    """Checkbox caption for *option*, flagged when the value cannot be taken at face value.

    "Unrecognized" outranks "unsupported": this tool documents no claim at all about a
    value it does not recognize, so asserting the API does not implement it would be a
    claim it cannot back. ``build_permission_options`` never produces that combination
    today, but the precedence is stated here rather than left to the caller's ordering.
    """
    if not option.known:
        return f"{option.label}  (unrecognized -- not documented by the API)"
    if option.unsupported:
        return f"{option.label}  (currently unsupported by the API)"
    return option.label


@dataclass(frozen=True)
class PermissionDiff:
    """Normalized before/after permission sets and the delta between them."""

    before: tuple[str, ...]
    after: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed


def permissions_equal(current: Sequence[Any] | None, other: Sequence[Any] | None) -> bool:
    """Compare two permission sets the way :class:`PermissionDiff` defines equality.

    Order is not significant, so this is a set comparison and not a list comparison. Both
    the staleness re-check and the post-update verification depend on that: the API is
    under no obligation to return permissions in the order they were sent, and a list
    comparison would abort every legitimate change the moment it reordered them.

    Inputs go through :func:`normalize_permissions`, so this fails closed on a ``str`` for
    the same reason that function does -- ``permissions_equal("email", ["email"])`` must
    raise rather than quietly report a difference between one permission and five
    single-character ones.

    Both sides are read results -- a live re-read against an approved snapshot, or a
    post-update verification read against the expected set -- so normalization is
    ``strict=True``. Filtering a non-string element out of one side would make the
    comparison report equality between sets that are not equal, which is precisely the
    staleness check and the verification check both failing open.
    """
    return set(normalize_permissions(current, strict=True)) == set(normalize_permissions(other, strict=True))


class ConfirmationTier(IntEnum):
    """How much friction a change earns. Ordered, so ``tier >= CRITICAL`` is meaningful."""

    NONE = 0  # nothing to confirm
    STANDARD = 1  # non-production, additive only
    ELEVATED = 2  # non-production removal, or production addition
    CRITICAL = 3  # production removal -- typed challenge plus acknowledgements


def diff_permissions(current: Sequence[Any] | None, proposed: Sequence[Any] | None) -> PermissionDiff:
    """Compare two permission sets. Order is not significant to equality.

    Lenient normalization on both sides, unlike :func:`permissions_equal`. This runs on
    every checkbox toggle to re-render the live diff, and both arguments are already ours:
    *current* is the snapshot the caller normalized when the dialog opened, *proposed* is
    the set of ticked checkbox values. There is nothing untrusted left to surface here,
    and raising on a keystroke would break the dialog rather than protect anything.
    """
    before = normalize_permissions(current)
    after = normalize_permissions(proposed)
    before_set = set(before)
    after_set = set(after)
    return PermissionDiff(
        before=tuple(before),
        after=tuple(after),
        added=tuple(v for v in after if v not in before_set),
        removed=tuple(v for v in before if v not in after_set),
        unchanged=tuple(v for v in before if v in after_set),
    )


def classify_change(diff: PermissionDiff, is_protected: bool) -> ConfirmationTier:
    """Decide the confirmation tier for *diff* in an environment of the given risk.

    Removal is the irreversible-in-effect direction -- it stops live traffic -- so it
    outranks addition at every risk level, and a removal in production is the only case
    that earns the typed challenge.
    """
    if diff.is_empty:
        return ConfirmationTier.NONE
    if is_protected and diff.removed:
        return ConfirmationTier.CRITICAL
    if diff.removed or is_protected:
        return ConfirmationTier.ELEVATED
    return ConfirmationTier.STANDARD


def validate_typed_challenge(typed: str | None, expected_service_name: str | None) -> bool:
    """Exact match after trimming surrounding whitespace. Case-sensitive by design.

    The point of the challenge is to force the operator to read which service they are
    changing, so a case-insensitive or fuzzy match would defeat it. Returns False when the
    expected name is unknown; otherwise a service with a blank name would accept an empty
    confirmation.
    """
    expected = (expected_service_name or "").strip()
    if not expected:
        return False
    return (typed or "").strip() == expected


def requires_typed_challenge(tier: ConfirmationTier) -> bool:
    """Whether *tier* earns the typed-name challenge and per-value acknowledgements.

    The single source of truth for that question. The dialog needs the same answer three
    times -- to decide whether to render the challenge, whether to enforce it, and whether
    to route through the final confirmation -- and three hand-written copies of
    ``tier >= CRITICAL`` is how one of them eventually drifts. Written as ``>=`` rather
    than ``==`` so a tier added above CRITICAL inherits the friction instead of falling
    through it.
    """
    return tier >= ConfirmationTier.CRITICAL


def is_challenge_satisfied(
    tier: ConfirmationTier,
    *,
    typed_name: str | None,
    expected_name: str | None,
    acknowledged: Collection[str],
    diff: PermissionDiff,
) -> bool:
    """Whether a change at *tier* has cleared its confirmation requirements.

    The core safety predicate of the permission editor. Below CRITICAL there is nothing to
    clear, so it returns True -- a sub-critical tier renders no challenge widgets at all,
    and an unsatisfiable answer here would deadlock every non-production change. At
    CRITICAL both halves are required: the service name typed exactly, and an
    acknowledgement for every value being removed.

    *acknowledged* is a plain collection of the permission values whose boxes are ticked,
    not the widgets themselves. That keeps this module free of NiceGUI and therefore
    directly unit-testable, which is the whole point of extracting the predicate.

    The acknowledgement test is a subset test on identity, never a count: ticking a box
    for a value that is not being removed says nothing about the value that is. Surplus
    acknowledgements left over from an earlier selection are harmless and must not block a
    fully-acknowledged change.

    Keyword-only past *tier*: ``typed_name`` and ``expected_name`` are adjacent strings
    and transposing them would compare the expected name against itself in the cases that
    matter, turning the gate into decoration.
    """
    if not requires_typed_challenge(tier):
        return True
    if not validate_typed_challenge(typed_name, expected_name):
        return False
    return set(diff.removed).issubset(set(acknowledged))


def build_confirm_button_label(diff: PermissionDiff) -> str:
    """Label the submit button with the counts, so the button itself states the impact."""
    parts: list[str] = []
    # Removal is named first: it is the destructive half, and the label is the last thing
    # read before the click.
    if diff.removed:
        parts.append(f"Remove {len(diff.removed)}")
    if diff.added:
        parts.append(f"Add {len(diff.added)}")
    return ", ".join(parts) if parts else "No changes"


def unrecoverable_removals(diff: PermissionDiff) -> tuple[str, ...]:
    """Removed values this tool cannot put back.

    The design document called rollback "running the editor again with the captured
    array". That is false for any value outside :data:`KNOWN_PERMISSIONS`. Unrecognized
    values only appear as checkboxes because :func:`build_permission_options` unions them
    in from the LIVE read; once removed they are no longer live, so the next read does not
    contain them, no checkbox is offered, and there is deliberately no free-text entry to
    type one back in. The runbook's own worked examples use ``letter``, which is exactly
    such a value.

    The audit record still holds the array, so a rollback is possible — through Postman,
    not through this tool. Saying so before the click is the whole point.
    """
    return tuple(value for value in diff.removed if value not in KNOWN_PERMISSION_LABELS)


def build_challenge_hint(service_name: str | None) -> str:
    """The instruction shown above the typed-name input at CRITICAL.

    A service with no name in the API response makes the challenge UNSATISFIABLE:
    :func:`validate_typed_challenge` correctly refuses a blank expected name, so submit
    stays disabled forever while the hint reads "Type the service name exactly: " and
    offers the operator nothing to act on. Correct, and undiagnosable. Say so instead.
    """
    expected = (service_name or "").strip()
    if not expected:
        return (
            "This service has no name in the API response, so the typed-name challenge "
            "cannot be satisfied and this removal cannot be completed here. Use the "
            "runbook's Postman flow, or fix the service name first."
        )
    return f"This REMOVES permissions from a PRODUCTION service. Type the service name exactly: {expected}"


def build_confirm_button_color(tier: ConfirmationTier) -> str:
    """Quasar colour for the submit button at *tier*.

    The button was ``negative`` at every tier, so an additive change in development looked
    identical to a production removal. Red at all tiers is red at none. T1 is ordinary,
    T2 and T3 are red, per the spec's tier table.
    """
    if tier >= ConfirmationTier.ELEVATED:
        return "negative"
    if tier == ConfirmationTier.STANDARD:
        return "primary"
    return "grey"


def build_confirmation_text(
    diff: PermissionDiff,
    service_name: str,
    environment: str,
    tier: ConfirmationTier,
    *,
    base_url: str | None,
) -> str:
    """Human-readable summary of the pending change, shown in the confirmation dialogs.

    *base_url* closes a residual fail-open in the risk classification. Protection is keyed
    on the environment NAME against the configured allowlist, but the name-to-URL binding
    lives in ``settings.base_url_{env}``, a database row editable from the Settings page.
    Point ``dev`` at ``https://api.notifications.va.gov`` and it is still on the allowlist,
    so ``is_env_protected`` is False, the CRITICAL gate disappears, and the audit record
    says ``protected: false`` -- the same outcome as the URL-heuristic bug the allowlist
    replaced, reached by a different route.

    Auto-escalating a listed environment that carries a URL override was considered and
    rejected: pointing ``dev`` at a local instance is routine, and escalating it recreates
    the friction fatigue the tiering exists to avoid. The mitigation is visibility instead
    of enforcement -- the operator is shown where they are actually pointing at the moment
    of decision, in both the inline diff panel and the final confirmation dialog.

    Keyword-only and REQUIRED, with no default. A default would let a call site quietly
    omit the one field that makes the misconfiguration visible, which is exactly the
    failure this parameter exists to prevent.
    """
    if diff.is_empty:
        return "No permission changes selected."
    lines = [f"{service_name} in {environment} -> {base_url or '(base URL not configured)'}"]
    if tier >= ConfirmationTier.CRITICAL:
        lines.append("PRODUCTION PERMISSION REMOVAL")
    if diff.removed:
        lines.append("Removing: " + ", ".join(diff.removed))
    unrecoverable = unrecoverable_removals(diff)
    if unrecoverable:
        lines.append(
            "WARNING: " + ", ".join(unrecoverable) + " cannot be re-added through this tool. "
            "There is no checkbox for a value the service does not currently hold and no free-text "
            "entry, so restoring it needs the runbook's Postman flow."
        )
    if diff.added:
        lines.append("Adding: " + ", ".join(diff.added))
    # Spelled out rather than left blank: an empty "Resulting set:" line reads like a
    # rendering bug, not like the service losing every permission it has.
    resulting = ", ".join(diff.after) if diff.after else "(none -- all permissions removed)"
    lines.append(f"Resulting set: {resulting}")
    return "\n".join(lines)


# Audit records for permission changes. Local-only (data/ is gitignored) and free of PII,
# unlike data/send_response/, which holds recipient addresses.
#
# Scope of the credential-free claim: the eleven structured fields carry no credentials by
# construction. The twelfth, `error`, is arbitrary caller-supplied text, so
# build_audit_payload runs it through http_errors.redact_error_message -- which redacts
# only messages mentioning a name in SENSITIVE_ERROR_FIELDS. That tuple is the exact scope
# of the guarantee, not a general secret scrubber. If notification-api starts echoing a
# credential under a name that is not in it, widen SENSITIVE_ERROR_FIELDS or these records
# silently stop being credential-free.
PERMISSION_CHANGES_DIR = "data/permission_changes"


def build_audit_payload(
    *,
    environment: str,
    base_url: str | None,
    service_id: str,
    service_name: str,
    diff: PermissionDiff,
    protected: bool,
    outcome: str = "attempted",
    verified: Sequence[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build the rollback/audit record for a permission change.

    ``before`` is the value a rollback restores. ``outcome`` is one of ``attempted``,
    ``success``, ``mismatch``, or ``error``. This record is written with ``attempted``
    BEFORE the update is sent, so the rollback value survives a crash or an ambiguous 500,
    then rewritten in place with the verified result.

    ``base_url`` records which host was actually targeted. ``environment`` alone does not:
    it is a local key whose URL binding is a mutable database row, so a record naming only
    ``dev`` cannot distinguish a change against a local instance from one against
    production. Required and untyped-defaulted for the same reason as in
    :func:`build_confirmation_text`.

    Keyword-only: the parameters include several adjacent strings, and a transposed
    ``service_id``/``service_name`` would misattribute the record a rollback depends on.
    """
    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "environment": environment,
        "base_url": base_url,
        "protected": protected,
        "service_id": service_id,
        "service_name": service_name,
        # Materialized as lists, not the diff's tuples: json.dump renders a tuple as an
        # array anyway, but the record is also compared and re-read in tests and rollback.
        "before": list(diff.before),
        "after": list(diff.after),
        "added": list(diff.added),
        "removed": list(diff.removed),
        "outcome": outcome,
        # None, not [], when unverified: an empty list is a legitimate verified result
        # (every permission removed) and must not be confused with "never checked".
        "verified": list(verified) if verified is not None else None,
        # Redacted here, not at the call site. This record is written to disk before the
        # destructive call, and notification-api echoes submitted values back in
        # jsonschema 400s, so a caller writing error=str(exc) would otherwise persist a
        # live credential. Making the guarantee structural means it does not depend on
        # every future dialog author remembering the convention.
        "error": redact_error_message(error) if error is not None else None,
    }


def write_permission_audit(payload: dict[str, Any], directory: str = PERMISSION_CHANGES_DIR) -> str:
    """Persist an audit record and return its path."""
    return write_json_artifact("permission_change", payload, directory)


def format_permissions_display(raw: str | None) -> str:
    """Render the JSON-encoded ``services.permissions`` column as a readable list.

    Never truncates. Truncated permission text sitting next to a destructive editor is a
    misread waiting to happen.
    """
    if not raw:
        return ""
    try:
        values = json.loads(raw)
    except ValueError:
        # ValueError, not TypeError: past the guard above *raw* is a str, and json.loads
        # only raises TypeError for non-str/bytes input. json.JSONDecodeError subclasses
        # ValueError, so the reachable case stays covered.
        return raw
    if not isinstance(values, list):
        return raw
    return ", ".join(str(value) for value in values)
