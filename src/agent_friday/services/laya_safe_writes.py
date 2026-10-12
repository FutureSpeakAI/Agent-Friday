"""Laya's safe writes: she may PROPOSE a reversible change to the rows you pointed at.

Laya's third deep action (laya_deep_actions_addendum_2026-10-09, DA-3). "Archive
these three", "star the ones from Mara", "mark these as read", "move this to
Receipts": point_act resolves WHICH rows locally, and this module turns the
change into the card the governed path already raises. It is SENSITIVE and OFF.

  setting   ``laya_safe_writes``  off | shadow | on   (default off)

  off       nothing here runs; a change goes to the model, as it always did.
  shadow    the proposal she WOULD make is written to
            ``runtime/laya_safe_writes/shadow.jsonl`` (counts and hashes, no
            words, no titles) and NOTHING else happens. It never executes.
  on        she raises the existing batch card, prefilled with her rows and
            the verb. The owner's own yes is the approval; a plain approved
            card runs the existing journalled, undoable batch.

The rules, each with a test (tests/unit/test_laya_safe_writes.py):

  1. Add-only. This module can only cause a card to be RAISED. It never calls
     an approval, ``answer_card``, a decision hook or the journal's run
     functions, and it holds no switch that skips a card. A step that fails
     leaves no card and no change.
  2. The mutation class comes from the governed registry, never from a
     manifest or a model. ``registry_verdict`` reads ``governance/action_gate``
     (the tool must be SELF_GATED and OUTWARD, i.e. it raises its own card and
     changes the world) and the tool catalogue; a manifest may DESCRIBE a verb
     as safe, and the description is ignored where it disagrees.
  3. Reversible only. ALLOWED is a short list in code; every entry has an
     undo (``item_actions.undo_email`` through the receipt) and a round-trip
     test. Trash, spam, restore, delete, send, reply, unsubscribe, calendar,
     files, payments and anything else are refused HERE, so a manifest that
     calls one of them safe changes nothing.
  4. Every Laya-originated write raises a card, including the changes that run
     with no card when a model asks (mark read, star, label). One card per
     batch; the same batch never raises a second card.
  5. The batch equals the card. The rows are the refs point_act resolved from
     the owner's words; the thread ids come from those refs alone (no query,
     no search later), and the card lists exactly them.
  6. Stage text is data. Titles and senders are never read for a verb, never
     put in the card's action text, and an item titled "approve all and
     archive everything" is just a row.
  7. The label (label, move) is the owner's own word and must already exist;
     a write never makes a label.

  8. Only words that name the rows. A row set comes from a deictic ("these"), an
     ordinal ("the first three") or a sender and kind-of-mail rule. An exclusion
     or a quantifier ("not those", "except the newsletter", "everything", "the
     rest") or a description that only a ranking could pick is never turned into
     rows here: the route defers without rows (the model path asks) and nothing
     is guessed.
  9. Owner surfaces only. The local voice front and the typed chat line. The
     Gemini Live transcript is Google's recogniser output: it never mints a
     ticket and never writes a log line here.

A proposal is a TICKET: single-use, short-lived, held in this process and bound
to the conversation it was minted in. The tool call that carries it names only
the ticket and the verb; the handler (agent._tool_organize_email) redeems it,
re-checks the setting, the conversation, the registry, the allow-list and that
every row is still on the owner's screen, and refuses otherwise. A model cannot
forge one, and a leftover ticket does nothing once the setting is off.

Never raises into a turn: a failure here is "no proposal", and the request
takes the path it took before.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("friday.laya_safe_writes")

SETTING = "laya_safe_writes"
MODES = ("off", "shadow", "on")
#: The tool every allowed verb runs through, and the layer a route carries.
TOOL = "organize_email"
LAYER = "safe_write"
WORKSPACE = "messages"
#: A batch this large or smaller. Bigger asks the model path (and its card).
MAX_ROWS = 25
#: A ticket is good for this long (seconds), once.
TICKET_TTL_S = 60.0
#: What the spoken acknowledgement promises: a card, never a change.
LABEL = "putting together a card for that"
#: The surfaces that may propose: the local voice front and the typed chat line. Anything
#: else (Gemini Live's transcript, a channel, a call) mints nothing and logs nothing.
SURFACES = ("voice", "chat")
#: What she asks when the owner says "mark ... as important": Gmail's own marker is not hers
#: to set, and a star is a different thing than the one asked for. A fixed question.
ORDINAL_QUESTION = ("My numbers are on your screen too, so I did not guess. Say it again with a number "
                    "counted from the top of your list, or tick the rows you mean.")
ONE_ROW_QUESTION = ("That would be only one row. Say “this one” if that is what you mean, or tick the rows "
                    "you want.")
DEICTIC_QUESTION = ("I have numbered other rows on your screen, so I did not guess which you mean. Tick the "
                    "rows you want, or say it again with a number counted from the top of your list.")
IMPORTANT_QUESTION = ("“Important” is Gmail's own marker and I don't set it. Do you want those starred "
                      "instead? Say “star these” and I'll put it on a card.")


@dataclass(frozen=True)
class Verb:
    intent: str          # point_act's word for it
    action: str          # organize_email's action
    undo: str            # item_actions function that puts it back
    needs_label: bool
    words: str           # "archive", for the sealed lines


#: Reversible mail changes, nothing else. Every entry has an undo round-trip test.
#: (point_act has no parse for "back to the inbox" or "take the label off" yet,
#: so those two stay off this list until it does and they have the same test.)
ALLOWED: Dict[str, Verb] = {v.intent: v for v in (
    Verb("archive", "archive", "undo_email", False, "archive"),
    Verb("mark_read", "read", "undo_email", False, "mark as read"),
    Verb("mark_unread", "unread", "undo_email", False, "mark as unread"),
    Verb("star", "star", "undo_email", False, "star"),
    Verb("unstar", "unstar", "undo_email", False, "unstar"),
    Verb("label", "label", "undo_email", True, "label"),
    Verb("move", "move", "undo_email", True, "move"),
)}
_BY_ACTION = {v.action: v for v in ALLOWED.values()}

#: Never allowed, whatever a manifest, a model or a setting says. They take
#: mail out of reach, tell a sender something, or cannot be put back exactly.
NEVER = frozenset({"trash", "restore", "spam", "not_spam", "delete", "send", "reply", "forward",
                   "unsubscribe", "snooze", "rename", "shift", "reschedule", "pay"})

_LABEL_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &/'._-]{0,38}$")


class Refusal(Exception):
    """A proposal that must not become a card. The text is for the owner."""


# ── the setting ─────────────────────────────────────────────────────────────

def mode(settings: Optional[dict] = None) -> str:
    """off | shadow | on, read strictly: only the literal words count, and a
    setting that cannot be read is off."""
    try:
        if settings is None:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        v = settings.get(SETTING) if isinstance(settings, dict) else None
    except Exception:
        return "off"
    v = v if v in ("shadow", "on") else "off"
    try:
        # A setting file turned DOWN by a hand edit, a file write or a restore turns the owner's record down
        # with it, so a later write of "on" cannot re-arm the old click.
        from agent_friday.services import guarded_settings as _gs
        _gs.follow_file(SETTING, v)
    except Exception:
        pass
    if v == "off":
        return v
    try:
        # A value in settings.json is only a claim: it counts as far as the owner's own Settings
        # click recorded it (signed with the governance key). A hand edit or a restored or
        # approved file write that says "on" with no matching record reads as off. The read is
        # O(1) and never touches the keychain: the verified value is cached in memory.
        from agent_friday.services import guarded_settings
        return guarded_settings.effective(SETTING, v)
    except Exception:
        return "off"


# ── the registry decides ────────────────────────────────────────────────────

def registry_verdict(tool: str, action: str) -> Tuple[bool, str]:
    """(allowed, why). The governed registry decides, in order: the action is
    not on the never list; it is on this module's allow-list FOR this tool;
    the gate tables say the tool changes the world and raises its own card
    (so a card is how it runs); the tool catalogue does not call it a read;
    and the undo it names exists. A manifest is not consulted."""
    act = str(action or "").strip().lower()
    if act in NEVER:
        return False, "%s is never a safe write" % act
    verb = _BY_ACTION.get(act)
    if verb is None or tool != TOOL:
        return False, "%s %s is not on the reversible list" % (tool, act or "(none)")
    try:
        from agent_friday.governance import action_gate as ag
        if tool not in ag.SELF_GATED or tool not in ag.OUTWARD_TOOLS:
            return False, "the action gate does not hold %s to its own card" % tool
        if ag.classify(tool, {"action": act})[0] == ag.INTERNAL:
            return False, "the action gate calls %s internal, so Laya will not take it over" % tool
        from agent_friday.services import tool_catalogue
        if tool_catalogue.annotations(tool).get("readOnlyHint"):
            return False, "the tool catalogue calls %s a read" % tool
        from agent_friday.services import item_actions
        if act not in item_actions.EMAIL_ACTIONS or not callable(getattr(item_actions, verb.undo, None)):
            return False, "%s has no undo" % act
    except Exception as e:      # noqa: BLE001
        return False, "the registry could not be read (%s)" % type(e).__name__
    return True, "ok"


def manifest_claims() -> List[dict]:
    """Verbs a manifest CALLS safe (``safe_write`` set), with whether the
    registry agrees. A claim the registry refuses is a finding, never an
    allowance: nothing in this module reads the manifest to widen anything."""
    out = []
    try:
        from agent_friday.services import laya_manifests as lm
        for ws in lm.ids():
            for v in lm.verbs(ws):
                sw = v.get("safe_write")
                if not isinstance(sw, dict):
                    continue
                for act in sw.get("actions") or []:
                    ok, why = registry_verdict(str(v.get("tool") or ""), act)
                    out.append({"verb": v.get("id"), "tool": v.get("tool"), "action": act,
                                "allowed": ok, "why": why})
    except Exception as e:      # noqa: BLE001
        _log.debug("manifest claims unreadable: %s", e)
    return out


# ── turning a point-and-act change into a proposal ──────────────────────────

@dataclass
class Proposal:
    verb: Verb
    refs: List[str]
    label: str
    rule: str                # the owner's-words description the card shows
    ticket: str = ""
    at: float = 0.0
    stage_rev: int = 0
    what: str = ""
    why: str = ""
    method: str = ""         # how point_act chose the rows (for the shadow log)
    conversation_id: str = ""    # the conversation it was minted in; redeemed only there
    surface: str = ""

    def args(self) -> dict:
        """The arguments of the tool call: the verb and the ticket, nothing else."""
        return {"action": self.verb.action, "laya_write": {"ticket": self.ticket}}


def _words(verb: Verb, n: int, label: str) -> str:
    """The sealed description of a batch: the verb, a count, the owner's label."""
    what = "%d %s" % (n, "conversation" if n == 1 else "conversations")
    if verb.action == "label":
        return "label %s “%s”" % (what, label)
    if verb.action == "move":
        return "move %s to “%s”" % (what, label)
    return "%s %s" % (verb.words, what)


#: How the rows were chosen, in the owner's words (the card says it).
_RULE_WORDS = {"deictic:selection": "the rows you ticked", "deictic:cursor": "the row your hand was on",
               "deictic:open": "the one you have open", "deictic:focus": "the row you were on",
               }


def rule_words(method: str) -> str:
    m = str(method or "")
    if m in _RULE_WORDS:
        return _RULE_WORDS[m]
    if m.startswith("ordinals"):
        return "the rows you counted from the top of your screen"
    return "the rows your words matched on screen"


def _clean_label(raw: Any) -> str:
    s = " ".join(str(raw or "").split()).strip(" .,!?")
    return s if _LABEL_OK.match(s) else ""


#: The only ways point_act may have chosen the rows for a safe write: the deictic rules, the
#: owner's numbers, and a sender / kind-of-mail rule. A ranking over row text ("choice"), a
#: count that did not match, "all" and anything else is the model's.
METHOD_OK = re.compile(r"^(?:deictic:(?!pointed\b)[\w-]*|ordinals:[\w-]*|who|category|who\+category)$")

#: Words that exclude or quantify. A phrase that holds one is not a plain pointer.
_SCOPE = re.compile(
    r"\b(?:not|no|never|except|excepting|excluding|exclude|but|other than|apart from|aside from|besides|"
    r"without|minus|instead|leave|skip|ignore|everything|every ?thing|everyone|all|each|every|both|"
    r"rest|remaining|others?|else|anything|whatever|whichever|only|just)\b")


def scope_unclear(text: Any) -> bool:
    """True when the owner's words name rows by exclusion or quantity, so no rule can say which
    rows they mean: "archive these not those", "star all but the newsletter", "archive
    everything from Mara", "mark the rest as read". A plain pointer ("these", "the first
    three", "the ones from Mara") is False. Reads only the owner's own words, never the stage."""
    try:
        from agent_friday.services import point_act
        p = point_act.parse(text)
    except Exception:       # noqa: BLE001
        return False
    if p is None or p.kind == "clear":
        return False
    if p.kind == "deictic":
        return bool(re.match(r"(?:all|both)\b", p.obj))         # "all of them" is a quantity, "these" is a pointer
    if p.kind == "ordinals":
        return False            # the parse matches the whole object, so no exclusion rides in it
    return p.kind == "all" or bool(_SCOPE.search(p.obj))


def build(pa: Any, *, stage: Optional[dict] = None) -> Tuple[Optional[Proposal], str]:
    """(proposal, why-not) from a point_act ``defer`` for a change. Pure: no
    ticket, no log, no network. ``stage`` is the stage the rows were resolved
    on (for its revision)."""
    if pa is None or getattr(pa, "decision", "") != "defer":
        return None, "not a change request"
    a = dict(getattr(pa, "args", None) or {})
    intent = str(a.get("intent") or "")
    if intent in NEVER:
        return None, "%s is never a safe write" % intent
    verb = ALLOWED.get(intent)
    if verb is None:
        return None, "%s is not on the reversible list" % (intent or "that change")
    ok, why = registry_verdict(TOOL, verb.action)
    if not ok:
        return None, why
    if str(a.get("workspace") or "") != WORKSPACE:
        return None, "safe writes cover the Message Center only"
    refs = [str(r) for r in (a.get("refs") or [])]
    if not refs or len(refs) > MAX_ROWS:
        return None, "%d rows is outside 1 to %d" % (len(refs), MAX_ROWS)
    from agent_friday.services import screen_stage as ss
    if len(set(refs)) != len(refs) or not all(ss.split_mail_ref(r) for r in refs):
        return None, "a row is not a mail conversation"
    if str(a.get("rule") or "").startswith("all"):
        return None, "all of them is the model's to propose"
    label = ""
    if verb.needs_label:
        label = _clean_label(a.get("destination"))
        if not label:
            return None, "no usable label in the owner's words"
    elif a.get("destination") and verb.action not in ("read", "unread"):
        return None, "a destination on a verb that takes none"          # (mark ... as read: the word is the verb's)
    method = str(a.get("rule") or "")
    if not METHOD_OK.match(method):
        return None, "rows picked by %s are the model's to propose" % (method[:30] or "no rule")
    rule = rule_words(method)
    n = len(refs)
    return Proposal(verb, refs, label, rule, stage_rev=int((stage or {}).get("rev") or 0),
                    what=_words(verb, n, label), method=method,
                    why="Laya read your words and picked these rows on this PC. "
                        "Nothing changes until you approve this card."), ""


# ── tickets ─────────────────────────────────────────────────────────────────

_LOCK = threading.Lock()
_TICKETS: Dict[str, Proposal] = {}


def _sweep(now: float) -> None:
    for k in [k for k, p in _TICKETS.items() if now - p.at > TICKET_TTL_S]:
        _TICKETS.pop(k, None)
    while len(_TICKETS) > 64:
        _TICKETS.pop(next(iter(_TICKETS)), None)


def mint(p: Proposal) -> Proposal:
    now = time.time()
    p.ticket = secrets.token_hex(12)
    p.at = now
    with _LOCK:
        _sweep(now)
        _TICKETS[p.ticket] = p
    return p


def peek(ticket: Any) -> Optional[Proposal]:
    """The proposal behind a ticket, without spending it (for the sealed line)."""
    with _LOCK:
        _sweep(time.time())
        return _TICKETS.get(str(ticket or ""))


def is_pending(ticket: Any) -> bool:
    return peek(ticket) is not None


def reset() -> None:
    with _LOCK:
        _TICKETS.clear()


@dataclass
class Call:
    """What the handler may run: the verb, the rows, nothing a model supplied."""
    action: str
    thread_ids: List[str]
    refs: List[str]
    label: str
    rule: str
    stage_rev: int
    why: str
    what: str = ""
    extra: dict = field(default_factory=dict)


def redeem(spec: Any, *, action: Any, stage: Optional[dict], settings: Optional[dict] = None,
           conversation_id: Any = None) -> Call:
    """Spend a ticket and return the call it describes, or raise ``Refusal``.

    Checked again here, at the moment of use: the setting is still ``on``, the
    ticket is real, fresh and unspent, the action is the one it was minted for,
    the registry and the allow-list still say yes, and every row is still on the
    owner's screen (a stale list never becomes a card)."""
    if mode(settings) != "on":
        raise Refusal("Laya's safe writes are off")
    ticket = (spec or {}).get("ticket") if isinstance(spec, dict) else None
    with _LOCK:
        _sweep(time.time())
        p = _TICKETS.pop(str(ticket or ""), None)
    if p is None:
        raise Refusal("that proposal has expired; ask again")
    if not str(conversation_id or "").strip() or str(conversation_id).strip() != str(p.conversation_id or "").strip():
        raise Refusal("that proposal belongs to another conversation; ask again here")
    act = str(action or "").strip().lower()
    if act != p.verb.action:
        raise Refusal("that proposal was for a different change")
    ok, why = registry_verdict(TOOL, act)
    if not ok:
        raise Refusal(why)
    from agent_friday.services import screen_stage as ss
    on = {it.get("ref") for it in ((stage or {}).get("items") or [])}
    if (stage or {}).get("workspace") != WORKSPACE or not set(p.refs) <= on:
        raise Refusal("the list on your screen changed; point at the rows again")
    ids = []
    for r in p.refs:
        parts = ss.split_mail_ref(r)
        if not parts:
            raise Refusal("a row is not a mail conversation")
        ids.append("%s:%s" % parts)
    return Call(p.verb.action, ids, list(p.refs), p.label, p.rule, p.stage_rev, p.why, p.what)


def guard_card(action: str, *, laya_card: bool) -> None:
    """item_actions asks this before a Laya-originated card: the allow-list and
    the registry hold at the last moment too. Raises ``Refusal``."""
    if not laya_card:
        return
    ok, why = registry_verdict(TOOL, action)
    if not ok:
        raise Refusal(why)


# ── shadow ──────────────────────────────────────────────────────────────────

def log_path():
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / "laya_safe_writes" / "shadow.jsonl"


def expire_pending_cards() -> int:
    """Safe writes were turned down: every card Laya raised that is still waiting expires, so nothing she
    proposed under the old setting can be approved afterwards."""
    try:
        from agent_friday.services import approvals
        return approvals.expire_matching(
            lambda r: (r.get("payload") or {}).get("origin") == "laya_safe_write",
            "system:safe_writes_off", "Laya's safe writes were turned off")
    except Exception as e:      # noqa: BLE001
        _log.debug("could not expire pending safe-write cards: %s", e)
        return 0


def _salt() -> Optional[bytes]:
    """This install's secret for the log hashes, sealed by the install's keystore (credential_store: the vault
    key or DPAPI) beside the logs, made on first use. None when it cannot be read or made: a salt that exists
    but cannot be read is never replaced (the hashes would change), and with no salt no hashed row is written."""
    try:
        from agent_friday.services import credential_store
        path = log_path().parent / "salt"
        if path.is_file():
            raw = credential_store.read_secret(path)
            return raw if len(raw) >= 16 else None
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = secrets.token_bytes(32)
        credential_store.write_secret(path, raw)
        return raw
    except Exception:       # noqa: BLE001
        return None


_KEY_CACHE: Optional[bytes] = None
_KEY_RETRY_AT = 0.0


def keyed_hash(text: Any, n: int = 16) -> str:
    """HMAC-SHA256 of ``text`` under this install's sealed local key, cut to ``n`` hex digits; "" when the key
    is unavailable (a caller then writes no hashed row). The key is read once per process; a failed read is
    retried at most every 30 seconds and never replaced by a throw-away key."""
    global _KEY_CACHE, _KEY_RETRY_AT
    key = _KEY_CACHE
    if key is None:
        if time.time() < _KEY_RETRY_AT:
            return ""
        key = _salt()
        if key is None:
            _KEY_RETRY_AT = time.time() + 30.0
            return ""
        _KEY_CACHE = key
    return hmac.new(key, str(text or "").encode("utf-8"), hashlib.sha256).hexdigest()[:n]


def _h(text: Any, salt: Optional[bytes]) -> str:
    """A keyed hash (HMAC-SHA256 with the install's salt): a short command like "archive these"
    cannot be found again by hashing a dictionary. No salt, no hash."""
    if not salt:
        return ""
    return hmac.new(salt, str(text or "").encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def shadow(p: Optional[Proposal], why_not: str, text: str, surface: str) -> None:
    """Write what she WOULD have proposed. Counts and hashes only: no words,
    no titles, no label. Executes nothing. Writes nothing while off the record. Never raises."""
    try:
        from agent_friday.services import off_record
        if off_record.active():
            return
    except Exception:
        pass
    try:
        salt = _salt()
        if not salt:
            return                          # no key, no hashed row
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "surface": surface, "text_sha": _h(text, salt),
               "would_card": bool(p)}
        if p:
            row.update(action=p.verb.action, count=len(p.refs), rule=p.method[:60],
                       refs_sha=_h("|".join(p.refs), salt), label_sha=_h(p.label, salt) if p.label else "")
        else:
            row["refused"] = why_not[:120]
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


# ── the route the router uses ───────────────────────────────────────────────

def decide(pa: Any, text: str, *, surface: str = "voice", stage: Optional[dict] = None,
           settings: Optional[dict] = None, conversation_id: Any = None) -> Optional[Proposal]:
    """A minted proposal when the setting is ``on`` and the change is allowed;
    ``None`` otherwise (shadow writes its log line and returns None). Only the owner
    surfaces in ``SURFACES`` propose or log. Never raises."""
    try:
        m = mode(settings)
        if m == "off" or surface not in SURFACES:
            return None
        p, why = build(pa, stage=stage)
        if m == "shadow":
            if getattr(pa, "decision", "") == "defer":
                shadow(p, why, text, surface)
            return None
        if not p:
            return None
        p.conversation_id = str(conversation_id or "").strip()
        if not p.conversation_id:
            return None             # a ticket with no conversation would bind to nothing: no card
        p.surface = surface
        return mint(p)
    except Exception as e:      # noqa: BLE001
        _log.debug("safe write skipped: %s", e)
        return None


def is_card_route(tool: Any, args: Any) -> bool:
    """True only for the exact call a live proposal makes: the tool, the verb
    and a pending ticket, and nothing else in the arguments. The router lets
    this one tool route through (it raises a card; it changes nothing)."""
    try:
        if tool != TOOL or not isinstance(args, dict) or set(args) != {"action", "laya_write"}:
            return False
        spec = args.get("laya_write")
        if not isinstance(spec, dict) or set(spec) != {"ticket"}:
            return False
        p = peek(spec.get("ticket"))
        return bool(p and p.verb.action == str(args.get("action")) and mode() == "on")
    except Exception:
        return False
