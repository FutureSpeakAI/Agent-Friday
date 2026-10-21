"""A spoken yes approves a card only on a voice call from Friday's own page on this PC.

PR #173 made a screen press count only with this launch's desk key, which only a
browser on the PC can fetch. Voice was left out: the voice WebSockets treated any
request that `_is_local_request()` accepted as the owner's own voice, and a tunnel
that strips its forwarding headers looks exactly like that. So voice arriving
through such a tunnel could approve ordinary cards.

Now the page puts the desk key on the voice socket (`dk`), the socket counts as the
owner's own voice only with it (`routes.voice._voice_on_this_desk`), and
`approvals.decide_with_outcome` refuses a voice approval made inside a voice call
that is not. Declining stays open to every channel.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import approvals as ap
from tests.desk_press_support import desk_key

REPO = Path(__file__).resolve().parents[2]


def _ws_ctx(query="", *, addr="127.0.0.1", host="localhost:3000", headers=None):
    return core.app.test_request_context(
        "/ws/voice-local" + (("?" + query) if query else ""), method="GET",
        headers=headers or {}, base_url="http://%s" % host, environ_base={"REMOTE_ADDR": addr})


def _on_desk():
    from agent_friday.routes import voice
    return voice._voice_on_this_desk()


# ── the socket ────────────────────────────────────────────────────────────────

def test_the_owners_page_on_this_pc_with_the_key_is_the_desk():
    with _ws_ctx("t=x&dk=" + desk_key()):
        assert _on_desk() is True


def test_a_header_stripped_tunnel_without_the_key_is_not_the_desk():
    """Loopback, local Host, no forwarding headers: everything _is_local_request() asks for."""
    with _ws_ctx("t=x"):
        assert core._is_local_request() is True
        assert _on_desk() is False


def test_a_wrong_or_stale_key_is_not_the_desk():
    with _ws_ctx("dk=not-this-launch"):
        assert _on_desk() is False


def test_a_forwarded_request_with_the_key_is_not_the_desk():
    with _ws_ctx("dk=" + desk_key(), headers={"X-Forwarded-For": "203.0.113.9"}):
        assert _on_desk() is False


def test_both_voice_sockets_use_the_desk_check():
    src = (REPO / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    assert "_owner_voice = bool(_ws_authenticated) and _voice_on_this_desk()" in src
    assert "_owner_local_live = _voice_on_this_desk()" in src
    assert "and bool(_is_local_request())\n" not in src.replace("\r\n", "\n"), \
        "live Laya's trusted flag must not fall back to the bare locality check"


# ── the decision ──────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_fire_hook", lambda rec: None)
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    return tmp_path


def _card(sid):
    return ap.create_approval(kind="desk_test_card", subject_type="desk_test", subject_id=sid,
                              title="A test card", action_description="send the test message",
                              force_gate=True)


def _in_voice_call(owner_local):
    from agent_friday.services import agent
    return agent._CURRENT_TOOL_CONTEXT.set({"surface": "voice-local", "is_voice": True,
                                            "owner_local": owner_local})


@pytest.mark.parametrize("label", ["owner:/voice/", "owner:voice"])
def test_voice_off_this_desk_cannot_approve(store, label):
    from agent_friday.services import agent
    card = _card("v-off-" + label)
    tok = _in_voice_call(False)
    try:
        rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by=label)
    finally:
        agent._CURRENT_TOOL_CONTEXT.reset(tok)
    assert won is False and rec["status"] == "pending"


def test_voice_off_this_desk_can_still_decline(store):
    from agent_friday.services import agent
    card = _card("v-off-deny")
    tok = _in_voice_call(False)
    try:
        rec, won = ap.decide_with_outcome(card["approval_id"], "deny", decided_by="owner:/voice/")
    finally:
        agent._CURRENT_TOOL_CONTEXT.reset(tok)
    assert won is True and rec["status"] == "denied"


def test_voice_on_this_desk_approves_as_before(store):
    from agent_friday.services import agent
    card = _card("v-on")
    tok = _in_voice_call(True)
    try:
        rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:/voice/")
    finally:
        agent._CURRENT_TOOL_CONTEXT.reset(tok)
    assert won is True and rec["status"] == "approved" and rec["decided_source"] == "voice"


def test_the_voice_tool_says_why(store):
    from agent_friday.services import agent
    card = _card("v-tool")
    tok = _in_voice_call(False)
    stok = agent._CURRENT_SURFACE.set("voice")
    try:
        out = agent._tool_answer_card({"card_id": card["approval_id"], "decision": "approve"})
    finally:
        agent._CURRENT_SURFACE.reset(stok)
        agent._CURRENT_TOOL_CONTEXT.reset(tok)
    assert out.startswith("NOT RECORDED") and "this PC" in out
    assert ap.get_approval(card["approval_id"])["status"] == "pending"


# ── the page ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_voice_page_carries_the_key_on_its_socket(page):
    html = (REPO / page).read_text(encoding="utf-8")
    assert "window.fridayDeskSocketParam = function" in html
    helper = html[html.index("window.fridayDeskSocketParam = function"):][:400]
    assert "refreshDeskKey()" in helper and "'dk='" in helper
    opener = html[:html.index("new WebSocket(proto")]
    assert "fridayDeskSocketParam" in opener[-600:], "the voice socket is opened without the desk key"
