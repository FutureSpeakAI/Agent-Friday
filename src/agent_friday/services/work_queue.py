"""
Agent Friday — the work queue: what makes the 53-second rule affordable.

Measured on the reference instance: waking `gemma4:26b` costs **53.5 seconds
before its first token**, and it then generates at 22.44 tok/s. A 500-token
answer is ~22 seconds of work behind ~53 seconds of waiting, so a cold-start
single question is about 70% pure load time.

That number is the whole argument for this module. Asking the heavy model one
question is close to the worst possible way to use it. Asking it six questions
in one sitting costs the 53 seconds once instead of six times — a saving of
roughly four and a half minutes, on exactly the kind of work someone is happy
to wait for.

The residency layer could already *place* models; the Arbiter's leases are the
authority that lets one seat take the card without the backends fighting over
it. What was missing was anything that ACCUMULATED work into a lease. A lease
held for a single item pays full price for it.

So: work is classed, parked, and drained in batches under one lease. The drain
records both the batched cost and what the same items would have cost one at a
time, because a saving that is claimed rather than measured is not a saving.

Nothing here decides that work is heavy. That is Stephen's call — see
`workflow_plan.py`, which asks him. This module only holds what he decided.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from agent_friday.core import FRIDAY_DIR

# Priority order, highest first. Straight from the latency classes in
# docs/design/symphony-of-intelligence.md §2.4: an interactive turn has someone
# waiting on it, a background job does not.
CLASSES = ("reflex", "interactive", "heavy", "image", "background")
CLASS_RANK = {c: i for i, c in enumerate(CLASSES)}

# Which Arbiter lease a class needs to run. reflex/interactive run on the
# pinned seats and need no lease at all — that is what pinning is for.
CLASS_LEASE = {"heavy": "heavy_turn", "image": "image_job",
               "background": None, "interactive": None, "reflex": None}

# What Stephen chose for a piece of work. Exactly the three options he named.
DISPOSITIONS = ("when_away", "now_local", "now_cloud")

STATUSES = ("queued", "running", "done", "failed", "cancelled")

# How long the machine must be quiet before an away-drain may take the card.
# Long enough that it is not competing with someone who stepped out for coffee,
# short enough to catch a lunch break.
#
# Configurable, and that is not a nicety: at fifteen minutes the timer cannot
# be OBSERVED firing without sitting on your hands for a quarter of an hour, so
# "the away-drain works" stayed an untested claim through a whole build. A
# setting that can be turned down to seconds is what makes it checkable.
AWAY_AFTER_S_DEFAULT = 15 * 60
AWAY_AFTER_S = AWAY_AFTER_S_DEFAULT


def away_after_s() -> float:
    """The idle threshold, from settings, falling back to the default.

    Read per call rather than cached: the point of making it configurable is
    that someone can change it and immediately watch the effect.
    """
    try:
        from agent_friday.core import _load_settings
        v = (_load_settings() or {}).get("away_drain_after_s")
        if v is not None:
            return max(1.0, float(v))
    except Exception:
        pass
    return AWAY_AFTER_S

_LOCK = threading.RLock()
_LAST_ACTIVITY = [time.time()]


def queue_dir() -> Path:
    return FRIDAY_DIR / "work_queue"


def queue_path() -> Path:
    return queue_dir() / "queue.json"


# ─────────────────────────────────────────────────────────────────────────────
#  Persistence
#
#  On disk, because a queue that dies with the process is not a promise Friday
#  can make. "I'll do this while you're away" has to survive a restart or it is
#  a lie with good intentions.
# ─────────────────────────────────────────────────────────────────────────────

def _load() -> list:
    try:
        data = json.loads(queue_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list) -> None:
    p = queue_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


def all_items() -> list:
    with _LOCK:
        return _load()


def get(item_id: str) -> dict | None:
    return next((i for i in all_items() if i["id"] == item_id), None)


# ─────────────────────────────────────────────────────────────────────────────
#  Activity — what "away" means
# ─────────────────────────────────────────────────────────────────────────────

def mark_active(when: float | None = None) -> None:
    """Called on real user interaction. The away-drain waits on this."""
    _LAST_ACTIVITY[0] = when if when is not None else time.time()


def idle_seconds() -> float:
    return max(0.0, time.time() - _LAST_ACTIVITY[0])


def is_away(threshold_s: float | None = None) -> bool:
    return idle_seconds() >= (away_after_s() if threshold_s is None
                              else threshold_s)


# ─────────────────────────────────────────────────────────────────────────────
#  Enqueue
# ─────────────────────────────────────────────────────────────────────────────

def enqueue(title: str, spec: str, cls: str = "background",
            disposition: str = "when_away", *, workflow_id: str | None = None,
            seat_hint: str | None = None, touches_vault: bool = False,
            est_s_local: float | None = None,
            est_s_cloud: float | None = None) -> dict:
    """Park one piece of work. Never executes it.

    `touches_vault` rides along because it is the one thing that can overrule a
    disposition: `routing/model_router._route_vault` forces a local route no
    matter what mode is configured, so a vault-touching item marked `now_cloud`
    would not do what the label says. Refused here rather than silently
    rewritten — a disposition the user chose must either hold or be reported.
    """
    if cls not in CLASSES:
        raise ValueError("unknown class %r; expected one of %s"
                         % (cls, ", ".join(CLASSES)))
    if disposition not in DISPOSITIONS:
        raise ValueError("unknown disposition %r; expected one of %s"
                         % (disposition, ", ".join(DISPOSITIONS)))
    if touches_vault and disposition == "now_cloud":
        raise ValueError(
            "this work reads vault-tier material, which never leaves the "
            "machine. The router would force it local anyway (model_router."
            "_route_vault), so offering a cloud run would be a label that "
            "does not match what happens.")
    item = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "spec": spec,
        "cls": cls,
        "disposition": disposition,
        "status": "queued",
        "created_at": time.time(),
        "workflow_id": workflow_id,
        "seat_hint": seat_hint,
        "touches_vault": bool(touches_vault),
        "est_s_local": est_s_local,
        "est_s_cloud": est_s_cloud,
        "started_at": None, "finished_at": None,
        "seat": None, "result": None, "error": None,
    }
    with _LOCK:
        items = _load()
        items.append(item)
        _save(items)
    return item


def update(item_id: str, **fields) -> dict | None:
    with _LOCK:
        items = _load()
        for i in items:
            if i["id"] == item_id:
                i.update(fields)
                _save(items)
                return i
    return None


def cancel(item_id: str) -> bool:
    it = get(item_id)
    if not it or it["status"] not in ("queued",):
        return False
    return update(item_id, status="cancelled",
                  finished_at=time.time()) is not None


def purge_finished(older_than_s: float = 7 * 86400) -> int:
    cutoff = time.time() - older_than_s
    with _LOCK:
        items = _load()
        keep = [i for i in items
                if i["status"] in ("queued", "running")
                or (i.get("finished_at") or 0) > cutoff]
        removed = len(items) - len(keep)
        if removed:
            _save(keep)
    return removed


# ─────────────────────────────────────────────────────────────────────────────
#  What is ready to run
# ─────────────────────────────────────────────────────────────────────────────

def pending(cls: str | None = None, disposition: str | None = None) -> list:
    """Queued items, highest-priority class first, oldest first within a class."""
    out = [i for i in all_items() if i["status"] == "queued"
           and (cls is None or i["cls"] == cls)
           and (disposition is None or i["disposition"] == disposition)]
    return sorted(out, key=lambda i: (CLASS_RANK.get(i["cls"], 99),
                                      i["created_at"]))


def batch_ready(cls: str, *, min_items: int = 1,
                threshold_s: float | None = None) -> dict:
    """Is it worth waking this seat right now, and why or why not?

    Returns the reasoning, not just a boolean, so a queue that is sitting still
    can explain itself instead of looking broken. "Nothing happened" and "three
    things are waiting for you to step away" are very different states and they
    look identical from the outside.
    """
    threshold_s = away_after_s() if threshold_s is None else threshold_s
    now = [i for i in pending(cls) if i["disposition"] == "now_local"]
    away = [i for i in pending(cls) if i["disposition"] == "when_away"]
    if now:
        return {"ready": True, "items": now + away,
                "why": "%d item(s) you asked to run now" % len(now)}
    if not away:
        return {"ready": False, "items": [], "why": "nothing queued"}
    if not is_away(threshold_s):
        return {"ready": False, "items": away,
                "why": "%d item(s) waiting until you are away; idle for %ds of "
                       "the %ds needed" % (len(away), int(idle_seconds()),
                                           int(threshold_s))}
    if len(away) < min_items:
        return {"ready": False, "items": away,
                "why": "%d item(s) waiting, batching until there are %d — "
                       "waking this seat costs the load either way"
                       % (len(away), min_items)}
    return {"ready": True, "items": away,
            "why": "away for %ds with %d item(s) queued"
                   % (int(idle_seconds()), len(away))}


# ─────────────────────────────────────────────────────────────────────────────
#  Drain — the point of the whole module
# ─────────────────────────────────────────────────────────────────────────────

def drain(cls: str, runner, *, arbiter=None, min_items: int = 1,
          threshold_s: float | None = None, force: bool = False,
          ttl_s: int = 1800) -> dict:
    """Take ONE lease and run every queued item of `cls` under it.

    `runner(item) -> (result, seat)` does the actual work and is injected
    rather than imported, so this module never grows a dependency on how a
    turn is dispatched — and so the batching can be tested without a GPU.

    The lease is taken once, around the whole batch. That is the saving: each
    item after the first runs against an already-resident model. The report
    carries `load_s` and `amortised_load_s` so the benefit is a measurement
    rather than a claim.

    A failing item fails alone. It is recorded and the drain moves on, because
    dropping the lease on the first error would make the batch cost the wake
    time again for everything behind it.
    """
    ready = batch_ready(cls, min_items=min_items, threshold_s=threshold_s)
    if not force and not ready["ready"]:
        return {"ran": 0, "skipped": True, "why": ready["why"]}

    items = ready["items"] or pending(cls)
    if not items:
        return {"ran": 0, "skipped": True, "why": "nothing queued"}

    lease_kind = CLASS_LEASE.get(cls)
    lease, load_s = None, 0.0
    t_lease = time.time()
    if lease_kind and arbiter is not None:
        got = arbiter.grant(lease_kind, ttl_s=ttl_s)
        if not got.get("ok"):
            return {"ran": 0, "skipped": True,
                    "why": "lease refused: %s" % got.get("error")}
        lease = got.get("lease")
        load_s = got.get("transition_s") or round(time.time() - t_lease, 2)

    done, failed, t0 = [], [], time.time()
    try:
        for it in items:
            update(it["id"], status="running", started_at=time.time())
            t_item = time.time()
            try:
                result, seat = runner(it)
                update(it["id"], status="done", result=result, seat=seat,
                       finished_at=time.time(),
                       took_s=round(time.time() - t_item, 2))
                done.append(it["id"])
            except Exception as e:
                update(it["id"], status="failed",
                       error="%s: %s" % (type(e).__name__, str(e)[:300]),
                       finished_at=time.time(),
                       took_s=round(time.time() - t_item, 2))
                failed.append(it["id"])
    finally:
        if lease is not None and arbiter is not None:
            try:
                arbiter.release()
            except Exception:
                pass

    ran = len(done) + len(failed)
    return {
        "ran": ran, "done": done, "failed": failed, "skipped": False,
        "why": ready["why"],
        "lease": lease_kind,
        "load_s": load_s,
        "work_s": round(time.time() - t0, 2),
        # What the same items would have cost run one at a time, each paying
        # its own wake. The difference is the reason this module exists.
        "amortised_load_s": round(load_s / ran, 2) if ran else None,
        "load_s_saved": round(load_s * (ran - 1), 2) if ran > 1 else 0.0,
    }


def stats() -> dict:
    items = all_items()
    by_status: dict = {}
    by_class: dict = {}
    for i in items:
        by_status[i["status"]] = by_status.get(i["status"], 0) + 1
        if i["status"] == "queued":
            by_class[i["cls"]] = by_class.get(i["cls"], 0) + 1
    return {
        "total": len(items),
        "by_status": by_status,
        "queued_by_class": by_class,
        "idle_s": round(idle_seconds()),
        "away": is_away(),
        "away_after_s": away_after_s(),
    }
