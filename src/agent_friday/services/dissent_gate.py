"""
Agent Friday — Dissent Gate (Dissent-Lite)
FutureSpeak.AI · Asimov's Mind

V6_WHOLENESS_SPEC.md §4 Phase 4 (subset) / AUTONOMY_SPEC.md §8 (A2). Gives
Friday a standing, designed way to say "this conflicts with what I understand
you actually want" *before* she complies — operationalizing cLaws Law 2's own
"except where they conflict" boundary. This module does NOT change Law 2, add
a new law, or touch the signed cLaws text (governance/proof_of_integrity.py::
CLAWS_TEXT is untouched) — dissent is a behavior, not a rule.

Scope note (AUTONOMY_SPEC A2 — deliberately smaller than V6 Phase 4):
  * check_dissent() is a pure decision function plus an audit side effect. It
    is designed to be CALLED from an approval/gate path — Phase A3
    (services/goals.py + services/approvals.py) is that caller, wiring this
    into approval-card creation. No turn-pipeline hook exists yet (V6 Phase 2
    GrowthWS surfacing is explicitly deferred too) — this module is built and
    fully unit-tested standalone so A3 has something real to call.
  * SOUL.md / SELF.md text is NOT edited here (also deferred) — the in-
    character voice below is expressed in code (render_dissent_statement /
    render_refusal_statement), not documentation.

THE ONE INVARIANT THAT MATTERS MOST (Q3/Q6, V6 §6 invariant 8):
  Law-1 (harm) asks are refused OUTRIGHT by the EXISTING content floor
  (services/content_policies.py, which runs services/moderation.py's H1-H4
  scan first) — that floor is never modified, never re-implemented, and never
  second-guessed here. check_dissent() consults it FIRST, before any interest-
  conflict logic runs, and if it blocks: the result is not a "hard dissent"
  that reaffirmation can unblock — `law1_blocked` stays True and `proceed`
  stays False no matter what the `reaffirmed` argument is. Dissent is a
  voice channel for INTEREST conflicts (Q6: soft vs hard); it is never a path
  around the harm floor. tests/security/test_dissent_not_override_law1.py
  is the adversarial suite for exactly this property.

  This ALSO covers the floor being unreachable, not just the floor firing: if
  content_policies.evaluate_content() itself raises (an outage — a moved/
  renamed module, an ImportError, a bug in its own unguarded tail), that is
  treated as a Law-1 BLOCK too (`law1_blocked=True`, `proceed=False`), never
  as "not blocked." An uncertain floor FAILS CLOSED, mirroring this codebase's
  posture for the equivalent gates dissent_gate is modeled on
  (egress_gate.seal_outbound, the planned person-boundary gate;
  V6_WHOLENESS_SPEC.md §6 invariants 2/4: "on uncertainty, it withholds") —
  see _check_law1_floor and check_dissent's `floor_unavailable` key, and
  tests/security/test_dissent_not_override_law1.py::TestFloorOutageFailsClosed.

Severity (Q6): "soft" by default — Friday names the conflict and proceeds.
"hard" only for outward/irreversible/high-cost actions (the same action class
Q3 gates behind human approval) — she surfaces the conflict and withholds
`proceed` unless `reaffirmed=True` is passed back in (Law 2 preserved: dissent
is voice, not veto — on reaffirmation she complies).

Conflict detection is intentionally a small, documented heuristic — same
family as user_model.py's lexicon matching and persona_eval's rubric
matching, NOT an LLM call (this must stay local, deterministic, and free to
run on every gated action): a stored constraint only ever triggers a conflict
when it (a) reads as a prohibition (contains one of _NEGATION_MARKERS) and
(b) shares a meaningful, near-synonym-normalized keyword with the action
description. A constraint that isn't phrased as a prohibition, or an action
that shares no vocabulary with any stored constraint, will not be flagged —
a known, documented limitation (same posture as persona_eval's soul-alignment
scorer), not a bug to chase in this phase.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger("friday.dissent_gate")

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
FRIDAY_DIR = _HOME / ".friday"
EVENTS_PATH = FRIDAY_DIR / "governance" / "dissent_events.jsonl"

_LOCK = threading.Lock()
_MAX_TEXT = 500  # cap stored/quoted text, same order of magnitude as user_model facts


# ═══════════════════════════════════════════════════════════════════════════
#  SEVERITY CLASSIFICATION (Q3/Q6) — outward / irreversible / high-cost
# ═══════════════════════════════════════════════════════════════════════════
# Mirrors the SAME action class AUTONOMY_SPEC §4 Q3 gates behind human
# approval by default ("outward/irreversible + any spend + any external
# message"), and governance/behavioral_monitor.py's RING_INTENT_HINTS keyword-
# family style. An action matching none of these is "soft" — internal
# research/drafting territory, per Q3.

_OUTWARD_MARKERS = (
    "send", "email", "message", "text ", "dm ", "post ", "publish", "tweet",
    "share publicly", "share it publicly", "reply to", "call ", "phone ",
    "upload", "submit", "announce",
)
_IRREVERSIBLE_MARKERS = (
    "delete", "remove all", "wipe", "erase", "permanently", "uninstall",
    "format ", "cancel my", "close my account", "deactivate", "unsubscribe",
    "overwrite",
)
_SPEND_MARKERS = (
    "buy ", "purchase", "pay ", "spend", "transfer", "wire ", "order ",
    "subscribe", "donate", "invest", "sell ", "checkout", "charge my",
)

_HARD_MARKERS = _OUTWARD_MARKERS + _IRREVERSIBLE_MARKERS + _SPEND_MARKERS

# A leading imperative that makes the WHOLE action internal research/drafting
# regardless of what topic it's about (Q3: "internal research/drafting
# auto-proceeds with a receipt"). This exists because _HARD_MARKERS is a bare
# substring scan: "spend" and "order " are hard (spend) markers, but they also
# occur as ordinary NOUNS inside purely analytical asks like "Analyze our
# spend trends for Q2" or "Research typical order fulfillment times for our
# industry" — neither is an actual spend act, yet an unqualified substring
# scan can't tell the difference. Anchored to the START of the (stripped,
# lowered) description on purpose, and checked as a whole leading word/phrase
# (via \b), NOT a bare substring anywhere in the text — an unanchored check
# would itself misfire, e.g. "Wipe the old drafts directory" contains "draft"
# mid-sentence but is an irreversible wipe, not drafting; only the ACTION'S
# OWN leading verb should ever downgrade it to soft.
_DRAFTING_MARKERS = (
    "analyze", "analyse", "research", "summarize", "summarise", "review",
    "draft", "outline", "study", "investigate", "assess", "examine",
    "explore", "audit", "reorganize", "reorganise", "compile", "catalog",
    "catalogue", "evaluate", "brainstorm", "look into", "report on",
)
_DRAFTING_PREFIX_RE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(m) for m in _DRAFTING_MARKERS) + r")\b"
)


def classify_severity(action_description: str) -> str:
    """"hard" if the action reads as outward/irreversible/high-cost (Q3's own
    gate set); "soft" otherwise. Pure text heuristic — see module docstring.

    A leading drafting/research imperative ("analyze/research/summarize/
    review/draft/study/investigate ...") always classifies as "soft"
    REGARDLESS of what follows — see _DRAFTING_MARKERS for why an unqualified
    substring scan over _HARD_MARKERS can't otherwise distinguish an actual
    outward/irreversible/spend ACT from research or drafting ABOUT that same
    topic (e.g. "spend"/"order " matching inside "analyze our spend trends"
    or "research order fulfillment times", neither of which spends or orders
    anything)."""
    low = (action_description or "").strip().lower()
    if _DRAFTING_PREFIX_RE.match(low):
        return "soft"
    return "hard" if any(m in low for m in _HARD_MARKERS) else "soft"


# ═══════════════════════════════════════════════════════════════════════════
#  CONFLICT DETECTION — constraint/goal prohibitions vs. the action text
# ═══════════════════════════════════════════════════════════════════════════

_NEGATION_MARKERS = (
    "never ", "don't ", "do not ", "avoid ", "no longer ", "stop ",
    "without asking", "without my permission", "without checking with me",
    "always ask before", "always check with me before", "always confirm before",
)

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "are", "was", "were", "be", "been", "with", "that", "this", "it",
    "you", "your", "my", "me", "i", "we", "can", "could", "would", "should",
    "please", "do", "does", "did", "have", "has", "had", "will", "before",
    "from", "into", "as", "if", "so", "then", "than", "again", "asking",
    "checking", "permission", "longer", "about", "just", "really", "very",
    "want", "wants", "wanted", "there", "here", "over", "under", "some",
    "any", "all", "one", "two", "get", "got", "need", "needs", "let", "lets",
    "make", "made", "now", "out", "up", "down", "new", "more", "most",
    "much", "many", "also", "only", "still", "yet", "going", "gonna",
    # Negation grammar itself must never be the thing that "overlaps" — a
    # conflict should be about the SUBJECT of the prohibition, not about both
    # texts happening to say "never"/"don't" independently.
    "never", "don't", "dont", "not", "no", "avoid", "stop", "always",
    "without", "confirm",
})

# A small, curated near-synonym table — NOT general NLP. Maps a handful of
# verbs/nouns that commonly co-refer in this exact domain (spend vs. buy,
# message vs. email, delete vs. wipe) onto one canonical token so "don't spend
# money without asking" recognizes "buy a $200 gift card" as the same topic.
# Anything not listed is left as its own literal token.
_SYNONYMS = {
    "buy": "spend", "purchase": "spend", "pay": "spend", "spend": "spend",
    "order": "spend", "checkout": "spend",
    "email": "message", "text": "message", "dm": "message", "message": "message",
    "contact": "message", "call": "message",
    "delete": "remove", "erase": "remove", "wipe": "remove", "remove": "remove",
    "post": "publish", "share": "publish", "publish": "publish", "upload": "publish",
    "tweet": "publish",
}

_WORD_RE = re.compile(r"[a-z0-9']+")


def _keywords(text: str) -> set:
    """Lowercased, stopword-filtered, synonym-normalized token set."""
    out = set()
    for w in _WORD_RE.findall((text or "").lower()):
        if len(w) < 3 or w in _STOPWORDS:
            continue
        out.add(_SYNONYMS.get(w, w))
    return out


def _is_prohibition(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _NEGATION_MARKERS)


def _iter_conflict_candidates(interest_model: Dict[str, Any]):
    """Yield (origin, text) for every prohibition-shaped constraint — from the
    stored constraints list and from any goal that carries its own
    "constraints" list of strings (goals are [] today; see interest_model.py)."""
    for c in (interest_model.get("constraints") or []):
        text = c.get("text") if isinstance(c, dict) else c
        if text:
            yield "constraint", str(text)
    for g in (interest_model.get("goals") or []):
        if not isinstance(g, dict):
            continue
        label = g.get("title") or g.get("goal_id") or "goal"
        for text in (g.get("constraints") or []):
            if text:
                yield f"goal:{label}", str(text)


def _find_conflict(action_description: str, interest_model: Dict[str, Any]
                    ) -> Optional[Dict[str, str]]:
    """First prohibition-shaped constraint whose keywords overlap the action
    description's keywords, or None. See module docstring for the heuristic's
    documented scope/limitations."""
    action_kw = _keywords(action_description)
    if not action_kw:
        return None
    for origin, text in _iter_conflict_candidates(interest_model):
        if not _is_prohibition(text):
            continue
        if _keywords(text) & action_kw:
            return {"origin": origin, "text": text}
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  LAW-1 FLOOR — consult, never reimplement, never bypass
# ═══════════════════════════════════════════════════════════════════════════

def _check_law1_floor(action_description: str) -> Dict[str, Any]:
    """Ask the EXISTING content floor (services/content_policies.py, which
    runs moderation.py's H1-H4 scan before any pack rule) whether this action
    description is an outright harm ask. This module has no opinion of its
    own on what counts as harm — content_policies.py is the single authority
    consulted here, unmodified.

    FAILS CLOSED: a floor OUTAGE (import/DB failure, a moved/renamed module,
    a bug in evaluate_content's own unguarded tail — an ImportError/
    ModuleNotFoundError class of failure, not just a DB hiccup) is treated as
    BLOCKED, not as "not blocked". This mirrors the codebase's existing
    fail-closed posture for the equivalent gates dissent_gate is modeled on
    (egress_gate.seal_outbound, the planned person-boundary gate;
    V6_WHOLENESS_SPEC.md §6 invariants 2/4: "on uncertainty, it withholds").
    The outage is loud (logged at ERROR, and surfaced to the caller via
    floor_unavailable=True) but never permissive — check_dissent forces
    law1_blocked=True/proceed=False whenever floor_unavailable is True,
    exactly as if the floor itself had fired, until it is reachable again."""
    action_description = (action_description or "").strip()
    if not action_description:
        return {"blocked": False, "floor_unavailable": False}
    try:
        from agent_friday.services import content_policies as cp
        result = cp.evaluate_content({"title": action_description, "description": ""})
    except Exception as e:
        _log.error("Law-1 floor check UNAVAILABLE (%s) — FAILING CLOSED "
                    "(treating this action as blocked) until the floor is "
                    "reachable again; this is a floor OUTAGE, not a silent "
                    "pass-through", e)
        return {
            "blocked": True,
            "floor_unavailable": True,
            "harm_level": None,
            "reason": "Law-1 content floor is unavailable — failing closed",
            "applied_packs": [],
        }

    rule = (result.get("blocking_rule") or {}).get("rule") or {}
    return {
        "blocked": bool(result.get("blocked")),
        "floor_unavailable": False,
        "harm_level": rule.get("category"),
        "reason": result.get("reason"),
        "applied_packs": result.get("applied_packs") or [],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  IN-CHARACTER STATEMENTS — Friday's voice (see services/soul.py)
# ═══════════════════════════════════════════════════════════════════════════
# SOUL.md: "Editorially sharp, loyally contrarian... allergic to corporate BS...
# Give the answer first... Be direct. Never be sycophantic... Push back when
# the user needs it — a good editor argues." These templates say the verdict
# first, name the conflict plainly, and propose the one honest "aligned
# alternative" available without inventing specifics she can't know yet:
# checking with the user before proceeding.

def render_dissent_statement(action_description: str, conflict_text: str,
                              severity: str, *, reaffirmed: bool = False) -> str:
    quoted = (conflict_text or "").strip().rstrip(". ")
    if severity == "hard" and not reaffirmed:
        return (
            f'Hold on — you told me "{quoted}." What you\'re asking now runs '
            f"straight into that, and it's outward or irreversible enough that "
            f"I'm not going to guess my way past it. Confirm it and I'll go; "
            f"otherwise I'd rather hold off and check with you first."
        )
    if severity == "hard" and reaffirmed:
        return (
            f'Noted, and noted again — this still runs into "{quoted}," but '
            f"you've confirmed it, so I'm proceeding. Flagging it for the "
            f"record; that's what dissent gets you, not a veto."
        )
    # soft
    return (
        f'Quick flag, boss — this brushes up against "{quoted}." Not big '
        f"enough a deal to stop for, so I'm going ahead, but I'd rather say "
        f"it out loud than bury it."
    )


def render_refusal_statement(harm_level: Optional[str], reason: Optional[str]) -> str:
    detail = f" ({reason})" if reason else ""
    return (
        f"That's not something I'll do{detail}. That's not a preference I'm "
        f"weighing against yours — it's the floor, and dissent doesn't get "
        f"anyone around it."
    )


# ═══════════════════════════════════════════════════════════════════════════
#  SIGNED, APPEND-ONLY DISSENT EVENTS — mirrors decision-bom.jsonl's scheme
# ═══════════════════════════════════════════════════════════════════════════
# Same signing primitive server-side BOM entries use (services/agent.py's
# _governance_check): the governance HMAC key from
# governance/proof_of_integrity.get_governance_key(), applied via
# privacy/vault_crypto.sign_entry() when available, with a manual-HMAC
# fallback so a missing vault_crypto import degrades to still-signed rather
# than unsigned.

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def _scrub_and_truncate(text: str, max_len: int = _MAX_TEXT) -> str:
    """Lightweight PII scrub (same intent as behavioral_monitor._scrub, kept
    as its own tiny copy here so this module stays a leaf) + length cap —
    dissent events are meant to be human-legible later (V6 Phase 2 growth
    timeline: "times I pushed back, and what happened"), so text is kept
    plain, not hashed away."""
    t = _EMAIL_RE.sub("<email>", text or "")
    t = _TOKEN_RE.sub("<token>", t)
    t = t.strip()
    return t[:max_len]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sign_event(entry: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from agent_friday.governance.proof_of_integrity import get_governance_key
        key = get_governance_key()
    except Exception as e:
        _log.error("dissent event signing key unavailable: %s", e)
        return {**entry, "hmac": "unsigned:governance_key_unavailable"}
    try:
        from agent_friday.privacy import vault_crypto as _vc
        return _vc.sign_entry(entry, key)
    except Exception:
        canonical = json.dumps(entry, sort_keys=True).encode("utf-8")
        h = _hmac.new(key, canonical, hashlib.sha256).hexdigest()
        return {**entry, "hmac": h}


def _append_event(entry: Dict[str, Any]) -> None:
    try:
        with _LOCK:
            EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with EVENTS_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.error("dissent event append FAILED: %s", e)


def record_dissent_event(action_description: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Build, sign, and append one dissent event. Returns the signed entry
    (never raises — a write/signing failure is logged and an 'unsigned:...'
    or best-effort entry is still returned so callers can inspect it)."""
    conflict_source = result.get("conflict_source")
    conflict_text = None
    if isinstance(conflict_source, dict):
        conflict_text = conflict_source.get("text")
    entry: Dict[str, Any] = {
        "event_id": f"dsg-{uuid.uuid4().hex[:12]}",
        "timestamp": _now_iso(),
        "action_summary": _scrub_and_truncate(action_description),
        "category": result.get("category"),
        "severity": result.get("severity"),
        "law1_blocked": bool(result.get("law1_blocked")),
        "floor_unavailable": bool(result.get("floor_unavailable")),
        "harm_level": result.get("harm_level"),
        "conflict_source": (_scrub_and_truncate(conflict_text)
                             if conflict_text else None),
        "proceed": bool(result.get("proceed")),
        "reaffirmed": bool(result.get("reaffirmed")),
        "statement": _scrub_and_truncate(result.get("statement") or "", max_len=1000),
    }
    signed = _sign_event(entry)
    _append_event(signed)
    return signed


def read_dissent_events(limit: int = 200) -> List[Dict[str, Any]]:
    """Newest-last (file order); tail-limited. [] on any failure or if the
    log doesn't exist yet — a fresh install has never dissented."""
    if not EVENTS_PATH.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with EVENTS_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        _log.warning("read_dissent_events failed: %s", e)
        return []
    return out[-limit:]


def verify_dissent_event(entry: Dict[str, Any]) -> bool:
    """Re-verify a stored event's HMAC against the current governance key."""
    try:
        from agent_friday.governance.proof_of_integrity import get_governance_key
        from agent_friday.privacy import vault_crypto as _vc
        return _vc.verify_entry(entry, get_governance_key())
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  check_dissent — the function Phase A3 calls from the approval/gate path
# ═══════════════════════════════════════════════════════════════════════════

def check_dissent(action_description: str,
                   interest_model: Optional[Dict[str, Any]] = None,
                   *, reaffirmed: bool = False, record: bool = True
                   ) -> Dict[str, Any]:
    """Given an action description and an interest model (see
    interest_model.assemble_interest_model()), decide whether the action
    conflicts with what the user has signalled they want.

    Returns (at minimum, per AUTONOMY_SPEC A2):
        {"conflict": bool, "severity": "soft"|"hard"|None, "statement": str}
    plus, for callers that need the full picture (A3's approval path does):
        "law1_blocked"      — True iff the Law-1 gate is blocking this action
                               outright — either a REAL content-floor hit, or
                               (see floor_unavailable) a forced fail-closed
                               block because the floor itself is unreachable
                               right now. When True, `proceed` is False and
                               stays False regardless of `reaffirmed` (Law-1
                               is not a dissent severity, it is a floor).
        "floor_unavailable" — True iff content_policies.evaluate_content()
                               itself raised (an outage) rather than
                               returning a real verdict. FAILS CLOSED: an
                               unavailable floor is treated as
                               law1_blocked=True/proceed=False, the SAME as
                               an actual harm hit, never as a free pass
                               (V6_WHOLENESS_SPEC.md §6 invariants 2/4: "on
                               uncertainty, it withholds"). A caller (e.g.
                               A3's approval gate) can inspect this flag to
                               tell "real harm floor hit" apart from "the
                               floor is down right now" for operator-visible
                               alerting — but MUST NOT use it to proceed
                               anyway; `proceed` is already False either way.
        "proceed"           — whether the gate should let the action through
                               right now: always True when there's no
                               conflict or the conflict is "soft"; for
                               "hard", True only once `reaffirmed=True` is
                               passed back in; always False when
                               law1_blocked (including a floor outage).
        "category"           — "law1_block" | "law1_floor_unavailable" |
                                "interest_conflict" | None
        "conflict_source"    — {"origin", "text"} of the matched constraint/
                                goal, or None
        "harm_level"         — the content floor's H1-H4 category, or None
                                (also None when floor_unavailable — no real
                                verdict was ever returned)
        "reaffirmed"         — echoes the argument, for the recorded event

    `record=True` (default) appends a signed audit event via
    record_dissent_event() whenever there IS something to record (a law1
    block, a floor outage, or an interest conflict) — a clean pass records
    nothing, keeping the log meaningful. Pass record=False for a pure,
    side-effect-free check (e.g. a dry-run preview).

    Never raises — degrades to a conservative "no conflict, proceed" result
    only when the input itself is empty; any internal failure in the Law-1
    floor check FAILS CLOSED (see _check_law1_floor / floor_unavailable
    above) — a floor outage blocks exactly like a real hit, it is never a
    silent pass-through to "no conflict."
    """
    action_description = (action_description or "").strip()
    interest_model = interest_model or {}

    floor = _check_law1_floor(action_description)
    if floor.get("blocked"):
        floor_unavailable = bool(floor.get("floor_unavailable"))
        statement = render_refusal_statement(floor.get("harm_level"), floor.get("reason"))
        result = {
            "conflict": True,
            "severity": "hard",
            "statement": statement,
            "law1_blocked": True,
            "floor_unavailable": floor_unavailable,
            "proceed": False,
            "category": "law1_floor_unavailable" if floor_unavailable else "law1_block",
            "conflict_source": None,
            "harm_level": floor.get("harm_level"),
            "reaffirmed": bool(reaffirmed),
        }
        if record:
            record_dissent_event(action_description, result)
        return result

    match = _find_conflict(action_description, interest_model)
    if not match:
        return {
            "conflict": False,
            "severity": None,
            "statement": "",
            "law1_blocked": False,
            "floor_unavailable": False,
            "proceed": True,
            "category": None,
            "conflict_source": None,
            "harm_level": None,
            "reaffirmed": bool(reaffirmed),
        }

    severity = classify_severity(action_description)
    proceed = True if severity == "soft" else bool(reaffirmed)
    statement = render_dissent_statement(action_description, match["text"], severity,
                                          reaffirmed=reaffirmed)
    result = {
        "conflict": True,
        "severity": severity,
        "statement": statement,
        "law1_blocked": False,
        "floor_unavailable": False,
        "proceed": proceed,
        "category": "interest_conflict",
        "conflict_source": match,
        "harm_level": None,
        "reaffirmed": bool(reaffirmed),
    }
    if record:
        record_dissent_event(action_description, result)
    return result
