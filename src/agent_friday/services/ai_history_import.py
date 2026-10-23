"""Bring your AI history in: ChatGPT and Claude data exports, staged for review.

First-run spec §6.4 (the preview-first import) and §6.6 (AI histories); North Star
NS-36.1-2 and NS-8.10-2. The owner downloads their own data export from ChatGPT
(Settings > Data controls > Export) or Claude (Settings > Privacy > Export data) and
gives Friday the ``conversations.json`` inside it, or the export ``.zip`` itself.

What happens to it:

* **Read on this PC only.** The file is parsed here, offline: no model reads it and
  nothing is sent anywhere. Every value in it is untrusted text.
* **A preview first.** ``preview`` counts the conversations, messages and dates and
  writes nothing.
* **Staged, not stored.** ``stage`` turns each conversation into one item in import
  staging (services/import_staging, kind ``ai_conversation``) with its provenance: which
  service, the conversation's own id, its title and dates. Importing the same export
  again proposes nothing new.
* **Saved only when the owner accepts it.** Accepting one creates a Friday conversation
  marked as imported (its title starts with the service's name, every message keeps its
  original time and carries ``imported_from``), which the existing conversation search
  then finds. Nothing is turned into memory, facts or the knowledge graph here. Off the
  record, nothing is saved and the item keeps waiting.

Limits: an export of at most 200 MB (a zip may hold at most 400 MB uncompressed and its
``conversations.json`` must not expand more than 100 times), at most 20,000
conversations, at most 2,000 messages per conversation and 20,000 characters per message
(longer ones are cut and say so).
"""
from __future__ import annotations

import io
import json
import re
import threading
import zipfile
from datetime import datetime, timezone
from typing import List, Optional, Tuple

SOURCES = {"chatgpt": "ChatGPT", "claude": "Claude"}
KIND = "ai_conversation"
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_UNZIPPED_BYTES = 400 * 1024 * 1024
MAX_RATIO = 100
MAX_CONVERSATIONS = 20000
MAX_MESSAGES = 2000
MAX_CHARS = 20000
TITLE_MAX = 120
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: One export at a time: a large one holds a few hundred MB while it is read.
_ONE_AT_A_TIME = threading.Lock()
BUSY = "Another export is being read; try again when it has finished."
UNREADABLE = "The file could not be read as a ChatGPT or Claude export."


def _parsed(data: bytes):
    """(source, conversations) or raises ImportRefused. Any failure inside a hostile or
    damaged file is a refusal in words, never an error page."""
    try:
        doc = read_export(data)
        source = detect(doc)
        return source, conversations(doc, source)
    except ImportRefused:
        raise
    except Exception:
        raise ImportRefused(UNREADABLE)


class ImportRefused(ValueError):
    """The file cannot be read as that service's export. The message is for the owner."""


def _text(value, limit: int = MAX_CHARS) -> Tuple[str, bool]:
    s = _CTRL.sub("", str(value if value is not None else ""))
    return (s[:limit], len(s) > limit)


def _title(value) -> str:
    return re.sub(r"\s+", " ", _text(value, 400)[0]).strip()[:TITLE_MAX] or "Untitled conversation"


def _when(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if 0 < value < 4e10 else None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _day(ts: Optional[float]) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts and ts > 0 else ""
    except (OverflowError, OSError, ValueError):
        return ""


# ── reading the file ─────────────────────────────────────────────────────────

def read_export(data: bytes) -> list:
    """The conversations list from an export: the raw ``conversations.json`` bytes, or a
    zip that contains one. Raises ImportRefused with words for the owner."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ImportRefused("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ImportRefused("That file is larger than 200 MB.")
    raw = bytes(data)
    if raw[:2] == b"PK":
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            raise ImportRefused("The zip file could not be opened.")
        members = [i for i in zf.infolist()
                   if i.filename.replace("\\", "/").rsplit("/", 1)[-1] == "conversations.json"]
        if not members:
            raise ImportRefused("The zip has no conversations.json in it.")
        info = min(members, key=lambda i: i.filename.count("/"))
        if info.file_size > MAX_UNZIPPED_BYTES or (
                info.compress_size and info.file_size / max(1, info.compress_size) > MAX_RATIO):
            raise ImportRefused("conversations.json in that zip is too large to read safely.")
        with zf.open(info) as fh:
            raw = fh.read(MAX_UNZIPPED_BYTES + 1)
        if len(raw) > MAX_UNZIPPED_BYTES:
            raise ImportRefused("conversations.json in that zip is too large to read safely.")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ImportRefused("The file is not a conversations.json export (it is not readable JSON).")
    if isinstance(doc, dict) and isinstance(doc.get("conversations"), list):
        doc = doc["conversations"]
    if not isinstance(doc, list):
        raise ImportRefused("The file is not a conversations.json export.")
    return doc


def detect(doc: list) -> str:
    """Which service an export came from, from its shape."""
    for c in doc[:20]:
        if isinstance(c, dict):
            if isinstance(c.get("mapping"), dict):
                return "chatgpt"
            if isinstance(c.get("chat_messages"), list):
                return "claude"
    raise ImportRefused("This does not look like a ChatGPT or Claude export.")


def _chatgpt_parts(content) -> str:
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if isinstance(parts, list):
        return "\n".join(p for p in parts if isinstance(p, str))
    if isinstance(content.get("text"), str):
        return content["text"]
    return ""


def _chatgpt(conv: dict) -> Optional[dict]:
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict):
        return None
    # The conversation as it was last shown: from its current node back to the root.
    node = conv.get("current_node")
    if node not in mapping:
        leaves = [k for k, v in mapping.items() if isinstance(v, dict) and not v.get("children")]
        node = leaves[-1] if leaves else None
    chain, seen = [], set()
    while node and node in mapping and node not in seen and len(chain) < 100000:
        seen.add(node)
        chain.append(mapping[node])
        node = (mapping[node] or {}).get("parent")
    msgs = []
    for n in reversed(chain):
        m = (n or {}).get("message") if isinstance(n, dict) else None
        if not isinstance(m, dict):
            continue
        role = ((m.get("author") or {}).get("role") or "")
        if role not in ("user", "assistant"):
            continue
        text = _chatgpt_parts(m.get("content"))
        if not text.strip():
            continue
        msgs.append({"role": role, "text": text, "ts": _when(m.get("create_time"))})
    cid = str(conv.get("conversation_id") or conv.get("id") or "")
    return {"id": cid, "title": _title(conv.get("title")), "created": _when(conv.get("create_time")),
            "updated": _when(conv.get("update_time")), "messages": msgs}


def _claude(conv: dict) -> Optional[dict]:
    rows = conv.get("chat_messages")
    if not isinstance(rows, list):
        return None
    msgs = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        role = {"human": "user", "assistant": "assistant"}.get(m.get("sender"))
        if role is None:
            continue
        text = m.get("text") if isinstance(m.get("text"), str) else ""
        if not text.strip() and isinstance(m.get("content"), list):
            text = "\n".join(p.get("text") for p in m["content"]
                             if isinstance(p, dict) and isinstance(p.get("text"), str))
        if not str(text).strip():
            continue
        msgs.append({"role": role, "text": text, "ts": _when(m.get("created_at"))})
    return {"id": str(conv.get("uuid") or ""), "title": _title(conv.get("name")),
            "created": _when(conv.get("created_at")), "updated": _when(conv.get("updated_at")),
            "messages": msgs}


def conversations(doc: list, source: str) -> List[dict]:
    """Each conversation of an export as {id, title, created, updated, messages, cut}, in
    the file's order. Conversations with no id or no messages are left out."""
    parse = {"chatgpt": _chatgpt, "claude": _claude}[source]
    out = []
    for conv in doc[:MAX_CONVERSATIONS]:
        if not isinstance(conv, dict):
            continue
        c = parse(conv)
        if not c or not c["id"] or not c["messages"]:
            continue
        cut = len(c["messages"]) > MAX_MESSAGES
        msgs = []
        for m in c["messages"][:MAX_MESSAGES]:
            text, long_ = _text(m["text"])
            if long_:
                text += "\n[cut: the rest of this message was longer than %d characters]" % MAX_CHARS
                cut = True
            msgs.append({"role": m["role"], "text": text, "ts": m["ts"]})
        c.update(messages=msgs, cut=cut)
        out.append(c)
    return out


# ── preview and stage ────────────────────────────────────────────────────────

def preview(data: bytes) -> dict:
    """Counts only. Nothing is written."""
    if not _ONE_AT_A_TIME.acquire(blocking=False):
        return {"ok": False, "error": BUSY}
    try:
        source, convs = _parsed(data)
    except ImportRefused as e:
        return {"ok": False, "error": str(e)}
    finally:
        _ONE_AT_A_TIME.release()
    stamps = [c["created"] or c["updated"] for c in convs if c["created"] or c["updated"]]
    return {"ok": True, "preview": True, "source": source, "service": SOURCES[source],
            "conversations": len(convs), "messages": sum(len(c["messages"]) for c in convs),
            "first": _day(min(stamps)) if stamps else "", "last": _day(max(stamps)) if stamps else "",
            "cut": sum(1 for c in convs if c["cut"])}


def stage(data: bytes) -> dict:
    """Every conversation of an export into import staging. Nothing becomes a conversation
    in Friday until the owner accepts it. Off the record nothing is staged; a key pasted
    into any message or title is withheld before anything is written."""
    from agent_friday.services import off_record
    if off_record.active():
        return {"ok": False, "error": "Off the record is on, so nothing is imported."}
    if not _ONE_AT_A_TIME.acquire(blocking=False):
        return {"ok": False, "error": BUSY}
    try:
        source, convs = _parsed(data)
    except ImportRefused as e:
        return {"ok": False, "error": str(e)}
    finally:
        _ONE_AT_A_TIME.release()
    from agent_friday.services.pasted_secrets import withhold
    for c in convs:
        c["title"] = _title(withhold(c["title"])[0])
        for m in c["messages"]:
            m["text"] = withhold(m["text"])[0]
    register()
    from agent_friday.services import import_staging
    rows = []
    for c in convs:
        when = c["created"] or c["updated"]
        rows.append({
            "key": "%s:%s" % (source, c["id"]),
            "label": "%s · %s%s · %d message%s" % (
                c["title"], SOURCES[source], (" · " + _day(when)) if when else "",
                len(c["messages"]), "" if len(c["messages"]) == 1 else "s"),
            "payload": {"service": source, "conversation_id": c["id"][:120], "title": c["title"],
                        "created": when, "cut": c["cut"]},
            # The transcript lives beside the item and is read only when the owner accepts it.
            "body": c["messages"],
            "observed_at": _day(when) or None,
            "sensitivity": "personal"})
    out = import_staging.stage(source, KIND, rows, source_ref="conversations.json")
    if not out.get("ok"):
        return {"ok": False, "error": "The conversations could not be staged for review."}
    counts = import_staging.counts(source)
    return {"ok": True, "source": source, "service": SOURCES[source], "staged": out["staged"],
            "already_decided": out["already_decided"],
            "waiting": counts["proposed"] + counts["deferred"]}


# ── accept ───────────────────────────────────────────────────────────────────

def _commit(payload: dict) -> dict:
    """An accepted conversation becomes a Friday conversation (services/import_staging calls
    this only on the owner's accept)."""
    import shutil
    from agent_friday.services import conversations as cv
    from agent_friday.services import off_record
    # One settings snapshot for the whole conversation: off the record cannot switch on halfway
    # and leave a conversation with some of its messages.
    snap = off_record._settings()
    if off_record.stops_storage(snap):
        raise RuntimeError("off the record")
    source = payload.get("service")
    if source not in SOURCES:
        raise ValueError("unknown service")
    msgs = payload.get("body") if isinstance(payload.get("body"), list) else []
    from agent_friday.services.pasted_secrets import withhold
    title = "%s: %s" % (SOURCES[source], _title(withhold(payload.get("title"))[0]))
    conv = cv.create(title=title[:120])
    try:
        conv["imported"] = {"source": source,
                            "conversation_id": str(payload.get("conversation_id") or "")[:120],
                            "created": payload.get("created") if isinstance(payload.get("created"), (int, float)) else None,
                            "cut": payload.get("cut") is True}
        cv.save(conv)
        # Another assistant's words are outside content to Friday: never her own turns, never
        # instructions (governance/outside_content keeps the flag on the conversation).
        cv.mark_outside_content(conv["id"], "imported AI history (%s)" % SOURCES[source])
        n = 0
        for m in msgs[:MAX_MESSAGES]:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                continue
            text, _ = _text(m.get("text"), MAX_CHARS + 200)
            msg = {"role": m["role"], "text": text, "imported_from": source}
            if isinstance(m.get("ts"), (int, float)):
                msg["ts"] = float(m["ts"])
            cv.append(conv["id"], msg, settings=snap)
            n += 1
    except Exception:
        # Nothing half-saved: the conversation goes, and the item keeps waiting.
        shutil.rmtree(cv._dir(conv["id"]), ignore_errors=True)
        raise
    return {"conversation_id": conv["id"], "messages": n}


def register() -> None:
    from agent_friday.services import import_staging
    import_staging.register_kind(KIND, _commit, editable=("title",))
