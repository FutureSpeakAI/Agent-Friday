"""Host delegation keeps the privacy origin from HTTP admission through its worker."""
import threading

import pytest


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/send"])
@pytest.mark.parametrize("raises", [False, True])
def test_typed_host_keeps_private_origin_after_mode_ends(app, monkeypatch, path, raises):
    from flask import jsonify
    from agent_friday.routes import chat
    from agent_friday.services import crew_runtime, off_record, agent
    state = {"private": True, "generation": 6}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    monkeypatch.setattr(chat, "_note_owner_turn", lambda: None)
    seen = []
    def host_turn():
        seen.append(crew_runtime.HOST_ORIGIN.get())
        state.update(private=False, generation=7)
        if raises:
            raise RuntimeError("Synthetic host failure")
        result = agent._execute_tool("ask_crew", {"agent": "Reviewer", "request": "Private marker"})
        return jsonify({"response": result})
    with app.test_request_context(path, method="POST", json={"message": "Synthetic request"}):
        if raises:
            with pytest.raises(RuntimeError, match="Synthetic host failure"):
                chat._traced_turn(host_turn)()
        else:
            result = chat._traced_turn(host_turn)().get_json()
            assert "CREW DENY" in result["response"]
    assert len(seen) == 1 and seen[0].off_record is True and seen[0].generation == 6
    assert crew_runtime.HOST_ORIGIN.get() is None


@pytest.mark.parametrize("private_at_start", [False, True])
@pytest.mark.parametrize("raises", [False, True])
def test_streaming_transfers_original_privacy_before_worker_starts(client, monkeypatch, private_at_start, raises):
    from flask import jsonify
    from agent_friday.routes import chat
    from agent_friday.services import crew_runtime, off_record, agent
    state = {"private": private_at_start, "generation": 6}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    parent = threading.get_ident()
    seen, restored, workers = [], [], []
    original_start = threading.Thread.start
    def start(thread):
        # The privacy lifetime ends after HTTP admission but before the new
        # thread runs. This is deterministic and requires no sleeps.
        state.update(private=False, generation=7)
        original_run = thread.run
        def run():
            try:
                return original_run()
            finally:
                restored.append(crew_runtime.HOST_ORIGIN.get())
        thread.run = run
        workers.append(thread)
        return original_start(thread)
    monkeypatch.setattr(threading.Thread, "start", start)
    def host_turn():
        seen.append((threading.get_ident(), crew_runtime.HOST_ORIGIN.get()))
        if raises:
            raise RuntimeError("Synthetic host failure")
        result = agent._execute_tool("ask_crew", {"agent": "Reviewer", "request": "Private marker"})
        return jsonify({"response": result})
    monkeypatch.setattr(chat, "chat", host_turn)
    result = client.post("/api/chat/stream", json={"message": "Synthetic request"})
    body = result.get_data(as_text=True)
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert ("The chat turn failed" if raises else "CREW DENY") in body
    assert len(seen) == 1 and seen[0][0] != parent
    assert seen[0][1].generation == 6 and seen[0][1].off_record is private_at_start
    assert restored == [None]
    assert crew_runtime.HOST_ORIGIN.get() is None


def test_public_typed_host_delegation_uses_current_trusted_origin(app, monkeypatch):
    from flask import jsonify
    from agent_friday.routes import chat
    from agent_friday.services import crew_runtime, off_record, agent
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: False)
    monkeypatch.setattr(off_record, "generation", lambda: 6)
    monkeypatch.setattr(chat, "_note_owner_turn", lambda: None)
    accepted = []
    monkeypatch.setattr(crew_runtime, "ask", lambda *a, **kw:
                        accepted.append((a, kw)) or {"task_id": "public-task"})
    def host_turn():
        token = agent._CURRENT_CONVERSATION.set("public-chat")
        try:
            result = agent._tool_ask_crew({"agent": "Reviewer", "request": "Public work"})
        finally:
            agent._CURRENT_CONVERSATION.reset(token)
        return jsonify({"response": result["task_id"]})
    with app.test_request_context("/api/chat", method="POST", json={"message": "Synthetic request"}):
        assert chat._traced_turn(host_turn)().get_json()["response"] == "public-task"
    assert accepted == [(("public-chat", "Reviewer", "Public work"), {"project_id": crew_runtime.DEFAULT_PROJECT})]
    assert crew_runtime.HOST_ORIGIN.get() is None


@pytest.mark.parametrize("front", [False, True])
@pytest.mark.parametrize("origin_kind", ["public", "private-ended", "public-changed"])
def test_local_socket_carries_admission_origin_to_both_minds(app, monkeypatch, tmp_path, front, origin_kind):
    import inspect
    from contextlib import nullcontext
    from types import SimpleNamespace
    from agent_friday.routes import voice
    from agent_friday.services import (agent, conversations, crew_runtime, local_seats, notification_policy,
                                      off_record, presence, reflex_turn, voice_ear_stream,
                                      voice_engine, voice_receipt, voice_session, voice_workers)

    # The actual socket admission requires this exact existing owner.
    monkeypatch.setattr(conversations, "FRIDAY_DIR", tmp_path)
    conversations.create(title="Synthetic voice owner", cid="fixture-chat")
    owner_path = tmp_path / "conversations" / "fixture-chat" / "conversation.json"
    owner_before = owner_path.read_bytes()
    state = {"private": origin_kind == "private-ended", "generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    capture = crew_runtime.capture_host_origin
    captured, contexts, results = [], [], []

    def capture_once():
        origin = capture()
        captured.append(origin)
        return origin

    def settings():
        # Engine setup can outlive the original privacy lifetime. It must not
        # be where the socket acquires a fresh public authority.
        if origin_kind != "public":
            state.update(private=False, generation=9)
        return {"voice_engine": "local"}

    denial = agent._crew_delegation_denial

    def admit(name, session_ctx=None):
        contexts.append(session_ctx)
        return denial(name, session_ctx)

    def generate(*a, session_ctx, **kw):
        return admit("ask_crew", session_ctx) or "accepted", []

    def bounded(name, args, send, session):
        return admit(name, voice_engine._voice_ctx(session)) or "accepted"

    class Front:
        def run_turn(self, *a, run_tool, admit_tool=None, **kw):
            refused = admit_tool("ask_crew") if admit_tool is not None else None
            return refused or run_tool("ask_crew", {"agent": "Reviewer", "request": "Private marker"})

    class Session:
        def __init__(self, send, *, generate, **kw):
            self.done = threading.Event()
            self.generate = generate

        def start(self):
            results.append(self.generate("Synthetic request", lambda delta: None, threading.Event()))
            self.done.set()

        def close(self):
            pass

    manifest = SimpleNamespace(refresh_selection=lambda settings: None,
                               snapshot=lambda: {"stages": {}, "contract": {}},
                               subscribe=lambda callback: None, unsubscribe=lambda callback: None)
    engine = SimpleNamespace(name="piper", describe=lambda: {"device": "cpu", "engine": "fixture"})
    monkeypatch.setattr(crew_runtime, "capture_host_origin", capture_once)
    monkeypatch.setattr(agent, "_crew_delegation_denial", admit)
    monkeypatch.setattr(voice, "FRIDAY_WS_TOKEN", "")
    monkeypatch.setattr(voice, "_api_token_valid", lambda token: True)
    monkeypatch.setattr(voice, "_ws_auth_ok", lambda ok: True)
    monkeypatch.setattr(voice, "_load_settings", settings)
    monkeypatch.setattr(voice._vm, "get_manifest", lambda: manifest)
    monkeypatch.setattr(voice._vm, "read_selection", lambda settings: {"ear": {}, "mouth": {}})
    monkeypatch.setattr(voice_workers, "held", lambda role: engine)
    monkeypatch.setattr(voice_workers, "NOTICES", [])
    monkeypatch.setattr(voice_workers, "gpu_queue", lambda: None)
    monkeypatch.setattr(voice_receipt, "log_route", lambda *a, **kw: None)
    monkeypatch.setattr(local_seats, "resolve", lambda role: "fixture-local")
    monkeypatch.setattr(voice, "_arm_voice_front", lambda *a, **kw:
                        {"seat": Front(), "prompt": "Fixture", "model": "fixture", "label": "fixture", "contract": {}}
                        if front else None)
    monkeypatch.setattr(voice, "_release_voice_front", lambda front: None)
    monkeypatch.setattr(voice, "_build_voice_system_prompt", lambda *a, **kw: ("Fixture", {"provider": "local"}))
    monkeypatch.setattr(voice, "_served_by", lambda *a, **kw: {})
    monkeypatch.setattr(voice, "_voice_user_message", lambda text, *a, **kw: text)
    monkeypatch.setattr(voice, "_generate_agent", generate)
    monkeypatch.setattr(voice, "_run_voice_tool_bounded", bounded)
    monkeypatch.setattr(voice, "_voice_orb_start", lambda name: None)
    monkeypatch.setattr(voice, "_voice_orb_finish", lambda *a: None)
    monkeypatch.setattr(notification_policy, "note_owner_turn", lambda: None)
    monkeypatch.setattr(reflex_turn, "shadow", lambda text: None)
    monkeypatch.setattr(presence, "acting_as", lambda person: nullcontext())
    monkeypatch.setattr(voice_ear_stream, "installed", lambda: False)
    monkeypatch.setattr(voice, "VADEndpointer", lambda **kw: None)
    monkeypatch.setattr(voice, "_local_talk_over_detector", lambda settings: None)
    monkeypatch.setattr(voice_session, "VoiceSession", Session)
    monkeypatch.setattr(voice._voice_live_channel, "register", lambda *a: None)
    monkeypatch.setattr(voice._voice_live_channel, "unregister", lambda *a: None)
    monkeypatch.setattr(voice, "_run_after_call", lambda *a: None)
    socket = SimpleNamespace(send=lambda text: None, close=lambda: None)
    with app.test_request_context("/ws/voice?conversation_id=fixture-chat"):
        inspect.unwrap(voice.ws_voice_local)(socket)
    assert len(captured) == 1 and captured[0].generation == 8
    assert captured[0].off_record is (origin_kind == "private-ended")
    assert contexts and all(ctx.get("_crew_host_origin") is captured[0] for ctx in contexts)
    assert all(ctx.get("conversation_id") == "fixture-chat" for ctx in contexts)
    assert len(results) == 1
    assert (results[0] == "accepted") if origin_kind == "public" else "CREW DENY" in results[0]
    assert owner_path.read_bytes() == owner_before
