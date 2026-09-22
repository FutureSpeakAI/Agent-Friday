"""Look at every credential Friday holds, on a schedule, and say what is wrong.

THE REASON THIS EXISTS, stated plainly because it is the lesson of 2026-09-19:
seven credentials were found stranded that day, and NOT ONE of them was found
by anybody noticing a symptom. Every one turned up because something finally
enumerated a whole class at once.

  * the Firecrawl key presented as "no API key set". It was present and
    undecryptable, and the user had been told to supply something he had
    already supplied
  * the GitHub token presented as a broken MCP server
  * Drive presented as working
  * the OpenRouter, AtlasCloud and Kie keys presented as nothing at all,
    because nothing looked

`credential_store._credential_files()` was written that morning as a one-off
helper for the keystore migration, and its FIRST RUN found five dead
credentials. A function that valuable should not be a migration detail that
runs once. This is the standing version.

WHAT IT CHECKS, in the order the user cares about:

  1. Does it OPEN? An unreadable credential is a local key fault, not a
     revoked grant, and saying "missing" sends the user to the provider to fix
     something the provider did not break.
  2. Has it EXPIRED, or is it about to? The publishing platforms stored an
     `expires_at` and never once consulted it - a token that expired in August
     reported as connected in September.
  3. Is the service switched off at the provider? Drive, 403, for weeks.

WHAT IT DOES NOT DO. It does not open a network connection, refresh a token,
or touch a provider. A sweep that costs API calls is a sweep somebody turns
off. Everything here is local: decrypt, read a timestamp, compare.

Nothing here logs, prints or returns secret material - only whether each
credential opened, and when it expires.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent_friday.services import connector_health as _ch

#: Warn this far ahead of an expiry. A week is long enough to act on over a
#: weekend and short enough not to cry wolf about a token good until spring.
EXPIRY_WARN_SECONDS = 7 * 24 * 3600

#: How often the background sweep runs. Hourly: these are local file reads,
#: and the failure being watched for (a key drifting out from under a stored
#: credential) appears between restarts rather than between seconds.
SWEEP_INTERVAL_S = 3600.0

#: States worth telling somebody about.
#:
#: ABSENT is not one: a connector nobody has set up is not a fault, and a sweep
#: that reports every unconnected thing as a problem is a sweep that gets
#: muted - and then the eighth stranded credential goes unnoticed for the same
#: reason as the first seven.
#:
#: DEGRADED IS one, which is a judgement call worth stating. It means "working,
#: and one thing it offers is switched off at the provider", so it is not
#: broken - but it is actionable, and Drive sat in exactly that state for weeks
#: reporting itself as fine. The alert is edge-triggered, so it says this once
#: rather than hourly.
_ALERT_STATES = (_ch.UNREADABLE, _ch.NEEDS_USER, _ch.DEGRADED)

_LAST: dict = {}


@dataclass
class Finding:
    id: str
    kind: str                      # provider_key | google | platform | mcp | vault
    label: str = ""
    health: _ch.Health = field(default_factory=_ch.Health)
    expires_at: float | None = None

    @property
    def expiring_soon(self) -> bool:
        return (self.expires_at is not None
                and 0 < (self.expires_at - time.time()) <= EXPIRY_WARN_SECONDS)

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time()

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label or self.id,
                "health": self.health.as_dict(), "expires_at": self.expires_at,
                "expiring_soon": self.expiring_soon, "expired": self.expired}


def _provider_key_findings() -> list:
    from agent_friday.services import credential_store as cs
    out = []
    try:
        names = cs.list_provider_keys()
    except Exception:
        return out
    for name in names:
        try:
            state = cs.provider_key_status(name)
        except Exception as e:
            out.append(Finding(id=name, kind="provider_key", label=name,
                               health=_ch.unknown(
                                   detail="%s: %s" % (type(e).__name__, e),
                                   source="credential_store")))
            continue
        out.append(Finding(id=name, kind="provider_key", label=name,
                           health=_ch.from_provider_key_status(state, name)))
    return out


def _google_findings() -> list:
    out = []
    try:
        from agent_friday.services import google_accounts as G
        for rec in G.list_accounts():
            out.append(Finding(id=rec.get("id") or "", kind="google",
                               label=rec.get("email") or "",
                               health=_ch.from_google_account(rec)))
        # Per-service conditions at the provider: the Drive case.
        for svc in ("gmail", "calendar", "drive", "tasks", "contacts"):
            h = G.service_health(svc)
            if h.state == _ch.DEGRADED:
                out.append(Finding(id="google:%s" % svc, kind="google",
                                   label="Google %s" % svc, health=h))
    except Exception:
        pass
    return out


def _platform_findings() -> list:
    out = []
    try:
        from agent_friday.services import connector_adapters as A
        for conn in A.platform_connectors():
            st = {}
            try:
                st = conn._load()
            except Exception:
                st = {}
            exp = st.get("expires_at")
            try:
                exp = float(exp) if exp is not None else None
            except (TypeError, ValueError):
                exp = None
            h = conn._safe_health()
            # Nothing stored, nothing to say. A platform the user never
            # connected is not a finding.
            if h.state == _ch.ABSENT:
                continue
            out.append(Finding(id=conn.id, kind="platform", label=conn.label,
                               health=h, expires_at=exp))
    except Exception:
        pass
    return out


def _mcp_secret_findings() -> list:
    """Credentials living inside `mcp_servers.json`.

    THE CLASS THAT HID THE GITHUB TOKEN. These are base64 inside a JSON
    document rather than blobs on disk, so `credential_store._credential_files`
    cannot see them - which is why five credentials were recovered on
    2026-09-19 while GitHub kept failing every spawn, and why it took reading
    a connector-health endpoint to notice. A sweep that inherited the same
    blind spot would be the same mistake with a schedule attached.

    Reads the config directly rather than through the MCP manager: the question
    is whether the CREDENTIAL opens, not whether the server is up, and a server
    that is merely stopped must not read as a credential fault.
    """
    import json
    from pathlib import Path

    from agent_friday.core import FRIDAY_DIR
    from agent_friday.services import connector_secrets as cse

    out = []
    path = Path(FRIDAY_DIR) / "mcp_servers.json"
    if not path.exists():
        return out
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return [Finding(id="mcp_servers.json", kind="mcp",
                        label="MCP server config",
                        health=_ch.unknown(
                            detail="%s: %s" % (type(e).__name__, e),
                            source="connector_secrets"))]
    for name, spec in (cfg.get("servers") or {}).items():
        env = (spec or {}).get("env") if isinstance(spec, dict) else None
        if not isinstance(env, dict):
            continue
        for key, value in env.items():
            if not cse.is_encrypted(value):
                continue
            try:
                cse.decrypt_value(value)
                h = _ch.Health(state=_ch.WORKING, source="connector_secrets",
                               source_state="opens",
                               summary="%s / %s opens" % (name, key))
            except Exception as e:
                h = _ch.Health(
                    state=_ch.UNREADABLE, source="connector_secrets",
                    source_state=type(e).__name__, action="unlock",
                    summary="%s cannot decrypt its %s" % (name, key),
                    detail=str(e)[:200])
            out.append(Finding(id="mcp:%s:%s" % (name, key), kind="mcp",
                               label="%s (%s)" % (name, key), health=h))
    return out


def _vault_findings() -> list:
    """Files sealed with a key nothing currently derives.

    Fifteen of these were found on 2026-09-19 - court records, family
    profiles, a job hunt - openable only with a passphrase living in one of
    three launchers. Reported rather than touched.
    """
    out = []
    try:
        from agent_friday.privacy import vault_rekey as vr
        from agent_friday.services import credential_store as cs
        import agent_friday.privacy.vault_crypto as vc

        keys = []
        try:
            cur = cs._vault_key()
            if cur:
                keys.append(cur)
        except Exception:
            pass
        stranded = 0
        for p in vr.encrypted_files():
            blob = p.read_bytes()
            if any(_try(vc, blob, k) for k in keys):
                continue
            stranded += 1
        if stranded:
            out.append(Finding(
                id="vault", kind="vault", label="Encrypted vault files",
                health=_ch.Health(
                    state=_ch.UNREADABLE, source="vault", action="unlock",
                    summary="%d encrypted vault file(s) do not open with the "
                            "key Friday currently derives" % stranded)))
    except Exception:
        pass
    return out


def _try(vc, blob, key) -> bool:
    try:
        vc.decrypt(blob, key)
        return True
    except Exception:
        return False


def inventory() -> dict:
    """Every credential Friday holds, and whether each one actually opens.

    EACH SOURCE IS GUARDED SEPARATELY. A sweep that dies on one unreadable
    store reports nothing about the other twenty, which is precisely the
    failure it exists to prevent - and the stores it reads are the ones most
    likely to be broken, since that is what it is looking for. A source that
    raises becomes a finding saying so, rather than silence.
    """
    findings = []
    for name, fn in (("provider keys", _provider_key_findings),
                     ("google", _google_findings),
                     ("platforms", _platform_findings),
                     ("mcp secrets", _mcp_secret_findings),
                     ("vault", _vault_findings)):
        try:
            findings.extend(fn() or [])
        except Exception as e:
            findings.append(Finding(
                id="source:%s" % name.replace(" ", "_"), kind="source",
                label="The %s store could not be read" % name,
                health=_ch.unknown(detail="%s: %s" % (type(e).__name__, e),
                                   source="credential_sweep")))
    problems = [f for f in findings
                if f.health.state in _ALERT_STATES or f.expired
                or f.expiring_soon]
    return {
        "checked_at": time.time(),
        "total": len(findings),
        "ok": sum(1 for f in findings if f.health.healthy and not f.expired),
        "problems": [f.as_dict() for f in problems],
        "findings": [f.as_dict() for f in findings],
        "summary": ("%d credential(s) need attention" % len(problems)
                    if problems else "%d credential(s), all readable"
                    % len(findings)),
    }


def sweep(notify: bool = True) -> dict:
    """One tick. Pushes a notification for anything newly wrong.

    EDGE-TRIGGERED. A credential that was broken an hour ago and is still
    broken is not news; it is the same news. Re-notifying hourly is how a
    useful alert becomes one the user filters out, and then the eighth stranded
    credential goes unnoticed for the same reason as the first seven.
    """
    inv = inventory()
    fresh = []
    for p in inv["problems"]:
        key = p["id"]
        signature = (p["health"]["state"], p["expired"], p["expiring_soon"])
        if _LAST.get(key) != signature:
            fresh.append(p)
        _LAST[key] = signature
    # Forget anything that has recovered, so it can alert again if it breaks
    # a second time.
    live = {p["id"] for p in inv["problems"]}
    for gone in [k for k in _LAST if k not in live]:
        _LAST.pop(gone, None)

    if notify and fresh:
        _push(fresh)
    inv["new_problems"] = fresh
    return inv


def _push(problems: list) -> None:
    try:
        from agent_friday.services.voice_engine import _notif_engine
        if not _notif_engine:
            return
    except Exception:
        return
    for p in problems:
        h = p["health"]
        # The action is the point. "Reconnect at the provider" and "Friday
        # cannot decrypt this locally" send the user to completely different
        # places, and conflating them cost a reconnect every morning for weeks.
        try:
            _notif_engine.push(
                title="🔑 %s needs attention" % p["label"],
                body="%s%s" % (h.get("summary") or h.get("label"),
                               (" — " + h["detail"]) if h.get("detail") else ""),
                priority="medium", source="credentials",
                kind="credential_problem",
                meta={"credential": p["id"], "state": h.get("state"),
                      "action": h.get("action")},
                target={"workspace": "system"},
                dedupe_key="credential:%s:%s" % (p["id"], h.get("state")),
            )
        except Exception:
            continue


def sweep_loop(interval: float = SWEEP_INTERVAL_S):
    """Background daemon. Sweeps forever."""
    import logging
    log = logging.getLogger("friday.credential_sweep")
    log.info("Credential sweep started.")
    time.sleep(45)          # let the keystore and the stores settle after boot
    while True:
        try:
            inv = sweep()
            if inv["new_problems"]:
                log.warning("credential sweep: %d new problem(s): %s",
                            len(inv["new_problems"]),
                            [p["id"] for p in inv["new_problems"]])
        except Exception as e:
            log.warning("credential sweep failed: %s", e)
        time.sleep(max(60.0, float(interval)))


def _reset_for_tests() -> None:
    _LAST.clear()
