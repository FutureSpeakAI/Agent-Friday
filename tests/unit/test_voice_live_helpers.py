"""Unit tests for the Gemini Live continuity helpers in routes/voice.py.

These pin the pieces that make an hours-long voice call survivable:
the cross-connection resumption cache (browser reconnect resumes the SAME
conversation), the speech-RMS gate that arms the stall watchdog, and the
GoAway time_left parsing that schedules a graceful drain.
"""
import ast
import asyncio
import base64
import json
import math
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday.routes import voice as v


@pytest.fixture(autouse=True)
def _clean_resume_cache(monkeypatch):
    monkeypatch.setattr(v, "_LIVE_RESUME", {})
    monkeypatch.setattr(v, "_LIVE_CONN_GEN", [0])
    v._live_resume_clear()
    yield
    v._live_resume_clear()


# ── Resumption cache ──────────────────────────────────────────────────────

@pytest.fixture
def resume_context(monkeypatch):
    from agent_friday.services import conversations, crew_runtime, off_record
    state = {"private": False, "generation": 4, "room_mode": "one",
             "project": "project-a", "room_revision": 3, "profile_revision": 7,
             "enabled": True, "archived": False, "member": "crew-a"}
    monkeypatch.setattr(off_record, "active", lambda: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    monkeypatch.setattr(conversations, "load", lambda cid: {
        "id": cid, "project": state["project"],
        "status": "archived" if state["archived"] else "active"})
    monkeypatch.setattr(v, "_load_settings", lambda: {"voice_room_mode": state["room_mode"]})
    monkeypatch.setattr(crew_runtime, "voice_room", lambda cid: {
        "conversation_id": cid, "project_id": state["project"],
        "revision": state["room_revision"], "member_ids": [state["member"]],
        "members": [{"id": state["member"], "revision": state["profile_revision"]}]
    } if state["enabled"] else None)
    return state, crew_runtime.capture_host_origin()


def _owner(cid="chat-a", *, crew=False, mode="one"):
    return v._live_resume_owner(cid, crew_enabled=crew, room_mode=mode)


def _store(owner, origin, *, handle="handle-1", gen=None):
    v._live_resume_store(handle, "model-a", "Aoede", gen=gen,
                         resume_owner=owner, crew_host_origin=origin)


def _load(owner, *, model="model-a", voice="Aoede", with_origin=False):
    return v._live_resume_load(model, voice, resume_owner=owner,
                               include_crew_origin=with_origin)


def test_resume_store_load_roundtrip(resume_context):
    _state, origin = resume_context
    owner = _owner()
    _store(owner, origin)
    assert _load(owner) == "handle-1"


def test_resume_load_rejects_model_or_voice_mismatch(resume_context):
    _state, origin = resume_context
    owner = _owner()
    _store(owner, origin)
    # A handle only resumes the session it came from — different model or
    # voice must start fresh, not resume into a config Gemini will reject.
    assert _load(owner, model="model-b") is None
    assert _load(owner, voice="Kore") is None


def test_resume_clear_and_empty(resume_context):
    _state, origin = resume_context
    owner = _owner()
    assert _load(owner) is None
    _store(owner, origin)
    v._live_resume_clear()
    assert _load(owner) is None


def test_resume_load_expires_after_ttl(monkeypatch, resume_context):
    _state, origin = resume_context
    owner = _owner()
    _store(owner, origin)
    real_time = v._time.time
    monkeypatch.setattr(v._time, "time",
                        lambda: real_time() + v._LIVE_RESUME_TTL_S + 1)
    assert _load(owner) is None


@pytest.mark.parametrize("crew", [False, True])
def test_interrupted_reconnect_requires_exact_conversation_and_mode(resume_context, crew):
    _state, origin = resume_context
    owner = _owner(crew=crew)
    old_generation = v._live_conn_next()
    _store(owner, origin, gen=old_generation)
    v._live_conn_next()  # socket dropped, without a deliberate end frame
    assert _load(_owner("chat-b", crew=crew)) is None
    assert _load(_owner(crew=not crew)) is None
    handle, inherited = _load(_owner(crew=crew), with_origin=True)
    assert handle == "handle-1" and inherited is origin


@pytest.mark.parametrize("change", [
    {"room_revision": 4}, {"profile_revision": 8}, {"member": "crew-b"},
    {"project": "project-b"}, {"enabled": False}, {"archived": True}, {"room_mode": "room"},
])
def test_reconnect_rejects_changed_room_authority_instead_of_relabelling(resume_context, change):
    state, origin = resume_context
    owner = _owner(crew=True)
    _store(owner, origin)
    assert _load(owner) == "handle-1"
    state.update(change)
    # The live call retains its immutable initial owner, and a new call
    # cannot relabel the old provider-held context with fresh room settings.
    assert not v._live_resume_owner_current(owner)
    assert _load(owner) is None
    assert _load(_owner(crew=True, mode=state["room_mode"])) is None
    assert v._LIVE_RESUME["owner"] is owner


@pytest.mark.parametrize("field,value", [
    ("owner", None), ("owner", {"conversation_id": "chat-a"}),
    ("crew_host_origin", None), ("crew_host_origin", {"off_record": False, "generation": 4}),
    ("ts", None), ("ts", float("nan")), ("handle", []), ("model", None),
])
def test_incomplete_or_json_shaped_cached_authority_never_resumes(resume_context, field, value):
    _state, origin = resume_context
    owner = _owner()
    _store(owner, origin)
    v._LIVE_RESUME[field] = value
    assert _load(owner) is None


def test_old_ownerless_store_cannot_create_a_resumable_entry(resume_context):
    _state, origin = resume_context
    v._live_resume_store("legacy-handle", "model-a", "Aoede", crew_host_origin=origin)
    assert _load(_owner()) is None


def test_chat_change_clear_fences_late_store_from_that_provider_session(resume_context):
    _state, origin = resume_context
    owner = _owner()
    generation = v._live_conn_next()
    _store(owner, origin, gen=generation)
    v._live_resume_clear(gen=generation)  # the chat-change and deliberate-stop path
    _store(owner, origin, gen=generation, handle="late-old-context")
    assert _load(owner) is None
    assert _load(_owner("chat-b")) is None
    fresh_generation = v._live_conn_next()
    fresh_owner = _owner("chat-b")
    _store(fresh_owner, origin, gen=fresh_generation, handle="fresh-context")
    assert _load(fresh_owner) == "fresh-context"


@pytest.mark.parametrize("crew", [False, True])
def test_actual_chat_change_ends_provider_owner_before_late_handle(resume_context, crew):
    _state, origin = resume_context
    owner = _owner(crew=crew)
    generation = v._live_conn_next()
    _store(owner, origin, gen=generation)
    frames, flushed, closed, receipts = [], [], [], []
    done = threading.Event()
    session = {"conversation_id": owner.conversation_id, "owner_text": "Original request",
               "delivery_preferences": {owner.conversation_id: {"voice_speaking_pace": "measured"}},
               "conv_state": {}, "spoken": []}
    pending = [["old input"], ["old output"], ["old result"], ["old Crew note"]]
    handle = ["handle-1"]
    tree = ast.parse(Path(v.__file__).read_text(encoding="utf-8"))
    callback = [node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "_retarget_call"][-1]
    scope = dict(vars(v), _voice_session=session, _resume_owner=owner,
                 _crew=[SimpleNamespace(close=lambda: closed.append(True)) if crew else None],
                 _drain_crew_receipts=lambda: receipts.append(True),
                 _flush_turn=lambda cid=None: flushed.append(cid or owner.conversation_id),
                 resume_handle=handle, _conn_gen=generation, _barged_turn=[False],
                 _safe_send=frames.append, done=done, in_buf=pending[0], out_buf=pending[1],
                 _inject_q=pending[2], _crew_notes=pending[3])
    exec(compile(ast.Module(body=[callback], type_ignores=[]), v.__file__, "exec"), scope)
    retarget = scope["_retarget_call"]
    assert not retarget(owner.conversation_id)
    assert not done.is_set() and _load(owner) == "handle-1"
    assert retarget("chat-b")
    assert done.is_set() and handle == [None] and all(not items for items in pending)
    assert session["conversation_id"] == owner.conversation_id and flushed == [owner.conversation_id]
    assert len(closed) == len(receipts) == int(crew)
    _store(owner, origin, gen=generation, handle="late-mixed-context")
    assert _load(owner) is None and _load(_owner("chat-b", crew=crew)) is None


def test_stale_socket_cannot_overwrite_or_clear_new_owner(resume_context):
    _state, origin = resume_context
    old_generation = v._live_conn_next()
    old_owner = _owner()
    _store(old_owner, origin, gen=old_generation)
    new_generation = v._live_conn_next()
    new_owner = _owner("chat-b")
    _store(new_owner, origin, gen=new_generation, handle="new-context")
    _store(old_owner, origin, gen=old_generation, handle="late-old-context")
    v._live_resume_clear(gen=old_generation)
    assert _load(new_owner) == "new-context"
    assert _load(old_owner) is None


@pytest.mark.parametrize("private_at_start", [False, True])
def test_same_lifetime_reconnect_keeps_original_privacy_authority(resume_context, private_at_start):
    from agent_friday.services import agent, crew_runtime
    state, _unused = resume_context
    state["private"] = private_at_start
    origin = crew_runtime.capture_host_origin()
    owner = _owner(crew=True)
    _store(owner, origin)
    fresh_origin = crew_runtime.capture_host_origin()
    handle, inherited = _load(owner, with_origin=True)
    assert handle == "handle-1" and inherited is origin and inherited is not fresh_origin
    assert bool(agent._crew_delegation_denial(
        "ask_crew", {"_crew_host_origin": inherited})) is private_at_start


@pytest.mark.parametrize("private_at_start,change", [
    (True, {"private": False, "generation": 5}),
    (False, {"private": False, "generation": 5}),
    (False, {"private": True}),
    (True, {"private": True, "generation": 5}),
])
@pytest.mark.parametrize("crew", [False, True])
def test_cached_history_cannot_cross_privacy_lifetime(
        resume_context, private_at_start, change, crew):
    from agent_friday.services import crew_runtime
    state, _unused = resume_context
    state["private"] = private_at_start
    origin = crew_runtime.capture_host_origin()
    owner = _owner(crew=crew)
    _store(owner, origin)
    assert _load(owner, with_origin=True) == ("handle-1", origin)
    state.update(change)
    assert _load(owner, with_origin=True) is None
    assert v._LIVE_RESUME["crew_host_origin"] is origin
    fresh_origin = crew_runtime.capture_host_origin()
    _store(owner, fresh_origin, handle="fresh-context")
    handle, inherited = _load(owner, with_origin=True)
    assert handle == "fresh-context" and inherited is fresh_origin


def test_privacy_lifetime_change_during_state_read_refuses_resume(monkeypatch, resume_context):
    from agent_friday.services import off_record
    state, origin = resume_context
    owner = _owner()
    _store(owner, origin)

    def end_during_read():
        state["generation"] += 1
        return False

    monkeypatch.setattr(off_record, "active", end_during_read)
    assert _load(owner, with_origin=True) is None


def _actual_provider_connect(scope):
    """Run the real retry loop with a mocked provider and no socket or model."""
    tree = ast.parse(Path(v.__file__).read_text(encoding="utf-8"))
    loops = [node for node in ast.walk(tree)
             if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
             and node.target.id == "_try"]
    assert len(loops) == 1
    wrapper = ast.parse("async def run():\n    session_cm = None\n    session_ai = None\n    return session_ai\n")
    wrapper.body[0].body.insert(2, loops[0])
    bindings = dict(vars(v), **scope)
    exec(compile(ast.fix_missing_locations(wrapper), v.__file__, "exec"), bindings)
    return asyncio.run(bindings["run"]())


def _connect_scope(owner, origin, generation, connect, *, handle="in-memory-history"):
    async def no_wait(_seconds):
        pass

    return {
        "_max_tries": 3 if handle else 1, "_resume_owner": owner,
        "_voice_session": {"_crew_host_origin": origin}, "_conn_gen": generation,
        "resume_handle": [handle], "_use_handle": handle,
        "_safe_send": lambda _frame: None, "done": threading.Event(),
        "active_client": SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=connect))),
        "model_name": "model-a", "types": None, "per_model_kwargs": {},
        "per_model_cfg": {"fresh": True},
        "_leg_config": lambda _types, _kwargs, value: {"handle": value},
        "_classify_live_error": lambda _error: "transient", "_vlog": lambda _text: None,
        "asyncio": SimpleNamespace(sleep=no_wait),
    }


@pytest.mark.parametrize("private_at_start,change", [
    (True, {"private": False, "generation": 5}),
    (False, {"private": False, "generation": 5}),
    (False, {"private": True}),
    (True, {"private": True, "generation": 5}),
])
@pytest.mark.parametrize("handle", ["in-memory-history", None])
def test_actual_connect_gate_ends_changed_privacy_without_rebinding(
        resume_context, private_at_start, change, handle):
    from agent_friday.services import crew_runtime
    state, _unused = resume_context
    state["private"] = private_at_start
    origin = crew_runtime.capture_host_origin()
    owner = _owner()
    generation = v._live_conn_next()
    _store(owner, origin, gen=generation)
    calls = []
    accepted = object()

    class Connection:
        async def __aenter__(self):
            return accepted

    def connect(**kwargs):
        calls.append(kwargs)
        return Connection()

    scope = _connect_scope(owner, origin, generation, connect, handle=handle)
    state.update(change)
    result = _actual_provider_connect(scope)
    assert calls == [], "expired provider history reached a new connection"
    assert result is None and scope["done"].is_set()
    assert scope["resume_handle"] == [None] and v._LIVE_RESUME["handle"] is None
    assert scope["_voice_session"]["_crew_host_origin"] is origin


@pytest.mark.parametrize("private", [False, True])
def test_actual_connect_gate_preserves_same_lifetime_in_memory_handle(resume_context, private):
    from agent_friday.services import crew_runtime
    state, _unused = resume_context
    state["private"] = private
    origin = crew_runtime.capture_host_origin()
    owner = _owner()
    generation = v._live_conn_next()
    calls, accepted = [], object()

    class Connection:
        async def __aenter__(self):
            return accepted

    def connect(**kwargs):
        calls.append(kwargs)
        return Connection()

    scope = _connect_scope(owner, origin, generation, connect)
    assert _actual_provider_connect(scope) is accepted
    assert calls == [{"model": "model-a", "config": {"handle": "in-memory-history"}}]
    assert not scope["done"].is_set()
    assert scope["_voice_session"]["_crew_host_origin"] is origin


def test_actual_connect_retry_rechecks_privacy_before_second_attempt(resume_context):
    state, origin = resume_context
    owner = _owner()
    generation = v._live_conn_next()
    _store(owner, origin, gen=generation)
    calls, accepted = [], object()

    class Connection:
        async def __aenter__(self):
            if len(calls) == 1:
                state.update(private=False, generation=5)
                raise RuntimeError("synthetic transient connect failure")
            return accepted

    def connect(**kwargs):
        calls.append(kwargs)
        return Connection()

    scope = _connect_scope(owner, origin, generation, connect)
    result = _actual_provider_connect(scope)
    assert len(calls) == 1, "retry reused a provider context after its privacy lifetime ended"
    assert result is None and scope["done"].is_set()
    assert scope["resume_handle"] == [None] and v._LIVE_RESUME["handle"] is None
    assert scope["_voice_session"]["_crew_host_origin"] is origin


@pytest.mark.parametrize("private_at_start,change", [
    (True, {"private": False, "generation": 5}),
    (False, {"private": False, "generation": 5}),
    (False, {"room_revision": 4}),
])
@pytest.mark.parametrize("close_raises", [False, True])
def test_actual_successful_connect_rechecks_authority_and_closes_once(
        resume_context, private_at_start, change, close_raises):
    from agent_friday.services import crew_runtime
    state, _unused = resume_context
    state["private"] = private_at_start
    origin = crew_runtime.capture_host_origin()
    owner = _owner(crew=True)
    generation = v._live_conn_next()
    _store(owner, origin, gen=generation)
    calls, closed, accepted = [], [], object()

    class Connection:
        async def __aenter__(self):
            state.update(change)
            return accepted

        async def __aexit__(self, *args):
            closed.append(args)
            if close_raises:
                raise RuntimeError("synthetic close failure")

    def connect(**kwargs):
        calls.append(kwargs)
        return Connection()

    scope = _connect_scope(owner, origin, generation, connect)
    result = _actual_provider_connect(scope)
    assert result is None, "a completed handshake admitted a context whose authority ended while awaiting it"
    assert len(calls) == 1 and closed == [(None, None, None)]
    assert scope["done"].is_set() and scope["resume_handle"] == [None]
    assert v._LIVE_RESUME["handle"] is None
    assert scope["_voice_session"]["_crew_host_origin"] is origin


@pytest.mark.parametrize("changed", [False, True])
def test_actual_renewal_backlog_honors_conversation_boundary(monkeypatch, changed):
    from agent_friday.services import conversations
    tree = ast.parse(Path(v.__file__).read_text(encoding="utf-8"))
    loops = [node for node in ast.walk(tree) if isinstance(node, ast.While)
             and any(isinstance(child, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "_raw0"
                             for target in child.targets)
                     for child in ast.walk(node))
             and not any(isinstance(child, ast.While) and child is not node
                         for child in ast.walk(node))]
    assert len(loops) == 1
    target = "chat-b" if changed else "chat-a"
    frames = [json.dumps({"type": "audio", "data": base64.b64encode(b"before").decode()}),
              json.dumps({"type": "conversation", "id": " selected "}),
              json.dumps({"type": "audio", "data": base64.b64encode(b"after").decode()})]
    resolved, retargeted = [], []
    done = threading.Event()

    def resolve(cid):
        resolved.append(cid)
        return target

    def retarget(cid):
        retargeted.append(cid)
        if cid != "chat-a":
            done.set()
            return True
        return False

    monkeypatch.setattr(conversations, "resolve", resolve)
    scope = dict(vars(v), done=done, ws=SimpleNamespace(receive=lambda **_kwargs: frames.pop(0) if frames else None),
                 _stale_audio=0, _seam_chunks=[], _retarget_call=retarget,
                 _client_signal_seen=[False], _client_playing=[False])
    exec(compile(ast.Module(body=loops, type_ignores=[]), v.__file__, "exec"), scope)
    assert resolved == ["selected"] and retargeted == [target]
    assert done.is_set() is changed
    assert scope["_seam_chunks"] == ([] if changed else [b"before", b"after"])
    assert len(frames) == int(changed)


def _actual_flush_turn(scope):
    tree = ast.parse(Path(v.__file__).read_text(encoding="utf-8"))
    callbacks = [node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == "_flush_turn"]
    assert len(callbacks) == 1
    bindings = dict(vars(v), **scope)
    exec(compile(ast.Module(body=callbacks, type_ignores=[]), v.__file__, "exec"), bindings)
    bindings["_flush_turn"]()


def _flush_scope(monkeypatch, owner, origin, generation):
    from agent_friday.services import taint
    persisted, tainted, actions, frames = [], [], [], []
    monkeypatch.setattr(taint, "note_user_message", lambda *args: tainted.append(args))
    scope = {
        "_voice_session": {"_crew_host_origin": origin, "spoken": [], "conv_state": {}},
        "in_buf": ["Private input marker"], "out_buf": ["Private reply marker"],
        "resume_handle": ["in-memory-history"], "_conn_gen": generation,
        "_barged_turn": [False], "done": threading.Event(), "_safe_send": frames.append,
        "_crew": [None], "_open_cid": [owner.conversation_id], "turn_log": [],
        "_vcs": SimpleNamespace(update=lambda *args: {"updated": True}),
        "_persist_voice_turn": lambda *args, **kwargs: persisted.append((args, kwargs)),
        "_voice_actions_for": lambda text: actions.append(text) or [],
    }
    return scope, persisted, tainted, actions, frames


@pytest.mark.parametrize("private_at_start,change", [
    (True, {"private": False, "generation": 5}),
    (False, {"private": False, "generation": 5}),
    (False, {"private": True}),
    (True, {"private": True, "generation": 5}),
])
def test_actual_turn_flush_drops_expired_privacy_before_content_instrumentation(
        monkeypatch, resume_context, private_at_start, change):
    from agent_friday.services import crew_runtime
    state, _unused = resume_context
    state["private"] = private_at_start
    origin = crew_runtime.capture_host_origin()
    owner = _owner()
    generation = v._live_conn_next()
    _store(owner, origin, gen=generation)
    scope, persisted, tainted, actions, frames = _flush_scope(monkeypatch, owner, origin, generation)
    state.update(change)
    _actual_flush_turn(scope)
    assert not persisted and not tainted and not actions
    assert not any("marker" in json.dumps(frame) for frame in frames)
    assert not scope["in_buf"] and not scope["out_buf"] and not scope["turn_log"]
    assert scope["_voice_session"] == {"_crew_host_origin": origin, "spoken": [], "conv_state": {}}
    assert scope["done"].is_set() and scope["_barged_turn"] == [True]
    assert scope["resume_handle"] == [None] and v._LIVE_RESUME["handle"] is None


@pytest.mark.parametrize("private", [False, True])
def test_actual_turn_flush_preserves_same_lifetime_behavior(monkeypatch, resume_context, private):
    from agent_friday.services import crew_runtime
    state, _unused = resume_context
    state["private"] = private
    origin = crew_runtime.capture_host_origin()
    owner = _owner()
    generation = v._live_conn_next()
    scope, persisted, tainted, actions, frames = _flush_scope(monkeypatch, owner, origin, generation)
    _actual_flush_turn(scope)
    assert persisted == [(("Private input marker", "Private reply marker"),
                          {"conversation_id": owner.conversation_id, "provider": "google-gemini"})]
    assert tainted == [("voice-live", "Private input marker")] and actions == ["Private input marker"]
    assert frames == [{"type": "voice_turn_done", "user_text": "Private input marker",
                       "agent_text": "Private reply marker"}]
    assert scope["turn_log"] == ([] if private else [("Private input marker", "Private reply marker")])
    assert not scope["done"].is_set() and scope["_voice_session"]["_crew_host_origin"] is origin


# ── Speech RMS gate ───────────────────────────────────────────────────────

def _pcm_sine(amplitude, n=1600, rate=16000, freq=440):
    return struct.pack(
        f"<{n}h",
        *(int(amplitude * math.sin(2 * math.pi * freq * i / rate))
          for i in range(n)))


def test_quick_rms_silence_vs_speech():
    assert v._quick_rms(b"\x00\x00" * 800) == 0
    quiet = v._quick_rms(_pcm_sine(150))    # room noise / speaker echo bleed
    loud = v._quick_rms(_pcm_sine(8000))    # actual speech
    assert quiet < v.LIVE_SPEECH_RMS <= loud


def test_quick_rms_empty_and_tiny():
    assert v._quick_rms(b"") == 0
    assert v._quick_rms(b"\x00") == 0          # odd byte → no full sample
    assert v._quick_rms(struct.pack("<h", 1000)) == 1000


# ── Barge-in detector ─────────────────────────────────────────────────────
#
# The contract: speaker bleed (Friday's own voice re-captured by the mic)
# must NEVER fire — that's the bug that got the old client-side detector
# removed — while deliberate, sustained talk-over fires within ~sustain_ms.

def _mk_detector(**kw):
    kw.setdefault("grace_ms", 800)
    kw.setdefault("sustain_ms", 200)
    d = v.LiveBargeDetector(**kw)
    d.reset_turn(now=1000.0)
    return d


def test_barge_ignores_grace_window_even_when_loud():
    d = _mk_detector()
    # Loud chunks inside the grace window never fire — they're sampled for the
    # baseline, which is seeded (percentile, capped) on the first post-grace feed.
    for i in range(9):
        assert d.feed(3000, 85, now=1000.0 + i * 0.085) is False
    d.feed(100, 85, now=1001.0)
    assert 0 < d.ema <= d.BLEED_EMA_CAP


def test_barge_grace_interjection_does_not_poison_baseline():
    # User interjects during PART of the grace window: the 25th-percentile
    # seeding keeps the baseline at the actual bleed level (the quiet chunks),
    # so their continued speech after grace still fires.
    d = _mk_detector()
    t = 1000.0
    for _ in range(5):                       # real bleed
        d.feed(300, 85, now=t)
        t += 0.085
    for _ in range(4):                       # user already talking
        d.feed(5000, 85, now=t)
        t += 0.085
    t = 1001.0
    fired = [d.feed(2500, 85, now=t + i * 0.085) for i in range(4)]
    assert d.ema <= 400          # seeded from the bleed quartile, not the speech
    assert any(fired)


def test_barge_bleed_never_fires_and_sets_baseline_bar():
    d = _mk_detector()
    # Grace: bleed ~300 RMS learned as baseline.
    t = 1000.0
    for _ in range(10):
        assert d.feed(300, 85, now=t) is False
        t += 0.085
    t = 1001.0  # past grace
    # Post-grace bleed keeps not firing (300 < max(550, 3*300=900)).
    for _ in range(50):
        assert d.feed(320, 85, now=t) is False
        t += 0.085
    # Loud-ish echo spike below the relative bar also doesn't fire.
    assert d.feed(800, 85, now=t) is False


def test_barge_fires_on_sustained_talkover():
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):                      # learn ~300 bleed during grace
        d.feed(300, 85, now=t)
        t += 0.085
    t = 1001.0
    fired = []
    for _ in range(4):                       # 4 × 85ms = 340ms of loud speech
        fired.append(d.feed(2500, 85, now=t))
        t += 0.085
    assert any(fired)
    # Fired at/after the sustain threshold, not on the first loud chunk.
    assert fired[0] is False and fired[-1] is True


def test_barge_single_spike_does_not_fire():
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):
        d.feed(200, 85, now=t)
        t += 0.085
    t = 1001.0
    assert d.feed(4000, 85, now=t) is False          # one 85ms bang (door slam)
    assert d.feed(150, 85, now=t + 0.085) is False   # back to quiet resets sustain
    assert d.sustained == 0.0


def test_barge_quiet_room_floor_governs():
    # Near-zero bleed (headset mic, AEC very good): the absolute floor is the
    # bar, so normal speech (well above 550) still fires.
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):
        d.feed(5, 85, now=t)
        t += 0.085
    t = 1001.0
    fired = [d.feed(900, 85, now=t + i * 0.085) for i in range(4)]
    assert any(fired)


def test_barge_baseline_poisoning_is_capped():
    # A user who talks THROUGH the entire grace window must still be able to
    # interrupt afterwards: baseline seeding is capped, so the bar can't rise
    # beyond mult × BLEED_EMA_CAP.
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):                      # loud speech all through grace
        d.feed(6000, 85, now=t)
        t += 0.085
    t = 1001.0
    fired = [d.feed(6000, 85, now=t + i * 0.085) for i in range(4)]
    assert d.ema <= v.LiveBargeDetector.BLEED_EMA_CAP
    assert any(fired)


def test_barge_reset_turn_relearns_baseline():
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):
        d.feed(300, 85, now=t)
        t += 0.085
    d.feed(2500, 85, now=1001.0)
    assert d.sustained > 0
    d.reset_turn(now=2000.0)                 # next response starts
    assert d.sustained == 0.0 and d.ema == 0.0
    # New grace window applies again.
    assert d.feed(2500, 85, now=2000.1) is False


# ── GoAway time_left parsing ──────────────────────────────────────────────

def test_duration_parsing_variants():
    from datetime import timedelta
    assert v._duration_to_seconds(None) is None
    assert v._duration_to_seconds(5) == 5.0
    assert v._duration_to_seconds(2.5) == 2.5
    assert v._duration_to_seconds("7s") == 7.0
    assert v._duration_to_seconds("3") == 3.0
    assert v._duration_to_seconds(timedelta(seconds=9)) == 9.0
    assert v._duration_to_seconds("not-a-duration") is None
