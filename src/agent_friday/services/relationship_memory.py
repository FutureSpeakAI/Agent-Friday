"""Relationship memory: who the owner has been in touch with, and when.

The people graph knows WHO someone is and how much the owner trusts them. It
has no timeline and no follow-ups, so it cannot answer "who at Acme have I
talked to, and when?" or "remind me to follow up with the recruiter". This
module adds both, and nothing it holds leaves the machine.

WHAT IS RECORDED
----------------
Headers, never content. From Gmail: sender, recipients, cc, date, subject,
thread id and the account it came through. From Calendar: the event title,
its start time and its attendees. No message body, snippet or event
description is stored, and `_email_record` / `_meeting_record` build each
entry from an explicit list of fields so a new field on the provider's side
cannot slip in. Bulk mail (anything with List-Unsubscribe) and automated
senders (no-reply and friends) are not relationships and are skipped.

Storage is under ~/.friday/relationships/, written atomically. The timeline
and follow-ups name people and quote subjects, so they are encrypted at rest
with Friday's keystore (timeline.json.enc, follow_ups.json.enc); config.json
holds only settings and stays plain.

SYNC
----
`tick()` is the scheduler builtin. It syncs each connected account from the
point the last successful sync reached (a Gmail `after:` watermark and a
calendar time, per account), reminds the owner of follow-ups that are due,
and, only when the owner has set a threshold, points out threads where the
owner wrote last and nobody has replied. Every one of those is a local
notification; nothing is sent to anyone.

FORGETTING
----------
A person removed with services/forget_person.py is removed here too: their
address is taken out of every entry, entries left with nobody in them are
dropped, their follow-ups are deleted, and later syncs skip their addresses.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from email.utils import getaddresses
from pathlib import Path

import agent_friday.core as core
from agent_friday.user_errors import ExceptionText

_log = logging.getLogger("friday.relationships")
_LOCK = threading.RLock()

#: Entries kept, newest first. A cap keeps the file small enough to load on
#: every question; the oldest interactions go first.
MAX_INTERACTIONS = 20000
#: Messages read from one account in one sync. The count is reported when it
#: is reached so a partial sync is never presented as a complete one.
MAX_MESSAGES_PER_SYNC = 500
#: How long a cold thread stays eligible for a nudge after it first goes cold.
COLD_WINDOW_DAYS = 14
#: At most this many cold-thread nudges per tick, so turning the feature on
#: over a busy mailbox does not produce a wall of notifications.
MAX_NUDGES_PER_TICK = 3
TEXT_CAP = 200

DEFAULT_CONFIG = {
    "sync_enabled": True,      # read mail and calendar headers into the timeline
    "first_sync_days": 30,     # how far back the first sync of an account reaches
    "cold_after_days": 0,      # 0 = no cold-thread nudges
}

#: Webmail domains say nothing about where someone works.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com",
    "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
})

_AUTOMATED = re.compile(
    r"^(?:no-?reply|do-?not-?reply|donotreply|notifications?|notify|alerts?|"
    r"mailer-daemon|postmaster|bounces?|automated|calendar-notification)(?:[+.\-_].*)?$",
    re.I)
# The domain needs a dot with at least one character on each side. Matching
# up to the FIRST such dot keeps the check linear; `[^@..]+\.[^@..]+$` tried
# every dot in the domain and rescanned the tail each time.
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>][^@\s,;<>.]*\.[^@\s,;<>]+$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ── storage ─────────────────────────────────────────────────────────────────

def _dir() -> Path:
    return Path(core.FRIDAY_DIR) / "relationships"


#: Files that name people and quote email subjects. They are encrypted at rest
#: with Friday's keystore (credential_store.protect) as `<name>.enc`; a plain
#: copy left by an earlier version is read once and replaced.
_ENCRYPTED = frozenset({"timeline.json", "follow_ups.json"})


def _read(name: str, default):
    d = _dir()
    try:
        if name in _ENCRYPTED and (d / (name + ".enc")).exists():
            from agent_friday.services import credential_store as _cs
            raw = _cs.unprotect((d / (name + ".enc")).read_bytes()).decode("utf-8")
        else:
            raw = (d / name).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, type(default)):
            if name in _ENCRYPTED and (d / name).exists():
                _write(name, data)          # migrate the plain copy
            return data
    except Exception:
        pass
    return json.loads(json.dumps(default))


def _write(name: str, data) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    if name in _ENCRYPTED:
        from agent_friday.services import credential_store as _cs
        blob, _method = _cs.protect(text.encode("utf-8"))
        path = d / (name + ".enc")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, path)
        plain = d / name
        if plain.exists():
            plain.unlink()
        return
    path = d / name
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_timeline() -> dict:
    tl = _read("timeline.json", {})
    tl.setdefault("interactions", [])
    tl.setdefault("names", {})
    tl.setdefault("sync", {})
    return tl


def _save_timeline(tl: dict) -> None:
    items = tl.get("interactions") or []
    items.sort(key=lambda e: e.get("at") or "", reverse=True)
    tl["interactions"] = items[:MAX_INTERACTIONS]
    _write("timeline.json", tl)


def _load_follow_ups() -> list:
    return _read("follow_ups.json", {"follow_ups": []}).get("follow_ups") or []


def _save_follow_ups(items: list) -> None:
    _write("follow_ups.json", {"follow_ups": items})


def get_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    stored = _read("config.json", {})
    for k in DEFAULT_CONFIG:
        if k in stored:
            cfg[k] = stored[k]
    return cfg


def set_config(updates: dict) -> dict:
    """Owner's settings. Unknown keys are ignored; numbers are clamped."""
    with _LOCK:
        stored = _read("config.json", {})
        u = updates or {}
        if "sync_enabled" in u:
            stored["sync_enabled"] = bool(u["sync_enabled"])
        if "cold_after_days" in u:
            stored["cold_after_days"] = max(0, min(90, int(u["cold_after_days"] or 0)))
        if "first_sync_days" in u:
            stored["first_sync_days"] = max(1, min(365, int(u["first_sync_days"] or 30)))
        _write("config.json", stored)
    return get_config()


# ── small helpers ───────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        s = str(s).strip()
        if len(s) == 10:
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _clean(s, cap: int = TEXT_CAP) -> str:
    """Untrusted text, kept as text: control characters out, length capped."""
    return _CTRL.sub("", str(s or "")).strip()[:cap]


def _addr_list(raw) -> list:
    """[(name, email)] from a header value or a list of values."""
    if not raw:
        return []
    vals = raw if isinstance(raw, list) else [raw]
    out = []
    for name, addr in getaddresses([str(v) for v in vals]):
        addr = (addr or "").strip().lower()
        if _EMAIL.match(addr):
            out.append((_clean(name, 120).strip('"'), addr))
    return out


def is_automated(addr: str) -> bool:
    local = (addr or "").split("@", 1)[0]
    return bool(_AUTOMATED.match(local)) or "noreply" in local.lower()


def _domain(addr: str) -> str:
    return (addr or "").rsplit("@", 1)[-1].lower()


# ── seams over Google (replaced in tests) ───────────────────────────────────

def _owner_addresses() -> set:
    try:
        from agent_friday.services import google_accounts as ga
        return {(r.get("email") or "").lower() for r in ga.list_accounts() if r.get("email")}
    except Exception:
        return set()


def _sync_accounts(service: str) -> list:
    """Connected, healthy accounts with `service` switched on."""
    try:
        from agent_friday.services import google_accounts as ga
        return [{"id": r.get("id"), "email": r.get("email"), "label": r.get("label")}
                for r in ga._accounts_with(service)]
    except Exception:
        return []


def _credentials(account_id: str):
    from agent_friday.services import google_accounts as ga
    return ga.credentials_for(account_id)


def _fetch_gmail(account: dict, query: str) -> dict:
    """Header metadata for messages matching `query`, oldest first.

    Returns {"messages": [...], "truncated": bool}. Raises on failure so the
    caller keeps the account's watermark where it was.
    """
    creds = _credentials(account["id"])
    if not creds:
        raise RuntimeError("the account needs reconnecting")
    from googleapiclient.discovery import build
    from agent_friday.services import gmail_api
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    ids, token, truncated = [], None, False
    while True:
        kw = {"userId": "me", "q": query, "maxResults": 100}
        if token:
            kw["pageToken"] = token
        resp = gmail_api.execute(svc.users().messages().list(**kw))
        ids += [m.get("id") for m in resp.get("messages", []) if m.get("id")]
        token = resp.get("nextPageToken")
        if not token:
            break
        if len(ids) >= MAX_MESSAGES_PER_SYNC:
            truncated = True
            break
    ids = ids[:MAX_MESSAGES_PER_SYNC]
    got, failed = gmail_api.batch_get(
        svc, ids, fmt="metadata",
        headers=["From", "To", "Cc", "Subject", "Date", "List-Unsubscribe"])
    out = []
    for mid in ids:
        msg = got.get(mid)
        if not msg:
            continue
        headers = {h["name"].lower(): h["value"]
                   for h in (msg.get("payload") or {}).get("headers", [])}
        try:
            at = datetime.fromtimestamp(int(msg.get("internalDate")) / 1000,
                                        tz=timezone.utc).isoformat()
        except Exception:
            continue
        out.append({"gmail_id": mid, "thread_id": msg.get("threadId", ""),
                    "from": headers.get("from", ""), "to": headers.get("to", ""),
                    "cc": headers.get("cc", ""), "subject": headers.get("subject", ""),
                    "at": at, "labels": msg.get("labelIds") or [],
                    "bulk": bool(headers.get("list-unsubscribe"))})
    if failed:
        truncated = True
    out.sort(key=lambda m: m["at"])
    return {"messages": out, "truncated": truncated}


def _fetch_calendar(account: dict, start: datetime, end: datetime) -> list:
    """Events in [start, end) with their attendees. Titles only, no descriptions."""
    creds = _credentials(account["id"])
    if not creds:
        raise RuntimeError("the account needs reconnecting")
    from googleapiclient.discovery import build
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    out, token = [], None
    for _ in range(10):
        kw = {"calendarId": "primary", "timeMin": start.isoformat(),
              "timeMax": end.isoformat(), "singleEvents": True,
              "orderBy": "startTime", "maxResults": 250,
              "fields": "nextPageToken,items(id,summary,start,attendees(email,displayName,self))"}
        if token:
            kw["pageToken"] = token
        resp = svc.events().list(**kw).execute()
        for ev in resp.get("items", []):
            s = ev.get("start") or {}
            out.append({"id": ev.get("id", ""), "title": ev.get("summary", ""),
                        "start": s.get("dateTime") or s.get("date") or "",
                        "attendees": [{"email": a.get("email", ""),
                                       "name": a.get("displayName", ""),
                                       "self": bool(a.get("self"))}
                                      for a in ev.get("attendees") or []]})
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def _push(**kw) -> None:
    """Seam over the local notification queue."""
    try:
        from agent_friday import notifications_engine as ne
        ne.push(**kw)
    except Exception as e:  # noqa: BLE001
        _log.info("relationships: could not push notification (%s)", e)


# ── forgotten people ────────────────────────────────────────────────────────

def _forgotten() -> tuple:
    """(names, emails) tombstoned by forget_person."""
    try:
        from agent_friday.services import forget_person as fp
        return fp.forgotten_names(), fp.forgotten_emails()
    except Exception:
        return set(), set()


# ── recording ───────────────────────────────────────────────────────────────

def _email_record(account_id: str, m: dict, owner: set, names: dict,
                  skip: set, skip_names: set) -> dict | None:
    """One timeline entry from one message's headers, or None to skip it."""
    if m.get("bulk"):
        return None
    frm = _addr_list(m.get("from"))
    to = _addr_list(m.get("to"))
    cc = _addr_list(m.get("cc"))
    if not frm:
        return None
    sender = frm[0][1]
    out = sender in owner or "SENT" in (m.get("labels") or [])
    everyone = frm + to + cc
    for n, a in everyone:
        if a in skip or (n and _norm(n) in skip_names):
            skip.add(a)
    people = []
    for n, a in everyone:
        if a in owner or a in skip or is_automated(a) or a in people:
            continue
        people.append(a)
        if n and a not in names:
            names[a] = n
    if not people:
        return None
    keep = lambda lst: [a for _, a in lst if a not in skip]  # noqa: E731
    return {
        "id": "gmail:%s:%s" % (account_id, m.get("gmail_id")),
        "kind": "email",
        "account_id": account_id,
        "at": m.get("at"),
        "subject": _clean(m.get("subject")),
        "thread_id": str(m.get("thread_id") or ""),
        "direction": "out" if out else "in",
        "from": sender if sender not in skip else "",
        "to": keep(to),
        "cc": keep(cc),
        "people": people,
    }


def _meeting_record(account_id: str, ev: dict, owner: set, names: dict,
                    skip: set, skip_names: set) -> dict | None:
    people = []
    for a in ev.get("attendees") or []:
        addr = (a.get("email") or "").strip().lower()
        n = _clean(a.get("name"), 120)
        if not _EMAIL.match(addr) or a.get("self") or addr in owner:
            continue
        if addr in skip or (n and _norm(n) in skip_names):
            skip.add(addr)
            continue
        # A room or resource booking, by the domain after the @ (not a suffix
        # of the whole address, which would also match a lookalike domain).
        if is_automated(addr) or _domain(addr) == "resource.calendar.google.com":
            continue
        if addr not in people:
            people.append(addr)
            if n and addr not in names:
                names[addr] = n
    at = _parse_dt(ev.get("start"))
    if not people or not at:
        return None
    return {
        "id": "cal:%s:%s:%s" % (account_id, ev.get("id"), at.isoformat()),
        "kind": "meeting",
        "account_id": account_id,
        "at": at.isoformat(),
        "title": _clean(ev.get("title")),
        "event_id": str(ev.get("id") or ""),
        "people": people,
    }


def sync(now: datetime | None = None) -> dict:
    """Bring the timeline up to date from every connected account.

    Incremental: each account resumes from the watermark its last successful
    sync reached. A failed fetch leaves that account's watermark alone and is
    reported, never read as "nothing new".
    """
    now = now or _now()
    cfg = get_config()
    owner = _owner_addresses()
    f_names, f_emails = _forgotten()
    added, errors, truncated = 0, [], []
    with _LOCK:
        tl = _load_timeline()
        seen = {e.get("id") for e in tl["interactions"]}
        names = tl["names"]

        for acct in _sync_accounts("gmail"):
            aid = acct["id"]
            state = tl["sync"].setdefault(aid, {})
            if acct.get("email"):
                owner.add(acct["email"].lower())
            after = state.get("gmail_after")
            q = ("after:%d" % int(after)) if after else "newer_than:%dd" % cfg["first_sync_days"]
            q += " -category:promotions -category:social -category:forums"
            try:
                got = _fetch_gmail(acct, q)
            except Exception as e:
                errors.append({"account_id": aid, "service": "gmail", "error": ExceptionText(str(e)[:200])})
                continue
            high = after or 0
            for m in got.get("messages") or []:
                rec = _email_record(aid, m, owner, names, set(f_emails), f_names)
                dt = _parse_dt(m.get("at"))
                if dt:
                    high = max(high, int(dt.timestamp()))
                if rec and rec["id"] not in seen:
                    tl["interactions"].append(rec)
                    seen.add(rec["id"])
                    added += 1
            if got.get("truncated"):
                truncated.append(aid)
            state["gmail_after"] = high or int(now.timestamp())
            state["gmail_synced_at"] = now.isoformat()

        for acct in _sync_accounts("calendar"):
            aid = acct["id"]
            state = tl["sync"].setdefault(aid, {})
            if acct.get("email"):
                owner.add(acct["email"].lower())
            last = _parse_dt(state.get("calendar_until"))
            # A day of overlap catches events edited after the last sync;
            # ids make the overlap harmless.
            start = (last - timedelta(days=1)) if last else now - timedelta(days=cfg["first_sync_days"])
            try:
                events = _fetch_calendar(acct, start, now)
            except Exception as e:
                errors.append({"account_id": aid, "service": "calendar", "error": ExceptionText(str(e)[:200])})
                continue
            for ev in events:
                rec = _meeting_record(aid, ev, owner, names, set(f_emails), f_names)
                if rec and rec["id"] not in seen:
                    tl["interactions"].append(rec)
                    seen.add(rec["id"])
                    added += 1
            state["calendar_until"] = now.isoformat()

        tl["last_sync"] = now.isoformat()
        _save_timeline(tl)
    return {"added": added, "errors": errors, "truncated_accounts": truncated,
            "total": len(tl["interactions"])}


# ── questions ───────────────────────────────────────────────────────────────

def _people_records() -> dict:
    try:
        from agent_friday.people_graph import PeopleGraph
        people = PeopleGraph(friday_dir=core.FRIDAY_DIR).load().get("people") or {}
        return people if isinstance(people, dict) else {}
    except Exception:
        return {}


def _record_names(p: dict, key: str = "") -> set:
    return {n for n in (_norm(x) for x in [p.get("name"), key.replace("_", " ")]
                        + list(p.get("aliases") or [])) if n}


def resolve(query: str, tl: dict | None = None) -> dict:
    """Who `query` means: {name, emails, record} or {ambiguous: [...]}/{}.

    An address resolves to itself. A name resolves through the people graph
    (its record's emails, plus any address whose display name is that name),
    and otherwise through display names seen in mail and invitations.
    """
    tl = tl or _load_timeline()
    names = tl.get("names") or {}
    q = _norm(query)
    if not q:
        return {}
    people = _people_records()
    if _EMAIL.match(q):
        for key, p in people.items():
            if isinstance(p, dict) and q in [str(e).lower() for e in p.get("emails") or []]:
                return {"name": p.get("name") or key, "emails": {q}, "record": p}
        return {"name": names.get(q) or q, "emails": {q}, "record": None}
    for key, p in people.items():
        if isinstance(p, dict) and q in _record_names(p, key):
            pn = _record_names(p, key)
            emails = {str(e).lower() for e in p.get("emails") or []}
            emails |= {a for a, n in names.items() if _norm(n) in pn}
            return {"name": p.get("name") or key, "emails": emails, "record": p}
    exact = {a for a, n in names.items() if _norm(n) == q}
    if exact:
        return {"name": names[sorted(exact)[0]], "emails": exact, "record": None}
    partial = {}
    for a, n in names.items():
        if q in _norm(n).split(" ") or _norm(n).startswith(q):
            partial.setdefault(_norm(n), (n, set()))[1].add(a)
    if len(partial) == 1:
        n, emails = next(iter(partial.values()))
        return {"name": n, "emails": emails, "record": None}
    if partial:
        return {"ambiguous": sorted(v[0] for v in partial.values())[:10]}
    return {}


def _summary_line(e: dict) -> dict:
    out = {"kind": e.get("kind"), "at": e.get("at"), "account_id": e.get("account_id")}
    if e.get("kind") == "email":
        out.update(subject=e.get("subject"), direction=e.get("direction"),
                   thread_id=e.get("thread_id"))
    else:
        out.update(title=e.get("title"))
    return out


def person_timeline(query: str, limit: int = 20) -> dict:
    """Every recorded interaction with one person, newest first."""
    tl = _load_timeline()
    who = resolve(query, tl)
    if not who:
        return {"found": False, "query": query,
                "note": "No interactions recorded with anyone by that name or address."}
    if "ambiguous" in who:
        return {"found": False, "query": query, "ambiguous": who["ambiguous"],
                "note": "More than one person matches; ask which one."}
    emails = who["emails"]
    hits = [e for e in tl["interactions"] if set(e.get("people") or []) & emails]
    hits.sort(key=lambda e: e.get("at") or "", reverse=True)
    # Heard from = they sent it; being copied on someone else's message is
    # contact, but not a reply from them.
    inbound = [e for e in hits if e.get("kind") == "email" and e.get("from") in emails]
    outbound = [e for e in hits if e.get("kind") == "email" and e.get("direction") == "out"]
    meetings = [e for e in hits if e.get("kind") == "meeting"]
    rec = who.get("record") or {}
    return {
        "found": bool(hits) or bool(rec),
        "name": who["name"],
        "emails": sorted(emails),
        "company": rec.get("company") or "",
        "position": rec.get("position") or "",
        "count": len(hits),
        "emails_received": len(inbound),
        "emails_sent": len(outbound),
        "meetings": len(meetings),
        "first_contact": hits[-1]["at"] if hits else None,
        "last_contact": hits[0]["at"] if hits else None,
        "last_heard_from": inbound[0]["at"] if inbound else None,
        "last_wrote_to": outbound[0]["at"] if outbound else None,
        "interactions": [_summary_line(e) for e in hits[:max(1, min(int(limit or 20), 100))]],
        "follow_ups": [f for f in _load_follow_ups()
                       if f.get("status") == "open"
                       and (set(f.get("emails") or []) & emails
                            or _norm(f.get("person")) == _norm(who["name"]))],
        "last_sync": tl.get("last_sync"),
    }


def _org_matches(domain: str, token: str) -> bool:
    if not domain or domain in FREE_MAIL:
        return False
    if "." in token:
        return domain == token or domain.endswith("." + token)
    labels = domain.split(".")[:-1] or domain.split(".")
    return any(lbl == token or lbl.replace("-", "") == token for lbl in labels)


def people_at(org: str, limit: int = 50) -> dict:
    """People at an organisation: by email domain, or by a contact's company."""
    token = _norm(org).replace(" ", "")
    if not token:
        return {"org": org, "people": [], "note": "Name an organisation or a domain."}
    if "." not in token:
        token = re.sub(r"[^a-z0-9\-]", "", token)
    tl = _load_timeline()
    names = tl.get("names") or {}
    stats = {}
    for e in tl["interactions"]:
        for a in e.get("people") or []:
            if _org_matches(_domain(a), token):
                s = stats.setdefault(a, {"email": a, "name": names.get(a, ""),
                                         "company": "", "interactions": 0,
                                         "last_contact": None})
                s["interactions"] += 1
                if not s["last_contact"] or (e.get("at") or "") > s["last_contact"]:
                    s["last_contact"] = e.get("at")
    org_n = _norm(org)
    for key, p in _people_records().items():
        if not isinstance(p, dict):
            continue
        company = _norm(p.get("company"))
        emails = [str(x).lower() for x in p.get("emails") or []]
        by_company = bool(company) and (org_n in company or company in org_n)
        by_domain = any(_org_matches(_domain(x), token) for x in emails)
        if not (by_company or by_domain):
            continue
        hit = next((stats[x] for x in emails if x in stats), None)
        if hit:
            hit["name"] = p.get("name") or hit["name"]
            hit["company"] = p.get("company") or ""
            continue
        stats["record:" + key] = {"email": emails[0] if emails else "",
                                  "name": p.get("name") or key,
                                  "company": p.get("company") or "",
                                  "interactions": 0, "last_contact": None}
    rows = sorted(stats.values(), key=lambda r: (r["last_contact"] or "", r["interactions"]),
                  reverse=True)
    return {"org": org, "count": len(rows), "people": rows[:max(1, min(int(limit or 50), 200))],
            "last_sync": tl.get("last_sync")}


# ── follow-ups ──────────────────────────────────────────────────────────────

def set_follow_up(person: str, note: str = "", due: str | None = None,
                  in_days=None, now: datetime | None = None) -> dict:
    """A local reminder to get back to someone. Nothing is sent to them."""
    now = now or _now()
    person = _clean(person, 120)
    if not person:
        return {"ok": False, "error": "Say who the follow-up is with."}
    f_names, f_emails = _forgotten()
    who = resolve(person)
    if "ambiguous" in who:
        return {"ok": False, "error": "More than one person matches that name: "
                + ", ".join(who["ambiguous"]) + ". Say which one."}
    name = who.get("name") or person
    emails = sorted(who.get("emails") or [])
    if _norm(name) in f_names or _norm(person) in f_names or set(emails) & f_emails:
        return {"ok": False, "error": "That person was forgotten at the owner's request; "
                "Friday does not keep records about them."}
    if due:
        when = _parse_dt(due)
        if not when:
            return {"ok": False, "error": "Could not read the due date %r; use YYYY-MM-DD." % due}
    else:
        try:
            days = int(in_days) if in_days not in (None, "") else 3
        except (TypeError, ValueError):
            return {"ok": False, "error": "in_days must be a whole number of days."}
        when = now + timedelta(days=max(0, min(days, 365)))
    item = {"id": "fu_" + uuid.uuid4().hex[:10], "person": name, "emails": emails,
            "known": bool(who), "note": _clean(note, 500), "due": when.isoformat(),
            "created": now.isoformat(), "status": "open", "notified": False}
    with _LOCK:
        items = _load_follow_ups()
        items.append(item)
        _save_follow_ups(items)
    return {"ok": True, "follow_up": item}


def list_follow_ups(status: str | None = "open") -> list:
    items = _load_follow_ups()
    if status:
        items = [f for f in items if f.get("status") == status]
    return sorted(items, key=lambda f: f.get("due") or "")


def complete_follow_up(fid: str) -> bool:
    with _LOCK:
        items = _load_follow_ups()
        hit = False
        for f in items:
            if f.get("id") == fid and f.get("status") == "open":
                f["status"] = "done"
                f["done_at"] = _now().isoformat()
                hit = True
        if hit:
            _save_follow_ups(items)
        return hit


def due_follow_ups(now: datetime | None = None) -> list:
    now = now or _now()
    return [f for f in list_follow_ups("open")
            if not f.get("notified") and (_parse_dt(f.get("due")) or now) <= now]


# ── cold threads ────────────────────────────────────────────────────────────

def cold_threads(now: datetime | None = None, days: int | None = None) -> list:
    """Threads where the owner wrote last and nobody has answered for `days`.

    Only threads that went cold within the last COLD_WINDOW_DAYS count, so a
    first run over old mail does not surface every unanswered message ever.
    """
    now = now or _now()
    days = get_config()["cold_after_days"] if days is None else int(days)
    if days <= 0:
        return []
    tl = _load_timeline()
    names = tl.get("names") or {}
    threads = {}
    for e in tl["interactions"]:
        if e.get("kind") == "email" and e.get("thread_id"):
            threads.setdefault((e.get("account_id"), e["thread_id"]), []).append(e)
    out = []
    for (aid, tid), msgs in threads.items():
        msgs.sort(key=lambda e: e.get("at") or "")
        last = msgs[-1]
        if last.get("direction") != "out":
            continue
        at = _parse_dt(last.get("at"))
        if not at:
            continue
        age = (now - at).total_seconds() / 86400
        if not (days <= age < days + COLD_WINDOW_DAYS):
            continue
        ppl = last.get("people") or []
        cands = [a for a in last.get("to") or [] if a in ppl] or ppl
        if not cands:
            continue
        who = cands[0]
        out.append({"account_id": aid, "thread_id": tid, "subject": last.get("subject") or "",
                    "email": who, "name": names.get(who) or who, "days": int(age),
                    "last_id": last.get("id"), "at": last.get("at")})
    out.sort(key=lambda c: c["at"])
    return out


# ── the scheduled tick ──────────────────────────────────────────────────────

def tick(now: datetime | None = None) -> dict:
    """Scheduler builtin: sync, then local reminders. Never raises.

    Reads the owner's own mail and calendar headers and writes the local
    store; its only other effect is local notifications.
    """
    now = now or _now()
    result = {"synced": None, "follow_ups_due": 0, "cold_nudges": 0}
    cfg = get_config()
    try:
        if cfg.get("sync_enabled"):
            result["synced"] = sync(now)
    except Exception as e:  # noqa: BLE001
        _log.warning("relationships: sync failed: %s", e)
        result["sync_error"] = ExceptionText(str(e)[:200])
    try:
        with _LOCK:
            due = {f["id"] for f in due_follow_ups(now)}
            if due:
                items = _load_follow_ups()
                for f in items:
                    if f.get("id") in due:
                        _push(title="Follow up with %s" % f.get("person"),
                              body=f.get("note") or "You asked to be reminded.",
                              priority="medium", source="relationships",
                              kind="follow_up", dedupe_key="follow-up:" + f["id"],
                              target={"workspace": "contacts", "name": f.get("person")})
                        f["notified"] = True
                _save_follow_ups(items)
            result["follow_ups_due"] = len(due)
    except Exception as e:  # noqa: BLE001
        _log.warning("relationships: follow-up reminders failed: %s", e)
    try:
        cold = cold_threads(now)
        if cold:
            with _LOCK:
                stored = _read("config.json", {})
                nudged = stored.setdefault("nudged", {})
                sent = 0
                for c in cold:
                    if sent >= MAX_NUDGES_PER_TICK:
                        break
                    key = "%s:%s" % (c["account_id"], c["thread_id"])
                    if nudged.get(key) == c["last_id"]:
                        continue
                    _push(title="No reply from %s in %d days" % (c["name"], c["days"]),
                          body="On “%s”." % (c["subject"] or "(no subject)"),
                          priority="low", source="relationships", kind="cold_thread",
                          dedupe_key="cold:" + key,
                          target={"workspace": "contacts", "name": c["name"]})
                    nudged[key] = c["last_id"]
                    sent += 1
                _write("config.json", stored)
                result["cold_nudges"] = sent
    except Exception as e:  # noqa: BLE001
        _log.warning("relationships: cold-thread check failed: %s", e)
    return result


# ── forget_person support ───────────────────────────────────────────────────

def emails_for(names, emails=()) -> set:
    """Addresses that belong to a person known by `names` (normalised)."""
    names = {_norm(n) for n in names if _norm(n)}
    out = {str(e).lower() for e in emails or () if e}
    tl = _load_timeline()
    out |= {a for a, n in (tl.get("names") or {}).items() if _norm(n) in names}
    return out


def _mentions(f: dict, names: set, emails: set) -> bool:
    return bool(set(f.get("emails") or []) & emails) or _norm(f.get("person")) in names


def count_for(names, emails) -> dict:
    names = {_norm(n) for n in names if _norm(n)}
    emails = set(emails)
    tl = _load_timeline()
    return {"interactions": sum(1 for e in tl["interactions"]
                                if set(e.get("people") or []) & emails),
            "follow_ups": sum(1 for f in _load_follow_ups() if _mentions(f, names, emails))}


def forget(names, emails) -> dict:
    """Remove a person from the timeline and the follow-ups.

    Their address goes from every field of every entry; an entry left with
    nobody else in it is dropped; their name is scrubbed from the subjects
    and titles that stay. Follow-ups about them are deleted.
    """
    names = {_norm(n) for n in names if _norm(n)}
    emails = set(emails)
    removed = edited = 0
    pattern = None
    words = sorted(names, key=len, reverse=True)
    if words:
        pattern = re.compile(r"\b(?:%s)\b" % "|".join(
            r"\s+".join(re.escape(w) for w in n.split(" ")) for n in words), re.I)
    with _LOCK:
        tl = _load_timeline()
        kept = []
        for e in tl["interactions"]:
            ppl = e.get("people") or []
            if not set(ppl) & emails:
                kept.append(e)
                continue
            rest = [a for a in ppl if a not in emails]
            if not rest:
                removed += 1
                continue
            e = dict(e, people=rest,
                     to=[a for a in e.get("to") or [] if a not in emails],
                     cc=[a for a in e.get("cc") or [] if a not in emails])
            if e.get("from") in emails:
                e["from"] = ""
            for field in ("subject", "title"):
                if e.get(field) and pattern:
                    e[field] = pattern.sub("[removed]", e[field])
            edited += 1
            kept.append(e)
        tl["interactions"] = kept
        tl["names"] = {a: n for a, n in (tl.get("names") or {}).items()
                       if a not in emails and _norm(n) not in names}
        _save_timeline(tl)
        items = _load_follow_ups()
        left = [f for f in items if not _mentions(f, names, emails)]
        if len(left) != len(items):
            _save_follow_ups(left)
    return {"timeline_removed": removed, "timeline_edited": edited,
            "follow_ups_removed": len(items) - len(left)}


# ── LinkedIn connections import ─────────────────────────────────────────────

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = 30000
_LI_URL = re.compile(r"^https://(?:[a-z]{2,3}\.)?(?:www\.)?linkedin\.com/in/[^\s<>\"']+$", re.I)


def import_linkedin_csv(text: str) -> dict:
    """Read LinkedIn's Connections.csv export into the people graph.

    The file is untrusted data. Every cell is kept as plain text (control
    characters removed, length capped) and never interpreted: a cell that
    starts with '=' is stored as those characters, not evaluated. Only a
    linkedin.com/in/ link is kept as a profile URL. Rows without a name are
    skipped and counted. People the owner asked Friday to forget are skipped.
    """
    if not isinstance(text, str):
        return {"ok": False, "error": "The file could not be read as text."}
    if len(text.encode("utf-8", errors="ignore")) > MAX_CSV_BYTES:
        return {"ok": False, "error": "That file is larger than 5 MB; a LinkedIn export is much smaller."}
    text = text.lstrip("﻿")
    lines = text.splitlines()
    head = next((i for i, ln in enumerate(lines[:40])
                 if "first name" in ln.lower() and "last name" in ln.lower()), None)
    if head is None:
        return {"ok": False, "error": "This does not look like LinkedIn's Connections.csv "
                "(no First Name / Last Name header)."}
    reader = csv.reader(io.StringIO("\n".join(lines[head:])))
    try:
        header = [_norm(h) for h in next(reader)]
    except Exception:
        return {"ok": False, "error": "The header row could not be read."}

    def col(row, name):
        try:
            i = header.index(name)
        except ValueError:
            return ""
        return _clean(row[i]) if i < len(row) else ""

    from agent_friday.people_graph import PeopleGraph
    graph = PeopleGraph(friday_dir=core.FRIDAY_DIR)
    f_names, f_emails = _forgotten()
    created = updated = forgotten = 0
    skipped = []
    for n, row in enumerate(reader, start=head + 2):
        if n - head - 1 > MAX_CSV_ROWS:
            skipped.append({"line": n, "reason": "row limit reached"})
            break
        if not any((c or "").strip() for c in row):
            continue
        name = re.sub(r"\s+", " ", (col(row, "first name") + " " + col(row, "last name"))).strip()
        if not name:
            skipped.append({"line": n, "reason": "no name"})
            continue
        email = col(row, "email address").lower()
        if email and not _EMAIL.match(email):
            email = ""
        if _norm(name) in f_names or (email and email in f_emails):
            forgotten += 1
            continue
        url = col(row, "url")
        _, was_new = graph.merge_profile(
            name, emails=[email] if email else [], source="linkedin",
            company=col(row, "company"), position=col(row, "position"),
            linkedin_url=url if _LI_URL.match(url) else "",
            connected_on=col(row, "connected on"))
        if was_new:
            created += 1
        else:
            updated += 1
    return {"ok": True, "created": created, "updated": updated,
            "skipped": skipped[:50], "skipped_count": len(skipped),
            "skipped_forgotten": forgotten}
