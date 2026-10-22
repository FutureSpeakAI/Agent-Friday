"""The weekly "send a Doctor report to FutureSpeak?" ask (amendment A3).

A3 rules out telemetry, opt-in included: nothing about Friday's use, crashes,
performance or health leaves the machine by itself. Its one refinement, in the
owner's words: "maybe Friday should ask if it's ok to email those to
futurespeak once a week". The person decides every single time:

* **An ask, never a background send.** Friday asks once and remembers one of
  three answers: "Yes, ask me weekly", "Not now" (asked again in four weeks)
  or "Never ask again" (only Settings turns it back on). A yes never becomes a
  send: each week is its own decision.
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
  approve it there. Without send permission Friday saves a draft (Gmail's
  compose scope) for them to send, and without either it offers the file to
  save and attach by hand. It never asks for a wider mail permission for this.
* **To one FutureSpeak address** (FUTURESPEAK_DOCTOR_ADDRESS), with a short
  plain promise of what FutureSpeak does with it (PROMISE).
* **Local-only mode and off the record.** In local-only mode the weekly card is
  not raised unless the owner said yes there too (a separate yes, asked in
  that mode). Off the record, no card is raised and nothing is prepared.

State is `<home>/doctor/weekly.json`. A data-folder writer can make the card
appear; it can never make anything send (every send is the owner's press on
the approval card).
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

#: The one FutureSpeak address the weekly report goes to.
FUTURESPEAK_DOCTOR_ADDRESS = "doctor@futurespeak.ai"

#: What FutureSpeak does with a report, in plain words (shown on every card).
PROMISE = ("FutureSpeak reads these reports only to find and fix problems in Agent Friday. "
           "A report holds version numbers, which checks passed, counts of error types, "
           "which settings differ from the defaults and speed figures; never your "
           "conversations, files, contacts or vault. We keep it no longer than a year, "
           "never sell or share it, never link it to anything else about you, and delete "
           "it when you ask (reply to the email).")

ANSWERS = ("weekly", "not_now", "never")
ASK_AGAIN_S = 28 * 86400.0
SUBJECT = "Agent Friday Doctor report"

_LOCK = threading.Lock()


class WeeklyError(ValueError):
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
    st = {"answer": None, "answered_at": None, "local_only_ok": False, "offered_week": None,
          "decided_week": None, "prepared": None}
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(d, dict):
            for k in st:
                if k in d:
                    st[k] = d[k]
    except (OSError, ValueError):
        pass
    if st["answer"] not in ANSWERS:
        st["answer"] = None
    if not isinstance(st["prepared"], dict):
        st["prepared"] = None
    st["local_only_ok"] = st["local_only_ok"] is True
    return st


def _save(st: dict) -> None:
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
    return {"answer": st["answer"], "local_only": _local_only(), "local_only_ok": st["local_only_ok"],
            "off_record": _off_record(), "address": FUTURESPEAK_DOCTOR_ADDRESS, "promise": PROMISE,
            "week": week_key(), "decided_this_week": st["decided_week"] == week_key(),
            "item": next_item(st)}


def next_item(st: Optional[dict] = None, now: Optional[float] = None) -> Optional[str]:
    """"ask" (the one question), "weekly" (this week's card), or None."""
    st = st or _state()
    now = time.time() if now is None else now
    if _off_record():
        return None
    if st["answer"] is None:
        return "ask"
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
    with _LOCK:
        st = _state()
        st.update(answer=choice, answered_at=time.time(),
                  local_only_ok=bool(local_only_too) if choice == "weekly" else False)
        if choice != "weekly":
            st["prepared"] = None
        _save(st)
    _receipt("answer", answer=choice, local_only_too=bool(local_only_too) and choice == "weekly")
    return status()


def prepare() -> dict:
    """This week's report text, exactly as it would leave: the Doctor report
    rendered, then the egress PII gate. Kept with its SHA-256 so Send sends
    this text and no other."""
    if _off_record():
        raise WeeklyError("Off the record, no report is prepared.")
    from agent_friday.services import doctor, egress_gate
    rep = doctor.save()
    text = doctor.render_text(rep)
    gated = egress_gate.gate_text(text, provider="futurespeak-mail", field="doctor_report")
    if not gated:
        raise WeeklyError("The privacy check held the whole report back; nothing can be sent.")
    sha = hashlib.sha256(gated.encode("utf-8")).hexdigest()
    with _LOCK:
        st = _state()
        st["prepared"] = {"report_id": rep.get("id"), "sha256": sha, "week": week_key(),
                          "at": time.time()}
        _save(st)
    return {"report_id": rep.get("id"), "text": gated, "sha256": sha,
            "changed_by_gate": gated != text, "to": FUTURESPEAK_DOCTOR_ADDRESS,
            "subject": "%s %s" % (SUBJECT, rep.get("id")), "promise": PROMISE}


def _prepared_text(st: dict, sha256: str) -> tuple[str, dict]:
    p = st.get("prepared") or {}
    if not p or p.get("sha256") != sha256 or p.get("week") != week_key():
        raise WeeklyError("The report changed since you looked at it. Look at it again, then send.")
    from agent_friday.services import doctor, egress_gate
    rid = str(p.get("report_id") or "")
    f = doctor.saved(rid, "json")
    try:
        rep = json.loads(f.read_text(encoding="utf-8")) if f else None
    except (OSError, ValueError):
        rep = None
    if not isinstance(rep, dict):
        raise WeeklyError("This week's report is no longer on this PC. Prepare it again.")
    text = egress_gate.gate_text(doctor.render_text(rep), provider="futurespeak-mail",
                                 field="doctor_report")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != sha256:
        raise WeeklyError("The report changed since you looked at it. Look at it again, then send.")
    return text, p


def send(sha256: str, *, account_id: Optional[str] = None) -> dict:
    """The owner pressed Send on this week's card. Raises the normal approval
    card for an email from their own account (nothing leaves until they
    approve it there), or saves a draft, or says how to share by hand."""
    st = _state()
    if st["answer"] != "weekly":
        raise WeeklyError("The weekly ask is off.")
    if _off_record():
        raise WeeklyError("Off the record, nothing is sent.")
    if _local_only() and not st["local_only_ok"]:
        raise WeeklyError("Friday is in local-only mode; say yes to the weekly ask in this mode first.")
    text, p = _prepared_text(st, sha256)
    subject = "%s %s" % (SUBJECT, p.get("report_id"))
    body = text + "\n\n" + PROMISE + "\n"
    from agent_friday.services import gmail_send
    out: dict = {"to": FUTURESPEAK_DOCTOR_ADDRESS}
    try:
        senders = [a.get("id") for a in (gmail_send.sendable_accounts() or []) if a.get("id")]
    except Exception:
        senders = []
    acct = account_id if account_id in senders else (senders[0] if senders else None)
    done = False
    if acct:
        r = gmail_send.request_send(to=FUTURESPEAK_DOCTOR_ADDRESS, subject=subject, body=body,
                                    account_id=acct, requested_by="doctor_weekly")
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
        out.update(how="by_hand", report_id=p.get("report_id"),
                   detail="No mail account Friday can use. Save the report and attach it yourself.")
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
        st["prepared"] = None
        _save(st)
    _receipt("skipped", week=week_key())
    return status()


def tick() -> Optional[dict]:
    """Once a day: raise this week's quiet card in Needs you when one is due
    (never twice in a week; never interrupting)."""
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
        _save(st)
    return {"offered": item}
