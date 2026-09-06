# Security Policy

This is the single security-policy entry point for Agent Friday. It covers how
to report a problem, which versions receive fixes, how credentials are stored,
and what the runtime enforces. The deeper design — trust boundaries, what is
and is not defended against, and the exact guarantees of each mechanism — is in
[docs/security/threat-model.md](docs/security/threat-model.md).

## Reporting a vulnerability

- Open a **private** GitHub security advisory: *Security → Advisories →
  Report a vulnerability* on this repository.
- Or email **security@futurespeak.ai** with the subject `SECURITY: Agent Friday`.

Include what you found, where (file, commit, or release), and the impact.
**Do not open a public issue** for an active secret or an exploitable defect.
We acknowledge reports within 48 hours and aim to ship a fix or mitigation
within 7 days for critical issues.

If you find an exposed credential, rotation comes first: a history rewrite
never un-leaks a value, so we revoke and re-issue before anything else.

## Supported versions

Agent Friday is a fast-moving personal project. Security fixes land on `main`
and ship in the next tagged release; they are not back-ported.

| Version | Supported |
|---------|-----------|
| The latest tagged release (currently the 5.13 line) | Yes |
| Anything older | No — upgrade |

Pin a tag for stability and watch the repository's releases for security notes.

## How credentials are stored

There are three places a credential can live, and they are not equivalent.

| Credential | Where it lives | Protection |
|---|---|---|
| Provider API keys entered in **Settings → Providers** | `~/.friday/providers/keys/<provider>.key` | Encrypted by `services/credential_store.py`: the vault key (Argon2id → AES-256-GCM) when a vault passphrase is set, otherwise Windows DPAPI, otherwise plaintext with a one-time warning and restricted file permissions. |
| Provider API keys entered through the **`friday setup` wizard** (source checkout) | `~/.friday/settings.json`, `~/.friday/config.yaml`, and a `start.bat` launcher in the checkout | **Plaintext.** `start.bat` is gitignored and never shipped; the settings files live outside the repository. Treat these files as containing live secrets. Use Settings → Providers for the encrypted store. |
| The vault passphrase | The OS keychain and a DPAPI-wrapped file under `~/.friday/security/` | Never written to any launch script. `services/vault_passphrase.py` is the single resolver. |
| Connected-account (Google, MCP) tokens | `~/.friday/` | Encrypted through the same `credential_store` mechanism. |
| Governance HMAC key and Ed25519 attestation key | OS keychain via `keyring`, with a `0600` file fallback under `~/.friday/vault/` | The fallback is logged as a warning. |

Precedence at startup: a real environment variable wins; a key from the
encrypted store beats a key sourced from a launch script; `FRIDAY_PASSWORD` /
`FRIDAY_VAULT_PASSPHRASE` in the environment beats the keychain copy.

On macOS and Linux there is no DPAPI, so provider keys fall back to plaintext
unless a vault passphrase is set. That is stated in the platform-support section
of the README rather than hidden here.

## What the runtime enforces

Every item below is implemented in the current tree; each names its module so
the claim can be checked.

- **Egress gate, fail-closed.** Every cloud call passes through
  `services/model_router._seal_or_block()` → `services/egress_gate.seal_outbound()`.
  Content the sensitivity classifier cannot confirm as public is redacted or
  withheld; a gate failure blocks the send rather than allowing it. Which
  classifier layers are active depends on how you installed Friday — the boot
  log prints the real count, and the threat model explains why the frozen
  `.exe` runs two layers of pattern matching.
- **Vault tiers.** `privacy/vault_access.py` classifies vault content as
  TIER_1 (public), TIER_2 (private — cloud receives a placeholder) or TIER_3
  (sensitive — cloud receives nothing). Unrestricted cloud access exists only
  as an explicit, recorded user decision (`privacy/cloud_consent.py`); it is
  never inherited from a routing preference.
- **File grants.** Content-pinned, expiring, user-only grants that let a
  specific document cross the gate; no model can create one.
  [docs/user-guide/file-grants.md](docs/user-guide/file-grants.md).
- **Authentication.** A persisted random session secret (`~/.friday/secret_key`,
  mode `0600`); constant-time credential comparison (`hmac.compare_digest`); a
  per-IP login throttle persisted in SQLite; `HttpOnly` + `SameSite=Lax`
  cookies, `Secure` when `FRIDAY_COOKIE_SECURE=1`. Loopback requests are
  trusted by default (`FRIDAY_TRUST_LOOPBACK=0` requires login locally too).
  The voice WebSocket accepts `FRIDAY_WS_TOKEN` or an ephemeral session token.
- **Tool sandbox.** Every tool call passes `_governance_check()` (privilege
  rings 0–3) and the `FRIDAY_SANDBOX_MODE` policy (`off` / `confine` [default]
  / `strict`): path-affecting tools are confined to `FRIDAY_SANDBOX_ROOT`, and
  `run_command` is checked against a destructive-command blocklist with
  word-boundary matching.
- **Spend controls.** An alert-only budget (default) and an opt-in hard stop
  that refuses further cloud calls when reached (`services/spend_guard.py`).
- **Worker isolation.** Subprocesses spawned for delegated work receive an
  explicit environment allowlist, never the server's environment.

## Repository hygiene

This repository is public. Nothing in it may contain API keys or tokens,
passwords or passphrases, private keys, personal identifiers (personal email
addresses, phone numbers, government IDs, home addresses), family or medical
data, or local filesystem paths that reveal a username.

A pre-commit scanner enforces this. Enable it once after cloning:

```bash
git config core.hooksPath .githooks
```

It scans staged additions and blocks the commit with a clear message. A
false positive is allowlisted per line with `# pragma: allowlist secret`; a
deliberately public credential (the bundled Google OAuth client, see the
threat model) is named in the scanner's own allowlist with its reasoning.
GitHub secret scanning with push protection is enabled on the repository as a
second layer.

## Deliberately public values

The Google OAuth client ID and secret in
`src/agent_friday/services/google_oauth_client.py` are public by design:
Friday is a native application and cannot keep a client secret (RFC 8252).
The threat model explains what that value does and does not grant.
