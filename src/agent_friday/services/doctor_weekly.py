"""The weekly "send a Doctor report to FutureSpeak?" ask (amendment A3).

A3 rules out telemetry, opt-in included: nothing about Friday's use, crashes,
performance or health leaves the machine by itself. Its one refinement, in the
owner's words: "maybe Friday should ask if it's ok to email those to
futurespeak once a week". The person decides every single time:

* **An ask, never a background send.** Friday asks once and remembers one of
  three answers: "Yes, ask me weekly", "Not now" (asked again in four weeks;
  a question left unanswered counts the same) or "Never ask again" (only the
  owner turns it back on, on the Doctor page). A yes never becomes a send:
  each week is its own decision, and a standing send pre-approval never covers
  it (the send is made outside any tool decision, so no grant applies).
* **Each week, a card** with the exact text that would leave, and Send, Skip
  this week, Stop asking. It sits in Needs you (never interrupting: tier FYI)
  and on the Doctor page, and is never raised twice in one week.
* **The payload is the local Doctor report and nothing else** (services/doctor:
  versions, check states, error signatures, settings drift, performance; no
  conversations, file names, addresses, contacts or vault content), passed
  through the egress PII gate (services/egress_gate.gate_text) as a second
  check. The card shows the literal text after the gate; Send sends exactly
  that text (its SHA-256 is checked), never a fresh one.
* **From the owner's own mail.** Send raises the normal approval card for an
  email from the owner's own Gmail (services/gmail_send.request_send), so it
  lands in their Sent folder and leaves a receipt; nothing goes until they
  approve it there. Without send permission Friday saves a draft (accounts
  connected with Gmail's modify permission) for them to send, and without
  either it shows the text to save and attach by hand. It never asks for a
  wider mail permission for this.
* **To one FutureSpeak address** (FUTURESPEAK_DOCTOR_ADDRESS), with a short
  plain promise of what FutureSpeak does with it (PROMISE), then how long it
  is kept, deletion on request and what the sender's address is for (TERMS).
* **Local-only mode and off the record.** In local-only mode the weekly card is
  not raised unless the owner said yes there too (a separate yes, asked in
  that mode). Off the record, no card is raised and nothing is prepared; and
  in a week that was partly off the record (seen by the quarter-hourly tick),
  error counts and speed figures are left out of the report, since they may
  come from that time.

The owner's answer (and the local-only yes) is read from its signed receipt
in the action ledger, never from a file. `<home>/doctor/weekly.json` holds only
when the card was offered, decided, and when off the record was last seen; a
data-folder writer can make the card appear or skip a week, never make
anything send (every send is the owner's press on the approval card) and
never choose its text: the text shown and sent is kept in this process's
memory only (after a restart it is made again).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from agent_friday.user_errors import UserFacingValueError

#: The one FutureSpeak address the weekly report goes to (owner ruling,
#: amendment A23).
FUTURESPEAK_DOCTOR_ADDRESS = "bugs@futurespeak.ai"

#: What FutureSpeak does with a report, in the owner's words (amendment A23),
#: shown on every card and closing every email.
PROMISE = "FutureSpeak only uses these reports to fix bugs and never shares them."

#: What follows the promise in every email, and under it on the card (owner
#: ruling, amendment A23): how long a report is kept, deletion on request, and
#: what the sender's address is used for.
TERMS = ("A report is kept for at most a year. Reply to this email to have yours deleted. "
         "Your email address is used only to reply to you.")

#: The report's "Sharing" line when the owner chose to email it.
SHARING = "you chose to email this report to FutureSpeak from your own account"

ANSWERS = ("weekly", "not_now", "never")
ASK_AGAIN_S = 28 * 86400.0
#: The privacy gate reads "doctor report" as health information (it is not: it
#: is Friday's own self-check), so the email calls it a diagnostic report.
SUBJECT = "Agent Friday diagnostic report"

_LOCK = threading.Lock()
#: This week's prepared text, in memory only: {"week", "report_id", "text", "sha256"}.
_PREPARED: dict = {}
#: When off the record was last seen in this process (the file can be edited).
_OFF_RECORD_SEEN: list = []
#: How far back an off-the-record session keeps counts and speed out of the report
#: (the error counts read the log's tail and speed covers the process's lifetime).
OFF_RECORD_SHADOW_S = 30 * 86400.0


class WeeklyError(UserFacingValueError):
    """Said plainly to the owner."""


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


def _path() -> Path:
    return _home() / "doctor" / "weekly.json"


def week_key(t: Optional[float] = None) -> str:
    d = datetime.fromtimestamp(time.time() if t is None else t, timezone.utc).isocalendar()
    return "%d-W%02d" % (d[0], d[1])


def _state() -> dict:
    st = {"offered_week": None, "asked_at": None, "decided_week": None,
          "off_record_weeks": [], "last_off_record_at": None, "ledger_unreadable": False}
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(d, dict):
            for k in st:
                if k in d:
                    st[k] = d[k]
    except (OSError, ValueError):
        pass
    if not isinstance(st["off_record_weeks"], list):
        st["off_record_weeks"] = []
    for k in ("asked_at", "last_off_record_at"):
        if not isinstance(st[k], (int, float)) or st[k] > time.time() + 600:
            st[k] = None
    if _OFF_RECORD_SEEN and (not st["last_off_record_at"] or st["last_off_record_at"] < _OFF_RECORD_SEEN[-1]):
        st["last_off_record_at"] = _OFF_RECORD_SEEN[-1]       # the file may have been cleared
    st.update(_answer_on_record())
    return st


def _answer_on_record() -> dict:
    """The owner's newest answer, from its signed receipt in the action ledger
    (only entries whose signature holds count)."""
    out = {"answer": None, "answered_at": None, "local_only_ok": False, "ledger_unreadable": False}
    try:
        from agent_friday.services import action_ledger
        # strict: the archive too (a "never" two years old still holds), and a
        # ledger that cannot be read in full, or has a break, answers "never"
        rows = [e for e in action_ledger.entries(["doctor_weekly"], newest_first=True, strict=True)
                if (e.get("rec") or {}).get("event") == "answer"][:1]
    except Exception:
        out.update(answer="never", ledger_unreadable=True)
        return out
    if rows:
        rec = rows[0].get("rec") or {}
        if rec.get("answer") in ANSWERS:
            out.update(answer=rec["answer"], answered_at=rows[0].get("t"),
                       local_only_ok=rec.get("local_only_too") is True)
    return out


def _note_off_record() -> None:
    """Off the record was on (its end, or a tick while on): counts and speed
    stay out of the report for OFF_RECORD_SHADOW_S."""
    now = time.time()
    _OFF_RECORD_SEEN[:] = [now]
    try:
        with _LOCK:
            st = _state()
            st["last_off_record_at"] = now
            if week_key() not in st["off_record_weeks"]:
                st["off_record_weeks"] = (st["off_record_weeks"] + [week_key()])[-8:]
            _save(st)
    finally:
        _PREPARED.clear()


def install_hooks() -> None:
    """Hear the end of every off-the-record session (services/off_record.on_end)."""
    try:
        from agent_friday.services import off_record
        off_record.on_end(_note_off_record)
    except Exception:
        pass


def _save(st: dict) -> None:
    st = {k: st.get(k) for k in ("offered_week", "asked_at", "decided_week", "off_record_weeks",
                                 "last_off_record_at")}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _local_only() -> bool:
    try:
        from agent_friday.services import cloud_intent
        return cloud_intent.routing_mode() == "local_only"
    except Exception:
        return True                      # unknown: treated as the stricter mode


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return True


def _receipt(event: str, **fields) -> None:
    from agent_friday.services import action_ledger
    action_ledger.try_append("doctor_weekly", dict(fields, event=event))


def status() -> dict:
    """What the Doctor page and Settings show (no report text)."""
    st = _state()
    return {"answer": st["answer"], "ledger_unreadable": st.get("ledger_unreadable", False),
            "local_only": _local_only(), "local_only_ok": st["local_only_ok"],
            "off_record": _off_record(), "address": FUTURESPEAK_DOCTOR_ADDRESS, "promise": PROMISE,
            "terms": TERMS,
            "week": week_key(), "decided_this_week": st["decided_week"] == week_key(),
            "item": next_item(st)}


def next_item(st: Optional[dict] = None, now: Optional[float] = None) -> Optional[str]:
    """"ask" (the one question), "weekly" (this week's card), or None."""
    st = st or _state()
    now = time.time() if now is None else now
    if _off_record():
        return None
    if st["answer"] is None:
        # asked once: a question left unanswered is asked again in four weeks
        at = st.get("asked_at")
        return "ask" if not at or now - at >= ASK_AGAIN_S else None
    if st["answer"] == "not_now":
        at = st.get("answered_at")
        return "ask" if not isinstance(at, (int, float)) or now - at >= ASK_AGAIN_S else None
    if st["answer"] != "weekly":
        return None
    if st["decided_week"] == week_key(now):
        return None
    if _local_only() and not st["local_only_ok"]:
        return None
    return "weekly"


def answer(choice: str, *, local_only_too: bool = False) -> dict:
    """The owner's answer to the ask (or Settings turning it back on/off)."""
    if choice not in ANSWERS:
        raise WeeklyError("Choose: ask me weekly, not now, or never ask again.")
    from agent_friday.services import action_ledger
    # the answer is its signed receipt: if it cannot be written, nothing changed
    action_ledger.append("doctor_weekly", {"event": "answer", "answer": choice,
                                           "local_only_too": bool(local_only_too) and choice == "weekly"},
                         durable=True)
    if choice != "weekly":
        _PREPARED.clear()
    return status()


def _ref(report_id) -> str:
    """The report's short reference (its random part): a full id reads to the
    privacy gate like a date of birth or an ID number."""
    return str(report_id or "").rsplit("-", 1)[-1][:12]


def _render(rep: dict, partly_off_record: bool) -> str:
    from agent_friday.services import doctor
    r = dict(rep, sharing=SHARING)
    # the store inventory (§37.3-1) stays on this PC: the weekly email carries
    # what the owner agreed to (A23), and store sizes were not part of it
    r.pop("stores", None)
    r.pop("known_issues", None)
    r["content"] = "none: versions, checks, error signatures, drift and performance only"
    if partly_off_record:
        r["errors"] = {}
        perf = dict(r.get("performance") or {})
        perf.pop("providers", None)
        perf.pop("action_ledger", None)
        r["performance"] = perf
    text = doctor.render_text(r)
    first, _, rest = text.partition("\n")
    text = "Agent Friday diagnostic report (reference %s)\n%s" % (_ref(rep.get("id")), rest)
    if partly_off_record:
        text += ("\nError counts and speed figures are left out this week: part of it was "
                 "off the record.\n")
    return text


def prepare() -> dict:
    """This week's report text, exactly as it would leave: the Doctor report
    rendered (with its Sharing line saying the owner chose to email it), then
    the egress PII gate. The text is kept with its SHA-256, and Send sends that
    text and no other. Made once a week (the page asks again for the same)."""
    if _off_record():
        raise WeeklyError("Off the record, no report is prepared.")
    st = _state()
    if next_item(st) != "weekly":
        raise WeeklyError("There is no weekly report to look at now.")
    p = dict(_PREPARED)
    if p.get("week") == week_key() and isinstance(p.get("text"), str):
        text, sha, rid = p["text"], p["sha256"], p.get("report_id")
    else:
        from agent_friday.services import doctor, egress_gate
        rep = doctor.build()
        last = st.get("last_off_record_at")
        shadow = (week_key() in st["off_record_weeks"]
                  or bool(last and time.time() - last < OFF_RECORD_SHADOW_S))
        text = egress_gate.gate_text(_render(rep, shadow), provider="futurespeak-mail",
                                     field="doctor_report")
        if not text:
            raise WeeklyError("The privacy check held the whole report back; nothing can be sent.")
        sha, rid = hashlib.sha256(text.encode("utf-8")).hexdigest(), rep.get("id")
        _PREPARED.clear()
        _PREPARED.update(report_id=rid, sha256=sha, week=week_key(), text=text)
    return {"report_id": rid, "text": text, "sha256": sha, "to": FUTURESPEAK_DOCTOR_ADDRESS,
            "subject": "%s (reference %s)" % (SUBJECT, _ref(rid)),
            "promise": PROMISE, "terms": TERMS}


def _prepared_text(st: dict, sha256: str) -> tuple[str, dict]:
    """The text the owner saw (kept in memory; its SHA-256 is what the page
    sent back)."""
    p = dict(_PREPARED)
    text = p.get("text")
    if (not p or p.get("week") != week_key() or not isinstance(text, str)
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != sha256 or p.get("sha256") != sha256):
        raise WeeklyError("The report changed since you looked at it. Look at it again, then send.")
    return text, p


def send(sha256: str, *, account_id: Optional[str] = None) -> dict:
    """The owner pressed Send on this week's card. Raises the normal approval
    card for an email from their own account (nothing leaves until they
    approve it there), or saves a draft, or says how to share by hand."""
    st = _state()
    if st["answer"] != "weekly":
        raise WeeklyError("The weekly ask is off.")
    if st["decided_week"] == week_key():
        raise WeeklyError("This week's report is already decided.")
    if _off_record():
        raise WeeklyError("Off the record, nothing is sent.")
    if _local_only() and not st["local_only_ok"]:
        raise WeeklyError("Friday is in local-only mode; say yes to the weekly ask in this mode first.")
    text, p = _prepared_text(st, sha256)
    subject = "%s (reference %s)" % (SUBJECT, _ref(p.get("report_id")))
    body = text + "\n\n" + PROMISE + "\n" + TERMS + "\n"
    from agent_friday.services import gmail_send
    out: dict = {"to": FUTURESPEAK_DOCTOR_ADDRESS}
    try:
        senders = [a.get("id") for a in (gmail_send.sendable_accounts() or []) if a.get("id")]
    except Exception:
        senders = []
    acct = account_id if account_id in senders else (senders[0] if senders else None)
    done = False
    if acct:
        # outside any tool decision, so no standing send pre-approval can cover
        # it: the owner approves this week's email on its own card
        from agent_friday.governance import action_gate
        tok = action_gate.DECIDED.set(None)
        try:
            r = gmail_send.request_send(to=FUTURESPEAK_DOCTOR_ADDRESS, subject=subject, body=body,
                                        account_id=acct, requested_by="doctor_weekly")
        finally:
            action_gate.DECIDED.reset(tok)
        appr = (r or {}).get("approval") or {}
        out.update(how="approval_card", approval_id=appr.get("approval_id") or appr.get("id"),
                   status=(r or {}).get("status"),
                   detail="Approve the email in Needs you; it is sent from your own account.")
        done = True
    if not done:
        for draft_acct in _draft_accounts():
            try:
                d = gmail_send.save_draft(account_id=draft_acct, to=FUTURESPEAK_DOCTOR_ADDRESS,
                                          subject=subject, body=body)
            except Exception:
                continue
            out.update(how="draft", link=(d or {}).get("link"),
                       detail="A draft is in your Gmail Drafts; send it from there.")
            done = True
            break
    if not done:
        out.update(how="by_hand", report_id=p.get("report_id"), text=body,
                   detail="No mail account Friday can use. Save the text above and attach it yourself.")
    with _LOCK:
        st = _state()
        st["decided_week"] = week_key()
        _save(st)
    _receipt("send_asked", report_id=p.get("report_id"), how=out["how"])
    return out


def _draft_accounts() -> list:
    """Connected accounts that may write drafts (no wider permission is asked)."""
    try:
        from agent_friday.services import google_accounts as G
        need = getattr(G, "GMAIL_MODIFY", None) or getattr(G, "GMAIL_COMPOSE", None)
        return [r.get("id") for r in G.list_accounts()
                if r.get("id") and (need is None or need in (r.get("scopes") or []))]
    except Exception:
        return []


def skip() -> dict:
    with _LOCK:
        st = _state()
        st["decided_week"] = week_key()
        _save(st)
    _PREPARED.clear()
    _receipt("skipped", week=week_key())
    return status()


def tick() -> Optional[dict]:
    """Every quarter hour (the server's backup loop): note a week that was
    partly off the record, then raise this week's quiet card in Needs you when
    one is due (never twice in a week; never interrupting). Sends nothing."""
    if _off_record():
        _note_off_record()
        return None
    st = _state()
    item = next_item(st)
    if item is None or st.get("offered_week") == week_key():
        return None
    try:
        from agent_friday import notifications_engine as ne
        title = ("Share a Doctor report with FutureSpeak?" if item == "ask"
                 else "This week's Doctor report: yours to send or skip")
        body = ("Friday can ask once a week whether to email a Doctor report (no conversations, "
                "files or contacts) to FutureSpeak. You decide each time."
                if item == "ask" else "Look at exactly what would be sent on the Doctor page.")
        ne.push(title=title, body=body, priority="low", source="doctor", kind="doctor_weekly",
                tier="fyi", emitter="L07", dedupe_key="doctor-weekly:%s" % week_key(),
                target={"type": "url", "url": "/doctor#weekly"})
    except Exception:
        return None
    with _LOCK:
        st = _state()
        st["offered_week"] = week_key()
        if item == "ask":
            st["asked_at"] = time.time()
        _save(st)
    return {"offered": item}
