"""Email paragraphs about money under the cautious cloud consent: the owner decides, never a silent hold.

Under the "cloud_guarded" consent the egress gate holds back email paragraphs it classifies as private, and
money (an invoice, a payment, a price, a deposit) is the commonest. A held paragraph used to be announced to
the model and nothing more: the owner never saw what was held or had a say. Now (the owner's choice,
Settings > Privacy & Data: `email_cloud_release`, "ask" by default):

* when the gate would withhold a money paragraph from a cloud seat in an email or thread tool result, an
  approval card shows EXACTLY the text that would go (per paragraph, as the cloud would receive it, with
  identifiers already replaced), with Send / Keep on this PC and ticks to remember the answer for this
  thread or this sender;
* while the card waits, the paragraph stays on this PC and the model is told plainly what was withheld and
  that the owner is being asked (the result's `privacy_gate` line), so nothing is dropped silently;
* Send lets those exact paragraphs go on this conversation's next cloud calls for RELEASE_TTL_S (the
  turn), and, with a tick, every money paragraph of that thread or sender from then on; Keep on this PC
  keeps today's behaviour (announced withholding) and is not asked again for the same paragraphs;
* "withhold" keeps every such paragraph on this PC without a card, as before.

Only where the text would leave the PC: when the seat receiving the email result is Friday's own local
model there is no card and no read-back, and the paragraphs reach it untouched (egress_gate passes an
email result for a local destination whole). The card appears at the point a later call would carry the
text to a cloud model: the same conversation escalating to a cloud seat seals its history then.

The never-send list and hard identifiers (account and card numbers) are never offered: they stay home
whatever anyone answers. The remembered choices are listed in Settings with Forget.

Card data contract (kept small so a per-source cloud card made elsewhere can merge with it):
    payload = {"handler": "cloud_release", "what": [paragraph, ...], "why": str,
               "scope": {"once": True, "thread": <thread ref or "">, "sender": <address or "">},
               "items": [{"hash", "text", "thread", "sender"}]}
and the answer's `remember` is {"thread": bool, "sender": bool} (POST /api/privacy/cloud-release/<id>/decide).
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import threading
import time
import uuid

_log = logging.getLogger("friday.cloud_release")

HANDLER = "cloud_release"
APPROVAL_KIND = "cloud_release"
SUBJECT_TYPE = "cloud_release"
#: How long a Send (without a remembered scope) lets the paragraphs go: the turn it was asked in.
RELEASE_TTL_S = 30 * 60
#: Paragraphs the owner kept on this PC are not asked about again (kept this many, newest).
DECLINED_MAX = 1000
REMEMBERED_MAX = 200
#: Who may say Send: a click on the card on Friday's own page (owner:ui, screen_click), or the owner's
#: own words after the exact text was read back (owner:voice / owner:chat, answer_in_words).
SEND_DECIDERS = frozenset({"owner:ui", "owner:voice", "owner:chat"})

_LOCK = threading.RLock()
#: The pii lookup (placeholder -> real value) of the call being sealed, so a scrubbed sender can be named.
LOOKUP: contextvars.ContextVar[dict | None] = contextvars.ContextVar("cloud_release_lookup", default=None)

#: A paragraph about money: an amount with a currency, or the words an invoice, a bill or a payment uses.
_MONEY = re.compile(
    r"(?:[$£€¥₹]\s?\d|\d[\d,.]*\s?(?:usd|eur|gbp|dollars?|pounds?|euros?|k\b)|"
    r"\b(?:invoice[sd]?|payments?|pay(?:ing|able)?|paid|deposits?|balance|refunds?|bill(?:ed|ing|s)?|"
    r"quote[sd]?|fees?|price[sd]?|costs?|amount due|remit(?:tance)?|wire transfer|budget|"
    r"receipts?|charge[sd]?|subtotal|total due|owed?)\b)", re.I)


def is_money(text: str) -> bool:
    return bool(_MONEY.search(str(text or "")))


def _hash(text: str) -> str:
    return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()[:24]


def _never_offer(text: str) -> bool:
    """The floor: never-send material and hard identifiers are not the owner's to release here."""
    try:
        from agent_friday.services import judgment_gate as jg
        return bool(jg.never_send_hits(text) or jg.hard_identifier_hits(text))
    except Exception:
        return True


def _choice() -> str:
    try:
        from agent_friday.services import owner_choices as _oc
        return _oc.email_cloud_release()
    except Exception:
        return "ask"


def guarded_consent() -> bool:
    try:
        from agent_friday.privacy import cloud_consent as _cc
        st = _cc.resolve()
        return bool(st.answered and st.choice == _cc.CHOICE_GUARDED)
    except Exception:
        return False


# ── store: remembered scopes (on disk), once-releases and declines ──────────

def _path():
    from agent_friday import paths
    return paths.friday_home() / "cloud_release.json"


def _read() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def remembered() -> list:
    """The owner's remembered Sends: [{id, scope: thread|sender, value, label, at}], newest first."""
    return list(reversed(_read().get("remembered") or []))


def remember(scope: str, value: str, label: str = "") -> dict | None:
    value = str(value or "").strip()
    if scope not in ("thread", "sender", "text") or not value:
        return None
    with _LOCK:
        d = _read()
        rows = [r for r in d.get("remembered") or [] if not (r.get("scope") == scope and r.get("value") == value)]
        row = {"id": "rel_%s" % uuid.uuid4().hex[:10], "scope": scope, "value": value,
               "label": str(label or value)[:200], "at": time.time()}
        rows.append(row)
        d["remembered"] = rows[-REMEMBERED_MAX:]
        _write(d)
    _log.info("cloud release remembered for a %s", scope)
    return row


def forget(row_id: str) -> bool:
    with _LOCK:
        d = _read()
        rows = d.get("remembered") or []
        keep = [r for r in rows if r.get("id") != row_id]
        if len(keep) == len(rows):
            return False
        d["remembered"] = keep
        _write(d)
    _log.info("cloud release forgotten: %s", row_id)
    return True


def _once_hashes(now=None) -> set:
    now = time.time() if now is None else now
    d = _read()
    return {h for h, until in (d.get("once") or {}).items() if float(until or 0) > now}


def _key(h: str, provider: str = "") -> str:
    """A release is for the destination the card named: "<hash>@<provider>". A card that named
    no provider releases to none ("@?" matches no destination). A bare key in the store is an
    older release (any provider), still honoured until it runs out."""
    return "%s@%s" % (h, provider or "?")


def _is_released_key(h: str, provider: str, keys: set) -> bool:
    return (bool(provider) and _key(h, provider) in keys) or h in keys


def _release_once(hashes, provider: str = "") -> None:
    now = time.time()
    with _LOCK:
        d = _read()
        once = {h: u for h, u in (d.get("once") or {}).items() if float(u or 0) > now}
        for h in hashes:
            once[_key(h, provider)] = now + RELEASE_TTL_S
        d["once"] = once
        _write(d)


def _decline(hashes) -> None:
    with _LOCK:
        d = _read()
        dec = [h for h in d.get("declined") or [] if h not in set(hashes)] + list(hashes)
        d["declined"] = dec[-DECLINED_MAX:]
        _write(d)


def _declined() -> set:
    return set(_read().get("declined") or [])


# ── reading an email-shaped tool result ─────────────────────────────────────

# Bounded, and neither side may contain "@", so matching is linear.
_ADDR = re.compile(r"[^\s<>\"',;@]{1,64}@[^\s<>\"',;@]{1,255}")


def looks_like_mail(text) -> bool:
    """Cheap test before parsing: a Gmail tool result (search_email hits or a thread read)."""
    t = str(text or "")
    return '"gmail"' in t and '"messages"' in t


def _sender(raw: str, lookup: dict | None) -> str:
    """The sender's address, a scrubbed placeholder put back from this call's lookup."""
    s = str(raw or "")
    for tag, real in (lookup or {}).items():
        if tag and tag in s:
            s = s.replace(tag, str(real))
    m = _ADDR.search(s)
    return m.group(0).lower() if m else ""


def _messages(parsed) -> list:
    """[(index, message dict, thread ref, sender address)] for an email-shaped result, else []."""
    if not isinstance(parsed, dict) or parsed.get("source") != "gmail":
        return []
    msgs = parsed.get("messages")
    if not isinstance(msgs, list):
        return []
    lookup = LOOKUP.get()
    out = []
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        thread = str(parsed.get("thread") or m.get("ref") or "")
        out.append((i, m, thread, _sender(m.get("from"), lookup)))
    return out


def _paras(text: str) -> list:
    t = str(text or "")
    sep = "\n\n" if "\n\n" in t else ("\n" if "\n" in t else None)
    return [p for p in (t.split(sep) if sep else [t]) if p.strip()]


def _scopes() -> tuple:
    rows = _read().get("remembered") or []
    return ({r["value"] for r in rows if r.get("scope") == "thread"},
            {r["value"] for r in rows if r.get("scope") == "sender"})


def released(parsed, provider: str = "") -> set:
    """The stripped paragraphs of this result the owner let go: a Send for these exact paragraphs (the
    turn, to the provider its card named), or a remembered thread or sender (its money paragraphs).
    The gate still runs every other check on everything else."""
    rows = _messages(parsed)
    if not rows:
        return set()
    once = _once_hashes()
    threads, senders = _scopes()
    out = set()
    for _i, m, thread, sender in rows:
        scoped = (thread and thread in threads) or (sender and sender in senders)
        for field in ("text", "snippet"):
            for p in _paras(m.get(field)):
                ps = p.strip()
                if _is_released_key(_hash(ps), provider, once) or (scoped and is_money(ps)):
                    if not _never_offer(ps):
                        out.add(ps)
    return out


def _withheld_money(parsed, gated) -> list:
    """[{hash, text, thread, sender, path}] for the money paragraphs the gate held back from this result."""
    out = []
    g_msgs = gated.get("messages") if isinstance(gated, dict) else None
    if not isinstance(g_msgs, list):
        return out
    for i, m, thread, sender in _messages(parsed):
        gm = g_msgs[i] if i < len(g_msgs) and isinstance(g_msgs[i], dict) else {}
        for field in ("text", "snippet"):
            orig = m.get(field)
            if not isinstance(orig, str) or not orig.strip() or gm.get(field) == orig:
                continue
            sent = str(gm.get(field) or "")
            for p in _paras(orig):
                ps = p.strip()
                if ps in sent or not is_money(ps) or _never_offer(ps):
                    continue
                out.append({"hash": _hash(ps), "text": ps, "thread": thread, "sender": sender,
                            "path": "messages[%d].%s" % (i, field)})
    return out


def to_cloud(provider: str = "", model: str = "") -> bool:
    """Would the text go to a cloud model? The card is about text leaving the PC, so it is keyed on the
    actual destination, never on the consent setting alone. Unsure means cloud."""
    try:
        from agent_friday.services import egress_gate as _eg
        return _eg.destination_is_cloud(provider, model)
    except Exception:
        return True


def offer(parsed, gated, provider: str = "", model: str = "") -> str:
    """After the gate ran on an email result: raise (or find) the card for the money paragraphs it held
    back, and return the line the model is told, or "" when there is nothing to ask. Never raises.
    No card when the destination is Friday's own local model: nothing leaves the PC there."""
    try:
        if _choice() != "ask" or not guarded_consent():
            return ""
        if not to_cloud(provider, model):
            return ""
        items = _withheld_money(parsed, gated)
        if not items:
            return ""
        declined = _declined()
        ask = [it for it in items if it["hash"] not in declined]
        if not ask:
            return ("%d paragraph(s) about money were held back on this PC: the owner chose to keep them "
                    "here. Say so; do not guess what they said." % len(items))
        card = _raise_card(ask, provider)
        status = (card or {}).get("status")
        if status == "approved":
            return ""                       # released on the next call
        return ("%d paragraph(s) about money (%s) were held back on this PC under the owner's cautious "
                "cloud setting. An approval card shows the owner exactly that text and asks whether it may "
                "go to the cloud model; until they answer it stays here. Tell the owner which messages are "
                "affected and that the card is waiting. Do not guess what the paragraphs said."
                % (len(ask), ", ".join(sorted({it["path"] for it in ask}))[:300]))
    except Exception as e:
        _log.warning("cloud release card not raised: %s", e)
        return ""


def _raise_card(items: list, provider: str = "") -> dict | None:
    from agent_friday.services import approvals as _ap
    threads = sorted({it["thread"] for it in items if it["thread"]})
    senders = sorted({it["sender"] for it in items if it["sender"]})
    subject = _hash("|".join(sorted(it["hash"] for it in items)))
    who = senders[0] if len(senders) == 1 else ("%d senders" % len(senders) if senders else "an email")
    texts = [it["text"] for it in items]
    shown = "\n\n".join("“%s”" % t for t in texts)
    payload = {
        "handler": HANDLER,
        "what": texts,
        "why": ("Your cautious cloud setting keeps paragraphs about money on this PC. The cloud model "
                "(%s) asked to read this email; these are exactly the words it would receive." % (provider or "cloud")),
        "scope": {"once": True, "thread": threads[0] if len(threads) == 1 else "",
                  "sender": senders[0] if len(senders) == 1 else ""},
        "items": [{k: it[k] for k in ("hash", "text", "thread", "sender")} for it in items],
        "provider": provider,
    }
    rec = _ap.create_approval(
        kind=APPROVAL_KIND, subject_type=SUBJECT_TYPE, subject_id=subject,
        title=("Send %d paragraph(s) about money from %s to the cloud model?" % (len(items), who))[:200],
        description=("Send lets exactly the text below go to the cloud model for this conversation. "
                     "Keep on this PC keeps it here, and the model is told it was withheld.")[:2000],
        action_description=("Send to the cloud model:\n\n" + shown)[:1000],
        payload=payload, force_gate=True, action_class="internal", requested_by="egress_gate")
    _mark_pending([it["hash"] for it in items], (rec or {}).get("approval_id"))
    return rec


# ── the answer ──────────────────────────────────────────────────────────────

def decide(approval_id: str, decision: str, remember_scope=None, decided_by: str = "owner:ui") -> dict:
    """Send (approve) or Keep on this PC (deny) for one card, with the ticks: remember_scope =
    {"thread": bool, "sender": bool}. Returns {ok, approval, won} or {ok: False, error}."""
    from agent_friday.services import approvals as _ap
    rec = _ap.get_approval(approval_id)
    if not rec or (rec.get("payload") or {}).get("handler") != HANDLER:
        return {"ok": False, "error": "not found"}
    if decision not in ("approve", "deny"):
        return {"ok": False, "error": "decision is approve (Send) or deny (Keep on this PC)"}
    if decision == "approve" and decided_by not in SEND_DECIDERS:
        return {"ok": False, "error": "Send is a click on the card on Friday's screen"}
    want = remember_scope if isinstance(remember_scope, dict) else {}
    if rec.get("status") == "pending" and decision == "approve":
        _ap._patch(approval_id, payload=dict(rec.get("payload") or {},
                                             remember={"thread": bool(want.get("thread")),
                                                       "sender": bool(want.get("sender")),
                                                       "text": bool(want.get("text"))}))
    if decision == "approve":
        # The server's own mark that THIS path decided it (the dedicated route after its screen
        # check, or answer_in_words after a read-back). A card approved through any other route
        # carries a label a request body could have claimed, so it releases nothing.
        with _SEND_LOCK:
            _SERVER_SEND.add(approval_id)
    try:
        out, won = _ap.decide_with_outcome(approval_id, decision, decided_by=decided_by)
    finally:
        with _SEND_LOCK:
            _SERVER_SEND.discard(approval_id)
    return {"ok": True, "approval": out, "won": won}


#: Approval ids being approved through decide() right now (see there). In-process only.
_SERVER_SEND: set = set()
_SEND_LOCK = threading.Lock()


def _on_decision(record: dict) -> None:
    payload = record.get("payload") or {}
    if payload.get("handler") != HANDLER:
        return
    status = (record.get("status") or "").lower()
    hashes = [it.get("hash") for it in payload.get("items") or [] if it.get("hash")]
    if status in ("approved", "denied", "expired"):
        _clear_pending(hashes)
    if status == "approved":
        with _SEND_LOCK:
            ours = record.get("approval_id") in _SERVER_SEND
        if not ours or record.get("decided_by") not in SEND_DECIDERS:
            # Approved by a path that is not the owner's click or their own words after a read-back
            # (another approvals route, a relayed or programmatic decision): nothing is released.
            _log.warning("cloud release: a Send decided by %r outside the card's own path was not "
                         "honoured; nothing released", record.get("decided_by"))
            return
    provider = str(payload.get("provider") or "")
    if status == "approved":
        conv = str(payload.get("conversation") or "") if payload.get("general") else ""
        # A held-detail card asked in a conversation releases for that conversation only (what the
        # card says); anything else (email money, no conversation) for RELEASE_TTL_S, as before.
        if not conv:
            _release_once(hashes, provider)
        rem = payload.get("remember") or {}
        scope = payload.get("scope") or {}
        if rem.get("thread") and scope.get("thread"):
            remember("thread", scope["thread"], "the email thread %s" % scope["thread"])
        if rem.get("sender") and scope.get("sender"):
            remember("sender", scope["sender"], "mail from %s" % scope["sender"])
        if rem.get("text") and scope.get("text"):
            for it in payload.get("items") or []:
                if it.get("hash"):
                    remember("text", _key(it["hash"], provider), "“%s” to %s"
                             % (_short(it.get("text")), provider or "any cloud model"))
        if conv:
            _release_for_conversation(conv, hashes, provider)
        _log.info("cloud release: owner sent %d paragraph(s) (thread=%s sender=%s)", len(hashes),
                  bool(rem.get("thread")), bool(rem.get("sender")))
    elif status in ("denied", "expired"):
        # The exact words. The same content with a new time or count is not asked again in that
        # conversation (its ledger); another conversation may ask once.
        _decline(hashes)
        _log.info("cloud release: %d paragraph(s) kept on this PC (%s)", len(hashes), status)


# ── the answer in words (answer_card): a read-back first, then a yes sends once ─────────────

#: A read-back is good for this long: the yes must come after it, in the same conversation, soon.
READBACK_TTL_S = 10 * 60
_READBACKS: dict = {}          # approval_id -> (hash of the owner's words when it was read back, time)
_KEEP_RE = re.compile(r"(?i)\bkeep\b.{0,20}\b(here|on (this|the|my) (pc|computer|machine)|local(ly)?|private)\b")
_THREAD_RE = re.compile(r"(?i)\b(thread|conversation)\b")
_SENDER_RE = re.compile(r"(?i)\b(sender|from (him|her|them)|this person|(everything|anything|always) from)\b")


def is_card(approval_id: str) -> bool:
    try:
        from agent_friday.services import approvals as _ap
        rec = _ap.get_approval(str(approval_id or "").strip())
        return bool(rec and (rec.get("payload") or {}).get("handler") == HANDLER)
    except Exception:
        return False


def _words_hash(text: str) -> str:
    return _hash(re.sub(r"\s+", " ", str(text or "")).strip().lower())


def readback_line(rec: dict) -> str:
    """What Friday says before a spoken Send: exactly the paragraphs that would go, and the choices."""
    p = rec.get("payload") or {}
    scope = p.get("scope") or {}
    texts = list(p.get("what") or [])
    extra = []
    if scope.get("thread"):
        extra.append("remember this thread")
    if scope.get("sender"):
        extra.append("remember this sender")
    return ("Here is exactly what would go to the cloud model%s: %s. Shall I send it, once? Say send, or keep it "
            "here.%s" % (" (%d paragraphs)" % len(texts) if len(texts) > 1 else "",
                         " ".join("“%s”" % t for t in texts),
                         (" You can also say " + " or ".join(extra) + ".") if extra else ""))


def answer_in_words(approval_id: str, claimed: str, owner_words: str, *, spoken: bool,
                    room_mode: bool = False, cloud_voice: bool = False) -> dict:
    """answer_card for a money-paragraph card, from typed or spoken words.

    Keep on this PC: the owner's own no (or "keep it here") decides it at once. Send: only AFTER Friday
    has read back exactly the paragraphs that would go (the first call returns that line to say, word
    for word, and records it), and the owner's own words since then say yes. Then the paragraphs go
    once; "remember this thread" or "this sender" in those same words remembers it, as the card's ticks
    do. In a room of several people the money is not read aloud, and a cloud voice (`cloud_voice`) is
    never handed the text: the card is answered on screen. Typed, the read-back points at the card on
    the screen, which shows exactly the text (the chat model may be a cloud one)."""
    from agent_friday.services import approvals as _ap
    rec = _ap.get_approval(str(approval_id or "").strip())
    if not rec or (rec.get("payload") or {}).get("handler") != HANDLER:
        return {"ok": False, "text": "NOT RECORDED: there is no money-paragraph card with that id."}
    if rec.get("status") != "pending":
        return {"ok": False, "text": "NOT RECORDED: that card is already %s." % rec.get("status")}
    words = str(owner_words or "")
    if not words.strip():
        return {"ok": False, "text": "NOT RECORDED: there are no words from the user to go by."}
    want = {"approve": "approve", "yes": "approve", "send": "approve", "decline": "deny", "deny": "deny",
            "no": "deny", "keep": "deny"}.get(str(claimed or "").strip().lower())
    if want is None:
        return {"ok": False, "text": "NOT RECORDED: the decision is approve (Send) or decline (Keep on this PC)."}
    from agent_friday.services import local_context as _lc
    from agent_friday.services import item_actions as _ia
    if spoken:
        verdict = _lc.spoken_decision(words, room_mode)
    else:
        verdict = _ia.typed_decision(words)
    if verdict is None and _KEEP_RE.search(words):
        verdict = "deny"
    if want == "deny":
        if verdict != "deny":
            return {"ok": False, "text": "NOT RECORDED: the user's own words did not keep it here. Ask them."}
        out = decide(rec["approval_id"], "deny", decided_by="owner:%s" % ("voice" if spoken else "chat"))
        _READBACKS.pop(rec["approval_id"], None)
        return {"ok": bool(out.get("ok")), "text": "Recorded: kept on this PC. Nothing was sent to the cloud model."}
    if spoken and room_mode:
        return {"ok": False, "text": ("NOT RECORDED: there are other people in the room, so the money details are "
                                      "not read aloud. Tell the user the card is on their screen: Send or Keep on "
                                      "this PC there.")}
    if spoken and cloud_voice:
        return {"ok": False, "text": ("NOT RECORDED: this voice runs on a cloud model, so reading the money text "
                                      "aloud would send it there before the user said yes. Tell the user the card "
                                      "is on their screen with exactly the words: Send or Keep on this PC there, "
                                      "or switch to local voice to hear it read back.")}
    rb = _READBACKS.get(rec["approval_id"])
    fresh = rb is not None and time.time() - rb[1] <= READBACK_TTL_S
    if not fresh or rb[0] == _words_hash(words):
        # Not read back yet (or the user has not answered since): read it back, and ask.
        _READBACKS[rec["approval_id"]] = (_words_hash(words), time.time())
        if not spoken:
            return {"ok": False, "readback": True, "text": (
                "NOT SENT YET: the card on the user's screen shows exactly the words that would go. Ask them to "
                "read it and say send (or keep it here), then call answer_card again with their answer.")}
        return {"ok": False, "readback": True, "text": (
            "NOT SENT YET: first read this back to the user word for word, then wait for their answer and call "
            "answer_card again: " + readback_line(rec))}
    if verdict == "revise":
        return {"ok": False, "text": ("NOT RECORDED: their yes had a condition, so it is not a yes to exactly this "
                                      "text. Nothing was sent. Tell them the card can only send these words as "
                                      "they are, or keep them here.")}
    if verdict != "approve":
        return {"ok": False, "text": "NOT RECORDED: the user's own words did not say send. Ask them directly."}
    scope = (rec.get("payload") or {}).get("scope") or {}
    remember = {"thread": bool(scope.get("thread") and _THREAD_RE.search(words)),
                "sender": bool(scope.get("sender") and _SENDER_RE.search(words))}
    out = decide(rec["approval_id"], "approve", remember,
                 decided_by="owner:%s" % ("voice" if spoken else "chat"))
    _READBACKS.pop(rec["approval_id"], None)
    if not out.get("ok") or not out.get("won"):
        return {"ok": False, "text": "That card was already decided."}
    kept = [k for k in ("thread", "sender") if remember[k]]
    return {"ok": True, "remembered": kept, "text": (
        "Recorded: sent once. Those exact paragraphs go to the cloud model for this conversation"
        + ("; from now on money paragraphs from this %s go without asking (Settings > Privacy & Data > "
           "Money paragraphs in email, Forget, takes it back)" % " and this ".join(kept) if kept else "")
        + ".")}


# ── every held detail gets a card (owner ruling, 2026-10-17) ─────────────────────────────
#
# "No silent holds": whenever the gate keeps a private detail back from a cloud model (any consent
# but "Cloud for everything", which holds nothing but the never-send floor), the owner sees a card
# with EXACTLY the text that would go (as the cloud would receive it, identifiers already replaced)
# and WHERE it would go (the provider and model, and which part of the request), with Send / Keep on
# this PC and a tick to always send that exact text. Send lets those exact words go on this
# conversation's next cloud calls (RELEASE_TTL_S); Keep on this PC is not asked again. The never-send
# floor and hard identifiers are never offered: they stay refused, and the refusal says why
# (egress_gate.NeverSendBlocked).
#
# The gate notes each held span (note_held) inside a collecting() scope opened by seal_outbound and by
# _gate_text; the OUTERMOST scope raises at most one card for the spans not already on a waiting card,
# declined, released or remembered. Money paragraphs from email keep their own card (offer, above),
# and the owner's "keep them without asking" choice for those (email_cloud_release "withhold") holds.

#: The gate's own self-test and probe battery seal planted text: never a card, never a release.
_PROBE_PROVIDERS = frozenset({"selftest-cloud", "probe-cloud"})
#: Off switch for the test suite's shared homes (tests opt in); always on in the product.
HELD_CARDS = True
#: Spans held in the current outermost gate call: None outside a collecting() scope.
_HELD: contextvars.ContextVar = contextvars.ContextVar("cloud_release_held", default=None)
#: The release store read once per outermost scope (every paragraph checks it).
_SNAP: contextvars.ContextVar = contextvars.ContextVar("cloud_release_snap", default=None)
#: True while the gate judges an email-shaped tool result (egress_gate._gate_text_held), so the
#: owner's "keep money paragraphs in email without asking" choice applies to those spans only.
IN_MAIL: contextvars.ContextVar = contextvars.ContextVar("cloud_release_in_mail", default=False)

# ── one card per conversation per distinct text (the per-turn background context guard) ──────
#
# Friday's background context is rebuilt every turn, and a part of it that changes each time (a clock,
# a count, a rotating summary) would otherwise be a "new" text, and a new card, on every turn. So:
#   * texts are compared by CONTENT (case, spacing and digits ignored: `content_key`) and provider;
#   * a conversation shows at most one card for a given content and provider, whatever the answer (or
#     none): the ledger "carded" remembers it for the conversation;
#   * while a conversation has a card waiting for a provider, details held meanwhile wait for it (still
#     kept on this PC, and the model is told), and are asked on a later turn once it is answered;
#   * Send on a card asked in a conversation lets its EXACT words go for the rest of that conversation
#     (what the card says); with no conversation, for RELEASE_TTL_S. The same content changed (a new
#     time) stays on this PC and is not asked again in that conversation: the card says so;
#   * Keep on this PC is for the exact words (and, through the ledger, the conversation's content).
# Calls with no conversation (voice, background jobs) share one bucket that forgets after NO_CONV_TTL_S.
# Inside a web request the conversation is kept on the request (flask.g), never on the thread.

#: The conversation whose turn is being sent: set by the chat routes beside turn_plan.begin_turn.
CONVERSATION: contextvars.ContextVar = contextvars.ContextVar("cloud_release_conversation", default=None)
CARDED_CONVERSATIONS_MAX = 200
CARDED_PER_CONVERSATION_MAX = 500
NO_CONV_TTL_S = 6 * 60 * 60
_DIGITS = re.compile(r"\d+")


_G_KEY = "friday_cloud_release_conversation"


def _in_request() -> bool:
    try:
        from flask import has_request_context
        return bool(has_request_context())
    except Exception:
        return False


def set_conversation(conversation_id):
    """Name the conversation these cloud calls belong to (None: none). Inside a web request it is kept
    on the request (flask.g), so it ends with the request and a later request on a reused thread never
    inherits it; elsewhere it is this thread's context (returns the ContextVar token)."""
    cid = (str(conversation_id).strip() or None) if conversation_id else None
    if _in_request():
        from flask import g
        setattr(g, _G_KEY, cid)
        return None
    return CONVERSATION.set(cid)


def _conversation() -> str:
    if _in_request():
        from flask import g
        c = getattr(g, _G_KEY, None)     # never the thread's leftover ContextVar inside a request
    else:
        c = CONVERSATION.get()
    if not c:
        # A tool's own model call runs with the agent's conversation set (agent._CURRENT_CONVERSATION,
        # set and reset around each tool call).
        import sys
        ag = sys.modules.get("agent_friday.services.agent")
        try:
            c = ag._CURRENT_CONVERSATION.get() if ag is not None else None
        except Exception:
            c = None
    return str(c or "").strip()


def content_key(text: str) -> str:
    """The text's content for comparing cards: case, spacing and digits ignored, so a clock or a count
    that changes every turn does not make it a new text. Never used to release anything: Send and
    "always send" are for the exact words."""
    t = _DIGITS.sub("#", " ".join(str(text or "").lower().split()))
    return "c:" + _hash(t)


def _ckey(text: str, provider: str) -> str:
    return "%s@%s" % (content_key(text), provider or "?")


def _carded(d: dict, conv: str, now: float) -> dict:
    """This conversation's ledger: {content@provider: [time, approval_id]} (no-conversation entries age out)."""
    out = {}
    for k, v in dict(((d.get("carded") or {}).get(conv) or {})).items():
        try:
            at, aid = float(v[0] or 0), str(v[1] or "")
        except Exception:
            continue                     # a damaged row is dropped, not fatal
        if conv or now - at < NO_CONV_TTL_S:
            out[k] = [at, aid]
    return out


def _note_carded(conv: str, keys, approval_id) -> None:
    now = time.time()
    with _LOCK:
        d = _read()
        allc = dict(d.get("carded") or {})
        rows = _carded(d, conv, now)
        for k in keys:
            rows[k] = [now, approval_id or ""]
        allc.pop(conv, None)
        allc[conv] = dict(list(rows.items())[-CARDED_PER_CONVERSATION_MAX:])
        d["carded"] = dict(list(allc.items())[-CARDED_CONVERSATIONS_MAX:])
        _write(d)


def _conv_released(conv: str) -> set:
    if not conv:
        return set()
    return set((_read().get("conv_released") or {}).get(conv) or [])


def _release_for_conversation(conv: str, hashes, provider: str) -> None:
    if not conv:
        return
    with _LOCK:
        d = _read()
        allr = dict(d.get("conv_released") or {})
        keep = list(allr.pop(conv, None) or [])
        keep += [_key(h, provider) for h in hashes if _key(h, provider) not in keep]
        allr[conv] = keep[-CARDED_PER_CONVERSATION_MAX:]
        d["conv_released"] = dict(list(allr.items())[-CARDED_CONVERSATIONS_MAX:])
        _write(d)


def _waiting_ids() -> set:
    try:
        from agent_friday.services import approvals as _ap
        return {r.get("approval_id") for r in _ap.list_approvals(status="pending", kind=APPROVAL_KIND)}
    except Exception:
        return set()


def _short(text, n: int = 60) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


def _mark_pending(hashes, approval_id) -> None:
    if not approval_id:
        return
    with _LOCK:
        d = _read()
        pend = dict(d.get("pending") or {})
        for h in hashes:
            pend[h] = approval_id
        d["pending"] = dict(list(pend.items())[-DECLINED_MAX:])
        _write(d)


def _clear_pending(hashes) -> None:
    with _LOCK:
        d = _read()
        pend = dict(d.get("pending") or {})
        if not any(h in pend for h in hashes):
            return
        for h in hashes:
            pend.pop(h, None)
        d["pending"] = pend
        _write(d)


def _pending() -> set:
    """Hashes on a card that is still waiting (a card that expired or was decided clears its own)."""
    pend = _read().get("pending") or {}
    if not pend:
        return set()
    try:
        from agent_friday.services import approvals as _ap
        waiting = {r.get("approval_id") for r in _ap.list_approvals(status="pending", kind=APPROVAL_KIND)}
        return {h for h, aid in pend.items() if aid in waiting}
    except Exception:
        return set(pend)


def _remembered_texts() -> set:
    return {r.get("value") for r in _read().get("remembered") or [] if r.get("scope") == "text"}


def _released_keys() -> set:
    """Send-once keys still in time, and "always send" keys: read once per outermost scope."""
    snap = _SNAP.get()
    if snap is not None:
        return snap
    return _once_hashes() | _remembered_texts() | _conv_released(_conversation())


def is_released(text: str, provider: str = "") -> bool:
    """Did the owner let exactly this text go to this provider (Send on its card, still in time, or
    "always send this exact text")?"""
    if provider in _PROBE_PROVIDERS:
        return False
    try:
        return _is_released_key(_hash(str(text or "").strip()), provider, _released_keys())
    except Exception:
        return False


def _part(field: str) -> str:
    """Where in the request a held span sits, in plain words (`field` is the gate's own label)."""
    f = str(field or "")
    if f.startswith("system") or f == "voice_system_instruction":
        return "Friday's background context for this request"
    if "tool" in f:
        return "a tool result in this conversation"
    if f.startswith("message[") or f.startswith("live.text"):
        return "this conversation"
    if f.startswith("web_search"):
        return "a web search"
    return "this request (%s)" % f if f else "this request"


class collecting:
    """Context manager: collect what the gate holds back in this call; the outermost one offers it."""

    def __init__(self, provider: str = "", model: str = ""):
        self.provider, self.model = str(provider or ""), str(model or "")
        self._tok = self._snap_tok = None

    def __enter__(self):
        if _HELD.get() is None:
            self._tok = _HELD.set([])
            try:
                snap = _once_hashes() | _remembered_texts() | _conv_released(_conversation())
            except Exception:
                snap = None
            self._snap_tok = _SNAP.set(snap)
        return self

    def __exit__(self, *exc):
        if self._tok is None:
            return False
        items = _HELD.get() or []
        _HELD.reset(self._tok)
        _SNAP.reset(self._snap_tok)
        if items and exc[0] is None:
            offer_held(items, self.provider, self.model)
        return False


def note_held(text: str, field: str, provider: str) -> None:
    """The gate held this span back from a cloud model. Never raises."""
    try:
        held = _HELD.get()
        if held is None or not HELD_CARDS or provider in _PROBE_PROVIDERS:
            return
        t = str(text or "").strip()
        if not t or _never_offer(t):
            return
        h = _hash(t)
        if any(it["hash"] == h for it in held):
            return
        held.append({"hash": h, "text": t, "field": str(field or ""), "provider": str(provider or ""),
                     "mail": bool(IN_MAIL.get())})
    except Exception as e:
        _log.debug("held span not noted: %s", e)


def held_mark() -> int:
    """How many spans are noted so far in this scope (-1 outside one)."""
    held = _HELD.get()
    return -1 if held is None else len(held)


def held_back_to(mark: int) -> None:
    """Take back every note made since `held_mark()` returned `mark` (the gate is about to judge a
    value's parts instead of the whole value, or its lines)."""
    held = _HELD.get()
    if held is not None and 0 <= mark <= len(held):
        del held[mark:]


def offer_held(items: list, provider: str = "", model: str = "") -> dict | None:
    """Raise one card for the held spans not already on a waiting card, declined, released or
    remembered. Returns the card, or None. Never raises."""
    try:
        if not HELD_CARDS or provider in _PROBE_PROVIDERS or not to_cloud(provider, model):
            return None
        withhold_money = _choice() == "withhold"
        conv = _conversation()
        with _LOCK:     # two calls sealing the same history at once raise one card, not two
            skip = _declined() | _pending()
            released = _once_hashes() | _remembered_texts() | _conv_released(conv)
            ask = [it for it in items if it["hash"] not in skip
                   and not _is_released_key(it["hash"], provider, released)
                   and not (withhold_money and it.get("mail") and is_money(it["text"]))]
            if not ask:
                return None
            # One card per conversation per distinct content and provider, and one waiting at a time.
            d = _read()
            carded = _carded(d, conv, time.time())
            waiting = _waiting_ids()
            busy = {k for c in (d.get("carded") or {}) for k, v in _carded(d, c, time.time()).items()
                    if v[1] in waiting}
            seen, fresh = set(), []
            for it in ask:
                k = _ckey(it["text"], provider)
                if k in carded or k in busy or k in seen:
                    continue
                seen.add(k)
                fresh.append(it)
            if not fresh:
                return None
            suffix = "@%s" % (provider or "?")
            if any(k.endswith(suffix) and v[1] in waiting for k, v in carded.items()):
                return None     # this conversation's card for this provider is still on screen
            rec = _raise_held_card(fresh, provider, model, conv)
            if rec and rec.get("approval_id"):      # only a card that exists counts as shown
                _note_carded(conv, [_ckey(it["text"], provider) for it in fresh], rec["approval_id"])
            return rec
    except Exception as e:
        _log.warning("held-detail card not raised: %s", e)
        return None


def _raise_held_card(items: list, provider: str, model: str, conversation: str = "") -> dict | None:
    from agent_friday.services import approvals as _ap
    dest = provider or "the cloud provider"
    if model:
        dest = "%s (%s)" % (dest, model)
    parts = sorted({_part(it["field"]) for it in items})
    # The card shows every character Send would let go: nothing is cut short.
    texts = [it["text"] for it in items]
    payload = {
        "handler": HANDLER,
        "general": True,
        "what": texts,
        "where": {"provider": provider, "model": model, "parts": parts},
        "why": ("Your cloud setting keeps private details on this PC unless you say otherwise. These are "
                "exactly the words that would go to %s, from %s." % (dest, "; ".join(parts))),
        "scope": {"once": True, "thread": "", "sender": "", "text": True},
        "items": [{"hash": it["hash"], "text": it["text"], "thread": "", "sender": "",
                   "field": it["field"]} for it in items],
        "provider": provider,
        "model": model,
        "conversation": conversation,
    }
    # A fresh subject each time: create_approval returns an existing card for the same subject, and
    # the same words may need asking again once an earlier Send has run out. Waiting cards are not
    # duplicated: their spans are skipped above (_pending).
    subject = "held-" + uuid.uuid4().hex[:16]
    rec = _ap.create_approval(
        kind=APPROVAL_KIND, subject_type=SUBJECT_TYPE, subject_id=subject,
        title=("Send %d private detail(s) to %s?" % (len(items), dest))[:200],
        description=("Send lets exactly the text below go to %s on %s. If the same text comes back changed "
                     "(a new time or number), it stays on this PC and this conversation does not ask again. "
                     "Keep on this PC keeps it here, and %s is told it was withheld. Your never-send list "
                     "is never on a card: it stays refused."
                     % (dest, "this conversation's next cloud calls" if conversation
                        else "the next cloud calls in the next 30 minutes", "the model"))[:2000],
        action_description=("Send to %s:\n\n" % dest + "\n\n".join("“%s”" % t for t in texts))[:1000],
        payload=payload, force_gate=True, action_class="internal", requested_by="egress_gate")
    _mark_pending([it["hash"] for it in items], (rec or {}).get("approval_id"))
    _log.info("held-detail card raised: %d item(s) for %s", len(items), provider)
    return rec


_REGISTERED = False


def register_hooks() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        from agent_friday.services import approvals as _ap
        _ap.register_decision_hook(APPROVAL_KIND, _on_decision)
        _REGISTERED = True
    except Exception as e:
        _log.warning("could not register the cloud release hook: %s", e)


register_hooks()
