"""Asking for file access: one approval card per request, decided on screen.

A file grant is created in exactly two places, both by the user's own click in
Friday's chrome: the Add button in Settings › Privacy › File access (POST
/api/privacy/file-grants), and the Approve button on a file-access card raised
here. Nothing in this module creates a grant on request. `request_access` only
raises a card; the grants appear when the card is approved, from the card's
own stored payload, through file_grants.create_file_grant/create_scope_grant.

ONE CARD, EXACTLY WHAT IT LISTS. Every path a turn needs goes into one card.
A card's payload never changes after it is raised, so approving it grants
exactly the rows the user saw. A second request in the same turn does not edit
the first card: it raises one new card carrying both lists and withdraws the
older one. Whichever of the two is decided first is the one that counts.

THE CONTENTS ARE THE SYSTEM'S OWN. Each row is the path as Friday resolved it
on disk, whether it is a file or a folder, and, for a file, the classifier's
own findings (file_grants.scan_path). A request's reason is shown as the
requester's words, never as a finding.

ON SCREEN ONLY. approvals.SCREEN_ONLY_KINDS holds this card's kind: voice, a
text message or a chat reply may decline it but cannot approve it. Voice can
ask for access and say a card is waiting; the yes is a click.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

KIND = "file_grant_request"
SUBJECT_TYPE = "file_access"
HANDLER = "file_grant_batch"
#: Requests closer together than this belong to one turn and share one card.
TURN_WINDOW_S = 120
MAX_ITEMS = 25
DEFAULT_FOLDER_DAYS = 7
MAX_FOLDER_DAYS = 30

_HOOKED = {"done": False}


def _approvals():
    from agent_friday.services import approvals
    return approvals


def _fg():
    from agent_friday.services import file_grants
    return file_grants


def _norm(path) -> str:
    import os
    return os.path.normcase(str(path or ""))


def _days(value) -> float:
    try:
        d = float(value)
    except Exception:
        d = DEFAULT_FOLDER_DAYS
    if d <= 0:
        d = DEFAULT_FOLDER_DAYS
    return min(d, MAX_FOLDER_DAYS)


def describe_item(raw) -> dict:
    """One requested row, resolved and checked here, never taken on trust.

    Returns {"ok": True, "path", "type", "expiry_days"?, "summary"} or
    {"ok": False, "path", "error"}."""
    raw = raw if isinstance(raw, dict) else {"path": raw}
    text = str(raw.get("path") or "").strip()
    fg = _fg()
    # The path usually comes from a model. Network, device, web and relative
    # paths are refused from the text alone, before anything is opened.
    why = fg.unsafe_path_reason(text)
    if why:
        return {"ok": False, "path": text, "error": why}
    try:
        p = Path(text).expanduser().resolve()
    except Exception:
        return {"ok": False, "path": text, "error": "that is not a path"}
    want = str(raw.get("type") or "").strip().lower()
    if not p.exists():
        return {"ok": False, "path": str(p), "error": "nothing exists at that path"}
    kind = "folder" if p.is_dir() else "file"
    if want in ("file", "folder") and want != kind:
        return {"ok": False, "path": str(p),
                "error": "that path is a %s, not a %s" % (kind, want)}
    if kind == "folder":
        broad = fg.too_broad_folder_reason(p)
        if broad:
            return {"ok": False, "path": str(p), "error": broad}
    elif _size(p) > fg.MAX_GRANT_BYTES:
        return {"ok": False, "path": str(p),
                "error": "that file is over %d MB, too large to allow"
                         % (fg.MAX_GRANT_BYTES // (1024 * 1024))}
    item = {"ok": True, "path": str(p), "type": kind}
    if kind == "folder":
        item["expiry_days"] = _days(raw.get("expiry_days"))
        item["summary"] = "everything in this folder, for %g day%s" % (
            item["expiry_days"], "" if item["expiry_days"] == 1 else "s")
    else:
        try:
            item["sha256"] = _sha(p)
            item["summary"] = _fg().scan_path(p).get("summary") or ""
        except Exception:
            return {"ok": False, "path": str(p), "error": "that file could not be read"}
    return item


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except Exception:
        return 1 << 62


def _sha(p: Path) -> str:
    """Content hash, reading at most MAX_GRANT_BYTES (+1 to tell it is over)."""
    import hashlib
    limit = _fg().MAX_GRANT_BYTES
    h = hashlib.sha256()
    read = 0
    with open(p, "rb") as f:
        while True:
            chunk = f.read(min(1 << 20, limit + 1 - read))
            if not chunk:
                break
            read += len(chunk)
            if read > limit:
                raise ValueError("file too large")
            h.update(chunk)
    return h.hexdigest()


def _line(item: dict) -> str:
    what = "Folder" if item["type"] == "folder" else "File"
    return "%s: %s (%s)" % (what, item["path"], item.get("summary") or "")


def _recent_pending() -> list:
    ap = _approvals()
    cutoff = time.time() - TURN_WINDOW_S
    return [r for r in ap.list_approvals(status="pending", kind=KIND)
            if (r.get("created_at") or 0) >= cutoff]


def request_access(items, *, reason: str = "", requested_by: str = "friday",
                   replaces: list | None = None) -> dict:
    """Raise ONE pending card listing every requested path. Grants nothing.

    `replaces` names notice ids (file_grants.notices) the new grants answer, so
    approving the card also closes those questions."""
    ensure_hook()
    rows = items if isinstance(items, list) else [items]
    rows = rows[:MAX_ITEMS]
    good, bad, seen = [], [], set()
    for raw in rows:
        it = describe_item(raw)
        if not it.get("ok"):
            bad.append({"path": it.get("path"), "error": it.get("error")})
            continue
        key = (it["type"], _norm(it["path"]))
        if key in seen:
            continue
        seen.add(key)
        good.append({k: v for k, v in it.items() if k != "ok"})
    replaces = [str(r) for r in (replaces or []) if r]
    if not good:
        return {"ok": False, "error": "none of those paths can be granted",
                "skipped": bad}

    ap = _approvals()
    superseded = []
    for old in _recent_pending():
        payload = old.get("payload") or {}
        for it in payload.get("items") or []:
            key = (it.get("type"), _norm(it.get("path")))
            if key not in seen:
                seen.add(key)
                good.append(it)
        replaces.extend(r for r in payload.get("replaces") or [] if r not in replaces)
        superseded.append(old)
    bad.extend({"path": it.get("path"), "error": "more than %d items in one card" % MAX_ITEMS}
               for it in good[MAX_ITEMS:])
    good = good[:MAX_ITEMS]

    n = len(good)
    if n == 1:
        title = "Let cloud models read this %s" % good[0]["type"]
    else:
        title = "Let cloud models read these %d items" % n
    lines = [_line(it) for it in good]
    card = ap.create_approval(
        kind=KIND, subject_type=SUBJECT_TYPE, subject_id="req-%s" % uuid.uuid4().hex[:12],
        title=title,
        description=("Approve gives cloud models these files' contents when they "
                     "are read. Each file is pinned to what it holds now; a "
                     "folder lasts the days shown. Your never-send list still "
                     "applies. Remove any of them in Settings › Privacy › File access."),
        action_description="; ".join(lines)[:1000],
        payload={"handler": HANDLER, "items": good, "lines": lines,
                 "reason": str(reason or "")[:300], "replaces": replaces,
                 "skipped": bad},
        # Content leaving for a cloud model is outward: always gated, and the
        # card lapses after the outward expiry rather than waiting forever.
        requested_by=requested_by, action_class="outward", force_gate=True)
    for old in superseded:
        if old.get("approval_id") != card.get("approval_id"):
            ap.decide_with_outcome(old["approval_id"], "deny",
                                   decided_by="friday:superseded",
                                   note="replaced by %s" % card.get("approval_id"))
    return {"ok": True, "approval_id": card.get("approval_id"),
            "status": card.get("status"), "items": good, "skipped": bad}


def regrant_notice(notice_id: str, *, requested_by: str = "owner") -> dict:
    """A card offering a fresh grant for the path an old line named."""
    n = _fg().find_notice(str(notice_id or ""))
    if n is None:
        return {"ok": False, "error": "no open notice with that id"}
    if n.get("reason") != "retired_key" or n.get("in_ledger"):
        # An unverified line's path is unauthenticated text; it is not offered.
        return {"ok": False, "error": "that line could not be verified, so there "
                                      "is no permission to offer again"}
    item = {"path": n.get("path"), "type": n.get("type") if n.get("type") in ("file", "folder") else ""}
    if n.get("type") == "folder" and n.get("expires_ts") and n.get("created_ts"):
        item["expiry_days"] = max(1.0, (n["expires_ts"] - n["created_ts"]) / 86400.0)
    return request_access([item], reason="re-grant of an earlier permission",
                          requested_by=requested_by, replaces=[n["id"]])


def apply_approved(record: dict) -> dict:
    """Create each grant an approved card lists. Called by the decision hook.

    Only an approved card of this kind, decided on screen, gets here: the
    approvals store refuses an off-screen approval of this kind outright."""
    ap = _approvals()
    if record.get("kind") != KIND or record.get("status") != "approved":
        return {"ok": False, "created": [], "failed": []}
    if record.get("decided_by") not in ap.SCREEN_ONLY_KINDS.get(KIND, frozenset()):
        return {"ok": False, "created": [], "failed": []}
    if not ap.claim_for_execution(record["approval_id"]):
        return {"ok": False, "created": [], "failed": []}
    fg = _fg()
    payload = record.get("payload") or {}
    for nid in payload.get("replaces") or []:
        try:
            fg.resolve_notice(nid, "regranted", confirmed_by="owner:file-access-card")
        except Exception:
            pass
    created, failed = [], []
    for it in payload.get("items") or []:
        try:
            if it.get("type") == "folder":
                ev = fg.create_scope_grant(it["path"], "folder", _days(it.get("expiry_days")))
            else:
                # The card showed this file as it was when the card was raised.
                # If it changed since, the click did not see what would be sent.
                try:
                    now_sha = _sha(Path(it["path"]))
                except Exception:
                    now_sha = None
                if it.get("sha256") and now_sha != it["sha256"]:
                    failed.append({"path": it.get("path"),
                                   "error": "the file changed after the card was raised"})
                    continue
                ev = fg.create_file_grant(it["path"])
            created.append({"id": ev.get("id"), "path": ev.get("path"), "type": ev.get("type")})
        except Exception as e:
            failed.append({"path": it.get("path"), "error": type(e).__name__})
    ap.mark_used(record["approval_id"], "file_grants",
                 {"created": created, "failed": failed})
    return {"ok": not failed, "created": created, "failed": failed}


def _on_decision(record: dict) -> None:
    if record.get("status") == "approved":
        apply_approved(record)


def ensure_hook() -> None:
    """Register the decision hook once per process."""
    if _HOOKED["done"]:
        return
    _approvals().register_decision_hook(KIND, _on_decision)
    _HOOKED["done"] = True


# ── The model's tool: list, ask, remove, re-grant ────────────────────────────

ACTIONS = ("list", "ask", "remove", "regrant")


def _said_rows(rows: list) -> str:
    if not rows:
        return "No file permissions are set. Cloud models get no file's contents."
    said = {"valid": "in force", "paused": "paused", "changed": "the file changed since",
            "missing": "the file is gone", "expired": "expired",
            "quarantined": "set aside, grants nothing",
            "unverified": "could not be verified, grants nothing"}
    return "; ".join("%s %s (%s, id %s)" % (r.get("type") or "item", r.get("path"),
                                            said.get(r.get("status"), r.get("status")),
                                            r.get("id"))
                     for r in rows[:20])


def handle(inp: dict) -> str:
    inp = inp if isinstance(inp, dict) else {}
    action = str(inp.get("action") or "list").strip().lower()
    if action not in ACTIONS:
        return "file_access error: action must be one of " + ", ".join(ACTIONS)
    fg = _fg()
    if action == "list":
        text = _said_rows(fg.access_rows())
        open_q = fg.notices()
        if open_q:
            text += " Waiting on the user: " + " ".join(
                "%s (notice id %s)" % (n["message"], n["id"]) for n in open_q[:5])
        return text
    if action == "ask":
        items = inp.get("items")
        if not items and inp.get("path"):
            items = [{"path": inp.get("path"), "type": inp.get("type"),
                      "expiry_days": inp.get("expiry_days")}]
        out = request_access(items or [], reason=str(inp.get("reason") or ""),
                             requested_by="friday")
        if not out.get("ok"):
            why = "; ".join("%s: %s" % (b.get("path"), b.get("error"))
                            for b in out.get("skipped") or []) or out.get("error")
            return "No card raised: %s." % why
        return ("A card listing %d item%s is on screen. Nothing is granted until the "
                "user approves it there, on screen; tell them so in one sentence."
                % (len(out["items"]), "" if len(out["items"]) == 1 else "s"))
    if action == "regrant":
        out = regrant_notice(str(inp.get("notice_id") or ""), requested_by="friday")
        if not out.get("ok"):
            return "No card raised: %s." % (out.get("error") or "that cannot be re-granted")
        return ("A card offering that permission again is on screen. Nothing is granted "
                "until the user approves it there.")
    # remove: grants only. A deny mark is never lifted from here, because lifting
    # one would let content out that the user marked never-send.
    target = str(inp.get("grant_id") or "").strip()
    path = str(inp.get("path") or "").strip()
    state = fg._load_state()
    live = dict(state.grants)
    live.update(state.paused_grants)
    if not target and path:
        try:
            want = _norm(Path(path).expanduser().resolve())
        except Exception:
            want = _norm(path)
        hits = [g for g in live.values() if _norm(g.get("path")) == want]
        target = hits[0]["id"] if len(hits) == 1 else ""
    if not target or target not in live:
        return "No file permission matches that. Nothing was changed."
    fg.revoke(target)
    return "Removed: cloud models no longer get %s." % live[target].get("path")


TOOLS = [
    {"name": "file_access",
     "description": (
         "The user's file permissions: which files and folders cloud models may "
         "see the contents of when Friday reads them. action=list says what is "
         "set and any old permission waiting on the user. action=ask raises ONE "
         "approval card on screen for every path this turn needs (put them all in "
         "items in one call); nothing is granted until the user approves the "
         "card on screen. action=remove takes away one permission (grant_id or "
         "path). action=regrant offers an old, set-aside permission again "
         "(notice_id from list) through the same card. Say the result in one "
         "plain sentence."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(ACTIONS)},
         "items": {"type": "array", "items": {"type": "object", "properties": {
             "path": {"type": "string"},
             "type": {"type": "string", "enum": ["file", "folder"]},
             "expiry_days": {"type": "number"}}, "required": ["path"]}},
         "path": {"type": "string"},
         "type": {"type": "string", "enum": ["file", "folder"]},
         "reason": {"type": "string"},
         "grant_id": {"type": "string"},
         "notice_id": {"type": "string"}},
         "required": ["action"]}},
]
RINGS = {"file_access": 1}


def _tool_file_access(inp):
    return handle(inp if isinstance(inp, dict) else {})


HANDLERS = {"file_access": _tool_file_access}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
    ensure_hook()
