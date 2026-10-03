# S3: OpenShell 0.1.2 on WSL 2 + Docker Desktop, the spike

**Status:** done 2026-09-30. Report only; builds nothing. Feeds the §2.2 gate
and the §4.4 box tiers in `docs/design/active/vibe-coding-salon.md`.

**Question.** Can OpenShell be a box backend (B2-OS) on the owner's machine:
does it run on WSL 2 + Docker Desktop, can its telemetry be switched off and
shown off, is its Windows support stable enough, and can its network policy
be driven live from Friday's grant ledger?

**Ground rules kept.** No Windows feature changed, nothing needing a reboot,
no admin prompt, no Docker sign-in. Docker Desktop and WSL ran only for the
spike and were stopped after it (`wsl --shutdown`). The full-suite lock was
honoured twice (the spike paused 07:50–07:57 and 08:05–08:08).

## What ran

| Piece | Where | How |
|---|---|---|
| Docker Desktop 4.41.2 | Windows, already installed | started from its exe; stopped at the end |
| OpenShell 0.1.2 CLI, gateway, prover | Ubuntu-24.04 (WSL 2), the deb | `dpkg -i`; the distro has no systemd, so the packaged user unit never starts anything |
| OpenShell gateway | Ubuntu, foreground process | `OPENSHELL_TELEMETRY_ENABLED=false`, a TOML config, `--bind-address 0.0.0.0`, mTLS on |
| OpenShell Python SDK 0.1.2 | Ubuntu, a venv | `pip install openshell` |
| Packet capture | Ubuntu `eth0`, tcpdump | 11 MB over 40 min, plus a Windows-side per-process TCP connection log every 2 s |

## Findings

### 1. It runs, after two WSL-specific fixes

Sandbox `Ready` in 3 s on a warm image; the gateway listens 2–13 s after
start. Two fixes were needed, and they are what "experimental" means here:

1. **The supervisor's callback address.** The Docker driver hands the
   sandbox supervisor `https://127.0.0.1:<port>` by default, correct on Linux
   where the supervisor shares the host's network. On Docker Desktop the
   supervisor lives in the `docker-desktop` VM, whose loopback is not the
   distro's, and the start fails after five attempts ("failed to connect to
   OpenShell server"). Fix in the gateway config:
   ```toml
   [openshell]
   version = 2
   [openshell.gateway]
   compute_driver = "docker"
   [openshell.drivers.docker]
   socket_path   = "/var/run/docker.sock"
   grpc_endpoint = "https://192.168.65.254:17670"
   ```
   `192.168.65.254` is Docker Desktop's host-gateway. Its relay arrives at
   the distro's VM address, not its loopback, so the gateway must bind
   `0.0.0.0` (mTLS still gates every connection). A plain connect test lies
   here: Docker Desktop's proxy accepts the TCP connection itself before
   relaying, so `/dev/tcp` "success" proved nothing; the capture showed five
   unanswered SYNs from the Windows side to the distro's VM address.
2. **The certificate.** With that endpoint the supervisor reached the gateway
   and rejected its certificate (`BadCertificate`), because the server SAN
   list has the alias names but not the IP. `openshell-gateway generate-certs
   --server-san host.openshell.internal --server-san 192.168.65.254 …` fixes
   it; the supervisor then connects with `OPENSHELL_ENDPOINT=https://192.168.65.254:17670`
   and `OPENSHELL_TELEMETRY_ENABLED=false` in its environment (read from the
   running container).

### 2. Policy, as the salon shapes it

The test policy was the salon's shape (§4.5): one read-only endpoint
(`api.github.com:443`, `protocol: rest`, `enforcement: enforce`) bound to
`/usr/bin/curl`, everything else denied. Verified:

- The workload container runs with `network=none`, unprivileged, no added
  capabilities, as uid 1000; the supervisor container runs host-networked,
  unprivileged. Memory: supervisor 24 MB, workload 10 MB.
- **Deny by default holds at the socket.** From the workload, a raw TCP
  connect to an unlisted host (`/dev/tcp/registry.npmjs.org/443`) fails with
  `connect: Permission denied` (seccomp staging), not a timeout.
- **A live rule change needs no restart.** `openshell policy update <sandbox>
  --add-endpoint example.com:443:read-only:rest:enforce` returned "Policy
  version 2 submitted" in 98 ms; `--remove-endpoint host:port` exists;
  `policy set --policy file.yaml` replaces; `policy list` gives history.
  This is the shape an approval card needs: allow writes a rule, revoke
  removes it.
- **Not completed:** the HTTP-level check (GET allowed, POST refused,
  revoke refused again). The default image `nvcr.io/nvidia/base/ubuntu:24.04`
  has no curl, and the second sandbox from a curl image was cut short twice
  by a `wsl --shutdown` from outside this session (07:55 and 08:11). It is
  the one remaining item for the gate and takes five minutes on a quiet
  machine.

### 3. The SDK

`openshell` 0.1.2 on PyPI (grpcio, httpx, protobuf, cloudpickle).
`SandboxClient(endpoint, tls=TlsConfig(ca_path, cert_path, key_path))`
offers create, exec, exec_python, exec_stream, list, get, start, stop,
delete, health, wait_ready and templates. No policy method is visible on
`SandboxClient`; driving policy from the ledger through the SDK is
**UNVERIFIED**, through the CLI it is verified. No telemetry code in the SDK
package (grep).

### 4. Telemetry

- **OpenShell** ships telemetry on. The endpoint is a constant in
  `crates/openshell-core/src/telemetry.rs`:
  `https://events.telemetry.data.nvidia.com/v1.1/events/json`, with a fixed
  client id. `OPENSHELL_TELEMETRY_ENABLED=false` on the gateway is honoured
  and propagated to every supervisor (seen in the container's environment).
  It can also be compiled out (`--no-default-features`; the repo ships
  `verify-telemetry-compiled-out.sh`). The CLI has no emitter. The packaged
  user unit reads `~/.config/openshell/gateway.env`, where the spike wrote
  the switch, so a systemd start would also be off.
- **Proof.** The Ubuntu-side capture (11 MB, 40 min, both gateway starts,
  the sandbox lifecycle) has **zero packets** to the telemetry host, and the
  gateway logs have zero telemetry lines. The only outbound connections from
  the distro were pip (Fastly), the gateway's own callback probe, and
  Bugsnag (below).
- **Caveat on the sandbox side.** Docker Desktop routes the engine's and
  containers' egress through its Windows backend, not the distro's
  interface, so that side is covered by the Windows per-process connection
  log, not a packet capture. It shows no NVIDIA telemetry address either. A
  Windows packet capture (`pktmon`) needs admin: the owner's hands.
- **Docker Desktop's crash reporter cannot be switched off below the Business
  tier, and it phones home.** The only switches a personal licence has are
  `AnalyticsEnabled` (usage statistics) and the update checks. Crash and
  session reporting goes to Bugsnag: `sessions.bugsnag.com` was contacted at
  the 07:30 start by the backend, the build helper and the Docker CLI inside
  Ubuntu, with analytics already off; `notify.bugsnag.com` is compiled into
  the backend; the reports file under Docker's roaming folder gained an entry
  the moment Docker was force-quit. The keys that would turn this off live in
  `admin-settings.json`, which Docker honours only under a Business
  subscription. **This counts against Docker Desktop as a required dependency
  for ordinary users under the no-telemetry rule (§12 A3).**
- **Docker Desktop calls home regardless.** With `AnalyticsEnabled=false`
  (the log confirms "system telemetry disabled"), `DisableUpdate=true`,
  `AutoDownloadUpdates=false`, `ShowSurveyNotifications=false`, the backend
  still connected at every start to `api.docker.com`, `hub.docker.com` and
  `desktop.docker.com`, and to **`sessions.bugsnag.com`** (crash-reporter
  sessions) from `com.docker.backend`, `com.docker.build` and the Docker CLI
  inside Ubuntu. None of these has a switch outside Docker Business's
  `admin-settings.json`. This is a finding for **every Docker Desktop-based
  tier**, not only B2-OS: a B2 backend that must not phone home should use
  Podman or plain containerd inside the distro, which OpenShell also drives.
  Image pulls seen, as expected: `ghcr.io` (supervisor, sandbox images),
  `nvcr.io` (the base image), `docker.io` (the curl image).

### 5. Cost on this machine

| Measure | Value |
|---|---|
| Docker Desktop VM at idle | +2.1 GB host RAM at start (`vmmemWSL` 1.15 → 3.29 GB), settling at 2.0–2.4 GB |
| Docker engine reachable | 11 s warm; 116 s after a cold WSL |
| Gateway start | 2–13 s |
| Sandbox create | 3 s warm; the first attempt with a pull ~21 s |
| Supervisor + workload | 24 MB + 10 MB |
| Free host RAM during the spike | fell from 7.3 GB to 1.9 GB at the low point, with other sessions building |

A Docker Desktop 4.41.2 quirk: after an unclean stop it crashes at start on
its own stale `dockerInference` socket ("The file cannot be accessed by the
system") and shows an error dialog. Deleting the file fixes it;
`EnableInference=false` does not prevent it.

## Verdict against the §2.2 gate

| Gate item | Result |
|---|---|
| runs on the owner's WSL 2 + Docker Desktop | **yes**, with the two config fixes above |
| telemetry off and proven off by capture | **gateway side yes**; sandbox side by connection log only; a full capture needs admin |
| Windows support stable, or shown stable here | **not out of the box**: the two fixes are exactly the experimental part; with them the run was stable until WSL was shut down from outside |
| network policy driven live from the ledger | **CLI yes** (98 ms, no restart); **SDK unverified** |

B2-OS stays behind the gate; the recipe to pass it is now written down.

## What this means for the box tier

Docker Desktop is the piece an ordinary Windows user would be asked to
install, and it is the piece that phones home with no switch. The
alternatives, weighed:

| Backend for B2 on Windows | Phones home? | Needs admin / reboot? | Runs OpenShell? | Verdict |
|---|---|---|---|---|
| Docker Desktop | yes: Docker hosts and Bugsnag at every start, no switch below Business | install needs admin once; WSL 2 feature | yes (this spike) | **not a required dependency**; supported only when the user already has it and accepts it |
| Podman inside the WSL distro (`apt install podman`, no Podman Desktop) | no known telemetry in the engine; verify by capture | no admin beyond WSL itself | yes: OpenShell has a Podman driver | **first candidate** for Friday's own WSL 2 backend and for B2-OS |
| containerd + nerdctl inside the distro | none | none beyond WSL | no (OpenShell drives Docker, Podman, Kubernetes, VM) | fine for Friday's own backend, not for B2-OS |
| WHP microVM (microsandbox) | none | WHP feature, admin to check | no | Phase 5 territory; unmeasured |
| No container at all: B1 host processes | none | none | no | the floor every user gets |

So: Friday's own B2 backend targets Podman inside the distro, proven by the
same capture method, with Docker Desktop as an accepted-if-present option;
B2-OS follows Podman too, which removes the callback and certificate fixes
above, because the supervisor then shares the distro's network. The WSL
memory cap stays the user's setting, with a documented recommendation; the
spike's 26 GB / 16 GB swap on a 32 GB machine is what let the disk fill. The
one open check is the HTTP-level allow/refuse/revoke with a curl-bearing
image, plus an admin packet capture on the Windows side if the owner wants
the sandbox path proven the same way.

## Left on the machine

In Ubuntu-24.04: the `openshell` deb (CLI, gateway, prover), a source clone,
a venv with the SDK, the spike's logs, captures and certificates under one
folder, and `~/.config/openshell/gateway.env` with telemetry off. Nothing
runs; the packaged unit is never enabled. In Docker Desktop's
`settings-store.json`: analytics, updates, surveys, CLI hints, the model
runner and the dashboard-on-start switched off; Ubuntu-24.04 added to the
WSL integration list (this is what puts the Docker CLI, and its Bugsnag
call, inside the distro). The pre-spike file is kept for the owner to
restore.

## Sources

- OpenShell release v0.1.2 assets and `install.sh`; the source tree at
  `main` (telemetry constants, `openshell-driver-docker/src/lib.rs`,
  `docs/how-it-works/gateways/configuration.mdx`, `docs/about/support-matrix.mdx`).
- Docker Desktop backend log and `settings-store.json` key names confirmed
  against the backend binary; Docker's Settings Management documentation for
  the Business-only keys.
