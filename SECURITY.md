# Security Policy

This is the security-policy entry point for Agent Friday™: how to report a
problem, which versions receive fixes, what is in scope, what Friday defends
against, what it guarantees about actions and receipts, and where secrets live. The detailed
design, with every guarantee and its limits, is in
[docs/security/threat-model.md](docs/security/threat-model.md).

## Reporting a vulnerability

Report privately. **Do not open a public issue** for a vulnerability or an
exposed secret.

- **Preferred:** GitHub private vulnerability reporting. On this repository,
  open *Security → Advisories → Report a vulnerability*.
- **Or email** **security@futurespeak.ai** with the subject
  `SECURITY: Agent Friday`.

Include what you found, where (file, commit or release), how to reproduce it,
and the impact you expect. A proof of concept helps but is not required. Do not
include real credentials or other people's data in a report.

What to expect:

- We acknowledge a report within **48 hours**.
- For a critical issue we aim to ship a fix or a mitigation within **7 days**.
  Other issues are fixed in the next release.

If you find an exposed credential, rotation comes first: rewriting history
never un-leaks a value, so the credential is revoked and re-issued before
anything else.

## Scope

In scope:

- The application in this repository: the server, the tray app, the voice
  workers, the phone ingress, the UI files, and the scripts that ship with them.
- The Windows setup program and its scripts in `packaging/windows/`.
- Bypasses of the approval checkpoint, the egress gate, the vault tiers, the
  credential store or the loopback rule described below.
- Secrets, tokens or personal data committed to this repository.

Out of scope:

- Anything that needs an attacker who already controls your Windows account or
  runs code as you (see "What Friday does not defend against" below).
- Physical access to an unlocked or unencrypted machine.
- Weaknesses in a third-party model provider, in a model's own output, or in a
  dependency that Friday does not use in a vulnerable way. Report those to the
  maintainer of that project. Tell us if a dependency advisory affects Friday.
- Reports that come only from an automated scanner, with no demonstrated
  effect on Friday.
- The Windows SmartScreen warning on the setup program. The installer is not
  code signed yet; that is a known limitation of this release.
- Social engineering of maintainers, and denial of service by sending a
  local process unreasonable amounts of work.

## Supported versions

Agent Friday™ Beta 1.2 (version `1.2.0b1`) is the latest release and the only
supported one. Security fixes land on `main` and ship in the next tagged
release, including Beta 1.2 patch releases. They are not back-ported.

| Version | Supported |
|---|---|
| Beta 1.2 (`1.2.0b1`) and its patch releases, the latest only | Yes |
| Beta 1.1, Beta 1.0.2, Beta 1.0.1, Beta 1.0, 5.x and anything older | No. Upgrade by running the new installer; your data is kept. |

Watch the repository's releases for security notes.

## Threat model in brief

Agent Friday is a personal agent that runs on your own Windows PC and acts on
your behalf. Its defences are aimed at the ways such an agent goes wrong.

**What Friday defends against**

- **Prompt injection through content it reads.** Email, web pages, documents
  and tool results are data, not instructions. Friday records where every
  value came from. When a recipient, link, account number, file path, command
  or memory write came from something it read rather than from you, the
  action waits on an approval card that names the source, and a quick "yes"
  in chat does not satisfy it.
- **Actions you did not approve.** Every tool call passes one fail-closed
  checkpoint (`governance/action_gate.py`). Anything that reaches outside the
  machine or changes something you own waits for your decision. An unknown
  or unclassified tool is treated as outward.
- **Exfiltration to cloud models.** An egress gate
  (`services/egress_gate.py`) inspects every payload bound for a cloud
  provider. Unless you answered **Cloud for everything** in setup, it
  withholds private and sensitive content and masks identifiers such as card
  numbers. Under every answer, the never-send floor (your never-send list,
  files you marked never-send, vault legal/finance/family paths and quoted
  lines of held raw records) stops the whole call; if that check cannot run,
  the call is held.
  If the gate fails, the send is blocked.
- **A default read as consent.** The factory routing mode is `cloud_only`,
  but cloud consent starts unanswered and every safeguard stays on until you
  answer the setup chat's privacy question (**Only on this PC**, **Cloud for
  public things** or **Cloud for everything**). No model can record that
  answer.
- **Raw records and pasted keys.** Mail archives and bank or card statements
  reach a cloud model only after a yes for that one file, under every
  posture. A key or password pasted into chat is withheld before the model,
  any store or any index sees it.
- **Remote access through tunnels and proxies.** A request that arrives
  through a tunnel or a reverse proxy is never treated as the local user and
  must log in. Only direct loopback requests, and Friday's own local-address
  proxy on loopback, count as local. Setup, Connect your services and the
  Locks page refuse remote requests even after login.
- **Tampering with its own rules.** The behavioural constraints are
  HMAC-signed and checked against a pinned value before every outward action.
  If the check fails, outward actions stop and reads keep working.

**What Friday does not defend against**

- **A compromised Windows account or malware running with your privileges.**
  Anything that runs as you can read `~/.friday`, including the credential
  keystore, and can change Friday's code. Friday is not a sandbox against the
  account it runs under.
- **Physical access** to an unlocked or unencrypted machine. Use BitLocker.
- **What a cloud provider does with content you chose to send it.** The
  egress gate limits what is sent; it cannot recall what was.
- **Sensitive content the classifier does not recognise.** The classifier is
  pattern-based. "She started sertraline last month" contains nothing that
  looks like a medical record. When a cloud model answers, assume the
  provider may read what you type. The setup chat's privacy step says this
  before you choose.
- **A model that builds a file path from pieces.** The raw-record guard
  checks the paths a tool call names. A model that deliberately assembles a
  path at run time can read around it; the never-send floor still catches
  quoted lines on the way out.
- **Supply-chain compromise** of a Python dependency. Dependencies are
  declared with minimum versions, not exact pins.

## Approval and receipt guarantees

These hold for every tool call from every surface: chat, voice, scheduled
jobs, background tasks and the phone. They are implemented in
`src/agent_friday/services/agent.py` (`_execute_tool` and its hook chain) and
`src/agent_friday/governance/action_gate.py`.

- **One path.** No tool handler runs except through `_execute_tool`, and the
  governance check is its first, critical hook. A critical hook cannot be
  switched off, and an exception in it denies the call.
  `tests/unit/test_every_action_is_governed.py` discovers every registered
  tool and every executor call site and fails on any that bypasses the
  checkpoint.
- **Internal work runs; outward work waits.** Reading, drafting and working
  inside Friday's own output folders are internal. Sending, publishing,
  creating calendar events, installing, overwriting a file Friday did not
  make, or any command that is not on a read-only allowlist is outward.
- **How you approve.**
  - Every confirmation is an **approval card** from the first ask. The same
    card shows in the chat, the Activity panel's **Needs you** tab, the popup
    on every page and **System > Approvals**; one decision wins everywhere.
    An approved card lets that one call through once.
  - In a live conversation on your own page, typing a whole "yes" approves
    the one card it answers, for that exact action with those exact
    arguments. A bare "no" declines only that card. When a detail came from
    content Friday read, the card says where it came from.
  - Some cards are **screen-only**: only a click on Friday's own page
    approves them, never a spoken or typed yes (file grants, starting the
    raw-record hold list fresh, Library changes, loosening or wiping what
    Laya learned).
  - A **scheduled job** can use an outward tool on its own only under a
    **grant** you create in **Settings > Privacy & Data**: it names the job,
    the actions, an expiry and a number of uses. A grant never covers an
    action whose details came from read content.
  - Email asks every time unless you made a **pre-approved send** in
    **Settings > Privacy & Data > Pre-approved sends**: named addresses or
    `@domain` (never "anyone", never a free-mail domain such as
    `@gmail.com`), for any job or chat, one scheduled job, or one skill. A
    send still gets its card if a recipient is not named, if anything came
    from outside content, if it was asked by voice (unless **Voice may use
    pre-approvals** is on; it is off by default), or if it looks personal or
    was written on a cloud model with private details. Editing the job voids
    it; **Revoke** takes back queued sends; every pre-approved send notifies
    you with Undo.
- **Signed receipts.** Each decision is appended to
  `~/.friday/decision-bom.jsonl`, HMAC-SHA256-signed with the governance key.
  If the receipt cannot be written, the outward action does not run.
- **Fail closed.** If the classifier, the integrity check, the optional
  judgment model or the receipt write fails, outward actions are held. Reads
  keep working.
- **The phone is not the owner.** Anything that arrives by text or call is
  untrusted input. A conversation that started by phone can only use
  read-only tools.

## Where secrets live

| Secret | Where | Protection |
|---|---|---|
| Provider API keys, Google tokens, MCP OAuth tokens, platform credentials, phone secrets | `~/.friday/providers/keys/`, `~/.friday/google_accounts/tokens/`, `~/.friday/mcp_oauth/`, `~/.friday/platforms/`, `~/.friday/phone/secrets/` | AES-256-GCM under Friday's own keystore root key (`services/keystore.py`, `services/credential_store.py`). Blobs written by older versions (vault key, Windows DPAPI) are still readable and are migrated. |
| The keystore root key | `~/.friday/security/keystore.json` | 32 random bytes. By default the file holds the key unwrapped, protected by an owner-only file ACL, so Friday can start unattended. With a vault passphrase set, Settings > Privacy & Data > Stored keys wraps it with that passphrase (Argon2id); Friday unwraps it at start-up with the passphrase from Credential Manager, and changing the vault passphrase rewraps it. Nothing changes the wrap without that control. |
| The vault passphrase | Windows Credential Manager (`agent-friday` / `vault-passphrase`) and a DPAPI-protected file, `~/.friday/security/vault-passphrase.dpapi` | Never written to a launch script. `services/vault_passphrase.py` is the single resolver. |
| Vault files (finance, health, legal, family records) | `~/.friday/finance`, `~/.friday/health`, `~/.friday/vault/` | When a vault passphrase is set: AES-256-GCM with a key derived from it by Argon2id. There is no recovery if the passphrase is lost. Without a passphrase these are ordinary readable files. |
| Governance signing key | Windows Credential Manager (`agent-friday` / `governance-key`), falling back to `~/.friday/vault/.governance-key` (owner-only) | Never replaced automatically: an unreadable key is an error, not a reason to mint a new one and orphan old receipts. |
| Ed25519 attestation key | `~/.friday/vault/.attestation-key-ed25519` (owner-only) | File only; used for federation and attestation. |
| Web session secret | `~/.friday/secret_key` (owner-only) | Random, persisted. |
| Keys entered through the `friday setup` terminal wizard | The encrypted store above only | The wizard writes no key to `config.yaml`, `settings.json` or `start.bat`. On its next run it moves plaintext keys an earlier version left in those files into the encrypted store and removes the copies; a key that cannot be stored, or that differs from a key already stored, is left in place and reported. |

Precedence at startup: a real environment variable wins; a key from the
encrypted store beats a key from a launch script; `FRIDAY_VAULT_PASSPHRASE` or
`FRIDAY_PASSWORD` in the environment beats the Credential Manager copy.

## What else the runtime enforces

- **Egress gate, fail-closed.** Cloud calls pass
  `services/model_router._seal_or_block()`, which applies the hard spending
  cap and a size ceiling, then `services/egress_gate.seal_outbound()`. Private
  vault content (TIER_2) becomes a placeholder; sensitive content (TIER_3) is
  withheld. A gate or scrub failure blocks the send. Unrestricted cloud access
  exists only as an explicit, recorded choice you make
  (`privacy/cloud_consent.py`); even then the never-send floor
  (`services/judgment_gate.never_send_hits`) runs on every call and fails
  closed.
- **Vault gate.** With **local-only records** on (the default), vault content
  is stripped from cloud prompts and vault questions go to a local model. A
  refused step becomes one card offering to switch the chat's model or hand
  that task to the local model. A tool that sends nothing off the PC is not
  gated.
- **Pasted secrets.** `services/pasted_secrets.py` replaces a pasted key with
  `[credential withheld]` before the turn starts and again wherever text is
  stored; it is never indexed in conversation search. Keys pasted before
  Beta 1.2 may still be in old history.
- **File grants.** Content-pinned, expiring grants that you create to let one
  specific document cross the gate. No model can create one. See
  [docs/user-guide/file-grants.md](docs/user-guide/file-grants.md).
- **Authentication.** Loopback requests from this PC are trusted as the owner
  (set `FRIDAY_TRUST_LOOPBACK=0` to require a login locally too). Proxied
  requests are not. Credentials are compared in constant time, logins are
  throttled per IP (persisted in SQLite), and cookies are `HttpOnly` and
  `SameSite=Lax`, plus `Secure` when `FRIDAY_COOKIE_SECURE=1`.
- **Sandbox policy.** `FRIDAY_SANDBOX_MODE` (`off`, `confine` by default, or
  `strict`) confines `write_file` to `FRIDAY_SANDBOX_ROOT`; `strict` adds a
  command allowlist. This sits behind the governance checkpoint, which is the
  stronger control for commands.
- **Worker isolation.** Subprocesses spawned for delegated work receive an
  explicit environment allowlist, never the server's environment.
- **Phone ingress.** Twilio webhooks are served by a separate listener on
  `127.0.0.1:3011` that has no Friday routes. Every request is checked for
  Twilio's signature, the account, replays and rate limits, and fails closed.

## No telemetry

Friday contains no telemetry, analytics, crash reporting or license check.
The network requests it makes on its own are listed in
[docs/user-guide/background-network.md](docs/user-guide/background-network.md);
the weekly update check is one of them and runs only if you said yes to it.

## Repository hygiene

This repository is public. Nothing in it may contain API keys or tokens,
passwords or passphrases, private keys, personal identifiers (personal email
addresses, phone numbers, government IDs, home addresses), family or medical
data, or local paths that reveal a username.

A pre-commit scanner enforces this. Enable it once after cloning:

```bash
git config core.hooksPath .githooks
```

It scans staged additions, masks what it finds, and blocks the commit; an
error in the scanner also blocks it. A false positive is allowlisted per line
with `# pragma: allowlist secret`. GitHub secret scanning with push protection
is enabled on the repository as a second layer.

## Deliberately public values

A Google OAuth client ID and secret placed in
`src/agent_friday/services/google_oauth_client.py` would be public by design:
Friday is a native application and cannot keep a client secret (RFC 8252).
In the current release both values are empty, so no client is bundled and
every Google connection uses your own client. The
[threat model](docs/security/threat-model.md) explains what such a value does
and does not grant.
