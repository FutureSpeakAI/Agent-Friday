"""Doctor repairs for the local model (north star §32.2-4 "Doctor offers repair
when local model down"; the repair half of §28.1-1: critical repair does not
depend on a model being available).

What the Doctor can do, each one a press on Friday's own page on this PC, run
in the background one at a time, with a receipt in the action ledger. No model
is asked anything, and nothing here decides on its own to run:

* restart: bring the local model seats back through the residency arbiter's
  own verified restore (Arbiter.restore_unheld_seats). It keeps every reason a
  seat is down on purpose: a lease, build hours, a stand-down, another program
  on the GPU. Not in safe mode, which never starts a seat.
* verify: re-check every model file Friday has registered against its recorded
  size and SHA-256 (model_store.verify). Reads only. A file that is gone or
  changed is named, with where to download it again (Settings > Models).
* runtime: reinstall the local model runtime from its pinned release when the
  installed one cannot serve (model_download.installed_runtime_problem). This
  downloads, so it is offered only when that check names a problem, and never
  in safe mode.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

REPAIRS = ("restart", "verify", "runtime")

_LOCK = threading.Lock()
_JOB: dict = {"state": "idle"}


class RepairError(ValueError):
    """Said plainly to the owner."""


def _safe_mode() -> bool:
    try:
        from agent_friday.services import safe_mode
        return bool(safe_mode.active())
    except Exception:  # noqa: BLE001 - unknown is treated as on: nothing starts
        return True


def _brain() -> dict:
    try:
        from agent_friday.services import capability_state as cs
        s = cs.local_brain_state()
        return {"state": s.state, "detail": s.detail,
                "down": s.state == cs.PRESENT_FAILING, "installed": s.state != cs.ABSENT}
    except Exception as e:  # noqa: BLE001
        return {"state": "unknown", "detail": "could not be read (%s)" % type(e).__name__,
                "down": False, "installed": False}


def _arbiter():
    try:
        from agent_friday.services import residency_arbiter
        return residency_arbiter.get_arbiter()
    except Exception:  # noqa: BLE001
        return None


def _runtime_problem() -> Optional[str]:
    try:
        from agent_friday.services import model_download
        return model_download.installed_runtime_problem()
    except Exception:  # noqa: BLE001
        return None


def _models() -> list:
    try:
        from agent_friday.services import model_store
        return sorted(model_store.all_models())
    except Exception:  # noqa: BLE001
        return []


def state() -> dict:
    """What the Doctor sees of the local model, and the repairs it offers."""
    brain = _brain()
    safe = _safe_mode()
    offers = []
    if brain["installed"]:
        if safe:
            why = "Safe mode never starts a model: leave safe mode at the next start first."
        elif _arbiter() is None:
            why = "The local model manager is not running in this Friday."
        else:
            why = None
        offers.append({"id": "restart", "label": "Restart the local model", "available": why is None,
                       "why": why, "recommended": brain["down"] and why is None})
    if _models():
        offers.append({"id": "verify", "label": "Check the model files", "available": True, "why": None,
                       "recommended": brain["down"]})
    problem = _runtime_problem()
    if problem:
        offers.append({"id": "runtime", "label": "Reinstall the local model runtime",
                       "available": not safe,
                       "why": "Safe mode downloads nothing." if safe else None,
                       "problem": problem, "recommended": not safe})
    return {"brain": brain, "safe_mode": safe, "offers": offers, "job": dict(_JOB)}


def _receipt(event: str, **fields) -> None:
    try:
        from agent_friday.services import action_ledger
        action_ledger.try_append("doctor_repair", dict(fields, event=event))
    except Exception:  # noqa: BLE001
        pass


def _run_restart() -> dict:
    arb = _arbiter()
    if arb is None:
        return {"ok": False, "note": "the local model manager is not running"}
    r = arb.restore_unheld_seats("doctor_repair")
    out = {"ok": bool(r.get("ok")), "note": r.get("note"),
           "restored": list(r.get("restored") or []), "missing": list(r.get("missing") or [])}
    out["brain_after"] = _brain()["state"]
    return out


def _run_verify() -> dict:
    from agent_friday.services import model_store
    rows = []
    for mid in _models():
        try:
            v = model_store.verify(mid)
        except Exception as e:  # noqa: BLE001
            v = {"ok": False, "error": "could not be checked (%s)" % type(e).__name__}
        rows.append({"model": mid, "ok": bool(v.get("ok")),
                     "checked": v.get("checked"),
                     "problem": None if v.get("ok") else
                     ("missing" if v.get("missing") else
                      "checksum" if v.get("error") == "checksum mismatch" else
                      "size" if str(v.get("error", "")).startswith("size changed") else "other")})
    bad = [r for r in rows if not r["ok"]]
    return {"ok": not bad, "models": rows,
            "next": ("Download the named models again from Settings > Models." if bad else None)}


def _run_runtime() -> dict:
    from agent_friday.services import model_download
    try:
        model_download.repair_runtime()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "note": "the runtime could not be reinstalled (%s)" % type(e).__name__}
    return {"ok": _runtime_problem() is None}


_RUNNERS = {"restart": _run_restart, "verify": _run_verify, "runtime": _run_runtime}


def start(what: str, *, by: str = "owner:ui", wait: bool = False) -> dict:
    """Start one repair in the background. Refused when it is not offered
    now, when safe mode forbids it, or while another repair runs."""
    if what not in REPAIRS:
        raise RepairError("Unknown repair: choose one of %s." % ", ".join(REPAIRS))
    offer = next((o for o in state()["offers"] if o["id"] == what), None)
    if offer is None:
        raise RepairError("That repair is not offered now.")
    if not offer["available"]:
        raise RepairError(offer["why"] or "That repair is not available now.")
    if not _LOCK.acquire(blocking=False):
        raise RepairError("A repair is running already.")
    _JOB.clear()
    _JOB.update(state="running", what=what, started_at=time.time(), by=by)
    _receipt("started", repair=what, by=by)

    def _go():
        try:
            result = _RUNNERS[what]()
            _JOB.update(state="done", result=result, finished_at=time.time())
            _receipt("finished", repair=what, ok=bool(result.get("ok")))
        except Exception as e:  # noqa: BLE001 - the job says what happened
            _JOB.update(state="failed", error=type(e).__name__, finished_at=time.time())
            _receipt("failed", repair=what, error=type(e).__name__)
        finally:
            _LOCK.release()

    t = threading.Thread(target=_go, name="doctor-repair-" + what, daemon=True)
    t.start()
    if wait:
        t.join()
    return dict(_JOB)
