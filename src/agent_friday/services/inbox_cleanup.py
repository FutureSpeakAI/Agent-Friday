"""Clean up your inbox: the first-hour offer (first-run spec, B3).

What it does, in order:

1. **Find.** Read message headers only (never bodies) from the person's own
   mail: a connected Gmail account with read access, or a Google Takeout
   ``.mbox`` file they chose. Twelve months first. Only mail that carries a
   ``List-Unsubscribe`` header counts: that header is what makes a sender a
   mailing list.
2. **Group.** One row per sender domain, with how many messages, how many a
   month, and how many of the newest ones are still unread. "Never opened" is
   approximated by "still unread", and the card says exactly that ("You
   haven't opened any of the last 14"), never "you never read these".
3. **Classify.** A sender is offered the standard one-click unsubscribe
   (RFC 8058) only when all of these hold:
   * the provider's own Authentication-Results header (the first one, from
     ``mx.google.com``; a sender can forge any other) says ``dkim=pass``;
   * the message carries ``List-Unsubscribe-Post: List-Unsubscribe=One-Click``;
   * the https unsubscribe address is on a domain that passed DKIM or on the
     From domain (the host equals it or is a subdomain of it);
   * the sender was never in Spam.
   Otherwise the row says what the person can do instead: unsubscribe on the
   sender's own page (the link is shown, never followed), send an unsubscribe
   email, or, for unauthenticated or spam senders, mark them as spam, because
   unsubscribing tells a spammer the address is live.
4. **Approve.** One batch card; every sender has its own tick; nothing is
   ticked at first; "Tick all" is a button the person presses.
5. **Act.** For each ticked sender, one HTTPS POST with the body
   ``List-Unsubscribe=One-Click`` to that sender's address: dialled through
   the SSRF guard's pinned resolution (no loopback, private, link-local or
   rebinding answers), no cookies, no redirects followed (a redirect is "not
   confirmed", never "done"), the response body never read, one request a
   second. Each one writes a receipt and a line in the egress log, so "what
   left this PC today" shows it.
6. **Verify.** Fourteen days later, the next scan reports any sender that is
   still sending.

The owner's own tick is the approval. When Friday is asked by voice or chat,
the request becomes an approval card listing every sender by name, through
the governance checkpoint; nothing runs until that exact card is approved.

Everything stays on this PC: the state file is encrypted at rest
(credential_store), and no result is reported anywhere.
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from agent_friday.paths import friday_home

_log = logging.getLogger("friday.inbox_cleanup")

_LOCK = threading.RLock()

#: How far back the first scan reads, and the most messages it reads.
WINDOW_DAYS = 365
MAX_MESSAGES = 5000
PAGE_SIZE = 500
#: A sender is offered once this many of its newest messages are unread.
UNREAD_STREAK_MIN = 3
#: How many newest messages per sender are kept to judge the streak.
KEEP_NEWEST = 50
#: Seconds between two unsubscribe requests.
RATE_S = 1.0
#: When "still sending?" is asked, in days after the request.
VERIFY_AFTER_DAYS = 14
#: Legitimate senders have this long to stop (CAN-SPAM: 10 business days).
GRACE_DAYS = 2
#: The authentication service whose verdict is trusted: the mailbox provider's
#: own. Any other Authentication-Results header could have been written by the
#: sender.
TRUSTED_AUTHSERV = ("mx.google.com",)

HEADERS = ("From", "List-Unsubscribe", "List-Unsubscribe-Post", "Authentication-Results", "Date")

STATE_VERSION = 1


# ── storage ──────────────────────────────────────────────────────────────────

def data_dir() -> Path:
    return friday_home() / "inbox_cleanup"


def state_path() -> Path:
    return data_dir() / "state.bin"


def receipts_path() -> Path:
    return data_dir() / "receipts.jsonl"


def _empty() -> dict:
    return {"version": STATE_VERSION, "job": {"state": "idle"}, "senders": {},
            "unsubscribed": {}, "found": {"confirmed": [], "possible": []}}


def load() -> dict:
    with _LOCK:
        p = state_path()
        if not p.exists():
            return _empty()
        from agent_friday.services import credential_store as cs
        try:
            data = json.loads(cs.read_secret(p).decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            _log.warning("inbox clean-up state unreadable (%s); starting empty", type(e).__name__)
            return _empty()
        st = _empty()
        st.update({k: v for k, v in data.items() if k in st})
        return st


def save(st: dict) -> None:
    with _LOCK:
        from agent_friday.services import credential_store as cs
        p = state_path()
        cs.write_secret(p, json.dumps(st, ensure_ascii=False).encode("utf-8"), harden=not p.exists())


def _receipt(event: str, **detail) -> dict:
    """Append one receipt: hash-chained, holding the sender's domain and the
    outcome, never message content."""
    with _LOCK:
        p = receipts_path()
        prev = "0" * 64
        if p.exists():
            try:
                lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
                if lines:
                    prev = json.loads(lines[-1]).get("hash") or prev
            except (OSError, ValueError):
                pass
        body = {"ts": round(time.time(), 3), "event": event, "detail": detail, "prev": prev}
        body["hash"] = hashlib.sha256((prev + json.dumps(body, sort_keys=True)).encode("utf-8")).hexdigest()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(body, sort_keys=True) + "\n")
        return body


def receipts(limit: int = 200) -> list:
    try:
        lines = receipts_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


# ── reading headers ──────────────────────────────────────────────────────────

_ENTRY = re.compile(r"<\s*([^>]+?)\s*>")
_AUTHSERV = re.compile(r"^\s*([A-Za-z0-9.\-]+)")
_DKIM_PASS = re.compile(r"\bdkim=pass\b([^;]*)", re.I)
_DKIM_DOMAIN = re.compile(r"header\.(?:d|i)=@?([A-Za-z0-9.\-]+)", re.I)


def parse_list_unsubscribe(value: str) -> dict:
    """{"https": [...], "mailto": [...]} in the order the list gives them."""
    out = {"https": [], "mailto": []}
    for u in _ENTRY.findall(value or ""):
        u = u.strip()
        low = u.lower()
        if low.startswith("https://"):
            out["https"].append(u)
        elif low.startswith("mailto:"):
            out["mailto"].append(u)
    return out


def is_one_click(post_header: str) -> bool:
    return "list-unsubscribe=one-click" in re.sub(r"\s+", "", str(post_header or "")).lower()


def dkim_pass_domains(auth_results: list) -> set:
    """The domains that passed DKIM, by the mailbox provider's own verdict.

    Only the FIRST Authentication-Results header counts, and only when its
    authentication service is the provider's (TRUSTED_AUTHSERV): a header lower
    down, or from any other service, may have been written by the sender.
    """
    if not auth_results:
        return set()
    first = str(auth_results[0] or "")
    m = _AUTHSERV.match(first)
    if not m or m.group(1).lower() not in TRUSTED_AUTHSERV:
        return set()
    out = set()
    for verdict in _DKIM_PASS.finditer(first):
        for d in _DKIM_DOMAIN.findall(verdict.group(1)):
            out.add(d.lower().strip("."))
    return out


def sender_of(from_header: str) -> dict:
    name, addr = email.utils.parseaddr(str(from_header or ""))
    addr = addr.strip().lower()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    return {"name": (name or "").strip()[:80], "address": addr[:200], "domain": domain[:120]}


def _host_matches(host: str, domains) -> bool:
    host = (host or "").lower().strip(".")
    for d in domains:
        d = (d or "").lower().strip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def normalize(headers: dict, *, msg_id: str = "", ts: float = 0.0,
              unread: bool = False, spam: bool = False) -> dict:
    """One message as the scan uses it. `headers` maps a lower-cased header
    name to a list of values, top to bottom."""
    def first(name):
        v = headers.get(name.lower()) or []
        return str(v[0]) if v else ""
    return {"id": str(msg_id)[:64], "ts": float(ts or 0),
            "from": sender_of(first("From")),
            "list_unsubscribe": first("List-Unsubscribe")[:2000],
            "list_unsubscribe_post": first("List-Unsubscribe-Post")[:200],
            "auth_results": [str(v)[:2000] for v in (headers.get("authentication-results") or [])[:6]],
            "unread": bool(unread), "spam": bool(spam)}


# ── grouping and classifying ─────────────────────────────────────────────────

def _month(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
    except (OverflowError, OSError, ValueError):
        return ""


def add_message(senders: dict, msg: dict) -> None:
    """Fold one normalized message into the per-sender rows (in place)."""
    if not msg.get("list_unsubscribe"):
        return
    frm = msg["from"]
    key = frm["domain"]
    if not key:
        return
    row = senders.setdefault(key, {"domain": key, "names": {}, "addresses": [], "count": 0,
                                   "months": {}, "newest": [], "spam": False,
                                   "evidence": None, "evidence_ts": 0.0})
    row["count"] += 1
    if frm["name"]:
        row["names"][frm["name"]] = row["names"].get(frm["name"], 0) + 1
        if len(row["names"]) > 8:
            keep = sorted(row["names"].items(), key=lambda kv: -kv[1])[:8]
            row["names"] = dict(keep)
    if frm["address"] and frm["address"] not in row["addresses"] and len(row["addresses"]) < 5:
        row["addresses"].append(frm["address"])
    m = _month(msg["ts"])
    if m:
        row["months"][m] = row["months"].get(m, 0) + 1
    row["spam"] = row["spam"] or bool(msg.get("spam"))
    row["newest"].append([msg["ts"], bool(msg["unread"])])
    row["newest"].sort(key=lambda x: -x[0])
    del row["newest"][KEEP_NEWEST:]
    if msg["ts"] >= row["evidence_ts"]:
        row["evidence_ts"] = msg["ts"]
        row["evidence"] = {"id": msg["id"], "from_domain": key,
                           "list_unsubscribe": msg["list_unsubscribe"],
                           "list_unsubscribe_post": msg["list_unsubscribe_post"],
                           "dkim": sorted(dkim_pass_domains(msg["auth_results"]))}


def unread_streak(row: dict) -> int:
    n = 0
    for _ts, unread in row.get("newest") or []:
        if not unread:
            break
        n += 1
    return n


def classify(row: dict) -> dict:
    """How this sender can be left: {"route", "url", "host", "mailto", "why"}.

    route: one_click | site | mailto | spam.
    """
    ev = row.get("evidence") or {}
    ways = parse_list_unsubscribe(ev.get("list_unsubscribe", ""))
    dkim = set(ev.get("dkim") or [])
    if row.get("spam") or not dkim:
        return {"route": "spam", "url": "", "host": "", "mailto": "",
                "why": ("It was in your Spam folder." if row.get("spam") else
                        "Its mail could not be confirmed as really coming from it.")}
    allowed = dkim | {row.get("domain") or ""}
    for url in ways["https"]:
        host = (urlsplit(url).hostname or "").lower()
        if is_one_click(ev.get("list_unsubscribe_post")) and _host_matches(host, allowed):
            return {"route": "one_click", "url": url, "host": host, "mailto": "", "why": ""}
    if ways["https"]:
        url = ways["https"][0]
        return {"route": "site", "url": url, "host": (urlsplit(url).hostname or "").lower(),
                "mailto": "", "why": ("Its unsubscribe page is on another company's address, so I "
                                      "won't contact it for you." if is_one_click(ev.get("list_unsubscribe_post"))
                                      else "It asks you to unsubscribe on its own page.")}
    if ways["mailto"]:
        return {"route": "mailto", "url": "", "host": "", "mailto": ways["mailto"][0][7:][:200],
                "why": "It unsubscribes by email."}
    return {"route": "site", "url": "", "host": "", "mailto": "", "why": "It doesn't say how."}


def _display_name(row: dict) -> str:
    names = row.get("names") or {}
    if names:
        return max(names.items(), key=lambda kv: kv[1])[0]
    return row.get("domain") or ""


def _per_month(row: dict) -> float:
    months = row.get("months") or {}
    if not months:
        return 0.0
    return round(sum(months.values()) / max(1, len(months)), 1)


def card_rows(st: dict | None = None) -> dict:
    """The batch card: senders never opened lately, grouped by what can be done.

    {"one_click": [...], "needs_you": [...], "spam": [...]}; each row has
    id (the domain), name, count, per_month, streak, how (words), and the link
    or address where the person acts themselves. Nothing is ticked.
    """
    st = st or load()
    done = set((st.get("unsubscribed") or {}).keys())
    out = {"one_click": [], "needs_you": [], "spam": []}
    for key, row in (st.get("senders") or {}).items():
        streak = unread_streak(row)
        if streak < UNREAD_STREAK_MIN or key in done:
            continue
        c = classify(row)
        item = {"id": key, "name": _display_name(row), "domain": key, "count": row["count"],
                "per_month": _per_month(row), "streak": streak,
                "streak_words": "You haven't opened any of the last %d." % streak,
                "route": c["route"], "why": c["why"], "url": c["url"] if c["route"] == "site" else "",
                "host": c["host"], "mailto": c["mailto"], "ticked": False}
        if c["route"] == "one_click":
            out["one_click"].append(item)
        elif c["route"] == "spam":
            out["spam"].append(item)
        else:
            out["needs_you"].append(item)
    for k in out:
        out[k].sort(key=lambda r: (-r["per_month"], -r["count"], r["name"]))
    return out


# ── sources ──────────────────────────────────────────────────────────────────

def gmail_accounts() -> list:
    """Connected Google accounts whose granted scopes allow reading mail."""
    try:
        from agent_friday.services import google_accounts as G
        return [{"id": r.get("id"), "email": r.get("email") or r.get("label") or ""}
                for r in G.list_accounts() if G.mail_permissions(r).get("read")]
    except Exception:
        return []


def _gmail_pages(account_id: str, page_token: str = ""):
    """Yield (next_page_token, [normalized messages]) newest first."""
    from agent_friday.services import gmail_api
    from agent_friday.services import gmail_mailbox as gm
    svc = gm._read_svc(account_id)
    token = page_token or None
    while True:
        resp = gmail_api.execute(svc.users().messages().list(
            userId="me", q="newer_than:%dd" % WINDOW_DAYS, maxResults=PAGE_SIZE,
            pageToken=token, includeSpamTrash=False))
        ids = [m.get("id") for m in resp.get("messages") or []]
        got, _failed = gmail_api.batch_get(svc, ids, fmt="metadata", headers=HEADERS)
        batch = []
        for i in ids:
            m = got.get(i)
            if not m:
                continue
            hdrs = {}
            for h in (m.get("payload") or {}).get("headers") or []:
                hdrs.setdefault(str(h.get("name") or "").lower(), []).append(h.get("value") or "")
            labels = set(m.get("labelIds") or [])
            batch.append(normalize(hdrs, msg_id=i, ts=int(m.get("internalDate") or 0) / 1000.0,
                                   unread="UNREAD" in labels, spam="SPAM" in labels))
        token = resp.get("nextPageToken") or ""
        yield token, batch
        if not token:
            return


def _mbox_pages(path: str, start: int = 0):
    """Yield (next_index, [normalized messages]) from a Takeout .mbox file.

    Takeout writes the Gmail labels into X-Gmail-Labels ("Unread", "Spam")."""
    import mailbox
    box = mailbox.mbox(path, create=False)
    keys = box.keys()
    cutoff = time.time() - WINDOW_DAYS * 86400
    i = start
    try:
        while i < len(keys):
            batch = []
            for k in keys[i:i + PAGE_SIZE]:
                try:
                    msg = box.get_message(k)
                except Exception:  # noqa: BLE001
                    continue
                hdrs = {}
                for name in HEADERS:
                    vals = msg.get_all(name) or []
                    if vals:
                        hdrs[name.lower()] = [str(v) for v in vals]
                try:
                    ts = email.utils.parsedate_to_datetime(msg.get("Date") or "").timestamp()
                except (TypeError, ValueError, IndexError):
                    ts = 0.0
                if ts and ts < cutoff:
                    continue
                labels = {x.strip().lower() for x in str(msg.get("X-Gmail-Labels") or "").split(",")}
                batch.append(normalize(hdrs, msg_id=str(k), ts=ts,
                                       unread="unread" in labels, spam="spam" in labels))
            i = min(len(keys), i + PAGE_SIZE)
            yield i if i < len(keys) else "", batch
    finally:
        box.close()


def _pages(source: dict, checkpoint):
    if source.get("kind") == "gmail":
        return _gmail_pages(source["account_id"], checkpoint or "")
    if source.get("kind") == "mbox":
        return _mbox_pages(source["path"], int(checkpoint or 0))
    raise ValueError("unknown source")


# ── the scan job ─────────────────────────────────────────────────────────────

#: Test seam: callable(source, checkpoint) -> iterator of (checkpoint, batch).
PAGES = None
#: Test seam: callable(fn) -> None that runs the scan (default: a thread).
RUNNER = None


def _source_label(source: dict) -> str:
    if source.get("kind") == "gmail":
        return "your Gmail"
    return "your mail export file"


def scan(source: dict, *, resume: bool = False) -> dict:
    """Read headers page by page, saving a checkpoint after each page, so a
    closed app picks up where it stopped. Returns the final job record."""
    pages = PAGES or _pages
    with _LOCK:
        st = load()
        job = st.get("job") or {}
        if resume and job.get("source") == source and job.get("state") in ("scanning", "paused"):
            checkpoint, scanned = job.get("checkpoint"), int(job.get("scanned") or 0)
        else:
            checkpoint, scanned = None, 0
            st["senders"] = {}
        st["job"] = {"state": "scanning", "source": source, "started": job.get("started") or time.time(),
                     "checkpoint": checkpoint, "scanned": scanned, "error": ""}
        save(st)
    try:
        for nxt, batch in pages(source, checkpoint):
            with _LOCK:
                st = load()
                for msg in batch:
                    add_message(st["senders"], msg)
                st["job"]["checkpoint"] = nxt
                st["job"]["scanned"] = int(st["job"].get("scanned") or 0) + len(batch)
                save(st)
                try:
                    from agent_friday.services import visible_jobs
                    visible_jobs.step("Read %d messages" % st["job"]["scanned"])
                except Exception:
                    pass
                if st["job"]["scanned"] >= MAX_MESSAGES:
                    break
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            st = load()
            st["job"].update(state="error", error=_error_words(e))
            save(st)
            _receipt("scan_failed", source=source.get("kind"), reason=type(e).__name__)
            return st["job"]
    with _LOCK:
        st = load()
        st["job"].update(state="ready", finished=time.time(), checkpoint=None)
        _verify_unsubscribed(st)
        save(st)
        rows = card_rows(st)
        _receipt("scan_done", source=source.get("kind"), scanned=st["job"]["scanned"],
                 offered=len(rows["one_click"]), needs_you=len(rows["needs_you"]))
        n = len(rows["one_click"]) + len(rows["needs_you"])
        _notify("Your inbox clean-up list is ready",
                ("%d mailing list%s you haven't opened lately. Open Messages > Clean up to "
                 "choose which go; nothing is unsubscribed until you tick it." % (n, "" if n == 1 else "s"))
                if n else "I didn't find mailing lists you've stopped opening.")
        return st["job"]


def _error_words(e: Exception) -> str:
    msg = getattr(e, "message", "") or ""
    return ("Couldn't read your mail: %s" % msg) if msg else (
        "Couldn't read your mail (%s)." % type(e).__name__)


def start(source: dict, *, resume: bool = False) -> dict:
    """Start (or resume) the scan in the background, as a visible job."""
    if source.get("kind") not in ("gmail", "mbox"):
        raise ValueError("unknown source")
    if source.get("kind") == "mbox":
        p = Path(str(source.get("path") or ""))
        if p.suffix.lower() != ".mbox" or not p.is_file():
            raise ValueError("That isn't a .mbox file Friday can read.")
        source = {"kind": "mbox", "path": str(p)}
    else:
        if source.get("account_id") not in {a["id"] for a in gmail_accounts()}:
            raise ValueError("That account can't be read: connect it with permission to read mail.")
        source = {"kind": "gmail", "account_id": source["account_id"]}

    def run():
        try:
            from agent_friday.services.visible_jobs import visible_job
            with visible_job("Inbox clean-up: reading %s" % _source_label(source), kind="inbox_cleanup",
                             workspace="messages", queued=False,
                             description="Reading message headers only, to find mailing lists you never open."):
                scan(source, resume=resume)
        except Exception as e:  # noqa: BLE001
            _log.warning("inbox clean-up scan failed: %s", type(e).__name__)

    with _LOCK:
        st = load()
        st["job"] = {"state": "scanning", "source": source, "started": time.time(),
                     "checkpoint": (st.get("job") or {}).get("checkpoint") if resume else None,
                     "scanned": (st.get("job") or {}).get("scanned", 0) if resume else 0, "error": ""}
        save(st)
    if RUNNER is not None:
        RUNNER(run)
    else:
        threading.Thread(target=run, name="inbox-cleanup-scan", daemon=True).start()
    return view()


def resume_if_interrupted() -> bool:
    """At start-up: a scan that was running when Friday closed carries on."""
    st = load()
    job = st.get("job") or {}
    if job.get("state") != "scanning" or not job.get("source"):
        return False
    try:
        start(job["source"], resume=True)
        return True
    except Exception as e:  # noqa: BLE001
        _log.warning("could not resume the inbox clean-up: %s", type(e).__name__)
        return False


# ── unsubscribing ────────────────────────────────────────────────────────────

class NotOffered(ValueError):
    pass


#: Test seam: callable(url) -> int status (default: the pinned one-click POST).
POSTER = None
#: Test seam: callable(seconds) (default: time.sleep).
SLEEP = time.sleep


def _post_one_click(url: str) -> int:
    """The one POST RFC 8058 defines; nothing else.

    Dialled through web_safety.pinned so the address that was judged public is
    the address dialled; no cookies (a fresh request each time), no redirects
    followed, the body never read."""
    import requests

    from agent_friday.services import web_safety
    with web_safety.pinned(url):
        r = requests.post(url, data="List-Unsubscribe=One-Click", timeout=12,
                          allow_redirects=False, stream=True,
                          headers={"Content-Type": "application/x-www-form-urlencoded",
                                   "User-Agent": "Friday (List-Unsubscribe one-click)"})
        try:
            return int(r.status_code)
        finally:
            getattr(r, "close", lambda: None)()


def _egress(host: str, action: str, reason: str) -> None:
    try:
        from agent_friday.services import egress_gate
        egress_gate.record_binary_egress(host, "inbox clean-up: one-click unsubscribe",
                                         action=action, reason=reason,
                                         byte_len=len("List-Unsubscribe=One-Click"))
    except Exception:
        pass


def unsubscribe(sender_ids: list, *, decided_by: str) -> dict:
    """Unsubscribe from each sender in `sender_ids` that is offered one-click.

    Every id is re-classified from the stored evidence right now; the caller
    cannot name a URL. Returns {"done": [...], "not_done": [...]} with words."""
    from agent_friday.services import web_safety
    ids = [str(s)[:120] for s in sender_ids or [] if str(s).strip()][:200]
    done, not_done = [], []
    for n, sid in enumerate(dict.fromkeys(ids)):
        with _LOCK:
            st = load()
            row = (st.get("senders") or {}).get(sid)
        if not row:
            not_done.append({"id": sid, "why": "I don't have that sender from the last scan."})
            continue
        c = classify(row)
        name = _display_name(row)
        if c["route"] != "one_click":
            not_done.append({"id": sid, "name": name, "why": c["why"] or "It isn't offered one-click."})
            continue
        ok, why = web_safety.check_url(c["url"])
        if not ok:
            not_done.append({"id": sid, "name": name, "why": "Its unsubscribe address isn't a public web address (%s)." % why})
            _egress(c["host"], "block", "refused by the SSRF guard: %s" % why)
            _receipt("unsubscribe_refused", sender=sid, host=c["host"], reason="ssrf")
            continue
        if n:
            SLEEP(RATE_S)
        try:
            status = (POSTER or _post_one_click)(c["url"])
        except Exception as e:  # noqa: BLE001
            not_done.append({"id": sid, "name": name, "why": "The sender didn't answer (%s)." % type(e).__name__})
            _egress(c["host"], "allow", "one-click POST sent; no answer")
            _receipt("unsubscribe_failed", sender=sid, host=c["host"], reason=type(e).__name__,
                     decided_by=decided_by)
            continue
        _egress(c["host"], "allow", "one-click POST, HTTP %d, approved by %s" % (status, decided_by))
        if 200 <= status < 300:
            with _LOCK:
                st = load()
                st.setdefault("unsubscribed", {})[sid] = {
                    "at": time.time(), "name": name, "host": c["host"], "status": status,
                    "per_month": _per_month(row), "still_sending": None}
                save(st)
            _receipt("unsubscribed", sender=sid, host=c["host"], status=status, decided_by=decided_by)
            done.append({"id": sid, "name": name, "host": c["host"]})
        else:
            words = ("It sent me somewhere else instead of confirming, so I didn't follow it."
                     if 300 <= status < 400 else "It refused (HTTP %d)." % status)
            _receipt("unsubscribe_not_confirmed", sender=sid, host=c["host"], status=status,
                     decided_by=decided_by)
            not_done.append({"id": sid, "name": name, "why": words})
    return {"done": done, "not_done": not_done}


def _verify_unsubscribed(st: dict) -> None:
    """After a scan: for each sender unsubscribed at least VERIFY_AFTER_DAYS
    ago, is it still sending (mail newer than the request + grace)?"""
    now = time.time()
    for sid, rec in (st.get("unsubscribed") or {}).items():
        if now - float(rec.get("at") or 0) < VERIFY_AFTER_DAYS * 86400:
            continue
        row = (st.get("senders") or {}).get(sid) or {}
        newest = max([ts for ts, _u in row.get("newest") or []] or [0])
        rec["still_sending"] = bool(newest > float(rec.get("at") or 0) + GRACE_DAYS * 86400)
        rec["checked_at"] = now


def found(st: dict | None = None) -> dict:
    """The found ledger (first-run spec B4): two lists, never added together.

    confirmed: senders that stopped sending after the request (checked 14 days
    on). possible: senders unsubscribed and not yet checked. Each carries the
    messages a month it used to send."""
    st = st or load()
    confirmed, possible, still = [], [], []
    for sid, rec in (st.get("unsubscribed") or {}).items():
        item = {"id": sid, "name": rec.get("name") or sid, "per_month": rec.get("per_month") or 0}
        if rec.get("still_sending") is True:
            still.append(item)
        elif rec.get("still_sending") is False:
            confirmed.append(item)
        else:
            possible.append(item)
    return {"confirmed": confirmed, "possible": possible, "still_sending": still,
            "confirmed_per_month": round(sum(i["per_month"] for i in confirmed), 1),
            "possible_per_month": round(sum(i["per_month"] for i in possible), 1)}


# ── what the board shows ─────────────────────────────────────────────────────

def view() -> dict:
    st = load()
    job = dict(st.get("job") or {})
    job.pop("checkpoint", None)
    src = job.get("source") or {}
    job["source"] = {"kind": src.get("kind") or "", "label": _source_label(src) if src else ""}
    rows = card_rows(st) if job.get("state") == "ready" else {"one_click": [], "needs_you": [], "spam": []}
    return {"job": job, "rows": rows, "found": found(st), "accounts": gmail_accounts(),
            "archive_hint": ("To archive old mail from a sender yourself, search Gmail for "
                             "from:(sender) older_than:1y and archive the results. I can't "
                             "archive for you yet: that needs a permission to change your mail "
                             "that Friday doesn't ask for.")}


# ── the first-run offer ──────────────────────────────────────────────────────

def offer_starter(st: dict) -> dict:
    """setup_chat.OFFER_STARTER: start once an inbox can be read; otherwise say
    what still works (a guided manual version), once."""
    accounts = gmail_accounts()
    if accounts:
        try:
            start({"kind": "gmail", "account_id": accounts[0]["id"]})
            return {"state": "running",
                    "text": ("I've started on your inbox, reading only the headers of the last "
                             "12 months. You'll find the list in Messages under \"Clean up\" "
                             "when it's ready, usually in 5 to 15 minutes.")}
        except Exception as e:  # noqa: BLE001
            _log.warning("the inbox offer could not start: %s", type(e).__name__)
    return {"state": "waiting_for_email",
            "text": ("No email I can read is connected yet, so the inbox clean-up waits. Connect "
                     "Gmail in Settings > Connections, or download your mail from Google Takeout "
                     "and open the .mbox file in Messages > Clean up; I'll start then.")}


# ── a request from Friday herself: an approval card, never a direct act ───────

APPROVAL_KIND = "governed_action"
HANDLER = "inbox_cleanup"
ACTION = "mail: unsubscribe from mailing lists"


def _detail(ids: list) -> dict:
    return {"handler": HANDLER, "action": "unsubscribe", "senders": sorted(set(ids))}


def propose(sender_ids: list, *, requested_by: str, reason: str = "") -> dict:
    """Put an unsubscribe request Friday was asked to make to the checkpoint.
    Nothing runs until the owner approves that exact card, which lists every
    sender by name; running it passes the checkpoint again and uses the card."""
    from agent_friday.governance import action_gate
    st = load()
    rows = st.get("senders") or {}
    ids = [s for s in dict.fromkeys(str(x) for x in sender_ids or []) if s in rows][:50]
    if not ids:
        return {"status": "error", "message": "None of those senders are in the last scan."}
    detail = _detail(ids)
    listing = "\n".join("- %s (%s)" % (_display_name(rows[s]), s) for s in ids)
    v = action_gate.authorize_external(
        ACTION, detail, requested_by=requested_by or "friday",
        title="Unsubscribe you from %d mailing list%s" % (len(ids), "" if len(ids) == 1 else "s"),
        action_description=("Friday proposes to send each of these the standard one-click "
                            "unsubscribe request:\n%s%s" % (
                                listing, ("\n\nWhy: " + reason.strip()) if reason.strip() else "")),
        description="Nothing is sent unless you approve this card.")
    if v.action == "allow":
        return {"status": "done", "result": unsubscribe(ids, decided_by="owner approval")}
    if v.action == "deny":
        return {"status": "refused", "message": "Friday's governance check held this: %s" % v.reason}
    return {"status": "pending_approval", "message": "Waiting for your approval card."}


def _on_decision(record: dict) -> None:
    detail = record.get("payload") or {}
    if detail.get("handler") != HANDLER or (record.get("status") or "").lower() != "approved":
        return
    try:
        from agent_friday.governance import action_gate
        v = action_gate.authorize_external(ACTION, detail, requested_by="owner approval",
                                           approval_id=record.get("approval_id"))
        if v.action != "allow":
            return
        out = unsubscribe(detail.get("senders") or [], decided_by="owner approval")
        _notify("Inbox clean-up: %d unsubscribed" % len(out["done"]),
                ("%d couldn't be done here; they're listed in Messages > Clean up."
                 % len(out["not_done"])) if out["not_done"] else "Each one has a receipt.")
    except Exception as e:  # noqa: BLE001
        _log.warning("approved unsubscribe failed: %s", type(e).__name__)


def _notify(title: str, body: str) -> None:
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=title, body=body, priority="medium", kind="info", source="mail",
                emitter="E44", dedupe_key="inbox-cleanup")
    except Exception:
        pass


_HOOKS = False


def register_hooks() -> None:
    global _HOOKS
    if _HOOKS:
        return
    try:
        from agent_friday.services import approvals as ap
        ap.register_decision_hook(APPROVAL_KIND, _on_decision)
        _HOOKS = True
    except Exception as e:  # noqa: BLE001
        _log.warning("could not register the inbox clean-up hook: %s", e)
