import json
import os

import pytest

from app.config import DEFAULT_NON_PRODUCTION_ENVIRONMENTS
from app.ui import permission_helpers as ph


def test_known_permissions_match_the_runbook():
    assert ph.KNOWN_PERMISSIONS == (
        "email",
        "sms",
        "push",
        "international_sms",
        "inbound_sms",
        "schedule_notifications",
    )


def test_known_permission_labels_match_the_runbook():
    # Labels are module-level constants, so line coverage cannot catch a typo in one.
    # Pin the values explicitly.
    assert ph.KNOWN_PERMISSION_LABELS == {
        "email": "Email notifications",
        "sms": "SMS notifications",
        "push": "Push notifications",
        "international_sms": "International SMS",
        "inbound_sms": "Inbound SMS",
        "schedule_notifications": "Scheduled notifications",
    }


def test_known_permissions_is_derived_from_the_labels_in_order():
    # The two constants must not drift: KNOWN_PERMISSIONS is the label keys, in the
    # dict's insertion order, which is the runbook's display order.
    assert ph.KNOWN_PERMISSIONS == tuple(ph.KNOWN_PERMISSION_LABELS)


def test_schedule_notifications_is_flagged_unsupported():
    assert ph.UNSUPPORTED_PERMISSIONS == frozenset({"schedule_notifications"})


DEFAULT_NON_PROD = DEFAULT_NON_PRODUCTION_ENVIRONMENTS


@pytest.mark.parametrize(
    "env_name,allowlist,expected",
    [
        # Every name in the shipped default is non-production.
        ("dev", DEFAULT_NON_PROD, False),
        ("development", DEFAULT_NON_PROD, False),
        ("local", DEFAULT_NON_PROD, False),
        ("test", DEFAULT_NON_PROD, False),
        ("perf", DEFAULT_NON_PROD, False),
        ("sandbox", DEFAULT_NON_PROD, False),
        ("staging", DEFAULT_NON_PROD, False),
        ("stage", DEFAULT_NON_PROD, False),
        # Both production spellings used in this repo.
        ("production", DEFAULT_NON_PROD, True),
        ("prod", DEFAULT_NON_PROD, True),
        # An environment nobody has classified yet is production, not a guess.
        ("mystery", DEFAULT_NON_PROD, True),
        ("e2e", DEFAULT_NON_PROD, True),
        # Fail closed on an absent or blank name.
        (None, DEFAULT_NON_PROD, True),
        ("", DEFAULT_NON_PROD, True),
        ("   ", DEFAULT_NON_PROD, True),
        # Case-insensitive on both sides of the comparison.
        ("PRODUCTION", DEFAULT_NON_PROD, True),
        ("Development", DEFAULT_NON_PROD, False),
        ("  STAGING  ", DEFAULT_NON_PROD, False),
        # Matching is EXACT, never substring: containing a listed name is not being one.
        ("development-2", DEFAULT_NON_PROD, True),
        ("dev-gov", DEFAULT_NON_PROD, True),
        ("localhost", DEFAULT_NON_PROD, True),
        ("pre-prod", DEFAULT_NON_PROD, True),
        # A custom allowlist fully overrides the default.
        ("qa", {"qa"}, False),
        ("dev", {"qa"}, True),
        ("development", frozenset(), True),
        # An operator can declare a name the default calls non-prod to be production.
        ("test", {"dev"}, True),
        # The blank guard must not depend on the config parser dropping blank entries.
        # config.parse_non_production_environments already drops them, so without this
        # case the guard is redundant and a future edit could delete it undetected --
        # after which an allowlist containing "" would classify a nameless environment as
        # non-production, which is the fail-open shape this whole rewrite exists to kill.
        ("", {"", "dev"}, True),
        (None, {"", "dev"}, True),
        ("   ", {"", "dev"}, True),
    ],
)
def test_is_protected_environment(env_name, allowlist, expected):
    assert ph.is_protected_environment(env_name, allowlist) is expected


@pytest.mark.parametrize(
    "tunnel_url",
    [
        "http://localhost:6011",
        "http://127.0.0.1:6011",
        "http://127.5.5.5:6011",
        "http://[::1]:8443",
        "https://api.notifications.va.gov",
    ],
)
def test_tunnelled_govcloud_production_is_protected_regardless_of_url(tunnel_url):
    """H1 regression: a GovCloud production API reached through a tunnel.

    VA engineers reach GovCloud production over SSH, ``kubectl port-forward``, or an SSM
    tunnel, so the production API answers on ``localhost``. The previous implementation
    inferred production-ness from the base URL and returned False here -- deleting the
    entire CRITICAL gate (red banner, typed challenge, per-permission acknowledgements,
    final dialog) for a live Veteran-facing service, and stamping the audit record
    ``"protected": false``.

    ``tunnel_url`` is parametrized purely to state that the URL is now irrelevant: the
    classification is a property the environment declares, and ``gov`` is not on the
    non-production allowlist.
    """
    assert ph.is_protected_environment("gov", DEFAULT_NON_PROD) is True, (
        f"gov reached via {tunnel_url} must stay protected; production-ness must not be "
        "inferred from a URL that a tunnel makes indistinguishable from local dev"
    )


def test_is_protected_environment_takes_no_url_or_mock_argument():
    """The mechanism, not just its output. A URL argument is what H1 was.

    A future edit that reintroduces a ``raw_base_url`` or ``use_mock`` parameter would
    reintroduce either the tunnel blind spot or a mock-mode short-circuit that makes the
    CRITICAL path unrehearsable. Pin the signature so that edit fails loudly here.
    """
    import inspect

    params = list(inspect.signature(ph.is_protected_environment).parameters)
    assert params == ["env_name", "non_production_environments"]


def test_url_inference_machinery_is_gone():
    """The deleted helpers must stay deleted; a reintroduced copy is how H1 comes back."""
    for name in ("_hostname", "_is_loopback_host", "NON_PROD_HOST_TOKENS", "PROD_ENV_TOKENS", "_ENV_NAME_SPLIT"):
        assert not hasattr(ph, name), f"{name} was reintroduced into permission_helpers"


class TestNormalizePermissions:
    def test_preserves_order_and_dedupes(self):
        assert ph.normalize_permissions(["sms", "email", "sms"]) == ["sms", "email"]

    def test_strips_whitespace_and_drops_blanks(self):
        assert ph.normalize_permissions([" email ", "", "   "]) == ["email"]

    def test_drops_non_strings_when_lenient(self):
        # The default is the WRITE path, where the values are our own checkbox values.
        assert ph.normalize_permissions(["email", None, 7, {"a": 1}]) == ["email"]

    def test_handles_none(self):
        assert ph.normalize_permissions(None) == []

    def test_does_not_lowercase(self):
        # Permission names are case-sensitive; lowercasing an unknown value would corrupt it.
        assert ph.normalize_permissions(["Email"]) == ["Email"]


class TestNormalizePermissionsStrictMode:
    """An element a live read would silently alter must be surfaced, never cleaned up.

    ``POST /service/{id}`` replaces the entire permission set. A live read of
    ``["email", "sms", 0]`` filtered leniently yields ``before = ("email", "sms")``: the
    ``0`` appears in no diff entry, earns no acknowledgement, and is destroyed by the
    submit — with LESS friction than a change the operator can actually see. The
    asymmetry was the tell: a non-list ``permissions`` hard-stopped loudly while a list
    containing garbage was silently filtered.
    """

    def test_strict_raises_on_a_non_string_element(self):
        with pytest.raises(TypeError):
            ph.normalize_permissions(["email", "sms", 0], strict=True)

    def test_strict_error_names_the_offending_element_and_its_type(self):
        with pytest.raises(TypeError) as excinfo:
            ph.normalize_permissions(["email", "sms", 0], strict=True)
        message = str(excinfo.value)
        assert "0" in message
        assert "int" in message

    @pytest.mark.parametrize("bad", [None, 7, {"a": 1}, ["nested"], 1.5, True])
    def test_strict_rejects_every_non_string_type(self, bad):
        with pytest.raises(TypeError):
            ph.normalize_permissions(["email", bad], strict=True)

    def test_strict_rejects_an_element_that_would_be_dropped_as_empty(self):
        # Same fail-open shape as the non-string case, reached through a different branch:
        # ["email", ""] yields before = ("email",), so the empty value appears in no diff
        # entry and is destroyed by the replace-everything POST.
        with pytest.raises(TypeError, match="empty or whitespace"):
            ph.normalize_permissions(["email", ""], strict=True)
        with pytest.raises(TypeError, match="empty or whitespace"):
            ph.normalize_permissions(["email", "   "], strict=True)

    def test_strict_rejects_an_element_that_would_be_rewritten(self):
        # The subtlest of the three and the worst. The snapshot holds "sms", the diff
        # reports nothing changed, the POST sends ["email", "sms"], the service's real
        # " sms " is deleted and replaced, and the verification read normalizes the new
        # value back to "sms" and reports success. A silent rewrite with zero friction.
        with pytest.raises(TypeError, match="surrounding whitespace"):
            ph.normalize_permissions(["email", " sms "], strict=True)

    def test_strict_still_collapses_duplicates(self):
        # The one alteration that cannot destroy anything: every consumer is set-based, so
        # collapsing an exact duplicate changes no set and hides no value.
        assert ph.normalize_permissions(["email", "sms", "email"], strict=True) == ["email", "sms"]

    def test_strict_still_accepts_a_clean_list(self):
        assert ph.normalize_permissions(["email", "sms"], strict=True) == ["email", "sms"]

    def test_strict_still_accepts_none(self):
        assert ph.normalize_permissions(None, strict=True) == []

    def test_lenient_is_the_default(self):
        assert ph.normalize_permissions(["email", 0]) == ["email"]
        assert ph.normalize_permissions(["email", 0], strict=False) == ["email"]
        assert ph.normalize_permissions(["email", "", " sms "]) == ["email", "sms"]

    def test_permissions_equal_is_strict(self):
        # Both sides are read results — a staleness re-check or a verification read. A
        # filtered element would make this report equality between unequal sets, i.e.
        # both safety checks failing open.
        with pytest.raises(TypeError):
            ph.permissions_equal(["email", "sms", 0], ["email", "sms"])
        with pytest.raises(TypeError):
            ph.permissions_equal(["email", "sms"], ["email", "sms", 0])

    def test_build_permission_options_is_strict(self):
        # Its argument is a live read, and a dropped element is omitted from the union and
        # therefore from the POST — the exact silent deletion the union exists to prevent.
        with pytest.raises(TypeError):
            ph.build_permission_options(["email", 0])

    def test_diff_permissions_stays_lenient(self):
        # Runs on every checkbox toggle with values that are already ours; raising on a
        # keystroke would break the dialog rather than protect anything.
        diff = ph.diff_permissions(["email", 0], ["email", "sms"])
        assert diff.before == ("email",)
        assert diff.added == ("sms",)


class TestIsSafeServiceId:
    @pytest.mark.parametrize(
        "value",
        [
            "svc-1",
            "0e2fe0d1-9c1a-4f8f-9a1e-2f5c9a0b1d3e",
            "A",
            "a_b-C9",
        ],
    )
    def test_accepts_a_plain_path_segment(self, value):
        assert ph.is_safe_service_id(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "../../organisation/x",
            "..",
            ".",
            "svc/1",
            "svc%2f1",
            "svc?x=1",
            "svc#frag",
            "svc 1",
            "-svc",
            "_svc",
            "",
            None,
            123,
            ["svc-1"],
            "a" * 65,
        ],
    )
    def test_rejects_anything_that_is_not_one(self, value):
        # Every one of these either escapes the /service/{id} path segment or lets httpx
        # resolve the request somewhere the operator never chose.
        assert ph.is_safe_service_id(value) is False


class TestValidateServiceRead:
    def test_returns_the_normalized_permission_set(self):
        body = {"id": "svc-1", "permissions": ["email", "sms", "email"]}
        assert ph.validate_service_read(body, "svc-1") == ["email", "sms"]

    @pytest.mark.parametrize("bad", [["email", ""], ["email", "   "], ["email", " sms "]])
    def test_rejects_an_element_the_read_would_silently_alter(self, bad):
        with pytest.raises(ph.ServiceReadError, match="malformed permissions value"):
            ph.validate_service_read({"id": "svc-1", "permissions": bad}, "svc-1")

    def test_rejects_a_non_dict_body(self):
        # get_service returns {} for a non-dict body, but a caller handing this the raw
        # response must not be the difference between safe and not.
        with pytest.raises(ph.ServiceReadError, match="not a service object"):
            ph.validate_service_read([], "svc-1")

    def test_rejects_a_different_service(self):
        with pytest.raises(ph.ServiceReadError, match="returned service other"):
            ph.validate_service_read({"id": "other", "permissions": []}, "svc-1")

    def test_rejects_a_missing_permissions_key(self):
        # THE critical one. A missing key is not knowledge of an empty set: reading it as
        # [] presents a fully-permissioned service as empty, drops the tier below
        # CRITICAL, and lets the replace-everything POST wipe the real set silently.
        with pytest.raises(ph.ServiceReadError, match="no 'permissions' key"):
            ph.validate_service_read({"id": "svc-1", "name": "VEText"}, "svc-1")

    def test_an_explicitly_empty_permission_set_is_accepted(self):
        # The other half of the same distinction: present-and-empty is knowledge.
        assert ph.validate_service_read({"id": "svc-1", "permissions": []}, "svc-1") == []

    def test_rejects_a_non_sequence_permissions_value(self):
        with pytest.raises(ph.ServiceReadError, match="malformed permissions value \\(got str\\)"):
            ph.validate_service_read({"id": "svc-1", "permissions": "email,sms"}, "svc-1")

    def test_rejects_a_non_string_element_rather_than_filtering_it(self):
        # Strict on every read path: a filtered element appears in no diff entry, earns no
        # acknowledgement, and is destroyed by the POST.
        with pytest.raises(ph.ServiceReadError, match="not str"):
            ph.validate_service_read({"id": "svc-1", "permissions": ["email", 0]}, "svc-1")

    def test_id_comparison_is_string_based(self):
        # The API is JSON, so an id could arrive as a number; str() on both sides keeps
        # that from reading as a mismatch.
        assert ph.validate_service_read({"id": 7, "permissions": ["email"]}, 7) == ["email"]


class TestBuildPermissionOptions:
    def test_all_known_permissions_are_always_offered(self):
        options = ph.build_permission_options([])
        assert [o.value for o in options] == list(ph.KNOWN_PERMISSIONS)
        assert all(o.enabled is False for o in options)
        assert all(o.known is True for o in options)

    def test_live_values_are_enabled(self):
        options = ph.build_permission_options(["email", "sms"])
        enabled = {o.value for o in options if o.enabled}
        assert enabled == {"email", "sms"}

    def test_unknown_live_value_is_appended_enabled_and_flagged(self):
        options = ph.build_permission_options(["email", "letter"])
        assert [o.value for o in options][-1] == "letter"
        letter = options[-1]
        assert letter.known is False
        assert letter.enabled is True
        assert letter.label == "letter"

    def test_multiple_unknown_values_keep_live_order(self):
        options = ph.build_permission_options(["zeta", "alpha"])
        assert [o.value for o in options][-2:] == ["zeta", "alpha"]

    def test_unsupported_flag(self):
        options = {o.value: o for o in ph.build_permission_options([])}
        assert options["schedule_notifications"].unsupported is True
        assert options["email"].unsupported is False

    def test_unknown_values_are_never_marked_unsupported(self):
        # Do NOT delete this as "obviously implied". The unknown branch passes
        # unsupported=False as a hardcoded literal, not derived from
        # UNSUPPORTED_PERMISSIONS, and flipping that literal to True is a real bug that
        # this test is the unique killer of: the sibling test asserts known/enabled/label
        # but not unsupported, and test_unsupported_flag passes [] so it never builds an
        # unknown option at all.
        options = ph.build_permission_options(["letter"])
        assert options[-1].unsupported is False

    def test_every_unsupported_value_is_also_a_known_value(self):
        # The invariant that makes the assertion above hold for ANY unknown value, not
        # just "letter": an unsupported value is by definition one this tool documents.
        # Adding an undocumented value to UNSUPPORTED_PERMISSIONS would break that.
        assert ph.UNSUPPORTED_PERMISSIONS <= set(ph.KNOWN_PERMISSIONS)

    def test_duplicate_live_values_produce_one_option(self):
        options = ph.build_permission_options(["letter", "letter"])
        assert [o.value for o in options].count("letter") == 1


class TestDiffPermissions:
    def test_addition(self):
        diff = ph.diff_permissions(["email"], ["email", "sms"])
        assert diff.added == ("sms",)
        assert diff.removed == ()
        assert diff.unchanged == ("email",)
        assert diff.before == ("email",)
        assert diff.after == ("email", "sms")
        assert diff.is_empty is False

    def test_removal(self):
        diff = ph.diff_permissions(["email", "sms"], ["email"])
        assert diff.added == ()
        assert diff.removed == ("sms",)
        assert diff.unchanged == ("email",)

    def test_mixed(self):
        diff = ph.diff_permissions(["email", "sms"], ["email", "push"])
        assert diff.added == ("push",)
        assert diff.removed == ("sms",)

    def test_clear_all(self):
        diff = ph.diff_permissions(["email", "sms"], [])
        assert diff.removed == ("email", "sms")
        assert diff.after == ()
        assert diff.is_empty is False

    def test_no_change(self):
        diff = ph.diff_permissions(["email", "sms"], ["sms", "email"])
        assert diff.added == ()
        assert diff.removed == ()
        assert diff.is_empty is True

    def test_both_empty(self):
        diff = ph.diff_permissions([], [])
        assert diff.is_empty is True

    def test_normalizes_inputs(self):
        diff = ph.diff_permissions([" email ", "email"], ["email", "", None])
        assert diff.before == ("email",)
        assert diff.after == ("email",)
        assert diff.is_empty is True

    def test_removed_preserves_before_order(self):
        diff = ph.diff_permissions(["sms", "email", "push"], [])
        assert diff.removed == ("sms", "email", "push")

    def test_added_preserves_after_order(self):
        diff = ph.diff_permissions([], ["sms", "email"])
        assert diff.added == ("sms", "email")


class TestClassifyChange:
    @pytest.mark.parametrize(
        "before,after,protected,expected",
        [
            # No change is never confirmed
            (["email"], ["email"], False, ph.ConfirmationTier.NONE),
            (["email"], ["email"], True, ph.ConfirmationTier.NONE),
            ([], [], True, ph.ConfirmationTier.NONE),
            # T1: non-prod, additive only
            (["email"], ["email", "sms"], False, ph.ConfirmationTier.STANDARD),
            ([], ["email"], False, ph.ConfirmationTier.STANDARD),
            # T2: non-prod with removals
            (["email", "sms"], ["email"], False, ph.ConfirmationTier.ELEVATED),
            (["email", "sms"], ["email", "push"], False, ph.ConfirmationTier.ELEVATED),
            (["email"], [], False, ph.ConfirmationTier.ELEVATED),
            # T2: prod, additive only
            (["email"], ["email", "sms"], True, ph.ConfirmationTier.ELEVATED),
            ([], ["email"], True, ph.ConfirmationTier.ELEVATED),
            # T3: prod with any removal
            (["email", "sms"], ["email"], True, ph.ConfirmationTier.CRITICAL),
            (["email", "sms"], ["email", "push"], True, ph.ConfirmationTier.CRITICAL),
            (["email"], [], True, ph.ConfirmationTier.CRITICAL),
        ],
    )
    def test_tier_matrix(self, before, after, protected, expected):
        assert ph.classify_change(ph.diff_permissions(before, after), protected) is expected

    def test_tiers_are_ordered(self):
        assert (
            ph.ConfirmationTier.NONE
            < ph.ConfirmationTier.STANDARD
            < ph.ConfirmationTier.ELEVATED
            < ph.ConfirmationTier.CRITICAL
        )


class TestValidateTypedChallenge:
    def test_exact_match(self):
        assert ph.validate_typed_challenge("VEText", "VEText") is True

    def test_surrounding_whitespace_is_tolerated(self):
        assert ph.validate_typed_challenge("  VEText  ", "VEText") is True
        assert ph.validate_typed_challenge("VEText", "  VEText  ") is True

    def test_case_must_match(self):
        assert ph.validate_typed_challenge("vetext", "VEText") is False

    def test_partial_does_not_match(self):
        assert ph.validate_typed_challenge("VETex", "VEText") is False

    def test_internal_whitespace_must_match(self):
        assert ph.validate_typed_challenge("VA  Notify", "VA Notify") is False

    def test_empty_input_never_matches(self):
        assert ph.validate_typed_challenge("", "VEText") is False
        assert ph.validate_typed_challenge(None, "VEText") is False

    def test_unknown_expected_name_is_never_satisfiable(self):
        # Otherwise a service with a blank name would accept an empty confirmation.
        assert ph.validate_typed_challenge("", "") is False
        assert ph.validate_typed_challenge("", None) is False
        assert ph.validate_typed_challenge("anything", None) is False


class TestBuildConfirmButtonLabel:
    def test_no_change(self):
        assert ph.build_confirm_button_label(ph.diff_permissions(["email"], ["email"])) == "No changes"

    def test_add_only(self):
        diff = ph.diff_permissions([], ["email", "sms"])
        assert ph.build_confirm_button_label(diff) == "Add 2"

    def test_remove_only(self):
        diff = ph.diff_permissions(["email", "sms"], [])
        assert ph.build_confirm_button_label(diff) == "Remove 2"

    def test_removal_is_named_first(self):
        diff = ph.diff_permissions(["email", "sms"], ["email", "push"])
        assert ph.build_confirm_button_label(diff) == "Remove 1, Add 1"


class TestBuildConfirmationText:
    def test_empty_diff(self):
        diff = ph.diff_permissions(["email"], ["email"])
        text = ph.build_confirmation_text(diff, "VEText", "production", ph.ConfirmationTier.NONE, base_url="https://production-notify.va.gov")
        assert text == "No permission changes selected."

    def test_names_service_and_environment(self):
        diff = ph.diff_permissions(["email"], ["email", "sms"])
        text = ph.build_confirmation_text(diff, "VEText", "development", ph.ConfirmationTier.STANDARD, base_url="https://development-notify.va.gov")
        assert "VEText" in text
        assert "development" in text

    def test_lists_removals_and_additions(self):
        diff = ph.diff_permissions(["email", "sms"], ["email", "push"])
        text = ph.build_confirmation_text(diff, "VEText", "staging", ph.ConfirmationTier.ELEVATED, base_url="https://staging-notify.va.gov")
        assert "Removing: sms" in text
        assert "Adding: push" in text
        assert "Resulting set: email, push" in text

    def test_critical_tier_is_announced(self):
        diff = ph.diff_permissions(["email", "sms"], ["email"])
        text = ph.build_confirmation_text(diff, "VEText", "production", ph.ConfirmationTier.CRITICAL, base_url="https://production-notify.va.gov")
        assert "PRODUCTION PERMISSION REMOVAL" in text

    def test_lower_tiers_are_not_announced_as_critical(self):
        diff = ph.diff_permissions(["email", "sms"], ["email"])
        text = ph.build_confirmation_text(diff, "VEText", "staging", ph.ConfirmationTier.ELEVATED, base_url="https://staging-notify.va.gov")
        assert "PRODUCTION PERMISSION REMOVAL" not in text

    def test_shows_the_resolved_base_url(self):
        # Protection is keyed on the environment NAME; the URL behind that name is a
        # mutable settings row. An operator pointing `dev` at production keeps the
        # low-friction tier, so the host has to be visible at the moment of decision.
        diff = ph.diff_permissions(["email", "sms"], ["email"])
        text = ph.build_confirmation_text(
            diff, "VEText", "dev", ph.ConfirmationTier.ELEVATED, base_url="https://api.notifications.va.gov"
        )
        assert text.splitlines()[0] == "VEText in dev -> https://api.notifications.va.gov"

    def test_a_missing_base_url_is_named_rather_than_left_blank(self):
        diff = ph.diff_permissions(["email", "sms"], ["email"])
        text = ph.build_confirmation_text(diff, "VEText", "dev", ph.ConfirmationTier.ELEVATED, base_url=None)
        assert text.splitlines()[0] == "VEText in dev -> (base URL not configured)"

    def test_base_url_is_keyword_only_and_required(self):
        # No default: a call site that forgets it must fail loudly rather than silently
        # dropping the one field that makes the misconfiguration visible.
        diff = ph.diff_permissions(["email", "sms"], ["email"])
        with pytest.raises(TypeError):
            ph.build_confirmation_text(diff, "VEText", "dev", ph.ConfirmationTier.ELEVATED)

    def test_clearing_everything_is_spelled_out(self):
        diff = ph.diff_permissions(["email"], [])
        text = ph.build_confirmation_text(diff, "VEText", "production", ph.ConfirmationTier.CRITICAL, base_url="https://production-notify.va.gov")
        assert "all permissions removed" in text


class TestBuildAuditPayload:
    def _payload(self, **overrides):
        kwargs = {
            "environment": "production",
            "base_url": "https://api.notifications.va.gov",
            "service_id": "svc-1",
            "service_name": "VEText",
            "diff": ph.diff_permissions(["email", "sms"], ["email"]),
            "protected": True,
        }
        kwargs.update(overrides)
        return ph.build_audit_payload(**kwargs)

    def test_records_the_change(self):
        payload = self._payload()
        assert payload["environment"] == "production"
        assert payload["protected"] is True
        assert payload["service_id"] == "svc-1"
        assert payload["service_name"] == "VEText"
        assert payload["before"] == ["email", "sms"]
        assert payload["after"] == ["email"]
        assert payload["added"] == []
        assert payload["removed"] == ["sms"]

    def test_records_the_targeted_host(self):
        # environment alone is a local key whose URL binding is editable at runtime, so a
        # record naming only "production" cannot say which host was actually written to.
        payload = self._payload(environment="dev", base_url="https://api.notifications.va.gov")
        assert payload["base_url"] == "https://api.notifications.va.gov"

    def test_base_url_is_required(self):
        kwargs = {
            "environment": "production",
            "service_id": "svc-1",
            "service_name": "VEText",
            "diff": ph.diff_permissions(["email"], []),
            "protected": True,
        }
        with pytest.raises(TypeError):
            ph.build_audit_payload(**kwargs)

    def test_defaults_to_attempted(self):
        payload = self._payload()
        assert payload["outcome"] == "attempted"
        assert payload["verified"] is None
        assert payload["error"] is None

    def test_timestamp_is_utc_iso8601(self):
        payload = self._payload()
        assert payload["timestamp"].endswith("Z")
        assert "T" in payload["timestamp"]

    def test_success_outcome_records_verified_set(self):
        payload = self._payload(outcome="success", verified=["email"])
        assert payload["outcome"] == "success"
        assert payload["verified"] == ["email"]

    def test_error_outcome_records_message(self):
        payload = self._payload(outcome="error", error="HTTP 500")
        assert payload["outcome"] == "error"
        assert payload["error"] == "HTTP 500"

    def test_audit_record_round_trips_as_real_json_types(self):
        # Stricter than production on purpose, and the only assertion in the suite that
        # is. artifacts._dump writes with default=str, so the production path CANNOT
        # fail on a non-serializable value -- a tuple lands on disk as
        # "('email', 'sms')" and a datetime as a quoted string, both written
        # successfully, both producing a rollback record whose `before` is an unusable
        # string rather than an array. json.dumps WITHOUT default=str is what catches
        # that, and the round-trip equality is what proves the types survived.
        payload = self._payload()
        assert json.loads(json.dumps(payload)) == payload

    def test_contains_no_credential_fields(self):
        # The payload is written to disk. It must never grow a secret.
        keys = set(self._payload())
        assert keys == {
            "timestamp",
            "environment",
            "base_url",
            "protected",
            "service_id",
            "service_name",
            "before",
            "after",
            "added",
            "removed",
            "outcome",
            "verified",
            "error",
        }


class TestWritePermissionAudit:
    def test_writes_to_the_given_directory(self, tmp_path):
        payload = {"outcome": "attempted"}
        path = ph.write_permission_audit(payload, directory=str(tmp_path))
        assert os.path.basename(path).startswith("permission_change_")
        with open(path, encoding="utf-8") as handle:
            assert json.load(handle) == payload

    def test_default_directory_constant(self):
        # Asserts the constant's VALUE only. The default argument's binding is
        # deliberately not asserted: CPython folds equal string constants within a module
        # into one object, so `signature(...).default is PERMISSION_CHANGES_DIR` is True
        # even when the default is written as a bare literal -- that assertion is
        # vacuous. The only thing that discriminates is a source-text match, which is not
        # worth its cost here.
        assert ph.PERMISSION_CHANGES_DIR == "data/permission_changes"


class TestFormatPermissionsDisplay:
    def test_renders_json_array_as_list(self):
        assert ph.format_permissions_display('["email", "sms"]') == "email, sms"

    def test_empty_array(self):
        assert ph.format_permissions_display("[]") == ""

    def test_none_and_blank(self):
        assert ph.format_permissions_display(None) == ""
        assert ph.format_permissions_display("") == ""

    def test_invalid_json_falls_back_to_raw(self):
        assert ph.format_permissions_display("not json") == "not json"

    def test_non_list_json_falls_back_to_raw(self):
        assert ph.format_permissions_display('{"a": 1}') == '{"a": 1}'

    def test_does_not_truncate(self):
        raw = json.dumps(list(ph.KNOWN_PERMISSIONS))
        rendered = ph.format_permissions_display(raw)
        assert "..." not in rendered
        for value in ph.KNOWN_PERMISSIONS:
            assert value in rendered


class TestNormalizePermissionsRejectsNonSequences:
    # A str satisfies Sequence[Any], so without a runtime guard
    # normalize_permissions("email") silently returns ['e','m','a','i','l'].
    # services.permissions is stored as a JSON *string* (app/models.py), and this module
    # also exposes format_permissions_display(raw: str), which consumes exactly that
    # string -- so passing the wrong one of two adjacent functions is a live mistake, not
    # a hypothetical. It raises rather than returning []: swallowing a bad input is the
    # same fail-open shape one layer up.
    def test_str_raises(self):
        with pytest.raises(TypeError):
            ph.normalize_permissions("email")

    def test_bytes_raises(self):
        with pytest.raises(TypeError):
            ph.normalize_permissions(b"email")

    def test_generator_raises(self):
        with pytest.raises(TypeError):
            ph.normalize_permissions(v for v in ["email"])

    def test_set_raises(self):
        with pytest.raises(TypeError):
            ph.normalize_permissions({"email"})

    def test_none_still_returns_empty(self):
        assert ph.normalize_permissions(None) == []

    def test_list_still_works(self):
        assert ph.normalize_permissions(["email", "sms"]) == ["email", "sms"]

    def test_tuple_still_works(self):
        assert ph.normalize_permissions(("email", "sms")) == ["email", "sms"]

    def test_the_json_column_string_is_rejected_not_exploded(self):
        # The exact confusion this guard exists for: the raw services.permissions value.
        with pytest.raises(TypeError):
            ph.diff_permissions('["email", "sms"]', ["email"])


class TestAuditPayloadRedactsError:
    def _payload(self, **overrides):
        kwargs = {
            "environment": "production",
            "base_url": "https://api.notifications.va.gov",
            "service_id": "svc-1",
            "service_name": "VEText",
            "diff": ph.diff_permissions(["email", "sms"], ["email"]),
            "protected": True,
        }
        kwargs.update(overrides)
        return ph.build_audit_payload(**kwargs)

    def test_error_mentioning_a_credential_is_redacted(self):
        # The record is written to disk before the destructive call. A dialog author
        # writing error=str(exc) must not be able to put a live token in it.
        payload = self._payload(outcome="error", error="bearer_token abc123def is not valid")
        assert "abc123def" not in payload["error"]
        assert payload["error"] == "bearer_token is invalid"

    def test_ordinary_error_passes_through(self):
        payload = self._payload(outcome="error", error="HTTP 500")
        assert payload["error"] == "HTTP 500"

    def test_none_error_stays_none(self):
        assert self._payload()["error"] is None

    @pytest.mark.parametrize(
        "field",
        ["bearer_token", "api_key", "secret", "password", "token", "authorization", "auth_parameter"],
    )
    def test_every_sensitive_field_is_redacted_out_of_the_record(self, field):
        # SENSITIVE_ERROR_FIELDS is the entire scope of the credential-free claim these
        # records make. Over-redaction costs a less specific message; under-redaction
        # writes a live credential to a file on disk.
        payload = self._payload(outcome="error", error=f"{field} s3kr1t-value-9 is not valid")
        assert "s3kr1t-value-9" not in payload["error"]

    def test_bearer_token_is_reported_specifically_not_as_token(self):
        # "token" is a substring of "bearer_token", so tuple order decides which name the
        # operator sees. The more specific one must win or the message loses diagnostic
        # value for the field the API actually rejects most often.
        payload = self._payload(outcome="error", error="bearer_token abc123 is too short")
        assert payload["error"] == "bearer_token is invalid"


class TestRequiresTypedChallenge:
    """The single source of truth for 'this change earns the typed challenge'.

    The dialog previously spelled `tier >= ConfirmationTier.CRITICAL` in three places:
    to decide whether to show the challenge, whether to enforce it, and whether to route
    through the final confirmation. Three copies of a gate is how one of them drifts.
    """

    def test_only_critical_requires_the_challenge(self):
        assert ph.requires_typed_challenge(ph.ConfirmationTier.NONE) is False
        assert ph.requires_typed_challenge(ph.ConfirmationTier.STANDARD) is False
        assert ph.requires_typed_challenge(ph.ConfirmationTier.ELEVATED) is False
        assert ph.requires_typed_challenge(ph.ConfirmationTier.CRITICAL) is True

    def test_every_tier_is_covered(self):
        # A tier added above CRITICAL must inherit the challenge, not fall through it.
        assert all(ph.requires_typed_challenge(t) is (t >= ph.ConfirmationTier.CRITICAL) for t in ph.ConfirmationTier)

    def test_production_removal_is_the_case_that_earns_it(self):
        diff = ph.diff_permissions(["email", "sms"], ["email"])
        assert ph.requires_typed_challenge(ph.classify_change(diff, True)) is True
        assert ph.requires_typed_challenge(ph.classify_change(diff, False)) is False


class TestIsChallengeSatisfied:
    """The core safety predicate: may a CRITICAL change proceed?

    This lived in a `# pragma: no cover` closure in services.py, where nothing tested it.
    It is the difference between a real production gate and a decorative one.
    """

    NAME = "VEText"

    def _removal(self, removed=("sms",), before=("email", "sms")):
        after = [v for v in before if v not in removed]
        return ph.diff_permissions(list(before), after)

    def _call(self, tier, typed, acknowledged, diff=None, expected=None):
        return ph.is_challenge_satisfied(
            tier,
            typed_name=typed,
            expected_name=self.NAME if expected is None else expected,
            acknowledged=acknowledged,
            diff=self._removal() if diff is None else diff,
        )

    @pytest.mark.parametrize(
        "tier",
        [ph.ConfirmationTier.NONE, ph.ConfirmationTier.STANDARD, ph.ConfirmationTier.ELEVATED],
    )
    def test_below_critical_is_always_satisfied(self, tier):
        # Sub-critical tiers have no challenge widgets at all, so an unsatisfiable
        # predicate here would deadlock every non-production change.
        assert self._call(tier, "", set()) is True

    def test_critical_needs_both_halves(self):
        assert self._call(ph.ConfirmationTier.CRITICAL, self.NAME, {"sms"}) is True

    def test_critical_refuses_a_missing_name(self):
        assert self._call(ph.ConfirmationTier.CRITICAL, "", {"sms"}) is False
        assert self._call(ph.ConfirmationTier.CRITICAL, None, {"sms"}) is False

    def test_critical_refuses_a_wrong_or_differently_cased_name(self):
        assert self._call(ph.ConfirmationTier.CRITICAL, "vetext", {"sms"}) is False
        assert self._call(ph.ConfirmationTier.CRITICAL, "VA Notify", {"sms"}) is False

    def test_critical_refuses_a_missing_acknowledgement(self):
        assert self._call(ph.ConfirmationTier.CRITICAL, self.NAME, set()) is False

    def test_critical_refuses_a_partial_acknowledgement(self):
        diff = self._removal(removed=("sms", "push"), before=("email", "sms", "push"))
        assert self._call(ph.ConfirmationTier.CRITICAL, self.NAME, {"sms"}, diff=diff) is False
        assert self._call(ph.ConfirmationTier.CRITICAL, self.NAME, {"sms", "push"}, diff=diff) is True

    def test_an_unrelated_acknowledgement_does_not_substitute_for_a_missing_one(self):
        # Count-based logic would pass this. Identity is what matters: ticking a box for
        # a value that is not being removed says nothing about the value that is.
        diff = self._removal(removed=("sms", "push"), before=("email", "sms", "push"))
        assert self._call(ph.ConfirmationTier.CRITICAL, self.NAME, {"sms", "email"}, diff=diff) is False

    def test_surplus_acknowledgements_are_harmless(self):
        # Stale boxes from an earlier selection must not block a fully-acknowledged change.
        assert self._call(ph.ConfirmationTier.CRITICAL, self.NAME, {"sms", "inbound_sms"}) is True

    def test_blank_expected_name_is_never_satisfiable(self):
        # Otherwise a service the API returned without a name would accept an empty box.
        assert self._call(ph.ConfirmationTier.CRITICAL, "", {"sms"}, expected="") is False
        assert self._call(ph.ConfirmationTier.CRITICAL, "", {"sms"}, expected=None) is False

    def test_critical_with_no_removals_needs_only_the_name(self):
        # classify_change cannot produce this, but the predicate must not depend on that.
        additive = ph.diff_permissions(["email"], ["email", "sms"])
        assert self._call(ph.ConfirmationTier.CRITICAL, self.NAME, set(), diff=additive) is True
        assert self._call(ph.ConfirmationTier.CRITICAL, "", set(), diff=additive) is False

    def test_acknowledged_accepts_any_container_of_values(self):
        assert self._call(ph.ConfirmationTier.CRITICAL, self.NAME, ["sms"]) is True
        assert self._call(ph.ConfirmationTier.CRITICAL, self.NAME, frozenset({"sms"})) is True


class TestPermissionsEqual:
    """Order-insensitive comparison, used for the staleness and verification checks.

    A list comparison in either place would abort or falsely flag every change whose
    permissions the API happened to return in a different order.
    """

    def test_same_values_in_a_different_order_are_equal(self):
        assert ph.permissions_equal(["email", "sms"], ["sms", "email"]) is True

    def test_different_values_are_not_equal(self):
        assert ph.permissions_equal(["email", "sms"], ["email"]) is False
        assert ph.permissions_equal(["email"], ["email", "sms"]) is False

    def test_empty_sets_are_equal(self):
        assert ph.permissions_equal([], []) is True
        assert ph.permissions_equal([], None) is True

    def test_empty_is_not_equal_to_populated(self):
        # "all permissions removed" must never compare equal to "unchanged".
        assert ph.permissions_equal([], ["email"]) is False

    def test_tuples_and_lists_interoperate(self):
        # The call sites pass a PermissionDiff tuple on one side and a list on the other.
        assert ph.permissions_equal(("email", "sms"), ["sms", "email"]) is True

    def test_duplicates_do_not_create_a_difference(self):
        assert ph.permissions_equal(["email", "email"], ["email"]) is True

    def test_surrounding_whitespace_is_refused_rather_than_normalized_away(self):
        # Both sides are read results. Treating " email " as equal to "email" is the
        # staleness check and the verification check agreeing about two different strings.
        with pytest.raises(TypeError, match="surrounding whitespace"):
            ph.permissions_equal([" email "], ["email"])

    def test_a_string_argument_raises_rather_than_comparing_characters(self):
        # Fail closed, exactly as normalize_permissions does: "email" must not be read as
        # five single-character permissions and reported as a difference.
        with pytest.raises(TypeError):
            ph.permissions_equal("email", ["email"])
        with pytest.raises(TypeError):
            ph.permissions_equal(["email"], "email")


class TestFormatPermissionOptionLabel:
    def _option(self, **overrides):
        kwargs = {"value": "email", "label": "Email notifications", "enabled": True, "known": True}
        kwargs.setdefault("unsupported", False)
        kwargs.update(overrides)
        return ph.PermissionOption(**kwargs)

    def test_a_plain_known_permission_is_undecorated(self):
        assert ph.format_permission_option_label(self._option()) == "Email notifications"

    def test_an_unsupported_permission_is_flagged(self):
        option = self._option(value="schedule_notifications", label="Scheduled notifications", unsupported=True)
        assert (
            ph.format_permission_option_label(option) == "Scheduled notifications  (currently unsupported by the API)"
        )

    def test_an_unrecognized_permission_is_flagged(self):
        option = self._option(value="letter", label="letter", known=False)
        assert ph.format_permission_option_label(option) == "letter  (unrecognized -- not documented by the API)"

    def test_unrecognized_wins_over_unsupported(self):
        # This tool documents no unsupported claim about a value it does not recognize,
        # so asserting one would be a claim it cannot back.
        option = self._option(value="letter", label="letter", known=False, unsupported=True)
        assert ph.format_permission_option_label(option) == "letter  (unrecognized -- not documented by the API)"

    def test_every_option_build_permission_options_produces_is_labelled(self):
        labels = [ph.format_permission_option_label(o) for o in ph.build_permission_options(["email", "letter"])]
        assert "Email notifications" in labels
        assert "Scheduled notifications  (currently unsupported by the API)" in labels
        assert "letter  (unrecognized -- not documented by the API)" in labels


class TestUnrecoverableRemovals:
    def test_known_values_are_recoverable(self):
        diff = ph.diff_permissions(["email", "sms"], ["email"])
        assert ph.unrecoverable_removals(diff) == ()

    def test_unknown_values_are_not(self):
        # Unrecognized values only appear as checkboxes because build_permission_options
        # unions them in from the LIVE read. Once removed they are no longer live, so the
        # next read does not contain them, no checkbox is offered, and there is
        # deliberately no free-text entry to type one back in.
        diff = ph.diff_permissions(["email", "letter"], ["email"])
        assert ph.unrecoverable_removals(diff) == ("letter",)

    def test_additions_are_never_flagged(self):
        diff = ph.diff_permissions([], ["email"])
        assert ph.unrecoverable_removals(diff) == ()

    def test_the_warning_reaches_the_confirmation_text(self):
        diff = ph.diff_permissions(["email", "letter"], ["email"])
        text = ph.build_confirmation_text(
            diff, "VEText", "production", ph.ConfirmationTier.CRITICAL, base_url="https://api.va.gov"
        )
        assert "letter cannot be re-added through this tool" in text

    def test_no_warning_when_every_removal_is_recoverable(self):
        diff = ph.diff_permissions(["email", "sms"], ["email"])
        text = ph.build_confirmation_text(
            diff, "VEText", "production", ph.ConfirmationTier.CRITICAL, base_url="https://api.va.gov"
        )
        assert "cannot be re-added" not in text


class TestBuildChallengeHint:
    def test_names_the_service_to_type(self):
        hint = ph.build_challenge_hint("VEText")
        assert hint.endswith("Type the service name exactly: VEText")
        assert "PRODUCTION" in hint

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_a_blank_name_says_the_challenge_cannot_be_satisfied(self, name):
        # validate_typed_challenge correctly refuses a blank expected name, so submit
        # stays disabled forever. Without this the hint reads "Type the service name
        # exactly: " and the operator has nothing to act on.
        hint = ph.build_challenge_hint(name)
        assert "cannot be satisfied" in hint
        assert "Postman" in hint

    def test_the_hint_is_consistent_with_the_validator(self):
        # Whenever the hint claims the challenge is satisfiable, the validator must agree
        # that the named string satisfies it.
        for name in ("VEText", " VEText "):
            assert ph.validate_typed_challenge(name, name) is True
            assert "cannot be satisfied" not in ph.build_challenge_hint(name)


class TestBuildConfirmButtonColor:
    @pytest.mark.parametrize(
        "tier,expected",
        [
            (ph.ConfirmationTier.NONE, "grey"),
            (ph.ConfirmationTier.STANDARD, "primary"),
            (ph.ConfirmationTier.ELEVATED, "negative"),
            (ph.ConfirmationTier.CRITICAL, "negative"),
        ],
    )
    def test_colour_tracks_the_tier(self, tier, expected):
        # The button was "negative" at every tier, so an additive change in development
        # looked identical to a production removal. Red at all tiers is red at none.
        assert ph.build_confirm_button_color(tier) == expected

    def test_an_additive_dev_change_is_not_red(self):
        diff = ph.diff_permissions([], ["email"])
        assert ph.build_confirm_button_color(ph.classify_change(diff, False)) == "primary"
        assert ph.build_confirm_button_color(ph.classify_change(diff, True)) == "negative"
