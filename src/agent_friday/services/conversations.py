"""Conversations — many working threads, one memory.

A single transcript that "+ New Chat" clears means there is no such thing as
going back to an earlier conversation. The maintainer's requirement: "I want
to trigger background tasks, go to a new chat, talk with a different model,
then go back to the other chat to get an update from the other model while
the other other model does something in the background too."

This is the store that makes a conversation a real object, per
docs/design/implemented/conversations-and-concurrency.md §3.1. Two rules from that spec are
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
from agent_friday.paths import contained, safe_name

# The conversation everything unaddressed reports into: voice, channels, the
# scheduler, and any caller that predates conversation_id. Named rather than
# "the first one" so the target is stable across restarts and deletions.
MAIN_ID = "conv-main"

_LOCK = threading.RLock()
_PRUNE_KEEP = 500          # per conversation, matching the old global prune


def _root() -> Path:
    return Path(FRIDAY_DIR) / "conversations"


def _dir(cid: str) -> Path:
    """The conversation's folder. Raises ValueError unless the id is one plain
    name inside the conversations root; ids arrive in requests."""
    return contained(_root(), safe_name(cid, what="conversation id"))


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
        # Which project folder this thread sits in, or None for loose chats.
        # The conversation owns this, not the project: see projects.py on why
        # membership has exactly one writer.
        "project": None,
        # When this thread was pinned to the top of the sidebar, or None.
        #
        # A TIMESTAMP RATHER THAN A FLAG, and named apart from the `pinned`
        # below on purpose. `pinned` here is a list of pinned MESSAGE ids - a
        # different feature that predates thread pinning and still drives
        # `clear` and `prune`. The list endpoint used to flatten it to a
        # boolean and hand it to the UI under the name `pinned`, where it read
        # exactly like "this thread is pinned" and always meant False. Two
        # features, one word, one of them silently broken; so they get two
        # words. The timestamp also gives pin ordering for free.
        "pinned_at": None,
        "pinned": [],
        "totals": {"turns": 0, "cost_usd": 0.0, "tokens": 0},
    }


# ── Metadata ────────────────────────────────────────────────────────────────

def load(cid: str) -> dict | None:
    try:
        p = _dir(cid) / "conversation.json"
    except ValueError:
        return None
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
    """Rename / archive / rebind seat / refile / pin. Unknown keys ignored."""
    with _LOCK:
        conv = load(cid)
        if conv is None:
            return None
        for k in ("title", "status", "seat", "pinned", "pinned_at",
                  "project", "totals"):
            if k in fields:
                conv[k] = fields[k]
        # Filing a chat is not working in it. Re-stamping last_active_at on a
        # refile or a pin would shuffle the whole sidebar every time you
        # tidied it, and tidying that reorders what you were tidying is worse
        # than no tidying at all.
        if any(k in fields for k in ("title", "status", "seat", "totals")):
            conv["last_active_at"] = time.time()
        return save(conv)


def effective_seat(cid: str) -> dict | None:
    """The seat a turn in this conversation should actually run on.

    The order is global default, then project, then the chat itself, with the
    most specific winner - and the chat still overrides its project, so
    pinning one thread in a Bonsai project to Sonnet works.

    RESOLVED PER TURN, NEVER SNAPSHOTTED. That is the existing rule for a null
    conversation seat, stated at the top of this module, and inserting a
    project level does not get to change it: renaming a project's default
    model has to affect the chats inside it immediately, or the default is
    not a default but a stamp applied at creation time.

    Returns None for "no binding at either level", which the router already
    reads as "follow the global capability_routing default".
    """
    return effective_seat_of(load(cid) or {})


def effective_seat_of(conv: dict, project_seats: dict | None = None):
    """`effective_seat` for a conversation record already in hand.

    The list endpoint summarises every conversation it just read. Going back to
    disk for each one - and then again for its project - would make listing N
    chats cost 2N+ file reads on a surface the sidebar polls. Pass a
    `{project_id: seat}` map built once and it costs none.
    """
    seat = (conv or {}).get("seat")
    if isinstance(seat, dict) and (seat.get("model") or "").strip():
        return seat
    pid = (conv or {}).get("project")
    if not pid:
        return None
    if project_seats is not None:
        pseat = project_seats.get(pid)
    else:
        try:
            from agent_friday.services import projects as _proj
            pseat = (_proj.load(pid) or {}).get("seat")
        except Exception:
            return None
    if isinstance(pseat, dict) and (pseat.get("model") or "").strip():
        return pseat
    return None


def project_instructions(cid: str) -> str:
    """Standing instructions from this chat's project, or ''.

    Charged as input tokens on every turn in the project, which is why
    projects.MAX_INSTRUCTIONS exists.
    """
    pid = (load(cid) or {}).get("project")
    if not pid:
        return ""
    try:
        from agent_friday.services import projects as _proj
        return str((_proj.load(pid) or {}).get("instructions") or "")
    except Exception:
        return ""


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
    try:
        p = _dir(cid) / "messages.jsonl"
    except ValueError:
        return []
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
    except Exception as e:
        # Do NOT mark this migrated. A one-shot import that marks itself done
        # after failing silently eats the transcript: the new conversation
        # comes up holding a fraction of what chat_history.json still holds,
        # and the flag means it will never look again. An import that did not
        # import has not run.
        print(f"  [conversations] legacy history unreadable, NOT marking "
              f"migrated: {e}")
        return 0
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
    if isinstance(rows, list) and rows and n == 0:
        # Rows were there and none came across — a shape this build does not
        # understand. Leaving the flag off means a later build can still
        # rescue it; the user's words are not something to give up on quietly.
        print(f"  [conversations] {len(rows)} legacy message(s) present but "
              f"none could be imported - leaving them for a later attempt")
        return 0
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
