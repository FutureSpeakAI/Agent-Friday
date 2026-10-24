"""North star §34.4-7: what a connector card says it can access is what
Friday actually asks for.

The Google card on Settings > Connections (and in the setup chat) lists each
permission with its scope. These tests capture the scopes the real OAuth flow
requests for every combination of the card's optional ticks and require the
two to be the same set: nothing asked for that the card does not show, and
nothing shown that is never asked for. Every scope is shown in plain words.
"""
from __future__ import annotations

import itertools
import sys
import types

import pytest


def _google_card():
    from agent_friday.services import setup_connections as sc
    items = [i for i in sc._google_items() if i["id"] == "google"]
    assert items, "no Google card"
    return items[0]


@pytest.fixture
def captured(monkeypatch):
    """The scopes build_auth_flow hands Google, without a network or a client file."""
    from agent_friday.services import google_accounts as ga
    from agent_friday.services import google_oauth_client as goc
    seen = []

    class _Flow:
        @classmethod
        def from_client_config(cls, cfg, scopes=None, redirect_uri=None, state=None):
            seen.append(list(scopes or []))
            return cls()
    fake = types.ModuleType("google_auth_oauthlib.flow")
    fake.Flow = _Flow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", types.ModuleType("google_auth_oauthlib"))
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake)
    monkeypatch.setattr(goc, "active_client",
                        lambda discover=None: ({"installed": {"client_id": "x", "client_secret": "y",
                                                              "redirect_uris": ["http://localhost"]}},
                                               "test", "installed"))
    monkeypatch.setattr(ga, "multi_redirect_uri", lambda cfg, client_type=None: "http://localhost/cb")
    return seen


def test_the_card_lists_exactly_the_scopes_google_is_asked_for(captured):
    from agent_friday.services import google_accounts as ga
    card = _google_card()
    required = {p["scope"] for p in card["permissions"] if p.get("scope") and not p.get("optional")}
    optional = {p["key"]: p["scope"] for p in card["permissions"] if p.get("optional")}
    flags = ("include_send", "include_modify", "include_contacts_write")
    for key in flags:
        assert key in optional, "the card offers no tick for %s" % key
    for combo in itertools.product((False, True), repeat=len(flags)):
        kw = dict(zip(flags, combo))
        captured.clear()
        ga.build_auth_flow(state="s", **kw)
        asked = set(captured[-1])
        shown = required | {optional[k] for k, on in kw.items() if on}
        assert asked == shown, ("asked Google for %s, the card shows %s"
                                % (sorted(asked - shown), sorted(shown - asked)))


def test_every_scope_on_the_card_is_in_plain_words():
    for p in _google_card()["permissions"]:
        assert p.get("text") and "googleapis.com" not in p["text"], p


def test_the_card_does_not_understate_the_calendar_scope():
    from agent_friday.services import google_accounts as ga
    perm = next(p for p in _google_card()["permissions"] if p.get("scope") == ga.CALENDAR_RW)
    text = perm["text"].lower()
    assert "share" in text and "delete" in text, perm["text"]


def test_the_page_sends_every_tick_the_card_offers():
    """A tick the page shows but never posts would make the card lie."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    keys = [p["key"] for p in _google_card()["permissions"] if p.get("optional")]
    for page in ("index.html", "ui_parts/app.html"):
        src = (root / page).read_text(encoding="utf-8")
        for key in keys:
            assert "%s: !!opts.%s" % (key, key) in src, (page, key)


def test_the_github_card_does_not_claim_scopes_friday_never_requests():
    from agent_friday.services import setup_connections as sc
    text = " ".join(sc._CONNECTOR_PERMS["github"])
    assert "read:org" not in text and "two scopes" not in text
    assert "fine-grained" in text
