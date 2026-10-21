"""R0 inside the nightly practice-trials job.

R0 (task_eval/r0) asks whether "edit the instructions, test, merge" beats the
best of four plain attempts on Friday's practice tasks. It is about 14 hours of
work on the local brain seat, so it never runs on its own: it is one more item
in the nightly practice-trials job (services/trial_job), which

* is a visible job: it shows on the desktop and in the Local AI queue, with a
  line for each task-repeat R0 finishes;
* takes the seat through the Local AI queue for every episode and for every
  editor, composer and selector call;
* runs only on AC power, with the PC idle, inside the shared 3-hour nightly
  testing cap (every step's seconds count against it);
* yields to the owner: a watcher drops a yield file the moment an interactive
  turn appears, the running episode stops, and the task-repeat it belonged to
  is thrown away (never stored as a missing result) and run again next night.

On a developer install (a source checkout of Friday, where R0's answer is
used) it schedules itself once (``ensure_scheduled``); an installed copy never
runs it unless ``schedule()`` is called. It runs after any queued trials
in whatever time is left, picks up where it stopped, and when every
task-repeat is done writes result.json and report.md (aggregate numbers only)
under ``<FRIDAY_HOME>/trials/r0/``. If the serving model changes
between nights the run stops and says so, rather than mixing two models.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Dict, Optional

R0_REPEATS = 2
#: Failed nights in a row (a seat error, a broken run folder) before R0 stops
#: for good and says why, instead of failing every night.
R0_MAX_FAILURES = 3
#: Longest single editor / composer / selector reply, in tokens.
R0_MAX_TOKENS = 2048
#: How long one editor call may take before it counts as a failed step.
R0_CALL_TIMEOUT_S = 900


def _dir() -> Path:
    from agent_friday.services import trials
    d = trials._home() / "r0"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path() -> Path:
    return _dir() / "state.json"


def state() -> Dict:
    """R0's state; {} only when there is no state file at all. A file that
    cannot be read is "unreadable", which never runs and never reschedules."""
    p = _state_path()
    if not p.exists():
        return {}
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        v = None
    if not isinstance(v, dict) or not v.get("status"):
        return {"status": "unreadable", "reason": f"{p} could not be read"}
    return v


def _save(st: Dict) -> None:
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _developer_install() -> bool:
    """A source checkout of Friday (the repository sits beside the package),
    where the owner builds Friday and R0's verdict decides what to build next."""
    try:
        import agent_friday
        return (Path(agent_friday.__file__).resolve().parents[2] / ".git").exists()
    except Exception:
        return False


def schedule(now: Optional[float] = None) -> Dict:
    """Schedule R0 (once). A run folder that already holds a result is done."""
    st = state()
    if st:
        return st
    run_dir = _dir() / "run"
    if (run_dir / "result.json").exists():
        st = {"status": "done", "run_dir": str(run_dir), "reason": "the result was already there"}
    else:
        st = {"status": "scheduled", "repeats": R0_REPEATS, "created": float(now or time.time()),
              "run_dir": str(run_dir), "cells_done": 0, "failures": 0}
    _save(st)
    return st


def ensure_scheduled(now: Optional[float] = None) -> Dict:
    """R0 schedules itself the first time the nightly job runs on a developer
    install. Once done, stopped, cancelled or unreadable it is never scheduled
    again."""
    st = state()
    if st or not _developer_install():
        return st
    return schedule(now)


def due() -> bool:
    return state().get("status") in ("scheduled", "running")


def cancel(reason: str = "cancelled") -> Dict:
    st = state() or {"run_dir": str(_dir() / "run")}
    st.update(status="cancelled", reason=str(reason)[:200], at=time.time())
    _save(st)
    return st


def _seat_chat(seat: Dict[str, str], system: str, user: str) -> str:
    """One editor / composer / selector call on the seat the nightly job holds,
    through the Local AI queue like every episode (services/trial_job)."""
    import requests
    from agent_friday.services import background_gate as _bg, local_scheduler as _ls
    _bg.before_local_model_call(seat["model"])
    grant = _ls.admit("practice_trial", model=seat["model"], cls="background",
                      label="Practice-task experiment (R0): editor")
    try:
        r = requests.post(seat["endpoint"].rstrip("/") + "/chat/completions",
                          json={"model": seat["model"], "max_tokens": R0_MAX_TOKENS, "stream": False,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": user}]},
                          timeout=R0_CALL_TIMEOUT_S)
        r.raise_for_status()
        return ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    finally:
        try:
            grant.release()
        except Exception:
            pass


def _log(line: str) -> None:
    """A line on the running visible job (the practice-trials card)."""
    try:
        from agent_friday.services.visible_jobs import CURRENT
        job = CURRENT.get()
        if job is not None:
            job.log(line)
    except Exception:
        pass


def run_tonight(seat: Dict[str, str], watcher, run_one: Callable[..., dict], *,
                call_model: Optional[Callable[[str, str], str]] = None,
                scan: Optional[Callable[[str], dict]] = None,
                tasks: Optional[list] = None) -> Dict:
    """Run R0 until it is done or tonight's job must stop. Called only from the
    nightly practice-trials job, inside its watcher."""
    from agent_friday.services import trials
    from agent_friday.services.trial_job import _stop_now
    from agent_friday.task_eval import pack as P, r0, runs as R
    st = state()
    if st.get("status") not in ("scheduled", "running"):
        return {"skipped": f"R0 is {st.get('status') or 'not scheduled'}"}
    if _stop_now(watcher):
        return {"skipped": "stopped before R0 began"}
    if scan is None:
        from agent_friday.task_eval.runner import scan_for_canary
        scan = scan_for_canary
    model_call = call_model or (lambda system, user: _seat_chat(seat, system, user))

    def step(fn, *a, **kw):
        # Every episode and every editor call: check first, count its time
        # against tonight's cap, and if an interactive turn arrived while it
        # ran, throw the result away rather than store a preempted run.
        if _stop_now(watcher):
            raise r0.Yielded("stopped before the next step")
        t0 = time.time()
        try:
            res = fn(*a, **kw)
        finally:
            trials._spend_night(time.time() - t0)
        if watcher.tripped.is_set():
            raise r0.Yielded("an interactive turn needed the seat")
        return res

    st["status"] = "running"
    st["model"] = seat.get("model")
    _save(st)
    _log("R0 experiment: resuming" if st.get("cells_done") else "R0 experiment: starting")

    def still_wanted() -> bool:
        # A cancel written while this night's run was going wins: the next
        # step stops, and nothing here writes "running" over it.
        return state().get("status") == "running"

    def progress(done: int, planned: int) -> None:
        if not still_wanted():
            raise r0.Yielded("R0 was cancelled")
        st["cells_done"] = done
        st["planned_cells"] = planned
        st["failures"] = 0
        _save(st)
        _log(f"R0 experiment: {done} of {planned} task-repeats done")

    base_scan = scan

    def timed_scan(text: str) -> dict:
        # The canary scans run on the CPU, not the seat, but their time is
        # still tonight's testing time.
        t0 = time.time()
        try:
            return base_scan(text)
        finally:
            trials._spend_night(time.time() - t0)

    from agent_friday.services.background_gate import JobCancelled
    try:
        out = r0.run(tasks=tasks if tasks is not None else P.load_pack("dev"),
                     repeats=int(st.get("repeats") or R0_REPEATS), run_dir=Path(st["run_dir"]),
                     run_one=lambda **kw: step(run_one, **kw),
                     call_model=lambda system, user: step(model_call, system, user),
                     # The seat's address can change between nights (a new
                     # port); the model may not, so only the model is pinned.
                     scan=timed_scan, model=seat["model"], endpoint="the nightly job's local seat",
                     should_yield=lambda: _stop_now(watcher), on_cell=progress)
    except R.ManifestMismatch as e:
        if not still_wanted():
            return {"stopped": "R0 was cancelled"}
        st.update(status="stopped", reason=f"the serving model or the task pack changed ({e})", at=time.time())
        _save(st)
        _log("R0 experiment: stopped, the setup changed since it started")
        return {"stopped": st["reason"]}
    except JobCancelled:
        raise                  # the owner cancelled tonight's job; R0 resumes next night
    except Exception as e:
        if not still_wanted():
            return {"stopped": "R0 was cancelled"}
        st["failures"] = int(st.get("failures") or 0) + 1
        st["last_error"] = f"{type(e).__name__}: {str(e)[:300]}"
        if st["failures"] >= R0_MAX_FAILURES:
            st.update(status="stopped", reason=f"failed {st['failures']} nights in a row ({st['last_error']})",
                      at=time.time())
            _log("R0 experiment: stopped after repeated failures")
        _save(st)
        return {"failed": st["last_error"], "failures": st["failures"]}
    if not still_wanted():
        return {"stopped": "R0 was cancelled"}
    st["cells_done"] = out.get("cells", st.get("cells_done", 0))
    st["planned_cells"] = out.get("planned_cells")
    if out.get("complete"):
        report = r0.report(out)
        (Path(st["run_dir"]) / "report.md").write_text(report, encoding="utf-8")
        (_dir() / "report.md").write_text(report, encoding="utf-8")
        verdict = (out.get("decision") or {}).get("verdict")
        st.update(status="done", verdict=verdict, finished=time.time())
        _log(f"R0 experiment: finished, verdict {verdict}")
    _save(st)
    return {"complete": bool(out.get("complete")), "cells": out.get("cells"),
            "planned_cells": out.get("planned_cells"), "verdict": st.get("verdict")}
