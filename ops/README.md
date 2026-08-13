# Agent Friday — local presentation proxy (`agent.friday`)

Presentation infrastructure for the **Thursday July 9 demo**. It makes the browser
address bar read **`https://agent.friday`** with a clean padlock and no port —
instead of `localhost:3000`.

This lives **beside** the app. It does **not** touch Friday's source, voice code,
knowledge/galaxy code, or its `:3000` port. It is a hosts entry + a Caddy reverse
proxy, nothing more.

```
browser ──HTTPS──▶  agent.friday:443  (Caddy, loopback only, internal CA)
                          │
                          └──HTTP──▶  127.0.0.1:3000  (Friday, unchanged)
```

## What's here

| File                       | Purpose                                              |
|----------------------------|------------------------------------------------------|
| `Caddyfile`                | Proxy config: `agent.friday` → `127.0.0.1:3000`, loopback-only, `tls internal`, machine-wide storage |
| `proxy-up.ps1`             | Manual bring-up: hosts entries + start Caddy + trust local CA |
| `proxy-service-install.ps1`| **Boot auto-start**: registers the SYSTEM scheduled task so agent.friday survives reboot |
| `proxy-boot.ps1`           | Worker the boot task runs as SYSTEM (hosts self-heal + Caddy supervise) |
| `proxy-down.ps1`           | **Rollback**: remove service + stop Caddy + untrust CA + remove hosts entries |
| `proxy-verify.ps1`         | End-to-end verification (DNS, padlock, galaxy, voice WS) |

The Caddy binary and its runtime data live **outside the repo** at
`%USERPROFILE%\.friday\proxy\` (binary, certs, `logs\`), so the repo working tree
stays clean.

## Bring it up

```powershell
.\ops\proxy-up.ps1
```

Self-elevates via UAC (needs admin — see below). Idempotent; safe to re-run.
Then open **https://agent.friday**.

## Make it survive reboot (boot auto-start)

```powershell
.\ops\proxy-service-install.ps1
```

Self-elevates, then registers a boot-triggered Scheduled Task **`AgentFridayProxy`**
that runs as **SYSTEM** (highest privileges, restart-on-failure). On every boot —
**before any user logs in** — it verifies/restores the hosts entry and brings Caddy
up on loopback `:443` with the trusted CA. Starts immediately too (no reboot needed).

**Why a scheduled task, not a `sc.exe`/NSSM service:** Caddy has no native Windows
service interface (needs a wrapper), and `caddy start`/`caddy stop` hang on this box.
A boot task running hidden `caddy run` under a supervising PowerShell loop is
dependency-free, runs as SYSTEM before login, and lets us fold in the **hosts
self-heal** (re-check every 30 s; restore from backup if Spybot wipes it) that a bare
service wrapper couldn't do.

Because the SYSTEM account has its own Caddy storage, the `Caddyfile` pins **machine-wide
storage** at `C:\ProgramData\AgentFriday\caddy` so the elevated-user and SYSTEM
contexts share the *one* trusted CA (otherwise SYSTEM would mint its own → padlock
warning). Runtime logs: `C:\ProgramData\AgentFriday\logs\` (`boot.log`, `caddy.err.log`).

Once installed, the service owns Caddy; `proxy-up.ps1` detects it and won't start a
second instance.

## Verify it

```powershell
.\ops\proxy-verify.ps1
```

No elevation needed. Checks: `agent.friday` resolves to loopback; `https://agent.friday`
serves the real Friday UI with a **trusted** cert (clean padlock); the proxy mirrors
`:3000` exactly (transparency); and both voice WebSockets (`/ws/voice-local`,
`/ws/live`) complete their upgrade through the proxy. Prints `ALL CHECKS PASSED`
when green.

## Roll it back — one command

```powershell
.\ops\proxy-down.ps1
```

Self-elevates via UAC, then: **unregisters the `AgentFridayProxy` boot task**,
stops Caddy, removes the local root CA from `LocalMachine\Root`, and strips **only**
the `agent.friday` block from the hosts file. After it runs, the service is gone,
`agent.friday` no longer resolves or serves, and the padlock CA is gone. Friday on
`:3000` is untouched throughout.

To remove **just the boot service** (leave the manual proxy usable):

```powershell
Unregister-ScheduledTask -TaskName AgentFridayProxy -Confirm:$false
```

To also delete the downloaded binary, certs, and machine-wide runtime:
```powershell
Remove-Item -Recurse -Force $env:USERPROFILE\.friday\proxy, C:\ProgramData\AgentFriday
```

## What needed admin elevation (what Stephen approved)

All three bring-up actions are per-machine and require an elevated (admin) token;
UAC prompts once per script run:

1. **Hosts file edit** — `C:\Windows\System32\drivers\etc\hosts` is
   admin-write-only. Adds `127.0.0.1 agent.friday` and `::1 agent.friday`.
   (On this box the file is also flagged **ReadOnly** by Spybot immunization —
   the script clears that attribute for the write and restores it after.)
2. **Trusting the local CA** — the script installs Caddy's local root CA into
   the Windows `LocalMachine\Root` store. This is what makes the padlock clean
   with no self-signed warning. (We install it directly via the .NET `X509Store`
   API rather than `caddy trust`, because `caddy trust` silently no-ops on this
   Windows build.)
3. Starting Caddy on port **443** (privileged port).
4. **Registering the boot service** — creating the `AgentFridayProxy` Scheduled
   Task that runs as SYSTEM requires admin (`proxy-service-install.ps1` only).

The hosts file is backed up before any edit: a **write-once** `hosts.friday.orig`
(pristine) and a rolling `hosts.friday.bak` (pre-edit), both next to `hosts`.

## Sovereignty / safety notes

- **Loopback only.** The Caddyfile uses `bind 127.0.0.1 ::1`, so Caddy listens
  only on loopback. `agent.friday` is **not** reachable from the LAN — the whole
  point. (Both hosts lines also point only at loopback.)
- **Local CA, no internet.** `tls internal` uses Caddy's own CA; no public
  ACME/Let's Encrypt request is made. The cert never leaves the machine.
- **WebSockets + SSE.** Caddy proxies the `Upgrade` handshake automatically, so
  the Gemini Live `/ws/live` voice socket works through the proxy.
  `flush_interval -1` disables buffering so SSE / streaming voice aren't stalled.
- **App untouched.** Friday still runs on `:3000` exactly as before; the proxy
  just fronts it. Stop the proxy and the app is unaffected.
- **Survives a `:3000` restart.** `reverse_proxy` has `lb_try_duration 15s` +
  `lb_try_interval 250ms`, so when Flask is briefly down during a restart Caddy
  **holds the request and retries** until `:3000` is back — instead of returning
  a 502 on the first hit and forcing you to prime `localhost:3000` by hand.
  agent.friday recovers on its own within seconds. (Verified against a controlled
  upstream restart; stale-keepalive reuse was also tested and auto-recovers, so no
  keepalive change was needed.)

## ⚠️ Spybot & the hosts file (important for the demo)

This machine runs **Spybot – Search & Destroy**, whose resident service
(`SDFSSvc`) keeps the hosts file **ReadOnly** ("immunized") and periodically
re-asserts it. During setup this was observed to occasionally **reset the hosts
file** when it was modified rapidly. The scripts defend against this:

- they keep a **write-once pristine backup** `hosts.friday.orig` and a rolling
  `hosts.friday.bak`;
- every write is verified — if it would shrink or empty the file, the script
  **restores from backup and aborts** rather than leave a broken hosts file;
- they restore the ReadOnly attribute after editing so Spybot stays satisfied.

**If `agent.friday` ever stops resolving during the demo** (Spybot reset the
file), restore it in one line from an elevated PowerShell:

```powershell
$h="$env:SystemRoot\System32\drivers\etc\hosts"; (Get-Item $h -Force).IsReadOnly=$false; Copy-Item "$h.friday.bak" $h -Force; ipconfig /flushdns
```

`hosts.friday.bak` holds the full original list **plus** the `agent.friday`
block. To restore the pristine list **without** the block, copy `hosts.friday.orig`
instead. For a rock-solid demo, consider temporarily pausing Spybot's hosts
immunization beforehand.
