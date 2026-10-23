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

It reads Friday's stores and never writes to Friday's data folder (one
receipt in the action ledger says an export was made, and where). It never
contains credentials, keys or tokens; messages kept off the record are never
on disk, so never in it. Wiki pages in an encrypted section are written out in
plain text when the vault is open on this PC, and listed as left out when it
is not: the export is the owner's own copy, kept wherever they choose.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

CATEGORIES = ("wiki", "memories", "conversations", "goals", "receipts", "contacts", "settings")


class ExportError(RuntimeError):
    """Said plainly to the owner."""


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


def _when(t) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(t)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _safe(name: str, limit: int = 80) -> str:
    s = re.sub(r"[^\w .,'()-]+", " ", str(name or "")).strip(" .")
    return (re.sub(r"\s+", " ", s)[:limit] or "untitled").strip()


class _Out:
    """The export folder: every file written through here is hashed."""

    def __init__(self, root: Path):
        self.root = root
        self.files: dict = {}

    def write(self, rel: str, text: str) -> None:
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
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts) or p.is_symlink():
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
        if p.is_file():
            out.write("memories/" + name, "# %s\n\n%s" % (title, p.read_text(encoding="utf-8", errors="replace")))
            n += 1
    from agent_friday.services import user_model
    db = Path(user_model.DB_PATH)
    if db.is_file():
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
        try:
            conv = json.loads((d / "conversation.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            conv = {"id": d.name}
        msgs = cv._stored_messages(d.name)
        if not msgs:
            continue
        title = conv.get("title") or "Untitled"
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
            lines += ["**%s** · %s" % (who, _when(m.get("ts"))), "", text.strip(), ""]
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
        try:
            g = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            notes.append("goals/%s: could not be read" % p.name)
            continue
        if isinstance(g, dict):
            items.append(g)
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
    lines = ["# Everything Friday did or decided", "",
             "One line per entry, oldest first. receipts.jsonl holds the same entries as signed lines.", ""]
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
    if not p.is_file():
        return 0
    try:
        raw = friday_bundle._scrub_settings(p.read_bytes())
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        notes.append("settings: could not be read (%s)" % type(e).__name__)
        return 0
    out.write("settings/settings.json", json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True))
    return 1


_WRITERS = {"wiki": _wiki, "memories": _memories, "conversations": _conversations, "goals": _goals,
            "receipts": _receipts, "contacts": _contacts, "settings": _settings}


# ── the export ───────────────────────────────────────────────────────────────

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
    readme = ["# Your Friday data, in plain files", "",
              "Made %s. Every file opens without Friday: Markdown (.md) in any text editor, "
              ".csv in a spreadsheet, .json and .jsonl in any text editor." % time.strftime("%Y-%m-%d %H:%M"),
              "", "## What is here", ""]
    readme += ["- **%s**: %d" % (c, counts.get(c, 0)) for c in cats]
    readme += ["", "## Never in an export", "",
               "- Passwords, API keys, account tokens and Friday's own keys.",
               "- Secret values in settings (blanked).",
               "- Messages said off the record (Friday never saves them).",
               "", "manifest.json lists every file with its SHA-256, so you can check nothing changed.",
               "", "Keep this folder somewhere safe: it is your data, unencrypted."]
    if notes:
        readme += ["", "## Left out or not readable", ""] + ["- %s" % n for n in notes]
    out.write("README.md", "\n".join(readme) + "\n")
    manifest = {"made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "counts": counts,
                "notes": notes, "files": dict(sorted(out.files.items()))}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    try:
        from agent_friday.services import action_ledger
        action_ledger.try_append("export", {"event": "readable_export", "by": str(by)[:40],
                                            "categories": cats, "files": len(out.files),
                                            "folder": folder.name})
    except Exception:
        pass
    return {"folder": str(folder), "counts": counts, "notes": notes, "files": len(out.files)}
