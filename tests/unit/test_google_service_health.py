"""Per-service Google health: an account being fine does not mean every
service on it works.

The failure shape: both accounts connected, tokens valid, `drive: true` on
both — and every Drive call returning `403 Google Drive API has not been used
in project <number> before or it is disabled`. The API had never been
switched on for the Cloud project. Nothing recorded that, so the connectors
page showed Drive as on and the 403 was thrown away inside an errors list.

`routes/news.py` meanwhile reported Gmail AND Calendar from one check of the
PRIMARY account's credentials, which answers none of the three questions that
decide whether a service is usable.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import connector_health as ch
from agent_friday.services import google_accounts as G


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "ACCOUNTS_DIR", tmp_path)
    monkeypatch.setattr(G, "_SERVICE_STATE_FILE", tmp_path / "service_state.json")
    accounts = {"accounts": []}
    monkeypatch.setattr(G, "_load_index", lambda: accounts)
    return accounts


def _acct(accounts, email, status="connected", **services):
    svc = {"gmail": True, "calendar": True, "drive": True}
    svc.update(services)
    accounts["accounts"].append(
        {"id": email.split("@")[0], "email": email, "status": status,
         "services": svc})


# ── question 1: is anything configured ──────────────────────────────────────

def test_no_account_for_a_service_is_absent(store):
    _acct(store, "a@b.c", drive=False)
    assert G.service_health("drive").state == ch.ABSENT
    assert G.service_health("drive").healthy is False


def test_no_accounts_at_all_is_absent(store):
    assert G.service_health("gmail").state == ch.ABSENT


# ── question 2: are the accounts usable ─────────────────────────────────────

def test_a_broken_account_is_not_hidden_by_a_healthy_one(store):
    """The aggregate-hides-a-failure shape: an aggregate that reports fine because SOMETHING
    is fine. One account needing reauthorisation is a thing the user must act
    on, whatever the other one is doing."""
    _acct(store, "good@b.c", "connected")
    _acct(store, "bad@b.c", "needs_reauth")
    h = G.service_health("gmail")
    assert h.state == ch.NEEDS_USER and h.healthy is False


def test_an_unreadable_account_says_so_rather_than_reconnect(store):
    _acct(store, "a@b.c", "unreadable")
    h = G.service_health("gmail")
    assert h.state == ch.UNREADABLE and h.action == "unlock"


def test_all_healthy_is_working(store):
    _acct(store, "a@b.c", "connected")
    _acct(store, "b@b.c", "connected")
    assert G.service_health("gmail").state == ch.WORKING


# ── question 3: is it switched on at the provider ───────────────────────────

def test_a_provider_refusal_makes_one_service_degraded(store):
    """THE DRIVE CASE. The account is fine and stays usable; the one service
    that is off at Google is the only thing reported off."""
    _acct(store, "a@b.c", "connected")
    G.note_service_result(
        "drive", False,
        "<HttpError 403 ... Google Drive API has not been used in project "
        "123456789012 before or it is disabled")
    drive = G.service_health("drive")
    gmail = G.service_health("gmail")

    assert drive.state == ch.DEGRADED
    assert drive.action == "enable_api"
    assert "123456789012" in drive.detail, "the provider's own words were dropped"
    # Degraded is USABLE: refusing the whole account over one dead capability
    # would be a worse lie than the one this exists to stop.
    assert drive.healthy is True
    assert gmail.state == ch.WORKING, "one dead service infected another"


def test_a_success_clears_the_condition(store):
    """A verdict that can only get worse is not a health check - the lesson
    from credentials_for the same day."""
    _acct(store, "a@b.c", "connected")
    G.note_service_result("drive", False, "the API is disabled for this project")
    assert G.service_health("drive").state == ch.DEGRADED
    G.note_service_result("drive", True)
    assert G.service_health("drive").state == ch.WORKING


@pytest.mark.parametrize("detail", [
    "timed out", "500 Internal Server Error", "connection reset",
    "", "something nobody has seen before",
])
def test_an_unrecognised_failure_is_not_recorded_as_switched_off(store, detail):
    """CONSERVATIVE ON PURPOSE. "I could not reach it" is not "it is off", and
    wrongly marking a service degraded is the same class of mistake as wrongly
    marking an account revoked. Same rule the seat probes follow."""
    _acct(store, "a@b.c", "connected")
    G.note_service_result("drive", False, detail)
    assert G.service_health("drive").state == ch.WORKING


def test_the_recorded_condition_survives_a_restart(store):
    """Enabling an API is a deliberate act in a console, so the condition
    persists until someone performs it. An in-memory flag would forget on every
    restart and the user would be told Drive worked again each morning."""
    _acct(store, "a@b.c", "connected")
    G.note_service_result("drive", False, "this API is disabled")
    assert (G._SERVICE_STATE_FILE).exists()
    on_disk = json.loads(G._SERVICE_STATE_FILE.read_text())
    assert on_disk["drive"]["blocked"] is True


def test_a_broken_account_outranks_a_degraded_service(store):
    """If the user must reconnect, say that first - it is the thing with an
    action attached."""
    _acct(store, "a@b.c", "needs_reauth")
    G.note_service_result("drive", False, "this API is disabled")
    assert G.service_health("drive").state == ch.NEEDS_USER


def test_services_switched_off_per_account_are_respected(store):
    """An account with Drive switched off in Friday must not make Drive look
    available just by existing."""
    _acct(store, "a@b.c", "connected", drive=False)
    _acct(store, "b@b.c", "needs_reauth", drive=True)
    assert G.service_health("drive").state == ch.NEEDS_USER
    assert G.service_health("gmail").state == ch.NEEDS_USER
