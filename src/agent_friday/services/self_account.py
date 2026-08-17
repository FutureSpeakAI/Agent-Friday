"""Friday's factual account of herself — generated, never remembered.

Her self-description had drifted into being wrong in three separate ways, all
of which look identical from the outside (she says something about herself with
total confidence and it is not true):

  * She said "Gemma 4" without distinguishing the 12b from the 26b, so a
    question about which model answered got a brand name back.
  * She called local image and embedding "integrated capabilities" when they
    are real seats the Arbiter schedules, with ports and VRAM budgets.
  * She listed Veo and Lyria as things she can do. Lyria she cannot: the
    installed google-genai 1.72.0 exposes no `generate_music`, so every music
    request falls back to a demo stub.

A hand-written self-description is a cache with no invalidation. This module
builds the account from what is actually loaded and installed at the moment it
is asked, so it cannot drift: the residency plan names the seats, and each
capability is probed rather than declared.

Everything here is best-effort and never raises — a self-description that
fails must degrade to saying less, never to blocking a turn.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("friday.self_account")


def _seats() -> list:
    """The seats as the Arbiter currently has them, from the residency plan."""
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        if arb is None:
            return []
        # The plan is the source routes/residency.py reads too. `status()`
        # flattens seats to bare model ids and drops the device and context,
        # which are exactly the details she keeps getting wrong.
        seats = (arb.plan or {}).get("seats") or {}
        out = []
        for name, s in seats.items():
            if not isinstance(s, dict):
                continue
            out.append({
                "seat": name,
                "model": s.get("model_id"),
                "backend": s.get("backend"),
                "device": s.get("device"),
                "context": s.get("num_ctx"),
                "status": s.get("status"),
            })
        return out
    except Exception as e:
        _log.debug("self account: no residency plan available: %s", e)
        return []


def capabilities() -> list:
    """Each creative capability, PROBED. `available` is measured, not claimed.

    The point of the probe is that a capability she cannot actually perform
    must not appear in a list of things she can do.
    """
    caps = []

    try:
        from agent_friday.services import local_image
        installed = local_image.is_installed()
        caps.append({
            "name": "image generation",
            "available": bool(installed),
            "how": "on-device, %s via ComfyUI under the Arbiter's exclusive "
                   "GPU lease" % local_image.MODEL_ID,
            "note": None if installed else
                    "the Z-Image build is not present on this machine",
        })
    except Exception as e:
        caps.append({"name": "image generation", "available": False,
                     "how": None, "note": "probe failed: %s" % e})

    try:
        from agent_friday.services import music_engine
        ok, why = music_engine.cloud_music_available()
        caps.append({
            "name": "music generation (Lyria)",
            "available": bool(ok),
            "how": "cloud, Lyria via google-genai" if ok else None,
            # This is the one she has been claiming. Say the real reason.
            "note": None if ok else
                    "NOT available — %s. A music request returns a demo "
                    "placeholder, not real audio. Do not offer it." % why,
        })
    except Exception as e:
        caps.append({"name": "music generation (Lyria)", "available": False,
                     "how": None, "note": "probe failed: %s" % e})

    # Video is the honest awkward case: the SDK surface and the key are both
    # present, so the path is real, but no generation has been run end to end
    # on this machine. Saying "yes" would be a guess and saying "no" would be
    # wrong, so it says exactly that.
    try:
        from agent_friday.services import creative_engine
        keyed = bool(creative_engine.is_available())
        surface = False
        try:
            from google.genai import models as _m
            surface = hasattr(_m.Models, "generate_videos")
        except Exception:
            surface = False
        caps.append({
            "name": "video generation (Veo)",
            "available": bool(keyed and surface),
            "how": "cloud, Veo 3.1 via google-genai" if keyed and surface
                   else None,
            "note": ("the API surface and key are present but no video has been "
                     "generated on this machine, so treat it as untested rather "
                     "than proven")
            if keyed and surface else
            ("no Gemini key configured" if not keyed
             else "installed google-genai exposes no generate_videos"),
        })
    except Exception as e:
        caps.append({"name": "video generation (Veo)", "available": False,
                     "how": None, "note": "probe failed: %s" % e})

    # Calendar, read vs WRITE, probed against the token's real scopes.
    #
    # This is the capability she claimed by implication for months. Stephen
    # asked four times to have a clinic's address added to his chiropractor
    # entries; there was no write tool and the legacy token was read-only, and
    # she said neither — she offered a map and reported "Done". A capability
    # list that cannot tell read from write is how that happens.
    try:
        from agent_friday.services.calendar_write import write_ready
        ok, why = write_ready()
        caps.append({
            "name": "calendar (read)",
            "available": True,
            "how": "Google Calendar via the connected account(s)",
            "note": None,
        })
        caps.append({
            "name": "calendar (edit events)",
            "available": bool(ok),
            "how": ("add/update events and annotate a whole recurring series "
                    "— annotate_calendar_events, create_calendar_event, "
                    "update_calendar_event") if ok else None,
            "note": None if ok else
                    ("NOT available — %s. Say this plainly if asked to change a "
                     "calendar entry; never substitute a map, directions, or "
                     "any other action for the edit that was requested." % why),
        })
    except Exception as e:
        caps.append({"name": "calendar (edit events)", "available": False,
                     "how": None, "note": "probe failed: %s" % e})

    return caps


def describe(include_policy: bool = True) -> str:
    """The whole factual account, as a system-prompt block.

    Returns "" if nothing could be established — better to say nothing about
    herself than to fall back on a stale hand-written description, which is
    what produced the wrong answers in the first place.
    """
    parts = []
    seats = _seats()
    if seats:
        parts.append("Your local models are seats the residency Arbiter "
                     "schedules on one RTX 4070. Right now:")
        for s in seats:
            bits = [str(s.get("model") or "?")]
            if s.get("device"):
                bits.append(str(s["device"]))
            if s.get("context"):
                bits.append("%s ctx" % s["context"])
            if s.get("status"):
                bits.append(str(s["status"]))
            parts.append("  - %s: %s" % (s.get("seat"), ", ".join(bits)))
        parts.append("")
        parts.append("Name the seat and the actual model when you say which "
                     "one is answering. 'Gemma 4' is a brand, not an answer — "
                     "the 12b and the 26b are different models with different "
                     "speeds, and Stephen can tell.")

    caps = capabilities()
    if caps:
        if parts:
            parts.append("")
        parts.append("What you can actually do right now (probed, not assumed):")
        for c in caps:
            line = "  - %s: %s" % (c["name"],
                                   "YES" if c["available"] else "NO")
            if c.get("how"):
                line += " — %s" % c["how"]
            parts.append(line)
            if c.get("note"):
                parts.append("      %s" % c["note"])
        parts.append("")
        parts.append("Never offer a capability marked NO, and never describe "
                     "one as working because it used to be in a list.")

    if include_policy:
        try:
            from agent_friday.services import creative_policy
            if parts:
                parts.append("")
            parts.append("== CREATIVE POLICY ==")
            parts.append(creative_policy.describe())
        except Exception as e:
            _log.debug("self account: policy unavailable: %s", e)

    return "\n".join(parts).strip()
