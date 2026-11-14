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
cannot take is refused with its reason, never answered by another model). The reply's closing
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
from typing import Iterator, Optional

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


def _call_desktop(text: str, conversation_id: Optional[str]):
    """The owner's chat turn on this PC's own app, in process, with this PC's own session token."""
    from agent_friday import core
    body = {"message": text}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return core.app.test_client().post(
        "/api/chat/stream", json=body, buffered=False,
        headers={"X-Friday-Token": core._current_api_token()})


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

        def work():
            try:
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


def _listed(model_id: str) -> bool:
    """True when this PC lists the id as an orchestrator seat it can use now (the pill's own list)."""
    try:
        from agent_friday.services.model_catalog import build_catalog
        rows = (build_catalog(include_engines=False).get("roles") or {}).get("orchestrator") or []
    except Exception:
        return False
    return any(isinstance(r, dict) and r.get("id") == model_id and r.get("available") is not False for r in rows)


def _json(resp) -> dict:
    try:
        return resp.get_json(silent=True) or {}
    except Exception:
        return {}


def bind_seat(cid: Optional[str], model_id: str) -> str:
    """Make the chat run on ``model_id`` and return its conversation id. Raises turn.TurnRefused
    (409) with the PC's own reason when the seat cannot be bound: never a quiet other model."""
    from agent_friday.tv_gateway import turn
    seat = {"model": model_id}
    if not cid:
        d = _json(_api("POST", "/api/conversations", {"title": "Phone chat", "seat": seat}))
        conv = d.get("conversation") or {}
        cid = conv.get("id")
        if not cid:
            raise turn.TurnRefused(503, "this computer could not start that chat")
    else:
        d = _json(_api("GET", "/api/conversations/" + cid))
        conv = d.get("conversation") or {}
        if (conv.get("seat") or {}).get("model") == model_id:
            return cid                      # already bound: nothing to change, nothing to reload
        d = _json(_api("PATCH", "/api/conversations/" + cid, {"seat": seat}))
        conv = d.get("conversation") or {}
    if (conv.get("seat") or {}).get("model") != model_id:
        raise turn.TurnRefused(409, str(d.get("note") or "this computer would not use that model for this chat")[:240])
    return cid


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
    cid = body.get("conversation_id")
    cid = cid.strip() if isinstance(cid, str) and cid.strip() else None
    requested = requested_model(body)
    if requested and _listed(requested):
        cid = bind_seat(cid, requested)
    turn.device_pairing._receipt("device_turn", dev, watching="owner", provider="owner-agent",
                                 model=requested, tools_offered=0)
    try:
        response = _post(text, cid)
    except Exception as e:
        _log.info("owner turn not started: %s", type(e).__name__)
        raise turn.TurnRefused(503, "this computer can't answer that right now")
    if response.status_code != 200:
        raise turn.TurnRefused(503 if response.status_code >= 500 else response.status_code,
                               "this computer did not take the turn")
    return _stream(response, dev, requested, cid)


def _status(status: str, started: float, detail: str = "") -> str:
    """An SSE `ping` that carries what the desktop is doing. Anthropic's stream defines `ping`, so a
    reader that knows nothing of it ignores it; one that does shows "thinking" instead of silence."""
    return _event({"type": "ping", "friday": {"status": status, "detail": detail,
                                              "elapsed_s": int(time.time() - started)}})


def _stream(response, dev: Optional[dict] = None, requested: str = "", cid: Optional[str] = None) -> Iterator[str]:
    """The phone's stream: progress while the desktop works, then the FINAL answer only.

    The desktop's own deltas are not relayed as they arrive: they can carry a tool round's narration,
    and a local reasoning model's scratchpad is not an answer. What the phone gets is the turn's
    final reply (the same text the desktop saves), after `ping` events that say it is working."""
    started = time.time()
    yield _event({"type": "message_start", "message": {"role": "assistant", "model": "", "content": []}})
    yield _event({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    yield _status("thinking", started)
    spoken = []
    final = ""
    failed = None
    payload: dict = {}
    for f in _frames_of(response):
        if f is None:
            yield _status("thinking", started)
        elif "tool" in f:
            yield _status("working", started, _tool_label(f["tool"]))
        elif "delta" in f:
            spoken.append(str(f["delta"]))
        elif "error" in f:
            failed = str(f["error"])
        elif f.get("done"):
            payload = f.get("payload") or {}
            final = str(payload.get("response") or "")
    text = answer_only(final) or answer_only("".join(spoken))
    if failed and not text:
        yield _event({"type": "error", "error": {"type": "api_error", "message": failed[:300]},
                      "friday": served({}, requested, cid)})
        return
    truth = served(payload, requested, cid)
    if dev is not None:
        from agent_friday.tv_gateway import turn
        turn.device_pairing._receipt("device_turn_served", dev, watching="owner", provider=truth["provider"],
                                     model=truth["model"], requested=truth["requested"] or "",
                                     seat=truth["seat"], substituted=truth["substituted"])
    if text:
        yield _event({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
    yield _event({"type": "content_block_stop", "index": 0})
    yield _event({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "friday": truth})
    yield _event({"type": "message_stop"})
