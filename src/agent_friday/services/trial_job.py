"""The nightly practice-task trial job: visible, deferrable, idle and on AC only.

Runs queued trials (services/trials) on the brain seat that is already loaded;
nothing new is loaded. It is a deferrable visible job (kind "trial"), so it
shows in the Local AI queue and waits for idle, and it checks the same gate
between episodes. While an episode runs, a watcher re-reads the gate every
two seconds: the moment an interactive turn appears it drops a yield file and
the episode child stops at its next model call (that episode is then missing,
not a zero), and the job ends; the trial resumes the next night from its
stored episodes. At most 3 seat-hours a night (decision HD7).

On Sundays it also raises the one weekly summary card (decision HD3).
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from agent_friday.services.visible_jobs import visible_job

# Loaded with the scheduler's roster at startup so the trial card's decision
# hook is registered before the owner can click a card left from a previous run.
from agent_friday.services import trials as _trials_hooks  # noqa: F401


def _seat() -> Optional[Dict[str, str]]:
    try:
        from agent_friday.services import local_brain, residency_arbiter
        seat = local_brain.brain_seat()
        if not seat or not seat.get("model"):
            return None
        ep = residency_arbiter.owned_endpoint(seat["model"])
        return {"model": seat["model"], "endpoint": ep} if ep else None
    except Exception:
        return None


def _gate_blocks() -> str:
    try:
        from agent_friday.services import background_gate as bg
        return bg.block_reason({"seat_is_local": True, "deferrable": True})
    except Exception:
        return "the idle check failed"


class _Watcher:
    def __init__(self, path: Path):
        self.path = path
        self.stop = threading.Event()
        self.tripped = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.is_set():
            if _gate_blocks():
                self.tripped.set()
                try:
                    self.path.write_text("yield", encoding="utf-8")
                except Exception:
                    pass
            self.stop.wait(2.0)

    def __enter__(self):
        # A yield file left by an earlier run (a crash, a reused process id)
        # would preempt every episode of this one.
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.t.join(timeout=5)
        try:
            self.path.unlink()
        except Exception:
            pass


def seat_run_one(seat: Dict[str, str], yield_file: Optional[str] = None):
    """An episode runner that takes the seat properly. Each episode child calls
    the seat itself, so the seat is taken HERE, in the calling visible job:
    through the Local AI queue (it waits while an interactive turn holds the
    seat or the PC is not idle), then held by the scheduler for the length of
    the episode. Used by the trial job and by SkillOpt's evaluator."""
    from agent_friday.task_eval.runner import run_episode

    def run_one(**kw):
        from agent_friday.services import background_gate as _bg, local_scheduler as _ls
        _bg.before_local_model_call(seat["model"])
        grant = _ls.admit("practice_trial", model=seat["model"], cls="background",
                          label="Practice-task trial")
        try:
            return run_episode(endpoint=seat["endpoint"], model=seat["model"],
                               yield_file=yield_file, **kw)
        finally:
            try:
                grant.release()
            except Exception:
                pass
    return run_one


def _sweep_stale_scratch(max_age_s: float = 86400.0) -> int:
    """Scratch homes left by a child that outlived a crash of Friday itself
    (they hold candidate text and a job file). Only our own prefix, only old."""
    import shutil
    n = 0
    root = Path(tempfile.gettempdir())
    for prefix in ("friday-trial-", "friday-skillopt-", "friday-grade-", "friday-scan-"):
        for d in root.glob(prefix + "*"):
            try:
                if d.is_dir() and time.time() - d.stat().st_mtime > max_age_s:
                    shutil.rmtree(d, ignore_errors=True)
                    n += 1
            except OSError:
                pass
    return n


def _stop_now(watcher) -> bool:
    """Checked before every episode: an interactive turn, the PC leaving AC
    power, or tonight's testing time used up all stop the run (resumable)."""
    from agent_friday.services import trials
    return (watcher.tripped.is_set() or not trials.on_ac_power()
            or trials.night_seconds_used() >= trials.NIGHT_CAP_SECONDS)


@visible_job('Practice-task trials', kind='trial', workspace='skills', deferrable=True)
def run_pending_trials() -> dict:
    from agent_friday.services import trials
    out = {"ran": [], "skipped": ""}
    if time.localtime().tm_wday == 6:
        try:
            # Learned things that keep hurting more than they help get a
            # "retire this?" card (services/lesson_records); quiet like the
            # trial cards, so the summary below is the one notice.
            from agent_friday.services import lesson_records
            lesson_records.raise_retirement_cards()
        except Exception:
            pass
        try:
            trials.weekly_digest()
        except Exception:
            pass
    pending = trials.candidates("queued") + trials.candidates("running")
    # R0, the one-off experiment that decides whether the candidate writer is
    # worth building, schedules itself once and runs here, after the queued
    # trials, in whatever is left of tonight's testing time (services/r0_nightly).
    from agent_friday.services import r0_nightly
    r0_nightly.ensure_scheduled()
    r0_due = r0_nightly.due()
    if not pending and not r0_due:
        out["skipped"] = "nothing to test"
        return out
    if not trials.on_ac_power():
        out["skipped"] = "on battery"
        return out
    if trials.night_seconds_used() >= trials.NIGHT_CAP_SECONDS:
        out["skipped"] = "tonight's testing time is used up"
        return out
    seat = _seat()
    if seat is None:
        out["skipped"] = "no local model is serving"
        return out
    why = _gate_blocks()
    if why:
        out["skipped"] = why
        return out
    _sweep_stale_scratch()
    yf = Path(tempfile.gettempdir()) / f"friday-trial-yield-{os.getpid()}"
    with _Watcher(yf) as w:
        run_one = seat_run_one(seat, str(yf))
        for cand in pending:
            if cand.get("cloud"):
                continue          # cloud trials run only when the owner starts them (HD2)
            if w.tripped.is_set() or trials.night_seconds_used() >= trials.NIGHT_CAP_SECONDS:
                break
            res = trials.run_trial(cand["id"], run_one=run_one, endpoint=seat["endpoint"],
                                   model=seat["model"], should_yield=lambda: _stop_now(w))
            out["ran"].append({"id": cand["id"], "status": res.get("status")})
        if r0_due and not _stop_now(w):
            out["r0"] = r0_nightly.run_tonight(seat, w, run_one)
    return out
