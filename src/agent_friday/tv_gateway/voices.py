"""The PC's voice slate as one gateway contract for the phone, the tablet and the TV, and the switch of voice
engine inside one conversation (Addendum H, 2026-11-15; the companion of tv_gateway/models).

* ``GET /tv/v1/voices`` lists every voice engine and voice the PC offers, from services/voice_slate (the same
  source the desktop reads): per engine its provider, local or cloud, whether its key is present, whether it can
  run now (and the reason in words if not), a latency class (a labelled guide), exactly what leaves the house,
  and its voices. An owner-bound personal device may select; a TV or family device sees the list and is told
  voice is chosen on the owner's phone or tablet. ``?conversation_id=`` adds what that conversation is bound to.
* ``POST /tv/v1/voice/switch {engine, voice?, conversation_id}`` (owner device only): the switch check
  (voice_slate.plan_switch) then, if allowed, a binding for THAT conversation id (the conversation, its words and
  its context stay; only the voice changes). A cloud speech engine is never switched here: the answer is 409
  ``needs_choice`` with what would leave, and the PC raises its own question (just this call, or always) for the
  owner to answer on the PC. Local only, an engine that is off, an unknown engine or voice: a coded refusal in
  words. Nothing is switched quietly and nothing answers with another voice.

The binding is held in this process only (it is a per-conversation choice, not a setting) and is reported with
``applies`` so a device never believes it changed more than it did.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Optional

_log = logging.getLogger("friday.tv.voices")
_MAX_BOUND = 256
_LOCK = threading.Lock()
_BOUND: "OrderedDict[str, dict]" = OrderedDict()
_LOADED = False


def _path():
    from agent_friday.paths import friday_home
    return friday_home() / "tv" / "voice_bindings.json"


def _load_once() -> None:
    """Read the saved bindings the first time they are needed, so a deploy or restart does not unbind a chat."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        for rec in json.loads(_path().read_text(encoding="utf-8")):
            if isinstance(rec, dict) and rec.get("conversation_id") and rec.get("engine"):
                _BOUND[str(rec["conversation_id"])] = {"conversation_id": str(rec["conversation_id"]),
                                                       "engine": str(rec["engine"]),
                                                       "voice": rec.get("voice") or None}
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        _log.warning("saved voice bindings unreadable, starting empty: %s", type(e).__name__)


def _save() -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(_BOUND.values())), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        _log.warning("voice bindings not saved: %s", type(e).__name__)


def _raw_bound(conversation_id: Optional[str]) -> Optional[dict]:
    with _LOCK:
        _load_once()
        got = _BOUND.get(str(conversation_id or ""))
        return dict(got) if got else None


def bound_voice(conversation_id: Optional[str]) -> Optional[dict]:
    """What this conversation is bound to, as true NOW, or None when nothing is bound.

    The binding (``engine``, ``voice``) is checked again against the live slate every time it is read, because
    the PC can change after the pick (Local only turned on, a key removed, an engine gone). The result carries
    ``still_valid``, what leaves the house, the latency class and, when it is no longer valid, ``notice`` in
    words: a device is told the voice it asked for cannot be used, never handed another one quietly."""
    rec = _raw_bound(conversation_id)
    if rec is None:
        return None
    out = dict(rec, still_valid=None, notice=None)
    try:
        from agent_friday.services import voice_slate
        row = voice_slate.find(rec["engine"])
        if row is None:
            out.update(still_valid=False, notice="That voice engine is no longer on this PC.")
        else:
            out.update(local=row["local"], latency_class=row["latency_class"], what_leaves=row["what_leaves"],
                       still_valid=not row["blocked"],
                       notice=(row["blocked"]["reason"] if row["blocked"] else None))
    except Exception as e:
        _log.info("bound voice could not be re-checked: %s", type(e).__name__)
    return out


def _bind(conversation_id: str, engine: str, voice: Optional[str]) -> dict:
    rec = {"conversation_id": conversation_id, "engine": engine, "voice": voice}
    with _LOCK:
        _load_once()
        _BOUND[conversation_id] = rec
        _BOUND.move_to_end(conversation_id)
        while len(_BOUND) > _MAX_BOUND:
            _BOUND.popitem(last=False)
        _save()
    return dict(rec)


def _reset_for_tests() -> None:
    global _LOADED
    with _LOCK:
        _BOUND.clear()
        _LOADED = False


def _forget_memory_only() -> None:
    """What a restart does to this module's memory (the saved file stays)."""
    global _LOADED
    with _LOCK:
        _BOUND.clear()
        _LOADED = False


def _ask_owner(engine: str) -> bool:
    """Raise the PC's own cloud-voice question for the owner. Never switches; True when a question is now waiting."""
    try:
        from agent_friday.services import local_voice_readiness as lvr
        out = lvr.ask_cloud_switch(source="voice", engine=engine) or {}
        return out.get("asked", True) is not False and not out.get("refused")
    except Exception as e:
        _log.info("cloud voice question not raised: %s", type(e).__name__)
        return False


def register(app, refuse) -> None:
    """Add the two routes to the gateway app. ``refuse(status, reason, **extra)`` is the gateway's refusal."""
    from flask import g, jsonify, request

    from agent_friday.services import voice_slate
    from agent_friday.tv_gateway import owner_turn

    @app.get("/tv/v1/voices")
    def voices_slate():
        try:
            s = voice_slate.slate()
        except Exception as e:
            _log.warning("voice slate failed: %s", type(e).__name__)
            return refuse(503, "this computer could not list its voices right now", code="slate_unavailable")
        owner = owner_turn.is_owner_device(g.device)
        out = {"ok": True, "scope": "owner" if owner else "household", "selected": s["selected"],
               "routing_mode": s["routing_mode"], "voice_in_use": s["voice_in_use"],
               "can_select": bool(owner), "engines": s["engines"]}
        if not owner:
            out["note"] = "voice is chosen on the owner's phone or tablet"
        cid = (request.args.get("conversation_id") or "").strip()
        if cid:
            out["conversation"] = bound_voice(cid) or {"conversation_id": cid, "engine": None, "voice": None}
        return jsonify(out)

    @app.post("/tv/v1/voice/switch")
    def voice_switch():
        body = request.get_json(silent=True) or {}
        if not owner_turn.is_owner_device(g.device):
            return refuse(403, "voice is chosen on the owner's phone or tablet", code="owner_only")
        engine = str(body.get("engine") or "").strip()
        voice = str(body.get("voice") or "").strip() or None
        cid = str(body.get("conversation_id") or "").strip()
        if not engine or not cid:
            return refuse(400, "say which voice engine and which conversation", code="bad_request")
        try:
            plan = voice_slate.plan_switch(engine, voice, cid)
        except voice_slate.VoiceNotServable as e:
            status = 400 if e.code in (voice_slate.UNKNOWN_ENGINE, voice_slate.UNKNOWN_VOICE) else 409
            return refuse(status, e.reason, code=e.code, engine=e.engine)
        if plan["outcome"] == voice_slate.NEEDS_CHOICE:
            plan["asked_on_pc"] = _ask_owner(plan["engine"])
            plan["applies"] = "nothing_changed"
            plan.update(ok=False, code=voice_slate.NEEDS_CHOICE)
            resp = jsonify(plan)
            resp.status_code = 409
            return resp
        rec = _bind(cid, plan["engine"], plan["voice"])
        plan.update(ok=True, applies="this_conversation", bound=rec)
        return jsonify(plan)
