"""Encryption at rest for MCP connector credentials.

``~/.friday/mcp_servers.json`` holds the environment each connector's server
process is spawned with, and for most connectors that environment IS the
credential: an Airtable personal access token, a Gmail app password, a Slack
bot token, a GitHub PAT. Those were written in plaintext, which is ordinary for
an MCP config file and wrong for this product — Friday's claim is that your
data stays on your machine AND under your control, and a secret sitting in
readable JSON is only the first half of that.

Two exposures, not one. The file is the obvious one. The quieter one is
``GET /api/mcp/servers``, which returns the config verbatim, so every connector
token was also handed to the browser on request. Both close here, because the
value is ciphertext from the moment it is written until the moment the server
process is spawned.

``credential_store`` already does the hard part and already protects the Google
OAuth tokens: it picks the strongest mechanism the host offers (vault key from
FRIDAY_PASSWORD, else Windows DPAPI, else plaintext with a loud warning) and
every blob is self-describing, so decryption never has to be told which was
used. This module is the thin JSON-safe layer over it — base64, because the
value has to survive a round trip through JSON and through a browser.

Deliberately NOT encrypting everything. ``ALLOWED_DIRS``, ``SLACK_TEAM_ID`` and
the like are configuration, not credentials: encrypting them would make the
file impossible to hand-fix and would blind ``extension_security.assess_config``,
which reasons about what a server is allowed to reach. ``looks_secret`` decides,
and it errs toward encrypting — a wrongly-encrypted config value is an
inconvenience, a wrongly-plaintext token is the thing this module exists for.
"""

from __future__ import annotations

import base64
import json
import re

#: Prefix marking an encrypted value. The full envelope is
#: ``friday-enc:v1:<method>:<base64 blob>``. Versioned because the payload is a
#: credential_store blob and a future change of envelope must be
#: distinguishable from this one rather than guessed at; the method is named
#: because a blob that does not match its declared method is a real condition
#: worth failing on, and credential_store.unprotect cannot tell on its own
#: (unwrapped bytes are legitimately plaintext for a host with no encryption).
SECRET_MARKER = "friday-enc:v1:"

#: Substrings that make an environment variable a credential. Matched
#: case-insensitively against the whole name.
_SECRET_HINTS = (
    "token", "secret", "password", "passwd", "api_key", "apikey",
    "access_key", "private_key", "credential", "auth", "_pat", "pat_",
    "session", "cookie", "signature", "webhook",
)

#: Names that end in a plainly non-credential noun, checked before the hints so
#: that SLACK_TEAM_ID does not match on "team" and AIRTABLE_BASE_ID stays
#: readable. A suffix test, because that is where the noun lives.
_NOT_SECRET = re.compile(
    r"^(?:.*_)?(?:id|ids|dir|dirs|path|url|host|port|env|level|mode|team|"
    r"base|region|version|timeout|enabled)$", re.I)


def looks_secret(key: str) -> bool:
    """True if this environment variable name names a credential.

    Name-based rather than value-based on purpose: a value-based guess would
    have to look at the secret in order to decide whether to protect it, and
    would get short tokens wrong in the dangerous direction.
    """
    k = str(key or "")
    if not k:
        return False
    if _NOT_SECRET.match(k):
        return False
    low = k.lower()
    return any(h in low for h in _SECRET_HINTS)


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(SECRET_MARKER)


def encrypt_value(value: str) -> str:
    """Protect one value. Idempotent, and a no-op on an empty string.

    Idempotency is load-bearing: the raw-config route hands this object to the
    browser and takes it straight back, so an already-encrypted value arrives
    here again on every save. Wrapping it twice would mean one decrypt returns
    ciphertext, which the connector would then present as its token.
    """
    if not isinstance(value, str) or not value or is_encrypted(value):
        return value
    from agent_friday.services import credential_store
    blob, method = credential_store.protect(value.encode("utf-8"))
    return (SECRET_MARKER + method + ":"
            + base64.b64encode(blob).decode("ascii"))


def decrypt_value(value: str) -> str:
    """Inverse of encrypt_value. A value with no marker is returned unchanged.

    That pass-through is what makes migration safe: a config written before
    this module existed reads correctly on the first load, and is upgraded on
    the way past.

    Raises rather than returning the ciphertext when the blob will not open —
    a DPAPI blob copied to another machine is the realistic case. Handing the
    ciphertext on as if it were the token would produce an auth failure deep
    inside somebody else's MCP server, a long way from the cause.
    """
    if not is_encrypted(value):
        return value
    from agent_friday.services import credential_store
    body = value[len(SECRET_MARKER):]
    method, _, payload = body.partition(":")
    if not payload:
        raise ValueError("malformed encrypted connector credential "
                         "(no protection method in the envelope)")
    raw = base64.b64decode(payload, validate=False)
    if method != "plaintext" and credential_store.looks_protected(raw) is None:
        # The envelope says this was encrypted and the bytes say it was not.
        # unprotect() would shrug and return them as plaintext, which is right
        # for a value that was never wrapped and wrong for one that was: a
        # DPAPI blob carried to another machine lands here, and passing it on
        # would send binary noise as the token.
        raise ValueError(
            f"connector credential claims {method} protection but carries no "
            f"{method} envelope — it was written on another machine or under "
            f"another login. Reconnect the connector to re-enter it.")
    return credential_store.unprotect(raw).decode("utf-8")


def encrypt_env(env: dict) -> dict:
    if not isinstance(env, dict):
        return env
    return {k: (encrypt_value(v) if looks_secret(k) and isinstance(v, str) else v)
            for k, v in env.items()}


def decrypt_env(env: dict) -> dict:
    if not isinstance(env, dict):
        return env
    return {k: (decrypt_value(v) if is_encrypted(v) else v)
            for k, v in env.items()}


# ── Config-level helpers ─────────────────────────────────────────────────────
def encrypt_config(cfg: dict) -> dict:
    """Return `cfg` with every server's secret env values protected.

    A new dict; the caller's object is not mutated, because the caller is
    usually about to hand the same object back to a browser.
    """
    if not isinstance(cfg, dict):
        return cfg
    servers = cfg.get("servers")
    if not isinstance(servers, dict):
        return cfg
    out = dict(cfg)
    out["servers"] = {
        name: (dict(spec, env=encrypt_env(spec.get("env") or {}))
               if isinstance(spec, dict) and isinstance(spec.get("env"), dict)
               else spec)
        for name, spec in servers.items()}
    return out


def config_has_plaintext_secrets(cfg: dict) -> bool:
    servers = (cfg or {}).get("servers")
    if not isinstance(servers, dict):
        return False
    for spec in servers.values():
        env = (spec or {}).get("env") if isinstance(spec, dict) else None
        if not isinstance(env, dict):
            continue
        for k, v in env.items():
            if looks_secret(k) and isinstance(v, str) and v and not is_encrypted(v):
                return True
    return False


# ── One-time migration of secrets written before this module existed ─────────
_MIGRATED = False


def reset_migration_state_for_tests() -> None:
    global _MIGRATED
    _MIGRATED = False


def migrate_config_file(path, cfg: dict) -> dict:
    """Upgrade plaintext secrets in `cfg`, rewriting `path` if any were found.

    Runs at most once per process and writes only when there is something to
    write, so the common case (already encrypted) costs one scan of a small
    dict and no disk I/O at all.

    A fix that protected only NEW writes would leave every existing install
    exactly where it was, which for this defect is the entire population.
    """
    global _MIGRATED
    if _MIGRATED or not config_has_plaintext_secrets(cfg):
        return cfg
    _MIGRATED = True
    upgraded = encrypt_config(cfg)
    try:
        from pathlib import Path as _Path
        p = _Path(path)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(upgraded, indent=2), encoding="utf-8")
        tmp.replace(p)
        try:
            from agent_friday.services import credential_store
            credential_store.harden_permissions(p)
        except Exception:
            pass
        print("  [MCP] upgraded plaintext connector credentials in "
              f"{p.name} to encryption at rest")
    except Exception as e:
        # The in-memory upgrade still stands, so this process is fine; the file
        # simply stays as it was and the next start tries again. Never fatal —
        # a migration that cannot write must not take the connectors down.
        print(f"  [MCP] could not rewrite {path} with encrypted credentials: {e}")
    return upgraded
