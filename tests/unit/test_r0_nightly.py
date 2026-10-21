"""R0 runs only inside the nightly practice-trials job.

R0 is about 14 hours on the local brain seat. Built as a stand-alone script it
held the seat invisibly, never yielded to the owner and never checked mains
power. Pinned here, with a scripted agent and editor (no model):
  * the nightly job (a visible job) schedules R0 itself and runs it when
    nothing else is queued, to the end, writing the report;
  * nothing runs on battery;
  * an interactive turn mid task-repeat throws that task-repeat away (never
    stored as missing) and it runs again the next night;
  * every episode and editor call counts against tonight's cap, which stops R0;
  * an editor call takes the seat through the Local AI queue;
  * a model change between nights stops R0 instead of mixing two models;
  * no other path runs R0 (the old script now only reports its state).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TASKS = [{"id": f"t{i}", "domain": "mail", "prompt": "Draft a reply", "tags": []} for i in range(2)]
SEAT = {"endpoint": "http://127.0.0.1:1/v1", "model": "seat-model"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    friday = tmp_path / "friday"
    friday.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(friday))
    from agent_friday.services import trials
    monkeypatch.setattr(trials, "on_ac_power", lambda: True)
    from agent_friday.task_eval import pack as P
    monkeypatch.setattr(P, "load_pack", lambda name="dev": [dict(t) for t in TASKS])
    from agent_friday.task_eval import runner
    monkeypatch.setattr(runner, "scan_for_canary", lambda text, **k: {"hit": False})
    return friday


def _grade(passed=True):
    return {"status": "graded", "pass": passed, "score": 1.0 if passed else 0.0,
            "safety_fail": False, "checks": []}


class _Runs:
    """A scripted episode runner; ``trip_on`` trips the watcher during that call."""

    def __init__(self, watcher=None, trip_on=None):
        self.calls = 0
        self.watcher = watcher
        self.trip_on = trip_on

    def __call__(self, *, task, arm, repeat, overlay, out_dir, **kw):
        self.calls += 1
        if self.trip_on is not None and self.calls == self.trip_on:
            self.watcher.tripped.set()
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "episode.json"
        p.write_text(json.dumps({"task_id": task["id"], "final_text": f"reply {arm}",
                                 "proposed": [{"tool": "draft_email"}], "effects": [], "transcript": []}),
                     encoding="utf-8")
        return {"episode": str(p), "grade": _grade(), "outcome": "completed"}


def _editor(system, user):
    from agent_friday.task_eval import r0
    if system == r0.COMPOSER:
        return '{"keep_current": true, "why": "x"}'
    if system == r0.SELECTOR:
        return '{"pick": "x"}'
    return '{"sections": {}, "skill": null, "notes": "none"}'


class _Watcher:
    def __init__(self):
        self.tripped = threading.Event()


def _job(monkeypatch, runs):
    from agent_friday.services import lesson_records, trial_job, trials
    monkeypatch.setattr(trials, "weekly_digest", lambda *a, **k: None)          # a Sunday run
    monkeypatch.setattr(lesson_records, "raise_retirement_cards", lambda *a, **k: [])
    monkeypatch.setattr(trial_job, "_seat", lambda: dict(SEAT))
    monkeypatch.setattr(trial_job, "_gate_blocks", lambda: "")
    monkeypatch.setattr(trial_job, "seat_run_one", lambda seat, yf=None: runs)
    try:
        from agent_friday.services import r0_nightly
    except ImportError:          # a build without it: the job is judged on what it does
        r0_nightly = None
    if r0_nightly is not None:
        monkeypatch.setattr(r0_nightly, "_seat_chat", lambda seat, system, user: _editor(system, user))
    return trial_job.run_pending_trials.__wrapped__


def test_the_nightly_job_schedules_r0_itself_and_runs_it_to_the_end(home, monkeypatch):
    runs = _Runs()
    out = _job(monkeypatch, runs)()
    assert "r0" in out, out
    assert out["r0"]["complete"] is True and out["r0"]["cells"] == 4
    from agent_friday.services import r0_nightly
    st = r0_nightly.state()
    assert st["status"] == "done" and st["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE")
    assert (home / "trials" / "r0" / "report.md").exists()
    assert runs.calls == 4 * 8
    # done means done: the next night runs nothing
    again = _job(monkeypatch, _Runs())()
    assert "r0" not in again and again["skipped"] == "nothing to test"


def test_nothing_runs_on_battery(home, monkeypatch):
    from agent_friday.services import trials
    monkeypatch.setattr(trials, "on_ac_power", lambda: False)
    runs = _Runs()
    out = _job(monkeypatch, runs)()
    assert out["skipped"] == "on battery" and runs.calls == 0


def test_an_interactive_turn_discards_the_task_repeat_and_it_runs_again(home, monkeypatch):
    from agent_friday.services import r0_nightly
    monkeypatch.setattr(r0_nightly, "_seat_chat", lambda seat, system, user: _editor(system, user))
    r0_nightly.ensure_scheduled()
    w = _Watcher()
    first = r0_nightly.run_tonight(dict(SEAT), w, _Runs(w, trip_on=3))
    assert first["complete"] is False and first["cells"] == 0
    run_dir = Path(r0_nightly.state()["run_dir"])
    assert not list((run_dir / "cells").glob("*/cell.json")), "a preempted task-repeat was stored"
    assert not (run_dir / "cells" / "t0-r0").exists()
    second = r0_nightly.run_tonight(dict(SEAT), _Watcher(), _Runs())
    assert second["complete"] is True and second["cells"] == 4
    assert r0_nightly.state()["status"] == "done"


def test_every_step_counts_against_tonights_cap_and_the_cap_stops_r0(home, monkeypatch):
    from agent_friday.services import r0_nightly, trials
    used = {"s": 0.0}
    monkeypatch.setattr(trials, "_spend_night", lambda s: used.__setitem__("s", used["s"] + 600.0))
    monkeypatch.setattr(trials, "night_seconds_used", lambda now=None: used["s"])
    calls = {"editor": 0}

    def editor(seat, system, user):
        calls["editor"] += 1
        return _editor(system, user)
    monkeypatch.setattr(r0_nightly, "_seat_chat", editor)
    r0_nightly.ensure_scheduled()
    runs = _Runs()
    out = r0_nightly.run_tonight(dict(SEAT), _Watcher(), runs)
    steps = runs.calls + calls["editor"]
    assert out["complete"] is False
    assert steps == trials.NIGHT_CAP_SECONDS // 600, steps
    assert calls["editor"] > 0, "editor calls were not counted"


def test_an_editor_call_takes_the_seat_through_the_queue(home, monkeypatch):
    from agent_friday.services import background_gate, local_scheduler, r0_nightly
    seen = []

    class Grant:
        def release(self):
            seen.append("release")
    monkeypatch.setattr(background_gate, "before_local_model_call", lambda model: seen.append(("gate", model)))
    monkeypatch.setattr(local_scheduler, "admit", lambda kind, **kw: seen.append(("admit", kind, kw.get("model")))
                        or Grant())

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}
    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: seen.append(("post", url)) or Resp())
    assert r0_nightly._seat_chat(dict(SEAT), "s", "u") == "ok"
    assert seen[0] == ("gate", "seat-model") and seen[1][:2] == ("admit", "practice_trial")
    assert seen[2][0] == "post" and seen[-1] == "release"


def test_a_model_change_between_nights_stops_r0(home, monkeypatch):
    from agent_friday.services import r0_nightly
    monkeypatch.setattr(r0_nightly, "_seat_chat", lambda seat, system, user: _editor(system, user))
    r0_nightly.ensure_scheduled()
    w = _Watcher()
    r0_nightly.run_tonight(dict(SEAT), w, _Runs(w, trip_on=10))
    out = r0_nightly.run_tonight(dict(SEAT, model="another-model"), _Watcher(), _Runs())
    assert "stopped" in out and r0_nightly.state()["status"] == "stopped"
    assert r0_nightly.due() is False


def test_r0_schedules_itself_once_and_a_cancel_holds(home):
    from agent_friday.services import r0_nightly
    a = r0_nightly.ensure_scheduled(now=1.0)
    b = r0_nightly.ensure_scheduled(now=2.0)
    assert a["created"] == b["created"] == 1.0 and r0_nightly.due()
    r0_nightly.cancel()
    r0_nightly.ensure_scheduled()
    assert r0_nightly.due() is False


def test_no_other_path_runs_r0():
    tool = (ROOT / "tools" / "task_eval" / "r0.py").read_text(encoding="utf-8")
    for banned in ("r0.run(", "run_episode", "/chat/completions"):
        assert banned not in tool, f"tools/task_eval/r0.py still runs R0 itself ({banned})"
    callers = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py")
               if "r0.run(" in p.read_text(encoding="utf-8")]
    assert callers == ["src/agent_friday/services/r0_nightly.py"], callers
