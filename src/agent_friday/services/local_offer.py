"""A local model that could not answer becomes an OFFER, never a cloud call.

Routing modes (routing-modes addendum 2.1 rule 4). When the local model errors,
times out or cannot fit the context:

* an interactive turn gets three choices, nothing preselected: try again
  locally (the recommended one, and the first), try a shorter version, or send
  it to Claude;
* a spoken turn gets the same offer in one short line, and a bare spoken yes
  answers it only under the card rules below.

Nothing here calls a cloud model by itself. ``run_cloud_once`` is what the owner's
"send it to Claude" runs: one turn, under the ``user_explicit`` intent, named in
the receipt.

The voice rule is the one every voice card follows: a yes decides an offer only
when it belongs to THIS call, was voiced in the previous turn (and nothing was
asked since), and exactly one offer qualifies. A conditional yes ("yes, but ...")
never approves it as offered; it is ordinary conversation and the offer lapses.
"""

from __future__ import annotations

import re
import threading
import time
import uuid

from agent_friday.services.visible_jobs import visible_job

#: The choices, in the order they are shown. ``recommended`` is a label, never a
#: preselection: the page renders three buttons and none is highlighted as chosen.
CHOICES = (
    {"id": "retry_local", "label": "Try again locally", "recommended": True},
    {"id": "shorter", "label": "Try a shorter version", "recommended": False},
    {"id": "send_claude", "label": "Send to Claude", "recommended": False},
)

_OFFER_TTL_S = 15 * 60


def short_reason(cause) -> str:
    """Why the local model did not answer, in a few plain words."""
    text = str(getattr(cause, "reason", None) or cause or "").lower()
    if not text:
        return "it did not answer"
    if "timed out" in text or "timeout" in text or "time out" in text:
        return "it timed out"
    if any(k in text for k in ("context", "too long", "too large", "n_ctx",
                               "exceeds", "token limit", "400")):
        return "the conversation was too big for it"
    if any(k in text for k in ("connection", "refused", "unreachable", "503",
                               "loading model", "not serving", "no local model",
                               "no reply", "empty response")):
        return "it isn't answering right now"
    return "it hit an error"


def build(cause=None, *, model: str = "", mode: str = "", kind: str = "local_failed",
          reason: str = "") -> dict:
    """The offer card, as the chat response and the voice line both carry it."""
    why = reason or short_reason(cause)
    local_only = str(mode or "").lower() == "local_only"
    who = str(model or "").strip() or "the local model"
    choices = [dict(c) for c in CHOICES]
    if local_only:
        choices[2]["label"] = "Send this one turn to Claude"
    if kind == "local_only_confirm":
        text = ("You're in local-only mode. Send this one turn to Claude? It would see "
                "your message and this conversation, nothing else. Nothing has been sent.")
        spoken = ("You're in local-only mode. Want me to send just this one to Claude? "
                  "Say yes, or no.")
    elif kind == "no_local_model":
        text = ("No local model is answering right now, so I didn't send this to a cloud "
                "model. You choose what happens next.")
        spoken = ("No local model is answering. Say try again, or ask Claude.")
    else:
        text = ("I couldn't get an answer out of %s (%s). Nothing was sent to a cloud "
                "model. You choose what happens next." % (who, why))
        spoken = ("My local brain couldn't finish that, because %s. Want me to ask Claude? "
                  "Or say try again." % why)
    offer = {
        "offer": True, "id": uuid.uuid4().hex[:12], "kind": kind,
        "reason": why, "model": str(model or ""), "mode": str(mode or ""),
        "text": text, "spoken": spoken, "choices": choices,
        # The cloud choice names the mode when it is local-only: the page must send
        # `confirm_local_only` with it, because the click IS the confirm.
        "confirm_local_only": local_only,
        "served": {"model": str(model or ""), "local": True, "answered": False},
    }
    if kind == "local_only_confirm":
        offer["choices"] = [
            {"id": "send_claude", "label": "Send this one turn to Claude", "recommended": False},
            {"id": "retry_local", "label": "Keep it local", "recommended": True},
        ]
    return offer


# ── the voice side ───────────────────────────────────────────────────────────

_PENDING: dict = {}
_LOCK = threading.Lock()

_YES = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay|please|do it|go ahead|go for it|send it|"
    r"yes please|please do|ask claude|ask claude please|send it to claude|"
    r"yes ask claude|yes send it)[\s.!,]*$", re.I)
_RETRY = re.compile(
    r"^(try again|try it again|try that again|retry|again|run it again|"
    r"try again locally|try it locally again|have another go)[\s.!,]*$", re.I)
_NO = re.compile(
    r"^(no|nope|nah|not now|never mind|nevermind|leave it|forget it|cancel|"
    r"no thanks|no thank you|don'?t)[\s.!,]*$", re.I)


def classify_reply(text) -> str | None:
    """What a spoken reply decides: ``send_claude``, ``retry_local``, ``decline``,
    or None (ordinary conversation, including any conditional yes)."""
    t = str(text or "").strip()
    if not t or len(t) > 60:
        return None
    if _YES.match(t):
        return "send_claude"
    if _RETRY.match(t):
        return "retry_local"
    if _NO.match(t):
        return "decline"
    return None


def remember(conversation_id, offer: dict, message: str) -> None:
    """Hold the offer for the call that was just told about it."""
    cid = str(conversation_id or "").strip()
    if not cid:
        return
    with _LOCK:
        for k in [k for k, v in _PENDING.items()
                  if time.time() - v["at"] > _OFFER_TTL_S]:
            _PENDING.pop(k, None)
        _PENDING[cid] = {"offer": offer, "message": str(message or "")[:4000],
                         "at": time.time()}


def forget(conversation_id) -> None:
    with _LOCK:
        _PENDING.pop(str(conversation_id or "").strip(), None)


def pending(conversation_id, spoken_ids) -> dict | None:
    """The offer a bare spoken yes may answer, or None.

    Same rule as ``routes.voice._pending_voice_card``: it belongs to this call's
    conversation, it was voiced in the previous turn (``spoken_ids`` is what that
    turn voiced, empty after any other question), and exactly one qualifies."""
    cid = str(conversation_id or "").strip()
    ids = {str(i) for i in (spoken_ids or [])}
    if not cid or not ids:
        return None
    with _LOCK:
        rec = _PENDING.get(cid)
        if not rec or time.time() - rec["at"] > _OFFER_TTL_S:
            return None
        return rec if rec["offer"]["id"] in ids else None


def take_reply(conversation_id, user_text, spoken_ids):
    """Decide the pending offer from a spoken reply.

    Returns (choice, message). ``choice`` is None when the words do not answer the
    offer; the offer is dropped either way, because it was voiced once and one
    turn has now passed (No Pestering: it is not asked again by itself)."""
    rec = pending(conversation_id, spoken_ids)
    if rec is None:
        return None, ""
    forget(conversation_id)
    return classify_reply(user_text), rec["message"]


# ── the one cloud turn the owner asked for ───────────────────────────────────

@visible_job("One turn on Claude", kind="cloud_once", workspace="chat")
def run_cloud_once(messages, *, system=None, source: str = "offer", session_ctx=None,
                   workspace=None, **kwargs):
    """One agent turn on the cloud model, under the ``user_explicit`` intent.

    The router sees a cloud-bound conversation and the transports see the intent;
    both go away when the turn ends. ``confirmed`` is set because the owner's
    choice (the button, or the spoken yes) is the confirm local-only asks for.
    """
    from agent_friday.core import ANTHROPIC_MODEL_DEFAULT, _load_settings
    from agent_friday.services import cloud_intent as _ci
    from agent_friday.services.agent import _generate_agent
    st = _load_settings() or {}
    cloud_model = ((st.get("model_routing") or {}).get("default_cloud_model")
                   or ANTHROPIC_MODEL_DEFAULT)
    with _ci.intent_scope("user_explicit", source=source, confirmed=True):
        return _generate_agent(messages, system=system, session_ctx=session_ctx,
                               workspace=workspace,
                               conversation_seat={"model": cloud_model}, **kwargs)
