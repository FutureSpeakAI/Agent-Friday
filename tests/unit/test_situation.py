"""The live situation (services/situation): answered from state the server
already holds, in well under a second, and never by probing.

A question like "what's running right now?" arrives by voice as often as by
text, and the voice bridge gives a tool twenty seconds in all. So the snapshot
may read module state, take a lock and copy, or use a cached value that is
refreshed behind the request; it may not replan residency, walk the reasoning
traces to disk, or wait on a slow source.
"""
import threading
import time
from types import SimpleNamespace

import pytest

from agent_friday.services import desktop_bus, situation


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    desktop_bus.reset()
    monkeypatch.setitem(situation._PINS, "loaded", False)
    monkeypatch.setitem(situation._PINS, "ids", {})
    yield
    desktop_bus.reset()


def _slow(*a, **k):
    time.sleep(2.0)
    return None


def _forbidden(*a, **k):
    raise AssertionError("the situation snapshot called a slow probe")


@pytest.fixture
def arbiter(monkeypatch):
    from agent_friday.services import residency_arbiter as ra
    fake = SimpleNamespace(
        plan={"seats": {"reasoning": {"model_id": "Bonsai2", "status": "loaded"}}},
        planned_at=time.time() - 12, state="steady",
        lease={"kind": "image", "role": "image", "model_id": "flux"},
        llama=SimpleNamespace(procs={"Bonsai2": object()}),
        plan_fresh=_forbidden, status=_forbidden)
    monkeypatch.setattr(ra, "ARBITER", fake)
    return fake


def test_the_snapshot_never_waits_on_a_slow_source(monkeypatch, arbiter):
    from agent_friday.services import cost_meter, reasoning_trace, scheduler, swr_cache
    situation.snapshot()                 # the server has these modules loaded at boot
    swr_cache.invalidate("")             # and nothing cached: a cold read
    monkeypatch.setattr(scheduler, "list_schedules", _slow)
    monkeypatch.setattr(cost_meter, "_rolling_spend", _slow)
    monkeypatch.setattr(reasoning_trace, "snapshot", _forbidden)
    t0 = time.time()
    snap = situation.snapshot()
    took = time.time() - t0
    assert took < 1.0, "the situation took %.2fs with slow sources behind it" % took
    for name in ("desktop", "machine", "models", "activity", "spend"):
        assert name in snap and "error" not in snap[name], (name, snap[name])
    assert snap["spend"].get("today_usd") is None, "a spend that was not ready is not reported as a number"


def test_models_come_from_the_arbiters_last_plan(arbiter):
    m = situation.snapshot()["models"]
    assert m["loaded"] == ["Bonsai2"]
    assert m["planned"] == {"reasoning": {"model": "Bonsai2", "status": "loaded"}}
    assert m["arbiter_state"] == "steady" and 11 <= m["plan_age_s"] <= 15
    assert m["lease"] == {"kind": "image", "role": "image", "model": "flux"}


def test_running_work_and_the_queue_are_counted(monkeypatch, arbiter):
    from agent_friday import core
    from agent_friday.services import agent
    now = time.time()
    monkeypatch.setattr(agent, "TASKS", {
        "t1": {"task_id": "t1", "name": "Research the Loom project", "status": "running",
               "started": now - 90, "model": "opus"},
        "t2": {"task_id": "t2", "name": "Write the weekly", "status": "queued-for-seat",
               "started": now - 5},
        "t3": {"task_id": "t3", "name": "Old", "status": "done", "started": now - 999}})
    monkeypatch.setattr(core, "PROCESSES", {
        "p1": {"label": "Indexing the wiki", "category": "knowledge", "status": "running",
               "started": now - 30, "progress": 40}})
    core.turn_begin("turn-test", "conv-1")
    core.turn_pet(label="thinking", model="Bonsai2")
    try:
        a = situation.snapshot()["activity"]
        text = situation.brief()
    finally:
        core.turn_end("turn-test")
    assert a["tasks"]["counts"] == {"running": 1, "queued-for-seat": 1, "done": 1}
    assert a["queue_depth"] == 1
    assert [t["model"] for t in a["turns"]] == ["Bonsai2"]
    assert a["processes"][0]["label"] == "Indexing the wiki"
    assert "chat turn on Bonsai2" in text
    assert "1 task running (Research the Loom project)" in text
    assert "1 background job" in text and "1 waiting in the queue" in text


def test_a_finished_turns_thread_is_not_reported(monkeypatch):
    from agent_friday import core
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    monkeypatch.setitem(core._TURNS, "gone", {"thread": dead, "started": time.time(),
                                             "last_progress": time.time()})
    assert situation.snapshot()["activity"]["turns"] == []


def test_the_machine_line_reads_the_monitors_last_sample(monkeypatch):
    from agent_friday.services import machine_monitor as mm
    monkeypatch.setattr(mm, "last_sample", lambda: {"gpus": [
        {"name": "RTX", "total_mib": 24576, "used_mib": 5120, "util_pct": 30}]})
    monkeypatch.setattr(mm, "last_sample_age_s", lambda: 12.0)
    with situation._CPU_LOCK:
        situation._CPU.update(pct=17.5, at=time.time())
    snap = situation.snapshot()
    assert snap["machine"]["gpu"] == {"cards": [{"name": "RTX", "total_gb": 24.0, "used_gb": 5.0,
                                                 "util_pct": 30}], "age_s": 12.0}
    line = [x for x in situation.brief(snap).split("\n") if x.startswith("Machine:")][0]
    assert "CPU 17.5%" in line and "GPU 5.0 of 24.0 GB used, 30% busy" in line
    assert "RAM " in line and " GB free" in line


def test_the_desktop_line_says_what_is_in_front():
    desktop_bus.subscribe("desk1", "desktop")
    desktop_bus.report_state("desk1", {
        "kind": "desktop", "visible": False, "focused": False,
        "open": [{"workspace": "knowledge", "label": "Knowledge"},
                 {"workspace": "news", "label": "News"}],
        "focused_window": {"workspace": "knowledge", "label": "Knowledge",
                           "detail": "view=pages, path=concepts/bootstrap.md"},
        "chat": {"open": True}})
    line = [x for x in situation.brief().split("\n") if x.startswith("Desktop:")][0]
    assert line == ("Desktop: Knowledge focused (view=pages, path=concepts/bootstrap.md); "
                    "also open: News; chat open; the Friday window is minimized or covered.")


def test_with_no_page_the_desktop_line_says_so():
    line = [x for x in situation.brief().split("\n") if x.startswith("Desktop:")][0]
    assert "not reported yet" in line


def test_a_tab_only_desktop_says_nothing_can_be_opened():
    desktop_bus.report_state("tab1", {"kind": "tab", "focused": True,
                                      "open": [{"workspace": "messages", "label": "Messages"}],
                                      "focused_window": {"workspace": "messages", "label": "Messages"}})
    line = [x for x in situation.brief().split("\n") if x.startswith("A workspace tab:")][0]
    assert "no desktop page is connected" in line


def test_a_pin_is_kept_on_disk_and_read_live(tmp_path):
    assert situation.pinned_block("conv-9") == ""
    situation.set_pinned("conv-9", True)
    situation._PINS["loaded"] = False          # as after a restart
    assert situation.is_pinned("conv-9")
    block = situation.pinned_block("conv-9")
    assert block.startswith("\n\n== SITUATION (pinned; live, read at the start of this turn) ==\n")
    assert "Situation at " in block
    situation.set_pinned("conv-9", False)
    assert situation.pinned_block("conv-9") == ""
    assert not situation.is_pinned(None)


def test_compact_drops_what_is_empty_and_trims_lists():
    out = situation.compact({"a": None, "b": "", "c": [], "d": {}, "e": {"f": 0, "g": None},
                             "h": list(range(20))})
    assert out == {"e": {"f": 0}, "h": list(range(8))}


def test_a_failing_section_costs_only_that_section(monkeypatch):
    monkeypatch.setattr(situation, "SECTIONS", situation.SECTIONS + (
        ("broken", lambda now: 1 / 0),))
    snap = situation.snapshot()
    assert snap["broken"] == {"error": "ZeroDivisionError: division by zero"}
    assert "error" not in snap["machine"]
