"""The PC's live process orbs, as an owner's phone or tablet gets them (PW-G11).

Stephen, 2026-11-17: "the phone app lacks our process orbs indicating subagent processes underway. I want to be
able to multitask with the phone app just like I can with the desktop app." The desktop draws an orb for each
running task from /api/processes. These tests hold what the phone is sent from that same feed
(tv_gateway/pc_orbs.py):

* the phone gets exactly the orbs the desktop draws (window.fridaySyncOrbs: ``orb_visible`` and not ``dismissed``):
  running ones, and a finished or failed one while the desktop still shows it, with how it ended (Stephen 2026-11-22:
  "They do not look or function like the process orbs on our Friday desktop. Why?": the feed dropped every finished
  row the desktop still drew, and the fields the desktop draws from);
* progress, category, icon and steps are the PC's own values; a step is a tool's name and status only;
* the label is the PC's own model value; a voice turn that calls five tools is still one orb;
* a tool's arguments and results never leave the PC, and no string carries a URL or a host path;
* a PC that cannot answer is a 503, never an empty list that reads as "nothing is running";
* the stream starts with a snapshot, then sends only changes, says why an orb left, and ends when the device
  loses its right.
"""
import json

import pytest

from agent_friday.routes.tasks import ORB_TERMINAL_STATES
from agent_friday.tv_gateway import pc_orbs, pc_read
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean

ORB_KEYS = {"id", "job_id", "parent_id", "kind", "title", "stage", "state", "model", "started_at",
            "last_event_at", "approval_id", "can_stop", "can_stop_after_step", "origin",
            "status", "progress", "category", "icon", "failed", "steps", "step_n", "step_total", "trace_id", "ended_at"}


class Desk:
    def __init__(self):
        self.processes, self.tasks, self.down = [], [], set()

    def __call__(self, path, params=None):
        if path in self.down:
            return 500, b"{}", "application/json"
        body = {"/api/processes": {"processes": self.processes}, "/api/tasks": {"tasks": self.tasks}}.get(path)
        if body is None:
            return 404, b"{}", "application/json"
        return 200, json.dumps(body).encode(), "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


def proc(pid="p1", status="running", **extra):
    return {"id": pid, "name": "Task", "label": "Draft the plan", "model": "bonsai2:27b", "status": status,
            "orb_visible": True, "started": 1000, "updated": 1010, "steps": [], "stop_capability": "stop_everything",
            **extra}


def orbs(client, dev=None):
    r = get(client, dev or paired(), "/tv/v1/orbs")
    assert r.status_code == 200
    return r.get_json()["orbs"]


def test_a_running_process_is_one_orb_with_the_pcs_own_model_label(client, desk):
    desk.processes = [proc(model="gpt-5.5")]
    [orb] = orbs(client)
    assert set(orb) == ORB_KEYS and orb["model"] == {"label": "gpt-5.5", "local": False}
    assert orb["title"] == "Draft the plan" and orb["state"] == "running" and orb["origin"] == "pc"
    assert orb["started_at"] == 1000 and orb["last_event_at"] == 1010


def test_the_phone_gets_exactly_the_rows_the_desktop_draws(client, desk):
    # the desktop keeps a finished orb on screen for ORB_VISIBLE_AFTER_DONE_S and a failed one with a warning; the server
    # says which by orb_visible, and the phone draws the same set
    finished = sorted(set(ORB_TERMINAL_STATES) | {"done", "complete", "completed"})
    desk.processes = ([proc("run")] + [proc("end-" + s, s) for s in finished]
                      + [proc("gone-" + s, s, orb_visible=False) for s in finished] + [proc("bye", "completed", dismissed=True)])
    got = {o["id"]: o for o in orbs(client)}
    assert set(got) == {"run"} | {"end-" + s for s in finished}
    assert got["run"]["state"] == "running" and got["run"]["can_stop"]
    for s in finished:
        assert got["end-" + s]["state"] in ("done", "failed", "stopped") and not got["end-" + s]["can_stop"]
    assert got["end-completed"]["state"] == "done" and got["end-cancelled"]["state"] == "stopped"
    # the desktop marks a cancel orb_failed too (its warning stays): still "stopped", never "failed"
    desk.processes = [proc("x", "cancelled", orb_failed=True), proc("y", "interrupted", orb_failed=True), proc("z", "error", orb_failed=True)]
    got = {o["id"]: o for o in orbs(client)}
    assert [got[k]["state"] for k in "xyz"] == ["stopped", "stopped", "failed"] and got["x"]["icon"] == "\u26a0"


def test_the_orb_carries_what_the_desktop_draws_it_from(client, desk):
    desk.processes = [proc("a", progress=0.42, category="search", icon="\U0001f50d", trace_id="tr_c380a403",
                           steps=[{"name": "search_web", "status": "done", "args": "SECRET"}, {"name": "read_page"}],
                           step_n=2, step_total=5),
                      proc("b", progress=None, category="weird-thing"),
                      proc("c", "failed", orb_failed=True, trace_id="../../etc"),
                      proc("d", progress=7)]
    got = {o["id"]: o for o in orbs(client)}
    a = got["a"]
    assert a["progress"] == 0.42 and a["category"] == "search" and a["icon"] == "\U0001f50d" and a["trace_id"] == "tr_c380a403"
    assert a["steps"] == [{"name": "search_web", "status": "done"}, {"name": "read_page", "status": None}]
    assert (a["step_n"], a["step_total"]) == (2, 5) and a["failed"] is False and a["ended_at"] is None
    assert got["b"]["progress"] is None and got["b"]["category"] == "default"       # unknown: an arc, never a made-up amount
    assert got["c"]["state"] == "failed" and got["c"]["failed"] and got["c"]["icon"] == "\u26a0" and got["c"]["trace_id"] is None
    assert got["d"]["progress"] == 1.0


def test_the_live_desktop_row_shape_reads(client, desk):
    # a row exactly as the running desktop answered /api/processes (2026-11-22), finished and not dismissed
    row = {"category": "monitoring", "color": None, "cost_usd": 0.0, "dismissed": False, "elapsed": 29, "ended": 1795338571.45,
           "eta_s": None, "goal_id": None, "icon": "\U0001f9e0", "id": "openai-d8e3ec39", "label": "Reasoning (step 2)",
           "log": ["model: bonsai2:27b via arbiter-local", "intent: OK."], "milestone_id": None, "model": "bonsai2:27b",
           "name": "Cloud Inference", "off_record": False, "orb_failed": False, "orb_visible": True, "principal": "owner",
           "progress": 1.0, "receipt_id": None, "result": "You said", "schedule_id": None, "started": 1795338541.51,
           "status": "completed", "step_n": 2, "step_total": None, "steps": [], "stop_capability": "stop_everything",
           "task_id": None, "trace_id": "tr_c380a403e8b048dd", "updated": 1795338571.52}
    desk.processes = [row]
    [o] = orbs(client)
    assert o["state"] == "done" and o["progress"] == 1.0 and o["model"]["label"] == "bonsai2:27b" and o["ended_at"] == 1795338571
    assert "You said" not in json.dumps(o) and "intent" not in json.dumps(o)


def test_a_dismissed_or_hidden_process_is_not_an_orb(client, desk):
    desk.processes = [proc("a", dismissed=True), proc("b", orb_visible=False), proc("c")]
    assert [o["id"] for o in orbs(client)] == ["c"]


def test_a_voice_turn_with_five_tool_calls_is_one_orb(client, desk):
    steps = [{"type": "tool", "name": "tool%d" % i, "args": "x", "result": "y"} for i in range(5)]
    desk.processes = [proc("voice-1", steps=steps)]
    assert len(orbs(client)) == 1


def test_the_stage_is_a_tools_name_and_its_arguments_and_results_never_leave(client, desk):
    steps = [{"type": "tool", "name": "search_web", "args": "SECRET ARGS", "result": "SECRET RESULT"}]
    desk.processes = [proc(steps=steps, log=["SECRET LOG"], result="SECRET FINAL", prompt="SECRET PROMPT")]
    body = get(client, paired(), "/tv/v1/orbs")
    assert body.get_json()["orbs"][0]["stage"] == "using search_web"
    assert "SECRET" not in body.get_data(as_text=True)


def test_no_string_carries_a_url_or_a_host_path(client, desk):
    desk.processes = [proc(label="Read https://example.org/a?q=1 and C:\\Users\\me\\notes.txt now",
                           model="local at /home/me/models/x.gguf",
                           steps=[{"type": "tool", "name": "open ~/secret/file"}], receipt_id=None)]
    [orb] = orbs(client)
    text = json.dumps(orb)
    for leak in ("http", "example.org", "C:\\", "Users", "/home", "models/", "~/", "secret"):
        assert leak not in text, leak
    assert orb["title"] == "Read and now"


def test_a_tasks_own_state_decides_waiting_stopping_and_stalled(client, desk):
    desk.processes = [proc("a", task_id="ta"), proc("b", task_id="tb"), proc("c", task_id="tc"), proc("d", task_id="td")]
    desk.tasks = [{"task_id": "ta", "state": "waiting_for_approval", "now": "waiting"},
                  {"task_id": "tb", "state": "cancel_requested"},
                  {"task_id": "tc", "state": "running", "stalled": True},
                  {"task_id": "td", "state": "running", "now": "reading sources"}]
    got = {o["id"]: o for o in orbs(client)}
    assert [got[k]["state"] for k in "abcd"] == ["waiting_on_you", "stopping", "stalled", "running"]
    assert got["d"]["stage"] == "reading sources" and got["d"]["job_id"] == "td" and got["d"]["kind"] == "task"


def test_a_tasks_list_that_is_down_still_shows_the_orbs_as_running(client, desk):
    desk.processes = [proc("a", task_id="ta")]
    desk.down.add("/api/tasks")
    assert [(o["id"], o["state"]) for o in orbs(client)] == [("a", "running")]


def test_stop_buttons_follow_the_pcs_own_stop_capability(client, desk):
    desk.processes = [proc("a", stop_capability="after_this_step"), proc("b", stop_capability="cancel_now"),
                      proc("c", stop_capability="nothing")]
    got = {o["id"]: (o["can_stop"], o["can_stop_after_step"]) for o in orbs(client)}
    assert got == {"a": (True, True), "b": (True, False), "c": (False, False)}


def test_a_pc_that_cannot_answer_is_a_503_not_an_empty_list(client, desk):
    desk.down.add("/api/processes")
    r = get(client, paired(), "/tv/v1/orbs")
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable" and "orbs" not in r.get_json()


def test_only_an_owner_phone_or_tablet_may_read_the_orbs(client, desk):
    desk.processes = [proc()]
    assert get(client, paired("tv"), "/tv/v1/orbs").status_code == 403
    assert get(client, paired(right=False), "/tv/v1/orbs").status_code == 403


def test_the_list_is_capped(client, desk):
    desk.processes = [proc("p%d" % i) for i in range(pc_orbs.MAX_ORBS + 20)]
    assert len(orbs(client)) == pc_orbs.MAX_ORBS


def live(*ids):
    desk = Desk()
    desk.processes = [proc(i) for i in ids]
    return desk


def test_changes_add_change_and_remove_with_the_reason_it_left(monkeypatch):
    monkeypatch.setattr(pc_read, "_desk", live("a", "b", "c", "d"))
    before, _ = pc_orbs.read()
    after = {k: dict(v) for k, v in before.items() if k != "a"}
    after["b"]["stage"] = "using search"
    after["e"] = dict(before["a"], id="e")
    ended = {"a": proc("a", "completed", receipt_id="r1"), "c": proc("c", "cancelled"), "d": proc("d", "error")}
    after.pop("c"), after.pop("d")
    got = {(event, data["id"]): data for event, data in pc_orbs.changes(before, after, ended)}
    assert set(got) == {("orb.changed", "b"), ("orb.added", "e"), ("orb.removed", "a"), ("orb.removed", "c"),
                        ("orb.removed", "d")}
    assert [got[("orb.removed", k)]["outcome"] for k in "acd"] == ["done", "stopped", "failed"]
    assert got[("orb.removed", "a")]["receipt_id"] == "r1" and got[("orb.removed", "a")]["job_id"] is None


def test_an_orb_that_vanished_with_no_record_is_stopped_not_done():
    before = {"x": {"id": "x", "job_id": "t"}}
    [(event, data)] = pc_orbs.changes(before, {}, {})
    assert event == "orb.removed" and data["outcome"] == "stopped" and data["job_id"] == "t"


def test_nothing_changed_sends_nothing():
    orb = {"id": "a", "job_id": None}
    assert pc_orbs.changes({"a": orb}, {"a": dict(orb)}, {}) == []


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run_stream(feeds, allowed=lambda: True, max_s=30.0, monkeypatch=None):
    """Frames from one stream over a scripted sequence of (live, ended) answers; the stream ends with the script."""
    clock, script, frames = Clock(), iter(feeds), []
    monkeypatch.setattr(pc_orbs, "MAX_STREAM_S", max_s)
    monkeypatch.setattr(pc_orbs, "_still_allowed", lambda dev: allowed())

    def read():
        try:
            return next(script)
        except StopIteration:
            raise pc_orbs.Unavailable("end of script")
    monkeypatch.setattr(pc_orbs, "read", read)
    frames.extend(pc_orbs._events({}, now=clock.now, sleep=clock.sleep))
    return frames


def events_of(frames):
    return [f.split("\n")[1 if f.startswith("id:") else 0].removeprefix("event: ") for f in frames]


def test_a_stream_starts_with_a_heartbeat_and_a_snapshot_then_sends_only_changes(monkeypatch):
    a, b = {"id": "a", "job_id": None}, {"id": "b", "job_id": None}
    frames = run_stream([({"a": a}, {}), ({"a": a}, {}), ({"a": a, "b": b}, {}), ({"b": b}, {"a": proc("a", "completed")})],
                        monkeypatch=monkeypatch)
    assert events_of(frames) == ["heartbeat", "orb.snapshot", "orb.added", "orb.removed", "heartbeat"]
    ids = [int(f.split("\n")[0][4:]) for f in frames if f.startswith("id:")]
    assert ids == sorted(set(ids)) and ids[0] == 1


def test_a_stream_ends_when_the_pc_cannot_answer(monkeypatch):
    assert events_of(run_stream([({"a": {"id": "a", "job_id": None}}, {})], monkeypatch=monkeypatch)) == [
        "heartbeat", "orb.snapshot"]


def test_a_stream_ends_at_the_heartbeat_when_the_device_loses_its_right(monkeypatch):
    answers = iter([True, False])
    feeds = [({}, {})] * 10
    frames = run_stream(feeds, allowed=lambda: next(answers), monkeypatch=monkeypatch)
    assert events_of(frames).count("heartbeat") == 1 and events_of(frames)[-1] == "orb.snapshot"


def test_a_stream_sends_a_heartbeat_every_few_seconds_and_stops_at_its_lifetime(monkeypatch):
    frames = run_stream([({}, {})] * 100, max_s=13.0, monkeypatch=monkeypatch)
    assert events_of(frames).count("heartbeat") == 3                  # at 0, 6 and 12 seconds (the poll steps 1.5 s)


def test_the_route_streams_events_and_refuses_a_device_that_may_not_read(client, desk):
    desk.processes = [proc("a")]
    assert get(client, paired("tv"), "/tv/v1/orbs/events").status_code == 403
    r = get(client, paired(), "/tv/v1/orbs/events")
    assert r.status_code == 200 and r.mimetype == "text/event-stream"
    first = next(iter(r.response))
    r.close()
    assert b"event: heartbeat" in first


# ── stop, as the desktop orb's Cancel does ──────────────────────────────────────────────────────────────────

class Posts:
    def __init__(self):
        self.calls, self.answer = [], (200, {"ok": True, "stopped": ["the job"]})

    def __call__(self, path, body=None):
        self.calls.append(path)
        return self.answer


@pytest.fixture
def posts(monkeypatch):
    from agent_friday.tv_gateway import pc_act
    p = Posts()
    monkeypatch.setattr(pc_act, "_desk_post", p)
    return p


def controller():
    from agent_friday.services import device_pairing
    dev = paired()
    device_pairing.set_right(dev.id, "pc_act", True)
    return dev


def stop(client, dev, orb_id):
    from tests.unit.test_tv_gateway import auth, open_session
    return client.post("/tv/v1/orbs/%s/stop" % orb_id, headers=auth(open_session(client, dev)))


def test_stop_does_what_the_desktop_cancel_does(client, desk, posts):
    desk.processes = [proc("helper", task_id="t-9", stop_capability="after_this_step"), proc("image-1")]
    dev = controller()
    r = stop(client, dev, "helper")
    assert r.status_code == 200 and r.get_json()["how"] == "after_this_step"
    r = stop(client, dev, "image-1")
    assert r.status_code == 200 and r.get_json()["how"] == "cancelled" and r.get_json()["stopped"] == ["the job"]
    assert posts.calls == ["/api/tasks/t-9/stop-after-step", "/api/processes/image-1/cancel"]


def test_stop_says_when_nothing_was_stopped(client, desk, posts):
    desk.processes = [proc("done-1", "completed"), proc("t", task_id="t-1")]
    dev = controller()
    assert stop(client, dev, "done-1").status_code == 409
    assert stop(client, dev, "nope").status_code == 404
    posts.answer = (409, {"ok": False, "status": "paused"})
    r = stop(client, dev, "t")
    assert r.status_code == 409 and r.get_json()["code"] == "not_running"


def test_stop_needs_the_right_to_control_the_pc(client, desk, posts):
    desk.processes = [proc("a")]
    assert stop(client, paired(), "a").status_code == 403          # pc_read alone reads orbs; it never stops one
    assert stop(client, paired("tv"), "a").status_code == 403
    assert posts.calls == []


def test_stop_refuses_a_bad_id_and_never_answers_with_a_bare_500(client, desk, posts, monkeypatch):
    desk.processes = [proc("a")]
    dev = controller()
    assert stop(client, dev, ".hidden").status_code in (400, 404) and posts.calls == []

    def boom(path, body=None):
        raise RuntimeError("desktop fell over")
    from agent_friday.tv_gateway import pc_act
    monkeypatch.setattr(pc_act, "_desk_post", boom)
    r = stop(client, dev, "a")
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable"


def test_every_stop_attempt_leaves_a_receipt_with_its_outcome(client, desk, posts, monkeypatch):
    from agent_friday.services import device_pairing
    seen = []
    monkeypatch.setattr(device_pairing, "_receipt", lambda kind, dev, **kw: seen.append((kind, kw)))
    desk.processes = [proc("a"), proc("done-1", "completed")]
    dev = controller()
    stop(client, dev, "a")
    stop(client, dev, "done-1")
    stops = [kw for kind, kw in seen if kind == "device_orb_stop"]
    assert [(s["orb"], s["outcome"]) for s in stops] == [("a", "stopped"), ("done-1", "not_running")]
