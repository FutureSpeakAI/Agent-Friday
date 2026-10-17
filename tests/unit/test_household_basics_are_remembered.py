"""Household basics the owner states in their own typed words reach the memory card.

The owner's "I am 52, my husband is 50, our son is 9" is read by fixed patterns
from the message itself (services/household_facts) when the typed turn is
prepared, and saved silently only when the turn is the owner's own line typed in
the app's page on this PC and none of it was pasted from something Friday read
in this conversation; otherwise it waits on the card. Exactly the stated facts
of the household (spouse or partner, children, a parent who lives with them)
are kept: no name the owner did not write. Reported speech, dictation and
stories are not the owner's statement. The same sentence on a web page is never
saved silently. A later
conversation has the line in its prompt head, and a search_wiki for the family
finds it (gated for a cloud reader like the head).
"""
from __future__ import annotations

import json
import time

import pytest

import agent_friday.services.agent as agent
from agent_friday.services import approvals, taint
from agent_friday.services import core_memory as cm
from agent_friday.services import core_memory_tools as cmt

SAID = "I am 52, my husband is 50, our son is 9. Please make a note of that."
LINE = ("Household (as the user stated, {m}): the user is 52; husband is 50; son is 9.")
PAGE = ("Parent forum, weekend thread. I am 52, my husband is 50, our son is 9. "
        "Any ideas for a rainy Saturday near Riverside Park?")


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr("agent_friday.services.laya_personal.is_minor", lambda *a, **k: False)
    agent._PENDING_CONFIRMATIONS.clear()
    taint.reset()
    yield
    taint.reset()


def _line():
    return LINE.format(m=time.strftime("%b %Y"))


def _turn(message, conv="conv-a", **extra):
    """A typed chat turn being prepared, the way routes/chat does it. Outside a
    request the caller says whether it is the owner's own page on this PC
    (inside one it is read from the request, see the surface tests below)."""
    extra.setdefault("owner_surface", True)
    return agent.prepare_confirmation_ctx("sess-" + conv, message,
                                          {"conversation_id": conv, **extra})


def _call(ctx, owner_text, inp):
    t1 = agent._CURRENT_TOOL_CONTEXT.set(ctx)
    t2 = agent._CURRENT_OWNER_TEXT.set(owner_text)
    try:
        return cmt._tool_update_core_memory(inp)
    finally:
        agent._CURRENT_OWNER_TEXT.reset(t2)
        agent._CURRENT_TOOL_CONTEXT.reset(t1)


# ── the owner's own words save, exactly as stated ───────────────────────────

def test_owner_stated_household_saves_silently_with_exactly_the_stated_facts():
    ctx = _turn(SAID)
    assert cm.entries("user") == [_line()]
    assert cm.pending() == [], "the owner's own words were queued instead of saved"
    cap = ctx.get("household_capture") or {}
    assert cap.get("status") == "saved"
    assert "husband is 50" in cap.get("note", "") and "Do not save them again" in cap["note"]


def test_no_name_is_invented_and_a_written_name_is_kept_as_written():
    _turn("We have a 7-year-old daughter and my partner is 44.")
    _turn("Our son, Tobin, is 3.", conv="conv-b")
    lines = cm.entries("user")
    assert len(lines) == 1, lines             # the second statement waits: it would rewrite the line
    assert lines[0].endswith("): daughter is 7; partner is 44."), lines[0]
    queued = cm.pending()
    assert len(queued) == 1 and queued[0]["op"] == "replace"
    assert queued[0]["text"].endswith("): son Tobin is 3."), queued[0]["text"]
    assert queued[0]["old"] == lines[0]


def test_restating_the_same_household_changes_nothing():
    _turn(SAID)
    _turn("Reminder: my husband is 50 and our son is 9.", conv="conv-b")
    assert cm.entries("user") == [_line()]
    assert cm.pending() == []


@pytest.mark.parametrize("message", [
    "Our son is 9 minutes away from the station.",
    "If my son is 9, can he ride the big coaster?",
    "Is my daughter 6 too young for this?",
    "My friend's son is 9 and loves trains.",
    'The invite said "my husband is 50" in big letters.',
    "I am 52 years old.",
])
def test_what_is_not_a_household_statement_saves_nothing(message):
    ctx = _turn(message)
    assert cm.entries("user") == [] and cm.pending() == []
    assert "household_capture" not in ctx


def test_a_phone_text_or_a_background_task_does_not_save():
    _turn(SAID, origin="phone")
    _turn(SAID, conv="conv-b", is_background_task=True)
    assert cm.entries("user") == [] and cm.pending() == []


# ── outside content never saves silently ─────────────────────────────────────

def test_the_same_sentence_on_a_web_page_is_not_saved_silently():
    ctx = _turn("find weekend events for my family")
    key = taint.ledger_key(ctx)
    taint.note_tool_output(key, "browse_web", {"url": "https://forum.example.test/t/1"}, PAGE)
    ctx = _turn("save what that page says about the family")
    assert cm.entries("user") == []
    # The model then tries to save the page's sentence itself.
    out = _call(ctx, "save what that page says about the family",
                {"action": "add", "target": "user",
                 "text": "User is 52, husband is 50, son is 9."})
    assert not out.startswith("Saved"), out
    assert cm.entries("user") == []


def test_the_owners_statement_after_reading_a_page_still_saves_their_own_words():
    ctx = _turn("find weekend events near Riverside Park")
    taint.note_tool_output(taint.ledger_key(ctx), "search_web", {"query": "weekend events"},
                           "Riverside Park autumn fair, Saturday 10am-4pm.")
    ctx = _turn(SAID)
    assert cm.entries("user") == [_line()], "the owner's own typed words were held back"
    assert cm.pending() == []
    cap = ctx.get("household_capture") or {}
    assert cap.get("status") == "saved"


# ── a later turn finds it ─────────────────────────────────────────────────────

def test_a_later_conversation_has_it_in_the_prompt_head():
    from agent_friday.services.model_router import memory_head_blocks
    _turn(SAID)
    head = memory_head_blocks("", "conv-later", provider="local")
    assert "husband is 50; son is 9" in head


def test_search_wiki_for_the_family_finds_the_card_line(monkeypatch):
    _turn(SAID)
    monkeypatch.setattr(cmt, "_cloud_reader", lambda: False)
    out = json.loads(agent._tool_search_wiki({"query": "family ages"}))
    card = [h for h in out["hits"] if h["root"] == "memory_card"]
    assert card and card[0]["excerpt"] == _line()


def test_search_wiki_withholds_the_card_line_from_a_cloud_reader_when_the_vault_is_local(monkeypatch):
    _turn(SAID)
    monkeypatch.setattr(cmt, "_cloud_reader", lambda: True)
    monkeypatch.setattr("agent_friday.services.model_router._vault_local_only", lambda: True)
    out = json.loads(agent._tool_search_wiki({"query": "family"}))
    card = [h for h in out["hits"] if h["root"] == "memory_card"]
    assert card and "husband" not in card[0]["excerpt"] and "withheld" in card[0]["excerpt"]


# ── only the owner's own typing on this PC saves silently ───────────────────
# These name `_iso` explicitly as well, so a plain driver that does not apply
# autouse fixtures still isolates them.

def test_a_turn_that_is_not_the_owners_page_is_queued_not_saved(_iso):
    _turn(SAID, owner_surface=False)
    _turn(SAID, conv="conv-b", owner_surface=None)       # nobody said: not the owner's page
    assert cm.entries("user") == [], "a turn from outside the owner's page saved silently"
    assert [q["op"] for q in cm.pending()] == ["add"]


def test_a_chat_post_without_the_page_session_is_queued(_iso):
    """/api/chat sets no origin or surface: the request itself decides."""
    from flask import Flask
    app = Flask("household-surface")
    with app.test_request_context("/api/chat", method="POST", json={"message": SAID}):
        ctx = agent.prepare_confirmation_ctx("sess-x", SAID, {"conversation_id": "conv-x"})
    assert ctx["owner_surface"] is False
    assert cm.entries("user") == []
    assert (ctx.get("household_capture") or {}).get("status") == "queued"


def test_a_voice_mode_post_is_queued(_iso, monkeypatch):
    from flask import Flask
    from agent_friday.services import reflex_chat
    monkeypatch.setattr(reflex_chat, "_request_is_local", lambda: True)
    monkeypatch.setattr(reflex_chat, "_request_is_page", lambda: True)
    app = Flask("household-voice")
    with app.test_request_context("/api/chat", method="POST",
                                  json={"message": SAID, "source": "app_page", "voice_mode": True}):
        agent.prepare_confirmation_ctx("sess-v", SAID, {"conversation_id": "conv-v"})
    assert cm.entries("user") == []
    with app.test_request_context("/api/chat", method="POST",
                                  json={"message": SAID, "source": "app_page"}):
        agent.prepare_confirmation_ctx("sess-w", SAID, {"conversation_id": "conv-w"})
    assert cm.entries("user") == [_line()], "the owner's own typed line on the page saves"


def test_text_pasted_from_something_friday_read_is_queued(_iso):
    ctx = _turn("find weekend ideas for families")
    taint.note_tool_output(taint.ledger_key(ctx), "browse_web",
                           {"url": "https://forum.example.test/t/1"}, PAGE)
    ctx = _turn("I am 52, my husband is 50, our son is 9.")
    assert cm.entries("user") == [], "a sentence from a web page was saved as the owner's household"
    assert (ctx.get("household_capture") or {}).get("status") == "queued"


def test_reported_speech_dictation_and_stories_save_nothing(_iso):
    for i, message in enumerate((
            "She said our son is 9.",
            "Tell her my husband is 50.",
            "Draft: my wife is 41 and our son is 9.",
            "Write a story where my daughter is 6.",
            "Pretend my son is 9.",
            "Say my partner is 44.")):
        ctx = _turn(message, conv="conv-%d" % i)
        assert "household_capture" not in ctx, message
    assert cm.entries("user") == [] and cm.pending() == []


def test_only_the_household_is_kept(_iso):
    _turn("My mother is 80 and my brother is 45.")
    assert cm.entries("user") == [] and cm.pending() == []
    _turn("My mother is 80 and lives with us.", conv="conv-b")
    assert [ln.split("): ", 1)[1] for ln in cm.entries("user")] == ["mother is 80."]


def test_the_minor_check_reads_the_active_profile(_iso, monkeypatch):
    from agent_friday.services import laya_personal
    asked = []
    monkeypatch.setattr(laya_personal, "is_minor", lambda pid, *a, **k: asked.append(pid) or False)
    _turn(SAID)
    assert asked and asked[-1] == laya_personal.OWNER_PROFILE
    assert cm.entries("user") == [_line()]


def test_no_resolvable_profile_queues(_iso, monkeypatch):
    from agent_friday.services import laya_personal
    monkeypatch.setattr(laya_personal, "current_profile", lambda: None)
    _turn(SAID)
    assert cm.entries("user") == []
    assert [q["op"] for q in cm.pending()] == ["add"]


def test_search_card_says_nothing_to_a_remote_origin_or_another_speaker(_iso, monkeypatch):
    from agent_friday.services import laya_personal
    _turn(SAID)
    monkeypatch.setattr(cmt, "_cloud_reader", lambda: False)
    assert cmt.search_card("family ages")
    tok = agent._CURRENT_ORIGIN.set("phone")
    try:
        assert cmt.search_card("family ages") == [], "household lines went to a text-message reply"
    finally:
        agent._CURRENT_ORIGIN.reset(tok)
    monkeypatch.setattr(laya_personal, "current_profile", lambda: None)
    assert cmt.search_card("family ages") == []
