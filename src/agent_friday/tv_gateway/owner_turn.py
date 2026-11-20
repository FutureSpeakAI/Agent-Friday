"""An owner's own phone or tablet gets the owner's Friday, not the household TV's.

The PC bound this device to the owner (household.bind_device, by the verified press at the PC), and
its kind is personal (tablet, phone, watch). A turn from such a device is the owner's turn: the same
turn the desktop's chat runs (routes/chat.chat_stream), with the knowledge graph, wiki, vault, people
and core memory, the owner's seat and picker choice, the same approvals (a side effect still waits for
a press at the PC) and the same ledger. It is answered in the Messages stream format, so the phone's
reader is the one it already has.

A TV (bound to the family member) never comes here: turn.run keeps it on the household prompt with no
owner context. The only way in is the binding the owner made at the PC, never a header, a field in the
body, or a kind the device declares at turn time.

The model the owner picked on the phone is the model that runs, and the reply says which one did
(2026-11-13, "is Bonsai2 really answering?"). The Messages body's ``model`` is bound to the chat the
way the desktop's picker binds one (PATCH /api/conversations, the same validated route: a seat this PC
cannot take is refused with its reason once; the next message with the same pick is answered on the
chat's own seat, marked substituted, with that reason, so a refusal is never a loop). The reply's closing
``message_delta`` carries a ``friday`` block with what the backend recorded as generating (the
desktop's ``served_by.answered_by``, not the requested id), the seat, and any fallback or notice;
a requested id that is not the served id is marked ``substituted``. Nothing is changed when the
phone sends no model, or one this PC does not list: the chat runs on its own seat and the block still
says what served and what was asked.

How: the turn runs on this PC's own app, in process, with this PC's own session token (a gateway
device never holds or sees one). Nothing about the owner's chat is reimplemented here.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from collections import OrderedDict
from typing import Iterator, NamedTuple, Optional

from agent_friday.tv_gateway import voice_reply

_log = logging.getLogger(__name__)


def is_owner_device(dev: Optional[dict]) -> bool:
    """True only for a device the PC bound to the owner, of a personal kind."""
    from agent_friday.tv_gateway import household
    if not dev or dev.get("kind") not in household.PERSONAL_KINDS:
        return False
    return household.member_for_device(str(dev.get("id"))) == household.OWNER_ID


def last_user_text(messages) -> str:
    """The words of the newest user message in a Messages-API conversation."""
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            text = "".join(str(b.get("text", "")) for b in c
                           if isinstance(b, dict) and b.get("type") == "text")
            if text.strip():
                return text.strip()
    return ""


def _event(obj: dict) -> str:
    from agent_friday.tv_gateway import turn
    return turn._event(obj)


def _frames(response) -> Iterator[dict]:
    """The `data:` frames of an SSE response, as dicts."""
    buf = ""
    for chunk in response.response:
        buf += chunk.decode("utf-8", "replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        while "\n\n" in buf:
            part, buf = buf.split("\n\n", 1)
            for line in part.split("\n"):
                if line.startswith("data: "):
                    try:
                        yield json.loads(line[6:])
                    except ValueError:
                        continue


#: How often the phone hears that the turn is alive while the model is thinking (an SSE `ping`).
STATUS_EVERY_S = 4.0

#: The longest a remote turn is followed. Past it the phone is told plainly (an `error` event) instead of hearing pings
#: for ever; the turn itself keeps running on the PC. A long "start my day" runs for minutes, so this is generous.
MAX_TURN_S = 30 * 60.0


def _call_desktop(text: str, conversation_id: Optional[str]):
    """The owner's chat turn on this PC's own app, in process, with this PC's own session token."""
    from agent_friday import core
    body = {"message": text}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return core.app.test_client().post(
        "/api/chat/stream", json=body, buffered=False,
        headers={"X-Friday-Token": core._current_api_token()})


_CTX = threading.local()


def _activity(act):
    """The desktop shows a remote turn as ONE activity named for the device and carrying the model
    ("Tablet chat · claude-opus-5-5"). The chat route's own jobs run inside it as steps."""
    import contextlib
    if not act:
        return contextlib.nullcontext()
    from agent_friday.services.visible_jobs import visible_job
    label, model = act
    return visible_job(label, kind="remote_turn", label=label, model=model or None)


class _DesktopTurn:
    """The owner's chat turn, run on THIS PC's own app on its own thread, its frames handed over a queue.

    Why a thread: the desktop's stream is a Flask generator bound to its own request context. Read
    lazily from inside the gateway's request (as this used to be), the two contexts interleave and
    Flask raises "Popped wrong app context" at the first read: the desktop finished the turn and
    the phone got an HTTP 500 and no answer (2026-11-13 19:51, 19:53, 20:08). On its own thread the
    turn's whole context lives and dies there, and the gateway only reads dicts.
    """

    def __init__(self, text: str, conversation_id: Optional[str]):
        self.status_code = None
        self.q: "queue.Queue" = queue.Queue()
        ready = threading.Event()
        act = getattr(_CTX, "activity", None)           # (label, model) set by run(): ONE activity per remote turn

        def work():
            try:
                with _activity(act):
                    r = _call_desktop(text, conversation_id)
                    self.status_code = r.status_code
                    ready.set()
                    if r.status_code == 200:
                        for f in _frames(r):
                            self.q.put(f)
            except Exception as e:  # noqa: BLE001
                _log.info("owner turn thread failed: %s", type(e).__name__)
                if self.status_code is None:
                    self.status_code = 599
                ready.set()
                self.q.put({"error": "this computer's chat turn failed (%s)" % type(e).__name__})
            finally:
                ready.set()
                self.q.put(None)

        threading.Thread(target=work, name="owner-turn", daemon=True).start()
        ready.wait(60.0)
        if self.status_code is None:
            self.status_code = 504

    def frames(self, idle_s: Optional[float] = None) -> Iterator[Optional[dict]]:
        """Each desktop frame as a dict; None whenever ``idle_s`` passes with nothing (a tick for the phone)."""
        while True:
            try:
                f = self.q.get(timeout=idle_s or STATUS_EVERY_S)
            except queue.Empty:
                yield None
                continue
            if f is None:
                return
            yield f


def _post(text: str, conversation_id: Optional[str]):
    """Start the owner's chat turn on this PC's own app, in process, and return it (status_code + frames)."""
    return _DesktopTurn(text, conversation_id)


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def answer_only(text: str) -> str:
    """The reply without a reasoning scratchpad: a phone gets the answer, never the thinking."""
    t = _THINK.sub("", text or "")
    if "</think>" in t:
        t = t.split("</think>", 1)[1]
    elif "<think>" in t:
        t = t.split("<think>", 1)[0]
    return t.strip()


def _tool_label(val) -> str:
    """A few words for what the desktop is doing ("Searching the wiki"), never an argument or a result."""
    if isinstance(val, dict):
        val = val.get("label") or val.get("name") or val.get("tool") or ""
    return str(val or "")[:60]


def _frames_of(response, idle_s: Optional[float] = None) -> Iterator[Optional[dict]]:
    return response.frames(idle_s) if hasattr(response, "frames") else _frames(response)


def _api(method: str, path: str, body: Optional[dict] = None):
    """A call to this PC's own app, in process, with this PC's session token (as the pill's save does)."""
    from agent_friday import core
    client = core.app.test_client()
    return getattr(client, method.lower())(path, json=body, headers={"X-Friday-Token": core._current_api_token()})


def requested_model(body: dict) -> str:
    m = (body or {}).get("model")
    return m.strip() if isinstance(m, str) else ""


def _slate(dev: Optional[dict]) -> list:
    """The PC's slate for this device (tv_gateway/models), from the same warm catalog the desktop picker reads."""
    from agent_friday.tv_gateway import models
    return models.rows(dev)


def _json(resp) -> dict:
    try:
        return resp.get_json(silent=True) or {}
    except Exception:
        return {}


def _answer(resp) -> "tuple[int, dict]":
    """(HTTP status, JSON body) of this PC's own answer. The status is read, never inferred from the body:
    a 404 for a chat this PC no longer has is a fact about the chat, not a refusal of the model
    (tests/unit/test_owner_turn_cloud_pick_over_phone.py)."""
    try:
        status = int(getattr(resp, "status_code", 0) or 0)
    except Exception:
        status = 0
    return status, _json(resp)


class Bound(NamedTuple):
    """A pick bound to a chat: the chat it runs in, and what the reply says about the bind (a lost chat,
    a visible fallback's reason, a cloud disclosure)."""
    cid: str
    notes: tuple = ()


#: One refusal per (device, chat, model): the same refusal is never given twice. The second message with the
#: same pick is answered on the chat's own seat with the reason as its notice (Stephen, 2026-11-20: "never
#: repeat the same refusal; if a pick truly can't run, fall back visibly or ask once"). A bind that later
#: succeeds forgets it. Bounded, no expiry (the owner's words were "never"): a restart forgets everything,
#: which costs at most one more refusal.
_REFUSED: "OrderedDict[tuple, str]" = OrderedDict()
#: (device, the chat id it keeps sending) -> the fresh chat it was moved to when that chat was gone: one
#: fresh chat per lost chat, not one per message, until the device adopts the new id.
_RELOCATED: "OrderedDict[tuple, str]" = OrderedDict()
_REFUSED_MAX = 256
_REFUSED_LOCK = threading.Lock()


def _refused_before(key: tuple, reason: str) -> bool:
    with _REFUSED_LOCK:
        if _REFUSED.get(key) == reason:
            return True
        _REFUSED[key] = reason
        _REFUSED.move_to_end(key)
        while len(_REFUSED) > _REFUSED_MAX:
            _REFUSED.popitem(last=False)
        return False


def _forget_refusal(key: tuple) -> None:
    with _REFUSED_LOCK:
        _REFUSED.pop(key, None)


def _reset_for_tests() -> None:
    with _REFUSED_LOCK:
        _REFUSED.clear()
        _RELOCATED.clear()


LOST_CHAT_NOTE = "The chat this device was in is no longer on your PC, so a new chat was started for your pick."
FELL_BACK_NOTE = ("This message was answered by this chat's own model instead; pick another model on the chip to "
                  "change it.")


def disclosure(row: Optional[dict]) -> str:
    """What leaves the PC when a cloud model is bound from a device, said once at the bind: the provider, the
    model, and that the context the PC adds goes with it. Nothing for a local model."""
    if not row or row.get("kind") != "cloud":
        return ""
    name = str(row.get("name") or row.get("id") or "this model")
    where = str(row.get("provider_name") or row.get("provider") or "its provider")
    return ("Your pick leaves your PC: this chat now goes to %s (%s) with the context your PC adds "
            "(knowledge graph, wiki, memory), redacted by the egress gate. Pick a local model on the chip to "
            "keep it on your PC." % (where, name))


def _bind_failed(status: int, model_id: str):
    from agent_friday.tv_gateway import turn
    return turn.TurnRefused(503, "this computer could not bind that model right now (its chat store answered %d); "
                                 "try again" % status, "seat_bind_failed", model=model_id)


SEAT_WAIT_S = 8.0       # a bind waits this long for the PC's local-model check: under a phone's 10 s read timeout
SEAT_POLL_S = 0.5


def _patch_seat_waiting(cid: str, seat: dict) -> "tuple[int, dict]":
    """PATCH the chat's seat. While the PC answers that it is still checking (``seat_pending``) the same selection is
    sent again with the ``seat_change_id`` it handed back, as the desktop's own picker does, until it settles or
    SEAT_WAIT_S passes. A check that is merely slow must not become a refusal on the phone."""
    deadline = time.monotonic() + SEAT_WAIT_S
    body = {"seat": seat}
    while True:
        status, answer = _answer(_api("PATCH", "/api/conversations/" + cid, body))
        if status != 200 or not answer.get("seat_pending") or time.monotonic() >= deadline:
            return status, answer
        if answer.get("seat_change_id"):
            body = {"seat": seat, "seat_change_id": answer["seat_change_id"]}
        time.sleep(SEAT_POLL_S)


_CID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def clean_cid(value) -> Optional[str]:
    """A conversation id a device sent, or None: a strict pattern before it touches a path or a store key
    (AGENTS.md, device auth). An id this PC would never mint is treated as no chat."""
    v = value.strip() if isinstance(value, str) else ""
    return v if v and _CID.match(v) else None


def _relocation(dev_id: str, cid: str) -> Optional[str]:
    with _REFUSED_LOCK:
        return _RELOCATED.get((dev_id, cid))


def _relocate(dev_id: str, cid: str, new_cid: str) -> None:
    if not dev_id or not cid:
        return
    with _REFUSED_LOCK:
        _RELOCATED[(dev_id, cid)] = new_cid
        _RELOCATED.move_to_end((dev_id, cid))
        while len(_RELOCATED) > _REFUSED_MAX:
            _RELOCATED.popitem(last=False)


def bind_pick(cid: Optional[str], model_id: str, row: Optional[dict] = None, dev_id: str = "") -> Bound:
    """Make the chat run on ``model_id`` for a device's pick: the chat it now runs in, with what the reply
    should say.

    Read by HTTP status, never by the shape of the body. A chat this PC no longer has (404) starts a fresh
    chat on the pick and says so once; a device that keeps sending the lost id is answered in that same
    fresh chat (relocated, no second chat, no second notice). A chat already on the pick is left alone;
    the PC's own failure to bind is a 503 "try again"; a seat the PC refuses with a reason is a 409 with
    that reason ONCE per (device, chat, model), and the next message with the same pick is answered on the
    chat's own seat, marked substituted, with the reason as its notice. The memo is for devices: with no
    ``dev_id`` every refusal raises (bind_seat). Never a quiet other model, never the same refusal twice
    (tests/unit/test_owner_turn_cloud_pick_over_phone.py)."""
    from agent_friday.tv_gateway import turn
    seat = {"model": model_id}
    asked_cid = cid
    key = (dev_id, cid or "", model_id)
    notes: list = []
    for attempt in (1, 2):
        if not cid:
            break
        status, d = _answer(_api("GET", "/api/conversations/" + cid))
        if status == 200:
            conv = d.get("conversation") or {}
            if (conv.get("seat") or {}).get("model") == model_id:
                _forget_refusal(key)
                return Bound(cid)           # already bound: nothing to change, nothing to reload
            break
        if status != 404:
            raise _bind_failed(status, model_id)
        moved = _relocation(dev_id, cid) if attempt == 1 else None
        if moved and moved != cid:
            cid = moved                     # the fresh chat this device was moved to last time
            continue
        notes.append(LOST_CHAT_NOTE)
        cid = None
    if not cid:
        status, d = _answer(_api("POST", "/api/conversations", {"title": "Phone chat", "seat": seat}))
        conv = d.get("conversation") or {}
        cid = conv.get("id")
        if status not in (200, 201) or not cid:
            raise turn.TurnRefused(503, "this computer could not start that chat", "chat_unavailable")
        if asked_cid:
            _relocate(dev_id, asked_cid, cid)
    else:
        status, d = _patch_seat_waiting(cid, seat)
        if status == 404:                   # gone between the read and the write: the same fresh start
            inner = bind_pick(None, model_id, row, dev_id)
            if asked_cid:
                _relocate(dev_id, asked_cid, inner.cid)
            return Bound(inner.cid, (LOST_CHAT_NOTE,) + tuple(inner.notes))
        if status != 200:
            raise _bind_failed(status, model_id)
        conv = d.get("conversation") or {}
    if d.get("seat_pending") and (conv.get("seat") or {}).get("model") != model_id:
        raise turn.TurnRefused(503, str(d.get("note") or "this computer is still checking its local models")[:240],
                               "seat_pending", model=model_id)
    if d.get("superseded") and (conv.get("seat") or {}).get("model") != model_id:
        raise turn.TurnRefused(409, "the model for this chat was changed on this computer a moment ago; pick again",
                               "seat_superseded", model=model_id)
    if (conv.get("seat") or {}).get("model") != model_id:
        reason = str(d.get("note") or "this computer would not use that model for this chat")[:240]
        if dev_id and _refused_before(key, reason):
            return Bound(cid, tuple(notes) + (reason.rstrip(".") + ". " + FELL_BACK_NOTE,))
        raise turn.TurnRefused(409, reason, "seat_refused", model=model_id)
    _forget_refusal(key)
    said = disclosure(row)
    if said:
        notes.append(said)
    return Bound(cid, tuple(notes))


def bind_seat(cid: Optional[str], model_id: str) -> str:
    """Make the chat run on ``model_id`` and return its conversation id, or raise turn.TurnRefused with
    the PC's reason. The strict form (no memo, no fallback): the voice default and tests use it."""
    return bind_pick(cid, model_id).cid


def served(payload: dict, requested: str, cid: Optional[str]) -> dict:
    """What the backend recorded as generating this reply, from the desktop's own turn record
    (``served_by.answered_by``), never the requested id. Fallbacks and notices ride along."""
    payload = payload if isinstance(payload, dict) else {}
    sb = payload.get("served_by") if isinstance(payload.get("served_by"), dict) else {}
    ans = sb.get("answered_by") if isinstance(sb.get("answered_by"), dict) else {}
    model = str(ans.get("model") or payload.get("model") or sb.get("model") or "")
    seat = str(ans.get("seat") or payload.get("seat") or "")
    chain = payload.get("fallback_chain")
    return {"model": model, "provider": str(ans.get("provider") or sb.get("provider") or ""),
            "seat": seat, "local": (seat == "local") if seat else None,
            "requested": requested or None, "substituted": bool(requested and model and requested != model),
            "unknown": not model,
            "fallback": chain if isinstance(chain, list) else [],
            "notice": payload.get("seat_notice") or None, "conversation_id": cid}


def is_voice_turn(dev: Optional[dict], body: dict) -> bool:
    """A turn that will be SPOKEN: the device says so (``voice: true``), or it sent audio for dictation
    in the last two minutes (so a device that has not been updated still gets a speech-safe reply)."""
    from agent_friday.tv_gateway import speech
    if (body or {}).get("voice") is True or str((body or {}).get("modality") or "").lower() == "voice":
        return True
    return speech.recently_heard(str((dev or {}).get("id") or ""))


def fast_voice_default(cid: Optional[str], dev: Optional[dict] = None):
    """(conversation id, what was chosen) for a voice turn whose device picked no model.

    Only when the chat has no seat of its own yet: a pick made at the PC or on the device is never
    overridden. The choice is the fastest listed cloud model (voice_reply.FAST_VOICE_PREFERENCE), bound
    the same validated way a picker binds one, and reported to the device as a recommendation it can
    change. A seat the PC will not bind, or no such model, leaves the chat on its own seat, quietly."""
    from agent_friday.tv_gateway import turn, voice_reply
    try:
        if cid:
            conv = _json(_api("GET", "/api/conversations/" + cid)).get("conversation") or {}
            if (conv.get("seat") or {}).get("model"):
                return cid, None
        row = voice_reply.fast_voice_model(_slate(dev))
        if not row:
            return cid, None
        cid = bind_seat(cid, row["id"])
    except turn.TurnRefused:
        return cid, None
    except Exception:
        return cid, None
    return cid, {"model": row["id"], "label": row.get("name") or row["id"], "recommended": True,
                 "why": "faster for voice; pick another model on this device to use it instead"}


def bound_voice(cid: Optional[str]) -> Optional[dict]:
    """The voice a phone or tablet bound to this conversation, or None. A no-op until tv_gateway.voices exists
    (CORE-MODELS fills it); a failure here never touches the turn."""
    try:
        from agent_friday.tv_gateway import voices
        return voices.bound_voice(cid) if cid else None
    except Exception:
        return None


def run(dev: dict, body: dict) -> Iterator[str]:
    """Validate, then stream the owner's turn. Raises turn.TurnRefused before the first event."""
    from agent_friday.services import global_stop
    from agent_friday.tv_gateway import turn
    try:
        global_stop.raise_if_stopped("an owner's phone turn")
    except Exception:
        raise turn.TurnRefused(503, "everything is stopped on this computer")
    text = last_user_text(body.get("messages"))
    if not text:
        raise turn.TurnRefused(400, "no message")
    cid = clean_cid(body.get("conversation_id"))
    requested = requested_model(body)
    asked = requested
    voice = is_voice_turn(dev, body)
    default = None
    notes: list = []
    if requested:
        # A pick is honoured or refused loudly, with the reason, before anything runs: never a quiet
        # other model (Stephen, 2026-11-14: "I selected opus ... no response at all").
        from agent_friday.tv_gateway import models
        try:
            row = models.resolve(requested, _slate(dev))
        except models.ModelRefused as e:
            turn.device_pairing._receipt("device_turn_refused", dev, watching="owner", requested=requested,
                                         code=e.code, reason=e.reason[:160])
            raise turn.TurnRefused(e.status, e.reason, e.code, **e.extra)
        except turn.TurnRefused:
            raise
        except Exception as e:
            _log.warning("model slate failed for an owner turn: %s", type(e).__name__)
            raise turn.TurnRefused(503, "this computer could not check that model right now; try again",
                                   "slate_unavailable", requested=requested)
        requested = row["id"]                   # a family word or a display name means this one id
        bound = bind_pick(cid, requested, row, str((dev or {}).get("id") or ""))
        cid, notes = bound.cid, list(bound.notes)
    elif voice:
        cid, default = fast_voice_default(cid, dev)
    from agent_friday.tv_gateway import tailnet
    turn.device_pairing._receipt("device_turn", dev, watching="owner", provider="owner-agent",
                                 model=requested, tools_offered=0, via=tailnet.via())
    kind = str((dev or {}).get("kind") or "").strip().lower()
    word = {"tablet": "Tablet", "phone": "Phone", "watch": "Watch"}.get(kind, "Remote")
    shown = requested or (default or {}).get("model") or ""
    _CTX.activity = ("%s %s" % (word, "voice chat" if voice else "chat"), shown)
    try:
        response = _post(text, cid)
    except Exception as e:
        _log.info("owner turn not started: %s", type(e).__name__)
        raise turn.TurnRefused(503, "this computer can't answer that right now")
    finally:
        _CTX.activity = None
    if response.status_code != 200:
        raise turn.TurnRefused(503 if response.status_code >= 500 else response.status_code,
                               "this computer did not take the turn")
    return _stream(response, dev, requested, cid, voice=voice, voice_default=default, asked=asked, notes=notes)


def _status(status: str, started: float, detail: str = "") -> str:
    """An SSE `ping` that carries what the desktop is doing. Anthropic's stream defines `ping`, so a
    reader that knows nothing of it ignores it; one that does shows "thinking" instead of silence."""
    return _event({"type": "ping", "friday": {"status": status, "detail": detail,
                                              "elapsed_s": int(time.time() - started)}})


def _with_notes(truth: dict, notes: Optional[list]) -> dict:
    """What the bind had to say (a lost chat, a fallback's reason, a cloud disclosure) joins the desktop's
    notice, on an answer and on a turn that came back empty alike: a one-time note is heard once."""
    truth["notice"] = " ".join(s for s in [str(truth.get("notice") or "")] + [str(n) for n in notes or []]
                               if s).strip() or None
    return truth


def _public(truth: dict) -> dict:
    """The block as a device reads it: no JSON null (a phone's JSON reader prints one as the word "null")."""
    return {k: v for k, v in truth.items() if v is not None}


def _stream(response, dev: Optional[dict] = None, requested: str = "", cid: Optional[str] = None,
            voice: bool = False, voice_default: Optional[dict] = None, asked: str = "",
            notes: Optional[list] = None) -> Iterator[str]:
    """The phone's stream: progress while the desktop works, then the FINAL answer only.

    The desktop's own deltas are not relayed as they arrive: they can carry a tool round's narration,
    and a local reasoning model's scratchpad is not an answer. What the phone gets is the turn's
    final reply (the same text the desktop saves), after `ping` events that say it is working."""
    started = time.time()
    yield _event({"type": "message_start", "message": {"role": "assistant", "model": "", "content": []}})
    yield _event({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    yield _status("thinking", started, voice_reply.THINKING)
    spoken = []
    final = ""
    failed = None
    payload: dict = {}
    tools = 0
    ended = "closed"
    try:
        for f in _frames_of(response):
            if time.time() - started > MAX_TURN_S:
                failed, ended = "the computer is still working on this after %d minutes; the answer will be on its screen" % (
                    MAX_TURN_S // 60), "deadline"
                break
            if f is None:
                yield _status("thinking", started, voice_reply.THINKING)
            elif "tool" in f:
                tools += 1
                yield _status("working", started, voice_reply.progress_words(f["tool"]))
            elif "delta" in f:
                spoken.append(str(f["delta"]))
            elif "error" in f:
                failed = str(f["error"])
            elif f.get("done"):
                payload = f.get("payload") or {}
                final = str(payload.get("response") or "")
                ended = "done"
    except GeneratorExit:
        # The phone dropped the connection (app backgrounded, radio handoff, its own read timeout): the next write
        # raised here. The turn keeps running on the PC, and this is the line that says the phone was gone.
        _log.warning("owner turn finished: ended=disconnected seconds=%d tools=%d answer_chars=%d (the phone closed "
                     "the stream before the answer was sent)", int(time.time() - started), tools, len(final))
        raise
    text = answer_only(final) or answer_only("".join(spoken))
    # One line per turn so a missing answer can be traced: how it ended, how long, how much came back.
    _log.info("owner turn finished: ended=%s seconds=%d tools=%d answer_chars=%d failed=%s", ended,
              int(time.time() - started), tools, len(text), bool(failed))
    if not text:
        # Never an empty "success": the phone is told why there is no answer (stream closed early, only a reasoning
        # scratchpad came back, a failure, the deadline). Guarded by tests/unit/test_owner_turn_long_turn.py.
        yield _event({"type": "error", "error": {"type": "api_error", "message": (failed or (
            "the computer finished without an answer; it may be on its screen")) [:300]},
                      "friday": _public(_with_notes(served(payload, requested, cid), notes))})
        return
    truth = _with_notes(served(payload, requested, cid), notes)
    if dev is not None:
        from agent_friday.tv_gateway import turn
        from agent_friday.tv_gateway.tailnet import via as tailnet_via
        turn.device_pairing._receipt("device_turn_served", dev, watching="owner", provider=truth["provider"],
                                     model=truth["model"], requested=truth["requested"] or "",
                                     seat=truth["seat"], substituted=truth["substituted"],
                                     via=tailnet_via())
    if asked and asked != requested:
        truth["asked"] = asked                                  # the word the device sent ("opus"), beside the id it meant
    truth["voice"] = bool(voice)
    truth["voice_default"] = voice_default
    truth["speech"] = voice_reply.speech_text(text)             # for TTS: no markdown, ever
    truth["voice_bound"] = bound_voice(cid) if voice else None   # a voice chosen for this chat; None until voices.py (CORE-MODELS) fills the hook
    if voice:
        truth["formatted"] = text                               # the screen's copy; the stream text is what is spoken
        text = truth["speech"] or text                          # an answer that is only a table or code is still sent
    if text:
        yield _event({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
    yield _event({"type": "content_block_stop", "index": 0})
    yield _event({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "friday": _public(truth)})
    yield _event({"type": "message_stop"})
