"""The privacy map: what Friday holds, where it lives, what has left this PC
and to whom, and how long each thing is kept, in plain words.

North star §26.22 (privacy dashboard) and first-run spec §9.4; burn-down BD-15
slice P1. Every line is read from the store or setting that decides it, the
same rule as the Locks page (services/locks.py): a fact Friday cannot read
says "I couldn't tell", never a guess, and what is not built yet says so.

What left the PC is regenerated from the egress log on every read, so a send
appears on the next refresh. The map shows counts, destinations, kinds of
field and times; it never shows a payload or the gate's reasons (those can
quote the text). The receipts for one destination are the log's own rows,
from `left_rows()`, which the page opens on request.

Channels whose sends do not reach the egress log yet are listed by name under
"not in this list yet" with where their own record lives, so the map never
reads as complete when it is not (BD-15 slice P4 closes them).

Reads only. Nothing here deletes or exports: each entry links to the control
that already does that, with its own confirmation. Nothing leaves the PC.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_log = logging.getLogger("friday.privacy_map")

#: Per-store measuring bound: entries visited, then "at least" is said.
MAX_ENTRIES = 20000
#: Per-store measuring time bound, seconds.
MAX_SECONDS = 1.0
#: All stores together: one request never walks the disk for longer than this.
MAX_TOTAL_SECONDS = 4.0
#: How long a measured size is reused before the disk is walked again.
SIZE_CACHE_SECONDS = 60.0
#: The most of a log read for one summary: its newest part. A record that is
#: never rotated (the egress log) is summarised from its end, never its start.
MAX_TAIL_BYTES = 64 * 1024 * 1024
#: A log line longer than this is skipped (it cannot be a real egress row).
MAX_LINE = 64 * 1024
#: Most egress rows read for the summary; past that the summary says so.
MAX_ROWS = 2_000_000
#: Most rows one receipts request returns.
MAX_RECEIPTS = 500

#: Plain names for destinations the egress log names by provider id.
DESTINATION_WORDS = {
    "anthropic": "Anthropic (Claude)", "openai": "OpenAI", "gemini": "Google Gemini",
    "google": "Google", "openrouter": "OpenRouter", "xai": "xAI (Grok)", "grok": "xAI (Grok)",
    "mistral": "Mistral", "groq": "Groq", "deepseek": "DeepSeek", "perplexity": "Perplexity",
    "together": "Together AI", "brave": "Brave Search", "firecrawl": "Firecrawl",
    "duckduckgo": "DuckDuckGo", "elevenlabs": "ElevenLabs", "huggingface": "Hugging Face",
    "ollama": "Ollama cloud models", "kie": "KIE", "google-gemini": "Google Gemini",
}

#: Sends that do not pass the egress gate yet, and where each keeps its own record.
NOT_RECORDED = (
    {"key": "mail", "what": "Email Friday sends for you",
     "record": "Each send is recorded with its recipients and subject in sent_mail.jsonl, and counted under Mail below."},
    {"key": "mcp", "what": "Calls to tool servers you added (MCP)",
     "record": "Not recorded as a send yet. Each call that changes something is an action with its own card."},
    {"key": "connectors", "what": "Calls to connected services' own APIs (for example Calendar or Tasks writes not made through Friday's tools)",
     "record": "Actions are recorded in Friday's action receipts (System > Friday's changes)."},
    {"key": "publishing", "what": "Posts to social and publishing accounts",
     "record": "Each post has an approval card and a publishing record."},
    {"key": "fetches", "what": "Web pages Friday opens to read",
     "record": ("The address, which Friday chose, goes to that website. When a cloud model reads the "
                "page, the page text goes to that model as part of the conversation, and that send is "
                "in this list under the model's name.")},
)

HONEST_LIMIT = (
    "This map lists what Friday itself keeps and sends, and counts everything "
    "else in Friday's folder under one line. It cannot see copies other programs "
    "make, what a website or service does with what it receives, or files you "
    "saved elsewhere yourself.")


def _home() -> Path:
    from agent_friday.paths import friday_home
    return friday_home()


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _days_words(days: Any, *, unset: str = "Kept until you delete it.") -> str:
    try:
        d = float(days or 0)
    except (TypeError, ValueError):
        return "I couldn't tell how long it is kept."
    if d <= 0:
        return unset
    d = int(d)
    return f"Kept {d} day{'s' if d != 1 else ''}, then deleted."


# ── retention, read from each store's own setting ─────────────────────────────

def _ret_tasks() -> str:
    try:
        from agent_friday.services import task_journal
        return _days_words(task_journal.settings().get("retention_days"))
    except Exception:
        return "I couldn't tell how long it is kept."


def _ret_traces() -> str:
    try:
        from agent_friday.services import reasoning_trace
        return _days_words(reasoning_trace.settings().get("retention_days"))
    except Exception:
        return "I couldn't tell how long it is kept."


def _ret_tool_output() -> str:
    try:
        from agent_friday.services import tool_output
        return _days_words(tool_output.retention_days())
    except Exception:
        return "I couldn't tell how long it is kept."


def _ret_meetings() -> str:
    try:
        from agent_friday.services import meeting_policy as mp
        audio = mp.audio_retention()
        mode, days = mp.text_retention()
        a = {"": "Recordings: you haven't chosen yet."}.get(audio, f"Recordings: {audio.replace('_', ' ')}.")
        if mode == "days":
            t = f"Transcripts: kept {days} days."
        elif mode == "keep":
            t = "Transcripts: kept until you delete them."
        else:
            t = "Transcripts: you haven't chosen yet."
        return a + " " + t
    except Exception:
        return "I couldn't tell how long it is kept."


def _ret_setting(key: str, default: Any = 0) -> str:
    return _days_words(_settings().get(key, default))


def _ret_laya() -> str:
    try:
        from agent_friday.services import laya_personal as lp
        return _days_words(_settings().get(lp.SETTING_RETENTION, lp.DEFAULT_RETENTION_DAYS))
    except Exception:
        return "I couldn't tell how long it is kept."


def _ret_rotates(mb: int) -> str:
    return f"Kept until it reaches about {mb} MB; then the oldest part is set aside."


_FOREVER = "Kept until you delete it."


# ── encryption, read from the store that decides it ───────────────────────────

def _vault_on() -> Optional[bool]:
    """The live lock, read the way Privacy & Data > Vault reads it: derive
    the key first (it is derived lazily), then read the state it set."""
    try:
        from agent_friday.services import agent as _agent
        _agent._get_vault_key()
    except Exception:
        pass
    try:
        from agent_friday import core
        return bool((getattr(core, "_VAULT_ENCRYPTION_STATE", {}) or {}).get("enabled"))
    except Exception:
        return None


def _enc_vault() -> tuple:
    on = _vault_on()
    if on is None:
        return "unknown", "I couldn't tell whether it is encrypted."
    if on:
        return "yes", "Encrypted with your passphrase (AES-256-GCM)."
    return "no", "Readable on this disk: no passphrase is set (Privacy & Data > Vault)."


def _enc_wiki() -> tuple:
    try:
        from agent_friday.services.wiki_engine import _wiki_encrypted_sections
        locked = sorted(_wiki_encrypted_sections())
    except Exception:
        return "unknown", "I couldn't tell which parts of the wiki are encrypted."
    if locked and _vault_on():
        return "partly", ("Readable on this disk, except the sections you chose to lock ("
                          + ", ".join(locked[:6]) + "), which are encrypted with your passphrase.")
    return "no", "Readable on this disk (no wiki section is locked)."


def _enc_keys() -> tuple:
    try:
        from agent_friday.services import keystore
        wrap = keystore.describe().get("wrap")
    except Exception:
        return "unknown", "I couldn't tell how the keys are locked."
    if wrap == "passphrase":
        return "yes", "Encrypted, and the key that opens them is locked with your passphrase."
    if wrap == "dpapi":
        return "yes", "Encrypted, and the key that opens them is locked with Windows' protection for your account."
    if wrap is None:
        return "unknown", "Friday's key store doesn't exist yet; nothing has been stored through it."
    return "partly", ("Encrypted, but the key that opens them is kept in Friday's folder without a "
                      "passphrase, so anyone who can read that folder can open them. Never put in an export.")


def _enc_tasks() -> tuple:
    try:
        from agent_friday.services import task_journal
        on = bool(task_journal.settings().get("encrypt_at_rest", True))
    except Exception:
        return "unknown", "I couldn't tell whether it is encrypted."
    if not on:
        return "no", "Readable on this disk: encryption for task records is switched off."
    if _vault_on():
        return "yes", "Encrypted on disk with your passphrase's key."
    return "yes", ("Encrypted on disk with Windows' protection for your account (anything running "
                   "as you on this PC can open it); with a passphrase set, with its key.")


def _enc(state: str, words: str):
    return lambda: (state, words)


_PLAIN = _enc("no", "Readable on this disk (not encrypted yet).")


# ── the stores ────────────────────────────────────────────────────────────────

_PRIVACY_TAB = {"workspace": "settings", "tab": "privacy"}


def _ctl(kind: str, label: str, words: str, *, nav: Optional[dict] = None,
         href: Optional[str] = None) -> dict:
    out = {"kind": kind, "label": label, "words": words}
    if nav:
        out["nav"] = nav
    if href:
        out["href"] = href
    return out


_EXPORT_ALL = _ctl("export", "Export all my data", "A zip of everything except keys and sign-ins.",
                   href="/api/data/export")
_ERASE_ALL = _ctl("delete", "Erase everything", "Deletes all of Friday's data on this PC; you type ERASE to confirm.",
                  nav=dict(_PRIVACY_TAB, section="Your data"))


def _stores() -> List[dict]:
    """The registry. `paths` are relative to Friday's folder."""
    return [
        {"key": "conversations", "name": "Your conversations",
         "holds": "Every chat with Friday, and the search index over them.",
         "paths": ["conversations", "chat_history.json"], "enc": _PLAIN, "ret": lambda: _FOREVER,
         "controls": [_ctl("delete", "Delete a message", "Open the conversation and delete the message; "
                           "a whole conversation can't be deleted on its own yet."), _EXPORT_ALL, _ERASE_ALL]},
        {"key": "memory_card", "name": "What Friday remembers about you",
         "holds": "The short notes Friday reads before every reply (your memory card).",
         "paths": ["core_memory"], "enc": _PLAIN, "ret": lambda: _FOREVER,
         "controls": [_ctl("delete", "Edit or delete a line", "Each line can be edited, deleted and undone.",
                           nav=dict(_PRIVACY_TAB, section="Memory card")), _EXPORT_ALL]},
        {"key": "memory_search", "name": "Searchable memory",
         "holds": "Summaries and search vectors of past conversations, and what Friday learned about your habits.",
         "paths": ["memory", "user_model.db"], "enc": _PLAIN, "ret": lambda: _FOREVER,
         "controls": [_EXPORT_ALL, _ERASE_ALL]},
        {"key": "knowledge", "name": "Knowledge graph",
         "holds": "People, places and things Friday connected from your files and notes.",
         "paths": ["knowledge-graph"],
         "enc": _enc("partly", "Private-tier records are encrypted; the rest is readable on this disk."),
         "ret": lambda: _FOREVER,
         "controls": [_ctl("delete", "Forget a person", "Contacts lets you forget one person everywhere.",
                           nav={"workspace": "contacts"}), _EXPORT_ALL, _ERASE_ALL]},
        {"key": "library", "name": "Your library",
         "holds": "The documents you added and the index Friday searches them with.",
         "paths": ["library"],
         "enc": _enc("partly", "Documents on the vault shelf are sealed with your vault key. The search "
                               "index is encrypted only when this PC has the encrypted database engine and "
                               "its key; I don't open the library to check from here. Other copies are readable."),
         "ret": lambda: _FOREVER,
         "controls": [_ctl("delete", "Remove or forget a document", "Select a document in the Library to remove it.",
                           nav={"workspace": "library"}), _EXPORT_ALL]},
        {"key": "vault", "name": "Private records",
         "holds": "Finance, health, legal and family records.",
         "paths": ["finance", "health", "vault/legal", "vault/finances", "vault/family"],
         "enc": _enc_vault, "ret": lambda: _FOREVER,
         "controls": [_ctl("settings", "Vault settings", "Set or change the passphrase that locks it.",
                           nav=dict(_PRIVACY_TAB, section="Vault")), _EXPORT_ALL, _ERASE_ALL]},
        {"key": "wiki", "name": "Your wiki",
         "holds": "Notes Friday keeps for you and with you, by section.",
         "paths": ["wiki"], "enc": _enc_wiki, "ret": lambda: _FOREVER,
         "controls": [_ctl("settings", "Wiki sections", "Choose which sections stay off the cloud.",
                           nav=dict(_PRIVACY_TAB, section="Wiki sections kept off the cloud")),
                      _EXPORT_ALL, _ERASE_ALL]},
        {"key": "meetings", "name": "Meetings",
         "holds": "Recordings and transcripts of meetings you asked Friday to capture.",
         "paths": ["meetings"],
         "enc": _enc("partly", "Archived meetings are encrypted; recent working files are readable."),
         "ret": _ret_meetings,
         "controls": [_ctl("settings", "Meeting retention", "Choose how long recordings and transcripts are kept.",
                           nav={"workspace": "meetings"}), _EXPORT_ALL]},
        {"key": "tasks", "name": "Task records",
         "holds": "What each background task did, step by step, including prompts and tool results.",
         "paths": ["tasks"], "enc": _enc_tasks, "ret": _ret_tasks,
         "controls": [_ctl("delete", "Task records", "Delete a task's record, or choose how long they are kept.",
                           nav={"workspace": "system"}), _EXPORT_ALL]},
        {"key": "goals", "name": "Goals",
         "holds": "Goals you set, their steps and the signed record of what was done.",
         "paths": ["goals"], "enc": _PLAIN, "ret": lambda: _FOREVER,
         "controls": [_EXPORT_ALL, _ERASE_ALL]},
        {"key": "traces", "name": "Reasoning traces",
         "holds": "How Friday reasoned through each request, for you to inspect.",
         "paths": ["traces"], "enc": _PLAIN, "ret": _ret_traces,
         "controls": [_ctl("export", "Export traces", "A file of the recorded traces.",
                           href="/api/traces/export"), _EXPORT_ALL]},
        {"key": "tool_output", "name": "Long tool results",
         "holds": "Full results too long to keep in a conversation.",
         "paths": ["tool-output"], "enc": _PLAIN, "ret": _ret_tool_output,
         "controls": [_EXPORT_ALL, _ERASE_ALL]},
        {"key": "context_log", "name": "Conversation log",
         "holds": "A day-by-day log of what was said, kept for context.",
         "paths": ["vault/context-log"], "enc": _PLAIN, "ret": lambda: _ret_setting("context_retention_days"),
         "controls": [_ctl("export", "Export conversation log", "A zip of the daily log.",
                           href="/api/context/export"),
                      _ctl("settings", "How long it is kept", "Choose 30 days to a year, or keep it.",
                           nav=dict(_PRIVACY_TAB, section="Conversation logging"))]},
        {"key": "laya", "name": "What Laya learns about you",
         "holds": "Your own phrasings, used to route requests on this PC.",
         "paths": ["runtime/laya2/personal"],
         "enc": _enc("yes", "Sealed with your vault key; with no vault key nothing is kept."),
         "ret": _ret_laya,
         "controls": [_ctl("delete", "Forget or wipe", "Forget one phrasing or wipe what Laya learned.",
                           nav={"workspace": "settings", "tab": "laya"})]},
        {"key": "mail_cache", "name": "Mail and calendar copies",
         "holds": "Copies of recent mail and calendar events Friday fetched so it can answer quickly.",
         "paths": ["gmail", "gmail-cache.json", "calendar", "messages"], "enc": _PLAIN,
         "ret": lambda: "Refreshed from your accounts; kept until you delete it.",
         "controls": [_EXPORT_ALL, _ERASE_ALL]},
        {"key": "receipts", "name": "Receipts and audit logs",
         "holds": "Signed records of approvals, actions, sends and what left this PC.",
         "paths": ["actions", "decision-bom.jsonl", "governance", "sent_mail.jsonl", "mail_outbox",
                   "activity_ledger.jsonl", "vault/egress-log.jsonl", "vault/access-log.jsonl",
                   "vault/retrieval-log.jsonl", "vault/privilege-log.jsonl"],
         "enc": _enc("no", "Readable on this disk; each is signed or chained so a change shows."),
         "ret": lambda: "Kept until you delete them, so the record stays whole. The activity record rotates at about 20 MB.",
         "controls": [_ctl("settings", "Friday's changes", "Every action with its receipt and undo.",
                           nav={"workspace": "system"}), _EXPORT_ALL]},
        {"key": "credentials", "name": "Keys and sign-ins",
         "holds": "Provider keys and account sign-ins Friday uses for you.",
         "paths": ["security", "providers", "google_accounts", "mcp_oauth", "platforms",
                   "credentials.json"],
         "enc": _enc_keys,
         "ret": lambda: "Kept until you disconnect the account or remove the key.",
         "controls": [_ctl("delete", "Disconnect", "Remove a key or sign out of an account.",
                           nav={"workspace": "settings", "tab": "connections"}), _ERASE_ALL]},
        {"key": "logs", "name": "Friday's logs",
         "holds": "Technical logs used to find problems; they can contain parts of what you asked.",
         "paths": ["friday.log", "server_stderr.log", "voice_debug.log", "logs"],
         "enc": _PLAIN, "ret": lambda: _FOREVER, "controls": [_ERASE_ALL]},
        {"key": "settings", "name": "Settings",
         "holds": "Your choices for how Friday works.",
         "paths": ["settings.json"], "enc": _PLAIN, "ret": lambda: _FOREVER,
         "controls": [_EXPORT_ALL]},
    ]


_REPARSE = 0x400   # FILE_ATTRIBUTE_REPARSE_POINT: a junction or a link on Windows


def _is_link(entry_or_path) -> bool:
    """A symlink, or (on Windows) a junction or other reparse point: never
    followed, so a measure can't walk out of Friday's folder."""
    try:
        if isinstance(entry_or_path, os.DirEntry):
            if entry_or_path.is_symlink():
                return True
            st = entry_or_path.stat(follow_symlinks=False)
        else:
            if entry_or_path.is_symlink():
                return True
            st = os.lstat(entry_or_path)
        return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)
    except OSError:
        return True


def _measure(root: Path, rel_paths: Iterable[str], deadline: float) -> dict:
    """Bytes and files under each path, bounded in entries and time. Never
    follows a link or junction out of Friday's folder."""
    total, files, complete, present = 0, 0, True, False
    deadline = min(deadline, time.monotonic() + MAX_SECONDS)
    seen = 0
    for rel in rel_paths:
        p = root / rel
        try:
            if not os.path.lexists(p) or _is_link(p):
                continue
            if p.is_file():
                present = True
                total += os.lstat(p).st_size
                files += 1
                continue
            if not p.is_dir():
                continue
            present = True
            stack = [p]
            while stack and complete:
                d = stack.pop()
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            seen += 1
                            if seen > MAX_ENTRIES or time.monotonic() > deadline:
                                complete = False
                                break
                            try:
                                if _is_link(e):
                                    continue
                                if e.is_dir(follow_symlinks=False):
                                    stack.append(Path(e.path))
                                elif e.is_file(follow_symlinks=False):
                                    total += e.stat(follow_symlinks=False).st_size
                                    files += 1
                            except OSError:
                                continue
                except OSError:
                    continue
        except OSError:
            continue
        if not complete:
            break
    return {"bytes": total, "files": files, "complete": complete, "present": present}


_SIZE_CACHE: Dict[tuple, tuple] = {}


def _measured(root: Path, key: str, rel_paths: List[str], deadline: float) -> dict:
    ck = (str(root), key, tuple(rel_paths))
    hit = _SIZE_CACHE.get(ck)
    if hit and time.monotonic() - hit[0] < SIZE_CACHE_SECONDS:
        return hit[1]
    if time.monotonic() > deadline:
        return {"bytes": 0, "files": 0, "complete": False, "present": True, "skipped": True}
    m = _measure(root, rel_paths, deadline)
    _SIZE_CACHE[ck] = (time.monotonic(), m)
    return m


def _size_words(m: dict) -> str:
    if m.get("skipped"):
        return "Not measured this time (the page stops measuring after a few seconds)."
    if not m["present"]:
        return "Nothing stored yet."
    b = m["bytes"]
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if b >= div:
            size = f"{b / div:.1f} {unit}"
            break
    else:
        size = f"{b} bytes"
    words = f"{size} in {m['files']} file{'s' if m['files'] != 1 else ''}"
    return ("At least " + words + ".") if not m["complete"] else (words + ".")


def _everything_else(home: Path, listed: List[str]) -> dict:
    """Top-level entries of Friday's folder that no line above covers."""
    covered = {Path(p).parts[0].lower() for p in listed if Path(p).parts}
    rest = []
    try:
        for e in sorted(os.scandir(home), key=lambda e: e.name.lower()):
            if e.name.lower() not in covered:
                rest.append(e.name)
    except OSError:
        pass
    return {"key": "everything_else", "name": "Everything else in Friday's folder",
            "holds": ("Other things Friday keeps that have no line of their own yet"
                      + (": " + ", ".join(rest[:12]) + ("…" if len(rest) > 12 else "") if rest else "")
                      + ". Some of them can hold parts of what you asked or did."),
            "paths": rest[:200], "enc": _enc("unknown", "I haven't checked these one by one; treat them as readable."),
            "ret": lambda: _FOREVER, "controls": [_EXPORT_ALL, _ERASE_ALL]}


def stores() -> List[dict]:
    home = _home()
    out = []
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    registry = _stores()
    registry.append(_everything_else(home, [p for s in registry for p in s["paths"]]))
    for s in registry:
        try:
            state, enc_words = s["enc"]()
        except Exception:
            state, enc_words = "unknown", "I couldn't tell whether it is encrypted."
        try:
            ret = s["ret"]()
        except Exception:
            ret = "I couldn't tell how long it is kept."
        m = _measured(home, s["key"], list(s["paths"]), deadline)
        out.append({"key": s["key"], "name": s["name"], "holds": s["holds"],
                    "where": [str(Path(p)) for p in s["paths"]] or ["(nothing else)"],
                    "encrypted": state, "encrypted_words": enc_words,
                    "retention_words": ret, "size_words": _size_words(m),
                    "bytes": m["bytes"], "files": m["files"], "present": m["present"],
                    "controls": s["controls"]})
    return out


# ── what left this PC ─────────────────────────────────────────────────────────

def _egress_log() -> Path:
    from agent_friday.services import egress_gate
    return Path(egress_gate._DEFAULT_LOG)


def _rows(path: Path, *, tail: bool = False, info: Optional[dict] = None) -> Iterable[dict]:
    """Well-formed rows of a JSONL log, oldest first. With `tail`, only the
    newest MAX_TAIL_BYTES are read (the start of a long log is what is left
    out, never its end); `info["complete"]` says whether the whole file was
    read. Lines over MAX_LINE are skipped without being held in memory."""
    if info is not None:
        info["complete"] = True
    if not path.exists():
        return
    with open(path, "rb") as fh:
        if tail:
            size = os.fstat(fh.fileno()).st_size
            if size > MAX_TAIL_BYTES:
                fh.seek(size - MAX_TAIL_BYTES)
                fh.readline(MAX_LINE + 1)          # drop the partial first line
                if info is not None:
                    info["complete"] = False
        n = 0
        while True:
            raw = fh.readline(MAX_LINE + 1)
            if not raw:
                return
            if len(raw) > MAX_LINE:
                while raw and not raw.endswith(b"\n"):
                    raw = fh.readline(MAX_LINE + 1)  # skip the rest of an oversized line
                continue
            n += 1
            if n > MAX_ROWS:
                if info is not None:
                    info["complete"] = False
                return
            try:
                row = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _ts(row: dict) -> Optional[float]:
    v = row.get("ts", row.get("at", row.get("timestamp")))
    if isinstance(v, (int, float)):
        return float(v)
    return None


def destination_words(provider: str) -> str:
    p = str(provider or "").strip()
    return DESTINATION_WORDS.get(p.lower(), p or "an unnamed destination")


def left() -> dict:
    """Every destination that has received something from Friday, from the
    egress log, as counts. Regenerated on every call."""
    path = _egress_log()
    by: Dict[str, dict] = {}
    total = 0
    info: Dict[str, Any] = {}
    try:
        for row in _rows(path, tail=True, info=info):
            total += 1
            provider = str(row.get("provider") or "")[:80]
            d = by.setdefault(provider, {"to": provider, "label": destination_words(provider),
                                         "sent": 0, "sent_with_parts_withheld": 0, "stopped": 0,
                                         "files_or_images": 0, "background": 0, "off_record": 0,
                                         "unrestricted_calls": 0,
                                         "first": None, "last": None, "fields": {}})
            action = row.get("action")
            if row.get("field") == "*":
                # One summary row per call under "Cloud for everything" (A14):
                # a call, not an item of text, so it is counted on its own.
                d["unrestricted_calls"] += 1
            elif action == "allow":
                d["sent"] += 1
            elif action == "redact":
                d["sent"] += 1
                d["sent_with_parts_withheld"] += 1
            else:
                d["stopped"] += 1
            if row.get("tier") == "UNCLASSIFIABLE":
                d["files_or_images"] += 1
            if not row.get("turn_id") and row.get("tier") != "UNCLASSIFIABLE":
                d["background"] += 1
            if row.get("reason") == "off the record":
                d["off_record"] += 1
            ts = _ts(row)
            if ts is not None:
                d["first"] = ts if d["first"] is None else min(d["first"], ts)
                d["last"] = ts if d["last"] is None else max(d["last"], ts)
            field = str(row.get("field") or "")[:60]
            if field and (field in d["fields"] or len(d["fields"]) < 8):
                d["fields"][field] = d["fields"].get(field, 0) + 1
        readable = True
    except OSError:
        readable = False
    dests = []
    for d in sorted(by.values(), key=lambda x: -(x["last"] or 0)):
        d["fields"] = sorted(d["fields"], key=lambda f: -d["fields"][f])
        d["receipts"] = "/api/privacy/map/left?to=" + _quote(d["to"])
        dests.append(d)
    return {"destinations": dests, "rows": total, "readable": readable,
            "log": "vault/egress-log.jsonl",
            "complete": bool(info.get("complete", True)),
            "not_recorded": list(NOT_RECORDED)}


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def left_rows(to: str, limit: int = 200) -> dict:
    """The egress log's own rows for one destination, newest first: the
    receipts behind one line of the map."""
    limit = max(1, min(int(limit or 200), MAX_RECEIPTS))
    keep: deque = deque(maxlen=limit)
    for row in _rows(_egress_log(), tail=True):
        if str(row.get("provider") or "")[:80] == to:
            keep.append({k: row.get(k) for k in ("ts", "provider", "field", "tier", "action",
                                                 "reason", "turn_id", "bytes") if k in row})
    rows = list(reversed(keep))
    for r in rows:
        if r.get("turn_id"):
            r["turn_receipt"] = "/api/turns/" + _quote(str(r["turn_id"])) + "/receipt"
    return {"to": to, "label": destination_words(to), "rows": rows}


def mail() -> dict:
    """Mail Friday sent for you, by recipient, from its outbox record."""
    try:
        from agent_friday.core import FRIDAY_DIR
        path = Path(FRIDAY_DIR) / "sent_mail.jsonl"
    except Exception:
        path = _home() / "sent_mail.jsonl"
    by: Dict[str, dict] = {}
    sent = failed = 0
    for row in _rows(path):
        if not row.get("ok"):
            failed += 1
            continue
        sent += 1
        to = row.get("to")
        cc = row.get("cc")
        people: List[str] = []
        for v in (to, cc):
            if isinstance(v, str):
                people += [x.strip() for x in v.split(",") if x.strip()]
            elif isinstance(v, list):
                people += [str(x).strip() for x in v if str(x).strip()]
        for person in people[:50]:
            p = person[:200]
            r = by.setdefault(p, {"to": p, "count": 0, "last": None})
            r["count"] += 1
            ts = _ts(row)
            if ts is not None:
                r["last"] = ts if r["last"] is None else max(r["last"], ts)
    recips = sorted(by.values(), key=lambda r: -(r["last"] or 0))[:100]
    return {"sent": sent, "failed": failed, "recipients": recips,
            "record": "sent_mail.jsonl"}


# ── grants, services, devices, vault access, off the record ──────────────────

def grants() -> dict:
    out = {"actions": [], "files": None}
    try:
        from agent_friday.governance import action_gate
        for g in action_gate.list_grants():
            out["actions"].append({"tools": list(g.get("tools") or [])[:10],
                                   "for": str(g.get("scope") or "")[:120],
                                   "expires_at": g.get("expires_at"),
                                   "uses_left": g.get("uses_left")})
    except Exception:
        out["actions"] = None
    try:
        from agent_friday.services import file_grants
        out["files"] = len(file_grants.list_grants())
    except Exception:
        out["files"] = None
    out["nav"] = dict(_PRIVACY_TAB, section="File access")
    return out


#: Connection groups whose items reach Friday from outside this PC.
_REMOTE_GROUPS = ("phone", "messaging", "network")


def services() -> dict:
    """Connected services and what each may do; phones and remote channels."""
    try:
        from agent_friday.services import setup_connections
        items = setup_connections.checklist().get("items") or []
    except Exception:
        return {"readable": False, "connected": [], "remote": []}
    connected, remote = [], []
    for it in items:
        if it.get("status") != "connected":
            continue
        row = {"label": str(it.get("label") or "")[:120], "group": it.get("group"),
               "may": [str(p.get("text") or "")[:200] for p in (it.get("permissions") or [])][:8]}
        (remote if it.get("group") in _REMOTE_GROUPS else connected).append(row)
    return {"readable": True, "connected": connected, "remote": remote,
            "nav": {"workspace": "settings", "tab": "connections"}}


#: Tiers that mean private data (privacy/vault_access writes the tier name).
_PRIVATE_TIERS = ("TIER_2", "TIER_3", "TIER_4")


def vault_access(now: Optional[float] = None) -> dict:
    """How often, in the last 7 days, something asked to use private data and
    what the checks decided: allowed and refused, from the access log and
    its one rotated file."""
    now = now or time.time()
    base = _home() / "vault"
    allowed = refused = 0
    last = None
    for path in (base / "access-log.1.jsonl", base / "access-log.jsonl"):
        for row in _rows(path, tail=True):
            ts = _ts(row)
            if ts is None or ts < now - 7 * 86400:
                continue
            if str(row.get("tier") or "") not in _PRIVATE_TIERS:
                continue
            if row.get("allowed"):
                allowed += 1
                last = ts if last is None else max(last, ts)
            else:
                refused += 1
    return {"allowed_7_days": allowed, "refused_7_days": refused, "last_allowed": last,
            "record": "vault/access-log.jsonl"}


OFF_RECORD_WORDS = (
    "Off the record, Friday saves nothing about the conversation itself. It still "
    "keeps the minimum receipt of each outward action and each send to the cloud, "
    "and services you connected (mail, "
    "calendar, publishing) can still keep their own records of anything they are "
    "asked to do.")


def off_record() -> dict:
    try:
        from agent_friday.services import off_record as off
        on = bool(off.active())
    except Exception:
        on = None
    return {"on": on, "words": OFF_RECORD_WORDS}


# ── the whole map ─────────────────────────────────────────────────────────────

def view(now: Optional[float] = None) -> dict:
    now = now or time.time()

    def safe(fn, fallback):
        try:
            return fn()
        except Exception as e:
            _log.warning("privacy map: %s could not be read: %s", getattr(fn, "__name__", "?"), e)
            return fallback

    return {
        "generated_at": now,
        "stores": safe(stores, []),
        "left": safe(left, {"destinations": [], "rows": 0, "readable": False,
                            "not_recorded": list(NOT_RECORDED)}),
        "mail": safe(mail, {"sent": 0, "failed": 0, "recipients": []}),
        "grants": safe(grants, {"actions": None, "files": None}),
        "services": safe(services, {"readable": False, "connected": [], "remote": []}),
        "vault_access": safe(lambda: vault_access(now), {"allowed_7_days": None, "refused_7_days": None}),
        "off_record": safe(off_record, {"on": None, "words": OFF_RECORD_WORDS}),
        "export": _EXPORT_ALL, "erase": _ERASE_ALL,
        "honest_limit": HONEST_LIMIT,
    }
