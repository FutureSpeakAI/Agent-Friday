"""Reasoning traces over HTTP: the chat turn names its trace, the stream says
so before the first token, and the archive is served only on this machine."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import reasoning_trace as rt

SEP = chr(10) + chr(10)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    import agent_friday.services.file_grants as fg
    fg._SIGNING_KEY_CACHE.clear()
    rt._reset_for_tests()
    monkeypatch.setattr(rt, "BASE_DIR_OVERRIDE", tmp_path / "traces")
    monkeypatch.setattr(rt, "settings", lambda: {"capture": True, "retention_days": 0})
    yield
    rt._reset_for_tests()


def _fake_turn(reasoning="I should greet them."):
    from flask import jsonify

    def turn():
        rt.reasoning(reasoning)
        cid = rt.tool_started("search_web", {"query": "hello"})
        rt.tool_finished("search_web", {"query": "hello"}, "3 results")
        return jsonify({"response": "Hello", "model": "bonsai2:27b"})
    return turn


def _frames(body):
    out = []
    for frame in body.split(SEP):
        frame = frame.strip()
        if frame.startswith("data:"):
            out.append(json.loads(frame[5:].strip()))
    return out


def test_stream_announces_the_trace_before_any_token_and_the_payload_names_it(client, monkeypatch):
    from agent_friday.routes import chat as chat_routes
    monkeypatch.setattr(chat_routes, "chat", chat_routes._traced_turn(_fake_turn()))
    res = client.post("/api/chat/stream", json={"message": "hi there"})
    frames = _frames(res.get_data(as_text=True))
    assert "trace_id" in frames[0] and len(frames[0]) == 1
    tid = frames[0]["trace_id"]
    payload = [f for f in frames if f.get("done")][0]["payload"]
    assert payload["trace_id"] == tid
    assert payload["reasoning_sources"] == ["full reasoning (local)"]
    tree = client.get("/api/traces/" + tid).get_json()["tree"]
    assert tree["kind"] == "chat" and tree["label"] == "hi there"
    assert [e["type"] for e in tree["events"]] == ["reasoning", "tool_call", "tool_result"]
    assert tree["reply_excerpt"] == "Hello"


def test_blocking_chat_response_carries_the_trace_and_archives_it(app):
    from agent_friday.routes import chat as chat_routes
    turn = chat_routes._traced_turn(_fake_turn("private reasoning"))
    with app.test_request_context("/api/chat", method="POST", json={"message": "hey"}):
        payload = turn().get_json()
    assert payload["response"] == "Hello" and payload["trace_id"].startswith("tr_")
    assert rt.verify()["valid"] and rt.verify()["records"] == 1


def test_a_failing_turn_is_archived_as_failed(app):
    from agent_friday.routes import chat as chat_routes

    def boom():
        rt.reasoning("started thinking")
        raise RuntimeError("provider exploded")
    turn = chat_routes._traced_turn(boom)
    with app.test_request_context("/api/chat", method="POST", json={"message": "hey"}):
        with pytest.raises(RuntimeError):
            turn()
    rows = rt.search()["traces"]
    assert rows[0]["status"] == "failed"


def test_live_feed_snapshot_search_verify_export_and_retention(client):
    tid = rt.start("chat", "flight plans", model="bonsai2:27b", parent_id=None)
    with rt.activate(tid):
        rt.reasoning("compare fares")
        child = rt.start("subagent", "fare research")
    snap = client.get("/api/traces/live?snapshot=1").get_json()
    assert {t["trace_id"] for t in snap["traces"]} == {tid, child}
    assert snap["labels"]["not_exposed"] == "reasoning not exposed by provider"
    cursor = snap["cursor"]
    rt.reasoning(" again", trace_id=tid)
    fed = client.get("/api/traces/live?since=%d" % cursor).get_json()
    assert [e["text"] for e in fed["events"] if e["type"] == "reasoning_delta"] == [" again"]
    rt.note("done", trace_id=child)
    rt.finish(child)
    rt.finish(tid)

    found = client.get("/api/traces?q=fares").get_json()
    assert found["total"] == 1 and found["traces"][0]["trace_id"] == tid
    assert client.get("/api/traces?subagents=1").get_json()["traces"][0]["trace_id"] == child
    tree = client.get("/api/traces/" + child).get_json()
    assert tree["tree"]["trace_id"] == tid and tree["tree"]["children"][0]["trace_id"] == child

    assert client.get("/api/traces/verify").get_json()["valid"] is True
    exp = client.get("/api/traces/export")
    assert exp.headers["Content-Type"].startswith("application/x-ndjson")
    lines = [json.loads(x) for x in exp.get_data(as_text=True).splitlines()]
    assert lines[0]["type"] == "verification" and len(lines) == 3
    assert client.get("/api/traces/nope").status_code == 404

    r = client.post("/api/traces/retention", json={"retention_days": -1})
    assert r.status_code == 400


def test_traces_are_refused_to_a_remote_caller(app):
    from agent_friday.routes import traces as tr_routes
    with app.test_request_context("/api/traces", environ_base={"REMOTE_ADDR": "203.0.113.9"}):
        from flask import g
        g.friday_principal = "user"
        resp = tr_routes._local_user_only()
    body, code = resp
    assert code == 403 and "only served on this machine" in body.get_json()["error"]


def test_traces_are_refused_to_an_observer(app):
    from agent_friday.routes import traces as tr_routes
    with app.test_request_context("/api/traces"):
        from flask import g
        g.friday_principal = "observer"
        body, code = tr_routes._local_user_only()
    assert code == 403
