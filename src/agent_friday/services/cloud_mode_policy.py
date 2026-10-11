"""What the routing mode says about reaching a cloud model, in one place.

The two local modes (`local_only`, `local_preferred`) promise that the cloud is
reached only when the owner asked for it. Several call sites that sit outside
the router (background tasks, health probes, video and music generation, avatar
growth, research scoping, task resume, seat warm) each need the same answer to
"may this call leave the machine?", and used to either skip the question or
answer it differently.

`cloud_allowed(purpose)` is that answer, for today's recorded intents:

  * `cloud_only` and any unreadable mode that is not a local mode: allowed.
  * a scheduled job the owner pinned to one cloud model (`scheduled_cloud`,
    carried by `local_only_guard.cloud_pinned`): allowed.
  * `local_preferred` with `explicit=True` (the owner picked a cloud model for
    this task or turn): allowed.
  * everything else in a local mode: not allowed.

`local_only` honours an explicit cloud request for one chat turn only, after a
confirm that names the mode; that confirm lives in the chat route, not here, so
`explicit=True` does not open the gate under `local_only`.

`services/cloud_intent` is the single source of truth: `require()` is the choke
point at every cloud transport, and this helper only reads the same intent so the
call sites that decide earlier than the transport agree with it.
"""

from __future__ import annotations

import logging

_log = logging.getLogger("friday.cloud_mode_policy")

LOCAL_MODES = ("local_only", "local_preferred")
CLOUD_MODES = ("cloud_only",)


def normalize_mode(mode) -> str:
    """The mode as the rest of the code should read it.

    `smart` was a duplicate of `local_preferred` with drifting meanings (RM-D1);
    an old value reads as `local_preferred`. An empty value reads as
    `local_preferred`, matching the model-soup payload.
    """
    m = str(mode or "").strip().lower()
    if m == "smart" or not m:
        return "local_preferred"
    return m


def current_mode() -> str:
    """The routing mode a direct (non-router) call must honour.

    Fails closed: an unreadable setting is `local_only`.
    """
    try:
        from agent_friday.services.local_only_guard import routing_mode
        return normalize_mode(routing_mode())
    except Exception:
        return "local_only"


def is_local_mode(mode: str | None = None) -> bool:
    return (normalize_mode(mode) if mode is not None else current_mode()) in LOCAL_MODES


def cloud_allowed(purpose: str = "", *, explicit: bool = False,
                  mode: str | None = None) -> bool:
    """May a call for `purpose` reach a cloud model right now?

    A thin reader over `services/cloud_intent`, the single source of truth: the
    mode must not be a local one, or the context must carry an intent that opens
    that mode (`cloud_intent.opens_cloud`: a recorded intent, or a job pinned to a
    cloud model). `explicit=True` is the caller's statement that the owner chose
    the cloud for this very call; it counts as a `user_explicit` intent under
    `local_preferred` only, never under `local_only`.
    """
    from agent_friday.services import cloud_intent as _ci
    m = normalize_mode(mode) if mode is not None else current_mode()
    if m not in LOCAL_MODES:
        return True
    if _ci.opens_cloud(m):
        return True
    if explicit and m == "local_preferred":
        return True
    _log.info("cloud call for %s held back: routing mode is %s and no cloud "
              "intent was recorded", purpose or "a call", m)
    return False


def cloud_media_refusal(kind: str) -> dict | None:
    """The refusal a cloud media generator returns under `local_only`, else None.

    `kind` is the lower-case word the owner knows it by ("video", "music").
    Call it after any on-device branch and before the first cloud provider, so
    a prompt is never sent anywhere under Local only. Image generation makes
    the same check inline.
    """
    if current_mode() != "local_only":
        return None
    return {
        "status": "unavailable",
        "message": ("%s generation is not done: Local only is on and no "
                    "on-device %s model is installed, so the prompt was not "
                    "sent to a cloud provider. Install a local %s model in "
                    "Settings → Models, or switch routing to allow the cloud."
                    % (kind.capitalize(), kind, kind)),
    }
