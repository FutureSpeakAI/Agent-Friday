"""DAgger capture for the Laya router's tool head (DA-1a).

When the owner corrects a routed turn, the correction is a training example
for the next head (weight x5 in tools/tool_head/dagger.py). Three
corrections are recognised, each from what really happened:

  undo      an undo of a change within ``UNDO_WINDOW_S`` (120 s) of the routed
            turn: the routed choice was too eager; the example says "ask".
  ask       the router asked and the owner's answer ran a DIFFERENT tool
            than the one the question named: that tool is the label.
  defer     the router deferred and the deeper mind resolved the request to
            a specific tool that ran: that tool is the label.

Rows go to ``<runtime>/tool_head/<profile>/shadow.jsonl`` through
``tool_head.dagger`` (same schema and the same ``learn_enabled`` strictness).
What a row holds depends on the owner's opt-in
(``tool_head_learn_from_my_history``): off, hashes and operations only; on,
the words and the chosen arguments too, kept in that profile's folder on
this PC. A tainted turn, a failed call and a denied call are never
recorded, and the words of a turn are not even held in memory without the
opt-in. Nothing here sends anything anywhere.

Tools that need the head's training code (``tools/tool_head``) find it
beside the package; where it is not shipped, capture is a quiet no-op.
"""
from __future__ import annotations

import hashlib
import logging
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

_log = logging.getLogger("friday.laya_router_learn")

UNDO_WINDOW_S = 120.0
ASK_WINDOW_S = 120.0
DEFER_WINDOW_S = 600.0
MAX_HELD = 20
#: Defer handoffs and card answers are the router's own machinery, never a label.
_NOT_A_LABEL = frozenset({"delegate_to_friday", "answer_card", "undo_action", "task_control"})

_lock = threading.Lock()
_held: "deque[dict]" = deque(maxlen=MAX_HELD)
#: Where rows go (tests replace it); None means the profile folder under runtime.
root_override: Optional[Path] = None
profile_name = "owner"
now_fn = time.time


def reset() -> None:
    with _lock:
        _held.clear()


def _dagger():
    d = str(Path(__file__).resolve().parents[3] / "tools")
    if not Path(d).is_dir():
        return None
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        from tool_head import common, dagger
        return common, dagger
    except Exception:
        return None


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _learning(settings: dict) -> bool:
    mods = _dagger()
    return bool(mods and mods[0].learn_enabled(settings))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def note_route(text: str, r, *, surface: str = "voice", conversation_id: Optional[str] = None,
               settings: Optional[dict] = None) -> None:
    """Remember a routed turn so a later correction can find it. In memory,
    bounded; the words are kept only with the owner's opt-in."""
    if not text or r is None or r.tool == "answer_card":
        return
    keep = _learning(settings if settings is not None else _settings())
    ent = {"at": now_fn(), "hash": _sha(text), "text": text if keep else None,
           "decision": r.decision, "tool": r.tool, "args": dict(r.args or {}),
           "confidence": r.confidence, "layer": r.layer, "surface": surface,
           "conversation_id": conversation_id, "resolved": False}
    with _lock:
        _held.append(ent)


def _same_conversation(ent: dict, conversation_id: Optional[str]) -> bool:
    a, b = ent.get("conversation_id"), conversation_id
    return a is None or b is None or str(a) == str(b)


def _router_op(ent: dict) -> str:
    if ent["decision"] == "tool":
        return ent["tool"]
    return {"no_tool": "no_tool", "ask": "ask"}.get(ent["decision"], "brain")


def _write(ent: dict, *, actor: str, op: str, args: Optional[dict], tainted: bool,
           settings: dict, root=None) -> bool:
    mods = _dagger()
    if mods is None:
        return False
    common, dagger = mods
    root = root if root is not None else root_override
    try:
        text_block = None
        if common.learn_enabled(settings) and ent.get("text") and not tainted:
            from agent_friday.services import laya_router_head as lh
            spans = lh.candidate_spans(ent["text"])
            gold, free = {}, []
            for k, v in (args or {}).items():
                if k == "query" and isinstance(v, str) and v.lower() in {s.lower() for s in spans}:
                    gold[k] = next(s for s in spans if s.lower() == v.lower())
                elif isinstance(v, bool):
                    gold[k] = v
                else:
                    free.append(k)
            text_block = {"gold_args": gold, "candidates": {"query": spans} if "query" in gold else {},
                          "free_args": free, "history": []}
        rec = dagger.make_record(
            profile=profile_name, ts=now_fn(), turn_id=ent["hash"][:12], surface=ent["surface"],
            utterance=ent["text"] or ent["hash"],
            head={"op": _router_op(ent), "score": ent["confidence"], "model": "router:" + str(ent["layer"]),
                  "args_hash": common.args_hash(ent["args"]) if ent["args"] else None},
            actual={"actor": actor, "op": op, "ok": True, "denied": False, "tainted": bool(tainted),
                    "args_hash": common.args_hash(args) if args else None},
            text=text_block)
        # The row's own utterance hash is of what was passed in; a held-less
        # turn (no opt-in) carries the stored hash as its "utterance".
        rec["utterance_hash"] = ent["hash"]
        dagger.log_shadow(profile_name, rec, settings=settings, home=root)
        return True
    except Exception as e:  # noqa: BLE001
        _log.warning("router correction not recorded: %s", type(e).__name__)
        return False


def note_undo(*, conversation_id: Optional[str] = None, settings: Optional[dict] = None,
              root=None) -> bool:
    """An undo just happened: if the newest routed turn is within 120 s, it
    was corrected. The example's label is "ask"."""
    now = now_fn()
    with _lock:
        ent = next((e for e in reversed(_held)
                    if not e["resolved"] and e["decision"] in ("tool", "defer")
                    and now - e["at"] <= UNDO_WINDOW_S and _same_conversation(e, conversation_id)), None)
        if ent is None:
            return False
        ent["resolved"] = True
    return _write(ent, actor="owner", op="ask", args=None, tainted=False,
                  settings=settings if settings is not None else _settings(), root=root)


def note_executed(tool: str, args: Optional[dict] = None, *, ok: bool, tainted=False,
                  denied: bool = False, actor: str = "brain", conversation_id: Optional[str] = None,
                  settings: Optional[dict] = None, root=None) -> bool:
    """A tool really ran. If exactly one routed ask or defer is waiting for
    its resolution, that resolution is the correction: an ask answered with
    a different tool (the owner's), a defer the brain resolved to a tool.
    ``tainted`` is a bool or a callable evaluated only when a correction is
    possible (it is read on every tool call otherwise)."""
    if not ok or denied or not tool or tool in _NOT_A_LABEL:
        return False
    if tainted is True:
        return False
    now = now_fn()
    with _lock:
        waiting = [e for e in _held
                   if not e["resolved"] and _same_conversation(e, conversation_id)
                   and ((e["decision"] == "ask" and now - e["at"] <= ASK_WINDOW_S)
                        or (e["decision"] == "defer" and now - e["at"] <= DEFER_WINDOW_S))]
        if len(waiting) != 1:
            return False
        ent = waiting[0]
        if callable(tainted):
            try:
                if tainted():
                    return False   # the turn read untrusted content: never a label
            except Exception:
                return False
        if ent["decision"] == "ask" and ent["tool"] == tool:
            ent["resolved"] = True      # the question named the right tool: nothing to learn
            return False
        ent["resolved"] = True
    who = "owner" if ent["decision"] == "ask" else "brain"
    return _write(ent, actor=who, op=tool, args=args, tainted=False,
                  settings=settings if settings is not None else _settings(), root=root)
