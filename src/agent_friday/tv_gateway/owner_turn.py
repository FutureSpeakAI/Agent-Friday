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

How: the turn runs on this PC's own app, in process, with this PC's own session token (a gateway
device never holds or sees one). Nothing about the owner's chat is reimplemented here.
"""
from __future__ import annotations

import json
import logging
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


def _post(text: str, conversation_id: Optional[str]):
    """Run the owner's chat turn on this PC's own app, in process."""
    from agent_friday import core
    body = {"message": text}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return core.app.test_client().post(
        "/api/chat/stream", json=body, buffered=False,
        headers={"X-Friday-Token": core._current_api_token()})


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
    turn.device_pairing._receipt("device_turn", dev, watching="owner", provider="owner-agent",
                                 model="", tools_offered=0)
    try:
        response = _post(text, cid)
    except Exception as e:
        _log.info("owner turn not started: %s", type(e).__name__)
        raise turn.TurnRefused(503, "this computer can't answer that right now")
    if response.status_code != 200:
        raise turn.TurnRefused(503 if response.status_code >= 500 else response.status_code,
                               "this computer did not take the turn")
    return _stream(response)


def _stream(response) -> Iterator[str]:
    yield _event({"type": "message_start", "message": {"role": "assistant", "model": "", "content": []}})
    yield _event({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    streamed = False
    final = ""
    failed = None
    for f in _frames(response):
        if "delta" in f:
            streamed = True
            yield _event({"type": "content_block_delta", "index": 0,
                          "delta": {"type": "text_delta", "text": str(f["delta"])}})
        elif "error" in f:
            failed = str(f["error"])
        elif f.get("done"):
            final = str((f.get("payload") or {}).get("response") or "")
    if failed and not streamed:
        yield _event({"type": "error", "error": {"type": "api_error", "message": failed[:300]}})
        return
    if not streamed and final:
        yield _event({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": final}})
    yield _event({"type": "content_block_stop", "index": 0})
    yield _event({"type": "message_delta", "delta": {"stop_reason": "end_turn"}})
    yield _event({"type": "message_stop"})
