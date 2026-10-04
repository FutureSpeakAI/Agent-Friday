"""Cards for Library changes Friday proposes: add, remove, forget.

Each is ONE approval card decided on screen only (`approvals.SCREEN_ONLY_KINDS`):
voice and chat may ask and may decline, but only a click on the card changes
the Library. A card's payload never changes after it is raised, so approving it
does exactly what the owner saw. A change the owner makes directly in the
Library workspace is itself the consent and raises no card.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from agent_friday.services.library import forget, grants, runtime
from agent_friday.services.library.grants import FORGET_KIND, KIND, REMOVE_KIND
from agent_friday.services.library.store import OWNER, store_for

SUBJECT_TYPE = "library"
MAX_ITEMS = 25
MAX_WAITING = 5
_HOOKED = {"done": False}


def _ap():
    from agent_friday.services import approvals
    return approvals


def _waiting_refusal() -> dict | None:
    """A model (or text it read) cannot bury the owner in Library cards: at most MAX_WAITING wait at once."""
    n = sum(len(_ap().list_approvals(status="pending", kind=k)) for k in (KIND, REMOVE_KIND, FORGET_KIND))
    if n >= MAX_WAITING:
        return {"ok": False, "error": ("There are already %d Library requests waiting on the owner's screen. "
                                       "Those need a decision before another is raised." % n)}
    return None


def find_documents(principal: str, text: str) -> list[dict]:
    """Documents whose title or file name contains `text` (case-insensitive)."""
    t = (text or "").strip().lower()
    if not t:
        return []
    out = []
    for r in store_for(principal).list_documents():
        if t in (r["title"] or "").lower() or t in Path(r["path"]).name.lower():
            out.append({"doc_id": r["id"], "title": r["title"], "pages": r["pages"]})
    return out[:10]


def request_add(items, *, reason: str = "", requested_by: str = "friday") -> dict:
    """ONE card listing every folder or file to add. Adds nothing."""
    ensure_hook()
    held = _waiting_refusal()
    if held:
        return held
    rows = items if isinstance(items, list) else [items]
    good, bad, seen = [], [], set()
    for raw in rows[:MAX_ITEMS]:
        raw = raw if isinstance(raw, dict) else {"path": raw}
        d = grants.describe(raw.get("path"))
        if not d["ok"]:
            bad.append({"path": d.get("path"), "error": d["error"]})
            continue
        if d["path"] in seen:
            continue
        seen.add(d["path"])
        good.append({"path": d["path"], "type": d["type"], "recursive": bool(raw.get("recursive", True)),
                     "glob": raw.get("glob") or None})
    if not good:
        return {"ok": False, "error": "none of those paths can be added", "skipped": bad}
    lines = ["%s: %s" % ("Folder" if i["type"] == "folder" else "File", i["path"]) for i in good]
    n = len(good)
    card = _ap().create_approval(
        kind=KIND, subject_type=SUBJECT_TYPE, subject_id="add-%s" % uuid.uuid4().hex[:12],
        title="Add this %s to your Library" % good[0]["type"] if n == 1 else "Add these %d items to your Library" % n,
        description=("Approve lets Friday read these on this PC and keep an index of them here. "
                     "Nothing is sent to a cloud model: that needs its own permission. "
                     "Remove any of them from the Library workspace."),
        action_description="; ".join(lines)[:1000],
        payload={"handler": KIND, "items": good, "lines": lines, "reason": str(reason or "")[:300],
                 "skipped": bad},
        requested_by=requested_by, action_class="internal", force_gate=True)
    return {"ok": True, "approval_id": card.get("approval_id"), "items": good, "skipped": bad}


def request_remove(principal: str, *, scope_id: str | None = None, document: str | None = None,
                   requested_by: str = "friday") -> dict:
    ensure_hook()
    held = _waiting_refusal()
    if held:
        return held
    item = None
    if scope_id:
        sc = next((a for a in grants.active_scopes(principal) if a["id"] == scope_id), None)
        if sc:
            item = {"kind": "scope", "id": sc["id"], "path": sc["path"]}
    elif document:
        hits = find_documents(principal, document)
        if len(hits) == 1:
            item = {"kind": "document", "doc_id": hits[0]["doc_id"], "title": hits[0]["title"]}
        elif hits:
            return {"ok": False, "error": "several documents match: " + "; ".join(h["title"] for h in hits)}
    if not item:
        return {"ok": False, "error": "nothing in the Library matches that"}
    what = item.get("path") or item.get("title")
    card = _ap().create_approval(
        kind=REMOVE_KIND, subject_type=SUBJECT_TYPE, subject_id="remove-%s" % uuid.uuid4().hex[:12],
        title="Take %s out of your Library" % what,
        description="The index of it is deleted from this PC. The file itself is not touched.",
        action_description="Remove from the Library: %s" % what,
        payload={"handler": REMOVE_KIND, "item": item, "principal": principal},
        requested_by=requested_by, action_class="internal", force_gate=True)
    return {"ok": True, "approval_id": card.get("approval_id"), "item": item}


def request_forget(principal: str, document: str, *, requested_by: str = "friday") -> dict:
    ensure_hook()
    held = _waiting_refusal()
    if held:
        return held
    hits = find_documents(principal, document)
    if len(hits) != 1:
        return {"ok": False, "error": ("several documents match: " + "; ".join(h["title"] for h in hits))
                if hits else "nothing in the Library matches that"}
    h = hits[0]
    card = _ap().create_approval(
        kind=FORGET_KIND, subject_type=SUBJECT_TYPE, subject_id="forget-%s" % uuid.uuid4().hex[:12],
        title="Forget %s completely" % h["title"],
        description=("Deletes its index, and in your saved chats replaces every footnote that cited it with "
                     "“[forgotten source]” and removes any quotation from it. The file itself is not "
                     "touched. This cannot be undone."),
        action_description="Forget everywhere: %s" % h["title"],
        payload={"handler": FORGET_KIND, "doc_id": h["doc_id"], "title": h["title"], "principal": principal},
        requested_by=requested_by, action_class="irreversible", force_gate=True)
    return {"ok": True, "approval_id": card.get("approval_id"), "title": h["title"]}


def _screen_approved(record: dict, kind: str) -> bool:
    ap = _ap()
    if record.get("kind") != kind or record.get("status") != "approved":
        return False
    if record.get("decided_by") not in ap.SCREEN_ONLY_KINDS.get(kind, frozenset()):
        return False
    return bool(ap.claim_for_execution(record["approval_id"]))


def apply_add(record: dict) -> dict:
    if not _screen_approved(record, KIND):
        return {"ok": False}
    created, failed = [], []
    for it in (record.get("payload") or {}).get("items") or []:
        try:
            ev = grants.add_scope(OWNER, it["path"], recursive=it.get("recursive", True),
                                  glob=it.get("glob"), source="card")
            runtime.index_scope(OWNER, ev)
            created.append({"id": ev["id"], "path": ev["path"]})
        except Exception as e:  # noqa: BLE001 - reported on the card
            failed.append({"path": it.get("path"), "error": type(e).__name__})
    _ap().mark_used(record["approval_id"], "library", {"created": created, "failed": failed})
    return {"ok": not failed, "created": created, "failed": failed}


def apply_remove(record: dict) -> dict:
    if not _screen_approved(record, REMOVE_KIND):
        return {"ok": False}
    p = record.get("payload") or {}
    item, principal = p.get("item") or {}, p.get("principal") or OWNER
    if item.get("kind") == "scope":
        grants.remove_scope(principal, item["id"])
        runtime.purge_uncovered(principal)
    elif item.get("kind") == "document":
        forget.remove_document(principal, int(item["doc_id"]))
    _ap().mark_used(record["approval_id"], "library", {"removed": item})
    return {"ok": True}


def apply_forget(record: dict) -> dict:
    if not _screen_approved(record, FORGET_KIND):
        return {"ok": False}
    p = record.get("payload") or {}
    out = forget.forget_document(p.get("principal") or OWNER, int(p["doc_id"]))
    _ap().mark_used(record["approval_id"], "library", out)
    return out


def ensure_hook() -> None:
    if _HOOKED["done"]:
        return
    ap = _ap()
    for kind, fn in ((KIND, apply_add), (REMOVE_KIND, apply_remove), (FORGET_KIND, apply_forget)):
        ap.register_decision_hook(kind, lambda rec, f=fn: f(rec) if rec.get("status") == "approved" else None)
    _HOOKED["done"] = True


# ── the model's tool: file_access gains the library actions ─────────────────

ACTIONS = ("library_add", "library_list", "library_remove", "library_forget")


def handle(action: str, inp: dict) -> str:
    from agent_friday.services.library import principal as pr
    principal = pr.current()
    if principal is None:
        return "The Library is not available to this account."
    if action == "library_list":
        st = store_for(principal)
        scopes = grants.active_scopes(principal)
        c = st.counts()
        if grants.suspended():
            return ("The Library is paused: the permissions ledger could not be verified, so nothing is "
                    "read until the owner resolves it in Settings.")
        if not scopes:
            return "The Library is empty. Nothing has been added."
        return ("In the Library: %s. %d documents read, %d being read, %d couldn't be read."
                % ("; ".join("%s %s (id %s)" % (a.get("type"), a.get("path"), a["id"]) for a in scopes[:15]),
                   c["indexed"], c["queued"], c["failed"]))
    if action == "library_add":
        items = inp.get("items")
        if not items and inp.get("path"):
            items = [{"path": inp.get("path"), "type": inp.get("type"), "recursive": inp.get("recursive", True),
                      "glob": inp.get("glob")}]
        out = request_add(items or [], reason=str(inp.get("reason") or ""))
        if not out.get("ok"):
            why = "; ".join("%s: %s" % (b.get("path"), b.get("error")) for b in out.get("skipped") or []) \
                or out.get("error")
            return "No card raised: %s." % why
        return ("A card listing %d item%s is on screen. Nothing is added until the user approves it there, "
                "on screen; tell them so in one sentence." % (len(out["items"]), "" if len(out["items"]) == 1 else "s"))
    if action == "library_remove":
        out = request_remove(principal, scope_id=str(inp.get("scope_id") or "") or None,
                             document=str(inp.get("document") or inp.get("path") or "") or None)
        return ("A card is on screen naming it. Nothing is removed until the user approves it there."
                if out.get("ok") else "No card raised: %s." % out.get("error"))
    if action == "library_forget":
        out = request_forget(principal, str(inp.get("document") or ""))
        return ("A card is on screen naming %s. Nothing is forgotten until the user approves it there."
                % out["title"] if out.get("ok") else "No card raised: %s." % out.get("error"))
    return "file_access error: unknown action"
