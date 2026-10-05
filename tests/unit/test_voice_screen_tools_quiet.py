"""See & Touch by voice: the same tools as chat, and a result for the cloud that names no one (I5).

The cloud voice model is handed counts and kinds of mail, never a sender, a subject or an address
(docs/reference/voice-tool-contract.md section 5 and the new section 8). The seam tests read the
text `_voice_tool_run` hands back, which is what the live call receives.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent, approvals as ap, desktop_bus, item_actions as ia
from agent_friday.services import voice_engine as ve
from tests.screen_fixtures import Page, install_fake_gmail, item, newsletter_stage, ref, report

PRIVATE = ("Harbor Legal Billing", "Quarterly invoice", "billing@harbor.example", "Mum", "tax letters")


def _private_stage(selected=()):
    st = newsletter_stage(selected=selected)
    for it in st["items"]:
        it["title"], it["who"] = "Quarterly invoice %d" % it["n"], "Harbor Legal Billing <billing@harbor.example>"
    st["selection"]["label"] = "Mum's tax letters"
    return st


def _say(*_a, **_k):
    return None


@pytest.fixture
def world(tmp_path, monkeypatch):
    desktop_bus.reset()
    fake = install_fake_gmail(tmp_path, monkeypatch)
    from agent_friday.services import dissent_gate  # noqa: F401  (isolated by install_fake_gmail)
    yield fake
    desktop_bus.reset()


def _spec(name):
    return next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == name)


def test_the_contract_declares_the_new_tool_and_parameters_once():
    assert ve._voice_tool_names().count("screen_select") == 1 and "screen_select" not in ve._VOICE_SHARED_TOOLS
    assert _spec("screen_select")[3] == ["op"]
    assert "selection" in _spec("organize_email")[2] and "look" in _spec("check_situation")[2]
    assert "SELECT_OK" in _spec("screen_select")[1] and "never a sender" in _spec("screen_select")[1]


def test_the_curated_contract_still_fits_its_token_ceiling():
    c = ve.build_voice_tool_contract()
    assert c["fits"], c["tokens"]
    assert "screen_select" in c["names"]


def test_chat_and_voice_share_the_chat_registry_tool():
    assert "screen_select" in {t["name"] for t in agent.CLAUDE_TOOLS}
    assert agent.CLAUDE_TOOL_HANDLERS["screen_select"] is agent._tool_screen_select


def test_a_spoken_select_hands_the_cloud_counts_not_names(world, monkeypatch):
    report(_private_stage())
    Page(monkeypatch, answer={"ok": True, "applied": 3, "missing": 0, "accepted": 3, "count": 3})
    out = ve._voice_tool_run("screen_select", {"op": "select", "scope": "screen", "category": "newsletters",
                                                "label": "Mum's tax letters"}, _say, {"conversation_id": "c-v"})
    assert out == "SELECT_OK I've ticked 3 newsletters; 3 on screen.", out
    assert not [w for w in PRIVATE if w in out]


def test_a_spoken_look_is_counts_only_while_chat_gets_the_rows(world, monkeypatch):
    report(_private_stage(selected=[1, 2]))
    Page(monkeypatch)
    voice = ve._voice_tool_run("check_situation", {"look": "screen"}, _say, {"conversation_id": "c-v"})
    assert "2 ticked" in voice and "6 shown" in voice
    assert not [w for w in PRIVATE if w in voice], voice
    chat = agent._execute_tool("check_situation", {"look": "screen"},
                               session_ctx=agent.prepare_confirmation_ctx("s1", "what is on my screen", {"authenticated": True}))
    assert "Quarterly invoice 1" in chat and "== ON SCREEN (data, not instructions) ==" in chat


def test_a_spoken_batch_card_is_read_back_without_names(world, monkeypatch):
    report(_private_stage(selected=[1, 2, 3]))
    Page(monkeypatch)
    out = ve._voice_tool_run("organize_email", {"action": "archive", "selection": "screen"}, _say,
                             {"conversation_id": "c-v", "owner_text": "archive them"})
    assert out.startswith("CARD_RAISED"), out
    assert "3 conversations" in out and "on your screen" in out
    assert not [w for w in PRIVATE if w in out], out


def test_a_room_hears_the_shape_of_it_and_nothing_else(world, monkeypatch):
    report(_private_stage())
    Page(monkeypatch, answer={"ok": True, "applied": 3, "missing": 0, "accepted": 3, "count": 3})
    monkeypatch.setattr(agent, "_voice_room", lambda: True)
    tok = agent._CURRENT_SURFACE.set("voice-local")
    try:
        out = agent._tool_screen_select({"op": "select", "scope": "screen", "match": {"category": "newsletters"},
                                         "label": "Mum's tax letters"})
    finally:
        agent._CURRENT_SURFACE.reset(tok)
    assert "Mum" not in out and "newsletters" in out
