"""Presence frames: the text-free events the holographic lattice moves on.

docs/design/active/avatar-visual-genome.md §13. A frame names a state from a
fixed list and carries counts and opaque ids only, never the user's words.
Frames ride the approvals stream (services/approval_feed.py) and are lossy:
a page that falls behind loses presence frames, never approval cards.
"""
import pytest

from agent_friday.services import approval_feed, presence


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


def test_a_frame_reaches_every_page_on_the_approvals_stream():
    a, b = approval_feed.subscribe(), approval_feed.subscribe()
    presence.emit("tool", "start", turn="tr_1", ref="c1")
    fa, fb = _drain(a), _drain(b)
    assert len(fa) == 1 and fa == fb
    f = fa[0]
    assert f["type"] == "presence" and f["state"] == "tool" and f["phase"] == "start"
    assert f["turn"] == "tr_1" and f["ref"] == "c1" and isinstance(f["at"], float)


@pytest.mark.parametrize("state,phase", [("dancing", "start"), ("tool", "wiggle"), ("", "start")])
def test_unknown_states_and_phases_are_refused(state, phase):
    q = approval_feed.subscribe()
    assert presence.emit(state, phase) is False
    assert _drain(q) == []


@pytest.mark.parametrize("field,value", [
    ("turn", "what the user said about their divorce"),
    ("ref", "search_files C:/Users/someone/secret.txt"),
    ("turn", "x" * 65),
    ("route", "https://api.example.com"),
    ("n", "4"),
    ("n", -1),
    ("of", 1.5),
])
def test_nothing_but_counts_and_opaque_ids_can_leave_in_a_frame(field, value):
    q = approval_feed.subscribe()
    presence.emit("tool", "start", **{field: value})
    frames = _drain(q)
    assert len(frames) == 1
    assert field not in frames[0]


def test_a_frame_has_only_allowlisted_keys():
    q = approval_feed.subscribe()
    presence.emit("round", "step", turn="tr_9", n=3, of=None, route="cloud",
                  ok=True, label="Thinking hard about secrets", args={"q": "x"})
    (f,) = _drain(q)
    assert set(f) <= {"type", "state", "phase", "turn", "ref", "n", "of", "route", "ok", "at"}
    assert "label" not in f and "args" not in f
    assert f["n"] == 3 and f["route"] == "cloud" and f["ok"] is True


def test_a_page_that_falls_behind_drops_presence_but_keeps_its_cards():
    q = approval_feed.subscribe()
    approval_feed.card_pending({"approval_id": "a1", "status": "pending"})
    for i in range(approval_feed.QUEUE_MAX * 2):
        presence.emit("tool", "start", ref="c%d" % i)
    frames = _drain(q)
    assert frames[0]["type"] == "pending"               # the card survived
    assert not any(f.get("type") == "resync" for f in frames)
    assert len(frames) == approval_feed.QUEUE_MAX


def test_a_card_published_behind_a_full_queue_still_forces_a_resync():
    q = approval_feed.subscribe()
    for i in range(approval_feed.QUEUE_MAX):
        presence.emit("tool", "start", ref="c%d" % i)
    approval_feed.card_pending({"approval_id": "a2", "status": "pending"})
    frames = _drain(q)
    assert frames == [{"type": "resync"}]


def test_emit_never_raises(monkeypatch):
    monkeypatch.setattr(approval_feed, "publish", lambda *a, **k: 1 / 0)
    assert presence.emit("tool", "start") is False


def test_tool_calls_pair_start_and_end_by_call_first_in_first_out():
    q = approval_feed.subscribe()
    presence.tool_started("search_files", turn="tr_1")
    presence.tool_started("search_files", turn="tr_1")
    presence.tool_finished("search_files", ok=True, turn="tr_1")
    presence.tool_finished("search_files", ok=False, turn="tr_1")
    f = _drain(q)
    assert [x["phase"] for x in f] == ["start", "start", "end", "end"]
    assert f[0]["ref"] != f[1]["ref"]
    assert f[2]["ref"] == f[0]["ref"] and f[3]["ref"] == f[1]["ref"]
    assert f[2]["ok"] is True and f[3]["ok"] is False
    # the tool's name never leaves
    assert all("search_files" not in str(x) for x in f)


def test_a_tool_that_finishes_without_a_start_still_ends_once():
    q = approval_feed.subscribe()
    presence.tool_finished("denied_tool", ok=False, turn="tr_2")
    (f,) = _drain(q)
    assert f["state"] == "tool" and f["phase"] == "end" and f["ok"] is False


def test_progress_is_sent_only_when_the_fraction_really_moves():
    q = approval_feed.subscribe()
    for p in (0.0, 0.001, 0.004, 0.2, 0.2, 0.205, 0.5):
        presence.progress("img-1", p)
    f = _drain(q)
    assert [x["n"] for x in f] == [0, 20, 50]
    assert all(x["of"] == 100 for x in f)
    assert len({x["ref"] for x in f}) == 1 and "img-1" not in f[0]["ref"]
