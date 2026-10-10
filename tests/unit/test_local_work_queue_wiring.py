"""The Local AI queue, wired: every background local-model caller goes through
the one door (agent._spawn_task -> seat_supervisor), and the user can see it.

Callers covered here: the voice-session wiki distillation, runner= tasks,
scheduled builtins (admitted at their first local model call), the chip's API,
the Settings row, the tray tooltip and the voice tool. A fake worker stands in
for the agent loop; nothing reaches a model.
"""
import inspect
import pathlib
import threading
import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import seat_supervisor as ss
from agent_friday.services import voice_engine as ve

try:
    from agent_friday.services import background_gate as bg
except ImportError:  # the base this guards against has no gate at all
    bg = None

# The tree the code under test was imported from (src/agent_friday/services/agent.py).
ROOT = pathlib.Path(ag.__file__).resolve().parents[3]
DISTILL = "Voice session: distill to wiki"


class Signals:
    def __init__(self, monkeypatch):
        self.os_idle = self.friday_idle = 0.0
        if bg is not None:
            monkeypatch.setattr(bg, "OS_IDLE_READER", lambda: self.os_idle)
            monkeypatch.setattr(bg, "friday_idle_seconds", lambda: self.friday_idle)
            monkeypatch.setattr(bg, "idle_threshold_s", lambda: 600.0)
            monkeypatch.setattr(bg, "_touch_friday_activity", lambda: None)
            bg._INTERACTIVE.clear()

    def away(self):
        self.os_idle = self.friday_idle = 7200.0


class Worker:
    """Stands in for _task_worker: records the start, makes one local model
    call through the choke point, waits to be released, then ends the way
    the real worker's finally block does."""

    def __init__(self):
        self.started = []
        self.prompts = {}
        self.release = {}

    def __call__(self, task_id, name, prompt, description='', **_kw):
        self.started.append(task_id)
        self.prompts[task_id] = prompt
        ev = self.release.setdefault(task_id, threading.Event())
        if bg is not None:
            bg.before_local_model_call("bonsai2:27b")
        ev.wait(5)
        with ag.TASKS_LOCK:
            if task_id in ag.TASKS:
                ag.TASKS[task_id]["status"] = "complete"
        ag._seat_supervisor().on_task_end(task_id)

    def finish(self, task_id):
        self.release.setdefault(task_id, threading.Event()).set()


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


@pytest.fixture
def wired(monkeypatch):
    sig = Signals(monkeypatch)
    kw = dict(thread_factory=ag._start_pending_task_thread)
    params = inspect.signature(ss.SeatSupervisor).parameters
    if bg is not None and "gate" in params:
        kw.update(gate=bg.block_reason, poll_seconds=0.005,
                  sleep=lambda _s: time.sleep(0.005),
                  brain_resolver=lambda: "bonsai2:27b")
    sup = ss.SeatSupervisor(**kw)
    monkeypatch.setattr(ag, "_SEAT_SUPERVISOR", sup)
    monkeypatch.setattr(ag, "_admission_seat_for",
                        lambda model: ("cloud/" + model) if model and ":" not in model
                        else "local/" + (model or "default"))
    worker = Worker()
    monkeypatch.setattr(ag, "_task_worker", worker)
    before = set(ag.TASKS)
    yield sig, sup, worker
    for tid in list(worker.release):
        worker.finish(tid)
    for tid in set(ag.TASKS) - before:
        th = ag.TASK_THREADS.get(tid)
        if th is not None and th.is_alive():
            th.join(2)


def _new_tasks(name, before):
    with ag.TASKS_LOCK:
        return [t for tid, t in ag.TASKS.items() if tid not in before and t.get("name") == name]


# ── the voice-session distillation ───────────────────────────────────────────

def test_four_distill_triggers_for_one_session_make_one_job_that_waits_for_idle(wired):
    sig, sup, worker = wired
    before = set(ag.TASKS)
    first = ("what's on today", "Three meetings.")
    threads = [threading.Thread(target=ve._spawn_voice_distill_unchecked,
                                args=([first] + [("u%d" % k, "a%d" % k) for k in range(n + 1)],))
               for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    jobs = _new_tasks(DISTILL, before)
    assert len(jobs) == 1, "four triggers made %d distill jobs" % len(jobs)
    assert worker.started == [], "the distillation started while the user was active"
    sup.pump()
    assert worker.started == []
    sig.away()
    sup.pump()
    assert _wait(lambda: worker.started == [jobs[0]["task_id"]])
    assert "u3" in worker.prompts[jobs[0]["task_id"]] or "u2" in worker.prompts[jobs[0]["task_id"]]


def test_distills_for_four_sessions_run_one_at_a_time(wired):
    sig, sup, worker = wired
    sig.away()
    before = set(ag.TASKS)
    for n in range(4):
        ve._spawn_voice_distill_unchecked([("session %d" % n, "ok"), ("more", "fine")])
    jobs = _new_tasks(DISTILL, before)
    assert len(jobs) == 4
    assert _wait(lambda: len(worker.started) == 1)
    time.sleep(0.1)
    assert len(worker.started) == 1, "two background jobs ran on the local seat at once"
    for n in range(4):
        worker.finish(worker.started[n])
        if n < 3:
            assert _wait(lambda: len(worker.started) == n + 2)
            time.sleep(0.05)
            assert len(worker.started) == n + 2


# ── runner= tasks and scheduled builtins ─────────────────────────────────────

def test_runner_tasks_on_a_local_seat_are_admitted_and_cloud_ones_are_not(wired):
    sig, sup, worker = wired
    sig.away()
    blocker = ag._spawn_task("Blocker", "x", model="bonsai2:27b")
    assert _wait(lambda: worker.started == [blocker])
    ran = []
    local = ag._spawn_task("Research", "q", model="bonsai2:27b",
                           runner=lambda tid: ran.append(tid) or {"status": "complete"})
    cloud = ag._spawn_task("Research", "q", model="claude-sonnet-4",
                           runner=lambda tid: ran.append(tid) or {"status": "complete"})
    assert _wait(lambda: cloud in ran)
    time.sleep(0.1)
    assert local not in ran, "a local runner task ran beside a running local job"
    assert ag.TASKS[local]["status"] == "queued-for-seat"
    worker.finish(blocker)
    assert _wait(lambda: local in ran)


def test_a_scheduled_builtin_on_a_local_seat_waits_its_turn(wired, monkeypatch):
    from agent_friday.services import scheduler
    sig, sup, worker = wired
    sig.away()
    blocker = ag._spawn_task("Blocker", "x", model="bonsai2:27b")
    assert _wait(lambda: worker.started == [blocker])
    called = []

    def edition():           # a builtin whose model call goes to the local seat
        if bg is not None:
            bg.before_local_model_call("bonsai2:27b")
        called.append("local")
        return "ok"

    def chore():             # a builtin that never touches a model
        called.append("chore")
        return "ok"
    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "t_edition", {"fn": edition, "label": "Test edition"})
    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "t_chore", {"fn": chore, "label": "Test chore"})
    monkeypatch.setattr(scheduler, "TIME_BOUND_BUILTINS",
                        frozenset({"t_edition"}), raising=False)
    run = lambda ref: scheduler._run_task_inner(
        {"id": "sch_" + ref, "name": ref, "task": {"kind": "builtin", "ref": ref}})
    th = threading.Thread(target=run, args=("t_edition",), daemon=True)
    th.start()
    run("t_chore")
    assert called == ["chore"], "a builtin with no model call was held, or the local one ran"
    time.sleep(0.1)
    assert "local" not in called, "a scheduled local-model job overlapped the running job"
    worker.finish(blocker)
    th.join(3)
    assert called == ["chore", "local"]


# ── what the user sees ───────────────────────────────────────────────────────

def test_the_api_count_equals_the_queue_and_the_controls_work(wired):
    flask = pytest.importorskip("flask")
    from agent_friday.routes import work_plan
    sig, sup, worker = wired
    for n in range(3):
        ve._spawn_voice_distill_unchecked([("chat %d" % n, "ok")])
    app = flask.Flask(__name__)
    app.register_blueprint(work_plan.work_plan_bp)
    c = app.test_client()
    body = c.get("/api/local-queue").get_json()
    assert body["count"] == sup.snapshot()["count"] == 3
    assert [j["why"] for j in body["queued"]] == ["waits for idle"] * 3
    assert {j["label"] for j in body["queued"]} == {"wiki notes from today's voice chat"}
    first = body["queued"][0]["id"]
    assert c.post("/api/local-queue/%s/run-now" % first).status_code == 200
    assert _wait(lambda: worker.started == [first]), "run now did not skip the idle wait"
    last = body["queued"][2]["id"]
    out = c.post("/api/local-queue/%s/cancel" % last).get_json()
    assert out["ok"] and out["queue"]["count"] == 1
    assert ag.TASKS[last]["status"] == "cancelled"
    assert c.get("/api/work/queue").get_json()["local_queue"]["count"] == 1


def test_the_tray_tooltip_names_the_queue():
    snap = {"running": [], "queued": [
        {"label": "wiki notes from today's voice chat", "why": "waits for idle"},
        {"label": "a background task", "why": "waits for the local model"},
        {"label": "a background task", "why": "waits for the local model"}]}
    assert ss.tray_tooltip(snap) == (
        "Local AI: 3 tasks queued · next: wiki notes from today's voice chat · waits for idle")
    assert ss.tray_tooltip({"running": [], "queued": []}) is None
    assert ss.tray_tooltip({"running": [{"label": "x"}], "queued": []}) == "Local AI: running: x"
    long = {"running": [], "queued": [{"label": "y" * 300, "why": "waits for idle"}]}
    assert len(ss.tray_tooltip(long)) <= 127

    tray = pytest.importorskip("agent_friday.friday_tray")
    t = tray.FridayTray.__new__(tray.FridayTray)

    class Icon:
        title = tray.TRAY_TITLE
    t.icon, t.running = Icon(), True
    t._meeting_status = lambda: {"recording": False}
    t._local_queue = lambda: snap
    t._update_meeting_title()
    assert t.icon.title == ss.tray_tooltip(snap)


def _component(path):
    text = (ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("// ── The Local AI queue chip (top bar) ──")
    end = text.index("function StandaloneApprovals(", start)
    return text, text[start:end]


def test_the_chip_and_the_settings_row_are_in_both_ui_files_and_identical():
    index, a = _component("index.html")
    mirror, b = _component("ui_parts/app.html")
    assert a == b, "the chip behaves differently in the mirror"
    for needle in ("function LocalAiQueueChip()", "'/api/local-queue'", "'run-now'",
                   "'cancel'", "'Local AI: '", "if (!jobs.length) return null;",
                   "\"aria-expanded\"", "e.key === 'Escape'",
                   "Background AI work waits until you've been away for",
                   "' (default)'", "background_idle_minutes: Number(e.target.value)"):
        assert needle in a, needle
    assert "React.createElement(LocalAiQueueChip, null)" in index
    assert "<LocalAiQueueChip/>" in mirror
    for text in (index, mirror):
        assert "React.createElement(LocalAiIdleRow, {" in text
    css = (ROOT / "ui_parts/styles_and_scene.html").read_text(encoding="utf-8")
    for text in (index, css):
        assert ".local-ai-chip {" in text and ".local-ai-chip.working" in text
    # Brand: waiting is never amber, and nothing in the chip animates.
    chip_css = [l for l in index.splitlines() if ".local-ai-" in l]
    assert not any("245,158,11" in l or "f59e0b" in l or "animation" in l or "transition" in l
                   for l in chip_css)


# ── settings, the voice tool, the choke point ────────────────────────────────

def test_the_idle_setting_has_a_default_a_validator_and_a_reader():
    from agent_friday.core import DEFAULT_SETTINGS
    from agent_friday.routes import core_routes
    assert DEFAULT_SETTINGS["background_idle_minutes"] == 10
    check = core_routes._check_background_idle_minutes
    for good in (1, 5, 10, 60, 240):
        assert check({"background_idle_minutes": good}) is None
    for bad in (0, 241, "10", True, 2.5, None):
        assert check({"background_idle_minutes": bad}) is not None, bad
    assert check({}) is None
    assert bg.idle_minutes_setting({"background_idle_minutes": 30}) == 30


def test_queue_status_is_a_read_only_voice_and_text_tool():
    from agent_friday.governance import action_gate
    from agent_friday.services import laya_router
    assert ag.TOOL_RINGS["queue_status"] == 0
    assert "queue_status" in ag.CLAUDE_TOOL_HANDLERS
    assert "queue_status" in action_gate.INTERNAL_TOOLS
    assert "queue_status" in {t[0] for t in ve._VOICE_LIVE_TOOLS}
    c = ve.build_voice_tool_contract()
    assert "queue_status" in c["names"] and c["fits"]
    r = laya_router.route("What's in your queue?", log=False)
    assert r.tool == "queue_status"
    assert ag._tool_queue_status({}) in ("Nothing is waiting for the local AI.",) or \
        "queued" in ag._tool_queue_status({}) or "Running" in ag._tool_queue_status({})


def test_both_local_transports_pass_the_choke_point():
    src = (ROOT / "src/agent_friday/services/model_router.py").read_text(encoding="utf-8")
    assert src.count("before_local_model_call(model)") == 2
