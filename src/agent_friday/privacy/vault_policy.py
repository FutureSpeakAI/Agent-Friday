"""The ONE place the vault posture is resolved.

Why this module exists
----------------------
On 2026-09-01 two sessions probing the same running server reached opposite
conclusions about whether Stephen's vault was protecting anything. Both were
right, because there were two enforcement points reading two different things:

  * ``routing/model_router.py::_route_vault`` force-routed a vault-touching
    question to a local model and **never read ``vault_local_only`` at all**.
    "remind me what my Chase account balance was" routed to
    ``functiongemma:270m`` -- which reads as a refusal, because a 270M model
    cannot answer it.

  * ``services/model_router.py::_gated_vault_control`` *did* read the setting,
    saw ``false``, and returned ``None`` -- which assembles every cloud system
    prompt ungated. Measured: 4,486 characters of TIER_2 material in the cloud
    prompt of turns as innocuous as "what's the weather today".

One flag, two enforcement points, two answers, and no way to tell from either
one what the machine's actual posture was. That is the same disease as the two
passphrase resolvers and the three version-truth implementations: the bug is
not in either branch, it is in there being two.

So: both enforcement points now call :func:`resolve`, and the policy object it
returns is the single answer to "what is this machine doing about the vault".

What the flag means
-------------------
``vault_local_only`` is one intent -- *vault content must not leave this
machine* -- with two enforcement points that must agree:

  ``True``   force vault-touching turns onto a local model, AND gate vault
             tiers out of any cloud prompt.
  ``False``  the owner has accepted cloud exposure. Neither is enforced.

The previous split meant ``False`` produced a third, undesigned posture:
routing still protected the question while prompt assembly leaked the context.
Nobody chose that, and nobody could see it.

**Consequence worth stating plainly:** on a machine with ``vault_local_only``
already set to ``False``, making routing honour the flag means vault-targeted
questions that used to land on a local model will now route to the cloud. That
is the setting doing what it says. It is also why :func:`resolve` treats an
ungated posture as an event worth logging every time it is resolved, and why
``describe()`` exists for the UI to say so out loud.

Fail-safe direction
-------------------
Every failure path here returns the PROTECTIVE answer (``local_only=True``).
A missing settings file, an unreadable key, a raised exception -- none of them
may quietly open the gate. ``vault_local_only`` is opened by an explicit
``false`` and by nothing else.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, NamedTuple

_log = logging.getLogger("friday.vault")

#: Protective defaults. A key absent from settings resolves to these, so a thin
#: `model_routing` block (Stephen's carries 2 of 15 keys) is gated, not open.
DEFAULT_LOCAL_ONLY = True
DEFAULT_CLOUD_FALLBACK = "redact"

VALID_FALLBACKS = ("redact", "deny", "warn")

# One WARNING per distinct posture per process. The gate turning itself off is
# worth saying loudly; it is not worth saying 1,038 times a day, which is how
# the GPU display-reserve error made itself invisible.
_ANNOUNCED: set = set()
_ANNOUNCE_LOCK = threading.Lock()


class VaultPolicy(NamedTuple):
    """The resolved vault posture. Both enforcement points read this."""

    local_only: bool
    cloud_fallback: str
    #: Where `local_only` came from: "settings" | "router-config" | "default"
    #: | "error-default". Anything ending in "default" means nobody chose it.
    source: str
    #: True when the owner explicitly wrote the key. Distinguishes "opted out"
    #: from "never configured", which the UI needs and the log needs.
    explicit: bool

    @property
    def gated(self) -> bool:
        """Whether vault enforcement is active at all."""
        return self.local_only

    @property
    def force_local_routing(self) -> bool:
        """Whether a vault-touching turn must be pinned to a local model.

        Same answer as `gated`, deliberately: one intent, two enforcement
        points. It is a named property rather than a bare `local_only` read at
        the call site so that a future divergence has to be written down here,
        where it can be reviewed, instead of appearing as a second opinion in
        another module.
        """
        return self.local_only

    def describe(self) -> str:
        """One line a human can act on. Used by the log and the UI."""
        if self.local_only:
            return ("Vault gating ACTIVE: vault-touching turns are pinned to a "
                    "local model and vault tiers are stripped from cloud prompts "
                    f"(cloud fallback: {self.cloud_fallback}).")
        how = ("explicitly set to false" if self.explicit
               else f"defaulted open by {self.source}")
        return ("Vault gating OFF: model_routing.vault_local_only is " + how +
                ". Vault-tier context is NOT stripped from cloud prompts and "
                "vault-touching turns are NOT pinned to a local model. Only the "
                "egress gate is still checking. Set it to true to restore "
                "gating.")


def _coerce_fallback(raw: Any) -> str:
    if isinstance(raw, str) and raw in VALID_FALLBACKS:
        return raw
    return DEFAULT_CLOUD_FALLBACK


def resolve(config: dict | None = None, *, announce: bool = True,
            source: str | None = None) -> VaultPolicy:
    """Resolve the vault posture from ONE place.

    `config` is the ``model_routing`` mapping when the caller already holds one
    -- the router is constructed with it, so asking it to re-read settings.json
    would be a second source of truth by another name. When `config` is None the
    live settings are read.

    Never raises. Every failure resolves protectively.
    """
    try:
        if config is None:
            from agent_friday.core import _load_settings
            cfg = (_load_settings() or {}).get("model_routing") or {}
            source = "settings"
        else:
            cfg = config or {}
            source = source or "router-config"

        if not isinstance(cfg, dict):
            cfg, source = {}, "default"

        explicit = "vault_local_only" in cfg
        if not explicit:
            source = "default"

        policy = VaultPolicy(
            local_only=bool(cfg.get("vault_local_only", DEFAULT_LOCAL_ONLY)),
            cloud_fallback=_coerce_fallback(cfg.get("vault_cloud_fallback")),
            source=source,
            explicit=explicit,
        )
    except Exception as exc:  # pragma: no cover - defensive
        policy = VaultPolicy(DEFAULT_LOCAL_ONLY, DEFAULT_CLOUD_FALLBACK,
                             "error-default", False)
        _log.error("vault policy could not be resolved (%s); failing SAFE to "
                   "gated. Vault content stays local until this is fixed.", exc)

    if announce and not policy.local_only:
        _announce_ungated(policy)
    return policy


def _announce_ungated(policy: VaultPolicy) -> None:
    """Say -- once per distinct posture -- that the gate is off.

    `_gated_vault_control()` returning None used to disable vault gating with
    no signal whatsoever: no log line, no badge, nothing in the health payload.
    A privacy gate that turns itself off quietly is worse than one that is
    simply absent, because the absent one does not read as protection.
    """
    key = (policy.local_only, policy.source, policy.explicit)
    with _ANNOUNCE_LOCK:
        if key in _ANNOUNCED:
            return
        _ANNOUNCED.add(key)
    _log.warning("%s", policy.describe())


def reset_announcements() -> None:
    """Test hook: forget what has already been announced."""
    with _ANNOUNCE_LOCK:
        _ANNOUNCED.clear()


def status() -> dict:
    """Serialisable posture for /api/health and the Intelligence panel.

    The UI needs this because a setting that silently disables a privacy
    control is exactly the thing a user should not have to read the logs to
    discover.
    """
    p = resolve(announce=False)
    return {
        "vault_local_only": p.local_only,
        "vault_cloud_fallback": p.cloud_fallback,
        "gated": p.gated,
        "explicit": p.explicit,
        "source": p.source,
        "summary": p.describe(),
        # The UI shows a warning chip on this.
        "degraded": not p.local_only,
    }
