"""The one connector verdict, and the six vocabularies mapped onto it.

Phase 1 of docs/design/connector-ecosystem.md. Each test here corresponds to a
real failure, because the six vocabularies did not cost anything until they
started disagreeing:

  * a Google account at needs_reauth rendered as "connected" for nine days
  * a Firecrawl key reported as MISSING when it was present and undecryptable
  * both accounts reporting drive:true while every Drive call returned 403
"""
from __future__ import annotations

import pytest

from agent_friday.services import connector_health as H


# ── rule 1: fails closed ────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    None, "", "   ", "something-nobody-mapped", "CONNECTED_MAYBE", 42, object(),
])
def test_an_unmapped_state_is_unknown_and_not_healthy(value):
    """THE CRUX. Every vocabulary here has grown a value at some point -
    google_accounts grew `unreadable` the same day this was written - and the
    new value must never land on whatever branch happened to be first."""
    for fn in (H.from_connector_status, H.from_capability_state,
               H.from_provider_health, H.from_key_verdict):
        h = fn({"status": value, "state": value})
        assert h.state == H.UNKNOWN, (fn.__name__, value, h.state)
        assert h.healthy is False


def test_a_health_built_with_a_nonsense_state_degrades_rather_than_raises():
    """A health check that throws takes down the page whose job is to report
    trouble."""
    h = H.Health(state="not-a-real-state")
    assert h.state == H.UNKNOWN and h.healthy is False
    assert "not-a-real-state" in h.detail or "not-a-real-state" == h.source_state


def test_only_working_and_degraded_are_healthy():
    for s in H.STATES:
        assert H.Health(state=s).healthy is (s in (H.WORKING, H.DEGRADED))


def test_degraded_is_usable_on_purpose():
    """A Google account whose Drive is off at the Cloud console still reads
    Gmail. Refusing the whole account over one dead capability would be a worse
    lie than the one this module exists to stop."""
    assert H.Health(state=H.DEGRADED).healthy is True
    assert H.Health(state=H.DEGRADED).actionable is True


# ── rule 2: whose problem is it ─────────────────────────────────────────────

def test_a_local_key_fault_is_not_a_revoked_grant():
    """Sending someone to reconnect a Google account because a local key was
    wrong is the failure that cost a reconnect every morning for weeks."""
    revoked = H.from_google_account({"status": "needs_reauth"})
    local = H.from_google_account({"status": "unreadable"})
    assert revoked.state == H.NEEDS_USER and revoked.action == "reconnect"
    assert local.state == H.UNREADABLE and local.action == "unlock"
    assert local.action != revoked.action


def test_an_unreadable_key_is_not_reported_as_missing():
    """The Firecrawl incident. The key was present and unopenable, and the
    surface told the user to supply one he had already supplied."""
    unreadable = H.from_provider_key_status("present_but_unreadable", "firecrawl")
    missing = H.from_provider_key_status("missing", "firecrawl")
    assert unreadable.state == H.UNREADABLE
    assert missing.state == H.ABSENT
    assert "firecrawl" in unreadable.summary
    assert unreadable.action != missing.action


def test_a_handshake_in_flight_is_not_guessed_either_way():
    """`connecting` is genuinely not determined. Guessing WORKING flickers a
    page between two confident wrong answers; guessing ABSENT offers a connect
    button for something already connecting."""
    assert H.from_connector_status({"status": "connecting"}).state == H.UNKNOWN
    assert H.from_mcp_server({"status": "starting"}).state == H.UNKNOWN


# ── rule 3: a verdict can improve ───────────────────────────────────────────

def test_nothing_is_sticky():
    """Derived at the moment it is asked for. The stored status is an input,
    never the answer - the bug fixed in credentials_for on 2026-09-19 was a
    verdict that could only ever get worse."""
    rec = {"status": "needs_reauth"}
    assert H.from_google_account(rec).healthy is False
    rec["status"] = "connected"
    assert H.from_google_account(rec).healthy is True


# ── rule 4: the backend's own words survive ─────────────────────────────────

def test_the_detail_is_carried_through():
    d = "Google Drive API has not been used in project 449982820564"
    assert d in H.from_connector_status({"status": "error", "detail": d}).detail
    assert d in H.from_provider_health({"status": "down", "detail": d}).detail
    assert d in H.from_capability_state({"state": "present_failing",
                                         "detail": d}).detail


# ── the mappings themselves ─────────────────────────────────────────────────

@pytest.mark.parametrize("stored, expected", [
    ("connected", H.WORKING), ("needs_reauth", H.NEEDS_USER),
    ("revoked", H.NEEDS_USER), ("disconnected", H.ABSENT),
    ("unreadable", H.UNREADABLE), ("error", H.UNKNOWN),
])
def test_google_vocabulary(stored, expected):
    assert H.from_google_account({"status": stored}).state == expected


@pytest.mark.parametrize("stored, expected", [
    ("connected", H.WORKING), ("disconnected", H.ABSENT),
    ("needs_setup", H.ABSENT), ("blocked_by_policy", H.NEEDS_USER),
    ("error", H.UNKNOWN), ("unknown", H.UNKNOWN),
])
def test_connectors_vocabulary(stored, expected):
    assert H.from_connector_status({"status": stored}).state == expected


@pytest.mark.parametrize("stored, expected", [
    ("ready", H.WORKING), ("stopped", H.ABSENT), ("disabled", H.ABSENT),
    ("needs_auth", H.NEEDS_USER), ("error", H.UNKNOWN),
])
def test_mcp_server_vocabulary(stored, expected):
    assert H.from_mcp_server(stored).state == expected


@pytest.mark.parametrize("stored, expected", [
    ("working", H.WORKING), ("present_unverified", H.WORKING),
    ("present_failing", H.NEEDS_USER), ("unconfigured", H.ABSENT),
    ("absent", H.ABSENT),
])
def test_capability_vocabulary(stored, expected):
    assert H.from_capability_state(stored).state == expected


@pytest.mark.parametrize("stored, expected", [
    ("ok", H.WORKING), ("degraded", H.DEGRADED), ("down", H.UNKNOWN),
    ("missing", H.ABSENT), ("needs", H.NEEDS_USER),
])
def test_provider_health_vocabulary(stored, expected):
    assert H.from_provider_health(stored).state == expected


@pytest.mark.parametrize("stored, expected", [
    ("ok", H.WORKING), ("rejected", H.NEEDS_USER),
    ("no_credit", H.NEEDS_USER), ("unknown", H.UNKNOWN),
])
def test_key_verdict_vocabulary(stored, expected):
    assert H.from_key_verdict(stored).state == expected


def test_every_mapping_lands_in_the_vocabulary():
    """No mapping may introduce a state the rest of the system cannot read."""
    for table in (H._GOOGLE, H._CONNECTORS, H._MCP_SERVER, H._CAPABILITY,
                  H._KEY_STATUS, H._PROVIDER_HEALTH, H._KEY_VERDICT):
        assert set(table.values()) <= set(H.STATES)


# ── presence is not health ──────────────────────────────────────────────────

def test_a_stored_credential_is_usable_but_not_claimed_as_verified():
    """FAILING CLOSED APPLIES TO UNRECOGNISED STATES, NOT ABSENCE OF PROOF.

    A Bluesky app password has no expiry and no cheap probe; the only way to
    know it works is to publish with it. Marking it unusable until proven would
    refuse a credential the user supplied correctly - the same mistake as
    calling an unreadable key "missing", pointed the other way. What it must
    not do is claim to have checked.
    """
    present = H.from_credential_presence(True, "platforms")
    absent = H.from_credential_presence(False, "platforms")
    assert present.healthy is True
    assert present.verified is False
    assert absent.state == H.ABSENT and absent.healthy is False


def test_a_configured_but_unproven_key_is_usable():
    """`present_unverified` must not block first use of every API key the user
    ever adds."""
    h = H.from_capability_state("present_unverified")
    assert h.healthy is True and h.verified is False


def test_a_proven_state_says_so():
    assert H.from_capability_state("working").verified is True
    assert H.from_google_account({"status": "connected"}).verified is True


# ── aggregation ─────────────────────────────────────────────────────────────

def test_an_empty_set_is_unknown_not_all_clear():
    """"Nothing to report" and "all clear" are different sentences, and the
    whole family of bugs here comes from writing the second when you mean the
    first."""
    assert H.worst([]).state == H.UNKNOWN
    assert H.worst(None).healthy is False


def test_the_worst_verdict_is_the_one_with_something_to_do():
    """Ordered by how much it matters to the user, not by abstract severity: a
    thing needing reconnection outranks one whose state is merely unknown,
    because the first has an action attached."""
    hs = [H.Health(state=H.WORKING), H.Health(state=H.UNKNOWN),
          H.Health(state=H.NEEDS_USER)]
    assert H.worst(hs).state == H.NEEDS_USER
    assert H.worst([H.Health(state=H.WORKING),
                    H.Health(state=H.UNKNOWN)]).state == H.UNKNOWN
    assert H.worst([H.Health(state=H.WORKING),
                    H.Health(state=H.DEGRADED)]).state == H.DEGRADED


def test_one_sick_account_does_not_hide_behind_a_healthy_one():
    """The 2026-09-09 shape: an aggregate that reports fine because SOMETHING
    is fine."""
    s = H.summarise([H.from_google_account({"status": "connected"}),
                     H.from_google_account({"status": "needs_reauth"})])
    assert s["total"] == 2 and s["healthy"] == 1
    assert s["overall"]["state"] == H.NEEDS_USER
    assert s["overall"]["healthy"] is False


def test_summarise_counts_every_state():
    s = H.summarise([H.Health(state=H.WORKING), H.Health(state=H.WORKING),
                     H.Health(state=H.ABSENT)])
    assert s["counts"][H.WORKING] == 2 and s["counts"][H.ABSENT] == 1
    assert sum(s["counts"].values()) == 3


def test_as_dict_is_serialisable_and_keeps_the_provenance():
    d = H.from_google_account({"status": "unreadable"}).as_dict()
    import json
    json.dumps(d)
    assert d["source"] == "google_accounts" and d["source_state"] == "unreadable"
    assert d["healthy"] is False and d["action"] == "unlock"
