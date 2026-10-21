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
MAX_SECONDS = 1.5
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
    "ollama": "Ollama cloud models", "kie": "KIE",
}

#: Sends that do not pass the egress gate yet, and where each keeps its own record.
NOT_RECORDED = (
    {"key": "mail", "what": "Email Friday sends for you",
     "record": "Each send is recorded in sent_mail.jsonl and shown under Mail below."},
    {"key": "mcp", "what": "Calls to tool servers you added (MCP)",
     "record": "Not recorded as a send yet. Each call that changes something is an action with its own card."},
    {"key": "connectors", "what": "Calls to connected services' own APIs (for example Calendar or Tasks writes not made through Friday's tools)",
     "record": "Actions are recorded in Friday's action receipts (System > Friday's changes)."},
    {"key": "publishing", "what": "Posts to social and publishing accounts",
     "record": "Each post has an approval card and a publishing record."},
    {"key": "fetches", "what": "Web pages Friday opens to read",
     "record": "The address goes to that website; the page text is not sent anywhere else."},
)

HONEST_LIMIT = (
    "This map lists what Friday itself keeps and sends. It cannot see copies "
    "other programs make, what a website or service does with what it receives, "
    "or files you saved elsewhere yourself.")


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

def _enc_vault() -> tuple:
    try:
        from agent_friday import core
        on = bool((getattr(core, "_VAULT_ENCRYPTION_STATE", {}) or {}).get("enabled"))
    except Exception:
        return "unknown", "I couldn't tell whether it is encrypted."
    if on:
        return "yes", "Encrypted with your passphrase (AES-256-GCM)."
    return "no", "Readable on this disk: no passphrase is set (Privacy & Data > Vault)."


def _enc_tasks() -> tuple:
    try:
        from agent_friday.services import task_journal
        on = bool(task_journal.settings().get("encrypt_at_rest", True))
    except Exception:
        return "unknown", "I couldn't tell whether it is encrypted."
    if on:
        return "yes", "Encrypted on disk with Windows' protection, or your passphrase when one is set."
    return "no", "Readable on this disk: encryption for task records is switched off."


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
         "enc": _enc("partly", "Its index is encrypted when its key is on this PC; added copies are readable."),
         "ret": lambda: _FOREVER,
         "controls": [_ctl("delete", "Remove or forget a document", "Select a document in the Library to remove it.",
                           nav={"workspace": "library"}), _EXPORT_ALL]},
        {"key": "vault", "name": "Private records and notes",
         "holds": "Finance, health, legal and family records, and your wiki.",
         "paths": ["vault", "wiki"], "enc": _enc_vault, "ret": lambda: _FOREVER,
         "controls": [_ctl("settings", "Vault settings", "Set or change the passphrase that locks it.",
                           nav=dict(_PRIVACY_TAB, section="Vault")), _EXPORT_ALL, _ERASE_ALL]},
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
         "paths": ["actions", "decision-bom.jsonl", "governance", "sent_mail.jsonl",
                   "activity_ledger.jsonl", "vault/egress-log.jsonl"],
         "enc": _enc("no", "Readable on this disk; each is signed or chained so a change shows."),
         "ret": lambda: "Kept until you delete them, so the record stays whole. The activity record rotates at about 20 MB.",
         "controls": [_ctl("settings", "Friday's changes", "Every action with its receipt and undo.",
                           nav={"workspace": "system"}), _EXPORT_ALL]},
        {"key": "credentials", "name": "Keys and sign-ins",
         "holds": "Provider keys and account sign-ins Friday uses for you.",
         "paths": ["security", "providers", "google_accounts", "mcp_oauth", "platforms"],
         "enc": _enc("yes", "Encrypted with Windows' protection or Friday's key store; never put in an export."),
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


def _measure(root: Path, rel_paths: Iterable[str]) -> dict:
    """Bytes and files under each path, bounded in entries and time. Never
    follows a link out of Friday's folder."""
    total, files, complete, present = 0, 0, True, False
    deadline = time.monotonic() + MAX_SECONDS
    seen = 0
    for rel in rel_paths:
        p = root / rel
        try:
            if p.is_symlink():
                continue
            if p.is_file():
                present = True
                total += p.stat().st_size
                files += 1
                continue
            if not p.is_dir():
                continue
            present = True
            stack = [p]
            while stack:
                d = stack.pop()
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            seen += 1
                            if seen > MAX_ENTRIES or time.monotonic() > deadline:
                                complete = False
                                break
                            try:
                                if e.is_symlink():
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
                if not complete:
                    break
        except OSError:
            continue
        if not complete:
            break
    return {"bytes": total, "files": files, "complete": complete, "present": present}


def _size_words(m: dict) -> str:
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


def stores() -> List[dict]:
    home = _home()
    out = []
    for s in _stores():
        try:
            state, enc_words = s["enc"]()
        except Exception:
            state, enc_words = "unknown", "I couldn't tell whether it is encrypted."
        try:
            ret = s["ret"]()
        except Exception:
            ret = "I couldn't tell how long it is kept."
        m = _measure(home, s["paths"])
        out.append({"key": s["key"], "name": s["name"], "holds": s["holds"],
                    "where": [str(Path(p)) for p in s["paths"]],
                    "encrypted": state, "encrypted_words": enc_words,
                    "retention_words": ret, "size_words": _size_words(m),
                    "bytes": m["bytes"], "files": m["files"], "present": m["present"],
                    "controls": s["controls"]})
    return out


# ── what left this PC ─────────────────────────────────────────────────────────

def _egress_log() -> Path:
    from agent_friday.services import egress_gate
    return Path(egress_gate._DEFAULT_LOG)


def _rows(path: Path) -> Iterable[dict]:
    """Every well-formed row of a JSONL log, oldest first, bounded."""
    if not path.exists():
        return
    n = 0
    with open(path, "rb") as fh:
        for raw in fh:
            n += 1
            if n > MAX_ROWS:
                return
            if len(raw) > MAX_LINE:
                continue
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
    try:
        for row in _rows(path):
            total += 1
            provider = str(row.get("provider") or "")[:80]
            d = by.setdefault(provider, {"to": provider, "label": destination_words(provider),
                                         "sent": 0, "sent_with_parts_withheld": 0, "stopped": 0,
                                         "files_or_images": 0, "background": 0, "off_record": 0,
                                         "first": None, "last": None, "fields": {}})
            action = row.get("action")
            if action == "allow":
                d["sent"] += 1
            elif action == "redact":
                d["sent"] += 1
                d["sent_with_parts_withheld"] += 1
            else:
                d["stopped"] += 1
            if row.get("tier") == "UNCLASSIFIABLE":
                d["files_or_images"] += 1
            if not row.get("turn_id"):
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
            "complete": total < MAX_ROWS,
            "not_recorded": list(NOT_RECORDED)}


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def left_rows(to: str, limit: int = 200) -> dict:
    """The egress log's own rows for one destination, newest first: the
    receipts behind one line of the map."""
    limit = max(1, min(int(limit or 200), MAX_RECEIPTS))
    keep: deque = deque(maxlen=limit)
    for row in _rows(_egress_log()):
        if str(row.get("provider") or "") == to:
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


def vault_access(now: Optional[float] = None) -> dict:
    now = now or time.time()
    path = _home() / "vault" / "access-log.jsonl"
    week = 0
    last = None
    for row in _rows(path):
        ts = _ts(row)
        if ts is None:
            continue
        last = ts if last is None else max(last, ts)
        if ts >= now - 7 * 86400:
            week += 1
    return {"last_7_days": week, "last": last, "record": "vault/access-log.jsonl"}


OFF_RECORD_WORDS = (
    "Off the record, Friday saves nothing about the conversation. Services you "
    "connected (mail, calendar, publishing) can still keep their own records of "
    "anything they are asked to do, and Friday keeps the minimum receipt for "
    "each outward action.")


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
        "folder": str(_home()),
        "stores": safe(stores, []),
        "left": safe(left, {"destinations": [], "rows": 0, "readable": False,
                            "not_recorded": list(NOT_RECORDED)}),
        "mail": safe(mail, {"sent": 0, "failed": 0, "recipients": []}),
        "grants": safe(grants, {"actions": None, "files": None}),
        "services": safe(services, {"readable": False, "connected": [], "remote": []}),
        "vault_access": safe(lambda: vault_access(now), {"last_7_days": None, "last": None}),
        "off_record": safe(off_record, {"on": None, "words": OFF_RECORD_WORDS}),
        "export": _EXPORT_ALL, "erase": _ERASE_ALL,
        "honest_limit": HONEST_LIMIT,
    }
