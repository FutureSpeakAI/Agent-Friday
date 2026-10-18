"""A background-context chunk that changes every turn cannot raise a card every turn.

Friday's background context is rebuilt on every turn, and a part of it that changes each time (a clock,
a count, a rotating note) made every turn a "new" private text and a new card. The guard
(services/cloud_release.py, "one card per conversation per distinct text"):
  * cards are compared by content (case, spacing and digits ignored) and provider;
  * a conversation shows at most one card for a given content and provider;
  * while a conversation has a card waiting for a provider, details held meanwhile wait for it;
  * Send holds for the exact words, for the rest of the conversation it was asked in, to that provider;
  * the conversation is kept on the web request, never inherited by a later request on the thread.
The details stay on this PC throughout. Synthetic text only; the classifier is pinned.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import cloud_release as cr
from agent_friday.services import egress_gate as eg

pytestmark = pytest.mark.held_cards

MARK = "PRIVATE-MARK"
PUBLIC = "What should I cook tonight?"
REPO = Path(__file__).resolve().parents[2]
WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]


@pytest.fixture
def gate(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "_path", lambda: tmp_path / "cloud_release.json")
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: False)
    monkeypatch.setattr(eg, "_classify_cloud",
                        lambda text: eg.Tier.SENSITIVE if MARK in str(text) else eg.Tier.PUBLIC)
    monkeypatch.setattr(eg, "_run_appeals", lambda appeals, gated, *a, **k: 0)
    monkeypatch.setattr(eg, "_DEFAULT_LOG", tmp_path / "egress.jsonl", raising=False)
    from agent_friday.services import judgment_gate as jg
    monkeypatch.setattr(jg, "never_send_tokens", lambda: [])
    yield tmp_path


def _turn(background, conversation="conv-1", provider="anthropic", model="claude-sonnet-5"):
    """One chat turn: the conversation is named (as routes/chat.py does) and the request sealed.
    (Before the guard there was no conversation to name: the turn is sealed as it was then.)"""
    var = getattr(cr, "CONVERSATION", None)
    tok = cr.set_conversation(conversation) if hasattr(cr, "set_conversation") else None
    try:
        out = eg.seal_outbound({"system": "You are Friday.\n\n" + background,
                                "messages": [{"role": "user", "content": PUBLIC}]},
                               provider, model=model)
    finally:
        if tok is not None:
            var.reset(tok)
    return out


def _sent_system(out):
    s = out["system"]
    return s if isinstance(s, str) else json.dumps(s)


def _cards():
    return [c for c in ap.list_approvals() if (c.get("payload") or {}).get("general")]


def _clock(n):
    return "At 10:%02d the PRIVATE-MARK reminder says the clinic results are due." % n


def test_a_background_chunk_that_changes_every_turn_raises_one_card(gate):
    for n in range(10):
        out = _turn(_clock(n))
        assert MARK not in _sent_system(out), "the private detail left the PC"
    cards = _cards()
    assert len(cards) == 1, "a card on every turn: %d" % len(cards)
    assert cards[0]["payload"]["where"]["parts"] == ["Friday's background context for this request"]
    assert cards[0]["payload"]["conversation"] == "conv-1"


def test_one_card_waits_at_a_time_per_conversation_and_provider(gate):
    for w in WORDS[:3]:
        out = _turn("The PRIVATE-MARK note today is %s." % w)
        assert MARK not in _sent_system(out)
    assert len(_cards()) == 1
    # Answered: the next turn's new detail may ask, once.
    cr.decide(_cards()[0]["approval_id"], "deny", decided_by="owner")
    for w in WORDS[3:]:
        _turn("The PRIVATE-MARK note today is %s." % w)
    assert len(_cards()) == 2


def test_a_conversation_never_shows_the_same_content_twice(gate):
    _turn(_clock(1))
    card = _cards()[0]
    assert "this conversation's next cloud calls" in card["description"]
    assert "comes back changed" in card["description"]
    cr.decide(card["approval_id"], "approve", decided_by="owner:ui")
    # Send holds for the conversation it was asked in, with no new card.
    assert _clock(1) in _sent_system(_turn(_clock(1)))
    assert _clock(1) in _sent_system(_turn(_clock(1)))
    # The same content with a new time in that conversation: kept here, no second card.
    out = _turn(_clock(2))
    assert MARK not in _sent_system(out)
    assert len(_cards()) == 1
    # Another conversation: the exact words the first one sent stay here until it is asked there.
    assert MARK not in _sent_system(_turn(_clock(1), conversation="conv-2"))
    _turn(_clock(4), conversation="conv-2")
    assert sorted(c["payload"]["conversation"] for c in _cards()) == ["conv-1", "conv-2"]


def test_a_send_in_a_conversation_is_for_that_provider_only(gate):
    _turn(_clock(1))
    cr.decide(_cards()[0]["approval_id"], "approve", decided_by="owner:ui")
    out = _turn(_clock(1), provider="openrouter", model="some/model")
    assert MARK not in _sent_system(out)
    assert sorted(c["payload"]["where"]["provider"] for c in _cards()) == ["anthropic", "openrouter"]


def test_a_send_with_no_conversation_runs_out(gate):
    _turn(_clock(1), conversation=None)
    card = _cards()[0]
    assert "the next cloud calls in the next 30 minutes" in card["description"]
    cr.decide(card["approval_id"], "approve", decided_by="owner:ui")
    assert _clock(1) in _sent_system(_turn(_clock(1), conversation=None))
    data = json.loads((gate / "cloud_release.json").read_text(encoding="utf-8"))
    data["once"] = {}
    (gate / "cloud_release.json").write_text(json.dumps(data), encoding="utf-8")
    assert MARK not in _sent_system(_turn(_clock(1), conversation=None))


def test_inside_a_request_the_conversation_is_the_requests_never_the_threads_leftover(gate):
    from flask import Flask
    app = Flask(__name__)
    tok = cr.CONVERSATION.set("left-over")           # a reused thread's earlier context
    try:
        with app.test_request_context("/api/chat", method="POST"):
            assert cr._conversation() == ""
            cr.set_conversation("conv-7")
            assert cr._conversation() == "conv-7"
        with app.test_request_context("/api/other"):
            assert cr._conversation() == "", "a later request inherited the conversation"
        assert cr.CONVERSATION.get() == "left-over"   # the request never wrote the thread's value
    finally:
        cr.CONVERSATION.reset(tok)


def test_a_card_that_was_not_created_does_not_count_as_shown(gate, monkeypatch):
    real = cr._raise_held_card
    monkeypatch.setattr(cr, "_raise_held_card", lambda *a, **k: None)
    _turn(_clock(1))
    monkeypatch.setattr(cr, "_raise_held_card", real)
    _turn(_clock(2))
    assert len(_cards()) == 1


def test_a_damaged_ledger_row_does_not_stop_cards(gate):
    (gate / "cloud_release.json").write_text(json.dumps(
        {"carded": {"conv-1": {"c:x@anthropic": "not a row", "c:y@anthropic": [None]}}}), encoding="utf-8")
    _turn(_clock(1))
    assert len(_cards()) == 1


def test_the_same_content_waiting_elsewhere_is_not_carded_again(gate):
    _turn(_clock(1), conversation="conv-1")
    _turn(_clock(2), conversation="conv-2")
    assert len(_cards()) == 1


def test_dedupe_is_by_content_and_provider(gate):
    _turn(_clock(1))
    _turn(_clock(2), provider="openrouter", model="some/model")
    _turn(_clock(3), provider="openrouter", model="some/model")
    assert sorted(c["payload"]["where"]["provider"] for c in _cards()) == ["anthropic", "openrouter"]


def test_keep_on_this_pc_holds_for_the_conversation_and_another_may_ask_once(gate):
    _turn(_clock(1))
    cr.decide(_cards()[0]["approval_id"], "deny", decided_by="owner")
    assert MARK not in _sent_system(_turn(_clock(42)))
    assert len(_cards()) == 1
    assert MARK not in _sent_system(_turn(_clock(43), conversation="conv-9"))
    _turn(_clock(44), conversation="conv-9")
    assert len(_cards()) == 2


def test_calls_with_no_conversation_share_one_bucket(gate):
    for n in range(5):
        _turn(_clock(n), conversation=None)
    assert len(_cards()) == 1


def test_the_content_key_ignores_case_spacing_and_digits_only():
    assert cr.content_key("At 10:01  the Note") == cr.content_key("at 11:59 the note")
    assert cr.content_key("the note") != cr.content_key("the other note")


def test_both_chat_routes_name_the_conversation_as_soon_as_it_is_resolved():
    src = (REPO / "src" / "agent_friday" / "routes" / "chat.py").read_text(encoding="utf-8-sig")
    named = "_conversation_id = _conv_id_from(data)\n"
    assert src.count(named) == 2
    for at in [i for i in range(len(src)) if src.startswith(named, i)]:
        nxt = src[at + len(named):at + len(named) + 400]
        assert "_cloud_release_conversation(_conversation_id)" in nxt


# ── a typed Send of a held detail names where it goes ─────────────────────────────────────

def _typed(card_id, words, decision="approve"):
    """The owner's typed words reach answer_card the way a chat turn does (agent._tool_answer_card)."""
    from agent_friday.services import agent
    toks = [agent._CURRENT_SURFACE.set("chat"), agent._CURRENT_OWNER_TEXT.set(words)]
    try:
        return agent._tool_answer_card({"card_id": card_id, "decision": decision})
    finally:
        agent._CURRENT_OWNER_TEXT.reset(toks[1])
        agent._CURRENT_SURFACE.reset(toks[0])


def test_a_bare_typed_yes_releases_no_held_detail(gate):
    _turn(_clock(1))
    cid = _cards()[0]["approval_id"]
    for words in ("yes", "ok", "continue", "yes, go ahead", "yes"):    # a model's "shall I continue?"
        said = _typed(cid, words)
        assert ap.get_approval(cid)["status"] == "pending", "a bare %r sent a held detail" % words
        assert said.startswith("NOT SENT") and 'Type "send it to Anthropic"' in said, said
    assert MARK not in _sent_system(_turn(_clock(1)))


def test_a_typed_send_naming_the_cards_provider_releases_exactly_that_text_there(gate):
    _turn(_clock(1))
    cid = _cards()[0]["approval_id"]
    said = _typed(cid, "Yes, send it to anthropic.")
    assert ap.get_approval(cid)["status"] == "approved", said
    assert ap.get_approval(cid)["decided_by"] == "owner:chat"
    assert _clock(1) in _sent_system(_turn(_clock(1)))
    # That card's text, to that provider, in that conversation only.
    assert MARK not in _sent_system(_turn(_clock(1), conversation="conv-2"))
    assert MARK not in _sent_system(_turn(_clock(1), provider="openrouter", model="some/model"))
    assert MARK not in _sent_system(_turn("The PRIVATE-MARK note today is alpha."))


def test_naming_a_different_provider_releases_nothing(gate):
    _turn(_clock(1))
    cid = _cards()[0]["approval_id"]
    for words in ("send it to OpenRouter", "send it to Anthropic or OpenRouter",
                  "send it to Anthropic but not the clinic part", "don't send it to Anthropic"):
        said = _typed(cid, words)
        assert ap.get_approval(cid)["status"] == "pending", words
        assert "send it to Anthropic" in said
    assert MARK not in _sent_system(_turn(_clock(1)))


def test_the_matcher_wants_send_and_the_cards_provider_as_whole_words():
    yes = [("send it to Anthropic", "anthropic"), ("yes, send to OpenRouter", "openrouter"),
           ("ok send this to Claude", "anthropic"), ("SEND IT TO THE OPEN ROUTER API", "openrouter")]
    no = [("yes", "anthropic"), ("send it", "anthropic"), ("send it to Anthropicx", "anthropic"),
          ("send it to anthropic-mirror", "anthropic"), ("send it to Anthropic?", "anthropic"),
          ("send it to Anthropic", ""), ("send it to Anthropic and summarise my email", "anthropic")]
    assert all(cr.typed_send_names_provider(w, p) for w, p in yes)
    assert not any(cr.typed_send_names_provider(w, p) for w, p in no)


def test_a_money_card_answered_in_typed_words_keeps_its_read_back_rule(gate):
    # The money-paragraph card shares answer_in_words but not the held-detail rule: a typed yes after
    # the read-back still sends it once, without naming a provider.
    card = cr._raise_card([{"hash": "h-money-1", "text": "The hall deposit of $400 is due by 1 May.",
                            "thread": "mail:acct_a:t-hall", "sender": "robin@example.test",
                            "path": "messages[0].text"}], provider="anthropic")
    cid = card["approval_id"]
    first = _typed(cid, "yes")
    assert first.startswith("NOT SENT YET") and ap.get_approval(cid)["status"] == "pending", first
    done = _typed(cid, "yes please")
    assert ap.get_approval(cid)["status"] == "approved", done
    assert "sent once" in done.lower()
