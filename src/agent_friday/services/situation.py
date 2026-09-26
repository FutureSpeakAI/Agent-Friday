"""What Friday and this machine are doing right now, answered in milliseconds.

The agents' `situation` tool and the pinned situation line read this. It is
built only from state the server already holds: module attributes, a lock and
a shallow copy, or a value that machine_probe.snapshot refreshes behind the
request. Nothing here probes. The slow sources (the inference probes behind
/api/health, /api/intelligence, nvidia-smi, Ollama over HTTP, a residency
replan, the task journal's liveness scan) are left out on purpose, and each
section says how old its numbers are, so a stale value reads as stale rather
than as now.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Any, Callable

# ── CPU ──────────────────────────────────────────────────────────────────────
# Nothing else in the server keeps a CPU percentage: the arbiter blocks 150 ms
# to take one and keeps only whole free cores. A daemon thread samples the
# change in cpu_times every few seconds, so a read is a lock and a copy.

CPU_SAMPLE_S = 5.0
_CPU_LOCK = threading.Lock()
_CPU: dict = {"pct": None, "at": 0.0, "prev": None}
_SAMPLER_LOCK = threading.Lock()
_SAMPLER: dict = {"thread": None}


def sample_cpu() -> float | None:
    """Update the CPU percentage from the change in cpu_times since last time."""
    import psutil
    t = psutil.cpu_times()
    total = float(sum(t))
    busy = total - float(getattr(t, "idle", 0.0)) - float(getattr(t, "iowait", 0.0))
    with _CPU_LOCK:
        prev = _CPU["prev"]
        _CPU["prev"] = (busy, total)
        if prev is not None and total > prev[1]:
            pct = 100.0 * (busy - prev[0]) / (total - prev[1])
            _CPU["pct"] = round(min(100.0, max(0.0, pct)), 1)
            _CPU["at"] = time.time()
        return _CPU["pct"]


def start_cpu_sampler() -> None:
    """Start the CPU sampler once (the server does it at boot). Inert under
    FRIDAY_TESTING, like every other background loop."""
    if _SAMPLER["thread"] is not None or os.environ.get("FRIDAY_TESTING"):
        return
    with _SAMPLER_LOCK:
        if _SAMPLER["thread"] is not None:
            return

        def loop():
            while True:
                try:
                    sample_cpu()
                except Exception:
                    pass
                time.sleep(CPU_SAMPLE_S)

        th = threading.Thread(target=loop, name="situation-cpu", daemon=True)
        _SAMPLER["thread"] = th
        th.start()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _age(ts: Any, now: float) -> float | None:
    try:
        return round(max(0.0, now - float(ts)), 1) if ts else None
    except (TypeError, ValueError):
        return None


def _gb(n_bytes: float) -> float:
    return round(float(n_bytes) / 2 ** 30, 1)


def _cached(key: str, compute: Callable[[], Any], fresh_for: float):
    """(value, age_s, state) from machine_probe.snapshot, never waiting past a
    tenth of a second: a cold read starts the work behind the request."""
    from agent_friday.services import machine_probe as mp
    value, at, state = mp.snapshot(key, compute, fresh_for=fresh_for,
                                   budget=0.1, allow_blocking=False)
    return value, (_age(at, time.time()) if at else None), state


def _safe(fn: Callable[[float], dict], now: float) -> dict:
    try:
        return fn(now)
    except Exception as e:                       # a section, not the snapshot
        return {"error": "%s: %s" % (type(e).__name__, e)}


# ── Sections ─────────────────────────────────────────────────────────────────

def _desktop(now: float) -> dict:
    from agent_friday.services import desktop_bus
    return desktop_bus.state(now)


def _machine(now: float) -> dict:
    import psutil
    from agent_friday.services import machine_monitor as mm
    vm = psutil.virtual_memory()
    with _CPU_LOCK:
        pct, at = _CPU["pct"], _CPU["at"]
    out = {
        "cpu": {"used_pct": pct, "cores": os.cpu_count(), "age_s": _age(at, now)},
        "ram": {"total_gb": _gb(vm.total), "available_gb": _gb(vm.available),
                "used_pct": vm.percent},
    }
    s = mm.last_sample() or {}
    cards = []
    for g in s.get("gpus") or []:
        cards.append({"name": g.get("name"),
                      "total_gb": round((g.get("total_mib") or 0) / 1024, 1),
                      "used_gb": round((g.get("used_mib") or 0) / 1024, 1),
                      "util_pct": g.get("util_pct")})
    out["gpu"] = {"cards": cards, "age_s": mm.last_sample_age_s()}
    drive = os.environ.get("SystemDrive", "C:") + os.sep
    du = shutil.disk_usage(drive)
    out["disk"] = {"drive": drive.rstrip("\\/"), "free_gb": _gb(du.free),
                   "total_gb": _gb(du.total)}
    return out


_SEAT_ROLES = ("reasoning", "local", "subagent", "heavy_hitter", "voice")


def _models(now: float) -> dict:
    from agent_friday import core
    from agent_friday.services import stand_down
    s = core._load_settings() or {}
    routing = s.get("model_routing") or {}
    cap = s.get("capability_routing") or {}
    seats = {}
    for role in _SEAT_ROLES:
        v = cap.get(role)
        if isinstance(v, dict) and v.get("model"):
            seats[role] = v.get("model")
    out: dict = {"mode": routing.get("mode"),
                 "chat_model": s.get("orchestrator_model"), "seats": seats}

    try:
        from agent_friday.services import residency_arbiter as ra
        arb = ra.ARBITER
    except Exception:
        arb = None
    if arb is not None:
        plan = getattr(arb, "plan", None) or {}
        planned = plan.get("seats") if isinstance(plan, dict) else None
        out["planned"] = {
            role: {"model": (v or {}).get("model_id"), "status": (v or {}).get("status")}
            for role, v in (planned or {}).items()}
        out["plan_age_s"] = _age(getattr(arb, "planned_at", None), now)
        out["arbiter_state"] = getattr(arb, "state", None)
        lease = getattr(arb, "lease", None)
        if lease:
            lease = dict(lease)
            out["lease"] = {"kind": lease.get("kind"), "role": lease.get("role"),
                            "model": lease.get("model_id")}
        procs = getattr(getattr(arb, "llama", None), "procs", None) or {}
        out["loaded"] = sorted(str(m) for m in list(procs.keys()))

    st = stand_down.state() or {}
    active = bool(st.get("active"))
    out["stand_down"] = {"active": active}
    if active:
        out["stand_down"].update({"since": st.get("since"),
                                  "auto_resume_at": st.get("auto_resume_at"),
                                  "reason": stand_down.reason()})
    return out


def _activity(now: float) -> dict:
    from agent_friday import core
    out: dict = {}

    turns = []
    with core._TURNS_LOCK:
        for rec in core._TURNS.values():
            th = rec.get("thread")
            if th is not None and not th.is_alive():
                continue
            turns.append({k: rec.get(k) for k in
                          ("label", "step", "model", "started", "last_progress")})
    quiet_after = getattr(core, "TURN_QUIET_AFTER_S", 180)
    out["turns"] = [{
        "label": t["label"], "step": t["step"], "model": t["model"],
        "running_s": _age(t["started"], now),
        "quiet": bool(t["last_progress"] and now - t["last_progress"] > quiet_after),
    } for t in turns]

    try:
        from agent_friday.services import reasoning_trace as rt
        with rt._LOCK:
            live = [rt._header(tr) for tr in rt._TRACES.values()
                    if tr.get("status") == "running"]
        out["generating"] = [{
            "model": h.get("model"), "seat": h.get("seat"),
            "provider": h.get("provider"), "kind": h.get("kind"),
            "label": h.get("label"), "tokens": h.get("tokens"),
            "running_s": _age(h.get("started"), now)} for h in live]
    except Exception:
        out["generating"] = []

    with core.PROCESSES_LOCK:
        procs = [{k: p.get(k) for k in ("label", "category", "model", "progress",
                                         "step_n", "step_total", "eta_s", "started")}
                 for p in core.PROCESSES.values()
                 if p.get("status") == "running" and not p.get("dismissed")]
    for p in procs:
        p["running_s"] = _age(p.pop("started"), now)
    out["processes"] = procs

    from agent_friday.services import agent
    with agent.TASKS_LOCK:
        tasks = [{k: t.get(k) for k in ("task_id", "name", "status", "started",
                                         "model", "chain_step")}
                 for t in agent.TASKS.values()]
    counts: dict = {}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    active = [t for t in tasks if t["status"] in ("running", "queued",
                                                  "queued-for-seat")]
    for t in active:
        t["running_s"] = _age(t.pop("started"), now)
    out["tasks"] = {"counts": counts, "active": active[:8]}
    out["queue_depth"] = counts.get("queued-for-seat", 0) + counts.get("queued", 0)

    from agent_friday.services import scheduler
    with scheduler._RUNNING_LOCK:
        running_ids = set(scheduler._RUNNING)
    recs, age, state = _cached("situation.schedules", scheduler.list_schedules, 30.0)
    names = {r.get("id"): (r.get("name") or r.get("id")) for r in (recs or [])}
    upcoming = sorted((r for r in (recs or [])
                       if r.get("enabled", True) and r.get("next_run")
                       and r.get("id") not in running_ids),
                      key=lambda r: r["next_run"])
    out["scheduled"] = {
        "running": sorted(names.get(i, i) for i in running_ids),
        "next": ({"name": upcoming[0].get("name") or upcoming[0].get("id"),
                  "at": upcoming[0]["next_run"]} if upcoming else None),
        "age_s": age, "state": state}
    return out


def _spend(now: float) -> dict:
    from agent_friday.services import cost_meter
    val, age, state = _cached("situation.spend", cost_meter._rolling_spend, 30.0)
    if not val:
        return {"state": state}
    today, month = val
    return {"today_usd": today, "month_usd": month, "age_s": age, "state": state}


SECTIONS = (("desktop", _desktop), ("machine", _machine), ("models", _models),
            ("activity", _activity), ("spend", _spend))


def snapshot() -> dict:
    """Everything above, each section on its own so one failure costs one part."""
    start_cpu_sampler()
    now = time.time()
    out = {"at": now}
    for name, fn in SECTIONS:
        out[name] = _safe(fn, now)
    out["took_ms"] = round((time.time() - now) * 1000, 1)
    return out


def compact(value: Any, depth: int = 0) -> Any:
    """The snapshot without empty fields, lists trimmed to what a model reads."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = compact(v, depth + 1)
            if v not in (None, "", [], {}):
                out[k] = v
        return out
    if isinstance(value, list):
        return [compact(v, depth + 1) for v in value[:8]]
    return value


# ── Pinning: a live line in view on every turn of one conversation ──────────
# check_situation(pin=true) marks the conversation; each later turn's prompt
# then carries brief() read at that moment, never a copy from the turn that
# pinned it (live state is never answered from memory: services/live_state).

_PINS_LOCK = threading.Lock()
_PINS: dict = {"loaded": False, "ids": {}}


def _pins_path():
    from agent_friday.paths import friday_home
    return friday_home() / "situation_pins.json"


def _pins() -> dict:
    if not _PINS["loaded"]:
        import json
        try:
            data = json.loads(_pins_path().read_text(encoding="utf-8"))
            _PINS["ids"] = {str(k): float(v) for k, v in (data or {}).items()}
        except (OSError, ValueError, TypeError, AttributeError):
            _PINS["ids"] = {}
        _PINS["loaded"] = True
    return _PINS["ids"]


def is_pinned(conversation_id: str | None) -> bool:
    if not conversation_id:
        return False
    with _PINS_LOCK:
        return str(conversation_id) in _pins()


def set_pinned(conversation_id: str, on: bool) -> None:
    import json
    with _PINS_LOCK:
        ids = _pins()
        if on:
            ids[str(conversation_id)] = time.time()
        else:
            ids.pop(str(conversation_id), None)
        try:
            p = _pins_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(ids), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass


def pinned_block(conversation_id: str | None) -> str:
    """The prompt block for a pinned conversation, read now; '' otherwise."""
    if not is_pinned(conversation_id):
        return ""
    try:
        return ("\n\n== SITUATION (pinned; live, read at the start of this turn) ==\n"
                + brief())
    except Exception:
        return ""


# ── The compact, spoken-ready form ───────────────────────────────────────────

def _dur(s: Any) -> str:
    try:
        s = int(s or 0)
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    return "%dh %dm" % (s // 3600, (s % 3600) // 60)


def _clock(ts: Any) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "?"


def brief(snap: dict | None = None) -> str:
    """A few short lines a model can keep in view or read aloud."""
    s = snap or snapshot()
    lines = ["Situation at %s." % time.strftime("%H:%M:%S", time.localtime(s["at"]))]

    d = s.get("desktop") or {}
    if d.get("known"):
        focused = d.get("focused") or {}
        others = [w.get("label") or w.get("workspace") for w in d.get("open") or []
                  if w.get("workspace") != focused.get("workspace")]
        page = {"tab": "A workspace tab", "chat": "The chat window"}.get(d.get("page"), "Desktop")
        if focused:
            where = focused.get("label") or focused.get("workspace")
            detail = focused.get("detail")
            text = "%s: %s focused%s" % (page, where, " (%s)" % detail if detail else "")
        else:
            text = "%s: no workspace focused" % page
        if others:
            text += "; also open: " + ", ".join(others[:6])
        if d.get("tabs"):
            text += "; in tabs of their own: " + ", ".join(d["tabs"][:4])
        if (d.get("chat") or {}).get("open") or d.get("chat_window"):
            text += "; chat open"
        if d.get("visible") is False:
            text += "; the Friday window is minimized or covered"
        if not d.get("commands"):
            text += "; no desktop page is connected, so nothing can be opened on screen"
        lines.append(text + ".")
    else:
        lines.append("Desktop: not reported yet (no Friday window has checked in).")

    m = s.get("machine") or {}
    if "error" not in m:
        parts = []
        cpu = (m.get("cpu") or {}).get("used_pct")
        if cpu is not None:
            parts.append("CPU %s%%" % cpu)
        ram = m.get("ram") or {}
        if ram:
            parts.append("RAM %s of %s GB free" % (ram.get("available_gb"), ram.get("total_gb")))
        for g in (m.get("gpu") or {}).get("cards") or []:
            parts.append("GPU %s of %s GB used%s" % (
                g.get("used_gb"), g.get("total_gb"),
                ", %s%% busy" % g["util_pct"] if g.get("util_pct") is not None else ""))
        disk = m.get("disk") or {}
        if disk:
            parts.append("%s %s GB free" % (disk.get("drive"), disk.get("free_gb")))
        lines.append("Machine: " + ", ".join(parts) + ".")

    mo = s.get("models") or {}
    if "error" not in mo:
        text = "Models: %s routing" % (mo.get("mode") or "unknown")
        if mo.get("chat_model"):
            text += ", chat on %s" % mo["chat_model"]
        loaded = mo.get("loaded")
        if loaded is not None:
            text += ", local seats loaded: %s" % (", ".join(loaded) if loaded else "none")
        if (mo.get("stand_down") or {}).get("active"):
            text += ", STOOD DOWN (local GPU released)"
        if mo.get("lease"):
            text += ", GPU leased to %s" % (mo["lease"].get("kind") or "a job")
        lines.append(text + ".")

    a = s.get("activity") or {}
    if "error" not in a:
        bits = []
        for t in a.get("turns") or []:
            bits.append("chat turn %s%s%s" % (
                "on %s " % t["model"] if t.get("model") else "",
                "for %s" % _dur(t.get("running_s")),
                " (quiet)" if t.get("quiet") else ""))
        gen = a.get("generating") or []
        if gen:
            bits.append("generating: " + ", ".join(
                "%s%s" % (g.get("model") or g.get("seat") or "?",
                          " (%s)" % g["label"] if g.get("label") else "") for g in gen[:3]))
        tasks = a.get("tasks") or {}
        running = [t for t in tasks.get("active") or [] if t.get("status") == "running"]
        if running:
            bits.append("%d task%s running (%s)" % (
                len(running), "" if len(running) == 1 else "s",
                ", ".join((t.get("name") or t.get("task_id") or "?")[:40] for t in running[:3])))
        procs = [p for p in a.get("processes") or [] if p.get("category") != "default"]
        if procs:
            bits.append("%d background job%s" % (len(procs), "" if len(procs) == 1 else "s"))
        if a.get("queue_depth"):
            bits.append("%d waiting in the queue" % a["queue_depth"])
        sch = a.get("scheduled") or {}
        if sch.get("running"):
            bits.append("scheduled now: " + ", ".join(sch["running"][:3]))
        if sch.get("next"):
            bits.append("next scheduled: %s at %s" % (sch["next"]["name"], _clock(sch["next"]["at"])))
        lines.append("Now: " + ("; ".join(bits) if bits else "nothing running") + ".")

    sp = s.get("spend") or {}
    if sp.get("today_usd") is not None:
        lines.append("Spend today: $%.2f (month $%.2f)." % (sp["today_usd"], sp.get("month_usd") or 0))
    return "\n".join(lines)
