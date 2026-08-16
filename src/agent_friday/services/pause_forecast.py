"""
Agent Friday — never go quiet without saying so first.

Stephen, 2026-08-15:

    "Friday should always warn the user when local inference will (or might)
     cause her to go silent for any amount of time so they can decide if cloud
     or scheduling for idle time would be better."

The failure mode is not the pause. A 53-second wait for the heavy model is
fine when you asked for depth and know what you are waiting on. The failure is
**unannounced** silence: the machine looks hung, you cannot tell whether it is
working or broken, and you had no chance to say "just use the cloud, I'm in a
hurry". Measured examples from this week, all of which read as a fault:

  * first message of a session: ~13 s of model loading before a single token
  * asking for the heavy seat: 53.5 s cold load, then the brain is gone too
  * an image: ComfyUI cold start, ~93 s warm, ~180 s from cold
  * a batch drain: the whole queue, under one lease, with the card taken

This module answers one question — *"if I do this now, will Friday go quiet,
for how long, and how sure are we?"* — and it answers in the three-way shape
Stephen already approved for heavy work: run it now locally, send it to the
cloud instead, or schedule it for when the machine is idle.

Two rules it holds itself to.

**"Might" is a legitimate answer and must be said as one.** A model may or may
not still be resident; Ollama evicts on its own criteria and we cannot see its
intent. Reporting a possible pause as certain trains people to ignore the
warning; reporting it as nothing is how the silence arrives unannounced. So
every forecast carries a confidence and the reason for it.

**Estimates are labelled by where they came from.** A number measured on this
machine and a number carried in from a constant are different kinds of claim,
and the difference belongs in the warning rather than in a comment here.
"""
from __future__ import annotations

import time

# How long a pause has to be before it is worth interrupting someone about.
# Below this it reads as ordinary latency, and a warning would be noise.
WORTH_WARNING_S = 3.0

CERTAIN = "certain"       # it is not resident; it will load
LIKELY = "likely"         # a transition we know the shape of
POSSIBLE = "possible"     # depends on something we cannot see

# Measured on the reference instance. Used only when nothing better exists,
# and reported as `basis: "recorded"` when they are.
RECORDED_COLD_LOAD_S = {
    "gemma4:e2b": 21.0, "gemma4:e4b": 27.5,
    "gemma4:12b": 20.5, "gemma4:26b": 53.5,
}
RECORDED_IMAGE_START_S = 93.0      # warm; ~180 s from a cold ComfyUI
RECORDED_IMAGE_RENDER_S = 93.0     # 1024x1024, measured 2026-08-15


def _plural(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return "about %d seconds" % max(1, s)
    if s < 3600:
        m = s / 60.0
        return "about %s minute%s" % (("%.1f" % m).rstrip("0").rstrip("."),
                                      "" if 0.9 < m < 1.1 else "s")
    return "about %.1f hours" % (s / 3600.0)


# ─────────────────────────────────────────────────────────────────────────────
#  What is resident right now
# ─────────────────────────────────────────────────────────────────────────────

def _residency():
    """(resident model ids, arbiter or None). Never raises.

    A forecast has to work when the residency layer is absent — that is the
    case where a pause is MOST likely and least predictable, so failing to
    forecast would be exactly backwards.
    """
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
    except Exception:
        arb = None
    resident = set()
    if arb is not None:
        try:
            resident |= set(arb.ollama.resident())
            resident |= set(getattr(arb.llama, "procs", {}))
        except Exception:
            pass
    else:
        try:
            import json
            import urllib.request
            with urllib.request.urlopen(
                    "http://localhost:11434/api/ps", timeout=3) as r:
                for m in json.loads(r.read().decode()).get("models", []):
                    resident.add(m["name"])
        except Exception:
            pass
    return resident, arb


def _load_estimate(model_id: str, arb) -> tuple[float, str]:
    """(seconds, basis) for loading this model cold."""
    if arb is not None:
        for e in (arb.entries or []):
            if e.get("model_id") != model_id:
                continue
            for m in (e.get("measured") or []):
                if m.get("cold_load_s"):
                    return float(m["cold_load_s"]), "measured on this machine"
            if e.get("est_load_s"):
                return float(e["est_load_s"]), "estimated from artifact size"
    if model_id in RECORDED_COLD_LOAD_S:
        return RECORDED_COLD_LOAD_S[model_id], "recorded from an earlier run"
    return 30.0, "a rough default — this model has never been timed here"


def _options(seconds: float, *, vault: bool = False,
             cloud_ok: bool = True) -> list:
    """The same three-way choice Stephen approved, with anything unavailable
    left visible and explained rather than dropped."""
    out = [{"id": "now_local", "label": "Wait for it",
            "detail": "Runs here. %s of quiet." % _plural(seconds).capitalize()}]
    if vault or not cloud_ok:
        out.append({"id": "now_cloud", "label": "Use the cloud instead",
                    "unavailable": True,
                    "detail": "This work reads vault-tier material, which "
                              "never leaves this machine."
                    if vault else "No cloud provider is configured."})
    else:
        out.append({"id": "now_cloud", "label": "Use the cloud instead",
                    "detail": "Faster, costs money, and this leaves the "
                              "machine."})
    out.append({"id": "when_away", "label": "Do it while I'm away",
                "detail": "Parked until the machine is idle, then run in one "
                          "batch."})
    return out


def _no_pause(why: str) -> dict:
    return {"will_pause": False, "seconds": 0.0, "confidence": None,
            "why": why, "basis": None, "affects": [], "options": [],
            "checked_at": time.time()}


# ─────────────────────────────────────────────────────────────────────────────
#  The forecasts
# ─────────────────────────────────────────────────────────────────────────────

def before_local_turn(model_id: str, *, vault: bool = False,
                      cloud_ok: bool = True) -> dict:
    """Will an ordinary local turn on this model make Friday wait?

    The common case, and the one that has read as a bug the most often: the
    first message of a session, where the seat is not resident and the reply
    takes ~13 s longer than every message after it for no visible reason.
    """
    resident, arb = _residency()
    if model_id in resident:
        # Resident is not a guarantee. Ollama evicts on its own criteria and
        # does not announce it, so a seat it holds is "probably here" — which
        # is the honest word for it.
        from agent_friday.services import residency_arbiter as ra
        owned = arb is not None and model_id in getattr(arb.llama, "procs", {})
        if owned:
            return _no_pause("%s is loaded in a process Friday owns, so it "
                             "cannot be taken away." % model_id)
        seconds, basis = _load_estimate(model_id, arb)
        if model_id in getattr(ra, "DAEMON_SERVED", {}):
            return {
                "will_pause": True, "seconds": seconds, "confidence": POSSIBLE,
                "basis": basis,
                "why": "%s is loaded now, but it runs on the Ollama daemon "
                       "rather than in a process Friday controls, and that "
                       "daemon can unload it without telling us. If it has "
                       "been unloaded, the next message costs %s."
                       % (model_id, _plural(seconds)),
                "affects": [model_id],
                "options": _options(seconds, vault=vault, cloud_ok=cloud_ok),
                "checked_at": time.time(),
            }
        return _no_pause("%s is loaded." % model_id)

    seconds, basis = _load_estimate(model_id, arb)
    if seconds < WORTH_WARNING_S:
        return _no_pause("%s loads in under %ds." % (model_id,
                                                     int(WORTH_WARNING_S)))
    return {
        "will_pause": True, "seconds": seconds, "confidence": CERTAIN,
        "basis": basis,
        "why": "%s is not loaded, so the first reply has to wait for it — %s "
               "before any text appears. Messages after that are normal speed."
               % (model_id, _plural(seconds)),
        "affects": [model_id],
        "options": _options(seconds, vault=vault, cloud_ok=cloud_ok),
        "checked_at": time.time(),
    }


def before_heavy_lease(*, vault: bool = False, cloud_ok: bool = True) -> dict:
    """The heavy seat takes the card. What goes quiet, and for how long."""
    resident, arb = _residency()
    seat = None
    if arb is not None and arb.plan:
        seat = (arb.plan.get("seats") or {}).get("heavy_hitter")
    model_id = (seat or {}).get("model_id") or "the heavy model"
    seconds, basis = _load_estimate(model_id, arb)

    from agent_friday.services import residency_policy as rp
    stays = sorted(rp.RETAINED_THROUGH_LEASE)
    brain = ((arb.plan.get("seats") or {}).get("interactive_brain") or {}) \
        if (arb and arb.plan) else {}
    goes = brain.get("model_id")

    why = ("Running %s means giving it the graphics card. It takes %s to "
           "start." % (model_id, _plural(seconds)))
    if goes:
        why += (" Your main model (%s) stands down while it works and has to "
                "reload afterwards." % goes)
    if stays:
        why += (" Friday stays reachable on %s throughout, though answers "
                "will be slower than usual." % ", ".join(stays))
    return {
        "will_pause": True, "seconds": seconds, "confidence": LIKELY,
        "basis": basis, "why": why,
        "affects": [m for m in [goes, model_id] if m],
        "stays_awake": stays,
        "options": _options(seconds, vault=vault, cloud_ok=cloud_ok),
        "checked_at": time.time(),
    }


def before_image(*, cloud_ok: bool = True) -> dict:
    """An image takes the whole card except the sidekick (R5 minus R10)."""
    _resident, arb = _residency()
    running = False
    try:
        running = bool(arb and arb.comfy.running())
    except Exception:
        pass
    seconds = RECORDED_IMAGE_RENDER_S if running else \
        (RECORDED_IMAGE_START_S + RECORDED_IMAGE_RENDER_S)
    from agent_friday.services import residency_policy as rp
    stays = sorted(rp.RETAINED_THROUGH_LEASE)
    return {
        "will_pause": True, "seconds": seconds,
        "confidence": LIKELY if running else POSSIBLE,
        "basis": "measured on this machine" if running else
                 "measured, plus a cold start that varies",
        "why": ("Generating an image takes the graphics card for %s. %s"
                % (_plural(seconds),
                   ("The image engine is already running."
                    if running else
                    "The image engine is not running yet, so this includes "
                    "starting it — that part varies a lot."))),
        "affects": ["the image engine", "every model except the sidekick"],
        "stays_awake": stays,
        "options": _options(seconds, cloud_ok=cloud_ok),
        "checked_at": time.time(),
    }


def before_drain(cls: str = "heavy") -> dict:
    """A batch drain: how long the card is spoken for, and for how many jobs."""
    from agent_friday.services import work_queue as wq
    items = wq.pending(cls)
    if not items:
        return _no_pause("nothing is queued for the %s seat." % cls)
    _resident, arb = _residency()
    seat = None
    if arb is not None and arb.plan:
        seat = (arb.plan.get("seats") or {}).get(
            {"heavy": "heavy_hitter", "image": "image"}.get(cls, "sidekick"))
    model_id = (seat or {}).get("model_id") or cls
    load_s, basis = _load_estimate(model_id, arb)
    work_s = sum(i.get("est_s_local") or 0 for i in items)
    total = load_s + work_s
    return {
        "will_pause": True, "seconds": total, "confidence": LIKELY,
        "basis": basis,
        "why": ("Running the %d queued %s job%s takes the card for %s. The "
                "model loads once for the whole batch instead of once per "
                "job, which is why they were queued together."
                % (len(items), cls, "" if len(items) == 1 else "s",
                   _plural(total))),
        "affects": [model_id],
        "items": len(items),
        "options": _options(total),
        "checked_at": time.time(),
    }


def forecast(kind: str, **kw) -> dict:
    """One entry point, so callers do not each pick their own vocabulary."""
    fn = {"local_turn": before_local_turn, "heavy_lease": before_heavy_lease,
          "image": before_image, "drain": before_drain}.get(kind)
    if fn is None:
        return _no_pause("unknown forecast kind %r" % kind)
    return fn(**kw)
