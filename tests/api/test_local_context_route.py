"""The payload card's buttons, through the route the card calls.

Send and Don't send decide the card through the one approval path; "Allow for
this conversation" records the conversation grant and sends; Edit saves his
text, or returns both versions when the privacy check would change it.
"""
import pathlib

import pytest

from agent_friday.services import approvals
from agent_friday.services import egress_gate as eg
from agent_friday.services import local_context as lc
from agent_friday.services import sensitivity_classifier as sc
from agent_friday.services import voice_live_channel as vlc

CID = "conv-share-route"
RAW = "On weekends {{person: Dana | their partner}} likes the farmers market."


@pytest.fixture
def card(monkeypatch, tmp_path):
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "_grants_file", lambda: tmp_path / "grants.json")
    monkeypatch.setattr(sc, "_embedding_tier", lambda t: (0, 0.0))
    monkeypatch.setattr(eg, "_rate_limit", lambda: None)
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: False)
    lc.register()
    heard = []
    vlc.register(CID, lambda text, kind: heard.append((kind, text)))
    out = lc.request("What do they like on weekends?", conversation_id=CID,
                     cloud_model="gemini-3.8-live", answer_fn=lambda q: (RAW, "local-seat"))
    yield out["approval_id"], heard
    vlc.unregister(CID)


def test_send_decides_once_and_delivers_the_shown_text(client, card):
    aid, heard = card
    shown = approvals.get_approval(aid)["payload"]["text"]
    r = client.post(f"/api/local-context/{aid}/act", json={"action": "send"})
    assert r.status_code == 200 and r.get_json()["won"] is True
    assert heard == [("context", shown)]
    again = client.post(f"/api/local-context/{aid}/act", json={"action": "send"}).get_json()
    assert again["won"] is False and len(heard) == 1


def test_allow_for_this_conversation_sends_and_grants(client, card):
    aid, heard = card
    r = client.post(f"/api/local-context/{aid}/act", json={"action": "allow_conversation"})
    assert r.get_json()["ok"] and heard and heard[0][0] == "context"
    nxt = lc.request("And on Sundays?", conversation_id=CID, cloud_model="gemini-3.8-live",
                     answer_fn=lambda q: (RAW, "local-seat"))
    assert nxt["status"] == "sent", "the grant should cover the next request in this conversation"


def test_edit_asks_once_more_when_the_check_would_change_it(client, card):
    aid, _heard = card
    d = client.post(f"/api/local-context/{aid}/act",
                    json={"action": "edit", "text": "Ring 512-555-0199 first."}).get_json()
    assert d.get("needs_confirm") and "[phone number 1]" in d["checked"]
    d = client.post(f"/api/local-context/{aid}/act",
                    json={"action": "edit", "text": "Ring 512-555-0199 first.",
                          "accept": "checked"}).get_json()
    assert d["ok"] and d["approval"]["payload"]["text"] == "Ring [phone number 1] first."


def test_both_copies_of_the_ui_render_the_card():
    root = pathlib.Path(__file__).resolve().parents[2]
    for html in (root / "index.html", root / "ui_parts" / "app.html"):
        t = html.read_text(encoding="utf-8")
        assert "function LocalContextShareCard(" in t
        assert "a.kind === 'local_context_share'" in t
        for label in ("Send", "Edit", "Don't send", "Allow for this conversation",
                      "Keep my words", "Use the checked version"):
            assert label in t, label
        assert "/api/local-context/" in t
