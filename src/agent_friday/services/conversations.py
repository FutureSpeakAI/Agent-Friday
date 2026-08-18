"""Conversations — many working threads, one memory.

Friday had exactly one transcript. "+ New Chat" cleared it, so there was no
such thing as going back to an earlier conversation: the previous one was
deleted. Stephen, 2026-08-18: "I want to trigger background tasks, go to a new
chat, talk with a different model, then go back to the other chat to get an
update from the other model while the other other model does something in the
background too."

This is the store that makes a conversation a real object, per
docs/design/conversations-and-concurrency.md §3.1. Two rules from that spec are
load-bearing and easy to get wrong:

  * **Transcripts are isolated; memory is shared.** A turn in conversation A is
    never context for conversation B. But ChromaDB, the wiki, the knowledge
    graph and the vault context are Friday's *memory* and stay global — what
    she knows does not fork per chat.
  * **A conversation with `seat = null` follows the global default AT DISPATCH
    TIME**, not a snapshot taken when it was created. So today's
    single-conversation behaviour is the degenerate case of this design, and
    nothing changes for anyone who never opens a second chat.

Layout, one directory per conversation:

    ~/.friday/conversations/<conversation_id>/
        conversation.json     metadata; atomic tmp+fsync+replace, the house pattern
        messages.jsonl        append-only, one message per line

Append-only matters: a crash mid-write costs the last line, not the thread. The
old chat_history.json rewrote the entire history on every turn, which is why a
half-written file could take the lot.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

from agent_friday.core import FRIDAY_DIR

# The conversation everything unaddressed reports into: voice, channels, the
# scheduler, and any caller that predates conversation_id. Named rather than
# "the first one" so the target is stable across restarts and deletions.
MAIN_ID = "conv-main"

_LOCK = threading.RLock()
_PRUNE_KEEP = 500          # per conversation, matching the old global prune


def _root() -> Path:
    return Path(FRIDAY_DIR) / "conversations"


def _dir(cid: str) -> Path:
    return _root() / cid


def new_id() -> str:
    return "conv-" + secrets.token_hex(4)


def _atomic_write(path: Path, text: str) -> None:
    """tmp + fsync + replace — the durability guarantee used for settings.

    A half-written conversation.json would otherwise read as a missing
    conversation, and a missing conversation is an erased one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    except Exception:
        pass
    tmp.replace(path)


def _blank(cid: str, title: str = "New chat") -> dict:
    now = time.time()
    return {
        "id": cid,
        "title": title,
        "created_at": now,
        "last_active_at": now,
        # null means "follow the global capability_routing.reasoning, resolved
        # per turn". Not a snapshot: see the module docstring.
        "seat": None,
        "status": "active",
        "pinned": [],
        "totals": {"turns": 0, "cost_usd": 0.0, "tokens": 0},
    }


# ── Metadata ────────────────────────────────────────────────────────────────

def load(cid: str) -> dict | None:
    p = _dir(cid) / "conversation.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def save(conv: dict) -> dict:
    with _LOCK:
        conv["last_active_at"] = conv.get("last_active_at") or time.time()
        _atomic_write(_dir(conv["id"]) / "conversation.json",
                      json.dumps(conv, indent=2))
    return conv


def create(title: str = "New chat", seat: dict | None = None,
           cid: str | None = None) -> dict:
    with _LOCK:
        conv = _blank(cid or new_id(), title)
        if seat:
            conv["seat"] = seat
        _dir(conv["id"]).mkdir(parents=True, exist_ok=True)
        (_dir(conv["id"]) / "messages.jsonl").touch(exist_ok=True)
        return save(conv)


def ensure_main() -> dict:
    """The main conversation, created (and migrated into) on first need."""
    conv = load(MAIN_ID)
    if conv is None:
        conv = create(title="Main", cid=MAIN_ID)
        _migrate_legacy_history(conv)
    return conv


def list_all(include_archived: bool = True) -> list[dict]:
    out = []
    root = _root()
    if root.exists():
        for d in root.iterdir():
            if not d.is_dir():
                continue
            conv = load(d.name)
            if conv is None:
                continue
            if not include_archived and conv.get("status") == "archived":
                continue
            out.append(conv)
    out.sort(key=lambda c: c.get("last_active_at") or 0, reverse=True)
    return out


def patch(cid: str, **fields) -> dict | None:
    """Rename / archive / rebind seat. Unknown keys are ignored on purpose."""
    with _LOCK:
        conv = load(cid)
        if conv is None:
            return None
        for k in ("title", "status", "seat", "pinned", "totals"):
            if k in fields:
                conv[k] = fields[k]
        conv["last_active_at"] = time.time()
        return save(conv)


# ── Messages ────────────────────────────────────────────────────────────────

def append(cid: str, message: dict) -> dict:
    """Append one message. Creates the conversation if it does not exist.

    Auto-titles from the first user message: a list of conversations all called
    "New chat" is a list you cannot navigate.
    """
    with _LOCK:
        conv = load(cid)
        if conv is None:
            conv = create(cid=cid)
        message.setdefault("id", secrets.token_hex(8))
        message.setdefault("ts", time.time())
        with open(_dir(cid) / "messages.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(message, ensure_ascii=False) + "\n")

        conv["last_active_at"] = time.time()
        if message.get("role") == "user":
            conv.setdefault("totals", {})["turns"] = \
                (conv.get("totals", {}).get("turns") or 0) + 1
            if conv.get("title") in (None, "", "New chat"):
                t = " ".join(str(message.get("text") or "").split())[:60]
                if t:
                    conv["title"] = t
        save(conv)
        return message


def messages(cid: str, limit: int | None = None) -> list[dict]:
    p = _dir(cid) / "messages.jsonl"
    if not p.exists():
        return []
    out = []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue          # one bad line never costs the thread
    except Exception:
        return []
    return out[-limit:] if limit else out


def clear(cid: str, include_pinned: bool = False) -> int:
    """Scoped clear — the per-conversation replacement for /api/chat/clear.

    Pinned messages survive unless asked otherwise, matching the old semantics.
    Crucially this touches ONE conversation: the global clear is what made
    "+ New Chat" destroy the previous thread.
    """
    with _LOCK:
        conv = load(cid)
        if conv is None:
            return 0
        keep = [] if include_pinned else [
            m for m in messages(cid) if m.get("pinned")]
        removed = len(messages(cid)) - len(keep)
        body = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in keep)
        _atomic_write(_dir(cid) / "messages.jsonl", body)
        conv.setdefault("totals", {})["turns"] = sum(
            1 for m in keep if m.get("role") == "user")
        save(conv)
        return max(0, removed)


def prune(cid: str, keep: int = _PRUNE_KEEP) -> int:
    """Cap a single conversation's transcript. Pins are never pruned."""
    with _LOCK:
        msgs = messages(cid)
        if len(msgs) <= keep:
            return 0
        pinned = [m for m in msgs if m.get("pinned")]
        tail = [m for m in msgs[-keep:] if not m.get("pinned")]
        kept = pinned + tail
        body = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in kept)
        _atomic_write(_dir(cid) / "messages.jsonl", body)
        return len(msgs) - len(kept)


def add_cost(cid: str, cost_usd: float = 0.0, tokens: int = 0) -> None:
    """Per-conversation spend, so several cloud chats do not blur together."""
    with _LOCK:
        conv = load(cid)
        if conv is None:
            return
        t = conv.setdefault("totals", {})
        t["cost_usd"] = round((t.get("cost_usd") or 0.0) + (cost_usd or 0.0), 6)
        t["tokens"] = (t.get("tokens") or 0) + (tokens or 0)
        save(conv)


# ── Migration ───────────────────────────────────────────────────────────────

def _migrate_legacy_history(conv: dict) -> int:
    """Import the single global chat_history.json into the main conversation.

    One-shot and non-destructive: the old file is left where it is. If this is
    ever run twice the guard below keeps it from duplicating the transcript.
    """
    if conv.get("_migrated_legacy"):
        return 0
    legacy = Path(FRIDAY_DIR) / "chat_history.json"
    if not legacy.exists():
        conv["_migrated_legacy"] = True
        save(conv)
        return 0
    try:
        rows = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:
        rows = []
    n = 0
    if isinstance(rows, list):
        with open(_dir(conv["id"]) / "messages.jsonl", "a", encoding="utf-8") as fh:
            for r in rows:
                if not isinstance(r, dict):
                    continue
                fh.write(json.dumps({
                    "id": r.get("id") or secrets.token_hex(8),
                    "role": r.get("role") or "user",
                    "ts": r.get("ts") or time.time(),
                    "text": r.get("text") or "",
                    "pinned": bool(r.get("pinned")),
                    "meta": {"kind": "turn", "migrated": True,
                             "sources": r.get("sources") or []},
                }, ensure_ascii=False) + "\n")
                n += 1
    conv["_migrated_legacy"] = True
    conv.setdefault("totals", {})["turns"] = sum(
        1 for m in messages(conv["id"]) if m.get("role") == "user")
    save(conv)
    if n:
        print(f"  [conversations] migrated {n} message(s) into {conv['id']}")
    return n


def resolve(cid: str | None) -> str:
    """Any caller without a conversation_id addresses Main.

    Voice, channels and the scheduler predate conversations and must keep
    working unchanged; giving them a real destination is what stops their
    output from having nowhere to go.
    """
    if cid and load(cid) is not None:
        return cid
    ensure_main()
    return MAIN_ID
