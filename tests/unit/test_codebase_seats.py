"""Seats, keys and the header line (salon spec §4.7, Phase 3).

A codebase carries a small routing record: which seat takes small edits,
which takes big ones, whose key pays. The header is one line that changes
the instant any of those changes, and a change is announced in the chat in
the seat_transparency style.
"""
from __future__ import annotations

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_resident_brain", lambda: ("bonsai2:27b", "Bonsai2"))
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_total", lambda cid, rng="all": 1.84)
    yield


def _new():
    conv = convs.create("Rent tracker")
    return cb.create("Rent tracker", conversation_id=conv["id"]), conv


def test_a_new_codebase_has_the_default_seats_and_the_owners_key():
    rec, _ = _new()
    assert rec["seats"] == {"small_edit_seat": "local", "heavy_seat": None, "engine": "friday"}
    assert rec["key_profile"] == "mine"
    # An older record without the fields reads the same defaults.
    raw = cb.load(rec["id"])
    raw.pop("seats"); raw.pop("key_profile"); cb._save(raw)
    assert cb.load(rec["id"])["seats"]["small_edit_seat"] == "local" and cb.load(rec["id"])["key_profile"] == "mine"


def test_setting_a_seat_changes_the_record_announces_it_and_is_checked():
    rec, conv = _new()
    seen = []
    from agent_friday.services import desktop_bus
    import agent_friday.services.codebases as _m
    monkey = pytest.MonkeyPatch(); monkey.setattr(desktop_bus, "broadcast", lambda ev, kind="chat": seen.append(ev) or 1)
    try:
        out = cb.set_seat(rec["id"], "heavy", "claude-opus-5-5", by="you")
    finally:
        monkey.undo()
    assert out["seats"]["heavy_seat"] == "claude-opus-5-5"
    msgs = convs.messages(conv["id"])
    line = [m for m in msgs if m.get("kind") == "seat_change"][-1]
    assert line["role"] == "system" and line["text"].startswith("⚙") and "Opus 5.5" in line["text"] and "big" in line["text"].lower()
    assert any(e.get("type") == "codebase_header" and e.get("codebase_id") == rec["id"] for e in seen)
    with pytest.raises(ValueError):
        cb.set_seat(rec["id"], "medium", "claude-opus-5-5")
    with pytest.raises(ValueError):
        cb.set_seat(rec["id"], "small", "")          # small edits always need a seat
    assert cb.set_seat(rec["id"], "heavy", "")["seats"]["heavy_seat"] is None   # empty clears the heavy seat
    # The small-edit seat may be "local" (the resident brain) or a named model.
    assert cb.set_seat(rec["id"], "small", "local")["seats"]["small_edit_seat"] == "local"


def test_the_key_profile_is_mine_or_a_guest_key_this_codebase_knows():
    rec, _ = _new()
    assert cb.set_key_profile(rec["id"], "mine")["key_profile"] == "mine"
    with pytest.raises(ValueError):
        cb.set_key_profile(rec["id"], "alex")          # no such guest key yet
    assert cb.guest_keys(rec["id"]) == []


def test_small_and_heavy_are_decided_by_a_rule_friday_can_explain():
    rec, conv = _new()
    assert cb.is_heavy(rec["id"], "make the heading blue") is False
    assert cb.is_heavy(rec["id"], "x" * 401) is True
    assert cb.is_heavy(rec["id"], "add a feature: a page that lists overdue rent") is True
    assert cb.is_heavy(rec["id"], "rewrite the layout across several files") is True
    # An approved plan with an open milestone is heavy work by definition.
    from agent_friday.services import plans
    p = plans.create(conv["id"], "Plan", "do things", ["one", "two"])
    plans.approve(conv["id"], p["id"], by="you")
    assert cb.is_heavy(rec["id"], "ok go") is True


def test_seat_for_follows_the_record_and_the_resident_brain(monkeypatch):
    rec, conv = _new()
    # No heavy seat chosen: the chat follows the default whatever the size.
    assert cb.seat_for(rec["id"], "x" * 500) is None
    cb.set_seat(rec["id"], "heavy", "claude-opus-5-5")
    assert cb.seat_for(rec["id"], "make the heading blue") is None
    assert cb.seat_for(rec["id"], "x" * 500) == {"model": "claude-opus-5-5"}
    assert cb.seat_for_conversation(conv["id"], "x" * 500) == {"model": "claude-opus-5-5"}
    # No local model resident: the heavy seat takes everything, and says so.
    monkeypatch.setattr(cb, "_resident_brain", lambda: None)
    assert cb.seat_for(rec["id"], "make the heading blue") == {"model": "claude-opus-5-5"}
    assert cb.seat_for_conversation("conv-none", "x") is None


def test_the_header_is_one_line_with_a_spoken_form(monkeypatch):
    rec, _ = _new()
    h = cb.header(rec["id"])
    assert h["text"] == "Rent tracker · Bonsai2 (this PC) for small edits · no heavy seat yet · your key · this codebase: $1.84"
    assert h["local_resident"] is True and h["red"] is False and h["cost_usd"] == 1.84
    cb.set_seat(rec["id"], "heavy", "claude-opus-5-5")
    h = cb.header(rec["id"])
    assert h["text"] == "Rent tracker · Bonsai2 (this PC) for small edits · Opus 5.5 for big ones · your key · this codebase: $1.84"
    assert "Bonsai2" in h["spoken"] and "Opus 5.5" in h["spoken"] and "1.84" in h["spoken"] or "1 dollar" in h["spoken"]
    # A cloud-only laptop: the header says so, it does not pretend.
    monkeypatch.setattr(cb, "_resident_brain", lambda: None)
    h = cb.header(rec["id"])
    assert h["text"].startswith("Rent tracker · no local model resident: Opus 5.5 for everything")
    assert h["local_resident"] is False
    cb.set_seat(rec["id"], "heavy", "")  # unset again
    h = cb.header(rec["id"])
    assert "no local model resident and no heavy seat" in h["text"]


def test_model_names_read_as_people_say_them():
    assert cb.model_short("claude-opus-5-5") == "Opus 5.5"
    assert cb.model_short("bonsai2:27b") == "Bonsai2"
    assert cb.model_short("some-unknown-model") == "some-unknown-model"
    assert cb.model_from_words("Opus 5.5") == "claude-opus-5-5"
    assert cb.model_from_words("claude-opus-5-5") == "claude-opus-5-5"
    assert cb.model_from_words("opus 5.5") == "claude-opus-5-5"
    assert cb.model_from_words("nothing like this") is None


def test_the_model_is_told_its_seats_and_key():
    rec, _ = _new()
    cb.set_seat(rec["id"], "heavy", "claude-opus-5-5")
    block = cb.context_block_for(rec["id"])
    assert "Seats:" in block and "Opus 5.5" in block and "your key" in block.lower()
