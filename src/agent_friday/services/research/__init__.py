"""
Deep research — the public entry point.

docs/design/deep-research.md. Friday takes a hard question, decomposes it from
several perspectives, grinds the searching and reading on local seats,
synthesizes a cited report, verifies every citation deterministically before it
renders, and delivers the result into the conversation unprompted — with an
honest account of what it could not confirm.

The one rule that shapes everything: a commission either produces a report or
produces a failure account. There is no third outcome, and "ran cleanly,
produced nothing" is a failure (RS9).
"""
from __future__ import annotations

import logging
import threading
import time

from agent_friday.services.research import deliver, harness
from agent_friday.services.research.objects import (
    DELIVERED, FAILED, Commission, list_commissions,
)

_log = logging.getLogger("friday.research")

__all__ = ["propose", "run", "run_async", "status", "list_commissions",
           "Commission"]


def propose(question: str, *, context: str | None = None,
            disposition: str = "now_local", budget: dict | None = None) -> dict:
    """Create a commission and compute what Stephen needs in order to veto it.

    Per §3.1 the protection plan arrives BEFORE the work starts, not in the
    credits: either "Claude will see a protected version of this question —
    2 names scrubbed" or "Claude will never see this question".
    """
    c = Commission(question, context=context, disposition=disposition,
                   budget=budget)
    c.save()
    from agent_friday.services import judgment_gate as jg
    dry = jg.dry_run("\n\n".join(x for x in (question, context) if x))
    c.protection.cloud_allowed = bool(dry["cloud_allowed"])
    c.protection.question_sent = dry.get("question_sent")
    c.protection.scrub_tags = dry.get("scrub_tags") or []
    c.protection.scrub_count = int(dry.get("scrub_count") or 0)
    c.protection.reason = dry.get("reason") or ""
    c.save()

    est_min = round(c.budget["wall_clock_soft_s"] / 60)
    dispositions = ["when_away", "now_local"]
    if c.protection.cloud_allowed:
        dispositions.append("now_cloud")
    return {
        "commission_id": c.id,
        "question": question,
        "protection": c.protection.sentence(),
        "cloud_allowed": c.protection.cloud_allowed,
        # RS3: cloud_allowed=false removes every cloud option, with the reason.
        "dispositions": dispositions,
        "estimate_minutes_max": est_min,
        "warning": (f"This can take up to about {est_min} minutes and will read "
                    f"up to {c.budget['fetches_total']} pages."),
    }


def run(commission_id: str) -> dict:
    """Run a commission to completion. Blocking. Returns its status dict."""
    # The inventory can change between commissions — and did, mid-flight.
    try:
        harness.refresh_seats()
    except Exception:
        pass
    c = Commission.load(commission_id)
    if c is None:
        return {"error": f"no commission {commission_id!r}"}
    t0 = time.time()
    ground_by: list[str] = []
    try:
        plan = harness.scope(c)
        if plan is None:
            return _fail(c, "I could not turn this into a research plan — the "
                            "scoping model did not return a usable one.")

        harness.grind(c)
        ground_by = [harness.BRAIN, harness.EXTRACTOR, harness.SIDEKICK]

        if c.failure:                       # the canary tripped (§7.2)
            return _fail(c, c.failure)

        c.colophon = {
            "scoped_by": plan.scoped_by, "ground_by": ground_by,
            "fetches": c.progress.get("fetches", 0),
            "wall_clock_s": round(time.time() - t0, 1),
            "protection": c.protection.sentence(),
        }
        c.save()

        draft = harness.synthesize(c)
        if draft is None:
            # §7.1 — absence honestly established is still a delivered answer,
            # but only when we actually looked. Distinguish the two.
            if c.progress.get("fetches", 0) == 0:
                return _fail(c, "I read no pages at all, so I have nothing to "
                                "report and no absence to claim either.")
            return _deliver_absence(c, t0)

        verified = harness.verify(c, draft)
        if not verified.get("integrity_ok"):
            return _fail(c, "The draft narrated tool calls that never happened, "
                            "so I discarded it rather than show it to you.")
        if verified.get("claims_verified", 0) == 0:
            return _deliver_absence(c, t0, verified=verified)

        c.colophon["wall_clock_s"] = round(time.time() - t0, 1)
        c.colophon["synthesized_by"] = verified.get("synthesized_by")
        c.save()

        markdown = deliver.build_markdown(c, verified)
        deliver.land(c, markdown)
        deliver.style(c, markdown)
        c.status = DELIVERED
        c.progress["stage"] = DELIVERED
        c.save()
        deliver.announce(c, verified)
        c.log("delivered")
        return c.status_dict()
    except Exception as e:
        _log.exception("commission %s blew up", commission_id)
        return _fail(c, f"The research run hit an error: {type(e).__name__}: {e}")


def _deliver_absence(c, t0, verified: dict | None = None) -> dict:
    """§7.1 — a finding-of-absence report. This is a SUCCESS, not a failure:
    absence honestly established is an answer. It reads as a search trail, not
    as prose that sounds researched."""
    v = verified or {"answer": "", "sections": [], "unconfirmed": [],
                     "verified_ids": [], "claims_verified": 0,
                     "claims_struck": 0}
    trail = []
    for entry in c.read_log(400):
        msg = entry.get("msg", "")
        if msg.startswith(("read ", "could not read ", "search returned")):
            trail.append(f"- {msg}")
    v["answer"] = ("**I could not answer this.** Below is what I actually did, "
                   "so you can judge whether the gap is in the sources or in "
                   "my search.")
    v["sections"] = [{"heading": "What I tried",
                      "body": "\n".join(trail) or "- (no searches completed)",
                      "finding_ids": []}]
    c.colophon["wall_clock_s"] = round(time.time() - t0, 1)
    markdown = deliver.build_markdown(c, v)
    deliver.land(c, markdown)
    deliver.style(c, markdown)
    c.status = DELIVERED
    c.progress["stage"] = DELIVERED
    c.save()
    deliver.announce(c, v)
    return c.status_dict()


def _fail(c, why: str) -> dict:
    c.failure = why
    c.status = FAILED
    c.progress["stage"] = FAILED
    c.save()
    c.log(f"FAILED: {why}")
    deliver.announce_failure(c)
    return c.status_dict()


def run_async(commission_id: str) -> str:
    threading.Thread(target=run, args=(commission_id,), daemon=True).start()
    return commission_id


def status(commission_id: str) -> dict:
    """§6 — the endpoint and the orb read this, so the UI cannot invent
    progress it does not have."""
    c = Commission.load(commission_id)
    if c is None:
        return {"error": f"no commission {commission_id!r}"}
    d = c.status_dict()
    d["log_tail"] = c.read_log(30)
    return d
