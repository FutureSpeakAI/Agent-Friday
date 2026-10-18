"""A background-context chunk that changes every turn cannot raise a card every turn.

Friday's background context is rebuilt on every turn, and a part of it that changes each time (a clock,
a count, a rotating note) made every turn a "new" private text and a new card. The guard
(services/cloud_release.py, "one card per conversation per distinct text"):
  * cards are compared by content (case, spacing and digits ignored) and provider;
  * a conversation shows at most one card for a given content and provider;
  * while a conversation has a card waiting for a provider, details held meanwhile wait for it;
  * Send holds for the rest of the conversation it was asked in; Keep on this PC covers the content.
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
    cr.decide(card["approval_id"], "approve", decided_by="owner:ui")
    # The send-once window runs out; the conversation it was asked in still sends it, without a new card.
    data = json.loads((gate / "cloud_release.json").read_text(encoding="utf-8"))
    data["once"] = {}
    (gate / "cloud_release.json").write_text(json.dumps(data), encoding="utf-8")
    assert _clock(1) in _sent_system(_turn(_clock(1)))
    # The same content with a new time in that conversation: no second card.
    out = _turn(_clock(2))
    assert MARK not in _sent_system(out)
    assert len(_cards()) == 1
    # Another conversation was never asked: it gets its one card.
    assert MARK not in _sent_system(_turn(_clock(3), conversation="conv-2"))
    _turn(_clock(4), conversation="conv-2")
    assert sorted(c["payload"]["conversation"] for c in _cards()) == ["conv-1", "conv-2"]


def test_the_same_content_waiting_elsewhere_is_not_carded_again(gate):
    _turn(_clock(1), conversation="conv-1")
    _turn(_clock(2), conversation="conv-2")
    assert len(_cards()) == 1


def test_dedupe_is_by_content_and_provider(gate):
    _turn(_clock(1))
    _turn(_clock(2), provider="openrouter", model="some/model")
    _turn(_clock(3), provider="openrouter", model="some/model")
    assert sorted(c["payload"]["where"]["provider"] for c in _cards()) == ["anthropic", "openrouter"]


def test_keep_on_this_pc_covers_the_content_not_only_the_exact_words(gate):
    _turn(_clock(1))
    cr.decide(_cards()[0]["approval_id"], "deny", decided_by="owner")
    out = _turn(_clock(42), conversation="conv-9")
    assert MARK not in _sent_system(out)
    assert len(_cards()) == 1


def test_calls_with_no_conversation_share_one_bucket(gate):
    for n in range(5):
        _turn(_clock(n), conversation=None)
    assert len(_cards()) == 1


def test_the_content_key_ignores_case_spacing_and_digits_only():
    assert cr.content_key("At 10:01  the Note") == cr.content_key("at 11:59 the note")
    assert cr.content_key("the note") != cr.content_key("the other note")


def test_both_chat_routes_name_the_conversation():
    src = (REPO / "src" / "agent_friday" / "routes" / "chat.py").read_text(encoding="utf-8-sig")
    assert src.count("set_conversation(_conversation_id)") == 2
