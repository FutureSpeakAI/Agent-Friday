"""Boot-critical health contract — the ONE place that decides whether Friday
booted into a working state (PR-6 of the OS-mode sequence — see Friday-Linux
docs/SPEC.md Section 13 and greenboot's `30-health.sh`).

WHY A SEPARATE MODULE.

`/api/health` (routes/core_routes.py) already existed as a general
system-status snapshot — uptime, inference reachability, model catalog, vault
warnings, the About panel's numbers. None of that answers the one question a
boot-health gate actually needs: "did Friday come up in a state fit to be
scored healthy by whatever put it there?" That is a narrower, harder-edged
question with a real caller (greenboot) that treats an unhealthy verdict as a
reason to roll back a whole OS deployment. So the logic that answers it lives
in ONE module, imported by both the HTTP route (so a human/monitor reads it as
JSON) and `friday health --exit-code` (so a boot script reads it as an exit
code), instead of being reimplemented in each — the same reasoning
`services/app_version.py` documents for "what version is this".

WHAT COUNTS AS BOOT-CRITICAL, AND WHY THESE FOUR.

  * config              — Friday cannot make a single correct decision (which
                           model, which routing mode, which seat) once its own
                           settings cannot be read. `core._load_settings_raw()`
                           SILENTLY forgives a corrupt settings.json — it
                           reverts to factory defaults so the running app does
                           not crash — which is the right call for a live
                           process and the wrong call for a health check,
                           whose entire job is to surface exactly the thing
                           that fail-soft path is designed to hide. This
                           module re-reads the same file and reports the parse
                           failure that loader swallows.
  * credential_store     — PR-5 made credential writes fail CLOSED under
                           FRIDAY_OS_MODE: with no FRIDAY_PASSWORD /
                           FRIDAY_VAULT_PASSPHRASE and no DPAPI available,
                           `credential_store.protect()` now raises instead of
                           falling back to plaintext. That is correct for the
                           write path and a real boot hazard for the read
                           path: a sealed image that cannot protect (or
                           re-open) a credential can still start Flask and
                           answer a liveness probe while every provider call
                           downstream fails. This check performs a REAL
                           write+read round trip through
                           `services.credential_store` so a misconfigured
                           vault on a fresh OS-mode image is caught here, not
                           three requests later inside an unrelated 500.
  * memory_db            — `services.memory_dreaming`'s sqlite store
                           (`dreams.db`) is a real on-disk database this
                           install already depends on. Opening it and running
                           a trivial query is genuine proof the disk under
                           FRIDAY_HOME is writable and the file is not
                           corrupt or half-written — not just that the path
                           string can be computed.
  * http_serving         — definitionally proven the moment this code is
                           executing inside a live `/api/health` request: the
                           request could not have been routed, dispatched and
                           handled by Flask if HTTP serving were not working.
                           No separate probe is needed from inside the route.
                           `friday health --exit-code` runs OUT OF PROCESS and
                           has no such free proof — it is a plain HTTP client
                           against the running server's own `/api/health`
                           (see cli.py's `cmd_health`), so unreachability
                           there already surfaces as its own top-level
                           failure without ever calling into this module.

Model seats, voice and cloud providers are reported here too (so `subsystems`
shows the whole picture, and so "these are explicitly non-critical" is
something a test can hold the code to) but NEVER counted toward
`boot_critical_ok` — a sealed kiosk with no cloud key configured yet, no voice
engine chosen, or no model seat picked is a normal, healthy, pre-setup state,
not a boot failure.

CROSS-MODULE FRIDAY_HOME NOTE (pre-existing, not introduced by this PR).
`agent_friday.core.FRIDAY_DIR` (used by `check_config` and
`check_credential_store` below, because that is genuinely where the running
Flask app's config and credential store live) is `Path.home()/.friday` and
does NOT read FRIDAY_HOME at all — a gap `agent_friday.paths.runtime_dir()`'s
docstring already flags for a different function. `services.memory_dreaming`
(used by `check_memory_db`) has a THIRD, independent convention:
`Path(FRIDAY_HOME or Path.home()) / ".friday"`. This module does not attempt
to reconcile the three; each check uses whatever path convention the real
subsystem it is checking actually uses, because a health check that inspects
a directory the subsystem does not itself use would be worse than no check at
all. See the PR description for what this means for FRIDAY_HOME-only
invocations.
"""
from __future__ import annotations

import json
import os
import time as _time
from typing import Optional, Tuple

#: Bumped whenever a key is added, removed, or changes meaning. A consumer
#: (greenboot included) should treat an unrecognized higher version as
#: "unknown shape, do not trust field absence by default" rather than
#: silently misreading it.
SCHEMA_VERSION = 1

#: /api/health is polled every ~15s by the UI's top-bar connection dot (see
#: ui_parts/app.html) and by the tray watchdog's readiness loop. The
#: credential-store check performs a real disk write + permission-hardening
#: pass (icacls on Windows) each time it runs uncached, and the vault key
#: derivation it may trigger is Argon2id — deliberately slow. Caching here
#: follows the same pattern `services/provider_health.py` already uses for
#: the same reason ("/api/health is polled often").
_CACHE_TTL_S = 20.0
_cache: dict = {"ts": 0.0, "report": None}


def reset_health_cache() -> None:
    """Clear the cached report. Tests call this so a monkeypatched subsystem
    is reflected on the very next call instead of a stale cached verdict."""
    _cache["ts"] = 0.0
    _cache["report"] = None


def check_config() -> Tuple[bool, str]:
    """Can the running app's own settings.json actually be parsed?"""
    try:
        import agent_friday.core as core
    except Exception as e:
        return False, f"agent_friday.core could not be imported: {type(e).__name__}: {e}"
    path = core.SETTINGS_FILE
    if not path.exists():
        return True, "settings.json not present yet — running on factory defaults (fresh install)"
    try:
        # utf-8-sig for the same reason core._load_settings_raw() reads it
        # that way — a BOM'd file (PowerShell's Out-File writes one) is a
        # JSONDecodeError under plain utf-8, and this check exists precisely
        # to surface a parse failure the app's own loader would otherwise
        # swallow and silently revert to defaults.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return False, (f"settings.json parsed but is not a JSON object "
                           f"(got {type(data).__name__})")
        return True, f"settings.json parses ({len(data)} keys)"
    except Exception as e:
        return False, f"settings.json exists but does not parse: {type(e).__name__}: {e}"


def check_credential_store() -> Tuple[bool, str]:
    """Can a secret actually be written and read back right now?

    A real round trip through services.credential_store — not a capability
    guess. Under FRIDAY_OS_MODE with no vault passphrase and no DPAPI, PR-5
    made write_secret() raise instead of silently falling back to plaintext
    (see credential_store.protect()); that raise is exactly what this check
    needs to see, since every provider call downstream of a broken credential
    store fails the same way, just later and less clearly.
    """
    try:
        import agent_friday.core as core
        from agent_friday.services import credential_store as cs
    except Exception as e:
        return False, f"credential_store could not be imported: {type(e).__name__}: {e}"
    canary_path = core.FRIDAY_DIR / "security" / ".health_check_canary"
    canary_value = b"friday-health-check-canary"
    try:
        method = cs.write_secret(canary_path, canary_value)
        readback = cs.read_secret(canary_path)
        if readback != canary_value:
            return False, "credential store round trip returned different bytes than were written"
        return True, f"write+read round trip OK (protection: {method})"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            canary_path.unlink(missing_ok=True)
        except Exception:
            pass


def check_memory_db() -> Tuple[bool, str]:
    """Does the memory-consolidation database actually open and answer a query?"""
    try:
        from agent_friday.services import memory_dreaming as md
    except Exception as e:
        return False, f"memory_dreaming could not be imported: {type(e).__name__}: {e}"
    try:
        conn = md._connect()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return True, f"{md.DB_PATH.name} opens and answers a query"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_http_serving(served_over_http: bool) -> Tuple[bool, str]:
    """Is Friday actually serving HTTP requests right now?

    `served_over_http=True` means this call is happening INSIDE a live Flask
    request handling /api/health itself — the answer is definitionally yes,
    since the request could not be here otherwise, so no separate probe is
    made. `/api/health`'s own route in routes/core_routes.py is the only
    caller that passes True, for exactly that reason.

    A caller that is NOT inside such a request (e.g. `friday health
    --exit-code`, which runs post-install / out-of-process and must not
    require the server to already be up) has no such free proof and must
    pass False here — see `_probe_http` below for how `boot_critical_report`
    lets that caller supply real evidence instead.
    """
    if served_over_http:
        return True, "this request was served over HTTP, which is the proof"
    return False, "not called from within a live HTTP request — cannot claim HTTP serving definitionally"


def _probe_http(url: str, timeout: float = 3.0) -> Tuple[bool, str]:
    """Real evidence of HTTP serving for a caller OUTSIDE the Flask process
    (the CLI). A plain GET with a short timeout — anything under 500 counts as
    "serving" (the same bar `friday_tray.py`'s `_wait_for_health` uses), since
    the point is "is something answering on this port", not this endpoint's
    own inference/auth verdict.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status < 500:
                return True, f"GET {url} -> HTTP {r.status}"
            return False, f"GET {url} -> HTTP {r.status}"
    except Exception as e:
        return False, f"GET {url} failed: {type(e).__name__}: {e}"


def check_cloud_providers() -> Tuple[bool, str]:
    """NON-CRITICAL. At least one cloud provider API key is configured."""
    try:
        import agent_friday.core as core
    except Exception as e:
        return False, f"could not check: {type(e).__name__}: {e}"
    have = [name for name, val in (("anthropic", core.ANTHROPIC_API_KEY),
                                   ("gemini", core.GEMINI_API_KEY)) if val]
    if have:
        return True, "configured: " + ", ".join(have)
    return False, "no cloud provider API key configured (local-only, or not set up yet)"


def check_model_seats() -> Tuple[bool, str]:
    """NON-CRITICAL. An orchestrator or subagent model seat is configured."""
    try:
        import agent_friday.core as core
        settings = core._load_settings()
    except Exception as e:
        return False, f"could not check: {type(e).__name__}: {e}"
    orch = settings.get("orchestrator_model")
    sub = settings.get("subagent_model")
    if orch or sub:
        return True, f"orchestrator={orch or '—'}, subagent={sub or '—'}"
    return False, "no orchestrator/subagent model seat configured yet"


def check_voice() -> Tuple[bool, str]:
    """NON-CRITICAL. A voice model/engine is configured."""
    try:
        import agent_friday.core as core
        settings = core._load_settings()
    except Exception as e:
        return False, f"could not check: {type(e).__name__}: {e}"
    voice = settings.get("voice_model") or settings.get("tts_voice")
    if voice:
        return True, f"voice={voice}"
    return False, "no voice model configured yet"


def deployment_id() -> str:
    """Which OS image / deployment this is running under.

    HONEST PLACEHOLDER. This repo has no ostree/deployment-generation concept
    today (verified by a repo-wide grep before writing this: no "ostree",
    "deployment_id" or "rpm-ostree" reference anywhere in the tree). Friday
    Linux's greenboot integration will eventually need a real one — most
    likely the active `rpm-ostree status` deployment's checksum or index,
    injected as an environment variable by the image build or the systemd
    unit, since this process has no way to ask the host OS about itself
    directly. Until that exists, FRIDAY_DEPLOYMENT_ID is read if set (so
    wiring it up later needs no code change here), and "unknown" is returned
    otherwise — deliberately, rather than fabricating a build number or git
    SHA that would look authoritative and mean nothing to greenboot.
    """
    return os.environ.get("FRIDAY_DEPLOYMENT_ID", "").strip() or "unknown"


def boot_critical_report(served_over_http: bool = True, *,
                         http_probe_url: Optional[str] = None,
                         use_cache: bool = True) -> dict:
    """The full boot-health contract.

    `served_over_http` — pass True ONLY from inside the /api/health Flask
    route itself (see `check_http_serving`). Every other caller passes False.

    `http_probe_url` — when `served_over_http` is False, an optional URL
    (e.g. `http://localhost:3000/api/health`) to GET as real evidence the
    server is up. None means no such evidence is available (http_serving
    reports unhealthy, honestly, rather than guessing). Ignored when
    `served_over_http` is True.

    Returns::

        health_schema_version  int   — see SCHEMA_VERSION.
        boot_critical_ok       bool  — AND of every CRITICAL subsystem's `ok`.
        boot_status            str   — "ok" | "degraded" | "failed". "failed"
                                        iff boot_critical_ok is False;
                                        "degraded" iff boot_critical_ok is True
                                        but some NON-critical subsystem is
                                        unhealthy; "ok" otherwise.
        subsystems              dict  — name -> {ok, detail, critical}.
        deployment              str   — see deployment_id().

    See the module docstring for what each subsystem means and why it is (or
    is not) boot-critical.
    """
    now = _time.monotonic()
    if use_cache and _cache["report"] is not None and (now - _cache["ts"]) < _CACHE_TTL_S:
        return _cache["report"]

    if served_over_http:
        http_result = check_http_serving(True)
    elif http_probe_url:
        http_result = _probe_http(http_probe_url)
    else:
        http_result = check_http_serving(False)

    checks = [
        ("config", True, check_config()),
        ("credential_store", True, check_credential_store()),
        ("memory_db", True, check_memory_db()),
        ("http_serving", True, http_result),
        ("cloud_providers", False, check_cloud_providers()),
        ("model_seats", False, check_model_seats()),
        ("voice", False, check_voice()),
    ]
    subsystems = {}
    boot_critical_ok = True
    any_unhealthy = False
    for name, critical, (ok, detail) in checks:
        subsystems[name] = {"ok": ok, "detail": detail, "critical": critical}
        if not ok:
            any_unhealthy = True
            if critical:
                boot_critical_ok = False

    if not boot_critical_ok:
        boot_status = "failed"
    elif any_unhealthy:
        boot_status = "degraded"
    else:
        boot_status = "ok"

    report = {
        "health_schema_version": SCHEMA_VERSION,
        "boot_critical_ok": boot_critical_ok,
        "boot_status": boot_status,
        "subsystems": subsystems,
        "deployment": deployment_id(),
    }
    if use_cache:
        _cache["ts"] = now
        _cache["report"] = report
    return report
