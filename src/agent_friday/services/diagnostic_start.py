"""Diagnostic start mode (north star §33.5-6): one start of Friday made to
find out what is wrong, and to say so in a report the owner can read.

A diagnostic start is asked for with ``friday diagnose``, with
``FRIDAY_DIAGNOSTIC_START=1``, or from the Doctor page ("make the next start a
diagnostic start", a one-shot marker ``<home>/diagnostics/NEXT`` that the next
start removes as it reads it). It starts Friday:

* in safe mode for that start (services/safe_mode.py: extensions, the cloud,
  schedules and outward actions off, no connection leaves the machine), so a
  part that breaks the start, or sends something, does not run while it is
  being looked at;
* with a timeline of the start, measured from the top of server.py: each named
  phase and when it was reached, rewritten to ``progress-<pid>.json`` at each
  phase, so a start that stops part way still shows how far it got;
* with a full Doctor run once the page is serving (or after WAIT_S if it never
  is, or at once if the port cannot be bound), saved with the timeline as
  ``<home>/diagnostics/diagnostic-<stamp>.json`` and ``.txt`` (the newest five
  kept), shown on the Doctor page and printed in the console. A start that
  ended before its report is reported by ``friday diagnose`` when the process
  ends, or by the next diagnostic start.

The report holds what the Doctor report holds (versions, check states, error
signatures, the store inventory; no content) plus the phase names and times,
how the start was entered, and the result of the start's migration check. No
environment values, paths, or log lines go in it. Nothing is sent. It is one
start only: the next start is as it would have been without it.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

ENV = "FRIDAY_DIAGNOSTIC_START"
KEEP = 5
WAIT_S = 120.0
PHASES = ("migrations", "imports", "safe_mode", "instance_lock", "boot_checks", "serving")

_LOCK = threading.Lock()
_T0 = [time.monotonic()]
_TIMELINE: list = []
_STATE: dict = {}


def active() -> bool:
    return os.environ.get(ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _dir() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "diagnostics"


def next_marker() -> Path:
    return _dir() / "NEXT"


def set_next_start(on: bool) -> bool:
    """Make the next start a diagnostic start (or not). Returns the new value."""
    p = next_marker()
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"chosen_at": time.time()}), encoding="utf-8")
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    return next_marker().exists()


def claim_next_start() -> bool:
    """At the top of a server start: a NEXT marker makes this start a
    diagnostic start and is removed, so only this start is. Returns active()."""
    if not active():
        try:
            next_marker().unlink()
        except (FileNotFoundError, OSError):
            return active()
        os.environ[ENV] = "1"
        os.environ["FRIDAY_SAFE_START"] = "1"
    return active()


def set_origin(t0: float) -> None:
    """The monotonic time the process started (taken at the top of server.py)."""
    _T0[0] = float(t0)


def _write(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".partial-" + uuid.uuid4().hex[:6])
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def _progress_path() -> Path:
    return _dir() / ("progress-%d.json" % os.getpid())


def mark(phase: str, **facts) -> None:
    """Record that the start reached `phase` (one of PHASES). Facts are kept
    only when they are numbers, true/false or short plain words."""
    if not active() or phase not in PHASES:
        return
    keep = {k: v for k, v in facts.items()
            if isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= 40
                                                     and all(c.isalnum() or c in "_-. " for c in v))}
    with _LOCK:
        if any(r["phase"] == phase for r in _TIMELINE):
            return
        _TIMELINE.append({"phase": phase, "at_s": round(time.monotonic() - _T0[0], 3), **keep})
        snap = [dict(r) for r in _TIMELINE]
    try:
        _dir().mkdir(parents=True, exist_ok=True)
        _write(_progress_path(), json.dumps({"pid": os.getpid(), "started_at": _STATE.get("started_at")
                                             or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                             "timeline": snap}))
    except OSError:
        pass


def timeline() -> list:
    with _LOCK:
        return [dict(r) for r in _TIMELINE]


def _prune(d: Path) -> None:
    reps = sorted(d.glob("diagnostic-*.json"))
    for p in reps[:-KEEP]:
        for q in (p, p.with_suffix(".txt")):
            try:
                q.unlink()
            except OSError:
                pass
    for p in list(d.glob("*.partial-*")):
        try:
            if time.time() - p.stat().st_mtime > 60:
                p.unlink()
        except OSError:
            pass


def _rows(tl) -> list:
    out = []
    for r in tl if isinstance(tl, list) else []:
        if isinstance(r, dict) and isinstance(r.get("phase"), str):
            try:
                out.append((r["phase"], float(r.get("at_s") or 0.0)))
            except (TypeError, ValueError):
                out.append((r["phase"], 0.0))
    return out


def render_text(rep: dict) -> str:
    from agent_friday.services import doctor
    rep = rep if isinstance(rep, dict) else {}
    lines = ["Agent Friday diagnostic start %s" % rep.get("id"),
             "Entered: %s. This start was safe mode: %s." % (rep.get("entered"), rep.get("safe_mode")),
             "The start %s." % ("finished" if rep.get("finished") else "ended before it was serving"),
             "Migration check: %s" % rep.get("migrations"), "", "Start timeline:"]
    rows = _rows(rep.get("timeline"))
    for ph, at in rows:
        lines.append("  %-14s %8.3f s" % (ph, at))
    missing = [p for p in PHASES if p not in {ph for ph, _ in rows}]
    if missing:
        lines.append("  not reached: %s" % ", ".join(missing))
    doc = rep.get("doctor") if isinstance(rep.get("doctor"), dict) else {}
    lines.append("")
    if doc.get("failed"):
        lines.append("Doctor run failed: %s" % doc["failed"])
    else:
        try:
            lines.append(doctor.render_text(doc))
        except Exception as e:  # noqa: BLE001
            lines.append("Doctor report could not be shown: %s" % type(e).__name__)
    return "\n".join(lines)


def _doctor() -> dict:
    from agent_friday.services import doctor
    try:
        return doctor.build()
    except Exception as e:  # noqa: BLE001 - the report says the Doctor failed
        return {"report": "agent-friday-doctor", "failed": type(e).__name__, "checks": []}


def _save(rep: dict) -> dict:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    _write(d / (rep["id"] + ".json"), json.dumps(rep, indent=1, sort_keys=True))
    _write(d / (rep["id"] + ".txt"), render_text(rep))
    _prune(d)
    return rep


def _new_id() -> str:
    t = time.time()
    return "diagnostic-%s-%03d-%s" % (time.strftime("%Y%m%d-%H%M%S", time.gmtime(t)), int(t * 1000) % 1000,
                                      uuid.uuid4().hex[:6])


def run_report(migrations: Optional[dict] = None) -> Optional[dict]:
    """Run the Doctor now and save this start's diagnostic report (once per
    process). None when this is not a diagnostic start."""
    if not active():
        return None
    with _LOCK:
        if _STATE.get("reported"):
            return _STATE.get("rep")
        _STATE["reported"] = True
    from agent_friday.services import safe_mode
    st = safe_mode.state()
    mig = None
    if isinstance(migrations, dict):
        mig = {k: migrations.get(k) for k in ("ok", "from", "to", "safe_mode", "stop", "failed")
               if k in migrations and isinstance(migrations.get(k), (bool, int, type(None)))}
    tl = timeline()
    rep = {"report": "agent-friday-diagnostic-start", "report_version": 1, "id": _new_id(),
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "sharing": "by hand only; Friday never sends this file anywhere",
           "entered": "diagnostic start" if st.get("how") == "diagnostic" else (st.get("how") or "start"),
           "safe_mode": bool(st.get("active")), "migrations": mig if mig is not None else "none to run",
           "finished": any(r["phase"] == "serving" for r in tl),
           "timeline": tl, "earlier_unfinished": _take_unfinished(), "doctor": _doctor()}
    _save(rep)
    try:
        _progress_path().unlink()
    except OSError:
        pass
    with _LOCK:
        _STATE["rep"] = rep
    return rep


def _take_unfinished() -> list:
    """Progress files left by diagnostic starts that ended before their
    report (another process id): their timelines, and the files removed."""
    out = []
    try:
        files = sorted(_dir().glob("progress-*.json"))
    except OSError:
        return out
    for p in files:
        if p.name == _progress_path().name:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                out.append({"started_at": str(d.get("started_at"))[:20],
                            "timeline": [{"phase": ph, "at_s": at} for ph, at in _rows(d.get("timeline"))
                                         if ph in PHASES]})
        except (OSError, ValueError):
            pass
        try:
            p.unlink()
        except OSError:
            pass
    return out


def settle_unfinished() -> Optional[dict]:
    """After a diagnostic start's process has ended (``friday diagnose``):
    a start that ended before its report gets one now, with a Doctor run from
    this process. None when there was nothing left over."""
    left = _take_unfinished()
    if not left:
        return None
    last = left[-1]
    rep = {"report": "agent-friday-diagnostic-start", "report_version": 1, "id": _new_id(),
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "sharing": "by hand only; Friday never sends this file anywhere",
           "entered": "diagnostic start", "safe_mode": True, "migrations": "not recorded",
           "finished": False, "timeline": last["timeline"], "earlier_unfinished": left[:-1],
           "doctor": _doctor()}
    return _save(rep)


def latest() -> Optional[dict]:
    """The newest saved diagnostic report (for the Doctor page), or None."""
    try:
        reps = sorted(_dir().glob("diagnostic-*.json"))
    except OSError:
        return None
    for p in reversed(reps):
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rep, dict):
            return rep
    return None


def this_run_id() -> Optional[str]:
    with _LOCK:
        rep = _STATE.get("rep")
    return rep.get("id") if isinstance(rep, dict) else None


def start_watch(migrations: Optional[dict] = None, wait_s: float = WAIT_S) -> None:
    """In a diagnostic start: run the report once the page is serving, or
    after `wait_s` if it never is. Returns at once (the work is on a thread)."""
    if not active() or _STATE.get("watching"):
        return
    _STATE["watching"] = True
    _STATE.setdefault("started_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def _go():
        t_end = time.monotonic() + wait_s
        while time.monotonic() < t_end and not any(r["phase"] == "serving" for r in timeline()):
            time.sleep(0.25)
        time.sleep(1.0)
        try:
            rep = run_report(_STATE.get("migrations", migrations))
            print("  Diagnostic start: report %s saved in the diagnostics folder; the Doctor page "
                  "shows it." % rep["id"])
        except Exception as e:  # noqa: BLE001
            print("  Diagnostic start: the report could not be saved (%s)" % type(e).__name__)
    threading.Thread(target=_go, name="diagnostic-start", daemon=True).start()


def note_migrations(result: Optional[dict]) -> None:
    _STATE["migrations"] = result
