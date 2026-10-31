"""The human-readable export (north star §36.6-2, §6.10-2, §27.3-2; BD-17).

What Friday keeps, written out as plain files the owner can open with any
text editor or spreadsheet, without Friday and without FutureSpeak:

  README.md                  what is here, what was left out, and why
  wiki/<page>.md             every wiki page, decrypted where the vault is open
  memories/about-you.md      core memory (what Friday knows about the owner)
  memories/friday-lessons.md core memory (Friday's own lessons)
  memories/profile.md        the learned profile: traits and facts
  conversations/<date> <title>.md   every saved conversation, message by message
  goals/goals.md             every goal with its steps and status
  receipts/receipts.md       the action ledger, one plain line per entry
  receipts/receipts.jsonl    the same entries, signed lines as stored
  receipts/verification.md   whether the chain verifies, and any break
  contacts/contacts.md, contacts.csv   the people Friday knows
  settings/settings.json     settings, with every secret value blanked
  manifest.json              each file with its SHA-256

It reads Friday's stores; the one thing it adds to Friday's data folder is a
receipt in the action ledger saying an export was made, and where. Reading
the ledger can finish importing legacy receipt files, as every start does.
Only files inside the data folder are read: a link to anywhere else is left
out. Credential files are never read; every value under a credential-like
name and every key shape Friday recognises (services/pasted_secrets) is
blanked in every file written. Messages kept off the record are never on
disk, so never in it. Wiki pages in an encrypted section are written out in
plain text when the vault is open on this PC, and listed as left out when it
is not: the export is the owner's own copy, kept where they choose.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from agent_friday.user_errors import UserFacingRuntimeError

CATEGORIES = ("wiki", "memories", "conversations", "goals", "receipts", "contacts", "settings")

#: A key name that may hold a credential (anywhere in the name, any case).
_SECRET_NAME = re.compile(r"(?i)(secret|token|passw|passphrase|pwd|api.?key|apikey|private.?key|"
                          r"credential|auth_?(?:token|key|header)|authorization|bearer|cookie|"
                          r"session_?(?:id|key|token)|webhook|signing|_pat$|^pat$|_pass$|^pass$)")
#: Fields of a ledger entry that are its chain, not its content.
_CHAIN_FIELDS = frozenset({"v", "seq", "ts", "t", "kind", "principal", "pv", "kid", "prev", "hash",
                           "sig"})
#: Values that carry a credential whatever their name: a webhook, a token or
#: key in a query, a user and password in a URL.
_SECRET_VALUE = re.compile(r"(?i)(hooks\.slack\.com/|discord(?:app)?\.com/api/webhooks/|"
                           r"[?&](?:token|key|access_token|api_key|sig|signature)=|://[^/\s:@]+:[^/\s@]+@)")
BLANKED = "(blanked)"


class ExportError(UserFacingRuntimeError):
    """Said plainly to the owner."""


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


def _when(t) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(t)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _inside(p: Path, root: Path) -> bool:
    """p is a real file inside root once every link and junction is followed."""
    try:
        rp = p.resolve(strict=True)
        rr = root.resolve()
        rp.relative_to(rr)
        rp.relative_to(_home().resolve())
    except (OSError, ValueError, RuntimeError):
        return False
    return rp.is_file()


def _inside_dir(d: Path, root: Path) -> bool:
    """A folder to walk into: not a link or junction to somewhere else."""
    try:
        d.resolve(strict=True).relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError):
        return False
    return not d.is_symlink()


def _scrub_text(text: str, whole: bool = False) -> str:
    from agent_friday.services import pasted_secrets
    return pasted_secrets.withhold(text, whole=whole)[0]


def _blank_strings(obj):
    """Every string inside obj blanked; numbers, switches and shape kept."""
    if isinstance(obj, str):
        return BLANKED if obj else obj
    if isinstance(obj, dict):
        return {k: _blank_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_blank_strings(v) for v in obj]
    return obj


def _scrub_value(obj, key=None):
    """Blank what may be a credential in a parsed document."""
    if key is not None and isinstance(key, str) and _SECRET_NAME.search(key):
        return _blank_strings(obj)
    if isinstance(obj, dict):
        return {k: _scrub_value(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_value(v) for v in obj]
    if isinstance(obj, str):
        return BLANKED if _SECRET_VALUE.search(obj) else _scrub_text(obj)
    return obj


def _safe(name: str, limit: int = 80) -> str:
    s = re.sub(r"[^\w .,'()-]+", " ", str(name or "")).strip(" .")
    return (re.sub(r"\s+", " ", s)[:limit] or "untitled").strip()


class _Out:
    """The export folder: every file written through here is hashed."""

    def __init__(self, root: Path):
        self.root = root
        self.files: dict = {}

    def write(self, rel: str, text: str) -> None:
        # a name taken from a title or a page can carry a key too
        rel = _scrub_text(rel)
        if not rel.endswith((".json", ".jsonl")):
            # JSON is scrubbed value by value before it is written: a text
            # pass over it could cut a string and break the file
            text = _scrub_text(text)
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        with open(p, "wb") as f:
            f.write(data)
        self.files[rel] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


# ── each category ────────────────────────────────────────────────────────────

def _wiki(out: _Out, notes: list) -> int:
    from agent_friday import core
    from agent_friday.services import wiki_engine
    root = Path(core.WIKI_DIR)
    if not root.is_dir():
        return 0
    n = 0
    pages = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and _inside_dir(here / d, root)]
        pages += [here / f for f in filenames if f.endswith(".md") and not f.startswith(".")]
    for p in sorted(pages):
        rel = p.relative_to(root)
        if not _inside(p, root):
            notes.append("wiki/%s: left out, it links outside Friday's data folder" % rel.as_posix())
            continue
        try:
            text = wiki_engine.wiki_read_text(p)
        except Exception as e:  # noqa: BLE001 - one page never stops the export
            notes.append("wiki/%s: could not be read (%s)" % (rel.as_posix(), type(e).__name__))
            continue
        if text == wiki_engine.VAULT_LOCKED_PLACEHOLDER:
            notes.append("wiki/%s: left out, it is in an encrypted section and the vault "
                         "is not open on this PC" % rel.as_posix())
            continue
        out.write("wiki/" + rel.as_posix(), text)
        n += 1
    return n


def _memories(out: _Out, notes: list) -> int:
    n = 0
    from agent_friday.services import core_memory
    for target, name, title in (("user", "about-you.md", "What Friday knows about you"),
                                ("friday", "friday-lessons.md", "What Friday has learned about working with you")):
        try:
            p = Path(core_memory._path(target))
        except Exception:
            continue
        if p.exists() and not _inside(p, _home()):
            notes.append("memories/%s: left out, it links outside Friday's data folder" % name)
        elif p.is_file():
            out.write("memories/" + name, "# %s\n\n%s" % (title, p.read_text(encoding="utf-8", errors="replace")))
            n += 1
    from agent_friday.services import user_model
    db = Path(user_model.DB_PATH)
    if db.exists() and not _inside(db, _home()):
        notes.append("memories/profile.md: left out, the profile links outside Friday's data folder")
    elif db.is_file():
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
            try:
                traits = con.execute("SELECT key, value, confidence FROM traits ORDER BY key").fetchall()
                facts = con.execute("SELECT ts, category, text, confidence, source FROM facts "
                                    "ORDER BY category, ts").fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            notes.append("memories/profile.md: the profile could not be read (%s)" % type(e).__name__)
        else:
            lines = ["# Your profile, as Friday has learned it", "", "## Traits", ""]
            lines += ["- **%s**: %s (confidence %.2f)" % (k, v, float(c or 0)) for k, v, c in traits] or ["(none)"]
            lines += ["", "## Facts", ""]
            cat = None
            for ts, category, text, conf, source in facts:
                if category != cat:
                    lines += ["", "### %s" % (category or "other")]
                    cat = category
                lines.append("- %s (%s; %s; confidence %.2f)" % (text, _when(ts), source or "unknown",
                                                                float(conf or 0)))
            out.write("memories/profile.md", "\n".join(lines) + "\n")
            n += 1
    return n


def _conversations(out: _Out, notes: list) -> int:
    from agent_friday.services import conversations as cv
    root = Path(cv._root())
    if not root.is_dir():
        return 0
    index = ["# Conversations", ""]
    n = 0
    used: set = set()
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.is_symlink():
            continue
        if not _inside_dir(d, root):
            continue
        conv = {"id": d.name}
        if _inside(d / "conversation.json", root):
            try:
                conv = json.loads((d / "conversation.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        mp = d / "messages.jsonl"
        if mp.exists() and not _inside(mp, root):
            notes.append("conversations/%s: left out, it links outside Friday's data folder" % d.name)
            continue
        msgs = cv._stored_messages(d.name)
        if not msgs:
            continue
        title = conv.get("title") or "Untitled"
        title = _scrub_text(str(title), whole=True)
        name = "%s %s" % (_when(conv.get("created_at") or msgs[0].get("ts"))[:10] or "undated", _safe(title))
        base, k = name, 2
        while name.lower() in used:
            name, k = "%s (%d)" % (base, k), k + 1
        used.add(name.lower())
        lines = ["# %s" % title, ""]
        if conv.get("status") == "archived":
            lines += ["_Archived._", ""]
        for m in msgs:
            who = {"user": "You", "assistant": "Friday"}.get(m.get("role"), str(m.get("role") or "Note"))
            text = m.get("text") if isinstance(m.get("text"), str) else m.get("content")
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False) if text is not None else ""
            lines += ["**%s** · %s" % (who, _when(m.get("ts"))), "", _scrub_text(text.strip(), whole=True), ""]
        out.write("conversations/%s.md" % name, "\n".join(lines))
        index.append("- [%s](%s.md) (%d messages)" % (title, name.replace(" ", "%20"), len(msgs)))
        n += 1
    if n:
        out.write("conversations/index.md", "\n".join(index) + "\n")
    return n


def _goals(out: _Out, notes: list) -> int:
    from agent_friday.services import goals
    root = Path(goals.GOALS_DIR)
    if not root.is_dir():
        return 0
    items = []
    for p in sorted(root.glob("goal_*.json")):
        if not _inside(p, root):
            notes.append("goals/%s: left out, it links outside Friday's data folder" % p.name)
            continue
        try:
            g = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            notes.append("goals/%s: could not be read" % p.name)
            continue
        if isinstance(g, dict):
            items.append(_scrub_value(g))
    if not items:
        return 0
    lines = ["# Goals", ""]
    for g in items:
        lines += ["## %s" % (g.get("title") or g.get("goal") or g.get("id") or "Goal"), "",
                  "- Status: %s" % (g.get("status") or "unknown"),
                  "- Started: %s" % _when(g.get("created_at"))]
        if g.get("why") or g.get("description"):
            lines.append("- Why: %s" % (g.get("why") or g.get("description")))
        steps = g.get("steps") or g.get("plan") or []
        if isinstance(steps, list) and steps:
            lines += ["", "Steps:"]
            for s in steps:
                if isinstance(s, dict):
                    lines.append("- [%s] %s" % ("x" if s.get("status") in ("done", "complete", "completed") else " ",
                                                s.get("title") or s.get("text") or s.get("description") or ""))
                else:
                    lines.append("- %s" % s)
        lines.append("")
    out.write("goals/goals.md", "\n".join(lines))
    out.write("goals/goals.json", json.dumps(items, indent=1, ensure_ascii=False, default=str))
    return len(items)


def _public(e: dict) -> dict:
    """An entry as the Ledger shows it: no undo data, credentials blanked in
    every field but the chain's own (seq, hash, signature and the rest)."""
    out = {}
    for k, v in e.items():
        if k in _CHAIN_FIELDS:
            out[k] = v
        elif k == "rec" and isinstance(v, dict):
            out[k] = _scrub_value({rk: rv for rk, rv in v.items() if rk != "undo"})
        else:
            out[k] = _scrub_value(v, k)
    return out


def _receipts(out: _Out, notes: list) -> int:
    from agent_friday.services import action_ledger
    from agent_friday.routes.ledger import plain_line
    try:
        rows = action_ledger.entries(None, newest_first=False, archive=True)
    except Exception as e:  # noqa: BLE001
        notes.append("receipts: the archive could not be read (%s); live entries only" % type(e).__name__)
        rows = action_ledger.entries(None, newest_first=False)
    try:
        rep = action_ledger.verify(force=True, migrate=False)
    except Exception as e:  # noqa: BLE001
        rep = {"ok": False, "problems": [{"reason": "the check could not run (%s)" % type(e).__name__}]}
    rows = [_public(e) for e in rows]
    lines = ["# Everything Friday did or decided", "",
             "One line per entry, oldest first. receipts.jsonl holds the same entries with "
             "their hashes and signatures; undo data and anything that looks like a credential "
             "are left out of both, so a changed line no longer matches its signature here. "
             "verification.md is the check Friday ran on the record itself.", ""]
    for e in rows:
        try:
            line = plain_line(e)
        except Exception:  # noqa: BLE001
            line = str(e.get("kind"))
        lines.append("- #%s · %s · %s" % (e.get("seq"), str(e.get("ts") or "")[:19].replace("T", " "), line))
    out.write("receipts/receipts.md", "\n".join(lines) + "\n")
    out.write("receipts/receipts.jsonl",
              "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in rows))
    probs = rep.get("problems") or []
    v = ["# Does the record verify?", "",
         ("**Yes.** Every entry is in order, linked to the one before it and signed with this "
          "PC's key." if rep.get("ok") else "**No.** The record has a break; each one is listed below."),
         "", "- Entries checked: %s" % rep.get("records", len(rows)),
         "- Checked: %s" % (rep.get("checked_at") or time.strftime("%Y-%m-%d %H:%M"))]
    if probs:
        v += ["", "## Breaks", ""] + ["- %s%s" % (p.get("reason"), " (entry #%s)" % p["seq"] if p.get("seq") else "")
                                       for p in probs]
    out.write("receipts/verification.md", "\n".join(v) + "\n")
    return len(rows)


def _contacts(out: _Out, notes: list) -> int:
    from agent_friday import people_graph
    g = people_graph.PeopleGraph(_home())
    src = g.path if g.path.exists() else g.legacy_path
    if not src.exists():
        return 0
    if not _inside(src, _home()):
        notes.append("contacts: left out, the contacts file links outside Friday's data folder")
        return 0
    rows = g.contacts_list()
    if not rows:
        return 0
    md = ["# Contacts", ""]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "aliases", "domains", "company", "last_interaction"])
    for c in rows:
        md.append("- **%s**%s%s%s" % (c.get("name"), (" (" + ", ".join(c.get("aliases") or []) + ")") if c.get("aliases") else "",
                                      (" · " + c["company"]) if c.get("company") else "",
                                      (" · last in touch " + str(c["last_interaction"])[:10]) if c.get("last_interaction") else ""))
        w.writerow([c.get("name"), "; ".join(c.get("aliases") or []), "; ".join(c.get("domains") or []),
                    c.get("company") or "", c.get("last_interaction") or ""])
    out.write("contacts/contacts.md", "\n".join(md) + "\n")
    out.write("contacts/contacts.csv", buf.getvalue())
    return len(rows)


def _settings(out: _Out, notes: list) -> int:
    from agent_friday.services import friday_bundle
    p = _home() / "settings.json"
    if not p.exists():
        return 0
    if not _inside(p, _home()):
        notes.append("settings: left out, settings.json links outside Friday's data folder")
        return 0
    try:
        raw = friday_bundle._scrub_settings(p.read_bytes())
        data = _scrub_value(friday_bundle._scrub_obj(json.loads(raw.decode("utf-8"))))
    except Exception as e:  # noqa: BLE001
        notes.append("settings: could not be read (%s)" % type(e).__name__)
        return 0
    out.write("settings/settings.json", json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True))
    return 1


_WRITERS = {"wiki": _wiki, "memories": _memories, "conversations": _conversations, "goals": _goals,
            "receipts": _receipts, "contacts": _contacts, "settings": _settings}


# ── the export ───────────────────────────────────────────────────────────────

_SYNCED = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "icloud drive", "box", "pcloud",
           "mega", "sync.com", "nextcloud", "owncloud")


def cloud_synced(path: Path) -> bool:
    """Whether a folder looks like one a sync service copies off this PC."""
    parts = [x.lower() for x in Path(path).expanduser().resolve().parts]
    return any(any(x == s or x.startswith(s + " ") or x.startswith(s + "-") for s in _SYNCED) for x in parts)


def _check_dest(dest: Path) -> Path:
    dest = Path(dest).expanduser()
    if not dest.is_absolute():
        raise ExportError("Give the whole path of the folder the export goes to.")
    home = _home().resolve()
    try:
        dest.resolve().relative_to(home)
    except ValueError:
        pass
    else:
        raise ExportError("Choose a folder outside Friday's own data folder.")
    return dest


def _stores() -> dict:
    """Every store in the data folder with its version, size and location
    (services/store_inventory.py, §37.3-1)."""
    try:
        from agent_friday.services import store_inventory
        return store_inventory.public(store_inventory.inventory(_home()))
    except Exception as e:  # noqa: BLE001 - an export is never refused for its inventory
        return {"error": "unavailable:" + type(e).__name__}


def export(dest_parent: Path, *, only: Optional[list] = None, by: str = "owner") -> dict:
    """Write a new folder `friday-readable-<stamp>` inside dest_parent.
    Returns {folder, counts, notes, files}."""
    cats = list(only or CATEGORIES)
    bad = [c for c in cats if c not in CATEGORIES]
    if bad:
        raise ExportError("Unknown categories: %s. Choose from %s." % (", ".join(bad), ", ".join(CATEGORIES)))
    parent = _check_dest(dest_parent)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = parent / ("friday-readable-%s" % stamp)
    k = 2
    while folder.exists():
        folder = parent / ("friday-readable-%s-%d" % (stamp, k))
        k += 1
    folder.mkdir(parents=True)
    out, notes, counts = _Out(folder), [], {}
    for c in cats:
        try:
            counts[c] = _WRITERS[c](out, notes)
        except Exception as e:  # noqa: BLE001 - one category never stops the rest
            counts[c] = 0
            notes.append("%s: could not be exported (%s)" % (c, type(e).__name__))
    notes = [_scrub_text(n) for n in notes]          # page and folder names can carry a key
    readme = ["# Your Friday data, in plain files", "",
              "Made %s. Every file opens without Friday: Markdown (.md) in any text editor, "
              ".csv in a spreadsheet, .json and .jsonl in any text editor." % time.strftime("%Y-%m-%d %H:%M"),
              "", "## What is here", ""]
    readme += ["- **%s**: %d" % (c, counts.get(c, 0)) for c in cats]
    readme += ["", "## Never in an export", "",
               "- Friday's credential files: account tokens, stored API keys, Friday's own keys.",
               "- Values under a name such as password, token or API key, and keys of the kinds "
               "Friday recognises wherever they appear: they read \"%s\" or are withheld. "
               "Other text is copied as it is, so check before you share this folder." % BLANKED,
               "- Files that link outside Friday's data folder.",
               "- Messages said off the record (Friday never saves them).",
               "", "manifest.json lists every file with its SHA-256, so you can check nothing changed.",
               "", "Keep this folder somewhere safe: it is your data, unencrypted."]
    if notes:
        readme += ["", "## Left out or not readable", ""] + ["- %s" % n for n in notes]
    out.write("README.md", "\n".join(readme) + "\n")
    manifest = {"made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "counts": counts,
                "notes": notes, "files": dict(sorted(out.files.items())),
                "stores": _stores()}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    try:
        from agent_friday.services import action_ledger
        action_ledger.try_append("export", {"event": "readable_export", "by": str(by)[:40],
                                            "categories": cats, "files": len(out.files),
                                            "folder": folder.name})
    except Exception:
        pass
    return {"folder": str(folder), "counts": counts, "notes": notes, "files": len(out.files)}
