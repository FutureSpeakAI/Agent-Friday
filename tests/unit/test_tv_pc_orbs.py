"""The PC's live process orbs, as an owner's phone or tablet gets them (PW-G11).

Stephen, 2026-11-17: "the phone app lacks our process orbs indicating subagent processes underway. I want to be
able to multitask with the phone app just like I can with the desktop app." The desktop draws an orb for each
running task from /api/processes. These tests hold what the phone is sent from that same feed
(tv_gateway/pc_orbs.py):

* an orb exists only while its process runs: every finished status leaves, and an orb has no "done" state;
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
            "last_event_at", "approval_id", "can_stop", "can_stop_after_step", "origin"}


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


def test_every_finished_status_leaves_and_nothing_finished_is_ever_listed(client, desk):
    finished = sorted(set(ORB_TERMINAL_STATES) | {"done", "complete", "completed"})
    desk.processes = [proc("run")] + [proc("end-" + s, s) for s in finished]
    assert [o["id"] for o in orbs(client)] == ["run"]


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
