"""The voice provider indicator - section 4.4 of cloud-voice-providers.md.

C2's enforcement point, and the reason this is a module rather than a field on
a settings object: the indicator must name **the provider that actually served
the most recent interaction**, and the only way to make that true by
construction is to give it no input other than the served path.

There is exactly one way to write it - :func:`record_served`, which takes the
provider that produced bytes - and no way to write it from configuration or
intent. If Gemini Live was requested and Tier 1 served, it says Tier 1, because
nothing ever told it what was requested. The spec's phrasing is "written after
the response is served, from the served path, never from configuration or
intent"; a function that cannot see configuration cannot get that wrong.

Non-negotiable properties from section 4.4, and where each lives:
  - written from the served path            -> :func:`record_served` only
  - renders during degraded states          -> :func:`record_degraded`
  - carries the session's rolling cost      -> :attr:`_STATE` totals
  - not dismissible while cloud is active   -> ``dismissible`` in :func:`snapshot`
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()

#: Rolling session state. Reset when the process restarts, which is the right
#: lifetime: "the session's rolling cost" is a per-session number, and
#: persisting it would make it a different, less useful figure that the
#: cost_meter summaries already provide.
_STATE: dict = {
    "provider": None,        # what SERVED, never what was requested
    "label": None,
    "model": None,
    "is_cloud": False,
    "degraded": False,
    "reason": None,
    "offer": None,
    "session_cost_usd": 0.0,
    "session_priced": True,  # False once any unpriced call lands in the total
    "interactions": [],      # section 4.4 "per-interaction on expand"
    "updated": 0.0,
    # F6 (voice-mode-diagnosis-and-repair.md): a live cloud voice SESSION is
    # not an interaction with a char count; it is a microphone streaming to a
    # provider for as long as it is open. Named here, with the bytes.
    "session_active": False,
    "mic_audio_provider": None,
    "mic_audio_bytes": 0,
}

_MAX_INTERACTIONS = 50


def record_served(*, provider: str, label: str, model: str | None = None,
                  is_cloud: bool = False, cost_usd: float | None = None,
                  chars: int = 0, priced: bool = True) -> None:
    """Record that `provider` SERVED an interaction. The only success writer.

    ``cost_usd=None`` means the call is genuinely unpriced (an unconfirmed rate,
    per ``cost_meter.UNPRICED_MODELS``). It is NOT added to the total as 0.0,
    because that would render an unknown as a verified-free call. The total is
    instead marked approximate via ``session_priced``.
    """
    with _LOCK:
        _STATE["provider"] = provider
        _STATE["label"] = label
        _STATE["model"] = model
        _STATE["is_cloud"] = bool(is_cloud)
        _STATE["degraded"] = False
        _STATE["reason"] = None
        _STATE["offer"] = None
        if cost_usd is None or not priced:
            _STATE["session_priced"] = False
        else:
            _STATE["session_cost_usd"] = round(
                float(_STATE["session_cost_usd"]) + float(cost_usd), 6)
        _STATE["interactions"].append({
            "ts": time.time(), "provider": provider, "model": model,
            "chars": chars, "cost_usd": cost_usd, "priced": bool(priced),
        })
        del _STATE["interactions"][:-_MAX_INTERACTIONS]
        _STATE["updated"] = time.time()


def record_cloud_session(*, provider: str, label: str, event: str,
                         model: str | None = None, bytes_sent: int = 0) -> None:
    """A live cloud voice session opened or closed. Written from the served
    path (the /ws/live handler, at the moment a leg to the provider opens and
    at the moment it closes), never from the engine setting.

    ``event`` is "start" (a new browser session: the byte total resets),
    "open" (a leg to the provider connected) or "close" (that leg ended;
    ``bytes_sent`` is the microphone audio forwarded on it). A session is
    several legs when the provider renews the connection, so open/close never
    reset the total: the receipt at the end names the whole session.
    """
    with _LOCK:
        _STATE["provider"] = provider
        _STATE["label"] = label
        _STATE["model"] = model
        _STATE["is_cloud"] = True
        _STATE["degraded"] = False
        _STATE["reason"] = None
        _STATE["offer"] = None
        _STATE["mic_audio_provider"] = provider
        if event == "start":
            _STATE["mic_audio_bytes"] = 0
            _STATE["session_active"] = False
        elif event == "open":
            _STATE["session_active"] = True
        else:
            _STATE["mic_audio_bytes"] = int(_STATE["mic_audio_bytes"]) + max(0, int(bytes_sent or 0))
            _STATE["session_active"] = False
        _STATE["updated"] = time.time()


def record_degraded(*, requested: str, serving: str | None, reason: str,
                    offer: str | None = None) -> None:
    """Record a degraded state. Section 4.4: the indicator renders here too.

    ``serving`` is None when nothing served - the failure surfaced and an offer
    was made instead (section 6.3). That is a state the indicator must be able
    to show; an indicator that only renders when things work is decoration.

    Note ``requested`` is recorded for the NOTICE (section 6.1 requires the
    notice name both the requested and the serving provider) and is deliberately
    NOT written into ``provider``, which stays the serving one.
    """
    with _LOCK:
        _STATE["degraded"] = True
        _STATE["reason"] = reason
        _STATE["offer"] = offer
        _STATE["requested"] = requested
        if serving is not None:
            _STATE["provider"] = serving
        _STATE["updated"] = time.time()


def snapshot() -> dict:
    """What the UI renders. A copy; callers cannot mutate the record."""
    with _LOCK:
        cloud = bool(_STATE["is_cloud"])
        return {
            "provider": _STATE["provider"],
            "label": _STATE["label"],
            "model": _STATE["model"],
            "is_cloud": cloud,
            "degraded": _STATE["degraded"],
            "reason": _STATE["reason"],
            "offer": _STATE["offer"],
            "requested": _STATE.get("requested"),
            # Local reads $0.00 - "the comparison that does the persuading".
            "session_cost_usd": round(float(_STATE["session_cost_usd"]), 4),
            "session_cost_exact": bool(_STATE["session_priced"]),
            "interactions": list(_STATE["interactions"]),
            # Section 4.4: not dismissible while a cloud provider is active.
            "dismissible": not cloud,
            "updated": _STATE["updated"],
            "session_active": bool(_STATE["session_active"]),
            "mic_audio_provider": _STATE["mic_audio_provider"],
            "mic_audio_bytes": int(_STATE["mic_audio_bytes"]),
        }


def reset_for_tests() -> None:
    with _LOCK:
        _STATE.update({
            "provider": None, "label": None, "model": None, "is_cloud": False,
            "degraded": False, "reason": None, "offer": None,
            "session_cost_usd": 0.0, "session_priced": True,
            "interactions": [], "updated": 0.0,
            "session_active": False, "mic_audio_provider": None,
            "mic_audio_bytes": 0,
        })
        _STATE.pop("requested", None)
