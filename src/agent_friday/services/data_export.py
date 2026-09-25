"""What an export of ~/.friday leaves out, shared by `friday export` and the
Settings -> Privacy & Approvals export button.

A data export holds the owner's data, not the keys that decrypt their
credentials: anyone holding the keystore root key and the credential blobs
can use every stored API key and account token. Those files go only into a
full backup (`friday export --full`), which is itself encrypted with a
passphrase the owner types. Downloaded model weights and caches are left out
of both: they are large and are fetched again on demand.

Import-light on purpose: `cli.py` imports this on every `friday export`.
"""
from __future__ import annotations

from pathlib import PurePath

TRANSIENT_PARTS = frozenset({"audio-cache", "vibe-code-logs", "__pycache__"})
DOWNLOAD_TOPS = frozenset({"runtime", "local_voice", "models", "cache"})
SECRET_DIRS = (
    ("security",),                 # keystore root key, DPAPI passphrase copy
    ("providers", "keys"),         # provider API keys
    ("google_accounts", "tokens"),
    ("mcp_oauth",),
    ("phone", "secrets"),
)
SECRET_NAMES = frozenset({
    "secret_key",                  # web session secret
    ".governance-key", ".attestation-key-ed25519",
    "ledger_signing.key",
    "key.pem", "ca-key.pem",       # TLS and local-address private keys
})
SECRET_SUFFIXES = (".key", ".dpapi", ".cred", ".oauth.enc", ".token.enc", "-key.pem")


def skip_reason(rel: PurePath, full: bool = False) -> str | None:
    """Why a file at `rel` (relative to ~/.friday) is left out, or None.

    "transient" and "download" apply to every export; "secret" applies unless
    `full` is set.
    """
    parts = tuple(rel.parts)
    if set(parts) & TRANSIENT_PARTS:
        return "transient"
    if parts and parts[0] in DOWNLOAD_TOPS:
        return "download"
    if full:
        return None
    for prefix in SECRET_DIRS:
        if parts[:len(prefix)] == prefix:
            return "secret"
    name = rel.name
    if name in SECRET_NAMES or name.endswith(SECRET_SUFFIXES):
        return "secret"
    return None
