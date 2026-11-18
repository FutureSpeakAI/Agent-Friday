"""Switch a conversation's model, and read what a conversation is bound to and what last answered it (the
phone parity item "PC model and voice switching from the phone").

* ``POST /tv/v1/model/switch {model, conversation_id?}`` (owner device only) binds ONE conversation to a PC
  model the same validated way a ``model`` in a Messages body does (models.resolve, then owner_turn.bind_seat):
  a model this PC cannot run, or that Local only forbids, is a coded refusal in words and nothing changes.
  Without a ``conversation_id`` it starts the chat and returns its id. The answer says what the switch costs
  (services/model_slate: a cold local seat's expected load, measured first-token wait, this PC's benchmark),
  and ``applies`` says "this_conversation", never more.
* ``GET /tv/v1/conversations/<cid>/state`` (owner device only): the model and voice that conversation is bound
  to, and ``last_served``: what actually answered its latest turn (the desktop's own record, not the request),
  held in this process, so it is empty after a restart until the next turn.

Voice is switched by POST /tv/v1/voice/switch (tv_gateway/voices); this module only reads it. A device that
asks for model A and is answered by model B sees both in ``last_served`` (``substituted``).
Held by tests/unit/test_phone_switching_contract.py.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Optional

_log = logging.getLogger("friday.tv.conversation_state")
_MAX = 256
_LOCK = threading.Lock()
_SERVED: "OrderedDict[str, dict]" = OrderedDict()


def note_served(truth: dict) -> None:
    """Remember what answered a conversation's latest turn (``truth`` is owner_turn.served()'s block)."""
    cid = str((truth or {}).get("conversation_id") or "")
    if not cid or (truth or {}).get("unknown"):
        return
    rec = {k: truth.get(k) for k in ("model", "provider", "seat", "local", "requested", "substituted",
                                     "fallback", "notice")}
    rec["at"] = time.time()
    with _LOCK:
        _SERVED[cid] = rec
        _SERVED.move_to_end(cid)
        while len(_SERVED) > _MAX:
            _SERVED.popitem(last=False)


def last_served(cid: Optional[str]) -> Optional[dict]:
    with _LOCK:
        got = _SERVED.get(str(cid or ""))
        return dict(got) if got else None


def _reset_for_tests() -> None:
    with _LOCK:
        _SERVED.clear()


def model_facts(model_id: Optional[str]) -> Optional[dict]:
    """The model slate's own row for one model (cost to switch, measured wait, benchmark, capabilities)."""
    if not model_id:
        return None
    try:
        from agent_friday.services import model_slate
        row = model_slate.find(model_id)
    except Exception as e:
        _log.info("model facts unavailable: %s", type(e).__name__)
        return None
    if not row:
        return None
    return {k: row.get(k) for k in ("id", "label", "provider", "local", "cloud", "warm", "can_serve", "blocked",
                                    "switch", "measured", "benchmark", "capabilities") if k in row}


def register(app, refuse) -> None:
    """Add the two routes. ``refuse(status, reason, **extra)`` is the gateway's refusal."""
    from flask import g, jsonify, request

    from agent_friday.tv_gateway import models, owner_turn, turn, voices

    def owner_only():
        if not owner_turn.is_owner_device(g.device):
            return refuse(403, "models and voices are chosen on the owner's phone or tablet", code="owner_only")
        return None

    @app.post("/tv/v1/model/switch")
    def model_switch():
        denied = owner_only()
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        asked = str(body.get("model") or "").strip()
        cid = str(body.get("conversation_id") or "").strip() or None
        if not asked:
            return refuse(400, "say which model", code="bad_request")
        try:
            row = models.resolve(asked, owner_turn._slate(g.device))
            cid = owner_turn.bind_seat(cid, row["id"])
        except models.ModelRefused as e:
            return refuse(e.status, e.reason, code=e.code, **e.extra)
        except turn.TurnRefused as e:
            return refuse(e.status, str(e), code=e.code or "refused", **e.extra)
        except Exception as e:
            _log.warning("model switch failed: %s", type(e).__name__)
            return refuse(503, "this computer could not check that model right now; try again",
                          code="slate_unavailable")
        turn.device_pairing._receipt("device_model_switch", g.device, watching="owner", model=row["id"])
        return jsonify({"ok": True, "conversation_id": cid, "model": row["id"], "asked": asked,
                        "applies": "this_conversation", "takes_effect": "next_turn",
                        "facts": model_facts(row["id"]),
                        "served": "known after the next turn: GET /tv/v1/conversations/<id>/state or the "
                                  "friday block on the reply"})

    @app.get("/tv/v1/conversations/<cid>/state")
    def conversation_state(cid):
        denied = owner_only()
        if denied:
            return denied
        if not owner_turn.valid_cid(cid):
            return refuse(404, "this computer has no such chat", code="unknown_conversation")
        try:
            conv = owner_turn._json(owner_turn._api("GET", "/api/conversations/" + cid)).get("conversation")
        except Exception as e:
            _log.warning("conversation lookup failed: %s", type(e).__name__)
            return refuse(503, "this computer could not look up that chat right now", code="chat_unavailable")
        if not conv:
            return refuse(404, "this computer has no such chat", code="unknown_conversation")
        bound = (conv.get("seat") or {}).get("model")
        return jsonify({"ok": True, "conversation_id": cid,
                        "model": {"bound": bound, "facts": model_facts(bound)},
                        "voice": voices.bound_voice(cid), "last_served": last_served(cid)})
