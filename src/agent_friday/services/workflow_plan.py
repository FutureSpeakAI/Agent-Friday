"""
Agent Friday — the workflow proposal: Friday proposes, Stephen disposes.

Stephen, 2026-08-15, on who decides a task is heavy:

    "The user decides when a task is heavy; perhaps Friday should present a
     custom workflow UI with a representation of the tasks it will execute,
     and a series of config options for the workflow, so the user can choose
     or select 'choose for me' as an option as well."

So heaviness is a QUESTION Friday raises, never a decision she makes. This
module builds the thing she puts in front of him: the tasks she intends to
run, what each would cost, and the three ways it could go.

    when_away   parked, drained under one lease while the machine is idle
    now_local   runs now on local seats — slower, private, no bill
    now_cloud   runs now on the frontier — faster, costs money

Two rules constrain the menu rather than being applied silently afterwards.

**The vault rule.** `routing/model_router._route_vault` forces a local route
regardless of configured mode, and `services/agent.py` refuses to let a
vault-forced route fall back to cloud. A task that reads vault-tier material
therefore CANNOT run in the cloud — so `now_cloud` is not offered for it, and
the reason is shown on the disabled option. Offering a choice the router would
overrule would be a lie in the interface.

**No silent default.** If Friday thinks work might be heavy, she asks. If he
does not want to think about it, "choose for me" picks — and then SAYS what it
picked and why, so a handed-back decision is still legible.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from agent_friday.core import FRIDAY_DIR
from agent_friday.services import work_queue as wq

_LOCK = threading.RLock()

# Above this, Friday raises the question rather than just running it. Tuned to
# the 53.5 s wake cost of the heavy seat: work that would finish faster than
# the heavy model takes to LOAD is not worth interrupting anyone about.
ASK_ABOVE_S = 60.0

# Rough per-class throughput for estimating, from the measured speed ladder
# (docs/design/symphony-of-intelligence.md §0.4). tok/s, medians of 5 warm runs.
SEAT_TOK_S = {
    "gemma4:e2b": 166.13, "gemma4:e4b": 99.93,
    "gemma4:12b": 49.36, "gemma4:26b": 22.44,
}
CLOUD_TOK_S = 60.0            # order-of-magnitude, for comparison only
HEAVY_WAKE_S = 53.5           # measured cold load of the heavy seat

# Words that suggest depth rather than a quick answer. Deliberately crude: this
# only decides whether to ASK, and asking a needless question is a much smaller
# failure than silently spending four minutes of someone's afternoon.
_HEAVY_HINTS = re.compile(
    r"\b(refactor|migrat\w+|rewrite|audit|analy[sz]e|review|debug|"
    r"implement|port|translate|summari[sz]e (?:all|every|the whole)|"
    r"across (?:all|every)|each (?:file|record|row|item)|batch|"
    r"every (?:file|test|record|page))\b", re.I)


def proposals_dir() -> Path:
    return FRIDAY_DIR / "work_queue" / "proposals"


# ─────────────────────────────────────────────────────────────────────────────
#  Estimating
# ─────────────────────────────────────────────────────────────────────────────

def estimate_task(detail: str, cls: str, seat_hint: str | None = None,
                  est_tokens: int | None = None) -> dict:
    """(seconds local, seconds cloud) for one task, and how it was reached.

    An estimate, and labelled as one. The point is not precision — it is giving
    Stephen a basis for choosing between "wait for this" and "pay for this".
    """
    tokens = est_tokens or max(256, min(4096, len(detail or "") // 2))
    tok_s = SEAT_TOK_S.get(seat_hint or "", None)
    if tok_s is None:
        tok_s = {"reflex": 166.13, "interactive": 49.36, "heavy": 22.44,
                 "background": 49.36, "image": None}.get(cls, 49.36)
    if cls == "image":
        return {"est_s_local": 93.0, "est_s_cloud": 12.0, "basis": "measured",
                "est_tokens": None}
    local = tokens / float(tok_s) + (HEAVY_WAKE_S if cls == "heavy" else 0.0)
    return {"est_s_local": round(local, 1),
            "est_s_cloud": round(tokens / CLOUD_TOK_S, 1),
            "basis": "estimated from the measured speed ladder",
            "est_tokens": tokens}


def looks_heavy(text: str) -> bool:
    """Does this smell like depth? Only ever decides whether to ASK."""
    return bool(_HEAVY_HINTS.search(text or ""))


# ─────────────────────────────────────────────────────────────────────────────
#  Building a proposal
# ─────────────────────────────────────────────────────────────────────────────

def build(title: str, tasks: list, *, summary: str = "",
          workflow_id: str | None = None) -> dict:
    """Turn a list of intended tasks into the object Stephen decides on.

    Each incoming task is a dict with at least `title` and `detail`, and
    optionally `cls`, `seat_hint`, `tools`, `touches_vault`, `est_tokens`.
    """
    built = []
    for t in tasks:
        cls = t.get("cls") or "interactive"
        est = estimate_task(t.get("detail", ""), cls, t.get("seat_hint"),
                            t.get("est_tokens"))
        built.append({
            "id": t.get("id") or uuid.uuid4().hex[:8],
            "title": t.get("title") or "(untitled step)",
            "detail": t.get("detail", ""),
            "cls": cls,
            "seat_hint": t.get("seat_hint"),
            "tools": list(t.get("tools") or []),
            "touches_vault": bool(t.get("touches_vault")),
            **est,
        })

    total_local = round(sum(t["est_s_local"] or 0 for t in built), 1)
    total_cloud = round(sum(t["est_s_cloud"] or 0 for t in built), 1)
    vault_tasks = [t["title"] for t in built if t["touches_vault"]]

    blocked = []
    if vault_tasks:
        blocked.append({
            "option": "now_cloud",
            "reason": "%d task(s) read vault-tier material, which never "
                      "leaves this machine: %s. The router forces those local "
                      "regardless of the configured mode, so a cloud run "
                      "would not do what the label says."
                      % (len(vault_tasks), ", ".join(vault_tasks[:3])),
        })

    options = [o for o in wq.DISPOSITIONS
               if o not in {b["option"] for b in blocked}]

    prop = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "summary": summary,
        "workflow_id": workflow_id,
        "created_at": time.time(),
        "status": "pending",
        "tasks": built,
        "options": options,
        "blocked": blocked,
        "totals": {"est_s_local": total_local, "est_s_cloud": total_cloud,
                   "n_tasks": len(built),
                   "n_heavy": sum(1 for t in built if t["cls"] == "heavy")},
        "decision": None,
    }
    prop["recommendation"] = recommend(prop)
    _save(prop)
    return prop


def recommend(prop: dict) -> dict:
    """What "choose for me" picks, and — always — why it picked it.

    A handed-back decision still has to be legible. "Friday chose" is not an
    answer Stephen can disagree with; "Friday chose local because two of these
    steps read your vault" is.
    """
    blocked = {b["option"] for b in prop.get("blocked") or []}
    totals = prop.get("totals") or {}
    local_s = totals.get("est_s_local") or 0
    away = wq.is_away()

    if "now_cloud" in blocked:
        return {"execution": "now_local",
                "why": "some of this reads your vault, so it stays on this "
                       "machine. Roughly %s of local work." % _dur(local_s)}
    if local_s > ASK_ABOVE_S and away:
        return {"execution": "when_away",
                "why": "about %s of local work and you have been away %s — "
                       "this can run on the card while nobody needs it."
                       % (_dur(local_s), _dur(wq.idle_seconds()))}
    if local_s > ASK_ABOVE_S * 5:
        return {"execution": "when_away",
                "why": "about %s of local work. Waiting through that is worse "
                       "than picking it up later." % _dur(local_s)}
    if local_s > ASK_ABOVE_S:
        return {"execution": "now_cloud",
                "why": "about %s locally against roughly %s in the cloud, and "
                       "you are here waiting."
                       % (_dur(local_s), _dur(totals.get("est_s_cloud") or 0))}
    return {"execution": "now_local",
            "why": "short enough (~%s) that local is simply the cheaper way "
                   "to do it." % _dur(local_s)}


def _dur(seconds: float) -> str:
    s = int(seconds or 0)
    if s < 90:
        return "%ds" % s
    if s < 3600:
        return "%dm" % round(s / 60)
    return "%.1fh" % (s / 3600.0)


# ─────────────────────────────────────────────────────────────────────────────
#  Deciding
# ─────────────────────────────────────────────────────────────────────────────

def decide(proposal_id: str, execution: str | None = None, *,
           choose_for_me: bool = False,
           per_task: dict | None = None) -> dict:
    """Record Stephen's choice and enqueue the work accordingly.

    `per_task` maps task id -> execution, for when one step of a workflow wants
    different treatment from the rest. A per-task choice that the vault rule
    forbids is refused with its reason rather than quietly downgraded: he asked
    for something specific and is owed either that or an explanation.
    """
    prop = load(proposal_id)
    if prop is None:
        return {"ok": False, "error": "no such proposal %r" % proposal_id}
    if prop.get("status") != "pending":
        return {"ok": False, "error": "already decided: %s" % prop["status"]}

    chosen_by = "user"
    if choose_for_me or not execution:
        execution = (prop.get("recommendation") or {}).get("execution")
        chosen_by = "friday"
    if execution not in wq.DISPOSITIONS:
        return {"ok": False, "error": "unknown execution %r" % execution}

    blocked = {b["option"]: b["reason"] for b in prop.get("blocked") or []}
    if execution in blocked and not (per_task or {}):
        return {"ok": False, "error": blocked[execution], "blocked": True}

    per_task = dict(per_task or {})
    enqueued, refused = [], []
    for t in prop["tasks"]:
        want = per_task.get(t["id"], execution)
        if t["touches_vault"] and want == "now_cloud":
            refused.append({
                "task": t["title"],
                "reason": "reads vault-tier material, which never leaves this "
                          "machine",
            })
            want = "now_local"
        try:
            item = wq.enqueue(
                t["title"], t["detail"], cls=t["cls"], disposition=want,
                workflow_id=prop["id"], seat_hint=t.get("seat_hint"),
                touches_vault=t["touches_vault"],
                est_s_local=t.get("est_s_local"),
                est_s_cloud=t.get("est_s_cloud"))
            enqueued.append(item["id"])
        except ValueError as e:
            refused.append({"task": t["title"], "reason": str(e)})

    prop["status"] = "decided"
    prop["decision"] = {
        "execution": execution, "chosen_by": chosen_by,
        "why": (prop.get("recommendation") or {}).get("why")
        if chosen_by == "friday" else None,
        "at": time.time(), "enqueued": enqueued, "refused": refused,
        "per_task": per_task,
    }
    _save(prop)
    return {"ok": True, "proposal": prop, "enqueued": enqueued,
            "refused": refused}


def dismiss(proposal_id: str) -> bool:
    prop = load(proposal_id)
    if not prop or prop.get("status") != "pending":
        return False
    prop["status"] = "dismissed"
    _save(prop)
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Storage
# ─────────────────────────────────────────────────────────────────────────────

def _path(proposal_id: str) -> Path:
    return proposals_dir() / ("%s.json" % proposal_id)


def _save(prop: dict) -> None:
    with _LOCK:
        try:
            proposals_dir().mkdir(parents=True, exist_ok=True)
            p = _path(prop["id"])
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(prop, indent=2), encoding="utf-8")
            os.replace(tmp, p)
        except Exception:
            pass


def load(proposal_id: str) -> dict | None:
    try:
        return json.loads(_path(proposal_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def listing(status: str | None = "pending", limit: int = 50) -> list:
    out = []
    try:
        for f in proposals_dir().glob("*.json"):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if status is None or p.get("status") == status:
                out.append(p)
    except Exception:
        pass
    return sorted(out, key=lambda p: p.get("created_at", 0),
                  reverse=True)[:limit]
