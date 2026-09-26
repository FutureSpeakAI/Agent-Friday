"""Turning what the user said into the exact thing to open on Friday's desktop.

People name things the way people do: "the Harbor Legal email", "my budget
spreadsheet", "the bootstrap page", "model settings". Each resolver turns such
a phrase into a navigation target that the desktop's own deep-link handlers
accept (the keys a workspace reads through useNavTarget). It reads local state
first: the mail list the Messages window already fetched, the wiki's graph,
and the manifest the desktop reported about its own workspaces. It goes to the
network or walks the disk only within a time budget, so a spoken request is
answered inside the voice bridge's hard limit. An ambiguous phrase opens the
newest best match and names the others, rather than guessing silently.

open_on_desktop() is what the navigate_to tool and POST /api/desktop/open call:
resolve, send to the desktop, and report what the desktop confirmed.
"""
from __future__ import annotations

import bisect
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Callable

KINDS = ("workspace", "email", "file", "wiki_page", "graph_node", "settings",
         "calendar", "contact", "content_post")

#: Words that say what kind of thing is meant, not which one.
_FILLER = {
    "the", "a", "an", "my", "our", "this", "that", "please", "open", "show",
    "me", "up", "pull", "bring", "go", "to", "in", "on", "of", "for", "from",
    "about", "with", "and", "email", "emails", "mail", "message", "messages",
    "thread", "conversation", "latest", "last", "recent", "newest", "file",
    "files", "page", "wiki", "note", "notes", "node", "graph", "settings",
    "setting", "section", "tab", "workspace", "window", "screen", "one", "it",
    "s"}

_TYPE_EXT = {
    "spreadsheet": {".xlsx", ".xls", ".xlsm", ".csv", ".ods", ".numbers"},
    "document": {".docx", ".doc", ".odt", ".rtf", ".pdf", ".txt", ".md"},
    "pdf": {".pdf"},
    "presentation": {".pptx", ".ppt", ".odp", ".key"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"},
    "video": {".mp4", ".mov", ".mkv", ".webm", ".avi"},
    "audio": {".mp3", ".wav", ".m4a", ".flac", ".ogg"},
}
_TYPE_WORDS = {
    "spreadsheet": "spreadsheet", "spreadsheets": "spreadsheet", "sheet": "spreadsheet",
    "excel": "spreadsheet", "csv": "spreadsheet", "workbook": "spreadsheet",
    "document": "document", "doc": "document", "docs": "document", "word": "document",
    "pdf": "pdf", "presentation": "presentation", "slides": "presentation",
    "deck": "presentation", "powerpoint": "presentation", "image": "image",
    "photo": "image", "picture": "image", "screenshot": "image", "video": "video",
    "recording": "audio", "song": "audio", "audio": "audio",
}

#: A folder named in the request narrows the search to that searchable root.
_ROOT_WORDS = {"documents": "documents", "downloads": "downloads",
               "download": "downloads", "desktop": "desktop",
               "creations": "creations", "creation": "creations"}

MAIL_NETWORK_BUDGET_S = 6.0
FILE_BUDGET_S = 6.0


def _tokens(s: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(s or "").lower())


def _content(s: Any, drop: set | None = None) -> list[str]:
    drop = drop or set()
    return [w for w in _tokens(s) if w not in _FILLER and w not in drop]


def _score(q: list[str], hay: str) -> float:
    """Share of the query's words found in `hay`, plus a bonus for the phrase.
    A word of three letters or more may sit inside a longer one (a file called
    HouseholdBudget matches "budget"); a shorter one must stand alone."""
    if not q:
        return 0.0
    words = set(_tokens(hay))
    s = sum(1 for t in q if t in words or (len(t) >= 3 and t in hay)) / len(q)
    return s + (0.5 if " ".join(q) in hay else 0.0)


def _when(v: Any) -> float:
    """A sortable time from an epoch number or an ISO string."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _within(fn: Callable[[], Any], budget_s: float) -> tuple[bool, Any]:
    """Run `fn` on a worker thread; (finished, value) after at most budget_s.
    A search that outlives its budget finishes behind the request."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except Exception as e:                     # reported, never raised
            box["error"] = e

    th = threading.Thread(target=run, name="desktop-resolve", daemon=True)
    th.start()
    th.join(max(0.0, budget_s))
    if th.is_alive():
        return False, None
    if "error" in box:
        return True, None
    return True, box.get("value")


def _ok(target: dict, label: str, verify: tuple | None = None, also: list | None = None) -> dict:
    out = {"ok": True, "target": target, "label": label}
    if verify:
        out["verify"] = {"workspace": target["workspace"], "key": verify[0],
                         "value": verify[1]}
    if also:
        out["also"] = also[:4]
    return out


def _fail(reason: str, candidates: list | None = None) -> dict:
    out = {"ok": False, "reason": reason}
    if candidates:
        out["candidates"] = candidates[:5]
    return out


# ── Workspaces and their sections (what the desktop said it accepts) ─────────

def _manifest() -> dict:
    from agent_friday.services import desktop_bus
    return desktop_bus.manifest()


def _resolve_ws(name: str) -> str | None:
    name = (name or "").strip()
    if not name:
        return None
    m = _manifest()
    workspaces = m.get("workspaces") or {}
    low = name.lower()
    if low in workspaces:
        return low
    for wid, spec in workspaces.items():
        if (spec.get("label") or "").lower() == low:
            return wid
    try:
        from agent_friday.services.agent import _resolve_workspace
        return _resolve_workspace(name)
    except Exception:
        return None


def _match_section(spec: dict, phrase: str, ws_words: set) -> dict | None:
    """The section in `spec` that `phrase` names, by id, label or alias."""
    q = _content(phrase, ws_words)
    if not q:
        return None
    best, best_s = None, 0.0
    for sec in spec.get("sections") or []:
        names = [sec.get("id"), sec.get("label")] + list(sec.get("aliases") or [])
        hay = " ".join(str(n or "").lower().replace("-", " ").replace("_", " ") for n in names)
        s = _score(q, hay)
        if " ".join(q) in {str(n or "").lower() for n in names}:
            s += 1.0
        if s > best_s:
            best, best_s = sec, s
    return best if best_s >= 0.99 else None


def _sections_hint(spec: dict) -> str:
    secs = spec.get("sections") or []
    return ", ".join("%s (%s)" % (s.get("label") or s.get("id"), s.get("id")) for s in secs[:20])


def resolve_workspace(workspace: str = "", section: str = "", query: str = "") -> dict:
    ws = _resolve_ws(workspace or query)
    if not ws:
        names = sorted((_manifest().get("workspaces") or {}).keys())
        return _fail("%r isn't one of the desktop's workspaces%s" % (
            workspace or query, (": " + ", ".join(names)) if names else ""))
    spec = (_manifest().get("workspaces") or {}).get(ws) or {}
    label = spec.get("label") or ws.title()
    target: dict = {"workspace": ws}
    phrase = section or (query if workspace else "")
    if phrase:
        sec = _match_section(spec, phrase, set(_tokens(ws)) | set(_tokens(label)))
        if not sec:
            hint = _sections_hint(spec)
            return _fail("%s has no section called %r%s" % (
                label, phrase, ("; its sections: " + hint) if hint else ""))
        key = sec.get("key") or spec.get("key") or "tab"
        target[key] = sec.get("id")
        extra = sec.get("with") or {}
        target.update(extra)
        return _ok(target, "%s › %s" % (label, sec.get("label") or sec.get("id")),
                   verify=(key, sec.get("id")))
    return _ok(target, label)


_PARTS_CACHE: dict = {"key": None, "map": {}}


def _index_html() -> Path | None:
    """The UI file the server serves (it opens 'index.html' from its cwd)."""
    for p in (Path.cwd() / "index.html", Path(__file__).resolve().parents[3] / "index.html"):
        if p.is_file():
            return p
    return None


def settings_parts() -> dict:
    """The titled sections each Settings tab shows, read from the served UI
    itself: the panels SettingsWS draws for each tab, and the StSection titles
    in those panels and in the Settings panels they draw in turn."""
    p = _index_html()
    if p is None:
        return {}
    try:
        key = (str(p), p.stat().st_mtime)
    except OSError:
        return {}
    if _PARTS_CACHE["key"] == key:
        return _PARTS_CACHE["map"]
    text = p.read_text(encoding="utf-8")
    starts = sorted((m.start(), m.group(1)) for m in
                    re.finditer(r"^function ([A-Za-z0-9_]+)\(", text, re.M))
    at = [st for st, _ in starts]
    body = {}
    for st, name in starts:
        i = bisect.bisect_right(at, st)
        body[name] = text[st:at[i] if i < len(at) else len(text)]
    comp_re = re.compile(r"createElement\((Settings[A-Za-z0-9_]+)")
    title_re = re.compile(r'createElement\(StSection, \{\s*title: "([^"]+)"')

    def titles(name, seen):
        if name in seen:
            return []
        seen.add(name)
        b = body.get(name, "")
        out = title_re.findall(b)
        for sub in comp_re.findall(b):
            out += titles(sub, seen)
        return out

    sw = body.get("SettingsWS", "")
    marks = list(re.finditer(r"tab === '([a-z]+)' && ", sw))
    parts = {}
    for i, m in enumerate(marks):
        seg = sw[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(sw)]
        seen: set = set()
        found = []
        for comp in comp_re.findall(seg):
            found += titles(comp, seen)
        parts[m.group(1)] = list(dict.fromkeys(found))
    _PARTS_CACHE.update(key=key, map=parts)
    return parts


def resolve_settings(query: str = "", section: str = "", id: str = "") -> dict:
    """A Settings tab, and optionally a named section within it."""
    spec = (_manifest().get("workspaces") or {}).get("settings") or {}
    phrase = id or section or query
    if not phrase:
        return _ok({"workspace": "settings"}, "Settings")
    ws_words = {"settings", "setting", "preferences"}
    tab = _match_section(spec, phrase, ws_words)
    if tab:
        return _ok({"workspace": "settings", "tab": tab["id"]},
                   "Settings › %s" % (tab.get("label") or tab["id"]),
                   verify=("tab", tab["id"]))
    # A section inside a tab, by the titles the served UI gives its sections.
    q = _content(phrase, ws_words)
    parts = settings_parts()
    best, best_s = None, 0.0
    for t in spec.get("sections") or []:
        for title in parts.get(t.get("id"), []):
            s = _score(q, str(title).lower())
            if s > best_s:
                best, best_s = (t, title), s
    if best and best_s >= 0.99:
        t, title = best
        # Confirmed by the section itself: a section a panel draws only when
        # it has data is not on screen just because its tab is.
        return _ok({"workspace": "settings", "tab": t["id"], "section": title},
                   "Settings › %s › %s" % (t.get("label") or t["id"], title),
                   verify=("section", title))
    hint = _sections_hint(spec)
    return _fail("no Settings tab or section matches %r%s" % (
        phrase, ("; tabs: " + hint) if hint else ""))


# ── Email ────────────────────────────────────────────────────────────────────

def _cached_cards() -> list[dict]:
    """Every card the Messages list has fetched recently, newest copy of each."""
    from agent_friday.services import message_triage as mt
    with mt._collect_cache_lock:
        results = [dict(s.get("result") or {}) for s in mt._collect_cache.values()]
    seen: dict = {}
    for res in results:
        for c in res.get("messages") or []:
            key = (c.get("account_id"), c.get("thread_id") or c.get("id"))
            if key[1] and key not in seen:
                seen[key] = c
    return list(seen.values())


def _rank_cards(q: list[str], cards: list[dict], keep_all: bool = False) -> list[tuple]:
    """(score, time, card), best first: the words in the sender or subject,
    then in the snippet, then the newest. keep_all keeps cards that name none
    of the words, for results Gmail's own search already matched."""
    out = []
    for c in cards:
        head = " ".join(str(c.get(k) or "") for k in
                        ("sender", "sender_email", "subject")).lower()
        s = _score(q, head)
        if s < 0.99:
            s = max(s, 0.9 * _score(q, head + " " + str(c.get("snippet") or "").lower()))
        if s > 0 or keep_all:
            out.append((s, _when(c.get("timestamp")), c))
    out.sort(key=lambda x: (round(x[0], 2), x[1]), reverse=True)
    return out


def _card_label(c: dict) -> str:
    who = c.get("sender") or c.get("sender_email") or "someone"
    return "%s — %s" % (who, (c.get("subject") or "(no subject)")[:80])


def resolve_email(query: str = "", id: str = "", account: str = "",
                  budget_s: float = MAIL_NETWORK_BUDGET_S) -> dict:
    if id:
        target = {"workspace": "messages", "thread_id": id}
        if account:
            target["account"] = account
        return _ok(target, "the email thread %s" % id, verify=("thread_id", id))
    q = _content(query)
    if not q:
        return _fail("say whose email or what it was about")
    ranked = _rank_cards(q, _cached_cards())
    if ranked and ranked[0][0] >= 0.99:
        return _email_target(ranked, [_card_label(c) for s, _t, c in ranked[1:6] if s >= 0.99])
    # Not in the list the Messages window fetched: ask Gmail's own search,
    # which matches every word somewhere in each message it returns.
    from agent_friday.services import message_triage as mt
    done, res = _within(lambda: mt.collect(limit_per_account=10,
                                            query=" ".join(q)), budget_s)
    found = _rank_cards(q, list((res or {}).get("messages") or []), keep_all=True)
    if found:
        return _email_target(found, [_card_label(c) for _s, _t, c in found[1:5]])
    cands = [_card_label(c) for _s, _t, c in ranked[:5]]
    if not done:
        return _fail("Gmail did not answer within %d seconds" % int(budget_s), cands)
    return _fail("no email matches %r" % " ".join(q), cands)


def _email_target(ranked: list[tuple], also: list[str]) -> dict:
    top = ranked[0][2]
    tid = top.get("thread_id") or top.get("id")
    target = {"workspace": "messages", "thread_id": tid,
              "account": top.get("account_id") or "",
              "subject": top.get("subject") or "", "from": top.get("sender") or ""}
    return _ok(target, "the email " + _card_label(top), verify=("thread_id", tid), also=also)


# ── Files ────────────────────────────────────────────────────────────────────

def _studio_place(path: Path) -> tuple | None:
    """(root id, folder relative to it, file name) for a path Studio can browse."""
    from agent_friday.services import studio_files
    try:
        real = path.resolve()
    except OSError:
        return None
    best = None
    for rid, root in studio_files.roots().items():
        try:
            rel = real.relative_to(Path(root).resolve())
        except (ValueError, OSError):
            continue
        if best is None or len(rel.parts) < len(best[1].parts):
            best = (rid, rel)
    if best is None:
        return None
    rid, rel = best
    folder = str(PurePosixPath(*rel.parts[:-1])) if len(rel.parts) > 1 else ""
    return rid, folder, rel.parts[-1] if rel.parts else ""


def resolve_file(query: str = "", id: str = "", budget_s: float = FILE_BUDGET_S) -> dict:
    if id:
        p = Path(id).expanduser()
        place = _studio_place(p)
        if not place:
            return _fail("%s is not in a folder Studio's file browser shows" % id)
        rid, folder, name = place
        return _ok({"workspace": "studio", "view": "files", "root": rid,
                    "path": folder, "file": name}, "the file %s" % name,
                   verify=("file", name))
    words = _tokens(query)
    kinds = {_TYPE_WORDS[w] for w in words if w in _TYPE_WORDS}
    exts = set().union(*(_TYPE_EXT[k] for k in kinds)) if kinds else set()
    folders = {_ROOT_WORDS[w] for w in words if w in _ROOT_WORDS}
    q = [w for w in _content(query) if w not in _TYPE_WORDS and w not in _ROOT_WORDS]
    if not q:
        return _fail("say what the file is called or what it is about")
    from agent_friday.services import file_search
    root = next(iter(folders)) if len(folders) == 1 else None
    done, res = _within(lambda: file_search.search_files(
        query=" ".join(q), root=root, newest_first=True, limit=60), budget_s)
    rows = list((res or {}).get("results") or [])
    if exts:
        rows = [r for r in rows if Path(r.get("name") or "").suffix.lower() in exts]
    ranked = sorted(((_score(q, str(r.get("name") or "").lower()), r.get("mtime") or 0, r)
                     for r in rows), key=lambda x: (round(x[0], 2), x[1]), reverse=True)
    ranked = [x for x in ranked if x[0] >= 0.99]
    if not ranked:
        what = " ".join(q) + (" (%s)" % "/".join(sorted(kinds)) if kinds else "")
        if not done:
            return _fail("the file search for %r did not finish within %d seconds"
                         % (what, int(budget_s)))
        return _fail("no file matches %r in Documents, Downloads, Desktop or "
                     "Friday's creations" % what)
    for _s, _m, r in ranked:
        place = _studio_place(Path(r["path"]))
        if place:
            rid, folder, name = place
            also = [x[2].get("name") for x in ranked[1:5]]
            return _ok({"workspace": "studio", "view": "files", "root": rid,
                        "path": folder, "file": name}, "the file %s" % name,
                       verify=("file", name), also=also)
    return _fail("the matching files are outside the folders Studio shows",
                 [x[2].get("path") for x in ranked[:5]])


# ── Knowledge ────────────────────────────────────────────────────────────────

def _entities() -> list[dict]:
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    return KnowledgeGraphStore().load("entities")


def _entity_path(e: dict) -> str | None:
    pages = (e.get("provenance") or {}).get("wiki_pages") or []
    return pages[0] if pages else None


def resolve_knowledge(query: str = "", id: str = "", pages_only: bool = False) -> dict:
    ents = _entities()
    if id:
        low = id.strip().lower()
        for e in ents:
            if e.get("id", "").lower() == low or (_entity_path(e) or "").lower() == low:
                return _knowledge_target(e)
        if low.endswith((".md", ".txt")) or "/" in low:
            return _ok({"workspace": "knowledge", "path": id.strip()},
                       "the wiki page %s" % id.strip(), verify=("path", id.strip()))
        return _fail("no wiki page or graph node has the id %r" % id)
    q = _content(query)
    if not q:
        return _fail("say which page or topic")
    ranked = []
    for e in ents:
        path = _entity_path(e)
        if pages_only and not path:
            continue
        title = str(e.get("title") or "").lower()
        key = (path or "").lower().replace("-", " ").replace("_", " ").replace("/", " ")
        s = max(_score(q, title), _score(q, key),
                0.8 * _score(q, title + " " + str(e.get("description") or "").lower()))
        if s > 0:
            ranked.append((s, bool(path), e))
    ranked.sort(key=lambda x: (round(x[0], 2), x[1]), reverse=True)
    if not ranked or ranked[0][0] < 0.99:
        return _fail("nothing in the wiki matches %r" % " ".join(q),
                     [x[2].get("title") for x in ranked[:5]])
    out = _knowledge_target(ranked[0][2])
    out["also"] = [x[2].get("title") for x in ranked[1:5] if x[0] >= 0.99]
    return out


def _knowledge_target(e: dict) -> dict:
    path = _entity_path(e)
    title = e.get("title") or e.get("id")
    if path:
        return _ok({"workspace": "knowledge", "path": path},
                   "the wiki page %s" % title, verify=("path", path))
    return _ok({"workspace": "knowledge", "node": e["id"]},
               "the graph node %s" % title, verify=("node", e["id"]))


# ── Calendar, contacts, content ──────────────────────────────────────────────

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def _parse_day(text: str, today: date | None = None) -> date | None:
    today = today or date.today()
    t = (text or "").strip().lower()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    words = _tokens(t)
    if "today" in words:
        return today
    if "tomorrow" in words:
        return today + timedelta(days=1)
    if "yesterday" in words:
        return today - timedelta(days=1)
    for i, name in enumerate(_WEEKDAYS):
        if name in words:
            ahead = (i - today.weekday()) % 7
            if "next" in words and ahead == 0:
                ahead = 7
            return today + timedelta(days=ahead)
    for i, name in enumerate(_MONTHS):
        for w in words:
            if len(w) >= 3 and name.startswith(w):
                m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", t)
                if m:
                    try:
                        d = date(today.year, i + 1, int(m.group(1)))
                    except ValueError:
                        return None
                    return d if d >= today - timedelta(days=183) else d.replace(year=d.year + 1)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", t)
    if m:
        try:
            return date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def resolve_calendar(query: str = "", id: str = "") -> dict:
    if id and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", id.strip()):
        return _ok({"workspace": "calendar", "view": "meetings", "meeting_id": id},
                   "the meeting %s" % id)
    d = _parse_day(id or query)
    if d is None:
        return _fail("say which day, e.g. 'tomorrow', 'Friday' or '2026-10-02'")
    iso = d.isoformat()
    return _ok({"workspace": "calendar", "date": iso},
               "the calendar for %s" % d.strftime("%A %d %B"), verify=("date", iso))


def _contacts() -> list[dict]:
    from agent_friday.services.misc_engine import _contacts_list
    return _contacts_list()


_CONTACT_WORDS = {"contact", "contacts", "card", "person", "people", "profile",
                  "details", "info", "entry", "who", "is"}


def resolve_contact(query: str = "", id: str = "") -> dict:
    """A person in Contacts, by name or alias, then by company."""
    q = _content(id or query, _CONTACT_WORDS)
    if not q:
        return _fail("say whose contact card")
    try:
        people = _contacts()
    except Exception:
        people = []
    ranked = []
    for c in people:
        names = " ".join(str(n).lower() for n in [c.get("name")] + list(c.get("aliases") or []) if n)
        s = _score(q, names)
        if s < 0.99:
            s = max(s, 0.9 * _score(q, names + " " + str(c.get("company") or "").lower()))
        if s > 0:
            ranked.append((s, c.get("overall") or 0, c))
    ranked.sort(key=lambda x: (round(x[0], 2), x[1]), reverse=True)
    if not ranked or ranked[0][0] < 0.99:
        return _fail("no contact matches %r" % " ".join(q),
                     [x[2].get("name") for x in ranked[:5]])
    name = ranked[0][2].get("name")
    also = [x[2].get("name") for x in ranked[1:5] if x[0] >= 0.99]
    return _ok({"workspace": "contacts", "name": name}, "the contact %s" % name,
               verify=("name", name), also=also)


def resolve_content_post(query: str = "", id: str = "") -> dict:
    pid = (id or "").strip()
    if not re.fullmatch(r"post_[0-9a-f]{6,}", pid):
        return _fail("give the post id (post_…); search the content queue first")
    return _ok({"workspace": "content", "post": pid}, "the post %s" % pid)


def resolve(kind: str, query: str = "", id: str = "", workspace: str = "",
            section: str = "", account: str = "") -> dict:
    kind = (kind or "").strip().lower().replace(" ", "_")
    if kind in ("wiki", "page"):
        kind = "wiki_page"
    if kind in ("node", "graph"):
        kind = "graph_node"
    if kind == "workspace":
        return resolve_workspace(workspace, section, query)
    if kind == "settings":
        return resolve_settings(query, section, id)
    if kind == "email":
        return resolve_email(query, id, account)
    if kind == "file":
        return resolve_file(query, id)
    if kind in ("wiki_page", "graph_node"):
        return resolve_knowledge(query, id, pages_only=(kind == "wiki_page"))
    if kind == "calendar":
        return resolve_calendar(query, id)
    if kind == "contact":
        return resolve_contact(query, id)
    if kind == "content_post":
        return resolve_content_post(query, id)
    return _fail("kind must be one of: " + ", ".join(KINDS))


# ── Resolve, show, and say what the desktop confirmed ────────────────────────

def open_on_desktop(kind: str, query: str = "", id: str = "", workspace: str = "",
                    section: str = "", account: str = "") -> dict:
    """Resolve the request, push it to the desktop, and return what happened.

    {"status": "opened" | "opened_unconfirmed" | "partial" | "failed" |
               "sent" | "no_desktop" | "not_found",
     "text": one line for the model, ...}

    The text starts NAV_OK only when the desktop confirmed the window opened;
    NAV_PARTIAL when it opened but shows something other than the target.
    """
    t0 = time.time()
    r = resolve(kind, query=query, id=id, workspace=workspace, section=section,
                account=account)
    if not r.get("ok"):
        text = "NAV_FAIL: " + r["reason"]
        if r.get("candidates"):
            text += ". Closest: " + "; ".join(str(c) for c in r["candidates"])
        return {"status": "not_found", "text": text, "resolved": r}
    from agent_friday.services import desktop_bus
    action = dict(r["target"], type="navigate")
    sent = desktop_bus.send([action], verify=r.get("verify"))
    ws = r["target"]["workspace"]
    out = {"resolved": r, "sent": sent, "took_s": round(time.time() - t0, 2)}
    if not sent.get("delivered"):
        out.update(status="no_desktop", text="NAV_FAIL: %s, so %s was not shown." % (
            sent.get("reason"), r["label"]))
        return out
    ack = sent.get("ack") or {}
    if not sent.get("acked"):
        out.update(status="sent", text=(
            "NAV_SENT:%s — sent %s to the desktop, but the page did not confirm "
            "it within %d seconds." % (ws, r["label"], int(desktop_bus.ACK_TIMEOUT_S))))
        return out
    if ack.get("opened") is False:
        out.update(status="failed", text="NAV_FAIL: the desktop did not open %s%s." % (
            r["label"], (" (%s)" % ack["note"]) if ack.get("note") else ""))
        return out
    also = (" Other matches: %s." % "; ".join(str(a) for a in r["also"])) if r.get("also") else ""
    hidden = ("" if ack.get("visible", True) else
              " The Friday window is minimized or covered, so it is not on screen yet.")
    matched = ack.get("matched")
    if r.get("verify") and matched is False:
        out.update(status="partial", text=(
            "NAV_PARTIAL:%s — opened %s, but it is not showing %s%s.%s%s" % (
                ws, ack.get("label") or ws, r["label"],
                (" (%s)" % ack["note"]) if ack.get("note") else "", hidden, also)))
        return out
    if r.get("verify") and matched is None:
        out.update(status="opened_unconfirmed", text=(
            "NAV_OK:%s — opened %s on the desktop; %s, so it could not confirm the "
            "exact item.%s%s" % (ws, r["label"], ack.get("note") or "the window does not "
                                 "report what it shows", hidden, also)))
        return out
    out.update(status="opened", text="NAV_OK:%s — opened %s on the desktop.%s%s" % (
        ws, r["label"], hidden, also))
    return out
