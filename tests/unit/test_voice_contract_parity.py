"""Audit B3: voice can open a Media card and ask what cards there are.

navigate_to knew the `card` kind for chat but the voice declaration did not list it, and media_cards (the
list that answers "what did I make this week") was not on voice at all. Both are on the contract now, and
both hand a cloud voice model counts and kinds, never a card's title (docs/reference/voice-tool-contract.md
section 5). The contract's token ceiling was raised for the See & Touch tools and is checked here.
"""
from __future__ import annotations

import json

from agent_friday.services import agent
from agent_friday.services import desktop_bus, desktop_targets as dt
from agent_friday.services import media_card_tools as mct
from agent_friday.services import voice_engine as ve


def _spec(name):
    return next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == name)


def _say(*_a, **_k):
    return None


class _Cards:
    """media_index.query / get for the tools under test."""
    cards = [{"id": "c1", "title": "Harbor Legal settlement draft", "kind": "draft", "status": "draft", "held": False,
              "when": "2026-10-01", "published_at": None, "sources": []},
             {"id": "c2", "title": "Mum's birthday video", "kind": "video", "status": "published", "held": False,
              "when": "2026-10-02", "published_at": "x", "sources": []}]

    @classmethod
    def query(cls, **kw):
        return {"total": len(cls.cards), "cards": list(cls.cards)}

    @classmethod
    def get(cls, cid):
        return next((c for c in cls.cards if c["id"] == cid), None)


def test_the_voice_navigate_to_lists_the_card_kind():
    kinds = _spec("navigate_to")[2]["kind"][1]
    assert "card" in kinds and "content_post" in kinds


def test_media_cards_and_organize_media_are_on_the_voice_contract():
    names = ve._voice_tool_names()
    assert "media_cards" in names and "organize_media" in names
    assert "media_cards" in ve._VOICE_SHARED_TOOLS and "organize_media" in ve._VOICE_SHARED_TOOLS
    contract = ve.build_voice_tool_contract()
    assert {"media_cards", "organize_media", "screen_select"} <= set(contract["names"])


def test_the_contract_fits_the_raised_ceiling_with_room_to_spare():
    c = ve.build_voice_tool_contract()
    assert ve.VOICE_CONTRACT_MAX_TOKENS == 10000
    assert c["fits"] and c["tokens"] <= ve.VOICE_CONTRACT_MAX_TOKENS - 300, c["tokens"]


def test_a_spoken_card_open_is_confirmed_and_names_no_title(monkeypatch):
    monkeypatch.setattr(mct.mi, "query", _Cards.query)
    monkeypatch.setattr(mct.mi, "get", _Cards.get)
    from agent_friday.services import media_index
    monkeypatch.setattr(media_index, "query", _Cards.query)
    monkeypatch.setattr(media_index, "get", _Cards.get)
    sent = []
    monkeypatch.setattr(desktop_bus, "send", lambda actions, verify=None, timeout=6.0: sent.append((actions, verify)) or {
        "delivered": True, "acked": True, "ack": {"opened": True, "matched": True, "visible": True, "label": "Media"}})
    out = ve._voice_tool_run("navigate_to", {"kind": "card", "query": "harbor legal settlement"}, _say,
                             {"conversation_id": "c-v"})
    assert out.startswith("NAV_OK") and "Harbor" not in out and "Legal" not in out, out
    assert sent[0][0][0]["workspace"] == "media" and sent[0][0][0]["card"] == "c1"


def test_media_cards_hands_a_cloud_voice_counts_and_chat_the_titles(monkeypatch):
    monkeypatch.setattr(mct.mi, "query", _Cards.query)
    voice = ve._voice_tool_run("media_cards", {"view": "all"}, _say, {"conversation_id": "c-v"})
    assert "Harbor" not in voice and "Mum" not in voice, voice
    data = json.loads(voice)
    assert data["count"] == 2 and data["statuses"] == {"draft": 1, "published": 1} and "titles are on their screen" in data["say"]
    chat = agent._execute_tool("media_cards", {"view": "all"},
                               session_ctx=agent.prepare_confirmation_ctx("s1", "what did I make", {"authenticated": True}))
    assert "Harbor Legal settlement draft" in chat, "chat keeps the names"


def test_media_show_is_quiet_for_the_cloud_too(monkeypatch):
    monkeypatch.setattr(mct.mi, "query", _Cards.query)
    monkeypatch.setattr(desktop_bus, "send", lambda *a, **k: {"delivered": True, "acked": True, "ack": {}})
    voice = ve._voice_tool_run("media_show", {"status": "draft"}, _say, {"conversation_id": "c-v"})
    assert "Harbor" not in voice and json.loads(voice)["count"] == 2
