"""One answer to "is this connected", for every connector Friday has.

Phase 1 of docs/design/connector-ecosystem.md. This module adds a vocabulary
and mappings onto it. It changes no storage, no authentication and no route:
every existing surface keeps its current output, and the existing suite is the
proof.

WHY. "Is this connected?" is computed in twenty-six places across seven
mechanisms, in six vocabularies that do not map onto each other (survey
2026-09-19, §2.2 of the spec). The cost is not untidiness. It is that no caller
can ask the question once and trust the answer, so every surface invents its
own, and each new surface is a fresh opportunity to invent it wrong. The
inventions so far:

  * the connectors page rendered the PRESENCE of a Google record as health, and
    reported both accounts fine while they sat at needs_reauth - nine days of
    confidently wrong calendar answers, including a day with two job interviews
    reported as empty (google_accounts.py:128-135)
  * the provider surface reported a Firecrawl key as MISSING when the key was
    present and merely undecryptable, so the user was told to supply something
    he had already supplied (2026-09-19)
  * both Google accounts carry `drive: true` while every Drive call returns 403
    because the API was never enabled on the Cloud project - a service reported
    on that is off at the provider (2026-09-19)

FOUR RULES, each bought with one of those.

1. FAILS CLOSED. An unrecognised, missing or unmapped state is `UNKNOWN` with
   `healthy=False`. Never infer health from a record existing.
2. WHOSE PROBLEM IT IS, is part of the answer. `NEEDS_USER` (fix it at the
   provider) is not `UNREADABLE` (Friday cannot decrypt this locally) is not
   `DEGRADED` (connected, but this capability is off). Sending someone to
   reconnect a Google account because a local key was wrong is the failure that
   cost a reconnect every morning for weeks.
3. A VERDICT CAN IMPROVE. Nothing here is sticky. A health object is derived at
   the moment it is asked for; a stored status is an input, never the answer.
4. THE BACKEND'S OWN WORDS SURVIVE, in `detail`. Every incident this month was
   made harder by a surface that replaced a specific error with a generic one.

`healthy` is the only field a caller may gate on.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# ── the one vocabulary ──────────────────────────────────────────────────────
WORKING = "working"          # usable right now
DEGRADED = "degraded"        # connected, but something it offers is not available
NEEDS_USER = "needs_user"    # the user must act AT THE PROVIDER (reconnect, enable an API)
UNREADABLE = "unreadable"    # the credential exists and Friday cannot open it, locally
ABSENT = "absent"            # nothing is configured; this was never connected
UNKNOWN = "unknown"          # not determined. NEVER treated as working.

STATES = (WORKING, DEGRADED, NEEDS_USER, UNREADABLE, ABSENT, UNKNOWN)

#: Only these are usable. `DEGRADED` is usable on purpose - a Google account
#: whose Drive is switched off at the Cloud console still reads Gmail, and
#: refusing the whole account over one dead capability would be a worse lie
#: than the one this module exists to stop.
_HEALTHY = {WORKING, DEGRADED}

#: What the user can do about it, by state. `None` means there is nothing for
#: them to do, which is itself information: it means wait, or it means this is
#: Friday's problem to fix.
_DEFAULT_ACTION = {
    WORKING: None,
    DEGRADED: "enable_capability",
    NEEDS_USER: "reconnect",
    UNREADABLE: "unlock",
    ABSENT: "connect",
    UNKNOWN: None,
}

_LABEL = {
    WORKING: "Working",
    DEGRADED: "Working, with something switched off",
    NEEDS_USER: "Needs you",
    UNREADABLE: "Stored credential unreadable",
    ABSENT: "Not connected",
    UNKNOWN: "Status unknown",
}


@dataclass(frozen=True)
class Health:
    """What one connector (or one account of one) is doing right now."""

    state: str = UNKNOWN
    summary: str = ""
    detail: str = ""
    action: str | None = None
    checked_at: float = field(default_factory=time.time)
    stale: bool = False
    #: Has anything actually PROVEN this works, or is it merely configured with
    #: nothing arguing against it?
    #:
    #: FAILING CLOSED APPLIES TO UNRECOGNISED STATES, NOT TO ABSENCE OF PROOF.
    #: A Bluesky app password has no expiry to check and no cheap probe; the
    #: only way to know it works is to publish with it. Marking it unusable
    #: until proven would mean refusing to use a credential the user correctly
    #: supplied - the same class of mistake, pointed the other way, as calling
    #: an unreadable key "missing". So `verified=False` is usable, and says so.
    verified: bool = True
    #: The vocabulary and value this was derived FROM, so a disagreement between
    #: two surfaces can be traced to its source instead of argued about.
    source: str = ""
    source_state: str = ""

    def __post_init__(self):
        if self.state not in STATES:
            # Fails closed rather than raising: a health check that throws takes
            # down the page whose job is to report trouble.
            object.__setattr__(self, "detail",
                               (self.detail + " ").strip()
                               + "[unmapped state %r]" % (self.state,))
            object.__setattr__(self, "source_state",
                               self.source_state or str(self.state))
            object.__setattr__(self, "state", UNKNOWN)
        if not self.summary:
            object.__setattr__(self, "summary", _LABEL[self.state])
        if self.action is None and self.state != WORKING:
            object.__setattr__(self, "action", _DEFAULT_ACTION[self.state])

    @property
    def healthy(self) -> bool:
        """The ONLY field a caller may gate on."""
        return self.state in _HEALTHY

    @property
    def actionable(self) -> bool:
        return self.action is not None

    @property
    def label(self) -> str:
        return _LABEL[self.state]

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "healthy": self.healthy,
            "actionable": self.actionable,
            "action": self.action,
            "label": self.label,
            "summary": self.summary,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "stale": self.stale,
            "verified": self.verified,
            "source": self.source,
            "source_state": self.source_state,
        }


def unknown(detail: str = "", source: str = "") -> Health:
    return Health(state=UNKNOWN, detail=detail, source=source)


def _mapped(table: dict, value, source: str, detail: str = "",
            stale: bool = False, summary: str = "") -> Health:
    """Look `value` up in `table`, failing closed when it is not there.

    The fail-closed branch is the point of this helper. A vocabulary that grows
    a new value - as google_accounts grew `unreadable` on 2026-09-19 - must
    surface as UNKNOWN here, not as whatever the first branch of an if-chain
    happened to be.
    """
    key = str(value).strip().lower() if value is not None else ""
    state = table.get(key)
    if state is None:
        return Health(state=UNKNOWN, source=source, source_state=key,
                      detail=(detail or "").strip(),
                      summary="%s reported %r, which this build does not "
                              "recognise" % (source, key or "nothing"))
    return Health(state=state, source=source, source_state=key, detail=detail,
                  stale=stale, summary=summary,
                  verified=key not in _UNVERIFIED_SOURCE_STATES)


# ── the six vocabularies ────────────────────────────────────────────────────

#: services/google_accounts.py. The only mechanism that already derives a
#: verdict; `unreadable` was added on 2026-09-19 to separate a local key fault
#: from a revoked grant.
_GOOGLE = {
    "connected": WORKING,
    "needs_reauth": NEEDS_USER,
    "revoked": NEEDS_USER,
    "error": UNKNOWN,
    "disconnected": ABSENT,
    "unreadable": UNREADABLE,
}

#: services/connectors.py. `connecting` maps to UNKNOWN rather than to WORKING
#: or ABSENT: a handshake in flight is genuinely not determined, and guessing
#: either way is how a page flickers between two confident wrong answers.
_CONNECTORS = {
    "connected": WORKING,
    "connecting": UNKNOWN,
    "error": UNKNOWN,
    "disconnected": ABSENT,
    "needs_setup": ABSENT,
    "blocked_by_policy": NEEDS_USER,
    "unknown": UNKNOWN,
}

#: mcp_client.SpawnedServer.status
_MCP_SERVER = {
    "ready": WORKING,
    "starting": UNKNOWN,
    "stopped": ABSENT,
    "disabled": ABSENT,
    "needs_auth": NEEDS_USER,
    "error": UNKNOWN,
}

#: services/capability_state.py
#: `present_unverified` maps to WORKING with `verified=False`, not to UNKNOWN.
#: It means "a key is configured and nothing has argued against it", and
#: refusing to use it until proven would break the first use of every API key
#: the user ever adds. See the `verified` field.
_CAPABILITY = {
    "working": WORKING,
    "present_unverified": WORKING,
    "present_failing": NEEDS_USER,
    "unconfigured": ABSENT,
    "absent": ABSENT,
}

#: States from a source vocabulary that are configured-but-unproven.
_UNVERIFIED_SOURCE_STATES = {"present_unverified"}

#: services/credential_store.provider_key_status. The distinction this module
#: exists to preserve: `present_but_unreadable` is NOT `missing`, and reporting
#: it as missing told the user to supply a key he had already supplied.
_KEY_STATUS = {
    "connected": WORKING,
    "present_but_unreadable": UNREADABLE,
    "missing": ABSENT,
}

#: services/provider_health
_PROVIDER_HEALTH = {
    "ok": WORKING,
    "degraded": DEGRADED,
    "down": UNKNOWN,
    "missing": ABSENT,
    "needs": NEEDS_USER,
    "error": UNKNOWN,
    "unknown": UNKNOWN,
}

#: services/key_verdict. FAILS OPEN at its own layer on purpose - only a plain
#: statement from the API counts - so `unknown` here means "no evidence either
#: way", which is UNKNOWN and therefore not usable, not WORKING.
_KEY_VERDICT = {
    "ok": WORKING,
    "rejected": NEEDS_USER,
    "no_credit": NEEDS_USER,
    "unknown": UNKNOWN,
}


def from_google_account(rec: dict) -> Health:
    """A Google account record, or the `health` dict already attached to one."""
    rec = rec or {}
    inner = rec.get("health") if isinstance(rec.get("health"), dict) else None
    stored = (inner or rec).get("stored_status") or rec.get("status")
    stale = bool((inner or {}).get("stale"))
    summary = (inner or {}).get("summary") or ""
    h = _mapped(_GOOGLE, stored, "google_accounts", stale=stale,
                summary=summary)
    return h


def from_connector_status(status: dict) -> Health:
    status = status or {}
    return _mapped(_CONNECTORS, status.get("status"), "connectors",
                   detail=str(status.get("detail") or status.get("hint") or ""))


def from_mcp_server(status) -> Health:
    if isinstance(status, dict):
        return _mapped(_MCP_SERVER, status.get("status"), "mcp_client",
                       detail=str(status.get("error") or ""))
    return _mapped(_MCP_SERVER, status, "mcp_client")


def from_capability_state(state, detail: str = "") -> Health:
    if isinstance(state, dict):
        detail = detail or str(state.get("detail") or "")
        state = state.get("state")
    return _mapped(_CAPABILITY, state, "capability_state", detail=detail)


def from_provider_key_status(status: str, provider: str = "") -> Health:
    h = _mapped(_KEY_STATUS, status, "credential_store")
    if h.state == UNREADABLE:
        return Health(
            state=UNREADABLE, source=h.source, source_state=h.source_state,
            summary=("Friday has a key for %s and cannot decrypt it on this "
                     "machine" % (provider or "this provider")),
            detail=h.detail)
    return h


def from_provider_health(status, detail: str = "") -> Health:
    if isinstance(status, dict):
        detail = detail or str(status.get("detail") or status.get("error") or "")
        status = status.get("status") or status.get("availability")
    return _mapped(_PROVIDER_HEALTH, status, "provider_health", detail=detail)


def from_key_verdict(verdict, detail: str = "") -> Health:
    if isinstance(verdict, dict):
        detail = detail or str(verdict.get("detail") or "")
        verdict = verdict.get("verdict") or verdict.get("state")
    return _mapped(_KEY_VERDICT, verdict, "key_verdict", detail=detail)


def from_credential_presence(present: bool, source: str,
                             detail: str = "") -> Health:
    """The honest reading of "a credential exists" — which is NOT health.

    Presence gives ABSENT when there is nothing, and WORKING with
    `verified=False` when there is something — usable, and honest that nothing
    has proven it. The alternative was UNKNOWN, which would have been prettier
    and would have stopped Bluesky publishing with a correctly supplied app
    password on the grounds that no cheap probe exists for one. Refusing a
    credential the user got right is the same class of mistake as calling an
    unreadable key "missing".

    What this must NOT do is call it verified. `PlatformAdapter.status` used to
    report exactly this case as plain "connected", which is how a token that
    expired in August read as connected in September.
    """
    if present:
        return Health(state=WORKING, source=source, source_state="present",
                      detail=detail, verified=False,
                      summary="A credential is stored; nothing has confirmed "
                              "it still works")
    return Health(state=ABSENT, source=source, source_state="absent",
                  detail=detail)


def worst(healths) -> Health:
    """The verdict for a thing made of several parts.

    ORDERED BY HOW MUCH IT MATTERS TO THE USER, not by severity in the
    abstract. An account that needs reconnecting outranks one whose state is
    merely unknown, because the first has an action attached and the second
    does not. An empty list is UNKNOWN, never WORKING - "nothing to report"
    and "all clear" are different sentences.
    """
    order = [NEEDS_USER, UNREADABLE, UNKNOWN, DEGRADED, ABSENT, WORKING]
    items = [h for h in (healths or []) if isinstance(h, Health)]
    if not items:
        return Health(state=UNKNOWN, source="worst",
                      summary="Nothing reported a state")
    for state in order:
        for h in items:
            if h.state == state:
                return h
    return items[0]


def summarise(healths) -> dict:
    """Counts for a dashboard, plus the verdict that should colour it."""
    items = [h for h in (healths or []) if isinstance(h, Health)]
    counts = {s: 0 for s in STATES}
    for h in items:
        counts[h.state] += 1
    overall = worst(items)
    return {
        "total": len(items),
        "healthy": sum(1 for h in items if h.healthy),
        "counts": counts,
        "overall": overall.as_dict(),
    }
