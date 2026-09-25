"""Workflows as a person sees them: what it does, when, and how it last went.

Two stores hold automations. A saved workflow (services/agent.py, one JSON
file per workflow) is the list of steps; a schedule (services/scheduler.py) is
the "when". A workflow that runs on a timetable is one of each, linked by the
schedule's task `{"kind": "workflow", "ref": <slug>}`, and this module is the
one place that joins them, so the Workflows screen shows one list instead of
two unrelated ones.

It also turns a plain-language request ("every weekday at 7:30, check the
council agendas and tell me what changed") into a draft the owner reviews
before anything is saved. The timetable is read by a deterministic parser
here, not by a model, so what the draft says about *when* is exactly what the
scheduler will do.

Outward actions are not decided here. The governance checkpoint
(governance/action_gate.py) holds every outward action a background step
attempts and raises an approval card. `asks_first` only predicts, from the
words of the steps, which of those cards the owner is likely to see, so the
draft can say so up front.
"""
from __future__ import annotations

import json
import re
import time as _time
from datetime import datetime, timedelta

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]
MAX_STEPS = 8
#: A scheduled workflow is several full agent runs back to back; the
#: scheduler's 30-minute default is sized for one.
WORKFLOW_TIMEOUT_S = 4 * 3600


# ── Describing a timetable ──────────────────────────────────────────────────

def _clock(hour: int, minute: int) -> str:
    h = int(hour) % 24
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    if h == 12 and minute == 0:
        return "noon"
    if h == 0 and minute == 0:
        return "midnight"
    return f"{h12}:{int(minute):02d} {suffix}" if minute else f"{h12} {suffix}"


def _join(words):
    words = list(words)
    if len(words) < 2:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _weekdays(spec):
    from agent_friday.services.scheduler import _spec_weekdays
    return _spec_weekdays(spec or {})


def describe_when(when) -> str:
    """One plain sentence for a timetable. `None` means run by hand."""
    if not when or not when.get("trigger") or when.get("trigger") == "manual":
        return "Only when you run it"
    trig, spec = when["trigger"], when.get("spec") or {}
    hm = _clock(spec.get("hour", 9), spec.get("minute", 0))
    if trig == "daily":
        return f"Every day at {hm}"
    if trig == "weekly":
        days = _weekdays(spec)
        if days == [0, 1, 2, 3, 4]:
            return f"Every weekday at {hm}"
        if days == [5, 6]:
            return f"Every Saturday and Sunday at {hm}"
        if len(days) == 7:
            return f"Every day at {hm}"
        return f"Every {_join(DAY_NAMES[d] for d in days)} at {hm}"
    if trig == "interval":
        mins = max(1, int(spec.get("every_minutes", 60)))
        if mins % 60 == 0:
            hrs = mins // 60
            return "Every hour" if hrs == 1 else f"Every {hrs} hours"
        return f"Every {mins} minutes"
    if trig == "once":
        try:
            at = datetime.fromtimestamp(float(spec.get("at") or 0))
        except (TypeError, ValueError, OSError):
            return "Once"
        return f"Once, on {at.strftime('%A, %B')} {at.day} at {_clock(at.hour, at.minute)}"
    if trig == "idle_daily":
        return "Once a day, while you're away from the computer"
    return trig


# ── Reading a timetable out of plain language ───────────────────────────────

_DAY_RX = {
    0: r"mon(?:day)?s?", 1: r"tue(?:s|sday)?s?", 2: r"wed(?:nesday)?s?",
    3: r"thu(?:rs?|rsday)?s?", 4: r"fri(?:day)?s?", 5: r"sat(?:urday)?s?",
    6: r"sun(?:day)?s?",
}
_PART_OF_DAY = {"morning": 8, "afternoon": 14, "evening": 18, "night": 21,
                "tonight": 21}
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "eight": 8, "ten": 10, "twelve": 12, "fifteen": 15, "thirty": 30,
              "a": 1, "an": 1}


def _num(tok):
    tok = (tok or "").lower()
    if tok.isdigit():
        return int(tok)
    return _NUM_WORDS.get(tok)


def _find_time(t):
    """(hour, minute, span) for the first clock time in `t`, or None."""
    m = re.search(r"\b(?:at\s+)?(noon|midday|midnight)\b", t)
    if m:
        return (0 if m.group(1) == "midnight" else 12), 0, m.span()
    m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?=\W|$)", t)
    if m:
        h, mi = int(m.group(1)) % 12, int(m.group(2) or 0)
        if m.group(3).startswith("p"):
            h += 12
        return h, mi, m.span()
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\b(?!\s*(?:minutes?|mins?|hours?|hrs?|%))", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if h > 23 or mi > 59:
            return None
        # A bare "at 7" means the working day, not seven in the morning or
        # seven at night by coin toss: 1-6 read as afternoon, 7-11 as morning.
        if 1 <= h <= 6:
            h += 12
        return h, mi, m.span()
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
    if m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
        h = int(m.group(1))
        return (h + 12 if 1 <= h <= 6 else h), int(m.group(2)), m.span()
    return None


def parse_when(text, now=None):
    """Read the timetable out of a plain-language request.

    Returns `(when, rest, note)`: `when` is a scheduler trigger and spec
    (None when the request says nothing about timing, which means "run it by
    hand"), `rest` is the request with the timing words taken out, and `note`
    explains a timing the scheduler cannot keep (monthly, for instance) so the
    draft can say so instead of guessing.
    """
    now = now or datetime.now()
    raw = (text or "").strip()
    t = raw.lower()
    spans = []
    note = ""

    def take(span):
        spans.append(span)

    if re.search(r"\b(every month|monthly|each month|once a month|first of the month)\b", t):
        note = ("Friday can't repeat things monthly yet. Pick a day of the week, "
                "or run it yourself when you need it.")

    tm = _find_time(t)
    if tm:
        take(tm[2])
    # A part of the day counts only when it is about timing ("every morning",
    # "in the evening", "tonight", "mornings"), not a noun in the task itself
    # ("summarize the morning news").
    part, part_today = None, False
    m = re.search(r"\b(?:(every|each|in the|this|at)\s+(morning|afternoon|evening|night)|(tonight)"
                  r"|(morning|afternoon|evening|night)s)\b", t)
    if m:
        word = m.group(2) or m.group(3) or m.group(4)
        part = _PART_OF_DAY[word]
        part_today = m.group(1) == "this" or word == "tonight"
        take(m.span())
    hour, minute = (tm[0], tm[1]) if tm else ((part, 0) if part is not None else (9, 0))

    def result(trigger, spec):
        return ({"trigger": trigger, "spec": spec}, _strip(raw, spans), note)

    # Every N minutes / hours.
    m = re.search(r"\b(?:every|each)\s+(\d+|[a-z]+)?\s*(minute|min|hour|hr)s?\b", t)
    if m:
        n = _num(m.group(1)) if m.group(1) else 1
        if n:
            take(m.span())
            mins = n * (60 if m.group(2) in ("hour", "hr") else 1)
            return result("interval", {"every_minutes": max(5, mins)})
    m = re.search(r"\bhourly\b", t)
    if m:
        take(m.span())
        return result("interval", {"every_minutes": 60})

    # Weekdays / weekends.
    m = re.search(r"\b(?:(?:every|each|on)\s+)?(?:week\s?days?|work\s?days?|business days|"
                  r"mon(?:day)?\s*(?:-|to|through|thru)\s*fri(?:day)?)\b", t)
    if m:
        take(m.span())
        return result("weekly", {"weekdays": [0, 1, 2, 3, 4], "hour": hour, "minute": minute})
    m = re.search(r"\b(?:(?:every|each|on)\s+)?weekends?\b", t)
    if m:
        take(m.span())
        return result("weekly", {"weekdays": [5, 6], "hour": hour, "minute": minute})

    # Named days: "every Monday and Thursday", "on Fridays", "on Friday".
    day_alt = "|".join(f"(?:{rx})" for rx in _DAY_RX.values())
    m = re.search(rf"\b(every|each|on)?\s*((?:{day_alt})(?:\s*(?:,|and|&|or)\s*(?:{day_alt}))*)\b", t)
    if m:
        days = [d for d, rx in _DAY_RX.items()
                if re.search(rf"\b{rx}\b", m.group(2))]
        last = m.group(2).split()[-1]
        plural = last.endswith("days") or (last.endswith("s") and last not in ("tues", "thurs"))
        recurring = m.group(1) in ("every", "each") or plural or len(days) > 1
        if days:
            take(m.span())
            if recurring:
                return result("weekly", {"weekdays": days, "hour": hour, "minute": minute})
            ahead = (days[0] - now.weekday()) % 7 or 7
            at = (now + timedelta(days=ahead)).replace(hour=hour, minute=minute,
                                                       second=0, microsecond=0)
            return result("once", {"at": int(at.timestamp())})

    m = re.search(r"\b(?:every|each|once a)\s+week\b|\bweekly\b", t)
    if m:
        take(m.span())
        return result("weekly", {"weekdays": [0], "hour": hour, "minute": minute})

    m = re.search(r"\b(?:every|each)\s+day\b|\bdaily\b|\bonce a day\b", t)
    if m:
        take(m.span())
        return result("daily", {"hour": hour, "minute": minute})

    # One-off: "tomorrow at 3pm", "today at 5", "tonight", "this afternoon".
    m = re.search(r"\b(today|tonight|tomorrow)\b", t)
    if m or part_today:
        if m:
            take(m.span())
        base = now + timedelta(days=1 if m and m.group(1) == "tomorrow" else 0)
        at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
        return result("once", {"at": int(at.timestamp())})

    if tm or part is not None:
        # A time with no day ("at 7am, check ...") repeats daily: nobody writes
        # an automation for a single time without saying which day.
        return result("daily", {"hour": hour, "minute": minute})
    return None, _strip(raw, spans), note


def _strip(raw, spans):
    """The request with its timing words removed, tidied into a sentence."""
    out = raw
    for a, b in sorted(spans, reverse=True):
        out = out[:a] + " " + out[b:]
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"^[\s,;:.-]*(?:(?:and|then)\b|,)?\s*", "", out, flags=re.I)
    out = re.sub(r"^(?:please|can you|could you|i want you to|i'd like you to)\s+", "", out, flags=re.I)
    out = out.strip(" ,;:-")
    return out[:1].upper() + out[1:] if out else raw.strip()


# ── Which steps will ask first ──────────────────────────────────────────────

# Each pattern looks for the ACT, not the topic: "summarize my email" reads
# mail, "the Washington Post" is a newspaper, "check my calendar" changes
# nothing. A false alarm here teaches the owner to ignore the warning.
_OBJ = r"(?:it|this|that|them|me|him|her|us|the|a|an|my|each|every|all|to|back)"
_PLATFORMS = r"(?:bluesky|linkedin|facebook|instagram|threads|mastodon|substack|twitter|x)"
_ASKS = [
    ("send email", rf"\be-?mail(?:s|ing)?\s+{_OBJ}\b|\b(?:send|forward)\w*\b[^.]{{0,40}}\be-?mails?\b"
                   r"|\b(?:reply|respond)(?:ing)?\s+to\b|\bwrite back\b"),
    ("post or publish online", rf"\b(?:post|tweet|publish|share)(?:s|ing)?\s+(?:it|this|that|them|the|a|an|my|our)\b"
                               rf"|\b(?:to|on)\s+{_PLATFORMS}\b"),
    ("send text messages", r"\b(?:text|message|slack|dm)\s+(?:him|her|them|me|the|my)\b|\bsend (?:a )?texts?\b"
                           r"|\b(?:sms|imessage|whatsapp)\b"),
    ("change your calendar or invite people", r"\binvite\b|\b(?:add|put)\b[^.]{0,30}\b(?:to|on) (?:my |the )?calendar\b"
                                              r"|\b(?:book|set up|schedule) (?:a |an )?(?:meeting|call|slot|interview)\b"
                                              r"|\breschedule\b|\bcreate (?:a |an )?(?:calendar )?event\b"),
    ("spend money", r"\b(?:buy|purchase|pay for|pay the|place an order|renew)\b"
                    r"|\border (?:a|an|some|two|three|\d+)\b|\bbook (?:a |an )?(?:flight|hotel|table|ticket|room)s?\b"),
    ("delete or move files", r"\b(?:delete|erase|wipe)\b|\bmove (?:the |my )?files\b"
                             r"|\bclean up (?:my |the )?(?:files|folders?|downloads|desktop)\b"),
    ("run programs on your computer", r"\brun (?:a |the |this )?(?:script|command|program)\b|\binstall\b"),
]


def asks_first(texts) -> list:
    """Plain names for the outward actions these words point to."""
    joined = " ".join(t for t in texts if t).lower()
    return [label for label, rx in _ASKS if re.search(rx, joined)]


# ── Drafting a workflow from plain language ─────────────────────────────────

_DRAFT_SYSTEM = (
    "You turn a person's plain-language request into a small automation for "
    "their assistant, Friday. Reply with JSON only, no prose, in this shape: "
    '{"name": "<3 to 6 word title>", "summary": "<one sentence, what it does, '
    'addressed to the person, e.g. \'Checks the council site and tells you what changed.\'>", '
    '"steps": [{"name": "<2 to 5 words>", "prompt": "<a clear instruction to Friday>"}]} '
    "Use one step unless the request clearly has separate stages; never more "
    "than 5. Each step's result is handed to the next. Leave timing out: the "
    "schedule is handled separately. Do not add steps the person did not ask "
    "for, and never add a step that sends, posts, buys or deletes unless the "
    "request asks for it."
)


def _title_from(text):
    words = re.findall(r"[A-Za-z0-9'’-]+", text or "")
    stop = {"the", "a", "an", "my", "me", "and", "to", "for", "of", "then", "please"}
    keep = [w for w in words if w.lower() not in stop][:5] or words[:5]
    title = " ".join(keep).strip()
    return (title[:1].upper() + title[1:])[:60] if title else "New workflow"


def _clean_steps(steps):
    out = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        prompt = str(s.get("prompt") or "").strip()
        if not prompt:
            continue
        out.append({"name": str(s.get("name") or "").strip()[:80] or f"Step {len(out) + 1}",
                    "prompt": prompt[:4000]})
        if len(out) >= MAX_STEPS:
            break
    return out


def _model_draft(request, generate):
    """Ask the configured model for a name, summary and steps. None if it
    cannot be reached or does not answer in the shape asked for."""
    try:
        raw = generate(
            [{"role": "user", "content": request}], system=_DRAFT_SYSTEM,
            max_tokens=1200, orb_label="Drafting a workflow", workspace="workflows")
    except Exception:
        return None
    m = re.search(r"\{.*\}", str(raw or ""), re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    steps = _clean_steps(data.get("steps"))
    if not steps or not str(data.get("name") or "").strip():
        return None
    return {"name": str(data["name"]).strip()[:60],
            "description": str(data.get("summary") or "").strip()[:300],
            "steps": steps}


def draft_from_text(text, *, generate=None, now=None) -> dict:
    """A reviewable draft for a plain-language request. Saves nothing."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Describe what you want Friday to do.")
    when, rest, note = parse_when(text, now=now)
    task_text = rest or text
    drafted = _model_draft(task_text, generate) if generate else None
    source = "model" if drafted else "simple"
    if not drafted:
        drafted = {"name": _title_from(task_text), "description": "",
                   "steps": [{"name": "Do the task", "prompt": task_text}]}
    return {
        "name": drafted["name"],
        "description": drafted["description"],
        "steps": drafted["steps"],
        "when": when,
        "when_text": describe_when(when),
        "when_note": note,
        "asks_first": asks_first([task_text] + [s["prompt"] for s in drafted["steps"]]),
        "source": source,
        "request": text,
    }


# ── The joined list ─────────────────────────────────────────────────────────

_RESULT = {"complete": "finished", "completed": "finished", "ok": "finished",
           "success": "finished", "done": "finished", "failed": "failed",
           "error": "failed", "timeout": "failed", "skipped": "skipped",
           "paused": "skipped", "running": "running", "interrupted": "stopped",
           "cancelled": "stopped"}


def _sched_last(rec):
    if not rec or not rec.get("last_run_ts"):
        return None
    status = "running" if rec.get("running") else _RESULT.get(
        str(rec.get("last_status") or "").lower(), str(rec.get("last_status") or "finished"))
    return {"at": float(rec["last_run_ts"]), "status": status,
            "summary": str(rec.get("last_summary") or "")[:300]}


def _chain_last(st):
    if not st:
        return None
    steps = st.get("steps") or []
    times = [float(s.get("ended") or s.get("started") or 0) for s in steps]
    at = max(times) if times else 0
    if not at:
        return None
    state = st.get("state")
    status = {"completed": "finished", "failed": "failed", "running": "running"}.get(
        state, "stopped")
    done = [s for s in steps if s.get("status") == "completed"]
    bad = next((s for s in steps if s.get("status") in ("failed", "interrupted", "cancelled")), None)
    if status == "finished":
        summary = (steps[-1].get("result_tail") or "").strip() if steps else ""
    elif bad:
        summary = f"Step {int(bad.get('index', 0)) + 1} ({bad.get('name')}): " + str(
            bad.get("reason") or bad.get("result_tail") or bad.get("status") or "").strip()
    elif status == "running":
        cur = next((s for s in steps if s.get("status") in ("running", "queued")), None)
        summary = f"On step {int(cur.get('index', 0)) + 1} of {len(steps)}: {cur.get('name')}" if cur else ""
    else:
        summary = f"Stopped after {len(done)} of {len(steps)} steps."
    return {"at": at, "status": status, "summary": summary[-300:],
            "steps": [{"name": s.get("name"), "status": s.get("status")} for s in steps]}


def _newest(*runs):
    runs = [r for r in runs if r]
    if not runs:
        return None
    best = max(runs, key=lambda r: r["at"])
    # A chain's own record knows its steps; keep them on whichever run wins.
    for r in runs:
        if r is not best and r.get("steps") and not best.get("steps") and abs(r["at"] - best["at"]) < 6 * 3600:
            best = dict(best, steps=r["steps"])
    return best


def _pending_approvals():
    try:
        from agent_friday.services import approvals as _ap
        return len(_ap.list_approvals(status="pending"))
    except Exception:
        return None


def overview() -> dict:
    """Everything the Workflows screen shows, joined into one list."""
    from agent_friday.services import agent as _agent
    from agent_friday.services import scheduler as _sched
    schedules = _sched.list_schedules()
    by_ref = {}
    for r in schedules:
        task = r.get("task") or {}
        if task.get("kind") == "workflow" and task.get("ref"):
            by_ref.setdefault(task["ref"], r)
    workflows, routines = [], []
    for c in _agent.list_workflow_chains():
        slug = c.get("slug")
        full = _agent.load_workflow_chain(slug) or {}
        steps = [{"name": s.get("name"), "prompt": s.get("prompt")} for s in full.get("steps") or []]
        rec = by_ref.get(slug)
        when = {"trigger": rec["trigger"], "spec": rec.get("spec") or {}} if rec else None
        try:
            st = _agent.chain_run_status(slug)
        except Exception:
            st = None
        last = _newest(_chain_last(st), _sched_last(rec))
        workflows.append({
            "id": "wf:" + slug, "slug": slug, "schedule_id": (rec or {}).get("id"),
            "name": c.get("name") or slug, "description": c.get("description") or "",
            "steps": steps, "when": when, "when_text": describe_when(when),
            "enabled": bool(rec.get("enabled", True)) if rec else True,
            "running": bool((st or {}).get("state") == "running" or (rec or {}).get("running")),
            "next_run": (rec or {}).get("next_run"), "last_run": last,
            "asks_first": asks_first(s["prompt"] for s in steps),
            "updated": c.get("updated"),
        })
    for r in schedules:
        task = r.get("task") or {}
        if task.get("kind") == "workflow":
            continue
        when = {"trigger": r.get("trigger"), "spec": r.get("spec") or {}}
        if r.get("source") == "builtin":
            routines.append({
                "schedule_id": r.get("id"), "name": r.get("name") or r.get("id"),
                "when_text": describe_when(when), "enabled": bool(r.get("enabled", True)),
                "running": bool(r.get("running")), "next_run": r.get("next_run"),
                "last_run": _sched_last(r),
            })
            continue
        prompt = task.get("prompt") or ""
        steps = [{"name": "Do the task", "prompt": prompt}] if prompt else []
        workflows.append({
            "id": "sch:" + str(r.get("id")), "slug": None, "schedule_id": r.get("id"),
            "name": r.get("name") or r.get("id"), "description": "",
            "steps": steps, "when": when, "when_text": describe_when(when),
            "enabled": bool(r.get("enabled", True)), "running": bool(r.get("running")),
            "next_run": r.get("next_run"), "last_run": _sched_last(r),
            "asks_first": asks_first([prompt]), "updated": r.get("updated"),
        })
    workflows.sort(key=lambda w: (not w["running"], -(w["last_run"] or {}).get("at", 0),
                                  (w["name"] or "").lower()))
    routines.sort(key=lambda r: (not r["enabled"], (r["name"] or "").lower()))
    return {"workflows": workflows, "routines": routines,
            "pending_approvals": _pending_approvals(), "now": _time.time()}


# ── Saving and deleting ─────────────────────────────────────────────────────

def _linked_schedule(slug, schedule_id):
    from agent_friday.services import scheduler as _sched
    if schedule_id:
        rec = _sched.get_schedule(schedule_id)
        if rec:
            return rec
    if slug:
        for r in _sched.list_schedules():
            t = r.get("task") or {}
            if t.get("kind") == "workflow" and t.get("ref") == slug:
                return r
    return None


def save(draft) -> dict:
    """Save a reviewed draft: the steps as a workflow, the timing as a
    schedule that runs it. Returns `{"slug", "schedule_id"}`.

    `slug` / `schedule_id` on the draft name what is being edited. A renamed
    workflow moves to its new name; a one-step scheduled prompt made by the
    older screen becomes a workflow the first time it is edited here.
    """
    from agent_friday.services import agent as _agent
    from agent_friday.services import scheduler as _sched
    draft = draft or {}
    name = str(draft.get("name") or "").strip()
    steps = _clean_steps(draft.get("steps"))
    if not name:
        raise ValueError("Give the workflow a name.")
    if not steps:
        raise ValueError("Add at least one step that says what Friday should do.")
    old_slug = draft.get("slug") or None
    new_slug = _agent._chain_slug(name)
    if new_slug != old_slug and _agent.load_workflow_chain(new_slug):
        raise ValueError(f"There's already a workflow called “{name}”. Pick another name.")
    rec = _linked_schedule(old_slug, draft.get("schedule_id"))
    if rec and rec.get("source") == "builtin":
        raise ValueError("Friday's built-in routines can be switched on or off, not edited.")
    when = draft.get("when") or None
    if when:
        trig = when.get("trigger")
        if trig not in ("daily", "weekly", "interval", "once"):
            raise ValueError("That timing isn't one Friday can keep.")
        if trig == "once" and float((when.get("spec") or {}).get("at") or 0) <= _time.time():
            raise ValueError("That time has already passed.")
    stored = _agent.save_workflow_chain({
        "name": name, "description": str(draft.get("description") or "").strip()[:300],
        "steps": [{"name": s["name"], "prompt": s["prompt"]} for s in steps]})
    slug = stored["slug"]
    if old_slug and old_slug != slug:
        _agent.delete_workflow_chain(old_slug)
    schedule_id = None
    if when:
        body = {"name": name, "trigger": when["trigger"], "spec": dict(when.get("spec") or {}),
                "task": {"kind": "workflow", "ref": slug}}
        if rec:
            patch = dict(body, enabled=bool(draft.get("enabled", rec.get("enabled", True))))
            if int(rec.get("timeout_seconds") or 0) < WORKFLOW_TIMEOUT_S:
                patch["timeout_seconds"] = WORKFLOW_TIMEOUT_S
            schedule_id = _sched.update_schedule(rec["id"], patch)["id"]
        else:
            schedule_id = _sched.register_schedule(dict(
                body, enabled=bool(draft.get("enabled", True)),
                notify="on_complete", timeout_seconds=WORKFLOW_TIMEOUT_S))["id"]
    elif rec:
        _sched.delete_schedule(rec["id"])
    return {"slug": slug, "schedule_id": schedule_id}


def delete(slug=None, schedule_id=None) -> bool:
    """Remove a workflow and the schedule that runs it."""
    from agent_friday.services import agent as _agent
    from agent_friday.services import scheduler as _sched
    rec = _linked_schedule(slug, schedule_id)
    if rec and rec.get("source") == "builtin":
        raise ValueError("Friday's built-in routines can be switched off, not deleted.")
    gone = False
    if rec:
        gone = _sched.delete_schedule(rec["id"]) or gone
    if slug:
        gone = _agent.delete_workflow_chain(slug) or gone
    return gone
