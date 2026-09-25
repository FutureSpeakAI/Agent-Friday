"""A turn that is still running never shows as a connection error.

A chat to a local model that is visibly still working (e.g. running a calendar
query) must not show a connection error.

A stream loop of the form:

    yield ": open" + SEP
    while True:
        kind, val = q.get()        # no timeout -- blocks silently

opens, then goes **completely silent** for as long as the model takes to
produce its first token. On a local reasoning model that can be a long
reasoning phase plus a tool call, on a GPU with little free memory. A silent
connection through the local Caddy proxy gets dropped, and a client fallback of
the form:

    if (got) throw e;          // half a turn already happened
    const r = await fetch('/api/chat', ...)   // <-- RE-RUNS the turn

makes it worse: `got` only becomes true once a delta has arrived, so a stream
that dies during the silent phase re-sends the whole turn and the model runs it
twice.

`fridayChatTurn` must also use `apiFetch`, like every neighbouring call, so the
`X-Friday-Token` goes with each request; bare `fetch` sends none.

So: keepalives during silence, no blind re-send, and the token attached.
"""

import json
import re
import pathlib
import threading
import time

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"


# ─────────────────────────────────────────────────────────────────────────────
# Server: the stream must not go silent.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_stream_sends_keepalives_while_the_model_is_silent(client, monkeypatch):
    """The regression, directly. A turn that emits no tokens for a while must
    still produce traffic, or a proxy is entitled to drop the connection."""
    import agent_friday.routes.chat as chat_mod

    slow = threading.Event()

    def _slow_chat(*a, **k):
        # Silent for longer than one keepalive interval, then answer.
        slow.wait(timeout=3.0)
        from flask import jsonify
        return jsonify({"status": "ok", "response": "done"})

    monkeypatch.setattr(chat_mod, "chat", _slow_chat)
    monkeypatch.setattr(chat_mod, "_STREAM_KEEPALIVE_S", 0.25, raising=False)

    r = client.post("/api/chat/stream", json={"message": "hi"})
    body = r.get_data(as_text=True)
    slow.set()

    comments = [ln for ln in body.splitlines() if ln.startswith(":")]
    assert len(comments) >= 2, (
        "the stream produced %d comment frame(s); a silent turn must be kept "
        "alive, not left to a proxy timeout. body=%r" % (len(comments), body[:200]))


def test_the_keepalive_interval_is_short_enough_to_beat_a_proxy():
    """A keepalive slower than a typical proxy read timeout is decoration."""
    import agent_friday.routes.chat as chat_mod
    iv = getattr(chat_mod, "_STREAM_KEEPALIVE_S", None)
    assert iv is not None, "no keepalive interval is declared"
    assert 1 <= iv <= 20, "keepalive of %rs is outside a useful range" % iv


def test_a_normal_turn_still_streams_its_payload(client, monkeypatch):
    """The fix must not disturb the happy path."""
    import agent_friday.routes.chat as chat_mod
    from flask import jsonify
    monkeypatch.setattr(chat_mod, "chat",
                        lambda *a, **k: jsonify({"status": "ok",
                                                 "response": "hello"}))
    r = client.post("/api/chat/stream", json={"message": "hi"})
    body = r.get_data(as_text=True)
    done = [ln for ln in body.splitlines()
            if ln.startswith("data:") and '"done"' in ln]
    assert done, "no done frame: %r" % body[:200]
    payload = json.loads(done[-1][5:].strip())["payload"]
    assert payload.get("response") == "hello"


def test_keepalive_frames_are_comments_not_data(client, monkeypatch):
    """A keepalive must be an SSE comment, or the client will try to parse it as
    an event and the turn will look corrupted."""
    import agent_friday.routes.chat as chat_mod
    slow = threading.Event()

    def _slow_chat(*a, **k):
        slow.wait(timeout=2.0)
        from flask import jsonify
        return jsonify({"status": "ok"})

    monkeypatch.setattr(chat_mod, "chat", _slow_chat)
    monkeypatch.setattr(chat_mod, "_STREAM_KEEPALIVE_S", 0.25, raising=False)
    body = client.post("/api/chat/stream", json={"message": "hi"}).get_data(as_text=True)
    slow.set()
    for ln in body.splitlines():
        if ln.startswith(":"):
            assert not ln.startswith(": data"), ln
            assert "delta" not in ln, "a keepalive carried event data: %r" % ln


# ─────────────────────────────────────────────────────────────────────────────
# Client: no blind re-send, and send the token.
# ─────────────────────────────────────────────────────────────────────────────

def _chat_turn_source():
    src = INDEX.read_text(encoding="utf-8", errors="replace")
    start = src.index("async function fridayChatTurn(")
    end = src.index("\nfunction ", start + 10)
    return src[start:end]


def test_the_stream_request_carries_the_api_token():
    body = _chat_turn_source()
    assert "apiFetch('/api/chat/stream'" in body or \
           'apiFetch("/api/chat/stream"' in body, (
        "the stream is still requested with bare fetch, so no X-Friday-Token "
        "goes with it")


def test_the_fallback_request_carries_the_api_token():
    body = _chat_turn_source()
    m = re.search(r"fetch\(\s*'/api/chat'", body)
    assert m is None, (
        "the blocking fallback still uses bare fetch; it needs the token too")


def test_a_dead_stream_does_not_blindly_resend_the_turn():
    """The turn ran; re-sending it runs it twice and bills it twice. The comment
    above this code already promised it never happens -- now it does not."""
    body = _chat_turn_source()
    assert "turn/" in body or "liveness" in body or "resume" in body, (
        "nothing in fridayChatTurn tries to RECOVER a dropped stream; it only "
        "re-sends")


def test_the_resend_guard_is_not_only_about_deltas():
    """`got` was set only by an arriving delta, so a stream that died during a
    long silent phase counted as 'nothing happened' and was re-sent. A keepalive
    or an accepted request must also count as 'the turn is underway'."""
    body = _chat_turn_source()
    assert "started" in body or "accepted" in body or "turnId" in body or \
           "turn_id" in body, (
        "the re-send guard still keys only on deltas")


# ---------------------------------------------------------------------------
# Acknowledge before acting.
# ---------------------------------------------------------------------------

def test_the_prompt_asks_for_a_line_of_intent_before_tools():
    """Half one: the model is ASKED to say what it is about to do."""
    from agent_friday.services.model_router import FRIDAY_SYSTEM_PROMPT as P
    assert "SAY WHAT YOU ARE ABOUT TO DO" in P
    low = P.lower()
    assert "before your first tool call" in low
    # and it must not license inventing work
    assert "do not do" in low or "fabrication" in low


def test_a_slow_tool_is_narrated_from_the_call_itself():
    """Half two: if the model said nothing, the loop narrates the REAL call."""
    from agent_friday.services.model_router import announce_tool, TOOL_SINK
    seen = []
    tok = TOOL_SINK.set(lambda ev: seen.append(ev))
    try:
        announce_tool("query_calendar", {})
        announce_tool("search_email", {})
    finally:
        TOOL_SINK.reset(tok)
    notes = [e.get("note") for e in seen]
    assert any("calendar" in (n or "").lower() for n in notes), notes
    assert any("inbox" in (n or "").lower() for n in notes), notes


def test_a_trivial_tool_is_not_narrated():
    """Narrating a clipboard write is noise, not transparency."""
    from agent_friday.services.model_router import announce_tool, TOOL_SINK
    seen = []
    tok = TOOL_SINK.set(lambda ev: seen.append(ev))
    try:
        announce_tool("write_clipboard", {})
    finally:
        TOOL_SINK.reset(tok)
    assert seen and not seen[0].get("note"), seen


def test_narration_never_describes_a_tool_that_is_not_running():
    """The note is derived from the tool NAME, so an unknown tool gets no prose
    invented for it."""
    from agent_friday.services.model_router import announce_tool, TOOL_SINK
    seen = []
    tok = TOOL_SINK.set(lambda ev: seen.append(ev))
    try:
        announce_tool("some_tool_that_does_not_exist", {})
    finally:
        TOOL_SINK.reset(tok)
    assert seen[0]["note"] == ""


def test_announce_tool_is_silent_with_no_sink():
    from agent_friday.services.model_router import announce_tool
    announce_tool("query_calendar", {})     # must not raise


def test_the_agent_loop_announces_before_executing(monkeypatch):
    """Every tool call is announced before its handler runs.

    Both agent loops execute through `_execute_tool`, which announces once the
    governance check has allowed the call (an action held for approval is not
    work that is happening, so it is not narrated). Checked by behaviour: the
    announcement must come before the handler.
    """
    import agent_friday.services.agent as agent_mod
    from agent_friday.services import model_router as mr
    order = []
    monkeypatch.setattr(mr, "announce_tool", lambda name, args=None: order.append(("say", name)))
    monkeypatch.setitem(agent_mod.CLAUDE_TOOL_HANDLERS, "search_wiki",
                        lambda inp: order.append(("run", "search_wiki")) or "ok")
    agent_mod._execute_tool("search_wiki", {"query": "x"},
                            session_ctx={"authenticated": True, "is_background_task": True})
    assert order == [("say", "search_wiki"), ("run", "search_wiki")]
    src = (ROOT / "src" / "agent_friday" / "services" / "agent.py").read_text(
        encoding="utf-8", errors="replace")
    loops = [src.index("def _call_claude_agent"), src.index("def _oai_agentic_loop")]
    assert all(src.find("_execute_tool(", i) > i for i in loops), (
        "an agent loop no longer executes through _execute_tool")


def test_the_client_renders_tool_progress():
    body = _chat_turn_source()
    assert "ev.tool" in body, "the client ignores tool progress events"
