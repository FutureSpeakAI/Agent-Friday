"""Voice can do what chat can, unless the user restricted it.

Voice used to reach 20 of chat's tools and nothing else, with its own
confirm-first flag on two of them, no conversation of its own (so a task it
started reported to Main), and no way to hear a background result. Now any
request can be handed to the full agent (delegate_to_friday), which runs with
every tool, reports to the call's conversation, and hands its outcome back to
the live call. The user's own restrictions still hold: local-only mode refuses.
"""
import inspect

import pytest

import agent_friday.routes.voice as rv
import agent_friday.services.agent as agent
import agent_friday.services.voice_engine as ve

REQUEST = "Draft an email to the school office asking when term photos are"


@pytest.fixture
def spawned(monkeypatch):
    calls = []

    def fake_spawn(name, prompt, description='', **k):
        calls.append({"name": name, "prompt": prompt, **k})
        return "task_123"
    monkeypatch.setattr(agent, "_spawn_task", fake_spawn)
    return calls


def _run(name, args, session=None):
    frames = []
    out = ve._voice_tool_run(name, args, frames.append, session=session)
    return out, frames


def test_the_delegate_is_a_voice_tool_and_the_surface_says_so():
    assert "delegate_to_friday" in ve._voice_tool_names()
    note = rv._voice_tool_surface_note()
    assert "is NOT callable in voice" not in note
    assert "delegate_to_friday" in note and "EVERYTHING ELSE CHAT CAN DO IS REACHABLE" in note


def test_a_voice_request_reaches_a_chat_only_tool(spawned, monkeypatch):
    """draft_email is not one of voice's direct tools; through the delegate the
    request runs as a task with the full registry, draft_email included."""
    monkeypatch.setattr(ve, "_load_settings", lambda: {"model_routing": {"mode": "local_preferred"}})
    assert "draft_email" not in ve._voice_tool_names()
    out, frames = _run("delegate_to_friday", {"request": REQUEST, "title": "School email"},
                       session={"conversation_id": "conv-voice-1"})
    assert out.startswith("DELEGATED:task_123"), out
    assert len(spawned) == 1
    call = spawned[0]
    assert call["tools"] is None, "the task must get the full registry, not a voice subset"
    assert "draft_email" in {t["name"] for t in agent.CLAUDE_TOOLS}
    assert call["conversation_id"] == "conv-voice-1", "the task must report to the call's conversation"
    assert REQUEST in call["prompt"]
    assert any(f.get("type") == "task_spawned" for f in frames)


def test_local_only_blocks_the_delegate(spawned, monkeypatch):
    monkeypatch.setattr(ve, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})
    out, _ = _run("delegate_to_friday", {"request": REQUEST},
                  session={"conversation_id": "conv-voice-1"})
    assert out.startswith("NOT DONE") and "local-only" in out
    assert spawned == [], "nothing may be started in local-only mode"


def test_voice_calls_carry_the_conversation_and_his_words():
    ctx = ve._voice_ctx({"conversation_id": "conv-9", "owner_text": "send it to the school"})
    assert ctx["conversation_id"] == "conv-9" and ctx["owner_text"] == "send it to the school"
    assert ctx["authenticated"] is True and ctx["surface"] == "voice-live"


def test_no_voice_only_confirm_flag_remains():
    specs = {t[0]: t for t in ve._VOICE_LIVE_TOOLS}
    for name in ("open_url", "navigate_workspace"):
        assert "confirmed" not in specs[name][2], name
    assert "confirmed=true" not in inspect.getsource(ve._voice_tool_run)


def test_a_finished_task_lands_in_its_conversation_and_the_live_call(monkeypatch):
    from agent_friday.services import voice_live_channel as vlc
    appended, heard = [], []
    monkeypatch.setattr(agent, "_task_conversation_id", lambda tid: "conv-voice-1")
    from agent_friday.services import conversations as cv
    monkeypatch.setattr(cv, "resolve", lambda cid: cid)
    monkeypatch.setattr(cv, "append", lambda cid, msg: appended.append((cid, msg)))
    vlc.register("conv-voice-1", lambda text, kind: heard.append((kind, text)))
    try:
        agent._post_task_result_to_conversation("task_123", "School email", "complete",
                                                "Drafted and left in Gmail drafts for your review.")
    finally:
        vlc.unregister("conv-voice-1")
    assert appended and appended[0][0] == "conv-voice-1"
    assert "Drafted and left in Gmail drafts" in appended[0][1]["text"]
    assert appended[0][1]["meta"]["kind"] == "task_result"
    assert heard and heard[0][0] == "task_result" and "Gmail drafts" in heard[0][1]


def test_the_bridge_hands_results_to_the_call_between_turns():
    src = inspect.getsource(rv).replace("\r\n", "\n")
    assert "_voice_live_channel.register(_voice_session[\"conversation_id\"], _deliver_to_call)" in src
    assert "_voice_live_channel.unregister(*_live_chan[0])" in src
    assert "await _flush_injections(sess)" in src
    assert "_gate_voice_tool_result(text, kind)" in src
    assert "_taint.note_user_message(\"voice-live\", user_text)" in src
    assert "_retarget_call(_call_cid())" in src


def test_the_remaining_restrictions_are_written_down_and_shown():
    rs = {r["id"]: r for r in ve.voice_restrictions({"model_routing": {"mode": "local_only"},
                                                    "voice_room_mode": "room"})}
    for rid in ("local_only", "vault_local_only", "voice_tools", "computer_control",
                "approvals", "never_send", "direct_time_limit", "room_approvals"):
        assert rid in rs and rs[rid]["why"], rid
    assert rs["local_only"]["active"] is True and rs["room_approvals"]["active"] is True
    assert ve.voice_restrictions({})[0]["active"] is False       # local-only off by default
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    for html in (root / "index.html", root / "ui_parts" / "app.html"):
        text = html.read_text(encoding="utf-8")
        assert "function VoiceRestrictions(" in text and "/api/voice/restrictions" in text
    assert (root / "docs" / "reference" / "voice-capability.md").exists()
