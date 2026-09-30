"""Ask the local model for private context, and share it only as the user approves.

The cloud voice model sometimes needs context only the user's own machine has
(the vault, the knowledge graph, the wiki, the calendar, preferences) to do
what he asked: "plan a weekend for the two of us" needs to know what they
like. It cannot read that itself. It asks here instead:

1. The local model (the brain seat) answers from local data. It marks every
   person it mentions as {{person: Name | relationship}}.
2. The answer is scrubbed: each person becomes a relationship placeholder
   ("[their partner]"), and structured identifiers (phone numbers, email and
   street addresses, card and ID numbers) become numbered placeholders
   ("[phone number 1]"). No original value is kept anywhere the cloud side can
   reach.
3. The egress gate's floor runs last: never-send material and hard
   identifiers stop the share outright, whatever he decides. Beyond the
   floor, the gate's usual tier judgement ("keep personal text on this PC")
   is exactly what the card asks him to overrule, so it is shown on the card
   instead of silently blanking it. The scrubbed text is the payload: the
   exact text the cloud model will receive, and every send is written to the
   egress audit log as owner-approved.
4. Unless a conversation-scoped grant covers it, an approval card shows that
   exact text, what each placeholder stands for (the category, never the
   value), which local model wrote it and which cloud model would receive it.
   He can send it, edit it, or decline it, on screen or by voice.
5. On approval the payload, unchanged, is handed to the live call. A declined
   or expired card sends nothing; the model is told only that he declined.

Edits are his words. If the scrubber would change what he saved, he is shown
the change and asked once more; his own version is sent if he keeps it
(never-send material is the one thing that stays refused).

Voice decisions (services/voice_engine: answer_share_request,
revise_share_request) count only when the user's own latest spoken words say
so, and in "Several people" room mode only when they name Friday: the cloud
model must not be able to approve its own request.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Callable, Optional

_log = logging.getLogger("friday.local_context")

KIND = "local_context_share"
#: The existing scoped, expiring grants (governance/action_gate) are keyed by
#: tool name and a scope; a conversation grant uses this name.
GRANT_TOOL = "share_local_context"
GRANT_TTL_S = 4 * 3600
GRANT_MAX_USES = 100

PERSON_RE = re.compile(r"\{\{\s*person\s*:\s*([^|}]+?)\s*\|\s*([^}]+?)\s*\}\}", re.I)
_PII_TAG_RE = re.compile(r"\[PII:([a-z]+):[0-9a-f]+\]")
_PII_WORDS = {"phone": "phone number", "email": "email address", "addr": "street address",
              "ssn": "ID number", "cc": "card number", "name": "person"}

RELAY_NOTE = (
    "A cloud voice model (Gemini Live) is helping the user and needs context only "
    "this machine has. Answer its question below from the user's own notes, "
    "calendar, memory and preferences, in a few plain sentences it can use. "
    "EVERY time you mention a person, write them as {{person: Name | how they "
    "relate to the user}}, for example {{person: Sam | their brother}} or "
    "{{person: Dana | a friend}}: the name is replaced by the relationship before "
    "anything leaves this machine. Leave out account numbers, passwords, health "
    "details and anything the question does not need. Do not mention this note.\n\n"
    "The question: ")

#: Payloads handed to a live call, newest last (what was actually sent).
SENT_LOG: list = []


# ── Scrubbing ────────────────────────────────────────────────────────────────

def _placeholder_for_relationship(rel: str) -> str:
    rel = re.sub(r"[\[\]{}]", "", str(rel or "")).strip() or "someone"
    return f"[{rel}]"


def scrub(text: str):
    """(scrubbed_text, placeholders) for text bound for a cloud model.

    placeholders is a list of {"placeholder", "category"}; never a value.
    Deterministic: the same text gives the same result.
    """
    text = str(text or "")
    placeholders = []

    def _add(ph, cat):
        if not any(p["placeholder"] == ph for p in placeholders):
            placeholders.append({"placeholder": ph, "category": cat})

    # Structured identifiers first, so a name inside an email address is removed
    # with the address instead of splitting it.
    try:
        from agent_friday.core import _scrub_pii
        out, lookup = _scrub_pii(text)
    except Exception as e:  # noqa: BLE001
        _log.warning("PII scrub unavailable (%s); withholding", type(e).__name__)
        return "", [{"placeholder": "(withheld)", "category": "privacy check unavailable"}]
    counters, pii = {}, []
    for tag in sorted(lookup or {}, key=lambda t: out.find(t)):
        m = _PII_TAG_RE.fullmatch(tag)
        word = _PII_WORDS.get(m.group(1) if m else "other", "private detail")
        counters[word] = counters.get(word, 0) + 1
        ph = f"[{word} {counters[word]}]"
        out = out.replace(tag, ph)
        pii.append((ph, ("an " if word[0] in "aeiou" else "a ") + word + " (removed)"))

    names = {}
    for m in PERSON_RE.finditer(out):
        names.setdefault(m.group(1).strip(), _placeholder_for_relationship(m.group(2)))
    out = PERSON_RE.sub(lambda m: _placeholder_for_relationship(m.group(2)), out)
    # The same person named again without the marker.
    for name, ph in sorted(names.items(), key=lambda kv: -len(kv[0])):
        if len(name) >= 2:
            out = re.sub(r"(?<![\w\[])" + re.escape(name) + r"(?![\w\]])", ph, out, flags=re.I)
        _add(ph, "a person (name removed)")
    for ph, cat in pii:
        _add(ph, cat)
    return out, placeholders


def _withheld_whole(text: str) -> bool:
    """Nothing may be shared: the floor refused it, or there is nothing left."""
    return not str(text or "").strip()


def floor_hits(text: str) -> list:
    """The egress floor: never-send material and hard identifiers. Moves for nothing."""
    try:
        from agent_friday.services import judgment_gate as jg
        return list(jg.never_send_hits(text) or []) + list(jg.hard_identifier_hits(text) or [])
    except Exception:
        return ["privacy floor unavailable"]          # cannot check: refuse


def gate_view(text: str) -> str:
    """What the usual egress gate would do with this text, in words, for the card."""
    try:
        from agent_friday.services import egress_gate as eg
        if eg.is_unrestricted_cloud():
            return "Your settings let cloud models see personal context; it still goes only if you send it."
        tier = eg._classify_cloud(text)
        if tier >= eg.Tier.PRIVATE:
            return ("Friday's privacy gate would normally keep this on your PC. It goes to the "
                    "cloud model only if you send it.")
        return "Nothing in this text is marked private by Friday's privacy gate."
    except Exception:
        return "The privacy gate's view could not be read; it goes only if you send it."


def prepare(raw_answer: str) -> dict:
    """Scrub a local answer and apply the floor: the exact payload, its placeholders
    and the gate's view. text is "" when the floor refuses it."""
    scrubbed, placeholders = scrub(raw_answer)
    if not scrubbed.strip() or floor_hits(scrubbed):
        return {"text": "", "placeholders": placeholders,
                "refused": "it includes something on the never-send list or a hard identifier"}
    return {"text": scrubbed, "placeholders": placeholders, "gate_view": gate_view(scrubbed)}


def _audit(approval_id, text):
    """The send, in the egress audit log, as owner-approved on this card."""
    try:
        from agent_friday.services import egress_gate as eg
        eg._log("google-gemini", "voice_local_context", eg._classify_cloud(text), "allow",
                f"owner-approved share card {approval_id} ({len(text)} chars)")
    except Exception:
        pass


# ── The local answer ─────────────────────────────────────────────────────────

def pick_local_seat() -> tuple:
    """(seat, spoken_note): which local model will read the private data.

    Order: the reasoning seat if it is already serving, then the sidekick if
    IT is, then either one cold. A seat that is already up answers in a second;
    a cold one can take a minute, which is why the note exists — the caller
    says it out loud before the wait rather than leaving a silence that reads
    as Friday ignoring the question.

    `seat` is None only when there is no local model at all. That case does
    NOT fall through to the cloud: the whole point of this path is that raw
    private data is read on this machine, so with nothing to read it, nothing
    is read.
    """
    from agent_friday.services import local_seats
    try:
        up = set(local_seats.serving() or {})
    except Exception:          # residency unknown: treat everything as cold
        up = set()
    brain = local_seats.resolve("brain")
    side = local_seats.resolve("sidekick")

    if brain and brain in up:
        return brain, ""
    if side and side in up:
        return side, ("Friday's main local model is not up, so her smaller "
                      "local sidekick is reading this one. Say that in one "
                      "short sentence.")
    if brain:
        return brain, ("Friday is starting her local model so it can read "
                       "this privately — that takes a moment. Say so in one "
                       "short sentence and keep the conversation going.")
    if side:
        return side, ("Friday is starting her local sidekick so it can read "
                      "this privately — that takes a moment. Say so in one "
                      "short sentence.")
    return None, ("Friday has no local model installed, so she cannot read "
                  "his private data without sending it to a cloud model, and "
                  "she will not do that. Tell him plainly, and offer to set "
                  "up a local model in Settings.")


def local_answer(question: str, seat: Optional[str] = None) -> tuple:
    """(text, local_model) from a local seat, or ("", None) when there is none."""
    from agent_friday.routes.voice import (_build_voice_system_prompt,
                                           _voice_user_message)
    from agent_friday.services.agent import _generate_agent, _load_settings
    settings = _load_settings() or {}
    if seat is None:
        seat, _note = pick_local_seat()
    if not seat:
        return "", None
    system, meta = _build_voice_system_prompt(settings)
    user = _voice_user_message(RELAY_NOTE + question, settings, volatile=meta.get("volatile"))
    text, _trace = _generate_agent(
        [{"role": "user", "content": user}], system=system, model=seat, max_tokens=600,
        session_ctx={"authenticated": True, "provider": "local", "is_voice": True,
                     "surface": "voice-live-relay"},
        workspace=settings.get("active_workspace") or "")
    return (text or "").strip(), seat


# ── The request, the card, the grant ────────────────────────────────────────

def _scope(conversation_id) -> str:
    return "conversation:" + str(conversation_id or "")


def _use_conversation_grant(conversation_id) -> Optional[dict]:
    if not conversation_id:
        return None
    from agent_friday.governance.action_gate import _use_grant
    return _use_grant(GRANT_TOOL, {"grant_scope": _scope(conversation_id)})


def grant_for_conversation(conversation_id) -> dict:
    """He allowed this kind of request for this conversation (from the card)."""
    from agent_friday.governance.action_gate import create_grant
    return create_grant(tools=[GRANT_TOOL], scope=_scope(conversation_id),
                        expires_in_seconds=GRANT_TTL_S, max_uses=GRANT_MAX_USES,
                        created_by="owner",
                        note="share local context with the cloud voice model in this conversation")


def _deliver(conversation_id, text, kind) -> bool:
    from agent_friday.services import voice_live_channel
    return voice_live_channel.deliver(conversation_id, text, kind=kind)


def sign_receipt(approval_id, text, payload=None, *, under_grant=None) -> None:
    """Sign a receipt naming exactly what left this machine. Raises on failure.

    The egress log line beside this records that a share happened and how many
    characters it was. That is enough to notice a share and not enough to
    audit one: it cannot answer "what did you tell Google about me?".

    So the receipt carries the payload itself. That is safe to keep because it
    is the text AFTER the scrub and the floor — the same text the owner read
    on the card and approved — so writing it down creates no copy of anything
    private that was not already cleared to leave. It also carries the
    placeholder categories, which say what was held back without saying what
    it was.

    It is signed with the governance key and appended to the decision BOM, so
    the record of a share cannot be edited afterwards without detection. Under
    off-record the signer trims it to its own key set, which is that mode
    working, not this one failing.

    Raises, deliberately: a share whose receipt cannot be written must not
    happen. An unauditable disclosure is the thing the receipt exists to
    prevent.
    """
    from agent_friday.governance import action_gate as ag
    p = payload or {}
    ag._receipt({
        "tool": "voice.local_context_share",
        "class": "egress",
        "decision": "granted" if under_grant else "owner_approved",
        "surface": "voice",
        "approval": approval_id,
        "grant": under_grant,
        "destination": p.get("cloud_model") or "(cloud voice model)",
        "local_model": p.get("local_model") or "(unknown)",
        "conversation": p.get("conversation_id"),
        "question": p.get("question"),
        "shared_text": text,
        "chars": len(text or ""),
        "withheld": [q.get("category") for q in (p.get("placeholders") or [])],
    })


def _send(approval_id, conversation_id, text, payload=None, *,
          under_grant=None) -> bool:
    """Hand the approved payload, exactly as shown, to the live call.

    The receipt is signed BEFORE the text leaves. If it cannot be signed the
    share does not happen: the point of the receipt is that nothing goes out
    unrecorded, which a receipt written afterwards cannot guarantee.
    """
    try:
        sign_receipt(approval_id, text, payload, under_grant=under_grant)
    except Exception as e:  # noqa: BLE001
        _log.error("holding a voice context share: its receipt could not be "
                   "signed (%s: %s)", type(e).__name__, e)
        _note_in_conversation(
            conversation_id,
            "Approved context was NOT sent: Friday could not write the signed "
            "record of what would leave the machine, so she held it back.")
        _deliver(conversation_id,
                 "That context was not sent: Friday could not record what "
                 "would have left the machine, so she held it back. Tell him "
                 "so in one sentence.", "notice")
        SENT_LOG.append({"approval_id": approval_id,
                         "conversation_id": conversation_id, "text": text,
                         "delivered": False, "held": "receipt_unsigned",
                         "at": time.time()})
        del SENT_LOG[:-50]
        return False
    ok = _deliver(conversation_id, text, "context")
    if ok:
        _audit(approval_id, text)
    SENT_LOG.append({"approval_id": approval_id, "conversation_id": conversation_id,
                     "text": text, "delivered": ok, "at": time.time()})
    del SENT_LOG[:-50]
    if not ok:
        _note_in_conversation(conversation_id,
                              "Approved context was not sent: the voice call had ended.")
    return ok


def _note_in_conversation(conversation_id, text):
    try:
        from agent_friday.services import conversations as cv
        cv.append(cv.resolve(conversation_id), {"role": "friday", "text": text,
                                                "pinned": False, "meta": {"kind": "context_share"}})
    except Exception:
        pass


def request(question: str, *, conversation_id, cloud_model: str,
            answer_fn: Optional[Callable] = None) -> dict:
    """Ask the local model; share its scrubbed answer on a grant or a card.

    Returns {"status": "sent"|"pending"|"unavailable"|"withheld", ...} and, when
    a card was raised, its approval_id. `answer_fn` stands in for the local
    model in tests.
    """
    question = str(question or "").strip()
    if not question:
        return {"status": "unavailable", "reason": "no question"}
    unavailable = ("Friday's local model is not running, so private context "
                   "cannot be reached.")
    if answer_fn is not None:
        raw, local_model = answer_fn(question)
    else:
        # Choose the seat and SAY SO before the slow part: summoning a cold
        # local model can take a minute, and an unexplained minute of silence
        # is how a working feature gets reported as broken.
        seat, note = pick_local_seat()
        if note:
            _deliver(conversation_id, note, "notice")
        if not seat:
            return {"status": "unavailable", "reason": note}
        raw, local_model = local_answer(question, seat=seat)
        if not local_model:
            unavailable = note or unavailable
    if not local_model:
        return {"status": "unavailable", "reason": unavailable}
    return offer(raw, conversation_id=conversation_id, cloud_model=cloud_model,
                 local_model=local_model, question=question)


def offer(raw_text: str, *, conversation_id, cloud_model: str, local_model: str,
          question: str, title: Optional[str] = None) -> dict:
    """Scrub text produced on this machine and share it on a grant or a card.

    The half of `request` after the local model has spoken, split out because
    a second caller has the text already: a WORKFLOW whose steps ran on a
    local seat. Its result is bound for the same cloud voice session and needs
    the same treatment — scrub to placeholders, apply the egress floor, sign a
    receipt, and let the owner read the exact words before any of it leaves.

    Routing a workflow result through here is not a way around the egress
    gate. The gate's verdict on private material is to withhold it whole,
    which keeps the promise and loses the answer; this keeps the promise AND
    offers the owner a scrubbed version to approve. Every protection the gate
    has is still in front of it: the never-send floor refuses outright, hard
    identifiers refuse outright, and nothing moves without the owner's yes.

    Returns the same ``{"status": ...}`` shape as `request`.
    """
    draft = prepare(raw_text)
    if _withheld_whole(draft["text"]):
        return {"status": "withheld",
                "reason": draft.get("refused")
                or "nothing in the answer could be shared"}
    draft.update({"local_model": local_model, "cloud_model": cloud_model,
                  "question": question, "conversation_id": conversation_id, "version": 1,
                  "history": []})
    grant = _use_conversation_grant(conversation_id)
    if grant:
        sid = "grant-" + uuid.uuid4().hex[:10]
        if not _send(sid, conversation_id, draft["text"], draft,
                     under_grant=grant.get("grant_id")):
            return {"status": "withheld",
                    "reason": "the share could not be recorded, so it was held"}
        return {"status": "sent", "under_grant": grant.get("grant_id"), "text": draft["text"]}
    from agent_friday.services import approvals
    rec = approvals.create_approval(
        kind=KIND, subject_type="local_context", subject_id=uuid.uuid4().hex,
        title=title or f"Share context from your local model with {cloud_model}?",
        description=draft["text"][:2000],
        action_description=(f"Send this answer from {local_model} to {cloud_model} "
                            f"for the voice conversation"),
        payload=draft, requested_by="voice", force_gate=True)
    return {"status": "pending", "approval_id": rec.get("approval_id")}


# ── Decisions ────────────────────────────────────────────────────────────────

def _on_decision(rec: dict) -> None:
    """The one executor for this kind: approve sends once, decline tells only that."""
    from agent_friday.services import approvals
    aid = rec.get("approval_id")
    p = rec.get("payload") or {}
    cid = p.get("conversation_id")
    if rec.get("status") == "approved":
        if not approvals.claim_for_execution(aid):
            return
        _send(aid, cid, str(p.get("text") or ""), p)
        approvals.mark_used(aid, "local_context", {"version": p.get("version")})
    else:
        _deliver(cid, "He chose not to share that context. Carry on without it, and do "
                      "not ask for it again unless he brings it up.", "declined")


def register() -> None:
    from agent_friday.services import approvals
    approvals.register_decision_hook(KIND, _on_decision)


def edit(approval_id: str, new_text: str, *, accept: Optional[str] = None) -> dict:
    """Save his edit as a new version of the card, re-shown on every tab.

    If the scrubber or the gate would change his text, nothing is saved until he
    chooses: accept="checked" takes the checked version, accept="mine" keeps his
    words exactly. Never-send material is refused either way.
    """
    from agent_friday.services import approvals, approval_feed
    rec = approvals.get_approval(approval_id)
    if not rec or rec.get("kind") != KIND or rec.get("status") != "pending":
        return {"ok": False, "error": "no pending share request with that id"}
    new_text = str(new_text or "")
    if not new_text.strip():
        return {"ok": False, "error": "the text is empty; decline instead to send nothing"}
    if floor_hits(new_text) and accept == "mine":
        return {"ok": False, "error": ("that text cannot be sent: it includes something on "
                                       "the never-send list or a hard identifier")}
    checked = prepare(new_text)
    if _withheld_whole(checked["text"]):
        return {"ok": False, "error": ("that text cannot be sent: it includes something on "
                                       "the never-send list or a hard identifier")}
    if checked["text"] != new_text and accept not in ("checked", "mine"):
        return {"ok": False, "needs_confirm": True, "yours": new_text,
                "checked": checked["text"], "placeholders": checked["placeholders"]}
    final = checked["text"] if accept == "checked" or checked["text"] == new_text else new_text
    p = dict(rec.get("payload") or {})
    p["history"] = (p.get("history") or []) + [{"version": p.get("version", 1),
                                                "text": p.get("text"), "at": time.time()}]
    p["text"] = final
    p["version"] = int(p.get("version") or 1) + 1
    if final == checked["text"]:
        p["placeholders"] = checked["placeholders"]
    p["gate_view"] = gate_view(final)
    p["edited_by_owner"] = True
    updated = approvals._patch(approval_id, payload=p, description=final[:2000])
    try:
        approval_feed.card_pending(updated)
    except Exception:
        pass
    return {"ok": True, "approval": updated}


def revise_by_voice(approval_id: str, instruction: str) -> dict:
    """"change X to Y" / "leave out the part about Z", applied here, locally.

    The cloud model never sees the draft before it is approved, so it cannot
    rewrite it; it passes his instruction and the edit happens on this machine.
    """
    from agent_friday.services import approvals
    rec = approvals.get_approval(approval_id)
    if not rec or rec.get("kind") != KIND:
        return {"ok": False, "error": "no share request with that id"}
    text = str((rec.get("payload") or {}).get("text") or "")
    ins = str(instruction or "").strip()
    m = re.match(r"(?i)^\s*(?:change|replace)\s+[\"']?(.+?)[\"']?\s+(?:to|with)\s+[\"']?(.+?)[\"']?\s*\.?$", ins)
    if m:
        old, new = m.group(1), m.group(2)
        if old.lower() not in text.lower():
            return {"ok": False, "error": f"the draft does not contain \"{old}\""}
        revised = re.sub(re.escape(old), new.replace("\\", "\\\\"), text, flags=re.I)
    else:
        m = re.match(r"(?i)^\s*(?:take out|leave out|leave off|remove|drop|cut|omit|skip)\s+(?:the part |the bit |anything )?(?:about|on|mentioning)?\s*[\"']?(.+?)[\"']?\s*\.?$", ins)
        if not m:
            return {"ok": False, "error": "say 'change X to Y' or 'leave out the part about Z'"}
        # He speaks in the first person about his own people — "my sister" —
        # while the draft, written by the local model out of his data, says
        # "her sister" or names her outright. So the noun is tried on its own
        # when the possessive form matches nothing.
        spoken = m.group(1).lower()
        candidates = [spoken]
        bare = re.sub(r"(?i)^(?:my|his|her|their|our|the|a|an)\s+", "", spoken).strip()
        if bare and bare != spoken:
            candidates.append(bare)
        parts = re.split(r"(?<=[.!?])\s+", text)
        for topic in candidates:
            kept = [s for s in parts if topic not in s.lower()]
            if len(kept) != len(parts):
                break
        else:
            return {"ok": False, "error": f"the draft has nothing about \"{m.group(1)}\""}
        revised = " ".join(kept)
    return edit(approval_id, revised, accept="checked")


#: Spoken words that decide a share request (checked against HIS words only).
_YES_WORDS = (r"send it|send that|go ahead|yes|yeah|yep|approve|share it|"
              r"okay send|ok send")
YES_RE = re.compile(r"(?i)\b(" + _YES_WORDS + r")\b")
NO_RE = re.compile(r"(?i)\b(don'?t send|do not send|no|nope|decline|don'?t share|do not share|cancel)\b")

#: A yes with one of these attached is not a yes to the text on the card: it
#: is consent to a card that does not exist yet, so it asks for a revision
#: instead of deciding anything. Typed chat never had this problem, because a
#: card there is decided by its own buttons and the same sentence revises the
#: draft and asks again.
COND_RE = re.compile(r"(?i)\b(but|except|apart from|other than|only|without|"
                     r"take out|leave out|leave off|leave in|drop|remove|cut|"
                     r"omit|skip|change|instead|as long as|provided)\b")

#: The hinge between his agreement and his instruction, removed when the
#: instruction is lifted out of the sentence.
_HINGE_RE = re.compile(r"(?i)^[\s,;.—-]*(?:but|except|apart from|"
                       r"other than|only|as long as|provided(?: that)?)\b")
_LEAD_YES_RE = re.compile(r"(?i)^\s*(?:friday[\s,]*)?(?:" + _YES_WORDS + r")\b")


def condition_from(owner_words: str) -> str:
    """The editing instruction inside a conditional yes.

    "yes, but take out the part about my sister" -> "take out the part about
    my sister": the agreement and the hinge come off, and what is left is an
    instruction `revise_by_voice` already knows how to apply.
    """
    s = str(owner_words or "").strip()
    s = _LEAD_YES_RE.sub("", s, count=1)
    s = _HINGE_RE.sub("", s, count=1)
    return s.strip(" ,;.—-")


def change_summary(approval_id: str) -> str:
    """How much the last revision changed, in words that are safe to say.

    Counts only. The draft is unapproved, so none of it may travel back out
    in a read-back — not the part that came out, and not the part that
    stayed.
    """
    from agent_friday.services import approvals
    rec = approvals.get_approval(approval_id) or {}
    p = rec.get("payload") or {}
    hist = p.get("history") or []
    if not hist:
        return "it changed"
    before = str(hist[-1].get("text") or "")
    after = str(p.get("text") or "")
    n_b = len([x for x in re.split(r"(?<=[.!?])\s+", before) if x.strip()])
    n_a = len([x for x in re.split(r"(?<=[.!?])\s+", after) if x.strip()])
    if n_a < n_b:
        gone = n_b - n_a
        return ("one sentence came out" if gone == 1
                else str(gone) + " sentences came out")
    if len(after) != len(before):
        return "some wording changed"
    return "it changed"


def _room_approvals_need_name() -> bool:
    """Whether a spoken approval in room mode must name Friday. Default yes.

    The owner can turn this off. It is left on by default alone among the
    voice limits because it is an identity gap, not a restriction: in chat an
    approval arrives on an authenticated session, and a room with several
    people offers no equivalent — "yes" from anyone present would count.

    A settings read that fails keeps the requirement, which is the stricter
    reading and matches `_voice_room_mode`'s own default.
    """
    try:
        from agent_friday.services.agent import _load_settings
        return (_load_settings() or {}).get(
            "voice_room_approvals_require_name", True) is not False
    except Exception:
        return True


def spoken_decision(owner_words: str, room_mode: bool) -> Optional[str]:
    """'approve', 'deny' or None from the user's own latest words.

    "no"/"don't send" wins over "yes" in the same breath. In room mode the
    words must name Friday, because another voice could otherwise decide —
    unless the owner has turned that requirement off.
    """
    words = str(owner_words or "")
    if room_mode and _room_approvals_need_name() and "friday" not in words.lower():
        return None
    yes = YES_RE.search(words)
    # Tested before the refusal, so a condition that carries its own "don't
    # send the part about X" is read as the edit he meant rather than as a
    # decline of the whole card. A bare "no, don't send it" holds no editing
    # words and still lands on deny below.
    if yes and COND_RE.search(words):
        return "revise"
    if NO_RE.search(words):
        return "deny"
    if yes:
        return "approve"
    return None


def decide_by_voice(approval_id: str, owner_words: str, room_mode: bool, claimed: str) -> dict:
    """The cloud model reports a spoken decision; it counts only if his words say so."""
    from agent_friday.services import approvals
    verdict = spoken_decision(owner_words, room_mode)
    if verdict == "revise":
        # Not a refusal and not an approval: he said yes to something that is
        # not on the card yet. Nothing is decided here.
        return {"ok": False, "revise": True,
                "instruction": condition_from(owner_words),
                "error": ("his yes had a condition attached, so it is not an "
                          "approval of the text on the card")}
    if verdict is None or verdict != claimed:
        return {"ok": False, "error": ("his own words did not " + ("approve" if claimed == "approve" else "decline")
                                       + " it" + (" (in a room of several people, a spoken OK must name Friday)"
                                                  if room_mode else ""))}
    rec, won = approvals.decide_with_outcome(approval_id, verdict, decided_by="owner:/voice/")
    return {"ok": bool(rec), "won": won, "status": (rec or {}).get("status")}
