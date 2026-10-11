"""The pre-execution verifier: every tool call a model proposes is checked here
before the governance checkpoint sees it.

Spec: lu_features_spec section 9.5(g). `agent._execute_tool` is the single
choke point; it calls `preflight` first, then runs the hook chain (governance
rings, confirmation, ...), then calls `record_result` from a post hook.

What it does, in the order it does it:

  1. Schema. The tool's own input schema (services/tool_args, the shared
     checker): required fields, types, enums. A call that does not fit never
     runs; the model gets back what was wrong and what it sent.
  2. Argument sanity. Dates parse and are plausible (and an end is not before
     its start); paths are well-formed and, when the session lists granted
     folders, inside them; email addresses are addresses; an id the planner
     shortlisted for this turn is one of the shortlist. A failure is a fix-it
     message at no cost to the owner.
  3. Egress and privacy class. Free text bound for a tool that reaches outside
     (openWorld) and carries private data (the sensitivity tier, or the Laya
     `carries_private` answer when the planner supplied one).
  4. Approval class. `reflex_turn.combine_gate` as one more vote on the
     severity lattice (the tier-1 verdict the planner left on the session).
  5. Repeats and loops. A read-only call already made this turn with the same
     normalised arguments returns the earlier result. The same call failing
     three times in a row is stopped with what it failed with.
  6. Injection flag on tool RESULTS. Text that addresses the assistant or asks
     for actions raises a flag for the turn; the next outward action carries a
     reason on its card. Taint (services/taint) is never touched.

THE VERIFIER ONLY ADDS FRICTION. It cannot approve, grant, or remove a card, a
confirmation or a deny: it returns a fix-it message, a cached result, or a list
of reasons to ask. Steps 1, 2 (and 5's loop breaker) stop a call that cannot
succeed or is not going to; steps 3, 4 and 6 only raise "ask the owner", through
the confirmation gate or an approval card (agent._hook_call_verifier). A call the
owner already decided on a card is never touched. Anything it cannot judge
passes, so a bug here is today's behaviour, not a lockout.

Receipts: every outcome the verifier originates is a signed decision-bom line
with chosen_by "verifier".
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("friday.call_verifier")

CHOSEN_BY = "verifier"

#: Result prefixes this module returns instead of running a tool. Each one is
#: registered with the result classifiers (agent._TOOL_DENY_SENTINELS,
#: completion_receipts.FAILURE_SENTINELS) so a call that did not run never
#: reads as one that did.
SENTINELS = ("[VERIFIER FIX]", "[VERIFIER LOOP]", "[VERIFIER HOLD]")

LOOP_LIMIT = 3                 # identical failures in a row before the stop
KEYED_TTL = 3600.0             # a turn's state lives this long
UNKEYED_TTL = 120.0            # with no turn id, a cached result is trusted this long
MAX_STATES = 64
MAX_CACHED_CHARS = 200_000
MAX_SCAN_CHARS = 20_000

_LOCK = threading.RLock()


# ── per-turn state ───────────────────────────────────────────────────────────

@dataclass
class _Turn:
    keyed: bool
    touched: float = field(default_factory=time.time)
    cache: Dict[str, Tuple[str, float]] = field(default_factory=dict)
    streak_sig: str = ""
    streak_n: int = 0
    streak_err: str = ""
    seen_text: List[str] = field(default_factory=list)
    flags: List[dict] = field(default_factory=list)


_TURNS: Dict[str, _Turn] = {}


def _turn_key(session_ctx: Optional[dict]) -> Tuple[str, bool]:
    """(state key, whether the turn is identifiable)."""
    sc = session_ctx or {}
    try:
        from agent_friday.services import taint
        who = taint.ledger_key(sc)
    except Exception:
        who = str(sc.get("session_id") or sc.get("conversation_id") or "default")
    tid = sc.get("confirm_turn") or sc.get("turn_id") or ""
    if not tid:
        try:
            from agent_friday.services import turn_plan
            tid = turn_plan.current_turn_id() or ""
        except Exception:
            tid = ""
    if not tid and sc.get("task_id"):
        tid = "task:%s" % sc["task_id"]
    return "%s|%s" % (who, tid), bool(tid)


def _turn(session_ctx: Optional[dict], create: bool = True) -> Optional[_Turn]:
    key, keyed = _turn_key(session_ctx)
    now = time.time()
    with _LOCK:
        for k in [k for k, t in _TURNS.items()
                  if now - t.touched > (KEYED_TTL if t.keyed else UNKEYED_TTL)]:
            _TURNS.pop(k, None)
        t = _TURNS.get(key)
        if t is None and create:
            if len(_TURNS) >= MAX_STATES:
                _TURNS.pop(min(_TURNS, key=lambda k: _TURNS[k].touched), None)
            t = _TURNS[key] = _Turn(keyed=keyed)
        if t is not None:
            t.touched = now
        return t


def reset(session_ctx: Optional[dict] = None) -> None:
    """Forget a turn (tests, and a conversation reset)."""
    with _LOCK:
        if session_ctx is None:
            _TURNS.clear()
        else:
            _TURNS.pop(_turn_key(session_ctx)[0], None)


# ── outcome ──────────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    """`kind` is pass | fixit | cached | loop. `escalate` is a list of
    (code, plain words) the hook turns into a question or a card; it is never
    a permission."""

    kind: str = "pass"
    message: str = ""
    escalate: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def stops(self) -> bool:
        return self.kind in ("fixit", "loop", "cached")


# ── settings ─────────────────────────────────────────────────────────────────

def _settings() -> dict:
    out = {"enabled": True, "argument_sanity": True, "repeat_cache": True,
           "loop_breaker": True, "loop_limit": LOOP_LIMIT,
           "injection_flag": True, "privacy_vote": True}
    try:
        from agent_friday.core import _load_settings
        s = (_load_settings() or {}).get("call_verifier")
        if isinstance(s, dict):
            for k in out:
                if k in s:
                    out[k] = s[k]
    except Exception:
        pass
    try:
        out["loop_limit"] = max(2, int(out["loop_limit"]))
    except (TypeError, ValueError):
        out["loop_limit"] = LOOP_LIMIT
    return out


def annotations(name: str) -> dict:
    """readOnlyHint / destructiveHint / openWorldHint (tool_groups.json plus
    rings). A tool the table cannot describe is treated as a writer that
    reaches outside: no cache, full checks."""
    try:
        from agent_friday.services import tool_catalogue
        return tool_catalogue.annotations(name)
    except Exception:
        return {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}


# ── 1. schema ────────────────────────────────────────────────────────────────

def check_schema(name: str, args: Any, schema: Optional[dict]) -> Tuple[Any, Optional[str]]:
    """(arguments, error). The error is the message the model sees instead of a
    result; it carries the [VERIFIER FIX] sentinel's meaning in the older
    'INVALID ARGUMENTS' words, which every result classifier already knows."""
    from agent_friday.services import tool_args
    return tool_args.check(name, args if args is not None else {}, schema)


# ── 2. argument sanity ───────────────────────────────────────────────────────

_PATH_KEYS = {"path", "file_path", "folder", "directory", "dir", "source",
              "destination", "dest", "target_path", "output_path", "src", "dst"}
_EMAIL_KEYS = {"to", "cc", "bcc", "recipient", "recipients", "attendees",
               "email", "emails"}
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_ANGLE_RE = re.compile(r"<\s*([^<>\s]+@[^<>\s]+)\s*>")
_PLACEHOLDER_PATH = re.compile(r"(<[^>]*>|\{[^}]*\}|\bpath/to/|\.\.\.|\byour[_ -]?(file|folder|path)\b)", re.I)
_YEAR_RANGE = (1990, 2100)


def _props(schema: Optional[dict]) -> dict:
    p = (schema or {}).get("properties")
    return p if isinstance(p, dict) else {}


def _desc(spec: Any) -> str:
    return str((spec or {}).get("description") or "") if isinstance(spec, dict) else ""


def _is_date_prop(spec: Any) -> bool:
    if not isinstance(spec, dict) or spec.get("type") != "string":
        return False
    # Only a declared format makes a property a date; prose in a description
    # (an id field that lists "YYYY-MM-DD" among its kinds) never does.
    return spec.get("format") in ("date", "date-time")


def parse_when(value: str) -> Optional[datetime]:
    """An ISO 8601 date or date-time, or None."""
    s = str(value or "").strip()
    if not s:
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _naive(d: datetime) -> datetime:
    return d.replace(tzinfo=None) if d.tzinfo else d


def _email_tokens(value: Any) -> List[str]:
    items = value if isinstance(value, list) else [value]
    out: List[str] = []
    for it in items:
        if not isinstance(it, str):
            continue
        for part in re.split(r"[,;]", it):
            part = part.strip()
            if not part:
                continue
            m = _ANGLE_RE.search(part)
            out.append((m.group(1) if m else part).strip())
    return out


def _mail_tool(name: str, key: str, spec: Any) -> bool:
    low = name.lower()
    if "mail" in low or "invite" in low:
        return True
    if key == "attendees" and "calendar" in low:
        return True
    return "address" in _desc(spec).lower()


def _inside(path: str, roots: List[str]) -> bool:
    import os
    from pathlib import Path
    try:
        p = os.path.normcase(str(Path(path).expanduser().resolve()))
    except Exception:
        return False
    for r in roots:
        try:
            root = os.path.normcase(str(Path(str(r)).expanduser().resolve()))
        except Exception:
            continue
        if p == root or p.startswith(root.rstrip("\\/") + os.sep):
            return True
    return False


def check_sanity(name: str, args: dict, schema: Optional[dict],
                 session_ctx: Optional[dict] = None,
                 state: Optional[_Turn] = None) -> List[str]:
    """Problems the model can fix, each one a plain sentence. Empty is clean."""
    props = _props(schema)
    sc = session_ctx or {}
    fixes: List[str] = []
    whens: Dict[str, datetime] = {}
    for key, val in (args or {}).items():
        spec = props.get(key)
        if spec is None or val is None:
            continue
        # dates
        if isinstance(val, str) and _is_date_prop(spec):
            d = parse_when(val)
            if d is None:
                fixes.append(f"'{key}' must be an ISO 8601 date or time (like "
                             f"2026-08-18 or 2026-08-18T11:30:00-05:00); you sent {val!r}")
            elif not (_YEAR_RANGE[0] <= d.year <= _YEAR_RANGE[1]):
                fixes.append(f"'{key}' is {val!r}, a year outside {_YEAR_RANGE[0]}-"
                             f"{_YEAR_RANGE[1]}; check the date")
            else:
                whens[key] = d
        # paths
        if key in _PATH_KEYS and isinstance(val, str) and spec.get("type") == "string" \
                and not re.match(r"^[a-z][a-z0-9+.-]*://", val, re.I):
            if "\x00" in val:
                fixes.append(f"'{key}' contains a NUL character")
            elif _PLACEHOLDER_PATH.search(val):
                fixes.append(f"'{key}' looks like a placeholder, not a real path: {val!r}")
            else:
                roots = sc.get("granted_folders")
                if isinstance(roots, (list, tuple)) and roots and not _inside(val, list(roots)):
                    fixes.append(f"'{key}' ({val!r}) is outside the folders this task may use: "
                                 + ", ".join(str(r) for r in list(roots)[:5]))
        # addresses
        if key in _EMAIL_KEYS and _mail_tool(name, key, spec):
            for tok in _email_tokens(val):
                if not _EMAIL_RE.match(tok):
                    fixes.append(f"'{key}' has {tok!r}, which is not an email address")
                    break
        # ids the planner shortlisted for this turn
        short = (sc.get("id_shortlist") or {}).get(key) if isinstance(sc.get("id_shortlist"), dict) else None
        if short and isinstance(val, str):
            known = {str(x) for x in short}
            seen = " ".join(state.seen_text) if state is not None else ""
            if val not in known and val not in seen:
                fixes.append(f"'{key}' {val!r} is not one of the ids found for this request: "
                             + ", ".join(sorted(known)[:8]))
    s, e = whens.get("start") or whens.get("start_time"), whens.get("end") or whens.get("end_time")
    if s and e and _naive(e) < _naive(s):
        fixes.append("the end is before the start")
    return fixes


# ── 3. egress / privacy class ────────────────────────────────────────────────

def _free_text(args: Any) -> str:
    parts: List[str] = []

    def walk(v):
        if isinstance(v, str):
            if re.search(r"\s", v.strip()) or len(v) > 64:
                parts.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(args)
    return "\n".join(parts)[:20_000]


def carries_private(args: Any, session_ctx: Optional[dict] = None) -> bool:
    """Would these arguments carry something private out? The Laya answer when
    the planner left one on the session, else the sensitivity tier over the
    free text (no model load: the regex and keyword layers only)."""
    sc = session_ctx or {}
    if str(sc.get("carries_private") or "").lower() == "personal" and _free_text(args):
        return True
    text = _free_text(args)
    if not text:
        return False
    try:
        from agent_friday.services import sensitivity_classifier as _sc
        return _sc.classify(text, egress=True, use_presidio=False,
                            use_embeddings=False) >= _sc.Tier.PRIVATE
    except Exception:
        return False


# ── 4. approval class ────────────────────────────────────────────────────────

def severity_vote(name: str, args: dict, session_ctx: Optional[dict]) -> Optional[str]:
    """The severity tier 1 would give this call when it is stricter than the
    keyword verdict, else None. Escalate-only: `combine_gate` never goes below
    the keyword verdict. Needs the tier-1 reading the planner stored on the
    session (`tier1`); with none, the vote is empty."""
    tier1 = (session_ctx or {}).get("tier1")
    if not isinstance(tier1, dict):
        return None
    try:
        from agent_friday.governance import action_gate as _ag
        from agent_friday.services import reflex_turn
        klass, _why = _ag.classify(name, args, session_ctx)
        kw = {"forbidden": "hard"}.get(klass, "internal" if klass == _ag.INTERNAL else "outward")
        combined = reflex_turn.combine_gate(kw, tier1)
        order = reflex_turn.GATE_ORDER
        if combined in order and kw in order and order.index(combined) > order.index(kw):
            return combined
    except Exception as e:
        _log.debug("severity vote skipped: %s", e)
    return None


# ── 5. repeats and loops ─────────────────────────────────────────────────────

def normalise_args(args: Any) -> str:
    """Arguments reduced to what they mean for matching: keys sorted, empty
    values dropped, strings lower-cased with white space collapsed."""
    def norm(v):
        if isinstance(v, str):
            return re.sub(r"\s+", " ", v).strip().rstrip(".!?").lower()
        if isinstance(v, dict):
            return {str(k): norm(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))
                    if x not in (None, "", [], {})}
        if isinstance(v, (list, tuple)):
            return [norm(x) for x in v]
        return v
    try:
        return json.dumps(norm(args or {}), sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        return repr(args)


def _sig(name: str, args: Any) -> str:
    return name + "|" + normalise_args(args)


_FAIL_PREFIXES = ("tool error", "tool call failed", "invalid arguments", "command error",
                  "not run", "blocked", "command timed out", "error", "unknown tool",
                  "[not run]", "[verifier")


def failure_text(result: Any) -> Optional[str]:
    """The reason a tool result is a failure, or None if it reads as a success."""
    r = result if isinstance(result, str) else ""
    head = r.lstrip()
    low = head[:80].lower()
    if any(low.startswith(p) for p in _FAIL_PREFIXES):
        return head.splitlines()[0][:160] if head else "failed"
    if head.startswith("[stderr]"):
        return head.splitlines()[1][:160] if len(head.splitlines()) > 1 else "stderr"
    if head.startswith("{"):
        try:
            d = json.loads(head)
        except Exception:
            d = None
        if isinstance(d, dict):
            if d.get("ok") is False or d.get("success") is False:
                return str(d.get("error") or d.get("message") or "failed")[:160]
            if d.get("error") and not d.get("results"):
                return str(d.get("error"))[:160]
    return None


# ── 6. injection flag on tool results ────────────────────────────────────────

_INJECTION = [
    ("override", re.compile(
        r"\b(?:ignore|disregard|forget|override)\b(?:\s+\w+){0,3}\s+"
        r"(?:previous|prior|above|earlier|all|any|your)\b(?:\s+\w+){0,3}\s+"
        r"(?:instructions?|prompts?|rules|guidelines|directions)", re.I)),
    ("new instructions", re.compile(r"\b(?:new|updated|real|actual)\s+instructions?\s*[:\-]", re.I)),
    ("addresses the assistant", re.compile(
        r"\b(?:ai assistant|ai agent|assistant|language model|llm|chatbot|claude|chatgpt|gpt-?\d?)\b\s*[,:]\s*"
        r"(?:please\s+|you\s+(?:must|should|need to|will|are to)\b|now\b|do\b|send\b|forward\b|"
        r"reply\b|delete\b|run\b|open\b|click\b|visit\b|ignore\b)", re.I)),
    ("when you read this", re.compile(
        r"\b(?:when|if)\s+you\s+(?:read|see|process|summari[sz]e|open)\s+this\b[^.\n]{0,40}"
        r"\b(?:must|should|please|will|need)\b", re.I)),
    ("hide it from you", re.compile(
        r"\b(?:do\s+not|don't|never)\s+(?:tell|inform|mention|reveal|let)\b[^.\n]{0,30}"
        r"\b(?:user|owner|human|them)\b|\bwithout\s+(?:telling|informing|notifying)\s+"
        r"(?:the\s+)?(?:user|owner)\b", re.I)),
    ("run this", re.compile(
        r"\b(?:execute|run)\s+(?:the\s+)?following\s+(?:command|script|code)\b", re.I)),
]


def injection_labels(text: Any) -> List[str]:
    """Why a tool result reads as instructions to the assistant. Heuristic and
    deliberately conservative; a hit only adds a question for the owner."""
    s = text if isinstance(text, str) else ""
    if not s:
        return []
    s = s[:MAX_SCAN_CHARS]
    return [label for label, rx in _INJECTION if rx.search(s)]


def is_outward(name: str, args: dict, session_ctx: Optional[dict] = None) -> bool:
    """Would this call act outside, for the injection flag's purposes?"""
    ann = annotations(name)
    if ann.get("destructiveHint") or (ann.get("openWorldHint") and not ann.get("readOnlyHint")):
        return True
    try:
        from agent_friday.governance import action_gate as _ag
        klass, _why = _ag.classify(name, args, session_ctx)
        return klass != _ag.INTERNAL
    except Exception:
        return True


# ── the two entry points ─────────────────────────────────────────────────────

def preflight(name: str, args: dict, *, schema: Optional[dict] = None,
              session_ctx: Optional[dict] = None) -> Outcome:
    """Judge one proposed call (arguments already pass the schema). Never
    raises; a check that errors counts as passing."""
    cfg = _settings()
    if not cfg["enabled"]:
        return Outcome()
    sc = session_ctx or {}
    if sc.get("approved_card"):
        # The owner decided exactly this call; carrying it out is not a proposal.
        return Outcome()
    try:
        return _preflight(name, args or {}, schema, sc, cfg)
    except Exception as e:
        _log.warning("call verifier skipped %s: %s", name, e)
        return Outcome()


def _preflight(name, args, schema, sc, cfg) -> Outcome:
    st = _turn(sc)
    ann = annotations(name)
    sig = _sig(name, args)

    # 5a. the loop breaker comes first: nothing else matters to a call that
    # has already failed this way the allowed number of times.
    if cfg["loop_breaker"] and st.streak_sig == sig and st.streak_n >= cfg["loop_limit"]:
        return Outcome("loop", (
            f"[VERIFIER LOOP] '{name}' has failed {st.streak_n} times in a row with "
            f"the same arguments (last error: {st.streak_err or 'no detail'}). It was NOT "
            f"run again. Try something else, or ask the owner."))

    # 5b. a read-only call repeated in this turn
    if cfg["repeat_cache"] and ann.get("readOnlyHint") and not ann.get("destructiveHint"):
        hit = st.cache.get(sig)
        if hit and (st.keyed or time.time() - hit[1] < UNKEYED_TTL):
            return Outcome("cached", (
                "[repeat call: you already made this exact call earlier in this turn; "
                "this is the same result, nothing was run again]\n" + hit[0]))

    # 2. sanity
    if cfg["argument_sanity"]:
        fixes = check_sanity(name, args, schema, sc, st)
        if fixes:
            shown = json.dumps(args, default=str)
            shown = shown if len(shown) <= 300 else shown[:300] + "..."
            return Outcome("fixit", (
                f"[VERIFIER FIX] '{name}' did not run: " + "; ".join(fixes)
                + f". You sent: {shown}. Fix the call and try again; nothing was asked of the owner."))

    out = Outcome()
    outward = None

    def _outward() -> bool:
        nonlocal outward
        if outward is None:
            outward = is_outward(name, args, sc)
        return outward

    # 2b. an address nobody gave: ask, do not refuse
    if ann.get("openWorldHint") and not ann.get("readOnlyHint"):
        for code, why in _unknown_addresses(name, args, schema, sc):
            out.escalate.append((code, why))

    # 3. privacy class (writers that reach outside)
    if cfg["privacy_vote"] and ann.get("openWorldHint") and not ann.get("readOnlyHint") \
            and carries_private(args, sc):
        out.escalate.append(("private", "its details look private and it reaches outside this computer"))

    # 4. approval class vote
    if cfg["privacy_vote"] and (ann.get("openWorldHint") or ann.get("destructiveHint")) \
            and not ann.get("readOnlyHint"):
        sev = severity_vote(name, args, sc)
        if sev:
            out.escalate.append(("severity", f"your words read as a request to change something ({sev})"))

    # 6. an injection flag raised earlier this turn
    if cfg["injection_flag"] and st.flags and _outward():
        f = st.flags[-1]
        out.escalate.append(("injection", (
            f"text from {f['tool']} earlier in this turn tried to instruct me "
            f"({f['label']}); you did not")))
    return out


def _unknown_addresses(name, args, schema, sc):
    props = _props(schema)
    try:
        from agent_friday.services import taint
        key = taint.ledger_key(sc)
        led = taint._ledger(key)
        with taint._LOCK:
            has_user_text = any(e.kind == "user" for e in led.entries)
        if not has_user_text:
            return []   # nothing of the owner's to compare with: cannot judge
        for k, v in (args or {}).items():
            if k not in _EMAIL_KEYS or not _mail_tool(name, k, props.get(k)):
                continue
            for tok in _email_tokens(v):
                if _EMAIL_RE.match(tok) and taint.origin_of(key, tok).kind == "model":
                    return [("address", f"{tok} did not come from you or from anything I read")]
    except Exception as e:
        _log.debug("address provenance skipped: %s", e)
    return []


def record_result(name: str, args: dict, result: Any,
                  session_ctx: Optional[dict] = None) -> None:
    """After a tool ran (post hook, after the PII scrub): cache a read, track a
    failure streak, learn the ids seen, scan for instructions. Never raises,
    never changes the result."""
    try:
        cfg = _settings()
        if not cfg["enabled"]:
            return
        st = _turn(session_ctx)
        text = result if isinstance(result, str) else ""
        ann = annotations(name)
        sig = _sig(name, args)
        why = failure_text(text)
        with _LOCK:
            if why:
                if st.streak_sig == sig:
                    st.streak_n += 1
                else:
                    st.streak_sig, st.streak_n = sig, 1
                st.streak_err = why
            else:
                st.streak_sig, st.streak_n, st.streak_err = "", 0, ""
            if not ann.get("readOnlyHint") or ann.get("destructiveHint"):
                st.cache.clear()        # a write may have changed what a read returns
            elif not why and 0 < len(text) <= MAX_CACHED_CHARS:
                st.cache[sig] = (text, time.time())
            if text and len(st.seen_text) < 40:
                st.seen_text.append(text[:MAX_CACHED_CHARS])
        if cfg["injection_flag"] and text:
            labels = injection_labels(text)
            if labels:
                with _LOCK:
                    st.flags.append({"tool": name, "label": labels[0],
                                     "sha8": hashlib.sha256(text[:4000].encode("utf-8", "replace")).hexdigest()[:8],
                                     "at": time.time()})
    except Exception as e:
        _log.debug("call verifier record skipped: %s", e)


# ── receipts ─────────────────────────────────────────────────────────────────

def receipt(name: str, args: dict, decision: str, reason: str,
            session_ctx: Optional[dict] = None) -> None:
    """A signed decision-bom line for something the verifier decided. Best
    effort: the verifier only adds friction, so a receipt that cannot be
    written is logged and the friction stands."""
    sc = session_ctx or {}
    try:
        from agent_friday.governance import action_gate as _ag
        _ag._receipt({
            "tool": name, "class": "internal", "decision": decision,
            "reason": str(reason)[:300], "tainted": False,
            "args_hash": hashlib.sha256(json.dumps(args or {}, sort_keys=True,
                                                   default=str).encode()).hexdigest(),
            "surface": sc.get("surface") or ("chat" if sc.get("session_id") else
                                             "background" if sc.get("is_background_task") else "other"),
            "grant": None, "task_id": sc.get("task_id"),
            "chosen_by": CHOSEN_BY,
            "plan_version": _ag._plan_version(), "turn_id": _ag._turn_id()})
    except Exception as e:
        _log.error("verifier receipt failed: %s", e)
