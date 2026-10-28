"""The Google card shows every permission the connect flow can ask for (north star NS-34.4-7).

google_accounts.build_auth_flow can add sending, mailbox changes, contacts
writes, calendar writes and task writes to the always-asked read scopes, each
only when ticked. The card listed the reads, sending and mailbox changes, but
not the calendar, task and contact writes, so Google's own consent screen could
show a permission the card never did. Every scope the flow can request is now on
the card in plain words, each write unticked by default, with the tick the
connect route reads.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

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


def test_every_scope_the_flow_can_ask_for_is_on_the_card_in_plain_words(monkeypatch):
    card = _card(monkeypatch)
    on_card = {p.get("scope"): p for p in card["permissions"]}
    missing = [s for s in _requestable() if s not in on_card]
    assert missing == [], "Google could ask for these and the card never said so"
    for p in card["permissions"]:
        assert not p["text"].startswith("http"), p


def test_each_extra_permission_is_unticked_and_is_the_tick_the_route_reads(monkeypatch):
    from agent_friday.services import google_accounts as ga
    card = _card(monkeypatch)
    route = (ROOT / "src" / "agent_friday" / "routes" / "google_accounts.py").read_text(encoding="utf-8")
    extra = [p for p in card["permissions"] if p.get("scope") not in ga.GOOGLE_MULTI_SCOPES]
    assert {p["scope"] for p in extra} >= {ga.CALENDAR_RW, ga.TASKS_RW, ga.CONTACTS_RW, ga.GMAIL_SEND, ga.GMAIL_MODIFY}
    for p in extra:
        assert p["optional"] is True and p["default"] is False, p
        assert 'body.get("%s")' % p["key"] in route, p["key"]