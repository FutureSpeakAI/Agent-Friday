"""Every private detail the gate holds back gets a card (owner ruling, 2026-10-17: no silent holds).

Under any consent but "Cloud for everything", a span the egress gate keeps back from a cloud model is
put on ONE approval card per call: exactly the text that would go (as the cloud would receive it), and
where it would go (provider, model, which part of the request). Send lets exactly those words go on the
conversation's next cloud calls; Keep on this PC is not asked again; "Always send this exact text"
remembers it. The never-send floor is never on a card: it stays refused, and the refusal says why.
A Send counts only from a click on the card on screen or the owner's own words after a read-back.

Synthetic text only; the classifier is pinned so the test is about the card, not the classifier.
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
PUBLIC = "What is the weather like in the city today?"
PRIVATE = "The PRIVATE-MARK result from the clinic came back on Tuesday."
REPO = Path(__file__).resolve().parents[2]


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
    monkeypatch.setattr(jg, "never_send_tokens", lambda: ["Riverside Clinic"])
    yield tmp_path


def _seal(text, provider="anthropic", model="claude-sonnet-5"):
    return eg.seal_outbound({"messages": [{"role": "user", "content": text}]}, provider,
                            model=model)


def _cards():
    return [c for c in ap.list_approvals() if (c.get("payload") or {}).get("general")]


def test_a_held_paragraph_goes_on_a_card_with_exactly_that_text_and_where(gate):
    out = _seal(PUBLIC + "\n\n" + PRIVATE)
    content = out["messages"][0]["content"]
    assert PUBLIC in content and MARK not in content, "the private paragraph stayed on this PC"
    cards = _cards()
    assert len(cards) == 1
    p = cards[0]["payload"]
    assert p["what"] == [PRIVATE]
    assert p["where"]["provider"] == "anthropic" and p["where"]["model"] == "claude-sonnet-5"
    assert p["where"]["parts"] == ["this conversation"]
    assert cards[0]["status"] == "pending" and cards[0]["kind"] == cr.APPROVAL_KIND
    assert "anthropic" in cards[0]["title"]


def test_a_whole_held_message_is_on_the_card_too(gate):
    _seal(PRIVATE)
    assert [c["payload"]["what"] for c in _cards()] == [[PRIVATE]]


def test_the_same_detail_is_not_asked_twice_while_its_card_waits(gate):
    _seal(PUBLIC + "\n\n" + PRIVATE)
    _seal(PUBLIC + "\n\n" + PRIVATE)
    assert len(_cards()) == 1


def test_send_lets_exactly_those_words_go_next_time(gate):
    _seal(PUBLIC + "\n\n" + PRIVATE)
    card = _cards()[0]
    out = cr.decide(card["approval_id"], "approve", decided_by="owner:ui")
    assert out["ok"]
    sent = _seal(PUBLIC + "\n\n" + PRIVATE)["messages"][0]["content"]
    assert PRIVATE in sent
    assert len(_cards()) == 1, "nothing new to ask"


def test_keep_on_this_pc_is_not_asked_again_and_stays_held(gate):
    _seal(PRIVATE)
    card = _cards()[0]
    assert cr.decide(card["approval_id"], "deny", decided_by="owner")["ok"]
    out = _seal(PRIVATE)["messages"][0]["content"]
    assert MARK not in out
    assert len(_cards()) == 1


def test_always_send_this_exact_text_is_remembered(gate):
    _seal(PRIVATE)
    card = _cards()[0]
    cr.decide(card["approval_id"], "approve", {"text": True}, decided_by="owner:ui")
    # the once-release runs out; the remembered text still goes
    data = json.loads((gate / "cloud_release.json").read_text(encoding="utf-8"))
    data["once"] = {}
    (gate / "cloud_release.json").write_text(json.dumps(data), encoding="utf-8")
    assert PRIVATE in _seal(PRIVATE)["messages"][0]["content"]
    assert [r["scope"] for r in cr.remembered()] == ["text"]


def test_a_send_that_is_not_the_owners_click_or_words_is_refused(gate):
    _seal(PRIVATE)
    card = _cards()[0]
    out = cr.decide(card["approval_id"], "approve", decided_by="owner")
    assert not out["ok"] and ap.get_approval(card["approval_id"])["status"] == "pending"
    # and an approval reached some other way releases nothing, even with a label a body can claim
    ap.decide(card["approval_id"], "approve", decided_by="owner:chat")
    assert ap.get_approval(card["approval_id"])["status"] == "pending", "another route approved it"
    assert "cannot be approved from here" in ap.refusal_for(card["approval_id"], "approve", "owner:chat")
    assert MARK not in _seal(PRIVATE)["messages"][0]["content"]


def test_a_send_is_for_the_provider_the_card_named(gate):
    _seal(PRIVATE)
    card = _cards()[0]
    cr.decide(card["approval_id"], "approve", {"text": True}, decided_by="owner:ui")
    assert PRIVATE in _seal(PRIVATE)["messages"][0]["content"]
    other = _seal(PRIVATE, provider="openrouter", model="some/model")["messages"][0]["content"]
    assert MARK not in other, "a Send to one provider let the text go to another"
    assert sorted(c["payload"]["where"]["provider"] for c in _cards()) == ["anthropic", "openrouter"]


def test_a_json_tool_result_puts_the_field_on_the_card_not_the_whole_result(gate):
    result = json.dumps({"title": "Clinic notes", "body": PRIVATE, "other": "nothing private here"})
    eg._gate_text(result, "anthropic", "message[1].content[0]")
    cards = _cards()
    assert len(cards) == 1 and cards[0]["payload"]["what"] == [PRIVATE]


def test_a_pretty_printed_json_result_carries_only_the_field_too(gate):
    result = json.dumps({"title": "Clinic notes", "body": PRIVATE, "other": "nothing private here"}, indent=2)
    eg._gate_text(result, "anthropic", "message[1].content[0]")
    assert [c["payload"]["what"] for c in _cards()] == [[PRIVATE]]


def test_money_in_ordinary_chat_is_carded_even_when_email_money_is_kept_quietly(gate, monkeypatch):
    monkeypatch.setattr(cr, "_choice", lambda: "withhold")
    _seal("The PRIVATE-MARK bill from the plumber cost $40.")
    assert len(_cards()) == 1


def test_the_card_shows_every_character_send_would_release(gate):
    long_text = "PRIVATE-MARK " + "x" * 6000
    _seal(long_text)
    assert _cards()[0]["payload"]["what"] == [long_text]


def test_the_card_route_sends_only_for_a_click_on_the_page(gate, monkeypatch):
    from flask import Flask
    from agent_friday.routes import control
    from agent_friday.services import screen_click
    _seal(PRIVATE)
    card = _cards()[0]
    monkeypatch.setattr(screen_click, "screen_session", lambda req: False)
    view = getattr(control.decide_cloud_release, "__wrapped__", control.decide_cloud_release)
    with Flask(__name__).test_request_context("/x", method="POST", json={"decision": "approve"}):
        resp = view(card["approval_id"])
    resp, status = (resp if isinstance(resp, tuple) else (resp, 200))
    assert status == 400 and ap.get_approval(card["approval_id"])["status"] == "pending"


def test_the_never_send_floor_is_refused_with_its_reason_and_never_carded(gate):
    with pytest.raises(eg.NeverSendBlocked) as e:
        _seal("Booked at Riverside Clinic. " + PRIVATE)
    assert "never-send list" in str(e.value)
    assert _cards() == []


def test_cloud_for_everything_holds_nothing_so_asks_nothing(gate, monkeypatch):
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    assert MARK in _seal(PRIVATE)["messages"][0]["content"]
    assert _cards() == []


def test_a_local_destination_raises_no_card(gate):
    _seal(PRIVATE, provider="ollama", model="gemma4:e2b")
    assert _cards() == []


def test_the_gates_own_probes_raise_no_card(gate):
    _seal(PRIVATE, provider="selftest-cloud")
    _seal(PRIVATE, provider="probe-cloud")
    assert _cards() == []


def test_a_direct_gate_call_raises_its_card_too(gate):
    """Voice, web search, TTS and other legs call _gate_text directly, not seal_outbound."""
    eg._gate_text(PRIVATE, "google-gemini", "live.text")
    cards = _cards()
    assert len(cards) == 1 and cards[0]["payload"]["where"]["provider"] == "google-gemini"


def test_the_card_shows_where_and_offers_always_send_in_both_ui_sources():
    for name in ("index.html", "ui_parts/app.html"):
        src = (REPO / name).read_text(encoding="utf-8")
        start = src.index("function CloudReleaseCard(")
        body = src[start:src.index("\n}\n", start)]
        assert "Where it would go: " in body and "Always send this exact text" in body, name
        assert "text: always && !!scope.text" in body, name
