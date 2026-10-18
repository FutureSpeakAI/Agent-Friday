"""A record for everything Friday learns: when it applies, what taught it, and
how it has done since.

Three kinds of learned thing get a record:

``skill``      a skill Friday wrote with ``learn_skill`` (ref: its folder name).
``lesson``     a line in FRIDAY.md, Friday's own lessons on the core memory card
               (ref: the SHA-256 of the line; the text itself is NOT copied
               here, so an owner delete leaves nothing behind, see ``purge``).
``heuristic``  an active or candidate learning-loop heuristic (learning.db). Its
               record is read through from learning_loop, never copied.

Each record says:

* ``applies_when``  the cue: a skill's triggers and description, a lesson's
  "when" (or "every conversation" when none was given), a heuristic's task type;
* ``taught_by``     a pointer to what taught it, taken from the call's own
  context, never from the model's arguments: conversation id, turn id, task id,
  surface, model, and who wrote it (friday, review, dream);
* ``uses`` / ``outcomes``  how it has done since: a skill counts a use when its
  full procedure is loaded in a conversation (``loaded``, read_skill) or when
  a background task's request matches its triggers so it is offered to Friday
  (``offered``); a task's verified/unverified end is its outcome (conversations
  have no outcome signal yet, so their uses stay unjudged); a lesson is in
  every prompt (``exposure: always_on``), so it counts every finished task
  since it was added; a heuristic carries the learning loop's trials and wins;
* ``replaces`` / ``replaced_by``  the lesson a reworded one replaced, kept as a
  retired record so an old-vs-new comparison has both sides.

``comparison(a, b)`` puts two records side by side (uses, success rate, Wilson
lower bound, whether exposure is attributable). It decides nothing: it is the
input for a future "keep the old version or the new one" card the owner sees.

Store: ``~/.friday/learning_records/records.json`` (atomic replace, a lock).
Leaf module: no Flask, never raises to a caller.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.paths import friday_home

KINDS = ("skill", "lesson", "heuristic")
STATUSES = ("proposed", "active", "retired", "archived", "replaced")
#: Where a helped / hurt / neutral judgement may come from (spec HA-E26):
#: a task's deterministic verification, the owner's explicit feedback, or a
#: practice-task trial. Never a guess from the shape of a reply.
JUDGE_SOURCES = ("verify", "owner", "trial")
VERDICTS = ("helped", "hurt", "neutral")
#: Retirement card: at least this many judged applications (helped + hurt)...
RETIRE_MIN_JUDGED = 6
#: ...and the Wilson upper bound (z=1.96) of helped/(helped+hurt) below this.
#: The earliest trigger is 0 helped in 6 (upper bound 0.39).
RETIRE_WILSON_UPPER = 0.40
#: A lesson judged only "neutral" this many times is offered for archiving.
NEUTRAL_ARCHIVE_AFTER = 20
#: Lessons injected into a turn when only the ones that apply are used.
LESSON_TOP = 3
LESSON_BUDGET_CHARS = 1200
RETIRE_KIND = "lesson_retire"
#: Open (unfinished) contexts kept per record, oldest dropped first.
OPEN_CONTEXTS_MAX = 50
#: Records kept per kind (retired ones are dropped oldest first past this).
RECORDS_MAX = 2000
_TEXT_MAX = 300

_LOCK = threading.RLock()


def _path() -> Path:
    return friday_home() / "learning_records" / "records.json"


def lesson_ref(text: str) -> str:
    """The ref of a FRIDAY.md line: a hash of its cleaned text."""
    try:
        from agent_friday.services.core_memory import _clean
        text = _clean(text)
    except Exception:
        text = " ".join(str(text or "").split())
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:24]


def record_id(kind: str, ref: str) -> str:
    return f"{kind}:{ref}"


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("records"), dict):
            return data
    except (FileNotFoundError, OSError, ValueError):
        pass
    return {"version": 1, "records": {}}


def _save(data: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _short(v) -> Optional[str]:
    s = " ".join(str(v if v is not None else "").split())[:_TEXT_MAX]
    return s or None


#: A cue ("applies when ...") is the model's own free text and is served by
#: /api/learning/records, so it is kept short and stripped of contact details
#: and of anything that looks like a name or a place: every capitalised word
#: (any script) except a short list of ordinary words, wherever it stands. A
#: name written in lower case is not caught; the cue is also capped at
#: CUE_MAX characters. The lesson text is never stored.
CUE_MAX = 160
_CUE_SCRUB = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\+?\d[\d ()./-]{6,}\d|https?://\S+")
_CUE_WORD = re.compile(r"(?<![\w'\u2019-])[^\W\d_][\w'\u2019-]*")
#: Capitalised words that are not names: Friday herself, days, months, a few
#: abbreviations, and the ordinary words a cue or a sentence starts with.
_CUE_KEEP = frozenset({
    "I", "Friday", "Monday", "Tuesday", "Wednesday", "Thursday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "AM", "PM", "OK", "PDF", "UTC", "CSV", "AI",
    "When", "Whenever", "If", "Before", "After", "While", "During", "Until", "Once", "For",
    "On", "In", "At", "By", "To", "From", "With", "Without", "About", "The", "A", "An",
    "Any", "Every", "Each", "All", "Some", "This", "That", "These", "Those", "My", "Your",
    "Always", "Never", "Only", "Not", "No", "Do", "Don't", "Then", "And", "Or", "But",
    "Applies", "Use", "Ask", "Check", "Prefer", "Keep", "Make", "Send", "Reply", "Write",
    "Read", "Open", "Find", "Look", "Say", "Tell", "Show", "Try", "Avoid", "Wait"})


def _scrub_cue(v) -> Optional[str]:
    s = _CUE_SCRUB.sub("[removed]", " ".join(str(v if v is not None else "").split()))

    def name(m):
        w = m.group(0)
        if not w[0].isupper() or w in _CUE_KEEP:
            return w
        return "[name]"
    s = _CUE_WORD.sub(name, s)
    return s[:CUE_MAX] or None


def current_taught_by(source: str = "friday") -> Dict[str, Any]:
    """Where a learned thing came from, read from the running tool call's own
    context (agent's context variables and the turn id). Identifiers only."""
    out: Dict[str, Any] = {"source": source}
    try:
        from agent_friday.services import agent
        ctx = agent._CURRENT_TOOL_CONTEXT.get() or {}
        out["conversation"] = agent._CURRENT_CONVERSATION.get() or ctx.get("conversation_id")
        out["task"] = ctx.get("task_id")
        out["surface"] = agent._CURRENT_SURFACE.get() or None
        out["model"] = agent._CURRENT_MODEL.get() or None
    except Exception:
        pass
    try:
        from agent_friday.services import turn_plan
        out["turn"] = turn_plan.current_turn_id()
    except Exception:
        pass
    return {k: (_short(v) if k != "source" else v) for k, v in out.items() if v not in (None, "")}


def context_key(taught_by: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The unit an outcome is judged on: the task when there is one, else the
    conversation."""
    tb = taught_by if taught_by is not None else current_taught_by()
    if tb.get("task"):
        return f"task:{tb['task']}"
    if tb.get("conversation"):
        return f"conversation:{tb['conversation']}"
    return None


def _blank(kind: str, ref: str, now: float) -> Dict[str, Any]:
    return {"id": record_id(kind, ref), "kind": kind, "ref": ref, "status": "active",
            "applies_when": None, "taught_by": {}, "created": now, "updated": now,
            "replaces": None, "replaced_by": None,
            "exposure": "always_on" if kind == "lesson" else "on_match",
            "revisions": 0, "uses": 0, "loaded": 0, "offered": 0, "last_used": None,
            "outcomes": {"success": 0, "failure": 0}, "open": {},
            # harness2 adoption HA3: what the record is about, the trials that
            # tested it, and whether each judged use helped or hurt.
            "tags": [], "trial_refs": [], "applied": 0,
            "judged": {"helped": 0, "hurt": 0, "neutral": 0}}


def _ensure_ha3(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Records written before HA3 gain its fields as empty (read as version 1)."""
    rec.setdefault("tags", [])
    rec.setdefault("trial_refs", [])
    rec.setdefault("applied", 0)
    j = rec.get("judged") if isinstance(rec.get("judged"), dict) else {}
    rec["judged"] = {k: int(j.get(k) or 0) for k in ("helped", "hurt", "neutral")}
    return rec


def _trim(records: Dict[str, Any]) -> None:
    for kind in KINDS:
        rows = [r for r in records.values() if r.get("kind") == kind]
        if len(rows) <= RECORDS_MAX:
            continue
        retired = sorted((r for r in rows if r.get("status") != "active"),
                         key=lambda r: r.get("updated") or 0)
        for r in retired[:len(rows) - RECORDS_MAX]:
            records.pop(r["id"], None)


def note_taught(kind: str, ref: str, *, applies_when: Any = None,
                taught_by: Optional[Dict[str, Any]] = None,
                replaces: Optional[str] = None, tags: Optional[List[str]] = None,
                status: Optional[str] = None) -> Dict[str, Any]:
    """A new learned thing, or a revision of one that already has a record
    (the original teacher is kept; the revision count goes up).

    ``status="proposed"`` marks a lesson that waits for the owner's click
    (written after Friday read untrusted content); it is not active until
    the owner keeps it."""
    if kind not in KINDS or not ref:
        return {"ok": False, "error": "unknown kind or empty ref"}
    try:
        with _LOCK:
            data = _load()
            recs = data["records"]
            now = time.time()
            rid = record_id(kind, ref)
            rec = recs.get(rid)
            if rec is None:
                rec = _blank(kind, ref, now)
                rec["taught_by"] = dict(taught_by or current_taught_by())
                recs[rid] = rec
            else:
                rec["revisions"] = int(rec.get("revisions") or 0) + 1
                rec["status"] = "active"
                rec["revised_by"] = dict(taught_by or current_taught_by())
            _ensure_ha3(rec)
            if status in STATUSES:
                rec["status"] = status
            if tags:
                rec["tags"] = _clean_tags(list(rec.get("tags") or []) + list(tags))
            if applies_when is not None:
                rec["applies_when"] = _scrub_cue(applies_when)
            if replaces and replaces != rid:
                rec["replaces"] = replaces
                old = recs.get(replaces)
                if old is not None:
                    old["status"] = "replaced"
                    old["replaced_by"] = rid
                    old["updated"] = now
            rec["updated"] = now
            _trim(recs)
            _save(data)
            return {"ok": True, "id": rid}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def note_status(kind: str, ref: str, status: str) -> Dict[str, Any]:
    """active | retired | archived | replaced. A record is never deleted by
    this (see purge for the owner's delete)."""
    try:
        with _LOCK:
            data = _load()
            rec = data["records"].get(record_id(kind, ref))
            if rec is None:
                return {"ok": True, "missing": True}
            rec["status"] = str(status)
            rec["updated"] = time.time()
            _save(data)
            return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def note_use(kind: str, ref: str, context: Optional[str] = None) -> Dict[str, Any]:
    """The learned thing was put to use (a skill's procedure was loaded). The
    context is remembered so the task's outcome can be credited to it."""
    try:
        with _LOCK:
            data = _load()
            rec = data["records"].get(record_id(kind, ref))
            if rec is None:
                return {"ok": True, "missing": True}
            now = time.time()
            rec["uses"] = int(rec.get("uses") or 0) + 1
            rec["loaded"] = int(rec.get("loaded") or 0) + 1
            rec["last_used"] = now
            ctx = context if context is not None else context_key()
            if ctx:
                opened = dict(rec.get("open") or {})
                opened[ctx] = now
                if len(opened) > OPEN_CONTEXTS_MAX:
                    for k in sorted(opened, key=opened.get)[:len(opened) - OPEN_CONTEXTS_MAX]:
                        opened.pop(k, None)
                rec["open"] = opened
            _save(data)
            return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def note_outcome(context: Optional[str], success: bool, *, checked: bool = False) -> Dict[str, Any]:
    """A task (or conversation) finished. Every record used in it gets the
    outcome once; every active always-on lesson counts it as one more task it
    was in the prompt for.

    ``checked``: the outcome came from deterministic verification
    (workflow_outcomes). Then each record that was APPLIED in this context
    (attributable, not merely always on) is also judged helped or hurt."""
    if not context:
        return {"ok": True, "credited": 0}
    try:
        with _LOCK:
            data = _load()
            credited = 0
            key = "success" if success else "failure"
            for rec in data["records"].values():
                opened = dict(rec.get("open") or {})
                in_context = context in opened
                always_on = rec.get("exposure") == "always_on" and rec.get("status") == "active"
                if not (in_context or always_on):
                    continue
                if in_context:
                    opened.pop(context, None)
                    rec["open"] = opened
                else:
                    # In the prompt for this task without a use of its own.
                    rec["uses"] = int(rec.get("uses") or 0) + 1
                outcomes = dict(rec.get("outcomes") or {"success": 0, "failure": 0})
                outcomes[key] = int(outcomes.get(key) or 0) + 1
                rec["outcomes"] = outcomes
                if checked and in_context:
                    j = _ensure_ha3(rec)["judged"]
                    j["helped" if success else "hurt"] += 1
                rec["updated"] = time.time()
                credited += 1
            if credited:
                _save(data)
            return {"ok": True, "credited": credited}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def note_task_outcome(task_id: str, prompt: str, success: bool, *, checked: bool = False) -> Dict[str, Any]:
    """A background task finished. Skills Friday wrote whose triggers match the
    task's request were offered to her for it (the prompt names them), so each
    counts one use ("offered") and gets the outcome; then everything loaded in
    the task and every always-on lesson is credited (note_outcome). A record
    is credited at most once per task."""
    if not task_id:
        return {"ok": True, "credited": 0}
    ctx = f"task:{task_id}"
    try:
        from agent_friday import skill_registry
        offered = [s.name for s in skill_registry.match_skills(prompt or "", limit=3)
                   if skill_registry.friday_wrote(s)]
    except Exception:
        offered = []
    try:
        with _LOCK:
            data = _load()
            now = time.time()
            for name in offered:
                rec = data["records"].get(record_id("skill", name))
                if rec is None:
                    continue
                rec["offered"] = int(rec.get("offered") or 0) + 1
                opened = dict(rec.get("open") or {})
                if ctx not in opened:
                    rec["uses"] = int(rec.get("uses") or 0) + 1
                    rec["last_used"] = now
                    opened[ctx] = now
                rec["open"] = opened
            if offered:
                _save(data)
    except Exception:
        pass
    return note_outcome(ctx, success, checked=checked)


# ── harness2 adoption HA3: applicability, judged outcomes, retirement ───────

def _clean_tags(tags) -> List[str]:
    out: List[str] = []
    for t in tags or []:
        s = " ".join(str(t or "").split()).casefold()[:60]
        if s and s not in out:
            out.append(s)
    return out[:20]


def _update(kind: str, ref: str, fn) -> Dict[str, Any]:
    try:
        with _LOCK:
            data = _load()
            rec = data["records"].get(record_id(kind, ref))
            if rec is None:
                return {"ok": True, "missing": True}
            _ensure_ha3(rec)
            out = fn(rec) or {}
            rec["updated"] = time.time()
            _save(data)
            return {"ok": True, **out}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def note_tags(kind: str, ref: str, tags: List[str]) -> Dict[str, Any]:
    """Domains, tools and skill names the record is about, for deterministic matching."""
    def fn(rec):
        rec["tags"] = _clean_tags(list(rec.get("tags") or []) + list(tags or []))
    return _update(kind, ref, fn)


def note_applied(kind: str, ref: str, context: Optional[str] = None) -> Dict[str, Any]:
    """The record was injected into a turn or task because it applied. Local,
    no text; the context is kept so a checked outcome can judge it."""
    def fn(rec):
        rec["applied"] = int(rec.get("applied") or 0) + 1
        rec["uses"] = int(rec.get("uses") or 0) + 1
        rec["last_used"] = time.time()
        ctx = context if context is not None else context_key()
        if ctx:
            opened = dict(rec.get("open") or {})
            opened[ctx] = time.time()
            if len(opened) > OPEN_CONTEXTS_MAX:
                for k in sorted(opened, key=opened.get)[:len(opened) - OPEN_CONTEXTS_MAX]:
                    opened.pop(k, None)
            rec["open"] = opened
    return _update(kind, ref, fn)


def judge(kind: str, ref: str, verdict: str, *, source: str) -> Dict[str, Any]:
    """One judged application: helped, hurt or neutral. Only from deterministic
    verification, the owner's explicit feedback, or a trial (HA-E26)."""
    if source not in JUDGE_SOURCES:
        return {"ok": False, "error": f"a judgement must come from {', '.join(JUDGE_SOURCES)}"}
    if verdict not in VERDICTS:
        return {"ok": False, "error": "verdict is helped, hurt or neutral"}

    def fn(rec):
        rec["judged"][verdict] += 1
    return _update(kind, ref, fn)


def note_trial(kind: str, ref: str, trial_id: str, recommend: str, per_task: Dict[str, dict],
               *, with_arm: str = "b") -> Dict[str, Any]:
    """A practice-task trial of this record: its id and verdict, and one judged
    application per task. ``with_arm`` is the arm that ran WITH the skill or
    lesson ("b" for a new or changed one, "a" when the trial tests archiving
    it): a task favouring that arm counts as helped, the other arm as hurt, a
    tie as neutral. The trial's own paired result is the strongest evidence."""
    if with_arm not in ("a", "b"):
        raise ValueError("with_arm must be 'a' or 'b'")
    without = "a" if with_arm == "b" else "b"

    def fn(rec):
        refs = list(rec.get("trial_refs") or [])
        refs.append({"trial": str(trial_id)[:40], "recommend": str(recommend)[:20],
                     "with_arm": with_arm, "at": time.time()})
        rec["trial_refs"] = refs[-20:]
        for t in (per_task or {}).values():
            fav = t.get("favours")
            rec["judged"]["helped" if fav == with_arm else "hurt" if fav == without else "neutral"] += 1
    return _update(kind, ref, fn)


def wilson_upper(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return round(min(1.0, (centre + margin) / denom), 4)


def confidence(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Computed from the judged counts, never from a model's own claim."""
    j = _ensure_ha3(dict(rec))["judged"]
    n = j["helped"] + j["hurt"]
    return {"helped": j["helped"], "hurt": j["hurt"], "neutral": j["neutral"], "judged": n,
            "wilson_lower": _wilson(j["helped"], n), "wilson_upper": wilson_upper(j["helped"], n)}


def retirement_due(rec: Dict[str, Any]) -> bool:
    c = confidence(rec)
    return (rec.get("status") == "active" and c["judged"] >= RETIRE_MIN_JUDGED
            and c["wilson_upper"] < RETIRE_WILSON_UPPER)


def archive_offer_due(rec: Dict[str, Any]) -> bool:
    c = confidence(rec)
    return rec.get("status") == "active" and c["judged"] == 0 and c["neutral"] >= NEUTRAL_ARCHIVE_AFTER


def raise_retirement_cards() -> List[Dict[str, Any]]:
    """One "retire this?" card per learned thing that keeps hurting more than it
    helps. Retiring archives it; nothing is deleted. Idempotent per record."""
    out = []
    try:
        from agent_friday.services import approvals
    except Exception:
        return out
    register_hooks()
    for rec in records():
        if rec.get("kind") not in ("lesson", "skill") or not retirement_due(rec):
            continue
        c = confidence(rec)
        what = "lesson" if rec["kind"] == "lesson" else f"skill '{rec.get('ref')}'"
        card = approvals.create_approval(
            kind=RETIRE_KIND, subject_type="learning_record", subject_id=rec["id"],
            title=f"Retire a {what} that is not helping?",
            description=(f"Judged {c['judged']} times: helped {c['helped']}, hurt {c['hurt']} "
                         f"(neutral {c['neutral']}). Retiring archives it; nothing is deleted, "
                         "and you can bring it back."),
            action_description=f"Archive a learned {rec['kind']}",
            payload={"record": rec["id"], "quiet_until_digest": True},
            requested_by="lesson_records", action_class="internal", force_gate=True)
        out.append(card)
    return out


def archive_offers() -> List[Dict[str, Any]]:
    """Lessons that never made a difference either way, for the weekly summary."""
    return [summary(r) for r in records("lesson") if archive_offer_due(r)]


def _archive_lesson_line(ref: str) -> bool:
    """Take the line off the core memory card through core_memory itself, as a
    non-owner change: its journal keeps the text, so Undo brings it back."""
    from agent_friday.services import core_memory
    for line in core_memory.entries("friday"):
        if lesson_ref(line) == ref:
            r = core_memory.remove("friday", line, actor="retired")
            return bool(r.get("ok"))
    return False


def _on_retire_decided(card: Dict[str, Any]) -> None:
    if card.get("status") != "approved":
        return
    # Only the owner's click on the card retires anything (the kind is
    # screen-only in approvals); any other decider changes nothing.
    if card.get("decided_by") != "owner:ui":
        return
    rid = (card.get("payload") or {}).get("record") or ""
    kind, _, ref = rid.partition(":")
    if kind == "lesson":
        _archive_lesson_line(ref)
        note_status("lesson", ref, "archived")
    elif kind == "skill":
        try:
            from agent_friday import skill_registry
            skill_registry.archive_skill(ref, actor="owner", reason="retired by the owner's card")
        except Exception:
            return
        note_status("skill", ref, "archived")


def select_lessons(entries: List[str], message: str, tags: List[str],
                   *, top: int = LESSON_TOP, budget: int = LESSON_BUDGET_CHARS,
                   similarity=None) -> List[str]:
    """The lessons that apply to a turn: tag overlap first, then similarity of
    the lesson's "applies when" to the message (``similarity(a, b) -> 0..1``,
    the CPU encoder when the caller has it, else word overlap), top ``top``
    within ``budget`` characters. Proposed, retired and archived lessons never
    apply. Pure: the caller injects and calls note_applied."""
    want = set(_clean_tags(tags))
    recs = {r.get("ref"): r for r in records("lesson")}

    def words(s):
        return {w for w in "".join(ch if ch.isalnum() else " " for ch in str(s or "").casefold()).split() if len(w) > 2}

    def sim(a, b):
        if similarity is not None:
            try:
                return float(similarity(a, b))
            except Exception:
                pass
        wa, wb = words(a), words(b)
        return len(wa & wb) / max(1, len(wa | wb))
    scored = []
    for line in entries:
        rec = recs.get(lesson_ref(line)) or {}
        if rec and rec.get("status") not in (None, "active"):
            continue
        tag_hit = len(want & set(rec.get("tags") or []))
        s = sim(rec.get("applies_when") or line, message)
        if tag_hit or s > 0:
            scored.append((tag_hit, s, line))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    out, used = [], 0
    for _t, _s, line in scored:
        if len(out) >= top or used + len(line) > budget:
            continue
        out.append(line)
        used += len(line)
    return out


_HOOKED = []


def register_hooks() -> None:
    """Register the retirement card's decision hook. Called at startup from
    services/trials (this module stays a leaf: it never imports approvals at
    import time)."""
    if _HOOKED:
        return
    try:
        from agent_friday.services import approvals
        approvals.register_decision_hook(RETIRE_KIND, _on_retire_decided)
        _HOOKED.append(True)
    except Exception:
        pass


def purge(kind: str, ref: str) -> Dict[str, Any]:
    """Drop a record entirely: the owner deleted or rewrote the line it is
    about, and a deleted line must not survive in a second file
    (services/core_memory)."""
    try:
        with _LOCK:
            data = _load()
            rid = record_id(kind, ref)
            gone = data["records"].pop(rid, None)
            for rec in data["records"].values():
                if rec.get("replaces") == rid:
                    rec["replaces"] = None
                if rec.get("replaced_by") == rid:
                    rec["replaced_by"] = None
            if gone is not None:
                _save(data)
            return {"ok": True, "purged": gone is not None}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _heuristic_records() -> List[Dict[str, Any]]:
    try:
        from agent_friday.services import learning_loop
        return learning_loop.heuristic_records()
    except Exception:
        return []


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in rec.items() if k != "open"}
    out["open_contexts"] = len(rec.get("open") or {})
    return out


def records(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every record (heuristics read through from the learning loop), newest
    first."""
    try:
        with _LOCK:
            rows = [_public(r) for r in _load()["records"].values()]
    except Exception:
        rows = []
    if kind in (None, "heuristic"):
        rows += _heuristic_records()
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    rows.sort(key=lambda r: r.get("updated") or r.get("created") or 0, reverse=True)
    return rows


def get(rid: str) -> Optional[Dict[str, Any]]:
    for r in records():
        if r.get("id") == rid:
            return r
    return None


def _wilson(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return round(max(0.0, (centre - margin) / denom), 4)


def summary(rec: Dict[str, Any]) -> Dict[str, Any]:
    o = rec.get("outcomes") or {}
    s, f = int(o.get("success") or 0), int(o.get("failure") or 0)
    n = s + f
    return {"id": rec.get("id"), "status": rec.get("status"), "uses": int(rec.get("uses") or 0),
            "judged": n, "success": s, "failure": f,
            "success_rate": round(s / n, 4) if n else None, "wilson_lower": _wilson(s, n),
            "attributable": rec.get("exposure") != "always_on"}


def comparison(old_id: str, new_id: str) -> Dict[str, Any]:
    """Two records side by side for an old-vs-new decision. Reports only; the
    owner decides. ``comparable`` is False while either side has no judged
    outcome or neither is attributable to its own uses."""
    a, b = get(old_id), get(new_id)
    if a is None or b is None:
        return {"ok": False, "error": "record not found", "old": old_id, "new": new_id}
    sa, sb = summary(a), summary(b)
    return {"ok": True, "old": sa, "new": sb,
            "comparable": bool(sa["judged"] and sb["judged"]
                               and (sa["attributable"] or sb["attributable"]))}


#: The workflow checks that read a deliverable back (services/workflow_outcomes).
#: Only these may judge whether a lesson helped: "a reply was produced" or a
#: check marked not automatically checked says nothing about that.
_JUDGING_CHECKS = frozenset({"artifact_saved", "file_saved", "code_change_saved", "comparison_read"})


def outcome_checked(outcome: Any) -> bool:
    """Did the task's own verification actually check the work? False when
    there was no verification, when any part could not be checked automatically,
    when the evidence could not be saved, or when only a reply was checked."""
    if not isinstance(outcome, dict):
        return False
    checks = [c for c in (outcome.get("checks") or []) if isinstance(c, dict)]
    if not checks:
        return False
    for c in checks:
        if c.get("status") == "not_automatically_checked":
            return False
        if c.get("name") in ("durable_evidence", "supported_output"):
            return False
    return any(c.get("name") in _JUDGING_CHECKS for c in checks)
