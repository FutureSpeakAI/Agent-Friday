# Sovereign hostname on agent naming — design note

**Status:** design only (not built). **Belongs in:** the v6 "Wholeness"
**self-healing-install** phase (P7) — fold this into `docs/V6_WHOLENESS_SPEC.md`
rather than shipping standalone.

## The idea

When a new user sets up Friday and **names their agent**, offer to provision a clean
sovereign hostname so they get **`https://<name>.local`** (or `agent.<name>`) with a
real padlock instead of `localhost:<port>`. Every user, out of the box, addresses
their agent by name over trusted TLS — no port, no cert warning, nothing exposed to
the network.

This generalizes the one-off `agent.friday` demo proxy (see `ops/`) into a
product feature keyed off the naming step.

## What it provisions (per platform, same three primitives)

1. **Hosts entry** → `127.0.0.1 <name>.local` + `::1 <name>.local` (loopback only).
2. **Reverse proxy** with a **trusted local CA** → `https://<name>.local` fronting
   the app's existing loopback port. Caddy `tls internal` + `bind 127.0.0.1 ::1`.
3. **Auto-start on boot** so the hostname is always up.

The working Windows implementation already exists in `ops/` and is the reference:
loopback-only `Caddyfile`, `X509Store` CA install (Caddy's own `trust` no-ops here),
`caddy run` under a boot Scheduled Task as SYSTEM (never `caddy start`/`stop` — they
hang), machine-wide storage so SYSTEM and user share one CA, and a hosts self-heal
loop.

## Steps to automate (on "name your agent" → user consents)

1. Slugify the name → validate (`^[a-z0-9-]{1,63}$`), check the host label isn't
   already taken in the hosts file, pick `<name>.local` vs `agent.<name>`.
2. Elevate **once**, with a clear explanation (see consent gate below).
3. Ensure the proxy binary is present (bundled or downloaded, pinned + checksummed).
4. Render the loopback-only proxy config for `<name>.local → 127.0.0.1:<app port>`.
5. Add the hosts lines (backup first; restore-on-failure; never leave hosts empty).
6. Start the proxy, install its root CA into the OS trust store, install the
   boot/auto-start unit.
7. Verify end-to-end (resolves to loopback, HTTPS 200 with a trusted cert, WS
   upgrade works) and show the user the green result + the new URL.
8. Persist what was installed to a manifest so uninstall is exact and reversible.

## Consent gate — REQUIRED, never silent

This **must** be opt-in and plainly explained before doing anything, because it:

- needs **admin/root elevation**,
- **installs a local Certificate Authority** into the OS trust store (a real trust
  decision — a CA can mint certs for any name), and
- **edits the system hosts file** and installs a background auto-start service.

Show exactly these three facts, name the CA ("a local certificate authority used
only to trust your own machine's `<name>.local`"), and offer a plain
`localhost:<port>` fallback if the user declines. Default to declined. Log the
consent. Re-prompt (don't assume) on later machines/accounts.

## Cross-platform abstraction seam

One interface, three backends. Define a `HostnameProvisioner` with:
`provision(name, appPort) / verify(name) / teardown(name)` and a capabilities probe
(is elevation available? is the trust store writable?).

| Concern      | Windows (built now)                          | macOS (later)                                        | Linux (later)                                              |
|--------------|----------------------------------------------|------------------------------------------------------|------------------------------------------------------------|
| hosts        | `%SystemRoot%\System32\drivers\etc\hosts`    | `/etc/hosts`                                         | `/etc/hosts`                                               |
| proxy + cert | Caddy `tls internal`, CA via **`X509Store`** into `LocalMachine\Root` | Caddy `tls internal`, `security add-trusted-cert` into the System keychain | Caddy `tls internal`, copy root to `/usr/local/share/ca-certificates` + `update-ca-certificates` |
| auto-start   | Scheduled Task (SYSTEM, AtStartup)           | `launchd` LaunchDaemon (`/Library/LaunchDaemons`)    | `systemd` system service (`WantedBy=multi-user.target`)    |
| elevate      | UAC                                          | `osascript`/`sudo` prompt                            | `pkexec`/`sudo`                                             |
| `.local` note| mDNS/Bonjour resolves `.local`; a hosts line still pins it to loopback deterministically | same — hosts line avoids mDNS ambiguity | same; also mind `nss-myhostname`/`systemd-resolved` for `.local` |

Keep the browser-trust reality in mind: Chrome/Edge/Safari use the OS store (the
above works); **Firefox uses its own NSS store** and needs `certutil -A` per profile
(or `security.enterprise_roots.enabled`) — surface this as a known caveat.

## Reversibility (hard requirement)

Every provision writes an uninstall manifest and ships a one-command teardown that
removes the auto-start unit, stops the proxy, **untrusts and deletes the CA**, and
strips **only** its own hosts lines (guarded so it can never empty the file). Teardown
must be idempotent and safe to run when already partly removed. The Windows reference
(`ops/proxy-down.ps1`) already does exactly this.

## Known risks to carry into the spec

- **Host security software can revert the hosts file** (this dev box runs Spybot,
  which wiped it once) — the auto-start worker must self-heal it, not assume it sticks.
- CA install is the highest-trust action — treat consent copy and the uninstall CA
  removal as first-class, audited, and tested.
- Name collisions and re-provisioning on rename must be handled (teardown old,
  provision new).
