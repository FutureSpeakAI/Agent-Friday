"""Card numbers and bank or account numbers are on every never-send list by default (owner ruling, 2026-10-17).

* fresh and existing installs get both entries once, through a migration that says so once in Activity;
* the owner's own entries are kept; a removed default is never added back;
* the floor refuses a payload with a real card number or a bank/account number in every mode,
  "Cloud for everything" included, and lets order numbers and plain digit runs through;
* the owner removes either entry on the Locks page, by a click on that page only.

Synthetic numbers only (published test card and IBAN examples); no model, no network.
"""
from __future__ import annotations

import json

import pytest

from agent_friday import core
from agent_friday.services import judgment_gate as jg
from agent_friday.services import never_send_defaults as nsd
from agent_friday.services import privacy_shield_store as ps

CARD = "4111 1111 1111 1111"            # the well-known test Visa number (passes the checksum)
NOT_A_CARD = "4111 1111 1111 1112"      # same length, fails the checksum: an order number
IBAN = "GB82 WEST 1234 5698 7654 32"    # the standard example IBAN (valid checksum)
BAD_IBAN = "GB00 WEST 1234 5698 7654 32"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    jg._NEVER_CACHE.update({"mtime": -1.0, "items": []})
    pushed = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    yield tmp_path, pushed
    jg._NEVER_CACHE.update({"mtime": -1.0, "items": []})


def _shield(path):
    return json.loads((path / "privacy_shield.json").read_text(encoding="utf-8"))


# ── the migration ──────────────────────────────────────────────────────────────

def test_a_fresh_install_gets_both_entries_and_one_notice(home):
    path, pushed = home
    added = nsd.migrate()
    assert added == list(nsd.ENTRIES)
    assert ps.entries("never_send") == list(nsd.ENTRIES)
    assert _shield(path)["sources"]["never_send"] == {e: "default" for e in nsd.ENTRIES}
    assert len(pushed) == 1 and pushed[0]["emitter"] == "NS1"
    assert "Settings > Privacy & Data > Locks" in pushed[0]["body"]
    # once only
    assert nsd.migrate() == [] and len(pushed) == 1


def test_an_existing_list_keeps_the_owners_entries(home):
    path, _ = home
    (path / "privacy_shield.json").write_text(json.dumps(
        {"watchlist": ["Dana"], "never_send": ["Riverside Clinic"]}), encoding="utf-8")
    nsd.migrate()
    data = _shield(path)
    assert data["never_send"] == ["Riverside Clinic", *nsd.ENTRIES]
    assert data["watchlist"] == ["Dana"]


def test_a_removed_default_is_not_added_back(home):
    _, pushed = home
    nsd.migrate()
    assert ps.remove("never_send", nsd.CARD)
    assert nsd.migrate() == []
    assert nsd.CARD not in ps.entries("never_send") and nsd.BANK in ps.entries("never_send")
    assert len(pushed) == 1


def test_an_unreadable_shield_file_is_left_alone_and_retried(home):
    path, pushed = home
    (path / "privacy_shield.json").write_text("{not json", encoding="utf-8")
    assert nsd.migrate() == [] and not pushed
    assert (path / "privacy_shield.json").read_text(encoding="utf-8") == "{not json"
    (path / "privacy_shield.json").unlink()
    assert nsd.migrate() == list(nsd.ENTRIES)


def test_the_notice_is_registered():
    from agent_friday.services import notice_registry as reg
    em = reg.get("NS1")
    assert em is not None and em.place == reg.ACTIVITY


def test_the_server_start_runs_the_migration():
    import inspect
    from agent_friday.services import scheduler
    assert "never_send_defaults" in inspect.getsource(scheduler)


# ── the floor matches the kinds of number, not the words ─────────────────────

@pytest.mark.parametrize("text, hit", [
    ("Please charge card %s for the order." % CARD, nsd.CARD),
    ("The transfer goes to %s today." % IBAN, nsd.BANK),
    ("My account number: 12345678, sort code 12-34-56.", nsd.BANK),
    ("acct # 00123456789", nsd.BANK),
    ("routing number 021000021", nsd.BANK),
    ("Card: 4111-1111-1111-1111", nsd.CARD),
    ("Amex 3782 822463 10005", nsd.CARD),
])
def test_the_floor_finds_each_kind(home, text, hit):
    nsd.migrate()
    assert hit in jg.never_send_hits(text)


@pytest.mark.parametrize("text", [
    "Order %s shipped." % NOT_A_CARD,
    "Reference %s" % BAD_IBAN,
    "Tracking 123456789 arrives Tuesday.",
    "Where do I find my card numbers and account numbers in the app?",
    "Your Google account 2 is connected.",
    # spaced small numbers and dates are not a card or a routing number (critic review)
    "Scores: 1 2 3 4 5 6 7 8 9 0 1 2 3 4",
    "routing 2026-10-17 to the new queue",
    "ABA 021000021 is a format example",
    # millisecond timestamps and long platform ids start with 1 (critic re-review)
    "Logged at 1760692800000 by id 1234567890123456789",
])
def test_the_floor_lets_ordinary_text_through(home, text):
    nsd.migrate()
    assert jg.never_send_hits(text) == []


def test_cloud_for_everything_still_refuses_a_card_number(home, monkeypatch):
    nsd.migrate()
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    monkeypatch.setattr(eg, "_DEFAULT_LOG", home[0] / "egress-log.jsonl", raising=False)
    with pytest.raises(eg.NeverSendBlocked):
        eg._gate_text_span("Pay with %s please." % CARD, "anthropic", "messages")
    with pytest.raises(eg.NeverSendBlocked):
        eg._gate_text_span("Wire it to %s." % IBAN, "anthropic", "messages")
    # once the owner removes the entry, their choice of the cloud decides
    ps.remove("never_send", nsd.CARD)
    out = eg._gate_text_span("Pay with %s please." % CARD, "anthropic", "messages")
    assert CARD in out


# ── the owner removes an entry on the Locks page, and only there ──────────────

def _post(monkeypatch, entry, click):
    from flask import Flask
    from agent_friday.routes import locks as rl
    from agent_friday.services import screen_click
    monkeypatch.setattr(screen_click, "screen_session", lambda req: click)
    with Flask(__name__).test_request_context("/api/locks/never-send/remove", method="POST",
                                              json={"entry": entry}):
        resp = rl.locks_never_send_remove()
    resp, status = (resp if isinstance(resp, tuple) else (resp, 200))
    return status, resp.get_json()


def test_removal_needs_a_click_on_the_page(home, monkeypatch):
    nsd.migrate()
    status, body = _post(monkeypatch, nsd.CARD, click=False)
    assert status == 403 and nsd.CARD in ps.entries("never_send")
    status, body = _post(monkeypatch, nsd.CARD, click=True)
    assert status == 200 and body["ok"] and nsd.CARD not in ps.entries("never_send")
    assert "card numbers" in body["message"]
    status, body = _post(monkeypatch, nsd.CARD, click=True)
    assert status == 400 and not body["ok"]


def test_the_locks_page_lists_the_entries_by_name(home, monkeypatch):
    nsd.migrate()
    ps.add("never_send", ["Riverside Clinic"], source="setup")
    from agent_friday.services import locks
    where = next(line for line in locks.view()["lines"] if line["key"] == "where")
    assert [n["label"] for n in where["never_send"]] == ["Card numbers", "Bank and account numbers",
                                                         "Riverside Clinic"]
    assert "has 3 items" in where["words"]
    assert "ID and account numbers" not in where["words"]
