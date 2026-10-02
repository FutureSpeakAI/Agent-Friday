"""Every presence frame says whose state it is (avatar-visual-genome.md §13,
the label contract).

- A frame sent from Friday's own turn carries `agent: "friday"`, taken from
  the context the turn runs in, never from callers remembering to pass it.
- A helper's frames carry the helper's own opaque id and never Friday's
  label, even when the helper runs on a thread or context that was Friday's.
- The label is shape-checked like every other id: a value that is not an
  opaque id is dropped, not sent.
- Code that runs under no agent sends no label, and the avatar treats only
  `agent === "friday"` as hers, so an unlabelled frame is never mistaken
  for Friday's own state.
"""
import pathlib
import re

import pytest

from agent_friday.services import approval_feed, presence

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
BLOCK = re.compile(r"// <presence-gestures>\n(.*?)// </presence-gestures>", re.S)


@pytest.fixture(autouse=True)
def _clean():
    approval_feed.reset()
    presence.reset()
    yield
    approval_feed.reset()
    presence.reset()


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _presence_frames(q):
    return [f for f in _drain(q) if f.get("type") == "presence"]


# ── the frame's shape check ──────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["friday", "helper-0a1b2c3d4e5f"])
def test_an_opaque_agent_id_is_kept(value):
    q = approval_feed.subscribe()
    presence.emit("tool", "start", ref="c1", agent=value)
    (f,) = _presence_frames(q)
    assert f["agent"] == value


@pytest.mark.parametrize("value", [
    "helper:1",                                   # a colon is not an opaque id
    "the user's helper for the divorce papers",
    "C:/Users/someone/agent",
    "x" * 65,
    "",
    7,
    None,
    True,
])
def test_a_garbage_agent_value_is_dropped(value):
    q = approval_feed.subscribe()
    presence.emit("tool", "start", ref="c1", agent=value)
    (f,) = _presence_frames(q)
    assert "agent" not in f


def test_a_garbage_agent_context_is_dropped_too():
    q = approval_feed.subscribe()
    with presence.acting_as("not an id: has spaces"):
        presence.emit("round", "step", n=1)
    (f,) = _presence_frames(q)
    assert "agent" not in f


def test_no_agent_in_context_means_no_label_never_friday():
    q = approval_feed.subscribe()
    presence.emit("round", "step", n=1)
    presence.tool_started("search")
    for f in _presence_frames(q):
        assert "agent" not in f


# ── Friday's own turn ────────────────────────────────────────────────────────

def test_friday_s_own_chat_turn_labels_every_frame_friday():
    """The real seam every chat turn passes (`routes/chat._traced_turn`, used
    by /api/chat and by /api/chat/stream through it)."""
    from flask import Flask, jsonify
    from agent_friday.routes import chat as chat_routes

    def _turn():
        presence.emit("round", "step", n=1, turn=presence.current_turn())
        presence.tool_started("search")
        presence.tool_finished("search", ok=True)
        presence.emit("egress", "sent", route="cloud")
        return jsonify({"response": "ok"})

    app = Flask(__name__)
    q = approval_feed.subscribe()
    with app.test_request_context("/api/chat", method="POST", json={"message": "hi"}):
        chat_routes._traced_turn(_turn)()
    frames = _presence_frames(q)
    assert len(frames) == 4
    assert all(f.get("agent") == "friday" for f in frames), frames
    # and the label does not outlive the turn
    presence.emit("round", "step", n=2)
    (after,) = _presence_frames(q)
    assert "agent" not in after


# ── a helper ─────────────────────────────────────────────────────────────────

def _register_task(agent_mod, tid):
    with agent_mod.TASKS_LOCK:
        agent_mod.TASKS[tid] = {"status": "running", "name": "probe", "description": "probe"}


def test_a_subagent_s_frames_carry_its_own_id_and_never_friday_s(monkeypatch):
    """Drive the real worker (`services/agent._task_worker`) on a context that
    is Friday's own: the helper's work still never carries her label."""
    from agent_friday.services import agent as agent_mod

    tid = "probe-agent-label"
    _register_task(agent_mod, tid)

    def _helper_work(*a, **k):
        presence.emit("round", "step", n=1)
        presence.tool_started("web_search")
        presence.tool_finished("web_search", ok=True)
        presence.emit("egress", "sent", route="cloud")
        with agent_mod.TASKS_LOCK:
            agent_mod.TASKS[tid]["status"] = "complete"

    monkeypatch.setattr(agent_mod, "_task_worker_untraced", _helper_work)
    q = approval_feed.subscribe()
    try:
        with presence.acting_as(presence.FRIDAY):
            agent_mod._task_worker(tid, "probe", "x")
            # Friday's context is hers again once the helper returns.
            assert presence.current_agent() == presence.FRIDAY
    finally:
        with agent_mod.TASKS_LOCK:
            agent_mod.TASKS.pop(tid, None)

    frames = _presence_frames(q)
    helper_frames = [f for f in frames if f["state"] != "subagent"]
    assert len(helper_frames) == 4
    own = presence.helper_id(tid)
    assert own != presence.FRIDAY
    for f in helper_frames:
        assert f.get("agent") != "friday", f
        assert f.get("agent") == own, f

    # The start and end of the helper are Friday's own state (she has a
    # helper working), which is what the avatar's helpers-working count reads.
    marks = [f for f in frames if f["state"] == "subagent"]
    assert [m["phase"] for m in marks] == ["start", "end"]
    assert all(m.get("agent") == "friday" for m in marks)


def test_a_helper_id_never_collides_with_friday():
    for v in ("friday", "", "FRIDAY", 0, "x" * 500):
        hid = presence.helper_id(v)
        assert hid != presence.FRIDAY and presence._ID.match(hid)


# ── the avatar's filter ──────────────────────────────────────────────────────

@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_the_avatar_treats_only_agent_friday_as_hers(path):
    m = BLOCK.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <presence-gestures> block"
    block = m.group(1)
    assert "const FRIDAY = 'friday';" in block
    assert "if (f.agent !== FRIDAY) return;" in block
    # No exception for an unlabelled frame.
    assert "f.agent !== undefined" not in block
    assert "f.agent === undefined" not in block


def test_both_scene_files_filter_identically():
    a, b = (BLOCK.search(p.read_text(encoding="utf-8")).group(1) for p in SCENES)
    line = re.compile(r"^.*f\.agent.*$", re.M)
    assert line.findall(a) == line.findall(b)
