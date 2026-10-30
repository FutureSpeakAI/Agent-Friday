"""observer_access — the scoped, durable, READ-ONLY credential for orchestrators.

Design: docs/design/active/task-visibility.md §4.5 and the maintainer's
ruling on its Q5. Orchestrator agents observe running work; steer and cancel
stay with the user. A credential that can read a journal and also cancel a
task would be a different product than the one approved, so read-only is
enforced structurally, not by convention:

  * A request that presents ``X-Friday-Observer`` is an OBSERVER for the
    whole request, before and regardless of loopback trust. Presenting the
    header demotes; it never elevates.
  * An observer may call only ``GET`` on the allowlisted read routes below.
    Every other method or path — steer, cancel, delete, dismiss, settings,
    spawning, minting another token — is refused with 403 at the app-wide
    auth hook, so no view code has to remember.
  * An invalid token is 401. There is exactly one token at a time; minting a
    new one revokes the old.

The token itself is never stored: only its SHA-256 hash, under
``<friday home>/security/observer_token.sha256``. The plaintext is returned
once by mint() and never again.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path
from typing import Optional

HEADER = "X-Friday-Observer"

# The two route FAMILIES the observer reads from (for the operator doc). What decides
# is READ_ONLY_RULES below: an exact list of route rules, GET only.
#
# Every route here must seal free text for the observer principal
# (routes/tasks._serve_sealed) or serve none. /api/processes and the
# orchestrator status/workers/results routes do not seal orb logs and worker
# outputs, so they stay off this list until they do. The three documented reads (list, digest, tail)
# and the journal all live under /api/tasks.
READ_ONLY_PREFIXES = (
    "/api/tasks",              # list, detail, journal, digest, events, retention (GET only; sealed)
    "/api/activity",           # the activity ledger (whitelisted metadata, 200-char cap)
)


def _hash_path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "security" / "observer_token.sha256"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint() -> str:
    """Create (or replace) the observer token. Returns the plaintext ONCE."""
    token = "fobs_" + secrets.token_urlsafe(32)
    p = _hash_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(_digest(token), encoding="utf-8")
    tmp.replace(p)
    try:
        import os
        os.chmod(p, 0o600)
    except Exception:
        pass
    return token


def revoke() -> bool:
    p = _hash_path()
    if p.exists():
        p.unlink()
        return True
    return False


def exists() -> bool:
    return _hash_path().exists()


def verify(token: Optional[str]) -> bool:
    if not token:
        return False
    p = _hash_path()
    if not p.exists():
        return False
    try:
        stored = p.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return hmac.compare_digest(stored, _digest(token.strip()))


#: The read routes an observer may use, as Flask route RULES, each of which seals its
#: free text for the observer principal (routes/tasks._serve_sealed) or serves none.
#: A rule, not a path prefix: a route added later under /api/tasks (a scoped-agent
#: list, a resumability reason) is refused to the observer until it is put here,
#: after it seals (NS-34.8-1, NS-34.17-2). tests/api/test_principal_isolation.py
#: walks every rule the app has and checks this.
READ_ONLY_RULES = frozenset({
    "/api/tasks",                          # list (sealed)
    "/api/tasks/<task_id>",                # detail (sealed)
    "/api/tasks/<task_id>/journal",        # journal (sealed, ledgered)
    "/api/tasks/<task_id>/digest",         # digest (sealed)
    "/api/tasks/<task_id>/events",         # events (sealed)
    "/api/tasks/retention",                # the retention setting (a number)
    "/api/activity",                       # the activity ledger (whitelisted metadata)
})


def is_read_allowed(method: str, path: str, rule: Optional[str] = None) -> bool:
    """GET on one of READ_ONLY_RULES. `rule` is the matched Flask route rule; without
    one (no route matched) nothing is allowed."""
    if (method or "").upper() != "GET":
        return False
    if rule is None:
        return False
    return str(rule) in READ_ONLY_RULES


def presented(headers) -> Optional[str]:
    """The observer token if the request presents one, else None."""
    try:
        val = headers.get(HEADER)
    except Exception:
        return None
    return val.strip() if val else None
