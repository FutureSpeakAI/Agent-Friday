"""The Google card names every permission Google can be asked for (north star NS-34.4-7).

google_accounts.build_auth_flow can add sending, mailbox changes, contacts
writes, calendar writes and task writes to the always-asked read scopes. The card
listed the reads, sending and mailbox changes, but not the calendar, task and
contact writes, so Google's consent screen could one day show a permission the
card never named. Those three are asked for later, each only from its own button
(read first, BD-06), never on the ordinary connect; the card now lists them under
"Asked for later, never on this connect", naming the button. Sending and mailbox
changes stay unticked options on the connect itself, and the page sends exactly
those ticks.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _card(monkeypatch):
    from agent_friday.services import google_accounts as ga, setup_connections as sc
    monkeypatch.setattr(ga, "accounts_summary", lambda: {"total": 0, "healthy": 0})
    return sc._google_items()[0]


def _requestable():
    """Every scope build_auth_flow can put on Google's consent screen."""
    from agent_friday.services import google_accounts as ga
    src = inspect.getsource(ga.build_auth_flow)
    names = re.findall(r"scopes\.append\((\w+)\)", src)
    assert names, "build_auth_flow no longer appends scopes by name; update this test"
    return list(ga.GOOGLE_MULTI_SCOPES) + [getattr(ga, n) for n in names]


def test_every_scope_google_can_be_asked_for_is_on_the_card_in_plain_words(monkeypatch):
    card = _card(monkeypatch)
    on_card = {p.get("scope"): p for p in card["permissions"]}
    missing = [s for s in _requestable() if s not in on_card]
    assert missing == [], "Google could ask for these and the card never said so"
    for p in card["permissions"]:
        assert not p["text"].startswith("http"), p


def test_the_writes_asked_for_later_name_their_button_and_are_never_on_this_connect(monkeypatch):
    from agent_friday.services import google_accounts as ga
    card = _card(monkeypatch)
    later = {p["scope"]: p for p in card["permissions"] if p.get("later")}
    assert set(later) == {ga.CALENDAR_RW, ga.TASKS_RW, ga.CONTACTS_RW}
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    for p in later.values():
        assert not p.get("optional"), "a later permission is not a tick on this connect"
        assert p["later"] in page, "the button it names exists: " + p["later"]


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_card_lists_them_apart_and_sends_exactly_the_ticks_it_offers(rel, monkeypatch):
    s = (ROOT / rel).read_text(encoding="utf-8")
    assert "const required = (it.permissions || []).filter(p => !p.optional && !p.later);" in s
    assert "const later = (it.permissions || []).filter(p => p.later);" in s
    assert "Asked for later, never on this connect" in s
    card = _card(monkeypatch)
    i = s.index("c.kind === 'google_oauth'")
    post = s[i:s.index("openUrl(d.auth_url)", i)]
    for p in card["permissions"]:
        if p.get("optional"):
            assert p["key"] in post, "the page sends the tick it offers: " + p["key"]