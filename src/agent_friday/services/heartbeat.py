"""The heartbeat: a deterministic pre-check, and a model call only on change.

What it replaced. The heartbeat was an agent_prompt schedule: every run
spawned a full agent (the ~16.7k-token chat prompt plus tool schemas) that
called the calendar and mail tools itself and, most of the time, answered
"NO CHANGE". One install measured 2-5 model calls and 100k-180k input tokens
per run, drifting across four cloud models: 17 runs, $2.65, for almost
nothing.

What it does now, each run:

  1. Pre-check, in code, what the heartbeat watches, since the last OK run:
       * Gmail: inbox mail that arrived after the last OK run;
       * the calendar window: timed events starting in the next
         `heartbeat.calendar_lookahead_minutes` not yet reported;
       * background tasks that finished since the last OK run.
     Reads go through the existing connector functions
     (google_accounts.merged_gmail / merged_calendar, agent._task_snapshot).
     A connector with no account at all is simply not watched. One that is
     set up but cannot be read HOLDS the run (`connector_unavailable`): no
     model call, nothing marked seen, so the next OK run looks back to the
     last OK one and nothing is lost.
  2. Nothing changed: no model call at all. Recorded as `no_change`.
  3. Something changed: ONE slim call (services/job_prompt.py; no persona,
     no tools, a few hundred tokens of instructions plus the changes) on a
     pinned model, as `cloud_intent.background_route` decides: under a Cloud
     only mode the owner chose, `scheduled_cloud.heartbeat_model`; otherwise
     the local seat when one serves, else that model if the owner allowed it.
     A cloud call is told finished tasks by name and status only, never their
     results. Recorded as `reported` (or `no_change` if the model judged it
     not worth saying).

Held statuses, recorded in the run log (schedule_runs.jsonl) with a reason:
  connector_unavailable  a watched connector could not be read;
  blocked                no model may run it now (no local seat serving and
                         the owner has not allowed a cloud model), or the
                         run's model refused (local-only guard);
  budget_cap             the owner's own spending hard stop has tripped.
                         Only ever when the owner set one: no cap ships on.

State (last OK run, what was already reported) lives in
~/.friday/heartbeat_state.json via the paths helper.

Setting `heartbeat.precheck` (default true). False sends the heartbeat back
through the old agent_prompt path unchanged.
"""
from __future__ import annotations

import contextlib
import json
import logging
import time as _time
from datetime import datetime, timedelta

from agent_friday.services.visible_jobs import visible_job

_log = logging.getLogger("friday.heartbeat")

SCHEDULE_ID = "sch_heartbeat"

#: Caps on what one slim call is handed; the material stays well under
#: ~2,500 tokens even when every list is full.
MAX_MAIL = 10
MAX_EVENTS = 8
MAX_TASKS = 8
SNIPPET_CHARS = 160
#: How many already-reported ids are remembered per kind.
SEEN_KEEP = 300

_TERMINAL = {"complete", "completed", "completed_unverified", "failed",
             "timeout", "error", "cancelled"}

#: The heartbeat's standing instructions in the slim call.
INSTRUCTIONS = (
    "You are the owner's heartbeat: observe and notify only, never act. The "
    "request lists what changed since the last check (new mail, calendar "
    "events coming up, background tasks that finished). If something there "
    "is genuinely actionable or time-sensitive, summarize it in one or two "
    "lines. If nothing needs the owner's attention, reply exactly: NO CHANGE."
)


class Unavailable(RuntimeError):
    """A watched connector is set up but could not be read."""

    def __init__(self, connector: str, reason: str):
        self.connector, self.reason = connector, reason
        super().__init__(f"{connector}: {reason}")


# ── settings and state ─────────────────────────────────────────────────────

def _settings() -> dict:
    out = {"precheck": True, "calendar_lookahead_minutes": 120}
    try:
        import agent_friday.core as core
        blk = (core._load_settings() or {}).get("heartbeat") or {}
        if isinstance(blk, dict):
            out.update({k: blk[k] for k in out if k in blk})
    except Exception:
        pass
    return out


def applies(rec) -> bool:
    """Does this schedule run through the pre-check?"""
    rec = rec or {}
    task = rec.get("task") or {}
    return (rec.get("id") == SCHEDULE_ID
            and task.get("kind") == "agent_prompt"
            and bool(_settings().get("precheck", True)))


def _state_path():
    from agent_friday.paths import friday_home
    return friday_home() / "heartbeat_state.json"


def load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        _log.warning("heartbeat: could not save its state (%s); the next run "
                     "looks back further and may repeat an item", e)


# ── the pre-check ──────────────────────────────────────────────────────────

def _google():
    try:
        from agent_friday.services import google_accounts as ga
        return ga if ga.has_accounts() else None
    except Exception:
        return None


def _read(result, connector, items_key):
    """The items of a merged_* read, or Unavailable when nothing could be read."""
    if not isinstance(result, dict):
        raise Unavailable(connector, "the read returned nothing usable")
    items = result.get(items_key) or []
    errors = result.get("errors") or []
    if errors and not result.get("accounts"):
        first = errors[0] if isinstance(errors[0], dict) else {"error": errors[0]}
        raise Unavailable(connector, str(first.get("error") or "could not be read"))
    if errors:
        _log.warning("heartbeat: %s partly unreadable (%d account error(s)); "
                     "reading the rest", connector, len(errors))
    return items


def _new_mail(ga, since: float, seen: set) -> list:
    if ga is None:
        return []
    try:
        res = ga.merged_gmail(limit_per_account=25,
                              query="in:inbox after:%d" % int(since))
    except Exception as e:
        raise Unavailable("Gmail", str(e) or type(e).__name__)
    out = []
    for m in _read(res, "Gmail", "messages"):
        mid = str(m.get("gmail_id") or m.get("id") or "")
        if not mid or mid in seen:
            continue
        out.append({"id": mid, "from": str(m.get("sender") or ""),
                    "subject": str(m.get("subject") or ""),
                    "snippet": str(m.get("snippet") or "")[:SNIPPET_CHARS],
                    "when": str(m.get("timestamp") or "")})
    return out


def _parse_start(value):
    s = str(value or "")
    if "T" not in s:          # an all-day event is not time-sensitive
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def _upcoming_events(ga, now: datetime, lookahead_min: int, seen: set) -> list:
    if ga is None:
        return []
    try:
        res = ga.merged_calendar(days=2)
    except Exception as e:
        raise Unavailable("Calendar", str(e) or type(e).__name__)
    end = now + timedelta(minutes=max(1, int(lookahead_min)))
    out = []
    for ev in _read(res, "Calendar", "events"):
        start = _parse_start(ev.get("start_time"))
        if start is None or not (now <= start <= end):
            continue
        key = "%s|%s" % (ev.get("id") or ev.get("title"), ev.get("start_time"))
        if key in seen:
            continue
        out.append({"id": key, "title": str(ev.get("title") or "(untitled)"),
                    "start": str(ev.get("start_time") or ""),
                    "end": str(ev.get("end_time") or ""),
                    "location": str(ev.get("location") or "")})
    return out


def _finished_tasks(since: float) -> list:
    try:
        from agent_friday.services.agent import _task_snapshot
        tasks = _task_snapshot() or []
    except Exception as e:
        _log.debug("heartbeat: task list unavailable (%s)", e)
        return []
    out = []
    for t in tasks:
        if not isinstance(t, dict) or t.get("status") not in _TERMINAL:
            continue
        if str(t.get("description") or "").startswith("scheduled:" + SCHEDULE_ID):
            continue
        try:
            ended = float(t.get("ended") or 0)
        except (TypeError, ValueError):
            continue
        if ended <= since:
            continue
        out.append({"name": str(t.get("name") or "a task"),
                    "status": str(t.get("status")),
                    "result": str(t.get("result") or "")[:SNIPPET_CHARS]})
    return out


def precheck(state: dict, *, now_ts: float | None = None, lookback_min: int = 60) -> dict:
    """What changed since the last OK run. Raises Unavailable."""
    now_ts = _time.time() if now_ts is None else now_ts
    since = float(state.get("last_ok_ts") or (now_ts - lookback_min * 60))
    ga = _google()
    mail = _new_mail(ga, since, set(state.get("mail_seen") or ()))
    events = _upcoming_events(ga, datetime.fromtimestamp(now_ts).astimezone(),
                              _settings()["calendar_lookahead_minutes"],
                              set(state.get("events_seen") or ()))
    tasks = _finished_tasks(since)
    return {"since": since, "mail": mail, "events": events, "tasks": tasks,
            "watched": {"google": ga is not None}}


def changed(obs: dict) -> bool:
    return bool(obs.get("mail") or obs.get("events") or obs.get("tasks"))


def material(obs: dict, *, task_results: bool = True) -> str:
    """The slim call's request: only what changed, capped.

    ``task_results=False`` (the call goes to a cloud model) reports finished
    background tasks by name and status only. Their result text can come from
    work that ran inside a private local-only scope (compaction, meeting
    capture, memory review, citations, private research), and the owner's
    consent for the heartbeat to use the cloud does not reach that text."""
    since = datetime.fromtimestamp(obs["since"]).strftime("%Y-%m-%d %H:%M")
    parts = [f"Changes since the last check ({since}):"]
    if obs.get("mail"):
        parts.append("NEW MAIL (%d):" % len(obs["mail"]))
        parts += ["- from %s | %s | %s" % (m["from"], m["subject"], m["snippet"])
                  for m in obs["mail"][:MAX_MAIL]]
    if obs.get("events"):
        parts.append("COMING UP ON THE CALENDAR:")
        parts += ["- %s to %s %s%s" % (e["start"], e["end"], e["title"],
                                       (" @ " + e["location"]) if e["location"] else "")
                  for e in obs["events"][:MAX_EVENTS]]
    if obs.get("tasks"):
        parts.append("BACKGROUND TASKS THAT FINISHED:")
        if task_results:
            parts += ["- %s (%s): %s" % (t["name"], t["status"], t["result"])
                      for t in obs["tasks"][:MAX_TASKS]]
        else:
            parts += ["- %s (%s)" % (t["name"], t["status"])
                      for t in obs["tasks"][:MAX_TASKS]]
    return "\n".join(parts)


# ── the run ────────────────────────────────────────────────────────────────

def _seat_for(rec):
    """(model, 'local'|'cloud', reason) this run may use; model None = held.

    The shipped (local-only) heartbeat asks the one mode reader,
    `cloud_intent.background_route`: a chosen Cloud only mode sends it to the
    cloud on `scheduled_cloud.heartbeat_model` (owner decision (b)); a local
    mode uses the serving local seat, else the owner's Settings > Spending
    opt-in, else holds. A heartbeat the owner took off local-only keeps its
    own rule: a serving local seat, else its own model, else the same pin.
    """
    from agent_friday.services import scheduler as _s
    task = (rec or {}).get("task") or {}
    if task.get("local_only", True):
        from agent_friday.services import cloud_intent as _ci
        r = _ci.background_route((rec or {}).get("id") or SCHEDULE_ID,
                                 local_serving=_s._resolve_local_seat)
        if r.route == "local":
            return (r.local_model or _s._resolve_local_seat() or None), "local", r.reason
        if r.route == "cloud" and r.model:
            return r.model, "cloud", r.reason
        return None, "", r.reason
    local = _s._resolve_local_seat()
    if local:
        return local, "local", "a local model is serving"
    from agent_friday.services import scheduled_cloud as _sc
    cloud = task.get("model") or _sc.settings().get("heartbeat_model")
    return ((cloud, "cloud", "your own heartbeat model") if cloud
            else (None, "", "no local model is serving and no model is set"))


def _held(status: str, reason: str):
    from agent_friday.services.scheduler import HeldRun
    _log.info("heartbeat held (%s): %s", status, reason)
    return HeldRun(status, reason)


def _owner_instructions(rec) -> str:
    """The owner's own heartbeat prompt, when they edited the shipped one."""
    prompt = str(((rec or {}).get("task") or {}).get("prompt") or "").strip()
    try:
        from agent_friday.services.scheduler import _DEFAULT_AGENT_SCHEDULES
        shipped = next((d["task"]["prompt"] for d in _DEFAULT_AGENT_SCHEDULES
                        if d["id"] == SCHEDULE_ID), "")
    except Exception:
        shipped = ""
    if not prompt or prompt == shipped.strip():
        return ""
    return "The owner's own instructions for the heartbeat: " + prompt


@visible_job("Heartbeat", kind="scheduled", workspace="scheduler", icon="\U0001F493",
             eager=False, queued=False)
def _call(rec, model: str, where: str, request: str) -> str:
    """The one slim model call, pinned to `model`. A visible task card, like
    every scheduled job that reaches a model; a run with nothing new makes no
    call and so no card."""
    from agent_friday.services import job_prompt
    from agent_friday.services import local_only_guard as _guard
    from agent_friday.services import model_router as _mr
    job = job_prompt.Job(name="Heartbeat",
                         instructions="\n\n".join(x for x in (INSTRUCTIONS, _owner_instructions(rec)) if x))
    jp = job_prompt.build(job, request)
    label = (rec or {}).get("name") or "Heartbeat"
    with contextlib.ExitStack() as stack:
        # Local: the local-only guard (no cloud leg may answer). Cloud: pinned
        # to exactly this model (no fallback leg may reach a dearer one).
        stack.enter_context(_guard.local_only(label) if where == "local"
                            else _guard.cloud_pinned(model, label))
        try:
            # The Local AI queue: admitted at its local call, time-bound.
            from agent_friday.services import background_gate as _bg
            stack.enter_context(_bg.background_job(
                kind="scheduled", key="schedule:" + SCHEDULE_ID, label=label,
                deferrable=False))
        except Exception as e:
            _log.debug("heartbeat: Local AI queue unavailable (%s)", e)
        try:
            from agent_friday.services import cost_meter as _cm
            _cm.push_attribution(kind="scheduled", schedule_id=SCHEDULE_ID,
                                 run_id=(rec or {}).get("_active_run_id"),
                                 workspace="system")
            stack.callback(_cm.pop_attribution)
        except Exception:
            pass
        return _mr._generate_text(jp.messages, system=jp.system, model=model,
                                  max_tokens=400, orb_label="💓 Heartbeat",
                                  workspace="system")


def _budget_tripped():
    """The owner's hard stop, if they set one and it has tripped. None by
    default: no cap ships on."""
    try:
        from agent_friday.services import spend_guard
        return spend_guard.tripped() if spend_guard.enabled() else None
    except Exception as e:
        _log.debug("heartbeat: spend guard unreadable (%s)", e)
        return None


def run(rec) -> dict:
    """One heartbeat run. Returns the scheduler result; raises HeldRun."""
    started = _time.time()
    state = load_state()
    every = int((((rec or {}).get("spec") or {}).get("every_minutes")) or 60)
    try:
        obs = precheck(state, now_ts=started, lookback_min=every)
    except Unavailable as e:
        raise _held("connector_unavailable",
                    f"{e.connector} could not be read ({e.reason}), so the "
                    f"heartbeat made no model call and will look back to the "
                    f"last good check next time")
    seen_note = "%d new mail, %d upcoming event(s), %d finished task(s)" % (
        len(obs["mail"]), len(obs["events"]), len(obs["tasks"]))

    def _ok(outcome, summary, calls):
        state.update({
            "last_ok_ts": started,
            "last_ok": datetime.fromtimestamp(started).isoformat(timespec="seconds"),
            "last_outcome": outcome,
            "mail_seen": (list(state.get("mail_seen") or [])
                          + [m["id"] for m in obs["mail"]])[-SEEN_KEEP:],
            "events_seen": (list(state.get("events_seen") or [])
                            + [e["id"] for e in obs["events"]])[-SEEN_KEEP:],
        })
        _save_state(state)
        _log.info("heartbeat %s: %s; %d model call(s)", outcome, seen_note, calls)
        return {"changed": outcome == "reported", "summary": summary,
                "outcome": outcome, "model_calls": calls}

    if not changed(obs):
        return _ok("no_change", "NO CHANGE", 0)

    model, where, why = _seat_for(rec)
    if not model:
        raise _held("blocked",
                    f"{seen_note} to report, but {why or 'no model may take it'} "
                    f"(Settings > Spending)")
    if where == "cloud":
        t = _budget_tripped()
        if t:
            raise _held("budget_cap",
                        f"your {t['period']} spending cap is reached "
                        f"(${t['spend']:.2f} of ${t['limit']:.2f}), so the "
                        f"heartbeat did not call {model}")
    to_cloud = where == "cloud"
    if to_cloud and obs.get("tasks"):
        _log.info("heartbeat: %d finished task(s) go to %s by name and status "
                  "only; their results stay on this PC", len(obs["tasks"]), model)
    try:
        text = _call(rec, model, where, material(obs, task_results=not to_cloud))
    except Exception as e:
        name = type(e).__name__
        if name == "SpendCapReached":
            raise _held("budget_cap", str(e))
        if name in ("CloudRefused", "LocalUnavailable"):
            raise _held("blocked", f"{model} could not run it: {e}")
        raise
    if type(text).__name__ == "RoutedRefusal":
        raise _held("blocked", f"the router refused the call: {text}")
    text = str(text or "").strip()
    if not text or text.rstrip(".").upper() == "NO CHANGE":
        return _ok("no_change", "NO CHANGE", 1)
    return _ok("reported", text, 1)
