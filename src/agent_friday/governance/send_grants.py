"""Pre-approved sends: the owner's standing yes for named recipients.

Outward sends ask by default: every email is a card. The owner can pre-approve,
in Settings, sending to specific recipients, or a specific scheduled job's or
skill's sends to the recipients it names, so a weekly email does not stall at
7am waiting for a click (the no-pestering rule: approve once, in a batch).

What a grant is:

* recipients: exact addresses, or "@domain" for everyone at one domain. A
  grant always names recipients; there is no "anyone" grant. An "@domain"
  grant is refused, for every scope, for a public suffix (@co.uk, @com.au)
  and for a free-mail domain (@gmail.com, @outlook.com ...): anyone can get
  an address there, so it would name nobody in particular. An address with a
  character outside ASCII is never covered and cannot be granted: case
  folding could make a lookalike match ("\\u212aelly" lower-cases to "kelly").
* actions: what it covers. Today: "send_email" (draft_email -> gmail_send).
* scope: "recipients" (any send to them), "job" (only that scheduled job's
  runs) or "skill" (only while that skill's procedure is in use: read in the
  current turn or task, `note_skill_read`, for SKILL_IN_USE_SECONDS at most).
* what it was granted FOR: a job grant keeps a fingerprint of the job's
  content when it was made (its prompt, or its workflow's steps, inputs,
  output and seat: `job_fingerprint`), and a skill grant a fingerprint of the
  skill's procedure and declared permissions (`skill_registry.fingerprint`).
  Both are inside the signed row. Once the job or skill is edited the grant no
  longer covers it; Settings says "changed after you pre-approved it" and
  offers a one-press re-approve (`reapprove`). Changing a workflow or
  schedule that a live grant covers is itself an outward action (a card:
  `edit_touches_granted_job`, used by action_gate.classify).

A skill's own `permissions` field (skill_registry) says what the skill NEEDS.
It grants nothing: `create_for_skill` turns it into a grant only when the
owner presses the button in Settings, and only if the recipient list the
owner saw on that page is exactly what the skill declares at that moment: a
skill rewritten after the page loaded is refused (409, with the new list)
rather than approved unseen. The grant keeps that list; a later edit to the
skill cannot widen it.

Where it is checked: governance/action_gate.py `_decide` (`covering_tool`)
for the tool call, again by gmail_send for the exact message it is about
to queue (`verify`), and once more by gmail_send.send just before the
message leaves, so a grant revoked during the undo window or before a
scheduled time is not used. Revoking a grant also takes back every message
still waiting under it. A send not covered asks, exactly as before.

Only a CLEAN call uses a grant: nothing in it (recipient, subject or body)
came from outside content, and nothing from outside content was read by the
conversation or task behind it, ever (governance/outside_content keeps that
as a lasting flag on the conversation and task records, inherited by the
tasks they spawn), nor recalled into this turn's prompt. Otherwise the send
gets its card, which says why (OUTSIDE_CONTENT_WHY), and the log records the
grant as withheld. Two more conditions (`use_refusal`): a request made by
voice uses no pre-approval unless the owner turned "voice may use
pre-approvals" on (default off), and the subject and body must screen as
TIER_1 by the sensitivity classifier the egress gate uses. Every covered send,
from chat, voice or a background run, tells the owner at once with an Undo.

A revoked grant stays revoked: a grant id with a "revoked" event in
send_grants_log.jsonl is not used even if send_grants.json is restored from an
earlier copy, and a grant id that appears more than once in the file is not
used at all (`revoke` revokes every row with that id).

Every grant row is signed (HMAC-SHA256) with the governance key the decision
receipts use (action_gate._governance_key). A row whose signature is missing
or does not match, say one written into send_grants.json by anything but
these functions, is ignored by `covering` and `verify` and logged once.

Every grant made, used, withheld, refused and revoked is appended to
send_grants_log.jsonl (who, what, when); grants are revocable at any time and
listed in Settings and on the Approvals card. Owner-only: no tool creates or
revokes a grant.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import re
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Optional

from agent_friday.user_errors import UserFacingValueError as _Refused

_LOCK = threading.RLock()
_log_py = logging.getLogger("friday.send_grants")

#: Actions a send grant can cover, in the owner's words.
SEND_ACTIONS = {"send_email": "send email"}
#: Tools that send, the action each needs, and where its recipients are.
TOOL_ACTIONS = {"draft_email": ("send_email", ("to", "cc", "bcc"))}
SCOPES = ("recipients", "job", "skill")

#: Why a covered send still gets its card when it drew on outside content.
OUTSIDE_CONTENT_WHY = ("this message drew on outside content, so the "
                       "pre-approval does not cover it")

#: What the owner is told when a queued message's pre-approval is gone.
REVOKED_WHY = "the pre-approval was revoked, so this was not sent"

#: Set around a tool's handler (agent._execute_tool) when the checkpoint found
#: a grant naming these recipients but withheld it: the reason, for the card.
WITHHELD: ContextVar = ContextVar("friday_send_grant_withheld", default=None)

# Unambiguous shapes (a domain label never contains a dot), so matching is
# linear: "local@label.label[.label...]" and "@label.label[...]".
_LABEL = r"[^@\s,;.]{1,63}"
_ADDR = re.compile(r"^[^@\s,;]{1,64}@" + _LABEL + r"(?:\." + _LABEL + r"){1,8}$")
_DOMAIN = re.compile(r"^@" + _LABEL + r"(?:\." + _LABEL + r"){1,8}$")

#: Free-mail providers: anyone can have an address there.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "ymail.com", "rocketmail.com", "icloud.com", "me.com",
    "mac.com", "aol.com", "proton.me", "protonmail.com", "protonmail.ch", "pm.me",
    "mail.com", "mail.ru", "inbox.ru", "list.ru", "bk.ru", "rambler.ru", "ya.ru",
    "qq.com", "163.com", "126.com", "yeah.net", "sina.com", "web.de",
    "fastmail.com", "fastmail.fm", "zoho.com", "zohomail.com", "tuta.io",
    "tuta.com", "hey.com", "gmx.com", "gmx.net", "hushmail.com", "mailfence.com",
    "posteo.de", "seznam.cz", "naver.com", "daum.net", "rediffmail.com"})
#: Free-mail providers that run one domain per country (yahoo.co.uk, gmx.de,
#: yandex.ru, tutanota.de, hotmail.fr, outlook.de ...): every domain that
#: starts with one of these names.
FREE_MAIL_FAMILIES = ("yahoo.", "gmx.", "yandex.", "tutanota.", "hotmail.", "outlook.",
                      "windowslive.")
#: Common multi-part public suffixes (registrable names sit one level below).
PUBLIC_SUFFIXES = frozenset({
    "co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "ac.uk", "gov.uk",
    "nhs.uk", "sch.uk", "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "id.au", "co.nz", "org.nz", "net.nz", "govt.nz", "ac.nz", "co.jp", "ne.jp",
    "or.jp", "ac.jp", "go.jp", "co.za", "org.za", "gov.za", "com.br", "net.br",
    "org.br", "gov.br", "com.cn", "net.cn", "org.cn", "gov.cn", "com.mx",
    "org.mx", "gob.mx", "com.ar", "com.tr", "gov.tr", "co.in", "net.in",
    "org.in", "gov.in", "ac.in", "co.kr", "or.kr", "go.kr", "com.sg", "edu.sg",
    "gov.sg", "com.hk", "org.hk", "gov.hk", "com.tw", "org.tw", "gov.tw",
    "co.il", "org.il", "gov.il", "ac.il", "co.id", "or.id", "go.id",
    "com.my", "gov.my", "com.ph", "gov.ph", "com.pk", "gov.pk", "com.sa",
    "gov.sa", "com.eg", "gov.eg", "com.ng", "gov.ng", "co.ke", "or.ke",
    "com.ua", "gov.ua", "co.th", "or.th", "go.th", "ac.th", "com.vn", "gov.vn",
    "com.co", "gov.co", "com.pe", "gob.pe", "com.ec", "com.uy", "com.ve",
    "com.pl", "net.pl", "org.pl", "co.at", "or.at", "gv.at", "com.es",
    "org.es", "gob.es", "com.pt", "com.gr", "com.ru", "org.ru", "co.ua",
    "eu.com", "uk.com", "us.com", "github.io", "gitlab.io", "blogspot.com",
    "herokuapp.com", "appspot.com", "azurewebsites.net", "cloudfront.net",
    "netlify.app", "vercel.app", "pages.dev", "workers.dev", "web.app",
    "firebaseapp.com"})


def domain_refusal(domain: str) -> str:
    """Why an "@domain" grant for `domain` is refused, in plain words, or ""."""
    d = str(domain or "").strip().lower().lstrip("@").rstrip(".")
    if not d or "." not in d:
        return "@%s is a whole top-level domain, not one organisation" % d
    if d in FREE_MAIL or d.startswith(FREE_MAIL_FAMILIES):
        return ("@%s is a free-mail provider: anyone can get an address there, so "
                "pre-approve the exact address instead" % d)
    if d in PUBLIC_SUFFIXES:
        return ("@%s is a public suffix shared by many organisations, so "
                "pre-approve the organisation's own domain or the exact address" % d)
    return ""


def _dir():
    from agent_friday.governance.action_gate import _gov_dir
    return _gov_dir()


def _path():
    return _dir() / "send_grants.json"


def _log_path():
    return _dir() / "send_grants_log.jsonl"


# ── signed rows ─────────────────────────────────────────────────────────────

def _key() -> bytes:
    from agent_friday.governance.action_gate import _governance_key
    return _governance_key()


def _sig(g: dict) -> str:
    body = {k: v for k, v in g.items() if k != "sig"}
    canonical = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return _hmac.new(_key(), canonical, hashlib.sha256).hexdigest()


def _signed_ok(g: dict) -> bool:
    sig = g.get("sig")
    if not isinstance(sig, str) or not sig:
        return False
    try:
        return _hmac.compare_digest(_sig(g), sig)
    except Exception:
        return False


_WARNED_UNSIGNED: set = set()


def _trusted(g: dict) -> bool:
    """A grant row is used only if its signature holds. One that does not is
    ignored and logged (once per row per process)."""
    if _signed_ok(g):
        return True
    gid = str(g.get("grant_id") or "?")
    if gid not in _WARNED_UNSIGNED:
        _WARNED_UNSIGNED.add(gid)
        _log_py.warning("send grant %s is unsigned or its signature does not match; "
                        "it is ignored", gid)
        try:
            _log("ignored", grant_id=gid, by="friday",
                 reason="unsigned or bad signature: the row was not written by Settings")
        except Exception:
            pass
    return False


def _read() -> list:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write(rows: list) -> None:
    for g in rows:
        # Re-sign only rows that were trustworthy when read (or are new):
        # writing must never launder a tampered row into a signed one.
        if g.pop("_resign", False):
            g["sig"] = _sig(g)
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    tmp.replace(p)


def _log(event: str, **fields) -> None:
    row = {"at": time.time(), "event": event, **fields}
    with open(_log_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def recipient_refusal(r) -> Optional[str]:
    """Why one recipient can never be pre-approved, in plain words, or None.
    A plain function (no exception), so a caller that shows the reason does
    not carry an exception's text to a page."""
    r = str(r or "").strip()
    if not r:
        return None
    if not r.isascii():
        # Case folding is not identity outside ASCII ("K".lower() is "k"),
        # so such an address always gets a card and is never pre-approved.
        return ("%s has characters outside plain ASCII; such an address "
                "always gets a card, so it cannot be pre-approved" % r)
    low = r.lower()
    if not (_ADDR.match(low) or _DOMAIN.match(low)):
        return "not an email address or @domain: %s" % low
    if low.startswith("@"):
        return domain_refusal(low) or None
    return None


def normalize_recipients(raw) -> list:
    """Lower-cased addresses and "@domain" patterns; refuses anything else,
    and an "@domain" that names no one organisation (`domain_refusal`)."""
    if isinstance(raw, str):
        raw = re.split(r"[,;\s]+", raw)
    out = []
    for r in raw or []:
        r = str(r or "").strip()
        if not r:
            continue
        why = recipient_refusal(r)
        if why:
            raise _Refused(why)
        r = r.lower()
        if r not in out:
            out.append(r)
    return out


def _covers(patterns, address) -> bool:
    a = str(address or "").strip()
    if not a or not a.isascii():
        return False                      # a non-ASCII address always gets a card
    a = a.lower()
    return any(a == p or (p.startswith("@") and a.endswith(p)) for p in patterns or [])


# ── what a job or skill grant was made for ──────────────────────────────────

#: The fields of a saved workflow that decide what its runs do. Its
#: `revision` is not one: the scheduler itself bumps it when it stamps the
#: owner's conversation on a first run, which changes nothing a run does,
#: while every edit that does change one of these.
_WORKFLOW_CONTENT = ("steps", "inputs", "success_criteria", "output", "seat")


def job_fingerprint(schedule_id: str) -> Optional[str]:
    """A fingerprint of what a scheduled job runs: its task (the prompt, or
    which workflow, and its model pin), and for a workflow the content of the
    workflow itself. None when there is no such job."""
    sid = str(schedule_id or "")
    if not sid:
        return None
    from agent_friday.services import scheduler as _sched
    rec = _sched.get_schedule(sid)
    if not rec:
        return None
    task = dict(rec.get("task") or {})
    body = {"task": task}
    if task.get("kind") == "workflow" and task.get("ref"):
        from agent_friday.services import agent as _agent
        chain = _agent.load_workflow_chain(str(task["ref"])) or {}
        body["workflow"] = {k: chain.get(k) for k in _WORKFLOW_CONTENT}
    canonical = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _skill_fingerprint(name: str) -> Optional[str]:
    from agent_friday import skill_registry
    sk = skill_registry.get_skill(name)
    return skill_registry.fingerprint(sk) if sk is not None else None


#: What Settings says about a job or skill grant whose job or skill changed.
JOB_CHANGED = "the job changed after you pre-approved it"
SKILL_CHANGED = "the skill changed after you approved it"


def changed(g: dict) -> str:
    """Why this job or skill grant no longer covers its job or skill (it was
    edited, or is gone), or "" when it still matches what was approved."""
    try:
        if g.get("scope") == "job":
            now = job_fingerprint(g.get("schedule_id") or "")
            if now is None:
                return "the scheduled job no longer exists"
            return "" if now == g.get("job_hash") else JOB_CHANGED
        if g.get("scope") == "skill":
            now = _skill_fingerprint(g.get("skill") or "")
            if now is None:
                return "the skill is no longer installed"
            return "" if now == g.get("skill_hash") else SKILL_CHANGED
    except Exception as e:
        return "it could not be checked (%s)" % e
    return ""


def create(*, scope: str, recipients, actions=("send_email",), schedule_id: str = "",
           skill: str = "", created_by: str = "owner", note: str = "",
           skill_hash: str = "") -> dict:
    """Make a standing send grant. Owner-only: called from the owner's
    authenticated Settings route, never from a tool. A job grant records the
    fingerprint of the job as it is now; a skill grant that of the skill
    (`create_for_skill` passes the one the owner's list was checked against)."""
    if scope not in SCOPES:
        raise _Refused("scope must be one of: " + ", ".join(SCOPES))
    acts = sorted({str(a) for a in actions or []})
    if not acts or any(a not in SEND_ACTIONS for a in acts):
        raise _Refused("actions must be among: " + ", ".join(SEND_ACTIONS))
    rcpts = normalize_recipients(recipients)
    if not rcpts:
        raise _Refused("a pre-approval names at least one recipient (or @domain)")
    if scope == "job" and not schedule_id:
        raise _Refused("a job pre-approval names the scheduled job")
    if scope == "skill" and not skill:
        raise _Refused("a skill pre-approval names the skill")
    job_hash = None
    if scope == "job":
        job_hash = job_fingerprint(schedule_id)
        if job_hash is None:
            raise _Refused("there is no scheduled job %s to pre-approve" % schedule_id)
    if scope == "skill":
        skill_hash = skill_hash or _skill_fingerprint(skill) or ""
        if not skill_hash:
            raise _Refused("no skill called %r" % skill)
    g = {"grant_id": "sgr_" + uuid.uuid4().hex[:10], "scope": scope, "actions": acts,
         "recipients": rcpts, "schedule_id": str(schedule_id or "") or None,
         "skill": str(skill or "") or None, "created_by": created_by,
         "created_at": time.time(), "note": str(note or "")[:200],
         "revoked_at": None, "revoked_by": None, "uses": 0, "last_used_at": None,
         **({"job_hash": job_hash} if scope == "job" else {}),
         **({"skill_hash": skill_hash} if scope == "skill" else {})}
    with _LOCK:
        rows = _read()
        rows.append(dict(g, _resign=True))
        _write(rows)
        _log("created", grant_id=g["grant_id"], by=created_by, scope=scope, actions=acts,
             recipients=rcpts, schedule_id=g["schedule_id"], skill=g["skill"])
    return get(g["grant_id"]) or g


class SkillChanged(_Refused):
    """The skill's declared needs are not what the owner saw. `needs` is the
    current declaration, so the page can show it."""

    def __init__(self, msg: str, needs: dict):
        super().__init__(msg, status=409)
        self.needs = needs


def create_for_skill(name: str, seen_recipients, created_by: str = "owner") -> dict:
    """Batch-approve, once, everything a skill declares it needs: only if the
    recipients the owner saw (`seen_recipients`, sent by the page) are exactly
    what the skill declares now."""
    from agent_friday import skill_registry
    sk = skill_registry.get_skill(name)
    if sk is None:
        raise _Refused("no skill called %r" % name)
    perms = skill_registry.send_permissions(sk)
    if not perms:
        raise _Refused("the skill %r declares no send permissions" % name)
    declared = sorted({r for rs in perms.values() for r in rs})
    if not seen_recipients:
        raise _Refused("say which recipients you are approving for %s (the list "
                         "shown in Settings)" % sk.name)
    seen = sorted(set(normalize_recipients(seen_recipients)))
    if seen != declared:
        _log("refused", by=created_by, skill=sk.name, seen=seen, declared=declared,
             reason="the skill's declared recipients changed after the page loaded")
        raise SkillChanged("%s now asks to send to %s, not what you saw. Nothing was "
                           "approved; look again and approve if you still want to."
                           % (sk.name, ", ".join(declared)), perms)
    return create(scope="skill", skill=sk.name, actions=sorted(perms), recipients=declared,
                  created_by=created_by, note="what %s declares it needs" % sk.name,
                  skill_hash=skill_registry.fingerprint(sk))


def reapprove(grant_id: str, seen_recipients=None, by: str = "owner") -> dict:
    """One press, from Settings: approve a job or skill grant again for the
    job or skill as it is now (the old grant is revoked). A skill is
    re-approved only for the list the owner sees now (`create_for_skill`)."""
    old = _get_trusted(str(grant_id or ""))
    if old is None or old.get("revoked_at") or str(grant_id) in _revoked_in_log():
        raise _Refused("that pre-approval is not in force, so there is nothing to re-approve")
    if old.get("scope") == "skill":
        g = create_for_skill(old.get("skill") or "", seen_recipients, created_by=by)
    elif old.get("scope") == "job":
        g = create(scope="job", schedule_id=old.get("schedule_id") or "",
                   recipients=old.get("recipients"), actions=old.get("actions"),
                   created_by=by, note="re-approved after the job changed")
    else:
        raise _Refused("only a job or skill pre-approval is re-approved")
    revoke(old["grant_id"], by=by)
    _log("reapproved", grant_id=g["grant_id"], by=by, replaces=old["grant_id"])
    return g


def revoke(grant_id: str, by: str = "owner") -> bool:
    hit = False
    with _LOCK:
        rows = _read()
        for g in rows:
            # EVERY row with this id: a copy restored or pasted beside it is
            # revoked too, not left to be used.
            if g.get("grant_id") == grant_id and not g.get("revoked_at"):
                # Revoking only narrows: a row that was not trustworthy stays
                # unsigned (revoked either way), never laundered into a signed one.
                g["_resign"] = _signed_ok(g)
                g["revoked_at"], g["revoked_by"] = time.time(), by
                hit = True
        if hit:
            _write(rows)
            _log("revoked", grant_id=grant_id, by=by)
    if hit:
        # Take back what is still waiting to go under it (the undo window, or
        # a scheduled time days away): revoking means those do not go either.
        try:
            from agent_friday.services import gmail_send as _gs
            taken = _gs.cancel_for_grant(grant_id)
            if taken:
                _log("held_cancelled", grant_id=grant_id, by=by, approval_ids=taken)
        except Exception as e:
            _log_py.warning("could not take back messages held under a revoked pre-approval (%s)",
                            type(e).__name__)
    return hit


def _revoked_in_log() -> set:
    """Grant ids the log says were revoked. A grant restored from an earlier
    copy of send_grants.json (rows are signed one by one, with no generation)
    would otherwise come back to life."""
    out = set()
    try:
        for ln in _log_path().read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("event") == "revoked" and row.get("grant_id"):
                out.add(str(row["grant_id"]))
    except FileNotFoundError:
        pass
    return out


def _duplicated(rows) -> set:
    seen, dup = set(), set()
    for g in rows:
        gid = str(g.get("grant_id") or "")
        (dup if gid in seen else seen).add(gid)
    return dup


_WARNED_DUP: set = set()


def _usable(g: dict, revoked: set, dup: set) -> bool:
    gid = str(g.get("grant_id") or "")
    if g.get("revoked_at") or gid in revoked:
        return False
    if gid in dup:
        if gid not in _WARNED_DUP:
            _WARNED_DUP.add(gid)
            _log_py.warning("send grant %s appears more than once; it is not used", gid)
            try:
                _log("ignored", grant_id=gid, by="friday",
                     reason="the same grant appears more than once in the file")
            except Exception:
                pass
        return False
    return _trusted(g)


def list_grants(include_revoked: bool = False) -> list:
    """Every grant row, for Settings. `in_force` is False for a row whose
    signature does not hold, or whose id is duplicated: it is shown, so the
    owner can see and revoke it, but never used. A row the log says was
    revoked counts as revoked. `changed` says why a job or skill grant no
    longer covers its job or skill ("" when it does)."""
    rows = _read()
    revoked, dup = _revoked_in_log(), _duplicated(rows)
    out = []
    for g in rows:
        gone = bool(g.get("revoked_at")) or str(g.get("grant_id") or "") in revoked
        if gone and not include_revoked:
            continue
        row = dict({k: v for k, v in g.items() if k != "sig"},
                   in_force=_signed_ok(g) and str(g.get("grant_id") or "") not in dup and not gone)
        if g.get("scope") in ("job", "skill"):
            row["changed"] = changed(g)
        out.append(row)
    return out


def get(grant_id: str) -> Optional[dict]:
    return next(({k: v for k, v in g.items() if k != "sig"}
                 for g in _read() if g.get("grant_id") == grant_id), None)


def _get_trusted(grant_id: str) -> Optional[dict]:
    rows = _read()
    mine = [g for g in rows if g.get("grant_id") == grant_id]
    if len(mine) != 1 or not _trusted(mine[0]):
        return None
    if grant_id in _revoked_in_log():
        return dict({k: v for k, v in mine[0].items() if k != "sig"},
                    revoked_at=mine[0].get("revoked_at") or "in the log")
    return {k: v for k, v in mine[0].items() if k != "sig"}


def _live_trusted() -> list:
    rows = _read()
    revoked, dup = _revoked_in_log(), _duplicated(rows)
    return [{k: v for k, v in g.items() if k != "sig"}
            for g in rows if _usable(g, revoked, dup)]


def log(limit: int = 50) -> list:
    try:
        lines = _log_path().read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


#: The kinds of outside content, as the card names them: (words in the plain source, the kind).
#: First match wins; a source that names none of them is "outside content".
_SOURCE_KINDS = (
    ("recalled", "a recalled memory"),
    ("memory", "a recalled memory"),
    ("friday's notes", "a wiki page"),
    ("wiki", "a wiki page"),
    ("contact notes", "contact notes"),
    ("your contacts", "contact notes"),
    ("task results", "a task's results"),
    ("workflow results", "a workflow's results"),
    ("workflow's previous result", "a workflow's earlier result"),
    ("working notes", "a task's working notes"),
    ("web search", "web search results"),
    ("web page", "a web page"),
    ("your email", "an outside email"),
    ("email", "an outside email"),
    ("library", "a document"),
    ("shared document", "a document"),
    ("document", "a document"),
    ("a file", "a document"),
    ("calendar", "a calendar event"),
    ("chat message", "a chat message"),
    ("connector", "a connector's data"),
)
_NOT_COVERED = "so your pre-approval doesn't cover this send"


def source_kind(why: str) -> str:
    """The kind of outside content a plain source description names (taint.describe_source,
    an outside_content reason), as the card says it: "an outside email", "a web page", ..."""
    low = str(why or "").lower()
    for needle, kind in _SOURCE_KINDS:
        if needle in low:
            return kind
    return "outside content"


def outside_content_why(ctx=None) -> str:
    """Why a pre-approval does not cover this send, in plain words naming the kind of outside
    content behind it: "This chat read an outside email, so your pre-approval doesn't cover this
    send" (a web page, a document, a recalled memory, a wiki page; or "This task was started from a
    chat that read ..."). Falls back to OUTSIDE_CONTENT_WHY's sense when no source can be named."""
    sc = ctx or {}
    who = "This task" if sc.get("task_id") or sc.get("is_background_task") else "This chat"
    why, spawned = "", False
    try:
        from agent_friday.governance import outside_content as _oc
        turn = _oc.turn_outside(sc)
        if turn:
            why = turn
        else:
            try:
                from agent_friday.services import taint as _t
                labels = _t.recent_content(_t.ledger_key(sc), window=_t.TTL_SECONDS, for_grants=True)
            except Exception:
                labels = []
            why = labels[0] if labels else _oc.is_marked(sc)
        low = str(why or "").lower()
        spawned = low.startswith("spawned from") or low.startswith("continues a task")
    except Exception:
        why = ""
    if not why:
        return "This message drew on outside content, " + _NOT_COVERED
    kind = source_kind(why)
    if spawned:
        return "This task was started from a chat that read %s, %s" % (kind, _NOT_COVERED)
    if kind == "a recalled memory":
        return "This chat's prompt carried %s, %s" % (kind, _NOT_COVERED)
    return "%s read %s, %s" % (who, kind, _NOT_COVERED)


def note_withheld(grant: dict, tool_name: str, ctx=None, why: str = OUTSIDE_CONTENT_WHY) -> None:
    """A grant named these recipients, but the call was not clean, so it gets
    its card. Logged, so the owner can see why a pre-approved send asked."""
    ctx = ctx or {}
    try:
        _log("withheld", grant_id=grant.get("grant_id"), by="friday", tool=tool_name,
             reason=why, schedule_id=ctx.get("schedule_id"), task_id=ctx.get("task_id"))
    except Exception:
        pass


# ── skills in use: the scope of a skill grant ──────────────────────────────

#: How long reading a skill keeps it "in use" for a skill grant, at most. It
#: is also tied to the turn (or task) that read it: a later turn must read it
#: again before its grant covers anything.
SKILL_IN_USE_SECONDS = 600

_SKILLS_IN_USE: dict = {}   # task / session / conversation -> {skill: (expires_at, turn_id, fingerprint)}


def _ctx_keys(ctx) -> list:
    ctx = ctx or {}
    return [str(ctx[k]) for k in ("task_id", "session_id", "conversation_id", "taint_key")
            if ctx.get(k)]


def _current_turn(ctx=None) -> Optional[str]:
    """The turn, named as `agent._execute_tool` names it, so the read (inside
    the handler) and the check (before it) agree for a typed turn, a spoken
    one and a task alike."""
    try:
        from agent_friday.governance import outside_content
        return outside_content.turn_id(ctx)
    except Exception:
        return None


def note_skill_read(name: str, ctx, fingerprint: str = "") -> None:
    """read_skill loaded this skill's procedure in this turn (or task). It
    counts as in use for that turn only, for SKILL_IN_USE_SECONDS at most, and
    only as the text that was read (`fingerprint`)."""
    exp, turn = time.time() + SKILL_IN_USE_SECONDS, _current_turn(ctx)
    with _LOCK:
        for k in _ctx_keys(ctx):
            _SKILLS_IN_USE.setdefault(k, {})[str(name)] = (exp, turn, str(fingerprint or ""))


def end_turn(ctx=None) -> None:
    """The turn is over: forget the skills read in this conversation or task,
    and what counted as outside content for this turn only."""
    with _LOCK:
        for k in _ctx_keys(ctx):
            _SKILLS_IN_USE.pop(k, None)
    try:
        from agent_friday.governance import outside_content
        outside_content.end_turn(ctx)
    except Exception:
        pass


def _skills_in_use(ctx) -> dict:
    """Skills read in THIS turn (or task) within SKILL_IN_USE_SECONDS, with
    the fingerprint of what was read. A session flag set by the model cannot
    widen it: only read_skill notes one."""
    now, turn = time.time(), _current_turn(ctx)
    out = {}
    with _LOCK:
        for k in _ctx_keys(ctx):
            held = _SKILLS_IN_USE.get(k) or {}
            for name, (exp, read_turn, fp) in list(held.items()):
                if exp <= now:
                    held.pop(name, None)
                elif read_turn == turn:
                    out[name] = fp
    return out


def approved_skill_grant(name: str, fingerprint: str) -> str:
    """The id of a live skill grant the owner made for exactly this text of
    the skill, or "". Then what read_skill returned is the owner-approved
    procedure, not outside content, for the pre-approved send check only."""
    if not name or not fingerprint:
        return ""
    for g in _live_trusted():
        if (g.get("scope") == "skill" and g.get("skill") == name
                and g.get("skill_hash") == fingerprint):
            return str(g["grant_id"])
    return ""


# ── the check ───────────────────────────────────────────────────────────────

def _in_scope(g, ctx) -> bool:
    if g.get("scope") == "recipients":
        return True
    if g.get("scope") == "job":
        # That scheduled job, and only while it is what the owner approved.
        return (bool(g.get("schedule_id"))
                and str((ctx or {}).get("schedule_id") or "") == g["schedule_id"]
                and bool(g.get("job_hash")) and job_fingerprint(g["schedule_id"]) == g["job_hash"])
    if g.get("scope") == "skill":
        # In use in this turn, as read, and both what was read and the skill
        # as it is now are what the owner approved.
        read = _skills_in_use(ctx)
        return (bool(g.get("skill")) and bool(g.get("skill_hash"))
                and read.get(g["skill"]) == g["skill_hash"]
                and _skill_fingerprint(g["skill"]) == g["skill_hash"])
    return False


def covering(action: str, recipients, ctx=None) -> Optional[dict]:
    """The live grant that covers sending `action` to EVERY one of
    `recipients` in this context, or None. Decides nothing else."""
    rcpts = [str(r).strip() for r in recipients or [] if str(r).strip()]   # _covers folds case, ASCII only
    if not rcpts or action not in SEND_ACTIONS:
        return None
    for g in _live_trusted():
        if action not in (g.get("actions") or []) or not _in_scope(g, ctx):
            continue
        if all(_covers(g.get("recipients"), r) for r in rcpts):
            return dict(g)
    return None


def tool_recipients(tool_name: str, args) -> Optional[tuple]:
    """(action, [recipients]) for a tool that sends, or None."""
    spec = TOOL_ACTIONS.get(tool_name)
    if not spec:
        return None
    action, keys = spec
    out = []
    for k in keys:
        v = (args or {}).get(k)
        if isinstance(v, str):
            out += [p for p in re.split(r"[,;]", v) if p.strip()]
        elif isinstance(v, (list, tuple)):
            out += [str(p) for p in v if str(p).strip()]
    return action, [p.strip() for p in out]


def covering_tool(tool_name: str, args, ctx=None) -> Optional[dict]:
    """The grant covering this tool call, for governance/action_gate."""
    got = tool_recipients(tool_name, args)
    if not got:
        return None
    return covering(got[0], got[1], ctx)


def verify(grant_id, action: str, recipients) -> Optional[dict]:
    """Re-check, for the exact message about to be queued, that the grant the
    checkpoint used is still live and still covers every recipient."""
    g = _get_trusted(str(grant_id or ""))
    if not g or g.get("revoked_at") or action not in (g.get("actions") or []):
        return None
    rcpts = [str(r).strip() for r in recipients or [] if str(r).strip()]   # _covers folds case, ASCII only
    if not rcpts or not all(_covers(g.get("recipients"), r) for r in rcpts):
        return None
    return g


def record_use(grant: dict, action: str, recipients, *, approval_id: str = "",
               ctx=None) -> None:
    """Every use, logged: which grant, what, to whom, for which job, when."""
    ctx = ctx or {}
    with _LOCK:
        rows = _read()
        for g in rows:
            if g.get("grant_id") == grant.get("grant_id"):
                g["_resign"] = _signed_ok(g)
                g["uses"] = int(g.get("uses") or 0) + 1
                g["last_used_at"] = time.time()
        _write(rows)
        _log("used", grant_id=grant.get("grant_id"), by="friday", action=action,
             recipients=list(recipients or []), approval_id=approval_id or None,
             schedule_id=ctx.get("schedule_id"), task_id=ctx.get("task_id"))


# ── conditions on the send itself ──────────────────────────────────────────

#: Why a spoken request does not use a pre-approval (the default).
VOICE_WHY = ("it was asked for by voice, and voice does not use pre-approvals "
             "unless you turn that on in Settings (Pre-approved sends)")
#: Why a message that does not screen as TIER_1 does not use one.
SENSITIVE_WHY = ("the message looks personal or sensitive (%s), so the "
                 "pre-approval does not cover it")


def _is_voice(ctx) -> bool:
    sc = ctx or {}
    return bool(sc.get("is_voice")) or str(sc.get("surface") or "").startswith("voice")


def content_tier(args) -> int:
    """The sensitivity tier of a send's subject and body, by the classifier
    the egress gate uses (services/sensitivity_classifier): patterns and the
    egress keyword list, plus the embedding layer where it is installed. When
    that layer is installed but cannot run, the message counts as TIER_2: a
    covered send is never screened by less than the egress gate would use."""
    from agent_friday.services import sensitivity_classifier as _sc
    a = args or {}
    text = "%s\n%s" % (a.get("subject") or "", a.get("body") or "")
    if not text.strip():
        return _sc.Tier.PUBLIC
    tier = _sc.classify(text, use_presidio=False, use_embeddings=False, egress=True)
    if tier < _sc.Tier.SENSITIVE and _sc._layer3_expected():
        emb, _sim = _sc._embedding_tier(text)
        tier = max(tier, _sc.Tier.PRIVATE if emb < 0 else emb)
    return int(tier or _sc.Tier.PUBLIC)


def use_refusal(tool_name: str, args, ctx=None) -> str:
    """Why a clean call that a grant covers still gets its card, or "":
    a voice request (unless the owner allowed voice), or a message that
    does not screen as TIER_1. Fails closed: an error is a reason."""
    if _is_voice(ctx) and not options().get("voice_may_use"):
        return VOICE_WHY
    if _is_voice(ctx) and not ((ctx or {}).get("owner_local") or (ctx or {}).get("voice_owner_local")):
        # Voice may spend a pre-approval only on a call from Friday's own page on this PC
        # (the desk key on the socket, routes/voice.py), never through a tunnel or a forward.
        return VOICE_WHY
    try:
        tier = content_tier(args)
    except Exception as e:
        return "the sensitivity check could not run (%s), so the pre-approval does not cover it" % e
    if tier != 1:
        return SENSITIVE_WHY % ({2: "TIER_2", 3: "TIER_3"}.get(tier, "TIER_%s" % tier))
    return ""


# ── owner options: signed, in the governance folder, set only in Settings ──

def _options_path():
    return _dir() / "send_grants_options.json"


def options() -> dict:
    """The owner's switches for pre-approved sends. A file whose signature
    does not hold counts as every switch off."""
    try:
        data = json.loads(_options_path().read_text(encoding="utf-8"))
    except Exception:
        return {"voice_may_use": False}
    if not isinstance(data, dict) or not _signed_ok(data):
        return {"voice_may_use": False}
    return {"voice_may_use": bool(data.get("voice_may_use"))}


def set_voice_may_use(on: bool, by: str = "owner") -> dict:
    """Owner-only (the Settings route): may a request made by voice use a
    pre-approval? Off by default."""
    with _LOCK:
        data = {"voice_may_use": bool(on), "set_at": time.time(), "set_by": by}
        data["sig"] = _sig(data)
        p = _options_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(p)
        _log("voice_switched", grant_id=None, by=by, on=bool(on))
    return options()


# ── a change to a job a grant covers is itself outward ─────────────────────

def edit_touches_granted_job(tool_name: str, args) -> str:
    """Why this call changes a scheduled job or workflow that a live job
    grant covers (so it needs a card), or "". The grant would stop covering
    the changed job anyway (its fingerprint no longer matches); the card is
    so the owner decides the change itself."""
    a = dict(args or {})
    jobs = [g for g in _live_trusted() if g.get("scope") == "job" and g.get("schedule_id")]
    if not jobs:
        return ""
    from agent_friday.services import scheduler as _sched
    from agent_friday.services import agent as _agent
    sids, slugs = set(), set()
    for g in jobs:
        sids.add(g["schedule_id"])
        rec = _sched.get_schedule(g["schedule_id"]) or {}
        t = rec.get("task") or {}
        if t.get("kind") == "workflow" and t.get("ref"):
            slugs.add(_agent._chain_slug(str(t["ref"])))
    action = str(a.get("action") or "").lower()
    named = {str(a.get("schedule_id") or "")} - {""}
    draft = a.get("draft") if isinstance(a.get("draft"), dict) else {}
    for v in (a.get("slug"), a.get("name"), a.get("workflow"), draft.get("slug"), draft.get("name")):
        if v:
            named.add(_agent._chain_slug(str(v)))
    why = "it changes a scheduled job whose sends you pre-approved"
    if tool_name == "create_workflow":
        return why if named & slugs else ""
    if tool_name == "workflow_action":
        if action in ("create", "update", "learn", "restore", "delete") and named & (slugs | sids):
            return why
        return ""
    if tool_name == "schedule_job":
        if action in ("delete", "update", "create") and named & (sids | slugs):
            return why
    return ""
