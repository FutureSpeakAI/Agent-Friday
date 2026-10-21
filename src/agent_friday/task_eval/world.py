"""The virtual world a practice task runs in, and the tools that reach it.

Everything here is in memory. The tools read and change only this world; none
of them touches the filesystem or the network. The world records every call
in order (``calls``) and every change it made (``effects``), and ``snapshot``
gives the final state for the grader.

Deliberately imports nothing from the app, so the grader's tests and the
world's own tests run without it.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable, Dict, List, Optional

NO_ANSWER = "(The owner has not answered.)"


def _norm(s: Any) -> str:
    return str(s or "").casefold()


def _brief(msg: dict) -> dict:
    return {"id": msg["id"], "from": msg.get("from", ""), "to": msg.get("to", ""),
            "subject": msg.get("subject", ""), "date": msg.get("date", ""),
            "unread": bool(msg.get("unread", False)),
            "snippet": str(msg.get("body", ""))[:120]}


class World:
    """One episode's mailbox, calendar, files, wiki, web pages, skills and owner."""

    def __init__(self, seed: Optional[dict] = None, owner: Optional[dict] = None):
        seed = copy.deepcopy(seed or {})
        self.mail: Dict[str, dict] = {}
        for m in seed.get("mail", []):
            m = dict(m)
            m.setdefault("archived", False)
            m.setdefault("labels", [])
            self.mail[m["id"]] = m
        self.drafts: List[dict] = []
        self.sent: List[dict] = []
        self.events: Dict[str, dict] = {e["id"]: dict(e) for e in seed.get("calendar", [])}
        self.deleted_events: List[dict] = []
        self.files: Dict[str, str] = dict(seed.get("files", {}))
        self.deleted_files: List[str] = []
        self.wiki: Dict[str, str] = dict(seed.get("wiki", {}))
        self.web: Dict[str, Any] = dict(seed.get("web", {}))
        self.skills: Dict[str, str] = dict(seed.get("skills", {}))
        self.owner_replies: List[dict] = list((owner or {}).get("replies", []))
        self.questions: List[str] = []
        self.calls: List[dict] = []
        self.effects: List[dict] = []
        self._n = 0

    # ── bookkeeping ─────────────────────────────────────────────────────────

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def _effect(self, kind: str, **detail) -> None:
        self.effects.append({"effect": kind, **detail})

    def snapshot(self) -> dict:
        return {
            "mail": {"messages": copy.deepcopy(self.mail), "drafts": copy.deepcopy(self.drafts),
                     "sent": copy.deepcopy(self.sent)},
            "calendar": {"events": copy.deepcopy(self.events),
                         "deleted": copy.deepcopy(self.deleted_events)},
            "files": dict(self.files), "deleted_files": list(self.deleted_files),
            "wiki": dict(self.wiki), "questions": list(self.questions),
        }

    # ── mail ────────────────────────────────────────────────────────────────

    def search_email(self, query: str = "", include_archived: bool = False) -> str:
        q = _norm(query)
        hits = []
        for m in self.mail.values():
            if m.get("archived") and not include_archived:
                continue
            hay = _norm(" ".join([m.get("from", ""), m.get("subject", ""), m.get("body", "")]))
            if not q or all(w in hay for w in q.split()):
                hits.append(_brief(m))
        return json.dumps({"messages": hits})

    def read_email(self, id: str) -> str:
        m = self.mail.get(str(id))
        if not m:
            return json.dumps({"error": f"no message with id {id}"})
        m["unread"] = False
        return json.dumps({k: m.get(k) for k in ("id", "from", "to", "cc", "subject", "date", "body", "labels")})

    def draft_email(self, to: str, subject: str = "", body: str = "", reply_to_id: str = "") -> str:
        d = {"id": self._id("d"), "to": to, "subject": subject, "body": body, "reply_to_id": reply_to_id}
        self.drafts.append(d)
        self._effect("draft", id=d["id"], to=to)
        return json.dumps({"draft_saved": d["id"], "sent": False})

    def send_email(self, to: str, subject: str = "", body: str = "") -> str:
        s = {"id": self._id("s"), "to": to, "subject": subject, "body": body}
        self.sent.append(s)
        self._effect("sent", id=s["id"], to=to)
        return json.dumps({"sent": s["id"]})

    def archive_email(self, id: str) -> str:
        m = self.mail.get(str(id))
        if not m:
            return json.dumps({"error": f"no message with id {id}"})
        m["archived"] = True
        self._effect("archive", id=m["id"])
        return json.dumps({"archived": m["id"]})

    def label_email(self, id: str, label: str) -> str:
        m = self.mail.get(str(id))
        if not m:
            return json.dumps({"error": f"no message with id {id}"})
        if label not in m["labels"]:
            m["labels"].append(label)
        self._effect("label", id=m["id"], label=label)
        return json.dumps({"labelled": m["id"], "label": label})

    # ── calendar ────────────────────────────────────────────────────────────

    def query_calendar(self, start_date: str = "", end_date: str = "") -> str:
        lo, hi = str(start_date or ""), str(end_date or "")
        out = []
        for e in sorted(self.events.values(), key=lambda e: e.get("start", "")):
            day = str(e.get("start", ""))[:10]
            if lo and day < lo[:10]:
                continue
            if hi and day > hi[:10]:
                continue
            out.append(dict(e))
        return json.dumps({"events": out})

    def create_calendar_event(self, title: str, start: str, end: str = "", attendees: Any = None,
                              location: str = "", description: str = "") -> str:
        e = {"id": self._id("e"), "title": title, "start": start, "end": end,
             "attendees": list(attendees or []) if not isinstance(attendees, str) else [attendees],
             "location": location, "description": description}
        self.events[e["id"]] = e
        self._effect("event_created", id=e["id"], start=start)
        return json.dumps({"created": e["id"]})

    def delete_calendar_event(self, id: str) -> str:
        e = self.events.pop(str(id), None)
        if not e:
            return json.dumps({"error": f"no event with id {id}"})
        self.deleted_events.append(e)
        self._effect("event_deleted", id=e["id"])
        return json.dumps({"deleted": e["id"]})

    # ── files ───────────────────────────────────────────────────────────────

    def list_files(self, folder: str = "") -> str:
        f = str(folder or "").strip("/")
        names = sorted(p for p in self.files if not f or p.startswith(f + "/"))
        return json.dumps({"files": names})

    def read_file(self, path: str) -> str:
        p = str(path or "").strip("/")
        if p not in self.files:
            return json.dumps({"error": f"no file at {path}"})
        return json.dumps({"path": p, "content": self.files[p]})

    def write_file(self, path: str, content: str) -> str:
        p = str(path or "").strip("/")
        self.files[p] = str(content or "")
        self._effect("file_written", path=p)
        return json.dumps({"written": p})

    def move_file(self, source: str, destination: str) -> str:
        s, d = str(source or "").strip("/"), str(destination or "").strip("/")
        if s not in self.files:
            return json.dumps({"error": f"no file at {source}"})
        if d.endswith("/") or (d and not re.search(r"\.[A-Za-z0-9]{1,5}$", d)):
            d = d.rstrip("/") + "/" + s.rsplit("/", 1)[-1]
        self.files[d] = self.files.pop(s)
        self._effect("file_moved", source=s, destination=d)
        return json.dumps({"moved": s, "to": d})

    def delete_file(self, path: str) -> str:
        p = str(path or "").strip("/")
        gone = [k for k in list(self.files) if k == p or k.startswith(p + "/")]
        if not gone:
            return json.dumps({"error": f"no file or folder at {path}"})
        for k in gone:
            self.files.pop(k)
            self.deleted_files.append(k)
        self._effect("file_deleted", paths=gone)
        return json.dumps({"deleted": gone})

    # ── wiki, web, skills ───────────────────────────────────────────────────

    def wiki_search(self, query: str = "") -> str:
        q = _norm(query)
        return json.dumps({"pages": sorted(t for t, body in self.wiki.items()
                                           if not q or q in _norm(t) or q in _norm(body))})

    def wiki_read(self, title: str) -> str:
        if title not in self.wiki:
            return json.dumps({"error": f"no wiki page called {title}"})
        return json.dumps({"title": title, "content": self.wiki[title]})

    def wiki_write(self, title: str, content: str) -> str:
        self.wiki[str(title)] = str(content or "")
        self._effect("wiki_written", title=title)
        return json.dumps({"saved": title})

    def browse_web(self, url: str) -> str:
        page = self.web.get(str(url or "").strip())
        if page is None:
            return json.dumps({"error": f"could not load {url}: no such page"})
        if isinstance(page, dict) and page.get("status", 200) != 200:
            return json.dumps({"error": f"could not load {url}: HTTP {page['status']}"})
        text = page.get("text", "") if isinstance(page, dict) else str(page)
        return json.dumps({"url": url, "text": text})

    def read_skill(self, name: str) -> str:
        if name not in self.skills:
            return json.dumps({"error": f"no skill called {name}", "skills": sorted(self.skills)})
        return json.dumps({"name": name, "steps": self.skills[name]})

    def ask_owner(self, question: str) -> str:
        self.questions.append(str(question or ""))
        q = _norm(question)
        for r in self.owner_replies:
            m = _norm(r.get("match", "*"))
            if m == "*" or m in q:
                return json.dumps({"owner_reply": r.get("reply", "")})
        return json.dumps({"owner_reply": NO_ANSWER})


# ── tool schemas ────────────────────────────────────────────────────────────

def _s(props: dict, required: List[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


_STR = {"type": "string"}

TOOLS: List[dict] = [
    {"name": "search_email", "description": "Search the owner's mailbox. Returns matching messages (id, from, subject, date, snippet). An empty query lists the inbox.",
     "input_schema": _s({"query": _STR, "include_archived": {"type": "boolean"}}, [])},
    {"name": "read_email", "description": "Read one message in full by its id.",
     "input_schema": _s({"id": _STR}, ["id"])},
    {"name": "draft_email", "description": "Save a draft email. Nothing is sent; the owner sends drafts.",
     "input_schema": _s({"to": _STR, "subject": _STR, "body": _STR, "reply_to_id": _STR}, ["to", "body"])},
    {"name": "send_email", "description": "Send an email now.",
     "input_schema": _s({"to": _STR, "subject": _STR, "body": _STR}, ["to", "body"])},
    {"name": "archive_email", "description": "Archive one message (it leaves the inbox; nothing is deleted).",
     "input_schema": _s({"id": _STR}, ["id"])},
    {"name": "label_email", "description": "Add a label to one message.",
     "input_schema": _s({"id": _STR, "label": _STR}, ["id", "label"])},
    {"name": "query_calendar", "description": "List calendar events between two dates (YYYY-MM-DD, inclusive).",
     "input_schema": _s({"start_date": _STR, "end_date": _STR}, [])},
    {"name": "create_calendar_event", "description": "Add an event. start/end are ISO local times, e.g. 2026-10-16T12:30.",
     "input_schema": _s({"title": _STR, "start": _STR, "end": _STR,
                         "attendees": {"type": "array", "items": _STR}, "location": _STR,
                         "description": _STR}, ["title", "start"])},
    {"name": "delete_calendar_event", "description": "Delete one calendar event by id.",
     "input_schema": _s({"id": _STR}, ["id"])},
    {"name": "list_files", "description": "List files, optionally under one folder.",
     "input_schema": _s({"folder": _STR}, [])},
    {"name": "read_file", "description": "Read a text file.",
     "input_schema": _s({"path": _STR}, ["path"])},
    {"name": "write_file", "description": "Write a text file (creates or replaces it).",
     "input_schema": _s({"path": _STR, "content": _STR}, ["path", "content"])},
    {"name": "move_file", "description": "Move or rename a file. A destination folder keeps the file name.",
     "input_schema": _s({"source": _STR, "destination": _STR}, ["source", "destination"])},
    {"name": "delete_file", "description": "Delete a file or a whole folder.",
     "input_schema": _s({"path": _STR}, ["path"])},
    {"name": "wiki_search", "description": "Search the owner's wiki page titles and text.",
     "input_schema": _s({"query": _STR}, [])},
    {"name": "wiki_read", "description": "Read a wiki page by title.",
     "input_schema": _s({"title": _STR}, ["title"])},
    {"name": "wiki_write", "description": "Create or replace a wiki page.",
     "input_schema": _s({"title": _STR, "content": _STR}, ["title", "content"])},
    {"name": "browse_web", "description": "Fetch a web page and return its text.",
     "input_schema": _s({"url": _STR}, ["url"])},
    {"name": "read_skill", "description": "Read one of the owner's saved skills (step-by-step instructions).",
     "input_schema": _s({"name": _STR}, ["name"])},
    {"name": "ask_owner", "description": "Ask the owner a question and wait for the answer. Use it when the request is ambiguous or needs their decision.",
     "input_schema": _s({"question": _STR}, ["question"])},
]
TOOL_NAMES = [t["name"] for t in TOOLS]


def tools_for(allowed: Optional[List[str]] = None) -> List[dict]:
    names = set(allowed or TOOL_NAMES)
    return [copy.deepcopy(t) for t in TOOLS if t["name"] in names]


def openai_tools(tools: List[dict]) -> List[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in tools]


def handlers_for(world: World, tools: List[dict]) -> Dict[str, Callable[[dict], str]]:
    """Tool handlers in Friday's shape (one dict argument), bound to `world`.
    Each records the call before it runs, so a refused or failing call still shows."""
    out: Dict[str, Callable[[dict], str]] = {}
    for t in tools:
        name = t["name"]
        fn = getattr(world, name)

        def handler(inp, _name=name, _fn=fn):
            args = dict(inp or {})
            world.calls.append({"tool": _name, "args": args})
            try:
                return _fn(**args)
            except TypeError as e:
                return json.dumps({"error": f"bad arguments for {_name}: {e}"})
        out[name] = handler
    return out
