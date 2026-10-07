"""Hand a result to a live voice call on a conversation, if one is open.

A spoken request can outlive the turn that asked for it: work delegated to the
full agent (delegate_to_friday), or private context the user approved on a card
(ask_local_for_context), finishes seconds or minutes later. The voice bridge
registers a delivery function for its conversation while the call is up;
whoever finishes the work calls `deliver`. The bridge queues the text and gives
it to the live model at the next quiet moment (never mid-reply), through the
same egress gate as any tool result.

`deliver` returns False when no call is listening, so the caller knows the
result reached only the conversation's transcript.
"""
from __future__ import annotations

import logging
import threading

_log = logging.getLogger("friday.voice_channel")
_LOCK = threading.Lock()
_CHANNELS: dict = {}
_CREW_CHANNELS: dict = {}


def register(conversation_id, deliver_fn) -> None:
    if not conversation_id or deliver_fn is None:
        return
    with _LOCK:
        _CHANNELS[str(conversation_id)] = deliver_fn


def unregister(conversation_id, deliver_fn=None) -> None:
    if not conversation_id:
        return
    with _LOCK:
        cur = _CHANNELS.get(str(conversation_id))
        if deliver_fn is None or cur is deliver_fn:
            _CHANNELS.pop(str(conversation_id), None)


def is_live(conversation_id) -> bool:
    with _LOCK:
        return bool(conversation_id) and str(conversation_id) in _CHANNELS


def deliver(conversation_id, text: str, *, kind: str = "result") -> bool:
    """Queue `text` for the live call on this conversation. False if none."""
    if not conversation_id or not str(text or "").strip():
        return False
    with _LOCK:
        fn = _CHANNELS.get(str(conversation_id))
    if fn is None:
        return False
    try:
        fn(str(text), kind)
        return True
    except Exception as e:  # noqa: BLE001
        _log.warning("voice channel delivery failed: %s", type(e).__name__)
        return False


def register_crew(conversation_id, deliver_fn) -> None:
    """Register structured reports separately from Friday's plain-text relay."""
    if conversation_id and deliver_fn is not None:
        with _LOCK:
            _CREW_CHANNELS[str(conversation_id)] = deliver_fn


def unregister_crew(conversation_id, deliver_fn=None) -> None:
    with _LOCK:
        cur = _CREW_CHANNELS.get(str(conversation_id))
        if deliver_fn is None or cur is deliver_fn:
            _CREW_CHANNELS.pop(str(conversation_id), None)


def deliver_crew(conversation_id, profile, text, *, task_id=None, off_record=None,
                 off_record_generation=None) -> bool:
    """Queue a trusted worker result in its open Crew call; never impersonate Friday.

    False means that the result remains in its conversation's written history.
    The registered room rechecks membership and profile revision before speech.
    """
    if not conversation_id or not isinstance(profile, dict) or not str(text or "").strip():
        return False
    with _LOCK:
        fn = _CREW_CHANNELS.get(str(conversation_id))
    if fn is None:
        return False
    try:
        kwargs = {"task_id": task_id}
        if off_record is not None:
            kwargs["off_record"] = off_record
        if off_record_generation is not None:
            kwargs["off_record_generation"] = off_record_generation
        return bool(fn(profile, str(text), **kwargs))
    except Exception as exc:
        _log.warning("Crew voice delivery failed: %s", type(exc).__name__)
        return False
