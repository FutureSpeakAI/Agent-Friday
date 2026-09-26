"""The working ledger of a long task: what it is for and how far it has got.

A long local job outlives its context window many times over. Compaction keeps
the window bounded, but a summary written fresh each time is only as good as
the one turn that wrote it, and a crash or reboot loses the window entirely.
The ledger is the part that must never be lost:

  goal       the task as it was given
  plan       the steps the model has said it will take
  done       every tool step taken, one line each (all of them on disk)
  facts      figures, identifiers, findings the rest of the job depends on
  files      paths the job has read or written
  next       the step the job was about to take

It is written two ways. Mechanically, after every tool round: the step line
and any file path in the arguments -- nothing the model has to remember to do.
And from each compaction summary, which the seat is asked to write in these
sections: its PLAN / FACTS / NEXT replace the ledger's, so the ledger is
always the latest consolidated understanding.

It is shown to the model as the compaction summary block (pinned right after
the task, the one place the transcript is rewritten anyway), so a local seat's
prompt cache is not invalidated every round. And it is stored in the task's
journal directory through `task_journal.write_blob` -- encrypted at rest like
the rest of the journal -- so a resumed task gets it back after a crash,
restart or reboot.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Dict, List, Optional

BLOB = "ledger.json"
VERSION = 1
_LOCK = threading.Lock()

# What the pinned view carries. The full record stays on disk; the view has
# to fit a seat's window alongside the work.
_VIEW_RECENT_STEPS = 12
_VIEW_MAX_FACTS = 60
_VIEW_MAX_FILES = 40

_PATH_KEYS = ("path", "file", "file_path", "filename", "dest", "destination",
              "target", "output_path", "src", "source_path", "directory", "dir")
# Group 2 keeps the whitespace after the colon; absorb_summary strips it. A
# `\s*` before `(.*)` would give two quantifiers the same spaces to fight over.
_SECTION = re.compile(r"^\s*(GOAL|PLAN|DONE|FACTS|FILES|NEXT)\s*:(.*)$", re.I)


def _journal():
    from agent_friday.services import task_journal as _tj
    return _tj


def new(goal: str) -> Dict[str, Any]:
    return {"version": VERSION, "goal": (goal or "").strip()[:4000], "plan": [],
            "done": [], "facts": [], "files": [], "next": "", "rounds": 0,
            "compactions": 0, "updated": time.time()}


# One live object per task, so the loop (compaction absorbing summaries) and
# the tool hooks (recording steps) update the same ledger instead of each
# saving a stale copy over the other's.
_LIVE: Dict[str, Dict[str, Any]] = {}


def load(task_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not task_id:
        return None
    with _LOCK:
        if task_id in _LIVE:
            # Only while the record still exists: a deleted task (journal
            # delete, retention) must not come back from the cache.
            try:
                if _journal().blob_exists(task_id, BLOB):
                    return _LIVE[task_id]
            except Exception:
                pass
            _LIVE.pop(task_id, None)
    try:
        led = _journal().read_blob(task_id, BLOB)
    except Exception:
        led = None
    if isinstance(led, dict) and led.get("version") == VERSION:
        with _LOCK:
            _LIVE.setdefault(task_id, led)
            return _LIVE[task_id]
    return None


def forget(task_id: Optional[str]) -> None:
    """Drop the live copy (the task ended; the record stays on disk)."""
    with _LOCK:
        _LIVE.pop(task_id or "", None)


def save(task_id: Optional[str], ledger: Dict[str, Any]) -> bool:
    if not task_id or not ledger:
        return False
    ledger["updated"] = time.time()
    try:
        with _LOCK:
            return bool(_journal().write_blob(task_id, BLOB, ledger))
    except Exception:
        return False


def ensure(task_id: Optional[str], goal: str) -> Optional[Dict[str, Any]]:
    """The task's ledger, created from its goal the first time."""
    if not task_id:
        return None
    led = load(task_id)
    if led is None:
        led = new(goal)
        with _LOCK:
            led = _LIVE.setdefault(task_id, led)
        save(task_id, led)
    return led


def note_pending(task_id: Optional[str], name: str, args: Any) -> None:
    """A tool is about to run. If the process dies before `note_tool`, a
    resume knows which step was in flight and whether it is safe to repeat."""
    led = load(task_id)
    if led is None:
        return
    led["pending"] = {"name": str(name), "args": _one_line(tier_safe(_one_line(args, 600)), 300),
                      "at": time.time()}
    save(task_id, led)


def note_tool(task_id: Optional[str], name: str, args: Any, result: Any) -> None:
    """A tool finished (or was denied): record the step and clear pending."""
    led = load(task_id)
    if led is None:
        return
    record_step(led, name, args, result)
    led["pending"] = None
    save(task_id, led)


def goal_of(messages) -> str:
    """The task as given: the first user message's text."""
    for m in messages or []:
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


def _paths_in(args: Any) -> List[str]:
    out = []
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return out
    if isinstance(args, dict):
        for k, v in args.items():
            if k.lower() in _PATH_KEYS and isinstance(v, str) and v.strip():
                out.append(v.strip()[:300])
    return out


def _one_line(value: Any, cap: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text = " ".join(str(text).split())
    return text if len(text) <= cap else text[:cap] + "…"


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _aba_ok(digits: str) -> bool:
    w = (3, 7, 1) * 3
    return sum(int(c) * k for c, k in zip(digits, w)) % 10 == 0


def tier_safe(text: str) -> str:
    """`text` with structured personal data replaced by placeholders, by the
    sensitivity classifier's own Layer 1a patterns (local, deterministic).

    Step lines are pinned into every later request and handed to every
    summary, so an SSN or a card number a tool returned would otherwise ride
    along with the whole task. Card and routing numbers are redacted only
    when their checksum holds: a 13-digit timestamp or a 9-digit order number
    is exactly what a resume needs to see."""
    try:
        from agent_friday.services import sensitivity_classifier as sc
    except Exception:
        return text
    s = str(text)
    s = sc._API_KEY_RE.sub("[redacted key]", s)
    s = sc._SSN_RE.sub("[redacted SSN]", s)
    s = sc._CC_RE.sub(lambda m: "[redacted card number]"
                      if _luhn_ok(re.sub(r"\D", "", m.group(0))) else m.group(0), s)
    s = sc._ROUTING_RE.sub(lambda m: "[redacted routing number]"
                           if _aba_ok(m.group(0)) else m.group(0), s)
    for pat, word in ((sc._ACCT_TAIL_RE, "account number"), (sc._ISSUED_ID_RE, "ID number"),
                      (sc._ADDRESS_RE, "address")):
        s = pat.sub("[redacted %s]" % word, s)
    s = sc._PHONE_RE.sub(lambda m: m.group(0) if (m.group(1) or m.group(2)) in sc._TOLLFREE_AREA
                         else "[redacted phone]", s)
    return s


def record_step(ledger: Dict[str, Any], name: str, args: Any, result: Any) -> None:
    """One tool round, mechanically: the step line and any file paths."""
    if ledger is None:
        return
    ledger["rounds"] = int(ledger.get("rounds") or 0) + 1
    # Distinct steps: the same call with the same arguments again is not
    # progress, and a job that only repeats itself must be seen to.
    import hashlib as _hl
    sig = _hl.sha256((str(name) + "|" + _one_line(args, 2000)).encode("utf-8", "replace")).hexdigest()[:16]
    seen = ledger.setdefault("step_sigs", [])
    if sig not in seen:
        seen.append(sig)
    ledger["distinct_steps"] = len(seen)
    ledger.setdefault("done", []).append(
        "%d. %s(%s) -> %s" % (ledger["rounds"], name, _one_line(tier_safe(_one_line(args, 400)), 160),
                              _one_line(tier_safe(_one_line(result, 600)), 200)))
    files = ledger.setdefault("files", [])
    for p in _paths_in(args):
        if p not in files:
            files.append(p)


def absorb_summary(ledger: Dict[str, Any], summary: str) -> None:
    """Take PLAN / FACTS / NEXT (and any FILES) from a structured summary.

    Sections the summary did not provide keep their previous value, so a
    terse summary never erases what an earlier one established."""
    if ledger is None or not summary:
        return
    sections: Dict[str, List[str]] = {}
    current = None
    for line in summary.splitlines():
        m = _SECTION.match(line)
        if m:
            current = m.group(1).upper()
            sections.setdefault(current, [])
            rest = m.group(2).strip()
            if rest:
                sections[current].append(rest)
        elif current and line.strip():
            sections[current].append(line.strip().lstrip("-*• ").strip())
    if sections.get("PLAN"):
        ledger["plan"] = sections["PLAN"][:40]
    if sections.get("FACTS"):
        # Replaced, not appended: the summarizer is handed the previous
        # ledger and restates every fact it still needs. Appending would
        # stack each restatement on the last until the pinned view alone
        # outgrew a small seat's window.
        ledger["facts"] = [f for f in sections["FACTS"] if f][:400]
    if sections.get("FILES"):
        files = ledger.setdefault("files", [])
        for f in sections["FILES"]:
            if f and f not in files:
                files.append(f)
    if sections.get("NEXT"):
        ledger["next"] = " ".join(sections["NEXT"])[:600]
    if not sections:
        # An unstructured summary is kept alongside the facts, never in place
        # of them: prose without the FACTS section says nothing about which
        # facts it meant to drop, and a refusal or an error message would
        # otherwise have erased them all.
        facts = ledger.setdefault("facts", [])
        facts.append(_one_line(summary, 4000))
        del facts[:-400]
    ledger["compactions"] = int(ledger.get("compactions") or 0) + 1
    ledger["summarized_rounds"] = int(ledger.get("rounds") or 0)


def render(ledger: Optional[Dict[str, Any]], max_chars: Optional[int] = None) -> str:
    """The pinned view: everything a model needs to continue the job.

    `max_chars` bounds it to what the seat can carry alongside the work; the
    oldest step lines go first, then the oldest facts. GOAL and NEXT stay."""
    if not ledger:
        return ""
    text = _render(ledger, None)
    if not max_chars or len(text) <= max_chars:
        return text
    for keep_steps in (40, 12, 4, 0):
        text = _render(ledger, keep_steps)
        if len(text) <= max_chars:
            return text
    head, _, tail = text.partition("\nFACTS:")
    budget = max(0, max_chars - len(head) - 200)
    return head + ("\nFACTS (latest, trimmed to fit):\n" + tail[-budget:] if budget else "")


def _render(ledger: Dict[str, Any], keep_steps: Optional[int]) -> str:
    done = ledger.get("done") or []
    lines = ["[Task Ledger] This is your working record for this task; it survives "
             "context compaction and restarts. Continue from NEXT; do not redo DONE steps.",
             "GOAL: " + (ledger.get("goal") or "")]
    if ledger.get("plan"):
        lines.append("PLAN:")
        lines += ["- " + p for p in ledger["plan"]]
    # Every step since the last summary is shown: those have not been folded
    # into FACTS yet, and after a crash the pinned ledger is all that remains
    # of them. Older steps are represented by FACTS.
    since = int(ledger.get("summarized_rounds") or 0)
    recent = done[since:] if since < len(done) else []
    if len(recent) < _VIEW_RECENT_STEPS:
        recent = done[-_VIEW_RECENT_STEPS:]
    if keep_steps is not None:
        recent = recent[-keep_steps:] if keep_steps else []
    lines.append("DONE: %d step(s)%s" % (len(done), "; latest:" if recent else ""))
    lines += ["  " + d for d in recent]
    facts = ledger.get("facts") or []
    if facts:
        lines.append("FACTS:")
        lines += ["- " + f for f in facts[-_VIEW_MAX_FACTS:]]
    files = ledger.get("files") or []
    if files:
        lines.append("FILES: " + ", ".join(files[-_VIEW_MAX_FILES:]))
    if ledger.get("next"):
        lines.append("NEXT: " + ledger["next"])
    return "\n".join(lines)


def carried_text(ledger: Optional[Dict[str, Any]]) -> str:
    """Everything the ledger carries forward EXCEPT its goal and run record:
    the parts written from what the task read (steps, facts, plan, files,
    next). This is what the taint record registers as outside content; the
    goal is the task's own instruction and is not."""
    if not ledger:
        return ""
    parts = []
    for key in ("plan", "facts", "files"):
        parts.extend(str(x) for x in (ledger.get(key) or []))
    parts.extend(str(x) for x in (ledger.get("done") or []))
    for key in ("next", "pending"):
        if ledger.get(key):
            parts.append(str(ledger[key]))
    return "\n".join(p for p in parts if p.strip())


def continuation_prompt(goal: str, ledger: Optional[Dict[str, Any]], why: str,
                        max_chars: Optional[int] = None) -> str:
    """The first message of a fresh leg: the task, its ledger, and why a new
    leg started (a per-turn limit, a restart). No question for the user --
    the job carries on."""
    return ("%s\n\n%s\n\n[Continuing this task: %s. The ledger above is your "
            "record of the work so far. Pick up at NEXT, do not repeat DONE "
            "steps, and finish the task.]" % (goal or (ledger or {}).get("goal") or "",
                                              render(ledger, max_chars), why))


def remember_run(ledger: Optional[Dict[str, Any]], **run) -> None:
    """How the task was started (name, model, tool names, icon), so a resume
    after a restart runs it the same way."""
    if ledger is None:
        return
    ledger.setdefault("run", {}).update({k: v for k, v in run.items() if v is not None})


SUMMARY_SECTIONS = (
    "Write the note in exactly these sections, each on its own line:\n"
    "PLAN: the remaining steps, one per line\n"
    "FACTS: every figure, identifier, finding and decision the rest of the task "
    "needs, one per line, exact values\n"
    "FILES: paths read or written, one per line\n"
    "NEXT: the single next step\n")
