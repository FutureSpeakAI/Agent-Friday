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
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.t.join(timeout=5)
        try:
            self.path.unlink()
        except Exception:
            pass


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
    if not pending:
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
    yf = Path(tempfile.gettempdir()) / f"friday-trial-yield-{os.getpid()}"
    with _Watcher(yf) as w:
        from agent_friday.task_eval.runner import run_episode

        def run_one(**kw):
            return run_episode(endpoint=seat["endpoint"], model=seat["model"],
                               yield_file=str(yf), **kw)
        for cand in pending:
            if cand.get("cloud"):
                continue          # cloud trials run only when the owner starts them (HD2)
            if w.tripped.is_set() or trials.night_seconds_used() >= trials.NIGHT_CAP_SECONDS:
                break
            res = trials.run_trial(cand["id"], run_one=run_one, endpoint=seat["endpoint"],
                                   model=seat["model"], should_yield=w.tripped.is_set)
            out["ran"].append({"id": cand["id"], "status": res.get("status")})
    return out
