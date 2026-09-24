"""A vocabulary for the food chain: which KIND of model should do this work.

Spec 3.1. Until now nothing could direct work to a model other than the one
already answering. `spawn_task` delegated but had no way to say what should
pick the task up; `switch_model` moved the user's own seat, returned nothing,
and took effect on the next message. So a four-tier architecture existed on
paper and was inert in practice: every background task ran on whatever the
orchestrator happened to be.

THE NAMES ARE ABOUT COST AND REACH, NOT ABOUT A PARTICULAR MODEL. A caller
asking for `small_local` is saying "this is cheap, keep it on the machine",
not "use gemma4". The mapping from tier to model belongs here, in one place,
so that swapping a model is a change to a table rather than a hunt through
call sites.

    small_local     on-device, fast, cheap. Classification, routing calls,
                    short judgements. Latency matters more than depth.
    large_local     on-device, slow, capable. The 27B. Real work that must
                    not leave the machine.
    cloud_frontier  off-device, fastest and most capable, and it COSTS MONEY
                    and SENDS DATA. Gated accordingly.

A TIER THAT CANNOT BE SERVED SAYS SO. `resolve()` returns a Resolution
carrying either a model or a reason, and never substitutes silently. The
failure this avoids: a seat that is absent, a router that quietly escalates,
and a user who sees a local model stop being local with nothing on screen to
explain it. If the 27B is not
loaded, a caller asking for `large_local` is told that - it does not get the
cloud with a shrug, and it does not get a 4B pretending to be a 27B.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

_log = logging.getLogger("friday.tiers")

SMALL_LOCAL = "small_local"
LARGE_LOCAL = "large_local"
CLOUD_FRONTIER = "cloud_frontier"

TIERS = (SMALL_LOCAL, LARGE_LOCAL, CLOUD_FRONTIER)

#: What each tier means, in the words the model reading the tool schema needs.
DESCRIPTIONS = {
    SMALL_LOCAL: "on-device and fast; for short judgements and classification "
                 "where latency matters more than depth",
    LARGE_LOCAL: "on-device and capable but slow; for real work that must not "
                 "leave this machine",
    CLOUD_FRONTIER: "off-device, fastest and most capable; costs money and "
                    "sends data off the machine, so it is used only when the "
                    "work genuinely needs it",
}


@dataclass(frozen=True)
class Resolution:
    """What a tier resolved to, or why it could not.

    `model` is None exactly when the tier cannot be served. `reason` is then a
    sentence a user could read - not a code - because the caller's job is to
    pass it on, and a reason nobody can read gets replaced with a guess.
    """
    tier: str
    model: str | None
    reason: str = ""
    is_local: bool = True

    @property
    def ok(self) -> bool:
        return bool(self.model)


def _settings():
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _capability(name):
    """The model bound to a capability seat, or ''. Never raises."""
    try:
        routing = (_settings().get("capability_routing") or {})
        return ((routing.get(name) or {}).get("model") or "").strip()
    except Exception:
        return ""


def _serving_locally():
    """Model ids a local seat is actually answering for, right now.

    Empty on any failure to look, and the callers treat empty as "could not
    confirm" rather than "nothing is there" - see `resolve`.
    """
    try:
        from agent_friday.services.residency_arbiter import survey_live_seats
        return set((survey_live_seats() or {}).keys())
    except Exception:
        return set()


def resolve(tier: str) -> Resolution:
    """Which model should do `tier` work, or why none can.

    Never substitutes across the local/cloud line. A caller that asked to stay
    on the machine and silently got the cloud has been lied to about the one
    property it named.
    """
    tier = (tier or "").strip().lower()
    if tier not in TIERS:
        return Resolution(tier, None,
                          "unknown tier %r; expected one of %s"
                          % (tier, ", ".join(TIERS)))

    if tier == CLOUD_FRONTIER:
        model = _capability("reasoning") or _capability("subagent")
        try:
            from agent_friday.services import local_seats
            if model and local_seats._is_local_name(model):
                model = ""      # the reasoning seat is local today; not cloud
        except Exception:
            pass
        if not model:
            return Resolution(tier, None,
                              "no cloud model is configured for this work")
        return Resolution(tier, model, is_local=False)

    wanted = (_capability("sidekick_fast") if tier == SMALL_LOCAL
              else _capability("heavy_hitter") or _capability("reasoning"))
    if not wanted:
        return Resolution(tier, None,
                          "no model is bound to the %s seat" % tier)

    live = _serving_locally()
    if not live:
        # Could not look. Say what we would have used and that it is
        # unverified, rather than refusing work on a failed probe or claiming
        # a seat we did not see.
        _log.info("tiers: could not read local residency; %s unverified", tier)
        return Resolution(tier, wanted,
                          "could not confirm %s is loaded" % wanted)
    if wanted not in live:
        return Resolution(tier, None,
                          "%s is not loaded right now (serving: %s)"
                          % (wanted, ", ".join(sorted(live)) or "nothing"))
    return Resolution(tier, wanted)


def describe_for_model() -> str:
    """The tier vocabulary, for a tool description."""
    return "; ".join("%s: %s" % (t, DESCRIPTIONS[t]) for t in TIERS)
