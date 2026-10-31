"""One shape for every grant (NS-18.4-4, NS-30.12-1).

Friday keeps several kinds of standing permission, each in its own store and
its own shape:

  scoped   governance/grants.json (action_gate.create_grant): named outward
           tools for one scheduled job, task or codebase task, an expiry and a
           use count. Claude Code task grants ("vibe:<task>") and codebase
           grants ("codebase:<id>") and the conversation grants for sharing
           local context ("conversation:<id>", services/local_context) are
           scoped grants too.
  run      a scoped grant minted by an approved card for the rest of one run
           (services/run_grants, A24): kept in grants.json, told apart by its note.
  send     governance/send_grants.json: pre-approved sends to named recipients,
           optionally one job or one skill (signed rows).
  file     privacy/file_grants.jsonl: a file, folder or glob whose contents may
           reach a cloud model (signed, append-only events).
  library  the same ledger: consent to read and index a path into the Library.
  desktop  governance/desktop_app_grants.json: a tier per app for Computer Control.
  crew     a Crew agent's file and folder grants (services/crew_profiles).

`GrantRecord` is the one record every kind reads into. Nothing is migrated:
the `from_*` normalisers build a record from the stored form at read time,
and every field a store does not carry gets the value that is the kind's
CURRENT behaviour (no grant is tightened or loosened by being read). New
scoped and send grants are written with the new fields (`new_fields`).

`exclusion` is the cover check every field takes part in. It returns the
name of the first field that keeps the grant from covering an action ("" when
it covers). A grant that does not cover never denies anything: the caller
falls back to its normal path (a card, a chat yes). Records and checks only;
nothing here creates, spends or revokes a grant.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from agent_friday.user_errors import UserFacingValueError

KINDS = ("scoped", "run", "send", "file", "library", "desktop", "crew")
SUBJECT_KINDS = ("goal", "task", "schedule", "session", "owner")
#: What a grant does in quiet hours. allow: it covers as at any other time
#: (what every grant did before these records). respect: it follows the
#: owner's quiet (quiet hours, "quiet until morning"): while quiet is on it
#: covers nothing and the action waits for the owner. deny: it never covers
#: during quiet. At the checkpoint respect and deny are the same; they differ
#: only in what a scheduler may do with the run (see the report, A11-E05).
QUIET_RULES = ("respect", "allow", "deny")
#: invalid: a limit field is stored but cannot be read as a limit (junk, NaN,
#: negative, an unknown rule). Such a grant covers nothing.
STATUSES = ("active", "revoked", "expired", "spent", "invalid")
CONSTRAINT_OPS = ("equals", "one_of", "prefix", "max_len")
OWNER = "owner"

#: The order fields are checked in, so the field named as excluding a grant is
#: always the same for the same grant and action.
FIELD_ORDER = ("status", "starts_at", "expires_at", "max_uses", "principal", "tools",
               "arg_constraints", "data_scope", "destination_scope", "money_cap",
               "time_cap_s", "quiet_hours", "remote_allowed")

#: The argument names a destination is read from (a recipient, an invitee, a
#: site or a channel).
DESTINATION_ARGS = ("to", "cc", "bcc", "recipient", "recipients", "attendees", "email",
                    "url", "domain", "host", "channel", "address")
#: Where an action's cost is read from: the session context's `action_cost`,
#: which only Friday's own code sets. Never the call's arguments: the model
#: writes those, so an amount there could make any action look cheap.
COST_CTX_KEY = "action_cost"

#: The note every run grant carries (services/run_grants.NOTE_PREFIX).
_RUN_NOTE = "run grant (A24) from card "


@dataclass
class GrantRecord:
    """One grant, whatever kind it is. Times are unix seconds; None means "no
    limit" for a cap, a use count or an expiry, and "from when it was made"
    for `starts_at`."""
    id: str
    kind: str
    principal: str = OWNER
    subject: dict = field(default_factory=lambda: {"kind": "owner", "id": ""})
    tools: list = field(default_factory=list)
    arg_constraints: dict = field(default_factory=dict)
    data_scope: list = field(default_factory=list)
    destination_scope: list = field(default_factory=list)
    max_uses: Optional[int] = None
    used_count: int = 0
    starts_at: Optional[float] = None
    expires_at: Optional[float] = None
    money_cap: Optional[float] = None
    money_spent: float = 0.0
    time_cap_s: Optional[float] = None
    first_used_at: Optional[float] = None
    quiet_hours: str = "allow"
    remote_allowed: bool = True
    status: str = "active"
    created_at: Optional[float] = None
    revoked_at: Optional[float] = None
    #: What only this kind has (a desktop tier, a file grant's type, a note).
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


RECORD_FIELDS = tuple(GrantRecord.__dataclass_fields__)


# ── small readers ───────────────────────────────────────────────────────────

def _float(v, default=None):
    if isinstance(v, bool):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _int(v, default=None):
    if isinstance(v, bool):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _strs(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    return [str(x) for x in v if str(x).strip()] if isinstance(v, (list, tuple, set)) else []


# ── validating what the owner writes ────────────────────────────────────────

def _bad(msg: str):
    raise UserFacingValueError(msg)


def _scalar(v, where: str):
    if isinstance(v, bool) or isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v):
        return v
    if isinstance(v, str) and len(v) <= 500:
        return v
    _bad("%s must be a short text, a number or true/false" % where)


def clean_arg_constraints(raw) -> dict:
    """{arg: {equals | one_of | prefix | max_len}}: plain declarative rules, no
    patterns. Raises UserFacingValueError for anything else."""
    if raw in (None, {}, []):
        return {}
    if not isinstance(raw, dict) or len(raw) > 20:
        _bad("argument rules are at most 20 named arguments")
    out = {}
    for arg, rule in raw.items():
        name = str(arg or "").strip()
        if not name or len(name) > 64:
            _bad("an argument rule names its argument (up to 64 characters)")
        if not isinstance(rule, dict) or not rule or set(rule) - set(CONSTRAINT_OPS):
            _bad("an argument rule is one or more of: " + ", ".join(CONSTRAINT_OPS))
        r: dict = {}
        if "equals" in rule:
            r["equals"] = _scalar(rule["equals"], "equals")
        if "one_of" in rule:
            vals = rule["one_of"]
            if not isinstance(vals, (list, tuple)) or not 1 <= len(vals) <= 50:
                _bad("one_of is a list of 1 to 50 values")
            r["one_of"] = [_scalar(v, "one_of") for v in vals]
        if "prefix" in rule:
            p = rule["prefix"]
            if not isinstance(p, str) or not 1 <= len(p) <= 200:
                _bad("prefix is a text of 1 to 200 characters")
            r["prefix"] = p
        if "max_len" in rule:
            n = _int(rule["max_len"])
            if n is None or not 0 <= n <= 100000:
                _bad("max_len is a whole number from 0 to 100000")
            r["max_len"] = n
        out[name] = r
    return out


def clean_scope(raw, what: str, *, lower: bool = True) -> list:
    """A list of owner-entered values (recipients, domains, data labels)."""
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        raw = re.split(r"[,;\s]+", raw)
    if not isinstance(raw, (list, tuple)) or len(raw) > 100:
        _bad("%s is a list of at most 100 values" % what)
    out = []
    for v in raw:
        s = str(v or "").strip()
        if not s:
            continue
        if len(s) > 300 or not s.isascii():
            _bad("each %s value is plain text of at most 300 characters" % what)
        s = s.lower() if lower else s
        if s not in out:
            out.append(s)
    return out


def _cap(v, what: str):
    if v is None or v == "":
        return None
    f = _float(v)
    if f is None or f < 0:
        _bad("%s is a number of zero or more" % what)
    return f


def new_fields(*, principal=None, subject_kind=None, subject_id="", arg_constraints=None,
               data_scope=None, destination_scope=None, starts_at=None, money_cap=None,
               time_cap_s=None, quiet_hours=None, remote_allowed=None, now=None) -> dict:
    """The fields a new grant is written with. Every default is what a grant
    did before these fields existed: the owner's, no argument rule, no data
    or destination limit, no money or time cap, covering in quiet hours and
    from a remote channel. Raises UserFacingValueError for a bad value."""
    now = time.time() if now is None else float(now)
    p = str(principal or OWNER).strip()[:80] or OWNER
    q = str(quiet_hours or "allow").strip().lower()
    if q not in QUIET_RULES:
        _bad("quiet hours is one of: " + ", ".join(QUIET_RULES))
    if remote_allowed is not None and not isinstance(remote_allowed, bool):
        _bad("remote allowed is true or false")
    st = _cap(starts_at, "the start time")
    out = {"principal": p,
           "arg_constraints": clean_arg_constraints(arg_constraints),
           "data_scope": clean_scope(data_scope, "data scope", lower=False),
           "destination_scope": clean_scope(destination_scope, "destination scope"),
           "starts_at": st if st is not None else now,
           "money_cap": _cap(money_cap, "the money cap"), "money_spent": 0.0,
           "time_cap_s": _cap(time_cap_s, "the time cap"), "first_used_at": None,
           "quiet_hours": q,
           "remote_allowed": True if remote_allowed is None else bool(remote_allowed),
           "used_count": 0}
    if subject_kind:
        if subject_kind not in SUBJECT_KINDS:
            _bad("a grant's subject is one of: " + ", ".join(SUBJECT_KINDS))
        out["subject"] = {"kind": subject_kind, "id": str(subject_id or "")[:120]}
    return out


#: The new fields a scoped or send grant row may carry, as `new_fields` writes them.
STORED_FIELDS = ("principal", "subject", "arg_constraints", "data_scope", "destination_scope",
                 "starts_at", "money_cap", "money_spent", "time_cap_s", "first_used_at",
                 "quiet_hours", "remote_allowed", "used_count", "max_uses")


def _num_ok(v, *, low: float = 0.0) -> bool:
    """A real, finite number of at least `low` (not a bool, not a text)."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v)) and float(v) >= low)


def _stored_invalid(row: dict) -> str:
    """The first limit field the row carries that cannot be read as a limit,
    or "". A field left out (or null) is not checked: the kind's default
    applies, as for a row written before these fields."""
    def has(k):
        return k in row and row[k] is not None
    if has("principal") and not (isinstance(row["principal"], str) and row["principal"].strip()):
        return "principal"
    if has("subject") and not (isinstance(row["subject"], dict)
                               and row["subject"].get("kind") in SUBJECT_KINDS):
        return "subject"
    if has("arg_constraints"):
        if not isinstance(row["arg_constraints"], dict):
            return "arg_constraints"
        try:
            clean_arg_constraints(row["arg_constraints"])
        except (UserFacingValueError, ValueError, TypeError):
            return "arg_constraints"
    for k in ("data_scope", "destination_scope"):
        if has(k) and not isinstance(row[k], list):
            return k
    for k in ("starts_at", "expires_at", "money_cap", "money_spent", "time_cap_s", "first_used_at"):
        if has(k) and not _num_ok(row[k]):
            return k
    if has("max_uses") and not (_num_ok(row["max_uses"], low=1) and isinstance(row["max_uses"], int)):
        return "max_uses"
    if has("used_count") and not (_num_ok(row["used_count"]) and isinstance(row["used_count"], int)):
        return "used_count"
    if has("quiet_hours") and row["quiet_hours"] not in QUIET_RULES:
        return "quiet_hours"
    if has("remote_allowed") and not isinstance(row["remote_allowed"], bool):
        return "remote_allowed"
    return ""


def _stored(rec: GrantRecord, row: dict) -> str:
    """Copy the new fields a row carries onto the record (a row written before
    them carries none: the record keeps the kind's defaults). Returns the
    name of a field present but unreadable ("" when none is): the caller
    marks the record "invalid", so it covers nothing."""
    bad = _stored_invalid(row)
    if row.get("principal"):
        rec.principal = str(row["principal"])[:80]
    s = row.get("subject")
    if isinstance(s, dict) and s.get("kind") in SUBJECT_KINDS:
        rec.subject = {"kind": s["kind"], "id": str(s.get("id") or "")}
    if isinstance(row.get("arg_constraints"), dict):
        rec.arg_constraints = dict(row["arg_constraints"])
    if isinstance(row.get("data_scope"), list):
        rec.data_scope = _strs(row["data_scope"])
    if isinstance(row.get("destination_scope"), list):
        rec.destination_scope = [x.lower() for x in _strs(row["destination_scope"])]
    if row.get("starts_at") is not None:
        rec.starts_at = _float(row.get("starts_at"))
    if row.get("money_cap") is not None:
        rec.money_cap = _float(row.get("money_cap"))
    rec.money_spent = _float(row.get("money_spent"), 0.0) or 0.0
    if row.get("time_cap_s") is not None:
        rec.time_cap_s = _float(row.get("time_cap_s"))
    rec.first_used_at = _float(row.get("first_used_at"))
    if row.get("quiet_hours") in QUIET_RULES:
        rec.quiet_hours = row["quiet_hours"]
    if isinstance(row.get("remote_allowed"), bool):
        rec.remote_allowed = row["remote_allowed"]
    if bad:
        rec.detail = dict(rec.detail, invalid_field=bad)
    return bad


# ── normalisers: each stored form into one record ───────────────────────────

def subject_of_scope(scope) -> dict:
    """What a scoped grant's scope names: a scheduled job ("sch_..."), a goal,
    a session, else a task (a background task, a run, a codebase or Claude
    Code task)."""
    s = str(scope or "")
    low = s.lower()
    if low.startswith("sch_"):
        return {"kind": "schedule", "id": s}
    if low.startswith("goal"):
        return {"kind": "goal", "id": s}
    if low.startswith(("session", "sess_", "conversation:")):
        # a conversation grant (services/local_context) is one chat's
        return {"kind": "session", "id": s}
    return {"kind": "task", "id": s}


def from_scoped(g: dict, now: Optional[float] = None) -> GrantRecord:
    """A grants.json row. Its expiry and uses left are what decide today: a
    row with no expiry has expired; uses left of zero is spent."""
    now = time.time() if now is None else now
    uses_left = _int(g.get("uses_left"), 0) or 0
    used = max(0, _int(g.get("used_count"), 0) or 0)
    max_uses = _int(g.get("max_uses"))
    if max_uses is None:
        max_uses = max(0, uses_left) + used
    expires = _float(g.get("expires_at"), 0.0) or 0.0
    note = str(g.get("note") or "")
    rec = GrantRecord(
        id=str(g.get("grant_id") or ""), kind="run" if note.startswith(_RUN_NOTE) else "scoped",
        subject=subject_of_scope(g.get("scope")), tools=sorted(_strs(g.get("tools"))),
        max_uses=max_uses, used_count=used, expires_at=expires,
        created_at=_float(g.get("created_at")), revoked_at=_float(g.get("revoked_at")),
        detail={"scope": str(g.get("scope") or ""), "uses_left": uses_left,
                "created_by": str(g.get("created_by") or ""), "note": note[:200]})
    bad = _stored(rec, g)
    rec.status = ("revoked" if rec.revoked_at else "invalid" if bad
                  else "expired" if not expires > now
                  else "spent" if uses_left <= 0 else "active")
    return rec


def _send_tools(actions) -> list:
    try:
        from agent_friday.governance.send_grants import TOOL_ACTIONS
    except Exception:  # noqa: BLE001
        TOOL_ACTIONS = {"draft_email": ("send_email", ("to", "cc", "bcc"))}
    acts = set(_strs(actions))
    return sorted(t for t, (a, _keys) in TOOL_ACTIONS.items() if a in acts)


def from_send(g: dict, now: Optional[float] = None, revoked_ids: Iterable = ()) -> GrantRecord:
    """A send_grants.json row. Its recipients are its destination scope; no
    expiry and no use limit unless the row names them."""
    now = time.time() if now is None else now
    scope = str(g.get("scope") or "recipients")
    if scope == "job":
        subject = {"kind": "schedule", "id": str(g.get("schedule_id") or "")}
    elif scope == "skill":
        subject = {"kind": "session", "id": "skill:" + str(g.get("skill") or "")}
    else:
        subject = {"kind": "owner", "id": ""}
    rec = GrantRecord(
        id=str(g.get("grant_id") or ""), kind="send", subject=subject,
        tools=_send_tools(g.get("actions")),
        destination_scope=[x.lower() for x in _strs(g.get("recipients"))],
        max_uses=_int(g.get("max_uses")), used_count=max(0, _int(g.get("uses"), 0) or 0),
        expires_at=_float(g.get("expires_at")), created_at=_float(g.get("created_at")),
        revoked_at=_float(g.get("revoked_at")),
        detail={"scope": scope, "actions": _strs(g.get("actions")),
                "schedule_id": g.get("schedule_id"), "skill": g.get("skill"),
                "note": str(g.get("note") or "")[:200]})
    bad = _stored(rec, {k: v for k, v in g.items() if k not in ("destination_scope", "used_count")})
    # revoked in the row, or only in the log (a row restored from an older copy)
    revoked = bool(g.get("revoked_at")) or str(g.get("grant_id") or "") in set(revoked_ids or ())
    rec.status = ("revoked" if revoked
                  else "invalid" if bad
                  else "expired" if rec.expires_at is not None and now >= rec.expires_at
                  else "spent" if rec.max_uses is not None and rec.used_count >= rec.max_uses
                  else "active")
    return rec


def from_file(ev: dict, now: Optional[float] = None) -> GrantRecord:
    """A file grant (grant_file or grant_scope event). It lets a path's
    contents reach a model; a folder or glob grant expires."""
    now = time.time() if now is None else now
    exp = _float(ev.get("expires_ts"))
    rec = GrantRecord(
        id=str(ev.get("id") or ""), kind="file", data_scope=_strs(ev.get("path")),
        expires_at=exp, created_at=_float(ev.get("created_ts")),
        detail={"type": str(ev.get("type") or ""), "pinned_to_content": bool(ev.get("sha256")),
                "never_send_override": bool(ev.get("never_send_override"))})
    rec.status = "expired" if exp is not None and now > exp else "active"
    return rec


def from_library(ev: dict) -> GrantRecord:
    """A Library consent (library_add): read and index a path on this PC."""
    path = str(ev.get("path") or "")
    return GrantRecord(
        id=str(ev.get("id") or ""), kind="library",
        principal=str(ev.get("principal") or OWNER)[:80],
        data_scope=[path] if path else ["everything Friday tracks"],
        created_at=_float(ev.get("created_ts")),
        detail={"type": str(ev.get("type") or ""), "recursive": bool(ev.get("recursive")),
                "glob": ev.get("glob"), "kinds": ev.get("kinds"),
                "source": str(ev.get("source") or "")})


def from_desktop(app: str, tier: str) -> GrantRecord:
    """A per-app Computer Control tier: the app is its destination scope."""
    try:
        from agent_friday.services.desktop_grants import NATIVE_TOOLS
    except Exception:  # noqa: BLE001
        NATIVE_TOOLS = frozenset()
    tools = (sorted(NATIVE_TOOLS) if tier == "act" else ["screenshot"] if tier == "observe" else [])
    return GrantRecord(id="desktop:" + str(app), kind="desktop", tools=tools,
                       destination_scope=[str(app)], detail={"tier": str(tier)})


def from_crew(profile: dict, index: int, grant: dict) -> GrantRecord:
    """One file or folder grant of a Crew agent: the agent is its principal,
    its tools and time budget are the agent's own."""
    pid = str(profile.get("id") or "")
    st = str(profile.get("status") or "active")
    return GrantRecord(
        id="crew:%s:%d" % (pid, index), kind="crew", principal="crew:" + pid,
        subject={"kind": "owner", "id": ""}, tools=sorted(_strs(profile.get("allowed_tools"))),
        data_scope=_strs(grant.get("path")), time_cap_s=_float(profile.get("time_budget_s")),
        status="active" if st == "active" else "revoked",
        detail={"access": str(grant.get("access") or ""), "agent": str(profile.get("name") or ""),
                "agent_status": st, "time_cap_is": "per run"})


def all_records(now: Optional[float] = None) -> dict:
    """Every grant of every kind, as records (dicts). A store that cannot be
    read is named in `unreadable`; the others are still listed."""
    now = time.time() if now is None else now
    out: list = []
    unreadable: list = []

    def take(kind, fn):
        try:
            out.extend(r.to_dict() for r in fn())
        except Exception:  # noqa: BLE001 - one unreadable store hides no other
            unreadable.append(kind)

    def scoped():
        from agent_friday.governance import action_gate
        # signed grants, with their uses and revocation as the action ledger has them
        return [from_scoped(g, now) for g in action_gate.ledger_grant_rows() if isinstance(g, dict)]

    def send():
        from agent_friday.governance import send_grants
        try:
            revoked = send_grants._revoked_in_log()
        except Exception:  # noqa: BLE001 - listed as the file says
            revoked = set()
        def _uses(g):
            # its uses and first use as the log has them, when it can be read
            try:
                return send_grants._with_log_uses(g)
            except Exception:  # noqa: BLE001 - listed as the file says
                return g
        return [from_send(_uses(g), now, revoked) for g in send_grants._read() if isinstance(g, dict)]

    def files():
        from agent_friday.services import file_grants
        st = file_grants._load_state()
        return ([from_file(ev, now) for ev in st.grants.values()]
                + [from_library(ev) for ev in (getattr(st, "library", None) or {}).values()])

    def desktop():
        from agent_friday.services import desktop_grants
        return [from_desktop(app, tier) for app, tier in sorted(desktop_grants.grants().items())]

    def crew():
        from agent_friday.services import crew_profiles
        recs = []
        for p in crew_profiles.list_profiles():
            for i, g in enumerate(p.get("grants") or []):
                recs.append(from_crew(p, i, g))
        return recs

    take("scoped", scoped)
    take("send", send)
    take("file", files)
    take("desktop", desktop)
    take("crew", crew)
    return {"records": out, "unreadable": unreadable}


# ── the cover check ─────────────────────────────────────────────────────────

def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def args_satisfy(rules: dict, args) -> bool:
    """Every rule holds for the call's arguments. A rule on an argument the call
    does not carry fails (max_len alone excepted: nothing is not too long)."""
    a = args if isinstance(args, dict) else {}
    for name, rule in (rules or {}).items():
        present = name in a and a[name] is not None
        v = a.get(name)
        if not isinstance(rule, dict):
            return False
        if "equals" in rule and not (present and _same(v, rule["equals"])):
            return False
        if "one_of" in rule and not (present and any(_same(v, o) for o in rule["one_of"] or [])):
            return False
        if "prefix" in rule and not (present and isinstance(v, str) and v.startswith(str(rule["prefix"]))):
            return False
        if "max_len" in rule and present:
            n = len(v) if isinstance(v, (str, list, tuple, dict)) else len(str(v))
            if n > int(rule["max_len"]):
                return False
    return True


def destinations(args) -> list:
    """Where a call reaches, from its arguments (lower-cased). Empty when the
    call names none."""
    a = args if isinstance(args, dict) else {}
    out: list = []
    for k in DESTINATION_ARGS:
        v = a.get(k)
        items = re.split(r"[,;\s]+", v) if isinstance(v, str) else v if isinstance(v, (list, tuple)) else []
        for it in items:
            if isinstance(it, dict):
                it = it.get("email") or it.get("address") or ""
            s = str(it or "").strip().lower()
            if s and s not in out:
                out.append(s)
    return out


def destination_ok(dest: str, patterns) -> bool:
    """An exact value, "@domain" for an address at that domain, or a host name
    for a URL at that host or under it. Nothing outside plain ASCII matches."""
    d = str(dest or "").strip().lower()
    if not d or not d.isascii():
        return False
    host = ""
    if "://" in d:
        try:
            host = (urlparse(d).hostname or "").lower()
        except ValueError:
            host = ""
    for p in patterns or []:
        p = str(p).strip().lower()
        if not p:
            continue
        if d == p or (p.startswith("@") and d.endswith(p)):
            return True
        if host and not p.startswith("@") and (host == p or host.endswith("." + p)):
            return True
    return False


def action_cost(ctx) -> Optional[float]:
    """What the action costs, from the session context's `action_cost` only
    (set by Friday's code, never by the model). An amount in the call's
    arguments is never read: the model writes those. None when unknown, so a
    grant with a money cap does not cover the action."""
    c = ctx if isinstance(ctx, dict) else {}
    v = c.get(COST_CTX_KEY)
    if not _num_ok(v):
        return None
    return float(v)


def quiet_now(now: Optional[float] = None) -> bool:
    """Whether the owner's quiet is on (services/interruption_policy). A check
    that cannot run counts as quiet: the grant then does not cover and the
    action waits for the owner, which is never less safe."""
    try:
        from agent_friday.services import interruption_policy as _ip
        return bool(_ip.quiet_state(now).get("active"))
    except Exception:  # noqa: BLE001
        return True


def is_remote(ctx) -> bool:
    """Whether the request came from away from this PC: a text-message or chat
    channel (origin or surface "channel" or "phone", even with a card), a
    context marked remote, or a voice call that is not the owner's own page on
    this PC (no desk key on its socket)."""
    c = ctx if isinstance(ctx, dict) else {}
    if c.get("remote") or c.get("remote_device"):
        return True
    # The markers themselves, read directly: derive_origin names a request
    # that carries an approved card "card" before it looks at the channel, so
    # a channel request with a card would not read as remote through it.
    origin = str(c.get("origin") or "").strip().lower()
    surface = str(c.get("surface") or "").strip().lower()
    if origin in ("channel", "phone") or surface.startswith(("channel", "phone")):
        return True
    if c.get("is_voice") or surface.startswith("voice"):
        return not (c.get("owner_local") or c.get("voice_owner_local"))
    return False


def exclusion(rec: GrantRecord, *, tool: Optional[str] = None, args=None, ctx=None,
              now: Optional[float] = None, cost: Optional[float] = None,
              skip: Iterable = ()) -> str:
    """The first field (FIELD_ORDER) that keeps `rec` from covering this
    action, or "" when it covers. `skip` names fields the kind's own check
    already decides (a send grant's recipients). Never raises for a well-formed
    record; the caller treats a raise as "does not cover"."""
    now = time.time() if now is None else now
    c = ctx if isinstance(ctx, dict) else {}
    skip = set(skip or ())
    if rec.status == "revoked" or rec.revoked_at:
        return "status"
    if rec.status == "expired":
        return "expires_at"
    if rec.status == "spent":
        return "max_uses"
    if rec.status != "active":
        return "status"
    if rec.starts_at is not None and now < rec.starts_at:
        return "starts_at"
    if rec.expires_at is not None and now >= rec.expires_at:
        return "expires_at"
    if rec.max_uses is not None and rec.used_count >= rec.max_uses:
        return "max_uses"
    if str(c.get("principal") or OWNER) != rec.principal:
        return "principal"
    if "tools" not in skip and tool is not None and tool not in (rec.tools or []):
        return "tools"
    if rec.arg_constraints and not args_satisfy(rec.arg_constraints, args):
        return "arg_constraints"
    if rec.data_scope and "data_scope" not in skip:
        labels = _strs(c.get("data_labels"))
        if not labels or not set(labels) <= set(rec.data_scope):
            return "data_scope"
    if rec.destination_scope and "destination_scope" not in skip:
        dests = destinations(args)
        if not dests or not all(destination_ok(d, rec.destination_scope) for d in dests):
            return "destination_scope"
    if rec.money_cap is not None:
        spend = cost if cost is not None else action_cost(c)
        if spend is None or (rec.money_spent or 0.0) + spend > rec.money_cap:
            return "money_cap"
    if (rec.time_cap_s is not None and rec.first_used_at is not None
            and now - rec.first_used_at > rec.time_cap_s):
        return "time_cap_s"
    if rec.quiet_hours in ("respect", "deny") and quiet_now(now):
        return "quiet_hours"
    if not rec.remote_allowed and is_remote(c):
        return "remote_allowed"
    return ""
