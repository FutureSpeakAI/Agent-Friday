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
    assert accepted == [(("public-chat", "Reviewer", "Public work"), {})]
    assert crew_runtime.HOST_ORIGIN.get() is None
