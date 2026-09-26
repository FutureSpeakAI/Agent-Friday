"""A restaurant's published address is not the owner's private data.

SENSITIVE SUBSYSTEM (AGENTS.md): this exercises the cloud-egress gate in
privacy/. Read the rule in privacy/public_provenance.py before changing
anything here.

2026-09-25: Stephen asked Friday to put a weekend itinerary on his calendar. The
first event was refused as ``cloud_denied_tier_TIER_2`` because the venue's
street address -- read minutes earlier from the restaurant's own public website
-- was tagged ``[PII:addr]``. The theatre and concert events in the same batch
went through, which is what made it a false positive rather than a policy.

THE RULE, as approved. An address is treated as non-personal FOR THE
CLOUD-EGRESS DECISION ONLY when all of these hold:

  1. it is the sole cause -- strip it and the payload classifies TIER_1;
  2. its provenance is public web content, positively attributed;
  3. the cloud model already holds it, having authored the arguments;
  4. the exemption is written to the access log, never silent;
  5. it is a business-contact type -- street address, place name, business
     phone, URL -- and never a government id, card or account number, a
     credential or key, a health term, or a personal email;
  6. it does not match the owner's own records, whatever the web says.

Unknown or unattributable provenance fails closed. The [PII:addr] detector is
untouched: this is a provenance exemption at the gate, not a looser detector.
"""

import json

import pytest

ADDR = "1131 Nonexistent Blvd, Springfield, IL 62701"
OWNER_HOME = "4412 Imaginary Cove Lane, Shelbyville, IL 62565"


@pytest.fixture
def key(monkeypatch):
    """A taint ledger with a public web page as the only source."""
    from agent_friday.services import taint
    k = "pbp-test"
    taint.reset(k)
    return k


def _from_web(k, text, host="example-restaurant.com"):
    from agent_friday.services import taint
    taint.note_tool_output(k, "browse_web", {"url": "https://%s/visit" % host},
                           "Come and see us. %s. Open daily." % text)


@pytest.fixture
def owner_records(tmp_path, monkeypatch):
    """An owner-records corpus holding the owner's home address."""
    from agent_friday.privacy import public_provenance as pp
    monkeypatch.setattr(pp, "_owner_corpus",
                        lambda: {pp._norm(OWNER_HOME), pp._norm("Alex Example")})
    return pp


# ── the headline: the reported false positive ──────────────────────────────

def test_a_public_business_address_is_exempt(key, owner_records):
    from agent_friday.privacy import public_provenance as pp
    _from_web(key, ADDR)
    ok, why = pp.exempt({"title": "Dinner at Salty Sow", "location": ADDR},
                        action="create_calendar_event", taint_key=key)
    assert ok, "the reported false positive is still blocked: %s" % why


def test_the_address_detector_is_untouched():
    """The exemption is at the gate. The address must still be DETECTED."""
    from agent_friday.services.sensitivity_classifier import classify
    assert classify(ADDR) > 1, "the detector was loosened; that is not the fix"


# ── addition 6: the owner's own records win ────────────────────────────────

def test_the_owners_home_address_is_blocked_even_from_a_public_page(key,
                                                                   owner_records):
    """The mis-attribution case, and the owner's address appearing on some web
    page. Web provenance does not overrule his own records."""
    from agent_friday.privacy import public_provenance as pp
    _from_web(key, OWNER_HOME, host="some-directory.example")
    ok, why = pp.exempt({"title": "Drop off keys", "location": OWNER_HOME},
                        action="create_calendar_event", taint_key=key)
    assert not ok, "the owner's own home address was exempted"
    assert "own records" in why, why


def test_a_contact_name_from_the_owners_records_is_blocked(key, owner_records):
    from agent_friday.privacy import public_provenance as pp
    _from_web(key, "Alex Example")
    ok, why = pp.exempt({"title": "Alex Example", "location": "Alex Example"},
                        action="create_calendar_event", taint_key=key)
    assert not ok, why


def test_an_unreadable_owner_corpus_fails_closed(key, monkeypatch):
    """If the owner's records cannot be consulted, the exemption cannot be
    granted -- the check is the safeguard, so losing it removes the exemption."""
    from agent_friday.privacy import public_provenance as pp

    def boom():
        raise OSError("corpus unavailable")

    monkeypatch.setattr(pp, "_owner_corpus", boom)
    _from_web(key, ADDR)
    ok, why = pp.exempt({"location": ADDR}, action="create_calendar_event",
                        taint_key=key)
    assert not ok and "own records" in why, why


# ── addition 5: only business-contact types ────────────────────────────────

@pytest.mark.parametrize("value,label", [
    ("123-45-6789", "an SSN"),  # pragma: allowlist secret
    ("4111 1111 1111 1111", "a card number"),
    ("sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "an api key"),  # pragma: allowlist secret
    ("routing number 021000021", "a routing number"),
    ("his a1c was 7.2 last visit", "a health term"),
    ("someone.private@gmail.com", "a personal email"),  # pragma: allowlist secret
])
def test_never_exempt_types_stay_blocked_even_on_a_web_page(value, label,
                                                           key, owner_records):
    """Published on a web page or not: putting these into a calendar invite
    spreads them further."""
    from agent_friday.privacy import public_provenance as pp
    _from_web(key, value)
    ok, why = pp.exempt({"notes": value}, action="create_calendar_event",
                        taint_key=key)
    assert not ok, "%s was exempted" % label


@pytest.mark.parametrize("value", [
    "512-555-0137",                               # a business phone
    "https://example-restaurant.com/menu",        # a URL
    "The Nonexistent Theatre, Springfield",            # a place name
])
def test_business_contact_types_reach_a_cloud_seat(value, key, owner_records,
                                                  tmp_path):
    """Asserted at the GATE, not on the rule.

    A URL or a place name may not tier above PUBLIC on its own, in which case
    there is nothing to exempt and the call was never blocked. Either way the
    property the owner cares about is the same: a business's published contact
    detail reaches the cloud seat.
    """
    from agent_friday.privacy.vault_access import VaultAccessControl
    _from_web(key, value)
    allowed, detail, tier = VaultAccessControl().check_action(
        "anthropic", "create_calendar_event",
        json.dumps({"location": value}),
        access_log_path=str(tmp_path / "a.jsonl"), taint_key=key)
    assert allowed, "%r was refused: %s" % (value, detail)


# ── condition 1: it has to be the only reason ──────────────────────────────

def test_a_mixed_payload_is_still_blocked(key, owner_records):
    """The address is exemptable; the contact's phone beside it is not. Strip
    the address and the payload is still TIER_2, so the denial stands."""
    from agent_friday.privacy import public_provenance as pp
    _from_web(key, ADDR)
    ok, why = pp.exempt(
        {"location": ADDR, "notes": "his ssn is 123-45-6789"},  # pragma: allowlist secret
        action="create_calendar_event", taint_key=key)
    assert not ok, why


# ── condition 2: provenance, positively attributed ─────────────────────────

def test_an_unattributable_address_is_blocked(key, owner_records):
    """Nothing was read from the web, so nothing proves this is a business
    address. Fail closed."""
    from agent_friday.privacy import public_provenance as pp
    ok, why = pp.exempt({"location": ADDR}, action="create_calendar_event",
                        taint_key=key)
    assert not ok, why


def test_an_address_from_email_is_blocked(key, owner_records):
    from agent_friday.services import taint
    from agent_friday.privacy import public_provenance as pp
    taint.note_tool_output(key, "search_email", {"query": "keys"},
                           "Let's meet at %s tomorrow." % ADDR)
    ok, why = pp.exempt({"location": ADDR}, action="create_calendar_event",
                        taint_key=key)
    assert not ok, why


def test_a_value_the_user_typed_is_not_exempted_by_this_rule(key, owner_records):
    """The rule is about PUBLIC WEB provenance. A value the user typed is his
    own; it is not this exemption's business."""
    from agent_friday.services import taint
    from agent_friday.privacy import public_provenance as pp
    taint.note_user_message(key, "put %s on the calendar" % ADDR)
    ok, why = pp.exempt({"location": ADDR}, action="create_calendar_event",
                        taint_key=key)
    assert not ok, why


# ── condition 4: the gate logs it, and allows it ───────────────────────────

def test_check_action_allows_it_and_says_so_in_the_log(tmp_path, key,
                                                       owner_records):
    from agent_friday.privacy.vault_access import VaultAccessControl
    _from_web(key, ADDR)
    ctl = VaultAccessControl()
    logp = tmp_path / "access-log.jsonl"
    data = json.dumps({"title": "Dinner at Salty Sow", "location": ADDR})
    allowed, detail, tier = ctl.check_action(
        "anthropic", "create_calendar_event", data,
        access_log_path=str(logp), taint_key=key)
    assert allowed, "the gate still refuses it: %s" % detail
    assert "public_web_provenance" in detail, detail
    rows = [json.loads(l) for l in
            logp.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows and "public_web_provenance" in rows[-1]["detail"], rows[-1]
    assert rows[-1]["allowed"] is True


def test_check_action_still_refuses_the_owners_address(tmp_path, key,
                                                      owner_records):
    from agent_friday.privacy.vault_access import VaultAccessControl
    _from_web(key, OWNER_HOME, host="some-directory.example")
    ctl = VaultAccessControl()
    logp = tmp_path / "access-log.jsonl"
    data = json.dumps({"title": "Drop off keys", "location": OWNER_HOME})
    allowed, detail, tier = ctl.check_action(
        "anthropic", "create_calendar_event", data,
        access_log_path=str(logp), taint_key=key)
    assert not allowed, detail
    assert "cloud_denied_tier" in detail, detail


def test_a_local_provider_is_unaffected(tmp_path, key, owner_records):
    """Local seats were never gated here; the exemption must not change that."""
    from agent_friday.privacy.vault_access import VaultAccessControl
    ctl = VaultAccessControl()
    allowed, detail, tier = ctl.check_action(
        "ollama", "create_calendar_event",
        json.dumps({"location": OWNER_HOME}),
        access_log_path=str(tmp_path / "a.jsonl"), taint_key=key)
    assert allowed, detail


def test_without_a_taint_key_nothing_is_exempted(tmp_path, owner_records):
    """A caller that cannot supply provenance gets today's behaviour."""
    from agent_friday.privacy.vault_access import VaultAccessControl
    ctl = VaultAccessControl()
    allowed, detail, tier = ctl.check_action(
        "anthropic", "create_calendar_event",
        json.dumps({"location": ADDR}),
        access_log_path=str(tmp_path / "a.jsonl"))
    assert not allowed, detail
