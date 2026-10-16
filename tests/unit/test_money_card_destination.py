"""The money-paragraph card appears only where email text would leave the PC (synthetic data only).

Under the cautious cloud consent, money paragraphs in an email result are held from a CLOUD model and the
owner is asked on a card (services/cloud_release). When the seat receiving the result is Friday's own
local model nothing leaves the PC: no card, no read-back, the paragraphs reach it untouched. The card is
raised at the point a later call would carry the text to a cloud model, keyed on that destination and
never on the consent setting alone.
"""
from __future__ import annotations

import json
import re

import pytest

REF = "mail:acct_a:t-hall"
MONEY = "The deposit of $400 for the hall is due by 1 May, invoice attached."
PLAIN = "Thanks for the update on the spring plans."


def _thread():
    return json.dumps({"source": "gmail", "thread": REF, "read": True, "message_count": 1,
                       "messages": [{"n": 1, "id": "m0", "from": "Robin Vale <robin@example.test>",
                                     "text": PLAIN + "\n\n" + MONEY}]})


def _convo(result):
    return [{"role": "user", "content": "What does the hall thread say?"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "read_email_thread",
                                               "input": {"ref": REF}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": result}]}]


def _result_of(payload):
    return payload["messages"][2]["content"][0]["content"]


@pytest.fixture
def guarded(monkeypatch, tmp_path):
    import importlib
    import agent_friday.core as core
    from agent_friday.services import approvals, egress_gate as eg
    box = {"model_routing": {"mode": "local_preferred", "mode_chosen": "local_preferred",
                             "cloud_consent": {"answered": True, "choice": "cloud_guarded"}}}
    monkeypatch.setattr(core, "_load_settings", lambda: box)
    monkeypatch.setattr(core, "_load_privacy_watchlist", lambda: [])
    monkeypatch.setattr(core, "_owner_emails", lambda: [])
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    cr = importlib.import_module("agent_friday.services.cloud_release")
    monkeypatch.setattr(cr, "_path", lambda: tmp_path / "cloud_release.json")
    # Money words classify as private, as the shipped financial rules do; the rest is public.
    monkeypatch.setattr(eg, "_classify_cloud",
                        lambda t: eg.Tier.SENSITIVE if re.search(r"deposit|\$", str(t)) else eg.Tier.PUBLIC)
    monkeypatch.setattr(eg, "_local_model_available", lambda: True)
    monkeypatch.setattr(eg, "_run_appeals", lambda *a, **k: 0)
    assert eg.is_local_provider("ollama"), "the sandbox's local seat is not judged local"
    return box


def _cards():
    from agent_friday.services import approvals
    return [a for a in approvals.list_approvals() if (a.get("payload") or {}).get("handler") == "cloud_release"]


def test_a_local_seat_reads_a_thread_with_money_paragraphs_untouched_and_no_card(guarded):
    from agent_friday.services import egress_gate as eg, cloud_release as cr
    thread = _thread()
    # The local seat's own call: the whole conversation goes to Friday's local model as it is.
    sealed = eg.seal_outbound({"model": "llama3.1:8b", "messages": _convo(thread)}, "ollama", model="llama3.1:8b")
    assert _result_of(sealed) == thread
    # The gate itself, asked about text for the local seat: passed whole, nothing held, nobody asked.
    out = eg._gate_text(thread, "ollama", "message[2].tool_result")
    assert out == thread, "money paragraphs bound for the local model were held back: %r" % out
    assert MONEY in out and "privacy_gate" not in out
    gated = json.loads(thread)
    gated["messages"][0]["text"] = PLAIN
    assert cr.offer(json.loads(thread), gated, "ollama") == "", "a card was offered for text that stays on this PC"
    assert _cards() == [], "a money card was raised although nothing would leave the PC"


def test_a_cloud_seat_still_gets_the_card_and_a_cloud_tagged_local_model_is_cloud(guarded):
    from agent_friday.services import egress_gate as eg
    sent = _result_of(eg.seal_outbound({"messages": _convo(_thread())}, "anthropic", pii_lookup={}))
    assert "$400" not in sent and PLAIN in sent
    assert len(_cards()) == 1 and _cards()[0]["payload"]["what"] == [MONEY]
    # An Ollama model tagged :cloud is relayed off the PC under the "ollama" name: it is a cloud seat.
    sent = _result_of(eg.seal_outbound({"messages": _convo(_thread())}, "ollama", pii_lookup={},
                                       model="glm-4.6:cloud"))
    assert "$400" not in sent, "a cloud-tagged model behind the local daemon was handed the money paragraph"
    assert len(_cards()) == 1                     # the same paragraphs: the same card, not a second one


def test_a_local_turn_whose_text_then_goes_to_a_cloud_call_raises_the_card_at_that_point(guarded):
    from agent_friday.services import egress_gate as eg
    convo = _convo(_thread())
    local = eg.seal_outbound({"messages": convo}, "ollama", model="llama3.1:8b")
    assert MONEY in _result_of(local) and _cards() == []
    # The same conversation escalates to a cloud seat: its history now leaves the PC, so the card is
    # raised here, and the paragraph is held while it waits.
    cloud = eg.seal_outbound({"messages": convo}, "anthropic", pii_lookup={})
    assert "$400" not in _result_of(cloud)
    cards = _cards()
    assert len(cards) == 1 and cards[0]["status"] == "pending" and cards[0]["payload"]["what"] == [MONEY]
    assert "approval card" in json.loads(_result_of(cloud))["privacy_gate"]
