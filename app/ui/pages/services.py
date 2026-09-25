from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx
from nicegui import ui

from app.repository import (
    count_active_api_keys_by_service,
    count_sms_senders_by_service,
    count_templates_by_service,
    list_services,
    update_service,
)
from app.repository import update_service_permissions as update_service_permissions_cache
from app.ui import state as _st
from app.ui.artifacts import rewrite_json_artifact
from app.ui.helpers import (
    add_copyable_slots,
    add_export_button,
    add_service_context_menu,
    format_environment,
    make_row_key,
    make_sortable,
    refresh_if_needed,
)
from app.ui.http_errors import format_http_error
from app.ui.permission_helpers import (
    ConfirmationTier,
    ServiceReadError,
    build_audit_payload,
    build_challenge_hint,
    build_confirm_button_color,
    build_confirm_button_label,
    build_confirmation_text,
    build_permission_options,
    classify_change,
    diff_permissions,
    format_permission_option_label,
    format_permissions_display,
    is_challenge_satisfied,
    is_safe_service_id,
    permissions_equal,
    requires_typed_challenge,
    validate_service_read,
    write_permission_audit,
)
from app.ui.shell import build_shell, ensure_theme_preference
from app.ui.state import (
    PAGE_RESPONSE_TIMEOUT,
    build_api_client,
    ensure_admin_auth,
    get_raw_base_url,
    get_view_environment,
    handle_unauthorized,
    is_env_protected,
    refresh_status_badge,
)
from app.ui.sync_handlers import handle_entity_sync, handle_full_sync

logger = logging.getLogger(__name__)


async def handle_service_search(value: Optional[str]) -> None:
    _st.service_search_query = (value or "").strip().lower()
    await refresh_if_needed(services_table)


async def handle_service_search_event(e) -> None:
    await handle_service_search(getattr(e, "value", None))


@ui.page("/services", response_timeout=PAGE_RESPONSE_TIMEOUT)
async def services_page() -> None:
    status_badge, sync_label, refresh_button, dark_mode, theme_button = build_shell(
        on_view_env_change=lambda: refresh_if_needed(services_table)
    )
    await ensure_theme_preference(dark_mode, theme_button)

    async def page_refresh():  # pragma: no cover
        await handle_full_sync(status_badge, sync_label)

    async def page_sync_services():  # pragma: no cover
        if await handle_entity_sync(["sync_services"], status_badge, sync_label, "services"):
            await refresh_if_needed(services_table)

    refresh_button.on_click(page_refresh)
    await refresh_status_badge(status_badge)

    selected_service: dict[str, Any] = {}

    with ui.dialog() as edit_dialog, ui.card().classes("p-6 w-full max-w-lg"):
        ui.label("Edit Service Limits").classes("text-md font-semibold")
        selected_service_label = ui.label("")
        edit_message_limit = ui.number(label="Message Limit", min=0, precision=0).classes("w-full")
        edit_rate_limit = ui.number(label="Rate Limit", min=0, precision=0).classes("w-full")
        with ui.row().classes("gap-2"):
            edit_update_button = ui.button("Update Limits", color="primary")
            ui.button("Close", on_click=edit_dialog.close, color="gray")

    def update_edit_fields(svc: dict[str, Any] | None) -> None:  # pragma: no cover
        if not svc:
            selected_service_label.text = "No service selected."
            edit_message_limit.value = None
            edit_rate_limit.value = None
            return
        name = svc.get("name") or svc.get("id")
        selected_service_label.text = f"Selected: {name} ({svc.get('id')})"
        edit_message_limit.value = svc.get("message_limit")
        edit_rate_limit.value = svc.get("rate_limit")

    async def handle_open_edit_dialog() -> None:  # pragma: no cover
        svc = selected_service if selected_service.get("id") else None
        if not svc:
            ui.notify("Select a service from the table first", color="red")
            return
        update_edit_fields(svc)
        edit_dialog.open()

    async def handle_update_service() -> None:  # pragma: no cover
        svc = selected_service if selected_service.get("id") else None
        if not svc:
            ui.notify("Select a service first", color="red")
            return
        service_id = svc.get("id")
        environment = svc.get("environment_value")
        if not (service_id and environment):
            ui.notify("Selected service is missing required details", color="red")
            return
        message_limit = int(edit_message_limit.value) if edit_message_limit.value is not None else None
        rate_limit = int(edit_rate_limit.value) if edit_rate_limit.value is not None else None
        if not await ensure_admin_auth(environment, sync_label):
            return
        api = await build_api_client(environment)
        try:
            await api.update_service(
                service_id=service_id,
                message_limit=message_limit,
                rate_limit=rate_limit,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response and exc.response.status_code == 401:
                handle_unauthorized(sync_label, environment)
                return
            ui.notify(f"Failed to update service: {exc}", color="red")
            return
        except Exception as exc:
            ui.notify(f"Error updating service: {exc}", color="red")
            return
        updated = await update_service(
            service_id=service_id,
            message_limit=message_limit,
            rate_limit=rate_limit,
            environment=environment,
        )
        if updated:
            ui.notify("Service limits updated", color="green")
        else:
            ui.notify(
                "Service limits updated, but cache is missing. Run sync to refresh.",
                color="warning",
            )
        selected_service["message_limit"] = message_limit
        selected_service["rate_limit"] = rate_limit
        update_edit_fields(selected_service if selected_service.get("id") else None)
        edit_dialog.close()
        await refresh_if_needed(services_table)

    edit_update_button.on_click(handle_update_service)

    # ------------------------------------------------------------------
    # Edit Service Permissions
    #
    # POST /service/{id} REPLACES the entire permission set. Every step below
    # (fresh read, diff, tier, concurrency re-check, audit-before-send, verify-after)
    # exists because of that. All logic lives in app/ui/permission_helpers.py; the
    # closures here only wire it to widgets.
    # ------------------------------------------------------------------
    permission_context: dict[str, Any] = {}
    selected_permissions: dict[str, bool] = {}
    acknowledgements: dict[str, Any] = {}
    acknowledged_for: dict[str, Any] = {}
    pending_permission_change: dict[str, Any] = {}
    # Set and cleared synchronously. perms_final_execute_button.disable() is a client
    # round-trip, so two fast clicks both dispatch before the button ever goes grey and
    # produce two concurrent apply_permission_change runs -- two POSTs and two audit files.
    permission_change_in_flight: dict[str, bool] = {}

    with ui.dialog() as perms_dialog, ui.card().classes("p-6 w-full max-w-2xl"):
        ui.label("Edit Service Permissions").classes("text-md font-semibold")
        perms_env_banner = ui.label("").classes("w-full p-2 rounded text-sm")
        perms_service_label = ui.label("").classes("text-sm")
        perms_checkbox_container = ui.column().classes("gap-1")
        ui.separator()
        perms_diff_label = ui.label("").classes("whitespace-pre-wrap text-sm")
        perms_challenge_container = ui.column().classes("gap-2 w-full")
        with perms_challenge_container:
            perms_challenge_hint = ui.label("").classes("text-sm text-red-600 font-semibold")
            perms_challenge_input = ui.input(label="Type the exact service name").classes("w-full")
            perms_ack_container = ui.column().classes("gap-1")
        with ui.row().classes("gap-2"):
            # Colour is driven from the tier by refresh_permission_ui; "grey" is the
            # no-change state the dialog opens in.
            perms_submit_button = ui.button("No changes", color="grey")
            perms_cancel_button = ui.button("Cancel", color="gray")

    with ui.dialog() as perms_final_dialog, ui.card().classes("p-6 w-full max-w-lg"):
        ui.label("Final Confirmation").classes("text-md font-semibold text-red-600")
        perms_final_label = ui.label("").classes("whitespace-pre-wrap text-sm")
        with ui.row().classes("gap-2"):
            perms_final_execute_button = ui.button("Execute Change", color="negative")
            perms_final_cancel_button = ui.button("Cancel", color="gray")

    def current_permission_diff():
        proposed = [value for value, enabled in selected_permissions.items() if enabled]
        return diff_permissions(permission_context.get("snapshot") or [], proposed)

    def rebuild_acknowledgements(removed) -> None:
        # Guarded so a keystroke in the challenge input cannot wipe ticked boxes, and so
        # a checkbox change cannot recurse through refresh_permission_ui.
        if acknowledged_for.get("removed") == removed:
            return
        acknowledged_for["removed"] = removed
        acknowledgements.clear()
        perms_ack_container.clear()
        with perms_ack_container:
            for value in removed:
                box = ui.checkbox(f"I confirm removing '{value}' from production")
                box.on_value_change(lambda _e: refresh_permission_ui())
                acknowledgements[value] = box

    def disarm_challenge() -> None:
        # Both halves are reset on every departure from CRITICAL, so re-entering always
        # re-arms from scratch. Without this, unticking a production permission, ticking
        # its acknowledgement, re-ticking the permission (which drops the tier and stops
        # calling rebuild_acknowledgements) and then unticking it again would find
        # acknowledged_for unchanged, short-circuit the rebuild, and present the gate
        # already satisfied.
        acknowledged_for.clear()
        acknowledgements.clear()
        perms_ack_container.clear()
        # Terminates: BindableProperty.__set__ only fires the change handler when the
        # value actually differs, so the re-entrant refresh_permission_ui this triggers
        # sets "" over "" and stops. Same mechanism relied on by clear_permission_state.
        perms_challenge_input.value = ""

    def ticked_acknowledgements() -> set[str]:
        return {value for value, box in acknowledgements.items() if box.value}

    def challenge_satisfied(diff, tier) -> bool:
        return is_challenge_satisfied(
            tier,
            typed_name=perms_challenge_input.value,
            expected_name=permission_context.get("service_name"),
            acknowledged=ticked_acknowledgements(),
            diff=diff,
        )

    def refresh_permission_ui() -> None:
        diff = current_permission_diff()
        tier = classify_change(diff, bool(permission_context.get("protected", True)))
        perms_diff_label.text = build_confirmation_text(
            diff,
            permission_context.get("service_name", ""),
            permission_context.get("environment", ""),
            tier,
            base_url=permission_context.get("base_url"),
        )
        needs_challenge = requires_typed_challenge(tier)
        perms_challenge_container.set_visibility(needs_challenge)
        if needs_challenge:
            perms_challenge_hint.text = build_challenge_hint(permission_context.get("service_name"))
            rebuild_acknowledgements(diff.removed)
        else:
            disarm_challenge()
        perms_submit_button.text = build_confirm_button_label(diff)
        perms_submit_button.props(f"color={build_confirm_button_color(tier)}")
        if diff.is_empty or not challenge_satisfied(diff, tier):
            perms_submit_button.disable()
        else:
            perms_submit_button.enable()

    def clear_permission_state() -> None:
        permission_context.clear()
        selected_permissions.clear()
        acknowledgements.clear()
        acknowledged_for.clear()
        pending_permission_change.clear()
        perms_challenge_input.value = ""

    def handle_permissions_cancel() -> None:
        clear_permission_state()
        perms_dialog.close()

    async def handle_open_permissions_dialog() -> None:
        svc = selected_service if selected_service.get("id") else None
        if not svc:
            ui.notify("Select a service from the table first", color="red")
            return
        service_id = svc.get("id")
        environment = svc.get("environment_value")
        if not (service_id and environment):
            ui.notify("Selected service is missing required details", color="red")
            return
        # The id is interpolated into the request URL and arrives from the sync cache,
        # i.e. from the API. Checked before the first request, not after.
        if not is_safe_service_id(service_id):
            ui.notify(
                f"Refusing to use service id {service_id!r}: it is not a safe URL path segment. "
                "Re-sync services and report this, the cached id came from the API.",
                color="red",
                timeout=0,
                close_button=True,
            )
            return
        if not await ensure_admin_auth(environment, sync_label):
            return
        api = await build_api_client(environment)
        # Runbook step 2: read the live state. Never build the proposed array from the
        # local cache, which is a possibly-stale sync snapshot.
        try:
            live = await api.get_service(service_id)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 401:
                handle_unauthorized(sync_label, environment)
            elif status == 404:
                ui.notify(
                    f"Service {service_id} was not found in {environment}. "
                    "Reverify the service UUID and the environment.",
                    color="red",
                    timeout=0,
                    close_button=True,
                )
            else:
                ui.notify(f"Failed to read service: {format_http_error(exc)}", color="red")
            return
        except httpx.RequestError as exc:
            ui.notify(f"Could not reach the notification API: {exc}", color="red")
            return
        except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
            ui.notify(f"Error reading service: {exc}", color="red")
            return

        # validate_service_read is the only way a response body is allowed to become a
        # snapshot: right id, "permissions" key actually present, every element a string.
        # Unguarded, the TypeError it can raise would be swallowed by
        # nicegui.events.handle_event and the operator would see the button do nothing.
        try:
            snapshot = validate_service_read(live, service_id)
        except ServiceReadError as exc:
            ui.notify(
                f"Unusable response reading service {service_id}: {exc} Nothing was changed.",
                color="red",
                timeout=0,
                close_button=True,
            )
            return
        protected = await is_env_protected(environment)
        # Stashed at open, not re-resolved later, so the inline panel and the final dialog
        # cannot disagree about where the request is going.
        base_url = await get_raw_base_url(environment)
        clear_permission_state()
        permission_context.update(
            {
                "environment": environment,
                "base_url": base_url,
                "service_id": service_id,
                "service_name": live.get("name") or svc.get("name") or "",
                "protected": protected,
                "snapshot": snapshot,
            }
        )

        perms_ack_container.clear()
        perms_checkbox_container.clear()
        with perms_checkbox_container:
            for option in build_permission_options(snapshot):
                selected_permissions[option.value] = option.enabled
                box = ui.checkbox(format_permission_option_label(option), value=option.enabled)
                box.on_value_change(
                    lambda e, value=option.value: (
                        selected_permissions.__setitem__(value, bool(e.value)),
                        refresh_permission_ui(),
                    )
                )

        perms_service_label.text = f"{permission_context['service_name']} ({service_id})"
        if protected:
            perms_env_banner.text = f"PRODUCTION ENVIRONMENT -- {environment}"
            perms_env_banner.classes(replace="w-full p-2 rounded text-sm bg-red-600 text-white font-bold")
        else:
            perms_env_banner.text = f"Environment: {environment}"
            perms_env_banner.classes(replace="w-full p-2 rounded text-sm bg-gray-200 text-gray-800")
        refresh_permission_ui()
        perms_dialog.open()

    def finalize_permission_audit(path, diff, outcome, verified, error, context) -> None:
        # *context* is a snapshot taken before the first await, never the live
        # permission_context. See apply_permission_change.
        rewrite_json_artifact(
            path,
            build_audit_payload(
                environment=context.get("environment", ""),
                base_url=context.get("base_url"),
                service_id=context.get("service_id", ""),
                service_name=context.get("service_name", ""),
                diff=diff,
                # Absent means unknown, and this is a safety predicate, so unknown is
                # production. bool(None) would record a production change as unprotected.
                protected=bool(context.get("protected", True)),
                outcome=outcome,
                verified=verified,
                error=error,
            ),
        )

    async def apply_permission_change(diff) -> None:
        if diff is None:
            ui.notify("The approved change was lost. Reopen the dialog.", color="red")
            return
        # Snapshotted synchronously, before the first await, and never re-read afterwards.
        # Quasar emits "hide" on ESC and on a backdrop click; that arrives as its own
        # websocket message and runs clear_permission_state() while this coroutine is
        # suspended on an await. Reading permission_context after an await -- which the
        # audit calls below used to do for service_name and protected -- then produces a
        # record with a blank environment, service_id and service_name and protected
        # false, and rewrite_json_artifact ATOMICALLY REPLACES the good pre-flight record
        # with the gutted one. before/after survive because they come from diff, so the
        # rollback array is intact but no longer says which service in which environment
        # it belongs to. The same interleaving is why the dialog closes only after the
        # last await.
        if permission_change_in_flight.get("busy"):
            ui.notify("A permission change is already in progress.", color="red")
            return
        permission_change_in_flight["busy"] = True
        context = dict(permission_context)
        perms_submit_button.disable()
        perms_final_execute_button.disable()
        request_issued = False
        try:
            environment = context.get("environment")
            service_id = context.get("service_id")
            # pragma justification: not reachable through any wired handler, and kept
            # deliberately. permission_context is populated atomically at dialog open, and
            # neither caller can reach here with it half-populated -- handle_permissions_submit
            # computes an empty diff and stops at tier NONE when the context is cleared, and
            # handle_permissions_execute stops at the challenge gate. Nothing yields to the
            # event loop between reading the stash and snapshotting the context, so the
            # Quasar "hide" that clears it cannot interleave. That last sentence is the
            # reason this stays: it is a property of the current code, not of the design,
            # and a single added await upstream would make this the only thing standing
            # between a lost context and an audit record attributed to nothing.
            if not (environment and service_id):  # pragma: no cover
                ui.notify("Permission context was lost. Reopen the dialog.", color="red")
                return
            if not await ensure_admin_auth(environment, sync_label):
                return
            api = await build_api_client(environment)

            # Runbook: stop if the current state differs from the state used to approve
            # the change. Guards against another operator editing the same service while
            # this dialog was open.
            try:
                live = await api.get_service(service_id)
                live_permissions = validate_service_read(live, service_id)
            except ServiceReadError as exc:
                ui.notify(
                    f"Unusable response re-reading service {service_id}: {exc} "
                    "Nothing was sent. Reopen and review.",
                    color="red",
                    timeout=0,
                    close_button=True,
                )
                return
            except Exception as exc:  # noqa: BLE001 - any failure here means do not send
                ui.notify(f"Could not re-read the service before updating: {exc}", color="red")
                return
            if not permissions_equal(live_permissions, diff.before):
                ui.notify(
                    "The service changed since this dialog was opened. Now: "
                    f"{', '.join(sorted(live_permissions)) or '(none)'}. "
                    "Nothing was sent. Reopen and review.",
                    color="red",
                    timeout=0,
                    close_button=True,
                )
                perms_final_dialog.close()
                perms_dialog.close()
                return

            # Written BEFORE the request so the rollback value survives a crash or a 500.
            audit_path = write_permission_audit(
                build_audit_payload(
                    environment=environment,
                    base_url=context.get("base_url"),
                    service_id=service_id,
                    service_name=context.get("service_name", ""),
                    diff=diff,
                    protected=bool(context.get("protected", True)),
                    outcome="attempted",
                )
            )

            try:
                request_issued = True
                await api.update_service_permissions(service_id, list(diff.after))
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 401:
                    handle_unauthorized(sync_label, environment)
                elif status == 403:
                    ui.notify("Not authorized to update this service. Obtain the required access.", color="red")
                elif status == 404:
                    ui.notify(
                        f"Service {service_id} not found in {environment}. Nothing was changed.",
                        color="red",
                    )
                elif status == 500:
                    ui.notify(
                        "The API returned 500. Do NOT retry. Re-read the service to establish its "
                        f"current state. Rollback value saved to {audit_path}",
                        color="red",
                        timeout=0,
                        close_button=True,
                    )
                else:
                    ui.notify(f"Failed to update permissions: {format_http_error(exc)}", color="red")
                finalize_permission_audit(audit_path, diff, "error", None, format_http_error(exc), context)
                return
            except httpx.RequestError as exc:
                ui.notify(f"Could not reach the notification API: {exc}", color="red")
                finalize_permission_audit(audit_path, diff, "error", None, str(exc), context)
                return
            except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
                ui.notify(f"Error updating permissions: {exc}", color="red")
                finalize_permission_audit(audit_path, diff, "error", None, str(exc), context)
                return

            # Runbook step 5: verify what persisted. Do not trust the update response.
            try:
                verify = await api.get_service(service_id)
                verified = validate_service_read(verify, service_id)
            except ServiceReadError as exc:
                ui.notify(
                    f"The update was SENT but the verification response is unusable: {exc} "
                    "The change may or may not have been applied. Re-read the service manually "
                    f"before doing anything else. Rollback value saved to {audit_path}",
                    color="red",
                    timeout=0,
                    close_button=True,
                )
                finalize_permission_audit(audit_path, diff, "error", None, f"unusable verification read: {exc}", context)
                return
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    f"Update was sent but the verification read failed: {exc}. Re-read the service "
                    f"manually before doing anything else. Rollback value saved to {audit_path}",
                    color="red",
                    timeout=0,
                    close_button=True,
                )
                finalize_permission_audit(audit_path, diff, "error", None, f"verification read failed: {exc}", context)
                return

            if not permissions_equal(verified, diff.after):
                ui.notify(
                    "VERIFICATION MISMATCH. Expected: "
                    f"{', '.join(sorted(diff.after)) or '(none)'}. Actual: "
                    f"{', '.join(sorted(verified)) or '(none)'}. "
                    f"Rollback value saved to {audit_path}",
                    color="red",
                    timeout=0,
                    close_button=True,
                )
                finalize_permission_audit(audit_path, diff, "mismatch", verified, None, context)
            else:
                finalize_permission_audit(audit_path, diff, "success", verified, None, context)
                ui.notify(f"Permissions updated and verified. Audit written to {audit_path}", color="green")

            cached = await update_service_permissions_cache(
                service_id=service_id,
                permissions=verified,
                environment=environment,
            )
            if not cached:
                # Matches handle_update_service. Without it the operator gets a green
                # "verified" beside a table still showing the old permission set.
                ui.notify(
                    "Permissions updated, but the local cache has no row for this service. "
                    "Run sync to refresh the table.",
                    color="warning",
                )
            await refresh_if_needed(services_table)
            # Closed only after the last await. close() merely pushes a prop update; the
            # resulting Quasar "hide" round-trips back as a separate event and would
            # otherwise be free to land during this await and clear permission_context
            # while the code below it still reads it.
            perms_final_dialog.close()
            perms_dialog.close()
        except Exception as exc:  # noqa: BLE001 - an escaping exception is invisible here
            # try/finally with no except hands the exception to NiceGUI, which passes it
            # to an app exception handler this application never registers
            # (nicegui/events.py:424), so the operator sees a button that does nothing.
            # Reachable without anything exotic: build_api_client raising
            # RuntimeError("Base URL missing...") for an environment with no configured
            # host; write_permission_audit raising FileExistsError from its O_EXCL create,
            # or OSError on a full data/; and worst, finalize_permission_audit raising on
            # the SUCCESS path after the POST has already landed -- no green notify, no
            # cache write, no dialog close. The operator re-submits, and the concurrency
            # check then aborts with "the service changed since this dialog was opened",
            # which is true and actively misleading: it changed because they changed it.
            logger.exception(
                "Permission change failed for service %s in %s",
                context.get("service_id"),
                context.get("environment"),
            )
            if request_issued:
                # Deliberately assumes the write landed. The failure could be anywhere
                # from the POST itself to the audit rewrite after a verified success, and
                # only one of those two guesses is safe to be wrong about.
                ui.notify(
                    f"The permission update request was issued and then something failed: {exc}. "
                    "The change may already have been applied. Re-read the service to establish "
                    "its current state before retrying.",
                    color="red",
                    timeout=0,
                    close_button=True,
                )
            else:
                ui.notify(
                    f"The permission change failed before anything was sent: {exc}. "
                    "Nothing was changed.",
                    color="red",
                    timeout=0,
                    close_button=True,
                )
        finally:
            permission_change_in_flight.pop("busy", None)
            perms_submit_button.enable()
            perms_final_execute_button.enable()

    async def handle_permissions_submit() -> None:
        diff = current_permission_diff()
        tier = classify_change(diff, bool(permission_context.get("protected", True)))
        if tier == ConfirmationTier.NONE:
            ui.notify("No permission changes selected", color="red")
            return
        if not challenge_satisfied(diff, tier):
            ui.notify("Complete the confirmation before continuing", color="red")
            return
        if requires_typed_challenge(tier):
            # Stash exactly what was approved so the final dialog cannot execute a diff
            # the operator never saw.
            pending_permission_change["diff"] = diff
            perms_final_label.text = build_confirmation_text(
                diff,
                permission_context.get("service_name", ""),
                permission_context.get("environment", ""),
                tier,
                base_url=permission_context.get("base_url"),
            )
            perms_final_dialog.open()
            return
        await apply_permission_change(diff)

    async def handle_permissions_execute() -> None:
        diff = pending_permission_change.get("diff")
        if diff is None:
            await apply_permission_change(None)
            return
        # Defence in depth on the last click before a production wipe. The stash is only
        # ever written by handle_permissions_submit after the gate passed, so today this
        # cannot fail -- but "today" rests on the final dialog having exactly one opener
        # and on the stash surviving dismissal of the outer dialog. Re-deriving the tier
        # and re-testing the challenge against the stashed diff costs two lines.
        tier = classify_change(diff, bool(permission_context.get("protected", True)))
        if not requires_typed_challenge(tier) or not challenge_satisfied(diff, tier):
            ui.notify(
                "The approved change no longer passes its confirmation gate. "
                "Nothing was sent. Reopen the dialog and redo the confirmation.",
                color="red",
                timeout=0,
                close_button=True,
            )
            return
        await apply_permission_change(diff)

    perms_challenge_input.on_value_change(lambda _e: refresh_permission_ui())
    perms_submit_button.on_click(handle_permissions_submit)
    perms_cancel_button.on_click(handle_permissions_cancel)
    perms_final_execute_button.on_click(handle_permissions_execute)
    perms_final_cancel_button.on_click(perms_final_dialog.close)
    # Quasar QDialog emits "hide" on every dismissal, including ESC and backdrop click,
    # which never reach the Cancel handler. args=[] because the handler needs nothing from
    # the event. Only the outer dialog clears state; the final dialog closing must not.
    #
    # Verified by precedent, not by test: app/ui/pages/service_callbacks.py:145, :312 and
    # :460 use the same wiring to blank bearer tokens on dismissal. If Quasar did not emit
    # "hide", that would be a live credential-retention bug in shipped code. Do not
    # re-litigate this in favour of on_value_change.
    perms_dialog.on("hide", clear_permission_state, args=[])
    # The final dialog drops only the approved diff, never the outer dialog's state: the
    # checkbox selections and the typed challenge must survive backing out of the last
    # confirmation. Dropping the stash means the operator presses submit again, which
    # re-derives the diff and re-tests the gate before re-opening this dialog -- so an
    # approval cannot outlive the dialog that displayed it.
    perms_final_dialog.on("hide", pending_permission_change.clear, args=[])

    with ui.column().classes("p-8 gap-6 w-full max-w-none"):
        ui.label("Services").classes("text-lg font-semibold")
        service_search = ui.input(label="Search by Service ID or Name").props("clearable").classes("w-full md:w-1/2")
        service_search.on_value_change(handle_service_search_event)
        await services_table(
            page_sync_services,
            selected_service,
            handle_open_edit_dialog,
            handle_open_permissions_dialog,
        )


@ui.refreshable
async def services_table(
    sync_callback,
    selected_service=None,
    on_edit_click=None,
    on_permissions_click=None,
) -> None:
    if selected_service is not None:
        selected_service.clear()
    view_env = get_view_environment()
    rows = await list_services(view_env)
    active_key_counts = await count_active_api_keys_by_service(view_env)
    template_counts = await count_templates_by_service(view_env)
    sms_sender_counts = await count_sms_senders_by_service(view_env)
    if _st.service_search_query:
        rows = [
            row
            for row in rows
            if _st.service_search_query in (row.id or "").lower()
            or _st.service_search_query in (row.name or "").lower()
        ]
    columns = [
        {"name": "id", "label": "ID", "field": "id"},
        {"name": "environment", "label": "Environment", "field": "environment"},
        {"name": "name", "label": "Name", "field": "name"},
        {"name": "active_keys", "label": "Active Keys", "field": "active_keys"},
        {"name": "templates", "label": "Templates", "field": "templates"},
        {"name": "sms_senders", "label": "SMS Senders", "field": "sms_senders"},
        {"name": "active", "label": "Active", "field": "active"},
        {"name": "restricted", "label": "Restricted", "field": "restricted"},
        {"name": "message_limit", "label": "Msg Limit", "field": "message_limit"},
        {"name": "rate_limit", "label": "Rate Limit", "field": "rate_limit"},
        {"name": "research_mode", "label": "Research", "field": "research_mode"},
        {"name": "count_as_live", "label": "Live", "field": "count_as_live"},
        {"name": "permissions", "label": "Permissions", "field": "permissions"},
    ]
    table_rows: List[Dict[str, Any]] = [
        {
            "_row_key": make_row_key(row.id, row.environment),
            "id": row.id,
            "environment": format_environment(row.environment),
            "environment_value": row.environment,
            "name": row.name,
            "active_keys": active_key_counts.get((row.id, row.environment), 0),
            "templates": template_counts.get((row.id, row.environment), 0),
            "sms_senders": sms_sender_counts.get((row.id, row.environment), 0),
            "active": row.active,
            "restricted": row.restricted,
            "message_limit": row.message_limit,
            "rate_limit": row.rate_limit,
            "research_mode": row.research_mode,
            "count_as_live": row.count_as_live,
            "permissions": format_permissions_display(row.permissions),
        }
        for row in rows
    ]
    with ui.row().classes("w-full items-center"):
        ui.button("Sync Services", on_click=sync_callback)
        if on_edit_click:
            ui.button("Edit Service Limits", on_click=on_edit_click, color="primary")
        if on_permissions_click:
            ui.button("Edit Permissions", on_click=on_permissions_click, color="warning")
        ui.space()
        add_export_button(table_rows, columns, "services.csv")

    if selected_service is not None:

        def handle_row_select(e) -> None:  # pragma: no cover
            if e.selection:
                clicked_key = e.selection[0].get("_row_key")
                current_key = selected_service.get("_row_key")
                if clicked_key == current_key:
                    selected_service.clear()
                    table.selected = []
                    return
                selected_service.clear()
                selected_service.update(e.selection[0])
            else:
                selected_service.clear()

        table = ui.table(
            columns=make_sortable(columns),
            rows=table_rows,
            selection="single",
            on_select=handle_row_select,
            pagination={"rowsPerPage": 10},
        )
    else:
        table = ui.table(
            columns=make_sortable(columns),
            rows=table_rows,
            pagination={"rowsPerPage": 10},
        )
    table.props("row-key=_row_key").classes("w-full")
    add_copyable_slots(table, table_rows)
    add_service_context_menu(table, column_name="name", id_field="id")
