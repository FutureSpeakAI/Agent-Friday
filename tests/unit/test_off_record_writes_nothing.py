"""Off the record, nothing about the conversation is written to disk.

Each test goes off the record the way the owner does (the `off_record`
setting, with `off_record_stops_storage` left at its default), drives one
store with a unique marker, and then looks for the marker on disk. It checks
the store's own file and also every file under the Friday home that changed
during the test. Where the conversation must still work while it lasts, the
test also checks the content is still there in memory. The signed receipts and
governance logs are still written, but hold only what the receipt needs.
"""
import ast
import json
import os
import time
import uuid
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday import core


def _marker():
    return "offrec-" + uuid.uuid4().hex


def _written_since(start, marker, root=None):
    """Files under `root` (the Friday home) changed since `start` holding `marker`."""
    root = Path(root or core.FRIDAY_DIR)
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            try:
                if p.stat().st_mtime < start - 1:
                    continue
                if marker.encode() in p.read_bytes():
                    hits.append(str(p))
            except OSError:
                continue
    return hits


@pytest.fixture
def off_record():
    """Off the record for the test, back on the record afterwards."""
    prior = core._load_settings_raw() or {}
    core._save_settings({"off_record": True, "context_logging_enabled": True})
    start = time.time()
    yield start
    core._save_settings({"off_record": False,
                         "context_logging_enabled": bool(prior.get("context_logging_enabled"))})
    try:
        from agent_friday.services import off_record as _off
        _off.end()
    except ImportError:
        pass


def test_off_record_stops_storage_by_default():
    assert core.DEFAULT_SETTINGS["off_record_stops_storage"] is True


def test_context_log(off_record):
    m = _marker()
    core._log_context("chat_user", {"message": m})
    core._log_context("tool_call", {"input": {"q": m}, "result_preview": m})
    assert not _written_since(off_record, m, core.CONTEXT_LOG_DIR)
    assert not _written_since(off_record, m)


def test_chat_turn_and_chat_history_stay_in_memory(off_record, monkeypatch):
    from agent_friday.routes import chat
    from agent_friday.services import conversations as cv
    cid = cv.create("Scratch")["id"]
    m = _marker()
    user = {"id": uuid.uuid4().hex, "role": "user", "text": "ask " + m,
            "timestamp": "2026-09-26T12:00:00"}
    reply = {"id": uuid.uuid4().hex, "role": "friday", "text": "answer " + m,
             "timestamp": "2026-09-26T12:00:01"}
    chat._persist_turn(cid, user, reply)
    core._save_chat_history(core.CHAT_HISTORY)
    assert m not in core.CHAT_HISTORY_FILE.read_text(encoding="utf-8")
    assert not _written_since(off_record, m)
    # The conversation still works while it lasts: the model sees the turn.
    assert any(m in (x.get("text") or "") for x in cv.messages(cid))
    assert any(m in r["content"] for r in chat._conv_context(cid))


def test_conversation_messages_and_title(off_record):
    from agent_friday.services import conversations as cv
    cid = cv.create("New chat")["id"]
    m = _marker()
    cv.append(cid, {"role": "user", "text": m})
    assert not _written_since(off_record, m)
    assert cv.load(cid)["title"] == "New chat", "an off-record message never names the thread"
    assert [x["text"] for x in cv.messages(cid)] == [m]


def test_voice_turn(off_record, monkeypatch):
    import agent_friday.services.voice_engine as ve
    from agent_friday.services import conversations as cv
    monkeypatch.setattr(ve, "_index_chat_turn", lambda *a, **k: None)
    cid = cv.create("Voice")["id"]
    m = _marker()
    ve._persist_voice_turn("said " + m, "replied " + m, conversation_id=cid,
                           provider="google-gemini")
    core._save_chat_history(core.CHAT_HISTORY)
    assert not _written_since(off_record, m)
    assert any(m in (x.get("text") or "") for x in cv.messages(cid))


def test_trajectories_and_cognitive_memory(off_record):
    from agent_friday import skill_capture
    m = _marker()
    skill_capture.capture("please " + m, "done " + m, tool_trace=[{"tool": "search_web"}])
    assert not skill_capture.TRAJ_FILE.exists() or m not in skill_capture.TRAJ_FILE.read_text(encoding="utf-8")
    assert not _written_since(off_record, m)


def test_cognitive_memory_writes_nothing(off_record, tmp_path):
    from agent_friday.cognitive_memory import CognitiveMemory
    mem = CognitiveMemory(memory_dir=tmp_path / "memory")
    mem.write_memory("note-" + uuid.uuid4().hex[:6], _marker())
    assert [p.name for p in (tmp_path / "memory").iterdir()] == []


def test_behavioral_monitor_keeps_a_receipt_without_content(off_record, tmp_path):
    from agent_friday.governance.behavioral_monitor import BehavioralMonitor
    m = _marker()
    mon = BehavioralMonitor(base_dir=tmp_path, notifier=None, epistemic=lambda: None)
    sid = mon.begin_session("email " + m + " about the lease")
    mon.log_action(sid, "send_email", {"to": m + "@example.com", "body": m}, ring_level=3)
    mon.evaluate(sid)
    for p in tmp_path.iterdir():
        assert m not in p.read_text(encoding="utf-8"), p.name
    kept = [json.loads(x) for x in (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    assert kept[-1]["actions"][0]["tool_name"] == "send_email"
    assert kept[-1]["meta"] == {"off_record": True}


def test_reasoning_traces_are_never_archived(off_record):
    from agent_friday.services import reasoning_trace as rt
    m = _marker()
    before = rt.ledger_path().read_bytes() if rt.ledger_path().exists() else b""
    tid = rt.start("chat", "ask " + m)
    rt.reasoning("thinking about " + m, trace_id=tid)
    rt.tool_call("search_web", {"q": m}, trace_id=tid)
    rt.finish(tid, reply="answer " + m)
    after = rt.ledger_path().read_bytes() if rt.ledger_path().exists() else b""
    assert after == before
    assert not [r for r in rt._PENDING if r.get("trace_id") == tid], "not even queued to be written"
    assert not _written_since(off_record, m)


def test_task_journal_and_ledger(off_record, tmp_path, monkeypatch):
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path)
    m = _marker()
    tid = "task-" + uuid.uuid4().hex[:8]
    tj.index_put(tid, "do " + m, "running", time.time())
    assert tj.append(tid, "created", prompt=m) is not None
    assert tj.write_state(tid, {"prompt": m, "status": "running"}) is True
    assert tj.write_blob(tid, "ledger.json", {"goal": m}) is True
    # Off-record ending mid-task does not start recording a task begun off it.
    core._save_settings({"off_record": False})
    tj.append(tid, "progress", note=m)
    assert list(tmp_path.rglob("*")) == []


def _voice_callback(name, **bindings):
    """Execute the real nested callback with no WebSocket or provider session."""
    import agent_friday.routes.voice as rv
    tree = ast.parse(Path(rv.__file__).read_text(encoding="utf-8"))
    callbacks = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    assert len(callbacks) == 1
    scope = dict(vars(rv), **bindings)
    exec(compile(ast.Module(body=callbacks, type_ignores=[]), rv.__file__, "exec"), scope)
    return scope[name]


@pytest.mark.parametrize("engine", ["local", "live", "crew"])
def test_voice_call_summary_leaves_out_off_record_turns(off_record, monkeypatch, engine):
    from threading import Event
    from agent_friday.services import crew_runtime, taint, voice_session as vs
    monkeypatch.setattr(taint, "note_user_message", lambda *a, **k: None)
    session = None
    frames, summaries, turn_log = [], [], []
    if engine == "local":
        session = vs.VoiceSession(
            lambda frame: frames.append(frame) or True,
            ear=object(), mouth=object(), vad=object(),
            generate=lambda text, delta, cancel: "answer " + text,
            hooks={"distill": lambda turns: summaries.extend(turns)})
        # This test exercises completion and privacy, without synthesis or a GPU.
        monkeypatch.setattr(session, "_enqueue_clause", lambda *a, **k: None)
        turn_log = session.turn_log
        complete = session.run_turn
    else:
        def new_live_call():
            incoming, outgoing = [], []
            flush = _voice_callback(
                "_flush_turn", in_buf=incoming, out_buf=outgoing, turn_log=turn_log,
                _crew=[object() if engine == "crew" else None],
                _voice_session={"spoken": [], "conv_state": {},
                                "_crew_host_origin": crew_runtime.capture_host_origin()},
                _vcs=SimpleNamespace(update=lambda state, user, reply: state),
                _persist_voice_turn=lambda *a, **k: None, _open_cid=["test-voice"],
                resume_handle=[None], _conn_gen=0, _barged_turn=[False], done=Event(),
                _live_resume_clear=lambda **kwargs: None,
                _safe_send=frames.append, _voice_actions_for=lambda text: [])

            def complete_turn(text):
                incoming.append(text)
                outgoing.append("answer " + text)
                flush()

            return complete_turn

        complete = new_live_call()

    private = _marker()
    try:
        complete(private)
        assert turn_log == []
        assert any(frame.get("type") == "voice_turn_done" for frame in frames)
        core._save_settings({"off_record": False})
        if engine != "local":
            # Privacy changes end a cloud call; new public words use a new
            # callback and origin, never an old provider session rebound in place.
            complete = new_live_call()
        complete("public turn")
        # Crew records playback separately; generation completion cannot enter
        # a call summary or claim that unheard output was spoken.
        expected = [] if engine == "crew" else [("public turn", "answer public turn")]
        assert turn_log == expected
        assert private not in str(turn_log)
    finally:
        if session:
            session.close()
            session._speaker.join(1.5)
            assert not session._speaker.is_alive()
    if session:
        assert summaries == expected


def test_late_crew_playback_never_saves_an_off_record_utterance(off_record):
    from agent_friday.services import conversations as cv, off_record as private_mode
    cid = cv.create("Crew privacy")["id"]
    private = _marker()
    receipt = {"conversation_id": cid, "speaker_id": "friday", "task_id": "",
               "utterance_id": "test-utterance", "status": "finished", "played_samples": 240,
               "text": private, "project_id": None, "shared_with": {},
               "provider": "google-gemini", "off_record": True,
               "off_record_generation": private_mode.generation()}
    pending = deque([dict(receipt)])
    state = {"conversation_id": cid, "spoken": [], "conv_state": {}}
    drain = _voice_callback(
        "_drain_crew_receipts", _crew_receipts=pending, _voice_session=state,
        _vcs=SimpleNamespace(update=lambda state, user, reply: state))
    drain()
    assert any(private in message.get("text", "") for message in cv.messages(cid))
    assert all(message.get("id") for message in cv.messages(cid))
    assert state["spoken"] == [] and state["conv_state"] == {}
    assert not _written_since(off_record, private)
    core._save_settings({"off_record": False})
    assert cv.messages(cid) == []
    pending.append(dict(receipt))
    drain()
    assert not pending
    assert cv.messages(cid) == []
    assert state["spoken"] == []
    assert not _written_since(off_record, private)
    # A fresh private session must not admit a receipt from the ended one.
    core._save_settings({"off_record": True})
    pending.append(dict(receipt))
    drain()
    assert cv.messages(cid) == []
    assert state["spoken"] == [] and state["conv_state"] == {}
    assert not _written_since(off_record, private)


def test_delayed_private_memory_write_loses_to_session_end(off_record, monkeypatch):
    from agent_friday.services import off_record as private_mode
    cid = "private-race-" + uuid.uuid4().hex
    generation = private_mode.generation()
    private = _marker()

    def end_after_active_check():
        # Deterministically place end between the settings probe and memory
        # insertion. No timing, sleeps, or background threads are required.
        private_mode.end()
        return True

    monkeypatch.setattr(private_mode, "active", end_after_active_check)
    assert private_mode.remember_if_active(
        cid, {"role": "friday", "text": private}, generation=generation) is None
    assert private_mode.recalled(cid) == []
    assert not _written_since(off_record, private)


@pytest.mark.parametrize("boundary", ["current", "ended", "new-private", "before-retry",
                                      "during-gate", "during-send"])
def test_private_crew_host_notes_cannot_outlive_their_session(off_record, boundary):
    import asyncio
    from agent_friday.services import off_record as private_mode
    private = _marker()
    row = {"conversation_id": "private-notes", "profile": {"name": "Researcher"},
           "text": private, "off_record": True,
           "off_record_generation": private_mode.generation()}
    pending, gated, sent, cards = deque([row]), [], [], []
    state = {"conversation_id": "private-notes"}
    current = _voice_callback("_crew_note_current", _voice_session=state)

    def gate(text, kind):
        gated.append((text, kind))
        if boundary == "during-gate":
            core._save_settings({"off_record": False})
        return text

    async def send(**kwargs):
        sent.append(kwargs)
        if boundary == "during-send":
            core._save_settings({"off_record": False})
        if boundary in ("before-retry", "during-send"):
            raise OSError("synthetic disconnected host")

    flush = _voice_callback(
        "_flush_crew_notes", _crew=[SimpleNamespace(busy=False)], _crew_notes=pending,
        _crew_note_current=current, _voice_session=state, _injection_text=gate,
        _injection_or_card=lambda *args: cards.append(args) or "deferred")
    host = SimpleNamespace(send_client_content=send)
    if boundary in ("ended", "new-private"):
        core._save_settings({"off_record": False})
    if boundary == "new-private":
        core._save_settings({"off_record": True})
    asyncio.run(flush(host))
    if boundary == "before-retry":
        assert list(pending) == [row]
        core._save_settings({"off_record": False})
        asyncio.run(flush(host))
    assert not pending
    assert not cards, "private reports must not enter a deferred approval path"
    assert len(gated) == (0 if boundary in ("ended", "new-private") else 1)
    assert len(sent) == (1 if boundary in ("current", "before-retry", "during-send") else 0)
    if boundary == "current":
        assert private in sent[0]["turns"]["parts"][0]["text"]
        assert sent[0]["turn_complete"] is False
    assert not _written_since(off_record, private)


@pytest.mark.parametrize("retry", [False, True])
def test_public_crew_host_note_preserves_gated_delivery_and_retry(off_record, retry):
    import asyncio
    from agent_friday.services import off_record as private_mode
    core._save_settings({"off_record": False})
    row = {"conversation_id": "public-notes", "profile": {"name": "Researcher"},
           "text": "Public report.", "off_record": False,
           "off_record_generation": private_mode.generation()}
    pending, gated, sent = deque([row]), [], []
    state = {"conversation_id": "public-notes"}
    current = _voice_callback("_crew_note_current", _voice_session=state)

    def gate(text, kind, conversation_id):
        gated.append((text, kind, conversation_id))
        return "Gated public report."

    async def send(**kwargs):
        sent.append(kwargs)
        if retry and len(sent) == 1:
            raise OSError("synthetic disconnected host")

    flush = _voice_callback(
        "_flush_crew_notes", _crew=[SimpleNamespace(busy=False)], _crew_notes=pending,
        _crew_note_current=current, _voice_session=state, _injection_or_card=gate,
        _injection_text=lambda *args: pytest.fail("Public report skipped the normal gate"))
    host = SimpleNamespace(send_client_content=send)
    asyncio.run(flush(host))
    if retry:
        assert list(pending) == [row]
        asyncio.run(flush(host))
    assert not pending
    assert gated == [("Crew report from Researcher: Public report.", "crew_report",
                      "public-notes")] * (2 if retry else 1)
    assert len(sent) == len(gated)
    assert all(call["turns"]["parts"][0]["text"] == "Gated public report." for call in sent)


@pytest.mark.parametrize("boundary,tool_name", [
    ("private", "ask_crew"), ("ended", "ask_crew"), ("new-private", "ask_crew"),
    ("public", "ask_crew"), ("worker", "ask_crew"),
    ("ended", "ask-crew"), ("worker", "ask-crew")])
def test_native_crew_delegation_checks_call_origin_before_logging_and_execution(
        monkeypatch, boundary, tool_name):
    import asyncio
    from agent_friday.services import crew_runtime, off_record as private_mode, voice_engine
    private, generation = [boundary in ("private", "ended", "new-private")], [4]
    monkeypatch.setattr(private_mode, "active", lambda: private[0])
    monkeypatch.setattr(private_mode, "generation", lambda: generation[0])
    origin = crew_runtime.capture_host_origin()
    state = {"conversation_id": "native-origin", "_crew_host_origin": origin}
    assert voice_engine._voice_ctx(state)["_crew_host_origin"] is origin
    if boundary in ("ended", "new-private"):
        private[0], generation[0] = boundary == "new-private", 5
    refusal = _voice_callback(
        "_crew_delegation_refusal", _crew_host_runtime=crew_runtime,
        _crew_call_origin=origin, _voice_session=state)
    calls, logs, debug, responses, orbs = [], [], [], [], []

    async def run_with_limit(*args, runner):
        if boundary == "worker":
            generation[0] = 5
        return runner(*args)

    async def send_tool_response(**kwargs):
        responses.extend(kwargs["function_responses"])

    run = _voice_callback(
        "_run_tool_calls", _crew=[None], _crew_host={},
        _crew_delegation_refusal=refusal, _voice_session=state,
        types=SimpleNamespace(FunctionResponse=lambda **kwargs: kwargs),
        _vlog=debug.append, _log=SimpleNamespace(info=lambda *a: logs.append(a)),
        _turn_tools=[], _safe_send=lambda frame: None,
        _voice_orb_start=lambda name: orbs.append(name) or "orb",
        _voice_orb_finish=lambda *args: None, _time=SimpleNamespace(time=lambda: 0),
        _voice_tool_with_limit=run_with_limit,
        _voice_tool_run=lambda *args: calls.append(args) or "accepted",
        _gate_voice_tool_result=lambda result, name: result,
        _tell_hold=lambda: None, _user_words_ts=[0],
        _mark_if_stale=lambda result, *args: result)
    fc = SimpleNamespace(name=tool_name, args={"agent": "Researcher", "request": "Private request."},
                         id="delayed-first-tool")
    asyncio.run(run(SimpleNamespace(send_tool_response=send_tool_response),
                    SimpleNamespace(function_calls=[fc])))
    assert len(responses) == 1 and responses[0]["id"] == "delayed-first-tool"
    assert len(calls) == (1 if boundary == "public" else 0)
    if boundary in ("private", "ended", "new-private"):
        assert not orbs and not logs
        assert not any("Private request." in message for message in debug)
    if boundary != "public":
        assert "NOT DONE" in responses[0]["response"]["result"]
    else:
        assert responses[0]["response"]["result"] == "accepted"


@pytest.mark.parametrize("origin_kind", ["private", "ended-public", "public", "missing"])
def test_native_crew_reconnect_keeps_cached_provider_privacy_origin(monkeypatch, origin_kind):
    from agent_friday.routes import voice
    from agent_friday.services import conversations, crew_runtime, off_record as private_mode
    private, generation = [origin_kind == "private"], [4]
    monkeypatch.setattr(private_mode, "active", lambda: private[0])
    monkeypatch.setattr(private_mode, "generation", lambda: generation[0])
    monkeypatch.setattr(voice, "_LIVE_RESUME", {"handle": None, "ts": 0.0, "model": None,
                                             "voice": None, "crew_host_origin": None})
    monkeypatch.setattr(conversations, "load", lambda cid: {"id": cid})
    monkeypatch.setattr(voice, "_load_settings", lambda: {"voice_room_mode": "one"})
    monkeypatch.setattr(crew_runtime, "voice_room", lambda cid: {
        "conversation_id": cid, "project_id": None, "revision": 2,
        "member_ids": ["crew-fixture"], "members": [{"id": "crew-fixture", "revision": 3}]})
    owner = voice._live_resume_owner("fixture-chat", crew_enabled=True, room_mode="one")
    original = None if origin_kind == "missing" else crew_runtime.capture_host_origin()
    voice._live_resume_store("old-context", "synthetic-model", "synthetic-voice",
                              crew_host_origin=original, resume_owner=owner)
    if origin_kind in ("private", "ended-public"):
        private[0], generation[0] = False, 5
    new_call_origin = crew_runtime.capture_host_origin()
    assert not new_call_origin.off_record
    cached = voice._live_resume_load(
        "synthetic-model", "synthetic-voice", resume_owner=owner, include_crew_origin=True)
    if origin_kind != "public":
        assert cached is None  # missing or ended privacy authority cannot resume provider history
        inherited = None
    else:
        handle, inherited = cached
        assert handle == "old-context" and inherited is original
    state = {"_crew_host_origin": inherited}
    refusal = _voice_callback("_crew_delegation_refusal", _crew_host_runtime=crew_runtime,
                              _voice_session=state)
    assert bool(refusal()) is (origin_kind != "public")
    # A deliberate stop discards both provider context and its old authority.
    voice._live_resume_clear()
    assert voice._live_resume_load("synthetic-model", "synthetic-voice",
                                   resume_owner=owner, include_crew_origin=True) is None
    state["_crew_host_origin"] = new_call_origin
    assert refusal() is None


def test_crew_utterance_binds_private_generation_before_reading_mode(off_record):
    from agent_friday.services import off_record as private_mode
    from agent_friday.services.crew_voice import CrewVoiceSession
    original_generation = private_mode.generation()
    frames, receipts = [], []

    def enter_next_private_session():
        # End A and enter B precisely while the utterance reads its private
        # flag. Its already-captured origin must remain A.
        core._save_settings({"off_record": False})
        core._save_settings({"off_record": True})
        return True

    floor = CrewVoiceSession(
        frames.append, "private-origin", room=lambda cid: {"members": []},
        off_record=enter_next_private_session,
        off_record_generation=private_mode.generation, receipt=receipts.append)
    assert floor.host_audio(b"\0\0" * 240)
    assert floor.host_end()
    start = next(frame for frame in frames if frame["type"] == "crew_speech_start")
    assert floor.acknowledge({
        "session_id": start["session_id"], "epoch": start["epoch"],
        "utterance_id": start["utterance_id"], "status": "finished", "played_samples": 240})
    assert receipts[0]["off_record_generation"] == original_generation
    assert private_mode.generation() != original_generation
    assert private_mode.remember_if_active(
        "private-origin", {"text": "Old private output"},
        generation=receipts[0]["off_record_generation"]) is None
    assert private_mode.recalled("private-origin") == []


def test_decisions_log(off_record):
    from agent_friday.services import decisions
    m = _marker()
    before = decisions.log_path().read_bytes() if decisions.log_path().exists() else b""
    decisions.decide("policy_class", "send an email saying " + m)
    after = decisions.log_path().read_bytes() if decisions.log_path().exists() else b""
    assert after == before


def test_approval_card_keeps_only_its_receipt_on_disk(off_record):
    from agent_friday.services import approvals
    m = _marker()
    card = approvals.create_approval(
        kind="send_email", subject_type="test", subject_id=uuid.uuid4().hex,
        title="Email " + m, description="body " + m, action_description="send " + m,
        payload={"body": m}, force_gate=True)
    stored = approvals.APPROVALS_FILE.read_text(encoding="utf-8")
    assert m not in stored
    row = [r for r in json.loads(stored) if r["approval_id"] == card["approval_id"]][0]
    assert row["kind"] == "send_email" and row["status"] == "pending" and row["off_record"]
    # The card itself still works in this session.
    assert approvals.get_approval(card["approval_id"])["payload"]["body"] == m
    approvals.decide(card["approval_id"], "approve")
    assert m not in approvals.APPROVALS_FILE.read_text(encoding="utf-8")
    assert approvals.get_approval(card["approval_id"])["status"] == "approved"
    assert not _written_since(off_record, m)


def test_boot_guard_snapshot_is_deferred(off_record, tmp_path, monkeypatch):
    from agent_friday.services import boot_guard as bg
    monkeypatch.setattr(bg, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bg, "KNOWN_GOOD", tmp_path / "state" / "known_good")
    src = tmp_path / "workspace_studio"
    src.mkdir()
    m = _marker()
    (src / "draft.md").write_text(m, encoding="utf-8")
    bg.snapshot_known_good(paths=[src])
    assert not _written_since(off_record, m, tmp_path / "state")


def test_signed_receipts_hold_only_the_receipt(off_record):
    from agent_friday.governance import action_gate
    m = _marker()
    action_gate._receipt({"tool": "send_email", "class": "outward", "decision": "card",
                          "reason": "sending " + m, "target": m + "@example.com",
                          "args_hash": "0" * 64, "surface": "chat"})
    last = json.loads(action_gate.receipts_path().read_text(encoding="utf-8").splitlines()[-1])
    assert m not in json.dumps(last)
    assert {"tool", "class", "decision", "timestamp"} <= set(last)
    assert "args_hash" not in last and "reason" not in last and "target" not in last
    assert action_gate.verify_receipt(last)


def test_governance_logs_hold_no_text(off_record, tmp_path):
    from agent_friday.services import activity_ledger, dissent_gate, egress_gate
    m = _marker()
    activity_ledger.record("subagent_spawn", task_id="t1", description="do " + m, model="x")
    dissent_gate.record_dissent_event("send " + m, {"category": "privacy", "statement": m,
                                                    "conflict_source": {"text": m}})
    egress_gate._log("anthropic", "message", 2, "block", "judged: " + m,
                     log_path=tmp_path / "egress.jsonl")
    assert m not in (tmp_path / "egress.jsonl").read_text(encoding="utf-8")
    assert not _written_since(off_record, m)


def test_web_fetch_cache(off_record, monkeypatch):
    from agent_friday.services import firecrawl, web_fetch
    m = _marker()
    monkeypatch.setattr(web_fetch, "_firecrawl_enabled", lambda: True)
    monkeypatch.setattr(firecrawl, "scrape", lambda url, **k: {
        "ok": True, "markdown": "page about " + m, "title": "t", "status": 200})
    rec = web_fetch.fetch("https://example.com/" + uuid.uuid4().hex, use_cache=False,
                          register_provenance=False)
    assert rec.get("ok")
    assert not _written_since(off_record, m)


def test_switching_off_the_record_off_drops_the_session(off_record):
    from agent_friday.services import conversations as cv
    cid = cv.create("Scratch")["id"]
    m = _marker()
    cv.append(cid, {"role": "user", "text": m})
    assert cv.messages(cid)
    core._save_settings({"off_record": False})
    assert cv.messages(cid) == []


def test_the_chat_box_says_nothing_is_being_saved():
    root = Path(__file__).resolve().parents[2]
    for rel in ("index.html", "ui_parts/app.html"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "Off the record: nothing is being saved" in text, rel
        i = text.index("function FridayChatInput(")
        assert "useFridayOffRecord()" in text[i:i + 400], rel


def test_notifications_keep_a_stub_without_the_words(off_record):
    from agent_friday import notifications_engine as ne
    m = _marker()
    n = ne.push(title="Approval needed: email " + m, body=m, source="approvals",
                kind="approval_pending", proactive_chat=True)
    assert m not in ne.NOTIF_FILE.read_text(encoding="utf-8")
    assert [x for x in ne.list_notifications() if x["id"] == n["id"]][0]["body"] == m


def test_forensics_snapshot_never_copies_what_was_made_off_the_record(off_record, tmp_path, monkeypatch):
    """The scheduled forensics snapshot copies the live orb and task registries
    to disk; an orb made off the record stays in memory after it ends, so the
    mark travels with the record and the snapshot drops it."""
    import importlib.util
    m = _marker()
    pid = "orb-" + uuid.uuid4().hex[:8]
    core.process_register(pid, name="Chat turn", label="ask " + m)
    try:
        assert core.PROCESSES[pid]["off_record"] is True
    finally:
        with core.PROCESSES_LOCK:
            row = dict(core.PROCESSES.pop(pid, {}))
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("forensics_snapshot",
                                                  root / "ops" / "forensics-snapshot.py")
    fs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fs)
    monkeypatch.setattr(fs, "OUT", tmp_path)
    kept = {"id": "orb-kept", "label": "on the record"}
    monkeypatch.setattr(fs, "get_json", lambda ep: {"processes": [row, kept]})
    assert fs.capture_registry({}, "/api/processes", "orbs", "id") == 1
    text = (tmp_path / "orbs.jsonl").read_text(encoding="utf-8")
    assert m not in text and "orb-kept" in text
