# First run to first month: the installer, the birth, the first major task, and the apprenticeship

> **Status:** active (spec plus clickable prototypes; nothing in this document is built)
> **Last verified:** 2026-09-30, against main at `02035ba6`
> **Implementation:** none of the new surfaces. The grounding sections cite what exists today.
> **Prototypes:** [`docs/design/prototypes/onboarding/`](../prototypes/onboarding/index.html), seven screens in Friday's brand, keyboard-complete, no server.
> **Binding owner rule:** Bonsai2 is the only local model suggested at first setup (§3.6); its lowest viable tier is the published minimum. Tier numbers come from `bonsai2-floor.md` and `bonsai2-tiers.json` (session local_6f0b72ec, branch `docs/model-soup`), not from this document.
> **Supersedes / superseded by:** extends [`vault-first-onboarding.md`](../implemented/vault-first-onboarding.md) (implemented) and [`hostname-onboarding.md`](hostname-onboarding.md); consumes Phase 0 of [`self-patching-installer.md`](self-patching-installer.md); replaces the installer described in [`docs/getting-started/installation.md`](../../getting-started/installation.md); depends on [`avatar-visual-genome.md`](avatar-visual-genome.md) §3 (seed, sigil) and §13 (processing states); implements north-star §8, §9 and the onboarding rows of §13, §15, §23, §25, §26, §27, §28, §33 and §34
> **Written:** 2026-09-30
> **Method:** STORM with a simulated expert panel (appendix A); every claim about today's code carries a `file:line` citation, every design claim maps to a north-star requirement ID from [`GAP-MATRIX.md`](../north-star/GAP-MATRIX.md)

## 0. The owner's brief, and the answer in one paragraph

The owner asked for one installer file, a setup that creates `agent.<name>` as part of the user's own process, the simplest possible way to connect every related service, and, as the first major task once set up, the biggest and most accurate knowledge graph and wiki of the user that can honestly be built. He asked what else should happen, how, and how settings and the other surfaces should carry a new person into the full architecture. He asked not to be limited by what exists.

The answer: a signed, per-user, single-file installer that finishes in under two minutes with no model; a model-free preflight that says what this machine can do in four honest buckets; a birth that is a voice-first conversation with Friday herself (name, mark, address, vault and recovery drill, routing, persona, quiet hours), each step flat and keyboard-complete; a "connect your life" surface built from a contract every card obeys (what she sees, where it runs, read and write apart, how to revoke), which tells the Gmail truth instead of hiding it and leads with imports that need no account; a first major task that stages everything, extracts locally with provenance on every fact, resolves entities, writes cited wiki pages, finds commitments, zones sensitive material, and asks the user small questions by voice a few minutes a day, with first value in about fifteen minutes; a thirty-day apprenticeship in which the first governed action, the first goal, routines, grants, backups and household members arrive one at a time and only when earned; and a control room in which every setting has four lines (meaning, consequence, who changed it, undo), can be edited by voice through a shown diff, and sits beside a live privacy map and the ledger of what left this machine today. Nothing is sent to FutureSpeak, ever.

Five decisions are the owner's alone. They are in §12 and nowhere else.

## 1. Grounding: what exists on 2026-09-30

The design below dreams. This section does not. Everything here was read from the tree at `02035ba6`.

### 1.1 The installer today

- **What a person downloads:** a zip, `AgentFriday-Setup-<version>.zip`, about 21 MB, from a GitHub release. They unzip it and double-click `Install Agent Friday.cmd`, which exists to run `install.ps1` under `-ExecutionPolicy Bypass` (`packaging/windows/Install Agent Friday.cmd:25-47`). Nothing is signed; `packaging/windows/README.md:342-343` says SmartScreen may warn.
- **What runs:** a 16-step PowerShell installer with `Read-Host` prompts, and then the terminal setup wizard `python -m agent_friday.cli setup` in the foreground on every run, including upgrades (`install.ps1:1039-1082`). The web setup chat that exists in the app is never invoked by the installer.
- **Per-user already:** the install root is `%LOCALAPPDATA%\AgentFriday`, registry writes are HKCU only (`install.ps1:24`, `lib/Shortcuts.ps1:7-11`). The only UAC prompt is the hosts-file block for `agent.<name>`, and it is started from Settings, not setup.
- **The runtime:** embeddable CPython 3.12.10, 11 MB, SHA-256 pinned, no venv (`lib/Python.ps1:44-48, 167-206`). Everything else is fetched from PyPI at install time: core about 500 MB of site-packages, recommended about 800 MB, memory about 2.3 GB (`README.md:199-203`). `get-pip.py` is not hash-pinned (`sources.json:36-54`); the Ollama installer is unversioned and unpinned (`lib/Ollama.ps1:167`).
- **Time:** the installer's own text says ten to fifteen minutes on a fast connection for the key-only path (`install.ps1:443`); rehearsal runs take fifteen to twenty-five minutes. North-star §8.1 asks for under five (NS-8.1-12, missing).
- **Models block the install:** when a local model is chosen, `models.install` runs inside the installer with a 7,200 s timeout (`install.ps1:896-945`). The gap matrix records this as conflicting with NS-8.1-13 and picks the spec: the installer finishes first (GAP-MATRIX:50).
- **Upgrades:** a full re-run. `app\` is deleted and recopied, `python\` re-extracted, every pip tier re-run. No delta, no rollback, no repair entry (ARP has `NoModify=1`, `NoRepair=1`). `services/update_check.py` only announces a newer release and applies nothing.
- **The two known regressions and their fixes.** From 5.6.0 to 5.6.4 upgrades copied nothing, because `Invoke-Step` runs verify first and `app.copy`'s verify only checked that four files existed; measured 0 of 489 files updated (fixed `70271d3b`, the verify now requires the measured version to equal the target, `install.ps1:452-468, 583-613`). In 5.6.5 the first upgrade that actually copied deleted `app\start.bat`, the only home of the vault passphrase, and the vault became undecryptable with no recovery (fixed `a6229b57`: secret-bearing files are carried across the copy in a `finally`, `install.ps1:494-581`; then `d7a0e9c6` moved the passphrase out of the app tree entirely). The invariants now: the installer never touches `~/.friday`; the passphrase's durable homes are the OS keychain and a DPAPI file under `~/.friday/security` (`services/vault_passphrase.py:30-42`); the uninstaller keeps credentials if and only if it keeps the vault; a fifteen-to-twenty-five-minute upgrade rehearsal must pass before a draft release.
- **Uninstall:** removes the app, shortcuts, its own Ollama tags, caches and the hosts block; keeps `~/.friday` unless the person types `DELETE`; exports nothing; leaves `%TEMP%` copies and `hosts.agent-friday.bak` (`uninstall.ps1:98-145, 214-222, 480-533`).
- **Doctor:** `Heal.ps1` is a closed menu of thirteen remediations, model-proposed but never model-executed (`lib/Heal.ps1:259-450`). The Phase 0 Doctor of `self-patching-installer.md` (a hashed release manifest, an install ledger, a read-only diff report) has no implementation (`self-patching-installer.md:3-7, 1056-1081`).

### 1.2 Setup today

- Six consent screens (`collects, vault, routing, cloud_ack, third_party, updates`; `services/onboarding_copy.py:28`), then a text setup chat with thirteen stages (`services/setup_chat_copy.py:21-23`): welcome, agent name, basics, connect, reader, research, ten personality questions, six style sliders, scheduled cloud, finish. No stage takes voice (`services/setup_chat.py:196-234`). The older voice-first state machine in `services/onboarding.py` is dead (`vault-first-onboarding.md:57, 119-126`).
- It works with no model and no key, by design (`setup_chat_copy.py:3-8`), which the new design keeps.
- It can be re-run and revisited (`setup_chat.py:722-755`), and never writes a secret to its state file (`:11-14`).
- The vault passphrase goes to keychain plus DPAPI (`vault_passphrase.py:333-404`). There is no Windows Hello, no recovery kit, no restore drill anywhere in code or docs; the passphrase-location note lists the recovery code as the missing Q2 (`vault-passphrase-location.md:12`).
- Naming produces `agent.<slug>`; the hosts step and the certificate trust are two Settings buttons, each confirmed by Windows; the CA is name-constrained to the one host (`hostname-onboarding.md:13-21`; `services/local_ca.py:98-215`). A prompt at the naming step of setup is listed as not built.
- Google sign-in is bundled-or-BYO, resolved in `services/google_oauth_client.py`; the bundled credential is not minted and nothing has been submitted to Google (`google-oauth-verification-checklist.md:11-12`). Testing mode expires refresh tokens after seven days and needs every tester listed by hand (`:20-46`). `gmail.readonly` is a restricted scope; calendar is sensitive (`google-oauth-onboarding.md:87-104`). Neither doc mentions IMAP app passwords or Takeout.

### 1.3 Knowledge today

- The wiki is Markdown under `~/.friday/wiki/`, opt-in encrypted per section, optionally mirrored (`services/wiki_engine.py:73-101, 46-70`). `_staging` is only a skipped directory name; no staging workflow exists (`services/knowledge_graph/wiki_graph.py:45-46`).
- Tier A of the graph is structural (pages, wikilinks, title mentions); Tier B is GraphRAG extraction through the model router with the egress gate on any cloud call (`knowledge_graph/indexer.py:426-470`). Entity resolution is a sha1 of the upper-cased title; no aliases, no coreference (`:418-419`). There is no timeline model, only an `updated` mtime. Provenance exists on every record (`knowledge-system-spec.md:159-187`); research facts without a URL are refused (`knowledge_graph/integration.py:82-146`).
- A pending queue exists for wiki proposals (`wiki_engine.py:242-303`, `routes/wiki.py:167-205`) and a general approval queue (`services/approvals.py`). Nightly dreaming auto-promotes facts at confidence 0.6 or higher (NS-8.10-3, partial), which this spec ends.
- Laya is a 421M typed-decision encoder on the CPU, about 0.3 s per question, running in shadow behind `services/decisions.py`; "Teach the scanner" exists as one yes/no question per item (`services/laya_labels.py:1-20`; `index.html:46140-46185`).
- The Knowledge workspace is a pages-and-galaxy split with nine arrangements and a live SSE feed that already carries `node_ignited` (`routes/knowledge_graph.py:337-362`).

### 1.4 The north star's onboarding rows, in one line each

Sections §8 and §9 have 10 shipped, 46 partial, 2 proposed, 14 missing and 3 conflicting requirements (GAP-MATRIX:24-35). The rows this spec closes are listed in appendix B. The ones that shape it most: NS-8.1-1 signing (missing), NS-8.1-12 under five minutes (missing), NS-8.2-2 the four buckets (partial), NS-8.5-2 a generated recovery key (partial), NS-8.6-1 five routing profiles (partial), NS-8.9-2 the connector card contract (partial), NS-8.10-2 import staging (partial), NS-9.1-1 first value (missing), NS-9.6-1 completion defined by experience rather than a marker (missing), NS-26.22-1 the privacy dashboard (partial), NS-27.4-3 restore rehearsals (missing), NS-27.10-1 export-first uninstall (partial), NS-34.4-1..10 first-run acceptance (mostly missing).

## 2. Standing rules

These outrank every choice below. They are the owner's rules from `docs/decisions/2026-09-29-program-delegated-decisions.md` and the north-star amendments, restated for this surface.

1. **No telemetry, ever.** Onboarding metrics stay on the machine (A3; `docs/user-guide/background-network.md:12`). The installer contacts no FutureSpeak endpoint. The update check is opt-in and fetches a manifest only. The one refinement, the weekly ask to email the Doctor report from the user's own account, is answered by the user every time (AMENDMENTS:49-95).
2. **Both paths.** Local and cloud both exist wherever local is feasible; the user picks; nothing is substituted silently in either direction (A6). The first major task runs on the local model by default; the cloud path is offered with its time and cost and taken only on a yes.
3. **Voice-first parity.** Every step of setup, review and settings is voice-callable per [`docs/reference/voice-tool-contract.md`](../../reference/voice-tool-contract.md), and every step has a flat, keyboard and screen-reader path (A1, A4, NS-33.1). Nothing is voice-only-limited.
4. **Brand integrity.** The hologram is her identity; the installer and onboarding are surfaces of the one brand system (A5). Her mark, the Genesis Lattice with a seeded sigil, appears from the first pane of the installer to the last page of the control room. Motion only on a real event (`feedback: holo motion needs a real event`).
5. **No pestering.** Every offer is made once, in Home's "what's waiting", and remembered. Fewer notifications and a daily digest by default (NS-8.14-2). There is no drip campaign.
6. **Accessibility.** WCAG 2.2 AA on the flat interface; reduced motion honoured; the 3D layer can be switched off entirely; captions and transcripts for everything Friday says; speech rate, repetition and confirmation are settings (NS-33.1, NS-33.2, NS-33.3).
7. **Fail visibly, never substitute quietly.** A capability whose dependency is missing is shown as unavailable with the reason (NS-8.2-3, NS-23.5-1). A held cloud call is shown, not dropped. An estimate says whether it is measured or guessed (NS-8.7-1).
8. **Approval gates are never weakened.** Setup creates no grant. The first grant arrives in week two, per workflow, narrow and expiring, and "full access" is not a phrase the product uses (NS-8.13-3).
9. **The user is the North Star. The user decides.** The owner's words: "I want the user to be our North Star, always. All of this should be presented to them upon setup. The user decides." Every product-intent choice that can be the user's is presented at setup as a choice: plain words, what it means, what it costs, what changes later. The option Friday would pick is labelled "recommended" and is never pre-selected; the person actively picks. Each choice can be changed later in the control room, with a receipt, and by voice. Choices are grouped so the number of screens stays humane, and "use recommended for the rest" is an explicit, visible option the person can select, never a silent default. Only what happens before any user exists stays with the owner (§12).

## 3. One signed installer file

### 3.1 The shape

| | Today | This spec |
|---|---|---|
| Artifact | zip + `.cmd` + unsigned `.ps1` | one signed `AgentFriday-Setup-<ver>.exe` per platform |
| Rights | per-user, HKCU | per-user, HKCU; one explained UAC only if the person wants `agent.<name>` via the hosts file |
| Runtime | embeddable CPython, wheels from PyPI at install time | embeddable CPython and the core wheel set inside the signed file, content-addressed; nothing resolved live |
| Size | 21 MB download, then 0.5 to 2.3 GB of downloads inside the installer | under 250 MB signed, of which about 150 MB is the app shell (§3.7); all model and tier downloads happen in the app, after first run, with pause, resume and checksums (NS-8.1-13, NS-8.1-14) |
| Time to a usable Friday | 10 to 25 minutes | under 2 minutes measured on the clean-machine matrix; under 5 is the acceptance floor (NS-8.1-12) |
| Updates | full re-run; notify-only check | signed delta packages; the previous version kept; automatic rollback on a failed health check; updates never install without the summary (NS-28.6-1, NS-26.15-1) |
| Repair | re-run the zip | "Repair" re-verifies every file against the signed manifest and replaces only what differs; never touches `~/.friday` |
| Uninstall | remove app; keep or `DELETE` data; no export; no revocation | export first, revoke connections and devices, remove, verify, receipt (NS-27.10-1) |
| Receipt | `install-manifest.json` without hashes | `install-receipt.json` with the hashed file list, the preflight report and every choice made |

**Recommended mechanism (a build decision the program lead can take):** a Velopack-style bootstrapper. It produces one `Setup.exe`, installs per-user into `%LOCALAPPDATA%\Programs\Agent Friday\app-<version>\`, keeps the previous version directory for rollback, ships binary delta packages between releases, and is signable with any Authenticode identity. The launcher reads a pending marker and only switches the current pointer after the new version's health check passes, which is rule SP8 of the self-patching spec (`self-patching-installer.md:1033-1052`) already written down. MSIX was considered and set aside for v6: its container restricts loopback listeners and `certutil -user` in ways the local address depends on, and Store review would put a third party between the owner and every release, against A7. Inno Setup with `PrivilegesRequired=lowest` is the fallback if the bootstrapper proves troublesome; it has no delta or rollback of its own, so those would be built on the version-directory layout regardless.

**What goes inside the signed file:** the Electron shell (about 150 MB compressed, §3.7), the embeddable CPython (11 MB), the app payload (about 90 MB uncompressed today), the core wheel set pinned by hash, the signed `release-manifest.json` (Phase 0 of the self-patching spec, `self-patching-installer.md:100-107`), the signed SBOM (NS-8.1-3), and the brand assets. Nothing else. `get-pip.py` is not needed when wheels are unpacked directly. Ollama is not bundled: the app offers it, or the in-process llama.cpp runtime the seat scheduler already understands, after first run.

**What is downloaded after first run, inside the app, only on a yes:** Bonsai2 at this machine's tier (§3.6; the Gemma ladder in `install.ps1:360-365` is retired), the memory tier (about 2.3 GB, torch included), the judgment tier (Laya, about 0.8 GB), local voice, OfficeCLI. Each has a checksum, a size, a pause, a resume, and a "not now". The interface is usable throughout (NS-8.7-2).

### 3.2 Preflight: the model-free Doctor

Runs before any file is written, in under five seconds, with no network. It is the first consumer of the Phase 0 Doctor. It checks, and reports without guessing: OS build and per-user install rights; free disk against three numbers (now, with recommended tiers, with the largest model); RAM; GPU vendor and VRAM through the vendor's own tool and, failing that, DXGI, so AMD and Intel stop reading as null (`install.ps1:333-446` only reads `nvidia-smi`); CPU features; microphone, speaker and camera; Windows Hello enrolment; Credential Manager reachability; the loopback ports 3000 and 443; an existing install and its version; and a stale vault passphrase location (the `start.bat` case) so the migration runs before anything is replaced.

It presents the north star's four buckets, literally (NS-8.2-2): **Ready now / Available after download / Cloud available / Unavailable on this device**. The report is saved into the install receipt and shown again in the control room's Health section. Every figure carries "measured" or "estimated" (NS-8.7-1). Nothing in the report can advertise a capability whose dependency is missing (NS-8.2-3).

### 3.3 Signing, SmartScreen, and what it costs

Numbers below are as of writing and must be re-checked at purchase.

- **Why sign at all.** Unsigned executables get the blue "Windows protected your PC" box with no publisher line; most non-technical people stop there. A signed file shows the publisher, which is the one thing the installer's first pane tells the person to read.
- **Certificate types and prices.** Since June 2023 every code-signing private key must live in a hardware token or HSM. An OV (organisation-validated) certificate costs roughly $200 to $500 a year plus a token; an EV certificate roughly $300 to $700 a year. **Azure Trusted Signing** is the cheap path: about $10 a month for the basic tier, Microsoft-issued short-lived certificates, no token to lose, identity validation for an organisation (an individual-validation path exists in some regions). SignPath Foundation signs open-source projects free of charge, subject to acceptance.
- **The reputation problem.** SmartScreen reputation accrues to the publisher identity and to each file's hash as people download and run it. A brand-new OV certificate starts with none, so early downloads still see a warning, with the publisher name filled in. EV certificates historically received reputation at once; Microsoft no longer guarantees that. Trusted Signing attaches reputation to the validated identity rather than to a certificate that rotates, which is the reason to prefer it. Reputation transfers between versions when the identity is stable; every rebuild of an unsigned file starts from zero.
- **What we do about it.** Sign every build with one stable identity from the first public 6.0 build; never ship an unsigned file again; publish the SHA-256 beside the download; list the package in `winget` so the "Windows says it doesn't recognise this" conversation has a second answer; and put the honest sentence on the first pane: "Windows may show a blue box the first time a new version is downloaded. The publisher line is the thing to check."
- **Mac (planned):** Apple Developer Program, $99 a year, notarised DMG with hardened runtime, per-user install into `~/Applications`, secrets in the login keychain, Touch ID through LocalAuthentication for the vault, `hosts` needs `sudo` once, exactly like Windows. **Friday Linux (planned):** the installer is the image; this whole document becomes the first-boot experience, and the elevation question disappears because the OS is hers.

### 3.4 Updates, rollback, repair

- **Channel and consent.** Stable by default; preview and pinned exist (NS-28.6). The app checks a signed manifest only when the person said "check" (today's consent screen, `onboarding_copy.py:31-38`). An update arrives as a card with the north star's preview fields (NS-28.7): version, size, what changes, any permission or governance diff, migration preview, rollback availability, known issues. Permission and governance changes are never applied without that summary (NS-26.15-1).
- **Apply.** Download the delta, verify signature and hashes, unpack to `app-<new>\`, run migrations against a backed-up copy of the small state files (`boot_guard.py` already snapshots settings and workspace JSON; this extends the snapshot to the wiki index and the knowledge manifest), flip the launcher pointer, start, and require the health check within a bounded time. On failure, flip back, keep the failed directory for the Doctor, and say so.
- **The vault passphrase invariant, tested every release.** Before the flip, resolve the passphrase through `vault_passphrase.resolve()` and verify it against real vault ciphertext; after the flip, do it again; refuse to declare the update complete otherwise. This turns the 5.6.5 incident into a permanent gate, alongside the existing rehearsal (`packaging/windows/tests/rehearsal/upgrade-vault-test.ps1`).
- **Repair.** A button in Health and a command-line flag on the installer. Reads the signed manifest, hashes the install tree, replaces only the differing files from the local package cache or a fresh signed download, and never reads or writes `~/.friday`. Prints the diff first.
- **Uninstall, four steps, each shown.** (1) Offer a Friday Bundle export to a folder of the person's choice, and wait for it to verify. (2) Revoke: every OAuth token through the provider's revocation endpoint where one exists, every app password held locally, every paired device, every scheduled task, the local-address certificate trust and the hosts block (one UAC, explained). (3) Remove the app; remove `~/.friday` only on the typed `DELETE`, and then remove the Credential Manager entries in the same breath, keeping today's live-or-die-together rule (`uninstall.ps1:18-28`). (4) Verify: list every path and registry key it intended to remove and whether it is gone, including `%TEMP%` copies and `hosts.agent-friday.bak`, which today survive. Write an uninstall receipt to the folder the bundle went to. Data is never held hostage (NS-27.10-2).

### 3.5 Testing on clean machines

- **Windows Sandbox** (Pro and Enterprise) for the fast loop: a `.wsb` file maps the built installer read-only, the sandbox is fresh on every start, and a script runs install, preflight assertions, first-run smoke, update from the previous release, rollback injection, repair, and uninstall-with-export.
- **Hyper-V evaluation VMs** for the matrix the north star requires (NS-34.3): supported Windows versions, minimum RAM, no GPU / low / mid / high, a non-English locale, a restricted (non-admin) account, upgrade from the oldest supported version and from the previous one, restore from a bundle.
- **GitHub-hosted Windows runners** keep the existing `fresh-install` and `upgrade` jobs (`.github/workflows/installer.yml`) and add signature verification (NS-34.4-1) and a SmartScreen-free assertion that the publisher field is populated.
- A release is not cut until the whole matrix is green (A7 keeps the release itself an owner conversation).

### 3.6 The local model: Bonsai2 only

A binding rule from the owner: **Bonsai2 is the standard. Not Gemma, not Qwen. Only Bonsai2.** The installer and the birth conversation suggest no other local model. Any model stays supported afterwards by the person's own choice in the control room; first setup does not mention one.

- **Detection and tier.** The preflight reads the machine (VRAM, RAM, disk, CPU features) and looks up the best Bonsai2 size and quantization it supports in the tier table, `docs/design/active/bonsai2-tiers.json`, which the "Bonsai2 floor and Model Soup" session (`docs/design/active/bonsai2-floor.md`) is measuring. Two members of the Ternary Bonsai family are on the reference machine today, 27B PTQ1_0 and 4B PQ2_0; the tier table, not this document, states each tier's VRAM, RAM, disk, context and measured tokens per second. Nothing here invents a number; the preflight prints the table's row for this machine and marks speed "measured" only after the first load.
- **The floor is the published minimum.** The lowest viable Bonsai2 configuration in that table is Friday's **application floor**, and it becomes the published minimum system requirements on the download page and in the preflight's "Unavailable on this device" bucket. A machine below the floor is told so before installing, with the cloud-only path offered plainly (rule 2, both paths), never with a smaller non-Bonsai model as a substitute.
- **Download after first run, never inside the installer.** The tier's weights download in the app with size, checksum, pause and resume (§3.1). The four-bucket report says "Available after download: Bonsai2 <tier>" and nothing else under local reasoning.
- **Routing copy uses the tier.** §4.4's profiles say "Bonsai2 27B on this PC" rather than a generic size, with the table's figures.
- **The Model Soup, at the end of onboarding and any time after.** Once birth is done, Friday offers the **Model Soup** advisor from `docs/design/active/model-soup-advisor.md` (the settings-surface authority, `model-soup.md`, stands beside it): a full hardware-tailored stack, a system-one responder, Laya, Needle, a deep reasoner, and image, video and music models, solved locally from a public catalog. The hardware profile never leaves the machine; the catalog is public data fetched like an update manifest. The offer is one card in Home's "what's waiting", re-runnable from Models and seats in the control room, and voice-callable ("make me a model soup", appendix C). Accepting it installs only what the person ticks, each with size, checksum and pause.

### 3.7 The app shell: Friday as her own application

The owner asked whether bundling Brave (brave-core is MPL-2.0 on Chromium) could make Friday a standalone application. The goal behind the question is the requirement: Friday runs as her own app, with her own window, taskbar icon, notifications, hotkey and deep links, not as a tab in whatever browser the person has. Today the tray opens the served UI in the default browser (`services/friday_tray.py:410`, `webbrowser.open`), notifications go through the tray icon (`:583-586`), and the hotkey is a pynput hook. The historical review recorded the same fact: "Not Electron. Flask server + `webbrowser.open` into the user's default browser" (`docs/design/historical/open-source-adoption-review.md:54`).

**Measured on the reference machine, 2026-09-30** (RTX 4070, driver 616.92, Windows 11 Pro, WebView2 runtime 154.0.4258.37 installed, 1,175 MB on disk for its two resident versions):

| Measurement | Result |
|---|---|
| Electron 44.5.0 download, `win32-x64.zip` | 150.7 MB for 44.5.1 (GitHub release asset, published 2026-09-30); 44.5.0 unpacked 368 MB, `electron.exe` 245 MB |
| Electron bare window, 40 s, sandboxed renderer, no crash reporter, no updater | **zero** TCP connections and **zero** UDP endpoints owned by any `electron.exe` process across 11 samples at 2 s intervals (`Get-NetTCPConnection` / `Get-NetUDPEndpoint`); the acceptance test in §3.7.4 replaces sampling with a packet capture, because a request shorter than the sample interval could hide from this method |
| Hologram stand-in in Electron (27 edged cubes, 4,000 additive points, UnrealBloom, 1264×761, DPR 1) | ANGLE Direct3D 11 on the RTX 4070; 55 fps in the first five seconds (shader warm-up), then 60, 60, 60, 60; worst frame 18 to 20 ms |
| WebView2 diagnostic data | per Microsoft's own page, "Regardless of the Windows Diagnostic data setting, WebView2 collects required data that's necessary to maintain performance and reliability", and "you don't have control of overall diagnostic data collection"; an app controls only SmartScreen (`IsReputationCheckingRequired`) and crash reporting (`IsCustomCrashReportingEnabled`) (learn.microsoft.com, WebView2 data and privacy, updated 2026-06-12) |

#### 3.7.1 The four options against the criteria

| Criterion | (a) Fork or bundle Brave | (b) Electron | (c) Tauri (OS webview) | (d) pywebview |
|---|---|---|---|---|
| Zero telemetry, provable by egress test | No, not without stripping: P3A analytics (the Constellation reporter is enabled by default since 1.62), Rewards and BAT, Ads, Leo, Brave Search as default, Brave's own update servers; each is a patch to carry forever | **Yes, measured** (above). No Google API keys are shipped; geolocation needs a key the developer must supply; no crash reporter or updater unless the app adds one | **No on Windows.** WebView2 sends "required" diagnostic data to Microsoft regardless of the Windows setting; the app cannot turn it off. macOS WKWebView follows Apple's OS telemetry; Linux WebKitGTK has none | **No on Windows**, same WebView2 underneath (pywebview's default Windows renderer is EdgeChromium, MSHTML as fallback) |
| Hologram frame budget (Three.js r128, bloom) | Chromium, fine | **Measured 60 fps**, ANGLE D3D11, worst frame 20 ms | WebView2 on Windows is Edge's Chromium, fine; WebKitGTK on Linux can succeed at WebGL2 context creation "even when the result is backed by a software rasterizer" with "no error to catch", masks the renderer string, and has NVIDIA DMABUF failures whose workaround costs "the faster rendering path" (Tauri v2 docs) | as (c) |
| Voice and microphone permission | Chromium permission model, but the fork's own | One `setPermissionRequestHandler`; the app is the permission principal, so Windows shows Friday, not a browser, in Privacy → Microphone | WebView2 permission API, similar | limited; pywebview exposes little of the permission surface |
| Single signed installer size | a rebranded browser is 200 to 300 MB before Friday | about 150 MB compressed for Electron plus 11 MB Python plus the app and core wheels: **under 250 MB** total | 3 to 15 MB when the Evergreen runtime is present, which Windows 11 ships; 127 MB with the offline runtime; 180 MB with a fixed version | as (c), plus the Python |
| Auto-update and security patching | **Perpetual.** Chromium ships security fixes weekly since 2023 and moves to a two-week major cadence from Chrome 153 (September 2026); a fork must rebuild and re-sign on that clock, on a 100 GB+ checkout, 2 to 4 hours per initial build, Visual Studio 2022 required | Electron ships a new major every 8 weeks and supports the latest three; Friday bumps one dependency per release train and re-signs through the same delta pipeline as §3.4 | Microsoft patches WebView2 through Windows Update; the app carries none of it | as (c) |
| Trademark | forks **must rebrand** (the Braver case; "open source licensing does not grant a trademark license"); no Brave name, lion, or servers | none | none | none |
| Windows first, Mac and Friday Linux planned | Chromium builds all three, at the cost above | win32-x64 150.7 MB, darwin-arm64 124.2 MB, linux-x64 117.3 MB from one code base; Friday Linux runs the same app as the session shell | WKWebView is fine on Mac; Linux is the weak leg for a WebGL identity layer | as (c) |
| Effort | months, then a standing team | **8 to 12 days** for the shell, plus 3 to 5 for the egress CI test and packaging integration | 10 to 15 days (Rust toolchain, sidecar for the Python server) and the telemetry rule still fails | 3 to 5 days, and the telemetry rule still fails |

#### 3.7.2 Recommendation: an Electron shell around the Python server

- **The server stays Python.** Electron is a thin shell: its main process starts or attaches to the existing Flask server on loopback, and a single `BrowserWindow` loads `https://localhost:<port>` or `https://agent.<name>` through Friday's own trusted certificate. Nothing in `index.html` changes to run inside it; the served UI stays reachable from an ordinary browser tab too, because the phone and household devices need it.
- **What the shell adds, natively:** the window and taskbar identity with her mark as the icon; Windows toast notifications through the `Notification` API with Friday as the sender; a global hotkey through `globalShortcut` (replacing the pynput hook and its "ready is not hooked" failure mode); deep links through `app.setAsDefaultProtocolClient('friday')` so `friday://approve/<id>` and `friday://open/knowledge` work from the phone, mail and the tray; a single-instance lock; the tray icon moved into the same process; microphone and camera permission handled once by `setPermissionRequestHandler`, so Windows lists Friday under Privacy → Microphone.
- **Zero-telemetry hardening, in code and asserted in CI:** no `crashReporter`; no Electron `autoUpdater` (updates come from Friday's signed manifest, §3.4); `spellcheck: false` so no dictionary downloads; no `GOOGLE_API_KEY`; a navigation guard that refuses any top-level navigation outside loopback and `agent.<name>` and opens external links in the system browser; `sandbox: true`, `contextIsolation: true`, no `nodeIntegration`; a Content Security Policy in the served page, which today's bare test warned about.
- **Bundled browsing is not the shell's job.** When Friday browses for the person (research, News), she does it in the server's fetcher and the actuation layer (§20 of the north star), not in her own window. The shell shows her; it is not a browser.
- **Packaging:** the Electron shell is one more payload in the single signed installer of §3.1; the size target in §3.1 becomes **under 250 MB**, and the install time target stays under two minutes because nothing is downloaded. The version-directory, delta and rollback mechanism is unchanged; Electron's own updater is not used.
- **Mac and Friday Linux:** the same shell builds for both from one code base (the notarised DMG of §3.3; on Friday Linux the shell runs as the session's kiosk window). Tauri is the right fallback only for a future device where 150 MB is unaffordable, and only if its webview passes the same egress test, which WebView2 on Windows cannot.

#### 3.7.3 Learn from Brave, build our own: adblock-rust inside Friday

The part of Brave worth taking is not the browser. It is **adblock-rust** (MPL-2.0, verified from the repository), the filter engine behind Brave Shields: it parses ABP, uBlock Origin and hosts-file syntaxes, does network blocking and cosmetic filtering, compiles to native code or WebAssembly, and ships Rust, npm and community Python bindings. Friday adopts it in the server's page fetcher for News and research:

- Every page Friday fetches for the person is passed through the engine with EasyList, EasyPrivacy and the uBlock Origin lists before extraction, stripping trackers, ads, consent walls and cosmetic clutter. Cleaner pages mean fewer tokens for Bonsai2 and fewer third-party hosts contacted on the person's behalf.
- Filter lists are public data fetched like the update manifest, on the same consent, and pinned by hash; no request goes to Brave. The engine runs in-process through the Python binding, or as a tiny signed sidecar if the binding lags a Rust release.
- MPL-2.0 is file-level copyleft: unmodified use inside Friday carries no obligation beyond attribution in `THIRD_PARTY_LICENSES.md`; any change to the engine's own files is published. This is engineering, not product intent, and needs no decision from the owner.

#### 3.7.4 Acceptance for the shell

- **Egress:** a CI job runs the packaged shell against a local server for ten minutes idle and ten minutes of ordinary use, under a packet capture on the host, and fails on any packet to a non-loopback address that is not the person's own configured cloud provider during an approved call. The sampling method used above is not the acceptance test.
- **Frame budget:** the real `index.html` lattice with bloom holds 60 fps at 1080p on the mid-GPU row of the clean-machine matrix (NS-34.3) and degrades to the flat interface, announced, below 30 fps.
- **Identity:** one window, one taskbar entry with her mark, notifications signed "Friday", `friday://` deep links round-trip from a notification to an approval card, the hotkey registers on every boot and reports failure visibly when it cannot.
- **Size and time:** signed installer under 250 MB; install under two minutes with no network on the clean-machine matrix.

## 4. Birth and naming

Birth is the first thing the person sees after the installer's last pane. It is not a form. It is Friday, speaking, with her mark forming beside her name.

### 4.1 The mark, at birth

- The installer's last pane starts Friday; her first act is to create `~/.friday/avatar/seed` (32 bytes from `os.urandom`, mode 0600, never exported; `avatar-visual-genome.md:421-430`). The sigil (arm count, tilt, accent placement) is derived from that seed and never changes (`:452`). The prototype's `renderHolo()` shows the idea: same seed, same mark, on every screen.
- "Her first look forms during setup": the genome's continuous genes start at v1 (Genesis Lattice), and the birth step records the first signed step with the persona choices as its signals, so the weekly evolution has a real starting point instead of an empty history. Motion during birth happens only when she speaks or works, per the processing-states rule "no event, no motion" (`avatar-visual-genome.md:1264-1294`).
- The recovery kit (§4.3) prints the sigil beside the date, so a person can tell their Friday's kit from another household member's.

### 4.2 Name and address

- "What would you like to call me? Most people keep Friday." The name is cleaned and capped at 32 characters as today (`setup_chat.py:324-327`). The address preview updates as they type: `https://agent.<slug>`.
- **Padlock without elevation, from the first minute.** Friday's own name-constrained CA already exists (`services/local_ca.py`). This spec extends the constraint to two names, `agent.<slug>` and `localhost`, so that `https://localhost:3000` shows a padlock the moment the user trusts the CA (a Windows Security Warning the card explains, with the thumbprint printed for comparison, as today). Binding 443 on loopback needs no elevation on Windows. Microphone and camera therefore work in the browser before any hosts edit, which today they do not until the name is trusted (`installation.md:433-453`).
- **The one elevation, explained on the prompt.** Giving her `agent.<slug>` needs one line in the hosts file. The card says exactly what will change, that it is backed up first, that it is removed on uninstall, and that saying no loses nothing. The elevated step is the existing marked-block writer (`services/local_address.py:551-607`). The UAC prompt's own text names the action. Offered once at birth, then available in the control room, never nagged. If the person is not an administrator of their PC (a work laptop), the card says so and skips.
- **The phone is later and it is theirs.** Remote access is off. When offered in week three, it is a pairing through the person's own Tailscale account (a private network with device identity and Let's Encrypt certificates for its names) or their own Cloudflare Tunnel with Cloudflare Access in front. Friday opens no inbound port and routes nothing through FutureSpeak. Pairing shows a code on the PC and the phone scans it; the paired device gets a per-device identity and permission set (NS-27.9) and never holds the vault key.

### 4.3 Vault first, then recovery, then the drill

The order is the implemented one (`vault-first-onboarding.md`), extended with the three pieces the north star requires and the code lacks: an OS-bound unlock, a generated recovery key, and an explicit no-recovery acceptance (NS-8.4-4, NS-8.5-2).

- **Three ways to lock, the person's choice, nothing pre-selected.** Windows Hello (a key in the Microsoft Passport Key Storage Provider, sealed by the Hello gesture, used to wrap the vault key; fast; tied to this PC and this Windows account; needs the kit to move), labelled **recommended** where Hello is enrolled; a passphrase (Argon2id as today, never stored readable; works on any machine; lost with the kit means lost for good); or "no vault for now" (sensitive data is refused rather than stored in the open, and Friday says so when it happens, rule 7). Each option states what it means, what it costs the person (a gesture, a phrase to remember, refusals), and what changes later (the other two can be added or switched in the control room with a receipt). The person picks; "use recommended for the rest" on the birth summary can pick Hello for them only if they press it.
- **The recovery kit.** Twenty-four words (BIP-39 wordlist, 256 bits) shown once, printable as one page with the sigil and the date, saveable as a PDF outside `~/.friday`, copyable with a sixty-second clipboard clear. The words rebuild the vault key on any machine; the OS keychain copy does not travel (`docs/user-guide/backup-and-restore.md:8-19`), which the card explains as the difference between "getting into this PC" and "getting your vault back on another one" (NS-8.5-3).
- **The restore drill, thirty seconds, before the vault counts as protected.** Friday locks the vault, asks the person to open it with the method they chose, and asks for two of the twenty-four words by number. Only then does the setup receipt say "vault: protected, recovery verified". Skipping records "vault: protected, no recovery" in plain words, and Home's "what's waiting" holds the drill for later, once.

### 4.4 Where thinking happens

Five profiles, nothing preselected, each stating what leaves, what does not, speed and quality, cost, what is unavailable, and how to change later (NS-8.6-1..3), with **Local preferred** labelled recommended on machines at or above the Bonsai2 floor and **Cloud preferred** labelled recommended below it: **On this device only / Local preferred / Ask each time / Cloud preferred / Custom**. The preflight's tier lookup feeds the copy ("this PC runs Bonsai2 27B; size and time from the tier table, speed measured after download", §3.6). No other local model is named. Cloud keys go into a secure field and straight into Credential Manager; key-shaped text in chat is refused, as today (`setup_chat.py:314-321`). The scheduled-cloud question ("about $9 a month", `setup-chat.md:166-168`) stays, asked only when it applies.

Two more choices sit on the same screen, grouped under "your data", because both decide where the person's own words go and how they are kept. Neither is pre-selected.

- **Where the picture of you is built.** (a) **On this PC only** (recommended): the local Bonsai2 does every extraction; raw mail and documents never leave; slower on the floor tier, with honest idle-time estimates. (b) **Cloud per source, when I say so**: local by default, and a cloud path can be chosen for a named source, each time, with time and cost shown and only scrubbed text sent. (c) **Cloud by default for extraction**: fastest; the person's own key and bill; every call in the egress ledger; Tier 2 and Tier 3 still never leave. What changes later: any of the three can be switched in the control room's Models and seats, and a switch re-queues nothing already done.
- **How the wiki is kept on disk.** (a) **Encrypt the whole wiki** (recommended when a vault lock was chosen): every page is AES-256-GCM at rest and opens only through Friday or the recovery kit; other Markdown tools on the PC stop reading the files; backups carry ciphertext. (b) **Readable Markdown, with sensitive sections encrypted**: health, money, legal, family and anything marked stay encrypted; the rest is plain files any editor can open. What it costs: (a) loses outside tools and search over the wiki; (b) leaves ordinary pages readable by anything on the PC. What changes later: switching either way re-seals or unseals in place with a receipt (the migration already exists for the encrypted sections, `wiki_engine.py:73-101`). This is the north star's NS-8.8-1 question, answered by each person rather than for them.

### 4.5 Persona, then proactivity and quiet hours

- The six existing sliders (`setup_chat_copy.py:308-319`) plus the four the north star adds: technical language, interruptibility, notification style, delicate subjects (NS-8.11-1). A live preview sentence changes as they move. The result is the editable persona (`soul.py`), never the constitution (NS-13.4-2).
- Voice: local (ready now) or cloud (hears only a PII-free summary made locally first, A4). A "hear a sample" button. How to talk about her is the person's choice; the "you are family" default becomes a choice (NS-13.10-2).
- Proactivity is a choice with nothing pre-selected: **Quiet** (recommended: one daily digest at a time they pick; quiet hours read from Windows Focus Assist if set; "may interrupt while I'm typing" off, NS-8.14-2), **Helpful**, or **Proactive**, each with an example of what a day looks like. Autonomy likewise: **Ask before every action** (recommended), **Draft only**, or **Observe only**; Routine grants and Custom are described and deferred to week two (NS-8.13-1). Two smaller choices sit in the same group: whether Friday may check for updates (it fetches a signed manifest and nothing else; installing is always a card), and whether her look may evolve weekly (recommended on, per the genome spec; off or approve-each-step are the alternatives; the sigil never changes either way).
- The ten personality questions keep their skips, including "rather not say" on every one (`setup_chat.py:220-225`).

## 5. Setup as a conversation with Friday herself

### 5.1 The shape of the conversation

- **Voice-first from minute one, with captions on.** Friday speaks each step's line (the prototype's "Friday says" panel) and listens on push-to-talk (Space) or on her name in room mode. Every spoken line is also the visible transcript and an `aria-live` region. "Say that again", "slower", "mute Friday" and "type instead" are on every step (NS-33.3-1, -2).
- **Flat and keyboard-complete.** Each step is one card with ordinary controls, Tab order top to bottom, focus moved to the step heading on change, Enter to continue, Escape to close a dialogue. Nothing requires drag or hover. The 3D layer is one toggle away from off, from the first step (A1).
- **No model needed for any of it.** The chat continues to run on rules with no key and no local model (`setup_chat_copy.py:3-8`). The only model-touching steps, reading the personality answers and drafting the first profile pages, keep the existing reader choice and say which model reads (`setup_chat_copy.py:151-165`).
- **Resumable, re-runnable, skippable.** State stays in `setup_chat.json` with no secrets (`setup_chat.py:11-14`); "Set up later" completes with defaults; every step is re-runnable from the control room's "Getting to know you" (`setup_chat.py:722-755`). Onboarding never really ends (§9.8).
- **Two things the conversation never does:** it never asks for a key or password in chat (refused, `setup_chat.py:314-321`), and it never claims a guarantee the code cannot keep (`vault-first-onboarding.md:549-562`).

### 5.2 The order, and why

1. **Preflight** (installer, model-free): honesty first, before she has a voice.
2. **Hello and contract**: what she is, no account, asks before acting, inspect and export, skip and resume (NS-8.3-1). The threat model opens in place (NS-8.3-2).
3. **Name, mark, address**: identity before secrets, because the recovery kit carries the mark.
4. **Vault, recovery kit, drill**: before any source is connected, so nothing sensitive is ever ingested into an unlocked store.
5. **Routing**: before any source, because the answer decides where extraction happens.
6. **Persona**: her voice and tone, so the rest of setup already sounds like her.
7. **Proactivity, quiet hours, autonomy profile**: how loud she is, before she has anything to say.
8. **Connect your life** (§6).
9. **First major task begins** (§7) and the conversation hands over to Home.

**Keeping the choices humane.** Birth asks for nine decisions that are the person's (vault lock, recovery, routing, where the picture is built, how the wiki is kept, voice, proactivity, autonomy, update checks and look evolution), grouped into four screens: *lock and recovery*, *thinking and data*, *how she talks*, *how loud she is*. Every group screen has a visible button, **"Use recommended for the rest"**, which fills the unanswered choices in that group with the labelled recommendations and lists what it chose before the person continues; it is never pressed for them. The setup summary (§5.3) shows every choice with a "you chose" or "recommended, accepted by you" tag, and each is one click from the control room later.

The consent screens that exist today (collects, vault, routing, cloud acknowledgement, third parties, updates) fold into steps 2, 4, 5 and 8 as the sentences they already are, rather than a separate six-screen gate. The third-party explanation (NS-8.12-1, shipped) stays verbatim at the top of the Messages group.

### 5.3 Setup summary and the setup receipt

The last card of birth is the north star's summary page (NS-8.15-1): principal, reasoning mode, models installed and pending, sources and scopes, storage and vault state, autonomy, notifications, backup status, what Friday knows so far, what is unconfigured, and "export setup receipt". The receipt is JSON in `~/.friday/receipts/`, signed with the governance key, and the same file the control room's Health section shows.

After the summary, one more card: the **Model Soup** offer (§3.6). "Not now" is remembered; the card lives on in Models and seats.
## 6. Connect your life

### 6.1 The card contract

Every connector card, whether OAuth, app password, bridge or import, renders the same eight lines, and a card that cannot fill a line is not shown as connectable (NS-8.9-2, NS-23.1):

| Line | Meaning |
|---|---|
| Enables | what connecting makes possible, in one sentence |
| Sees | the exact data, in words, and the exact scopes, in a disclosure |
| Read / Write | separate switches; write is off and disabled until a workflow needs it, when it arrives as a permission diff plus the provider's own flow (NS-23.2-2) |
| Processed | where: on this PC (Laya, local model), or a cloud model that sees scrubbed text and only when routing allows |
| Kept | what stays: facts and pages, never a copy of the source unless the person asked for one |
| Acts alone | whether Friday may act through it without a card; at setup the answer is always "no" |
| Revoke | one button here, plus the provider's own page, and what happens to facts already learned ("forget this source" is a separate, explicit act with a deletion receipt) |
| Health | last verified, using the single `Health` verdict from `connector-ecosystem.md:196-231`, so "connected" never means "present" |

Grouped by outcome, as the north star asks (NS-8.9-1): **Email and calendar**, **Files and writing**, **Messages**, **Work tools**, **Media and publishing**, **Imports that need no account**. The vendor-grouped `GROUPS` in `services/setup_connections.py:33-44` become this.

A sticky summary at the top says, in a sentence, what she can see so far and what leaves the PC, and it is an `aria-live` region. When two sources are on, Friday says so: two are enough for first value (NS-9.1-1).

### 6.2 One tap where it is honest, least privilege always

- OAuth where a verified path exists; the provider's own consent page in the system browser; PKCE and loopback redirect from the shared OAuth module the connector ecosystem specifies (`connector-ecosystem.md:271-279`).
- Read scopes first, always. The Google default set today includes calendar and tasks read-write (`services/google_accounts.py:72-73`, NS-8.9-3 partial); this spec drops the write halves from the default request.
- Before any provider page opens, an in-place sheet lists the scopes being asked for and the ones not being asked for, and says what happens when a scope expires (the prototype's "Before Google's page opens").

### 6.3 The hard truth about Gmail, told on the card

Reading Gmail through Google's own sign-in needs Google's app verification. `gmail.readonly` is a **restricted** scope; calendar is **sensitive** (`google-oauth-onboarding.md:87-104`). Facts, as of writing:

- **Sensitive scopes** need brand verification: a homepage, a privacy policy, a demo video, scope justifications; no fee; four to six weeks. The checklist for it exists and every box is unchecked (`google-oauth-verification-checklist.md:135-164`).
- **Restricted scopes** need the above plus an annual CASA security assessment. The cost question is unresolved in the repo and worth up to $75k a year depending on how one Google sentence is read (`:95-108`); authorised labs advertise Tier 2 assessments from a few hundred dollars for self-scanned apps to tens of thousands for full engagements. One email settles it, and the checklist says to send it first (`:186-199`).
- **Until verification:** a bundled client in Testing mode limits access to a hand-entered list of test users and expires their refresh tokens every seven days (`:20-46`); a bundled client In production but unverified shows the "Google hasn't verified this app" interstitial and carries a lifetime cap of one hundred users per scope (`google-oauth-onboarding.md:58-75`).

So the card offers four ways in, ordered by what is honest today, with the cost of each:

| Option | Cost to the user | Cost to FutureSpeak | When |
|---|---|---|---|
| **Google Takeout import** (mbox + calendar ICS + contacts) | free; ten minutes to request, a wait for Google to prepare it | none | today; recommended first |
| **IMAP with an app password** (needs 2-Step Verification on the Google account) | free; three minutes | none | today; live mail, read-only if wanted |
| **Bring your own Google sign-in** (the person creates a Desktop client in Google Cloud and pastes two strings; Friday walks them through it; the 100-user cap is theirs alone; the interstitial appears once) | free; ten fiddly minutes | none | today; already resolved as `byo` in `services/google_oauth_client.py` |
| **FutureSpeak's verified sign-in** (one tap) | free | verification effort now; the CASA fee yearly if restricted scopes are kept | after decision D2 |

**The route is the person's choice, made on the card.** All four are shown together with the trade-offs above; the one Friday would pick today is labelled recommended (Takeout import while one-tap does not exist; one-tap once FutureSpeak's sign-in is verified); none is pre-selected, and the choice can be changed on the same card later with a receipt. The card shows the seven-day expiry as a countdown when the Testing path is in use, reminds the person the day before, and offers the Takeout or IMAP route in the same breath. Friday never lets a silent expiry look like "connected".

**Microsoft, briefly.** Personal Outlook.com accounts no longer accept basic-auth IMAP, so live access needs an app registration and OAuth. A `.pst` export needs no account at all and is the first thing the Outlook card offers. Work tenants may need an administrator's consent; the card says so instead of failing later.

### 6.4 Imports that need no account

The largest, safest source is an archive the person already owns. The import path is the north star's seven steps (NS-8.10-2), and it is the same path for every format:

1. Choose files and a time range: Google Takeout (`.zip`/`.tgz` with mbox, ICS, contacts, bookmarks, Keep), a Microsoft export (`.pst`, Outlook CSV), `.mbox` from any client, browser bookmarks (`.html`), chat exports (WhatsApp `.txt`, iMessage exports), local folders, a CV or portfolio (`.docx`, `.pdf`), published work (URLs fetched once and cited), a previous **Friday Bundle**.
2. A local scan reports what is inside: categories, counts, dates, how much looks sensitive, and an estimated extraction time on the local model, with "measured" or "estimated" on the number.
3. The person excludes folders, senders, people or years.
4. Everything is staged, not stored.
5. Proposed facts, people, projects, commitments and pages are presented.
6. Accept, edit, reject or defer, per class or per item, in the review queue (§7.6).
7. Only accepted items, or items under a policy the person set, become durable.

Whether the archive itself is deleted after import or kept is asked on the scan card (delete is labelled recommended; keep is one tap). Nothing from an import becomes truth silently (NS-8.10-3 ends the 0.6-confidence auto-promotion for imported material).

### 6.5 Connectors: legitimate first, and borrowing only what is the person's to lend

The owner's brief, verbatim: "we're going to need to have as much of a legitimate connector ecosystem as possible, and where impossible we will just reach into the codex and Muse and Claude installations to borrow those connectors, with permission of course."

**The legitimate ecosystem, as big as it can be.** Three kinds, all under the card contract of §6.1: Friday's own OAuth connectors (the Google reference implementation and the unified registry of `connector-ecosystem.md`); standard MCP servers, including vendors' official ones, each shown with its declared tools, a domain allowlist and a size cap (NS-23.10); and archive imports (§6.4, §6.6). Where a vendor publishes an MCP server, Friday lists it before anything home-made.

**"Borrow from other AI apps, with permission" means importing their MCP server configurations, nothing else.** Other AI apps on the PC keep lists of MCP servers the person already set up. Those lists are the person's own work, and Friday can save them the trouble of doing it twice:

| App | Where its list lives (verified on the reference machine, 2026-09-30) | Shape |
|---|---|---|
| Claude Desktop | `claude_desktop_config.json` in the app's roaming folder | JSON, `mcpServers` map (empty on the reference machine) |
| Claude Code | the user-level `~/.claude.json` (`mcpServers`, three servers on the reference machine), `~/.claude/settings.json`, and any project `.mcp.json` (two found under the person's projects) | JSON, `mcpServers` map |
| Codex | `~/.codex/config.toml` | TOML, `[mcp_servers.<name>]` tables (one on the reference machine) |
| Cursor | `~/.cursor/mcp.json` | JSON, `mcpServers` map (not installed on the reference machine) |
| Gemini CLI | `~/.gemini/settings.json` | JSON, `mcpServers` map (not installed on the reference machine) |
| Grok, "Muse", any other MCP-capable app | no application named Muse and no Grok desktop app exists on the reference machine; Friday's discovery is by shape, not by name: any readable file matching the `mcpServers` JSON schema or the `[mcp_servers]` TOML schema in a known app folder is a candidate | as above |

The flow: with consent, Friday scans those files, shows each server it found (name, command or URL, the app it came from, the environment variable names it expects, never their values), and the person picks what to bring over. Friday then runs or connects those same user-owned servers herself, under her own registry, her own allowlist and her own size cap, **re-authenticating where a server needs it**: a stdio server is started fresh; a remote server goes through Friday's own OAuth flow with PKCE and gets its own token.

**The hard line, written plainly.** Friday never extracts other apps' stored OAuth tokens, API keys, cookies or session secrets. Those credentials were issued to those apps' clients. Reusing them violates the providers' terms, risks getting the person's accounts flagged, and is exactly the behaviour Friday's own defences (the egress gate, the credential store's ownership metadata, the behavioural monitor) must treat as malware. The scanner reads server *definitions* and stops at the first field that looks like a secret; a definition whose value is a secret is shown as "needs your key" with a secure field. Friday's own credential inventory never gains an entry it did not mint.

**What cannot be borrowed, said on the card.** Connectors that exist only on a vendor's servers, the cloud connectors inside claude.ai, ChatGPT or Grok, are not on the person's PC and cannot be brought over. For each, the card offers the vendor's public MCP server if one exists, Friday's own OAuth connector to the same service, or an archive import, and says which of the three it is offering and why.

### 6.6 Import AI histories and memories from the big providers, all of them

The owner's brief, verbatim: "grokbot as well. We should also be able to import context and chat histories and memories, all of them, from the Big AI providers."

These histories are some of the richest context for the first major task: years of the person's own questions, plans, drafts and corrections, already in their words. The card sits **early in Connect your life, right after email and calendar**, with a guided "download your export" walkthrough per provider. Export paths change; the table below was verified on 2026-09-30 and the build keeps it in a data file the guided steps read, so a change is a data update, not a release.

| Provider | Official export path today | What arrives | What does not, and what Friday does about it |
|---|---|---|---|
| ChatGPT | Settings → Data controls → Export data; a zip arrives by email | `conversations.json` (every message, timestamps, model), `chat.html`, account info, shared links, uploaded files | saved memories are not reliably in the zip; the guided step has the person open Settings → Personalization → Manage memories and paste the list into a secure text field, which Friday stores as `import` memories with that provenance |
| Claude.ai | Settings → Privacy → Export data; the link arrives by email and expires after 24 hours | `conversations.json` (messages, content blocks, citations, tool activity, attachment metadata), `users.json`, usage logs | projects, project knowledge and memory are not separated out; the guided step covers copying project instructions and memory text by hand |
| Gemini | Google Takeout → **My Activity → Gemini Apps** (the "Gemini" product entry exports Gems, not chats); choose JSON | `MyActivity.json`, one record per prompt with the response and a timestamp | "Saved info" has no export; the guided step has the person copy it; Friday warns that some Takeout Gemini exports arrive thin |
| Grok / xAI | grok.com Settings → Data controls → Export account data, or the account data download at the xAI accounts site | a whole-account archive | no per-conversation export; Friday imports the whole archive and lets the person exclude by date |
| Microsoft Copilot | the Microsoft account privacy dashboard → Copilot activity history → Export all activity history | a CSV | thin; Friday extracts what is there and says so |
| Perplexity | no native bulk export; a data-access request under privacy law returns the person's threads; per-thread export from the app | JSON or text per thread | Friday accepts both and the folder a browser extension produced, with the extension named as the source |
| Meta AI | meta.ai → Download your information; in WhatsApp, `/download-all-ai-info` in a chat with the AI; Instagram and Facebook through their privacy centre | per-surface archives | each surface is a separate source with its own provenance |

**The person's own local agent logs, with permission.** These are on the PC already and need no export: Claude Code session logs (`~/.claude/projects/<project>/*.jsonl`; 49 project folders and 1,037 files on the reference machine), Codex sessions (`~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl`; 108 on the reference machine), Cursor's workspace state, Gemini CLI's history. Friday lists what she found by count and date range, and the person ticks folders. Code and tool output inside these logs is classified before anything else, because it can carry secrets; anything that looks like a key is dropped at the scanner, never staged.

**The import, the same seven steps as every archive (§6.4), with these specifics:**

- **Provenance per conversation:** every fact carries the provider, the conversation title and the date, so the wiki can say "learned from your ChatGPT history, March 2024" and the fact browser can filter by it.
- **Dedup across sources:** the same plan told to three assistants becomes one fact with three sources, not three facts; contradictions between assistants (a project called finished in one and ongoing in another) become a `disputed` pair for the review queue (§7.8).
- **Sensitive zones:** health, money, legal and family go to the vault zone unread by any cloud model, exactly as for mail.
- **The review queue by voice** promotes what becomes durable.
- **"What the other AIs knew about you":** a page per provider listing the durable facts, preferences and memories drawn from it, with the provider's own saved-memory list shown beside Friday's reading of it, and every line correctable or forgettable. It is the first thing shown when the import finishes, because it is the fastest way for the person to see what Friday thinks she has learned and where she is wrong.

### 6.7 The self-knowledge base is entirely optional

The owner's framing: the self-knowledge-base step is offered as an opportunity, never a requirement. The connect screen, the import flow and the Getting to know you surface (§7A) say so in these words, and the code keeps them true:

- **Opt-in.** Nothing is imported, read or extracted until the person says yes to a named source. Declining leaves Friday fully usable for chat, voice and everything that needs no history.
- **Guided.** Each provider and platform has plain step-by-step instructions for its official export, shown in place, and Friday imports the files the person downloads.
- **Local and encrypted.** Processing is on this PC; Tier 2 and Tier 3 facts and every page in a sensitive section are encrypted at rest in the vault (`vault_crypto.py`, `wiki_encrypted_sections`); the raw archives are deleted after import unless the person keeps them.
- **Reviewed.** The review queue stands before anything durable.
- **Visible.** "How complete is my picture" (the density and coverage meter, §7.8) shows what is covered and what is left, per source.
- **One step out.** Opting out, pausing or deleting any source is one control on its card, and deletion writes a deletion receipt that traverses the graph, indexes, summaries, caches and proposals (NS-15.7).

## 7. The first major task: the biggest, most accurate knowledge graph and wiki of the user

### 7.1 What "biggest and most accurate" means here

This task is optional from the first word (§6.7): Friday offers it, the person chooses each source, and every source can be paused or deleted in one step. Biggest: every person, project, organisation, place and topic that the connected sources support, with every dated event on a timeline and every promise found. Accurate: nothing durable without provenance, nothing merged without evidence, nothing sensitive outside the vault zone, nothing published without the person's eyes on it, and a correction path from every fact back to its source. The two pull against each other; the review queue is where they meet.

### 7.2 Pipeline

```
sources ──► scan ──► stage ──► classify (Laya, CPU) ──► extract (local model, GPU)
        ──► resolve entities ──► timeline + commitments ──► zone sensitive
        ──► draft pages with citations ──► review queue ──► publish to wiki + graph
```

- **Staging is real.** `~/.friday/wiki/_staging/` becomes a working directory with one JSON per proposed item carrying the north star's memory fields (NS-15.2-1): source refs, observed and valid dates, confidence, authority (`import`, `model-inferred`), sensitivity, status `proposed`. The graph builder already skips `_staging` (`wiki_graph.py:45-46`); the review queue promotes items out of it.
- **Classification before extraction.** Laya answers, per item, on the CPU in about 0.3 s: sensitive tier; touches a third party; is a commitment shaped sentence present; which of the A-F source families it belongs to. Tier-3 items go straight to the vault zone unread by any model except a local one under an explicit reveal. Laya's disagreements with the keyword rules are queued for "teach the scanner" (§8.3).
- **Extraction is local by default.** The existing Tier B GraphRAG prompts run through the local seat (`indexer.py:426-470`). The cloud path is offered with a measured estimate and a cost ("about 40 minutes and about $6; Friday will not use it unless you say so") and, if taken, sees only egress-gated, scrubbed text, never Tier 2 or 3 content. Extraction adds the two entity types the vendored prompts lack for this job, `commitment` and `role`, and keeps `person, organization, project, tool, concept, event, place` (`indexer.py:58`).
- **Provenance on every fact.** The existing provenance block (`knowledge-system-spec.md:159-187`) plus a span citation: file, message id or URL, and the sentence. A fact without one is refused, as research facts already are (`integration.py:82-146`).

### 7.3 Entity resolution across sources

Today's resolution is a hash of the upper-cased title (`indexer.py:418-419`). The first major task needs:

- **Candidate generation:** name similarity (normalised, initials, diacritics), shared email address or domain, shared handle, co-occurrence in the same thread, calendar attendee overlap, the trust graph's `trust_person_resolve` where it already knows the person.
- **Decision:** merge automatically only on a hard key (same email address, same handle); otherwise queue a "same person?" question with the evidence lines. The prototype's first card is that question.
- **Result:** one canonical page per entity with an `aliases` list, every merge recorded as a reversible event with its evidence, and "split" available from the page.

### 7.4 Pages, timeline, commitments, zones

- **Auto-written pages** for every person, project, organisation, place and topic, from the existing page model (frontmatter title, tags, summary; `wiki_graph.py:36-43`), each claim followed by a citation chip that opens the source. Drafts land in staging and reach the queue; the person publishes, edits, or rejects. Publishing is the one act that writes into `~/.friday/wiki/` proper.
- **Timeline:** a first-class store of dated events (message sent, meeting held, document created, commitment made, commitment met) keyed to entities, replacing the mtime-only "Timeline" arrangement (`index.html:10694-10697`) with real dates. The Knowledge workspace's Timeline view reads it.
- **Commitments detection:** first-person future-tense promises with a recipient and, when present, a date ("I'll send you the notes by Friday"), found by Laya's shape question and confirmed by the local model, stored as `commitment` memories with status open / met / lapsed. Each becomes a small review question, and the open ones feed Home's "what's waiting" and the first synthesis. This is the seed of NS-15.1's commitment store.
- **High-sensitivity zones:** health, finance, legal, family and anything the person marks. Tier-3 items are stored encrypted (`vault_crypto.py`), never sent to a cloud model, logged on access without content (NS-26.6), and their pages live in an encrypted wiki section (`wiki_engine.py:73-101`). The queue's "sensitive" answer moves an item there; "never read that sender" writes a source rule.
- **Third parties:** facts about other people are limited to role, relationship the person confirms, and contact channel; no inferred traits, and never protected characteristics (NS-8.12-3, NS-15.4-3). Every person page has "forget this person", which already tombstones through the graph (`wiki_graph.py:483-514`).

### 7.5 Running in idle windows, resumable, honest about time

- The job runs when the PC is idle (no input for N minutes, on mains power, GPU not claimed by another seat), yields at once when the person returns, and resumes after sleep. Progress per source is a real fraction of items processed; the estimate is items remaining times the measured per-item time on this machine, labelled "measured" once ten items are through.
- A pause button, "nothing lost", a per-source order the person can change, and a "stop reading this source" that drops what is staged from it.
- The graph grows visibly in the Knowledge workspace: `node_ignited` already reaches the galaxy (`routes/knowledge_graph.py:337-362`); this adds `page_drafted`, `entity_merged`, `commitment_found` and the counts by type the prototype shows. The hologram's **Saving to memory** and **Memory or KG search** states (`avatar-visual-genome.md:1325-1341`) draw one cube per fact saved, from the new `memory_saved` emit the processing-states spec asks for (`:1416`), so the mark itself shows her learning. No fake busy motion: when the job is paused the mark is still.

### 7.6 The review queue, by voice, a few minutes a day

- **Bite-size.** One question per card, one word answers: yes, no, not sure, skip, or "tell me". Keys Y N U S. Four minutes and it stops, whether or not the queue is empty; "keep going" is one press. Held in Home's "what's waiting" as a count, never a notification.
- **Question kinds:** same person; same organisation; commitment still open; this looks sensitive, vault it; publish this page (with the first lines and "read the whole page"); what is this relationship (friend, colleague, both, prefer not to say); what is this project; is this a duplicate; here is a rule I could learn ("file newsletters"), yes or no.
- **Voice:** every kind is a voice tool under the contract (appendix C): a description that names the spoken exits, `spoken_decision` for yes/no, "no" beats "yes", a one-sentence read-back, and a card for anything outward (nothing in the queue is outward). The queue reads the evidence line aloud only on "why".
- **Every answer is a receipt:** what was asked, the evidence shown, what the person said, what changed, signed into the decision BOM (`governance/action_gate.py:680-700`). "Why did you ask me that?" opens it.
- **Learning from answers:** priority and memory only, never governance (NS-9.3-1). "Don't ask me things like this" suppresses a kind (NS-15.5-1 adds suppress).

### 7.7 First value in about fifteen minutes

While the long job runs, a short pass produces **"What I see so far"**: at most five items (NS-9.2-1), each with observation, why it may matter, source, freshness, confidence, related commitment, and a next step (NS-9.2-2). It prefers one modest correct insight over a dramatic one (NS-9.2-3). The person marks each useful / wrong / not important / sensitive / remember / forget / don't surface this kind (NS-9.3-1). The prototype's five items are the target register: an outlet that sends most deadline mail; an editor and an open promise; a triage rule; a project with no page; a sensitive cluster the scanner held back.

The fifteen minutes is measured from the second source coming on, on the local path, and only counts once the person has marked at least one item; the number is written to the local onboarding receipt and nowhere else.

### 7.8 The local model builds it, as dense as the person permits, on every Bonsai2 tier

A binding rule from the owner: on the new user's machine, the local model, the Bonsai2 the installer picked for their hardware, builds the knowledge graph and wiki, as dense as the user permits.

- **All of it, locally:** entity and relationship extraction, cross-source resolution, wiki pages with citations, the timeline, commitments, the "what the other AIs knew" pages. Laya does the cheap classification on the CPU first (§7.2). No cloud model takes part unless the person explicitly chooses a cloud path for a specific source, per rule 2, and then only scrubbed Tier 1 text goes, with the egress ledger showing it.
- **Density goes as far as permissions allow.** Every permission scope is a line on the source card and in the Getting to know you surface, visible and adjustable: which folders, which mailboxes, which years, which people are excluded, whether third-party facts beyond role are kept, whether the vault zone is extracted at all (a presented choice: "flag and hold, don't read" is labelled recommended; "read it locally and keep the facts in the vault" is the alternative). Widening a scope re-queues only the newly permitted items.

**Scaling across the tiers.** Tier figures come from `bonsai2-tiers.json` (session local_6f0b72ec); this section states the policy in terms of the table's fields, not numbers. The build reads, per tier: measured tokens per second, context length, VRAM and RAM.

| Policy | How it uses the tier row |
|---|---|
| Chunk size | context length minus the prompt overhead, never more than the tier's context; smaller chunks on the floor tier, more of them |
| Batch and concurrency | one extraction stream on the floor tier; more only when the residency arbiter reports free VRAM after the seat is resident |
| Estimates | items remaining × the measured seconds per item on this machine; "measured" after ten items, "estimated" before; recomputed every ten minutes and after every pause |
| Idle windows | floor-tier machines run slower and only in idle windows (§7.5); the estimate says "about N hours of idle time" and shows how much idle time the machine has had per day this week, so the person can see why it will take a fortnight |
| Early partial value | the first pass over every source is a shallow one (headers, people, dates, commitments) before any deep pass, so "What I see so far" arrives within the fifteen-minute target on every tier; the deep pass fills in over days |
| Resumable | every item is a checkpoint; a restart, a model swap or a tier change resumes at the next unprocessed item, with the model name recorded on every fact it produced |

**Quality checks, all of them measurable in the surface (§7A.4):**

- **Provenance on every fact:** source, span, date, the model that extracted it, and the review answer that promoted it. A fact without a source is refused at the store (`integration.py:82-146` already refuses research facts without a URL; the rule becomes universal).
- **Dedup:** content-hash identity for exact repeats; for near-repeats, embedding similarity above a threshold plus the same entity set proposes a merge, which merges automatically when the sources agree and otherwise queues a question.
- **Contradiction flags:** two facts about the same entity and attribute with different values become a `disputed` pair; neither is durable until the person picks, unless one is dated later and the earlier one carries a `valid_until`, in which case the later supersedes with history (NS-15.6). Disputed pairs are the highest-ranked questions in the daily set (§7A.3).
- **Density and coverage, one meter the person can read:** per source, the fraction of items processed and the fraction reviewed; per entity type, how many people, projects, organisations, places and topics have a page and how many are still stubs; per page, how many claims are cited versus uncited; overall, a single "how complete is my picture" number that is the coverage-weighted mean, shown with what would raise it most ("your 2021 mail is unread; that would add about 40 people"). The number is honest about what it cannot see: sources not connected count as unknown, not as zero.

## 7A. Getting to know you: the long-running goal, its clearance, its questions, and its surface

The owner's brief, verbatim: "there should be special UI support for this new user onboarding and knowledge building process. This is going to take a long time, particularly for a local model, and it's going to need to chain workflows over days and weeks to make this full and complete. It will probably even need to ask the user questions. So we need to give this special clearance."

### 7A.1 The durable goal

"Getting to know you" is a durable goal in the sense of [`goals-and-delivery-receipts.md`](goals-and-delivery-receipts.md): a chained, checkpointed pipeline with typed blockers and a signed receipt per run. Its stages, each a workflow step with its own receipt: **import → classify (Laya) → extract → resolve entities → write pages → find gaps → ask questions → re-extract**, looping per source until that source's coverage target is met. It runs across days and weeks.

- **Survives everything.** State is a per-source checkpoint (last item processed, last question asked, coverage counters) in `~/.friday/goals/getting-to-know-you/`, written atomically. A restart, a model swap, a tier change or a crash resumes at the next unprocessed item. The brain seat's persistence across restarts is a dependency (§7A.6).
- **Never "done" on a model's say-so.** The evaluator's "Met" is computed, not asked: per source, processed and reviewed fractions reach their targets; per coverage target (people with pages, commitments resolved, disputed pairs settled), the counters reach the numbers the person set or accepted. "Not yet" carries one of the five typed blockers: **needs you** (a question), **waiting outside** (an export the person has not downloaded yet), **run failed** (with the Doctor's line), **insufficient evidence** (a source too thin to reach its target, which lowers the target with a note), **more work**. Only "more work" auto-continues.

### 7A.2 Special clearance: one signed, scoped standing grant

Shown to the person once, right after "What I see so far", and approved once. It is a `create_grant` of a new kind, `standing_local`, signed with the governance key and stored with the goal.

| It covers | It grants nothing outward |
|---|---|
| local reads of the sources the person approved, by name | any new source asks |
| local writes to the knowledge graph, the wiki and the vault zone | anything leaving the machine asks: a cloud path for a source is its own card, every time |
| local-model compute on the resident seat, and the seat's loading and unloading | any send, post, spend or delete asks, as always |
| running outside the normal idle window, during hours the person sets (overnight, say), on mains power | |
| lifting the round and token ceilings that protect interactive turns, for this goal only, because local compute is free (the ceilings and their call sites: `gotcha: caps live at the call site`; `project: no built-in caps`) | |

- **Expiry:** when the coverage targets are met, or after a user-set number of weeks (default four), whichever is first. Renewal is a new card.
- **Revocable from the Ledger** in one click; revocation stops the run at the next checkpoint and leaves the checkpoint intact.
- **Receipts:** every run leaves a receipt (items processed, facts staged, pages drafted, questions queued, model used, minutes of compute, watts if the platform reports them), signed into the decision BOM. The daily digest is built from them.
- **No pestering, enforced:** zero cards during the run. The only card that can appear is one for something outside the clearance (a new source, a cloud path, a send), and even that waits for the daily question set unless the person opened the surface.
- **The dissent gate still reads it.** A run that starts drifting into a source not named in the grant is a scope-drift event for the behavioural monitor and stops the run.

### 7A.3 Questions: a typed blocker that never stalls the whole run

When Friday needs the person, the item raises a **needs user input** blocker on that item only; every other item, source and stage keeps running. Questions collect in an inbox and are served as **one short daily set of about five minutes**, ranked by expected improvement to the picture: how many facts, pages or merges the answer would settle, with disputed pairs and entity merges first and taste questions last. Voice or text; each can be answered, skipped (it returns after the next pass with new evidence, at most twice), or turned off with "don't ask me about this", which suppresses the kind for that source or entity. Examples, in the register the surface uses: "Is D. Ruiz in your 2019 email the same person as Dana Ruiz, your editor?" and "Which of these three projects is still active?" Every answer is a receipt and a fact with authority `user-confirmed`.

### 7A.4 The surface: "Getting to know you", plus a Home card

A dedicated workspace, reachable from Home's card, the control room, and by voice ("how's the picture coming?"). The prototype `knowledge.html` is its first draft.

- **Source map:** one row per source with four states, **connected → imported → processed → reviewed**, each a fraction, plus the permission scopes as editable chips, and pause, delete-with-receipt and "work only while I'm away" per source.
- **The live-growing graph:** the Knowledge galaxy, fed by `node_ignited`, `page_drafted`, `entity_merged`, `commitment_found`; counts by type.
- **The question inbox and the review queue**, side by side: questions are what Friday needs; the queue is what she proposes.
- **Time estimates** per source and overall, honest on floor-tier machines (§7.8): idle hours needed, idle hours the machine actually gives per day, and the date that implies.
- **Pause, resume, "work only while I'm away"**, and the clearance's hours.
- **The density and coverage meter** (§7.8) and a daily **"what I learned today"** digest built from the run receipts, spoken on request.
- **The hologram tells the truth:** the processing states of the genome spec (`avatar-visual-genome.md` §13) drive the mark: memory cubes drift into the core per `memory_saved`, one per fact; a still mark when paused; never a fake busy loop.
- **Notifications:** bundled into the daily digest, at most once a day, inside quiet hours never. **Fully by voice:** every control above is a voice tool under the contract (appendix C).
- **Home card:** the meter, the next question count, and the estimated finish date, in three lines.

### 7A.5 Resources

The run yields instantly on user input (keyboard, mouse, voice, a chat turn), pauses on battery and above a thermal threshold the platform reports, shares the GPU through the residency arbiter (it holds a lease that any interactive seat displaces), and never degrades interactive Friday: the interactive path keeps its own seat priority and its own token ceiling, and the goal's lifted ceiling applies only to its own calls.

### 7A.6 Dependencies

- `goals-and-delivery-receipts.md` (typed blockers, run receipts, the evaluator);
- batch and standing grants (the `standing_local` kind on `create_grant`; today's grants are per tool and schedule, `governance/action_gate.py:603-677`);
- the scheduler's idle-window policy and its override hours;
- the token-ceiling work (call-site caps become per-goal caps);
- brain-seat persistence across restarts (the resident Bonsai2 seat reloads without a person present);
- the processing-states build (PS1 to PS5) for the hologram and the `memory_saved` emit;
- `bonsai2-tiers.json` for the scaling policy of §7.8.

## 8. The first thirty days: an apprenticeship

The arc is opt-in throughout. Friday offers each step once, in Home's "what's waiting", when its precondition is true, and remembers "not now" and "never". Nothing in this section is a notification.

### 8.1 Day by day, week one

| Day | Precondition | What Friday offers | Rule |
|---|---|---|---|
| 0 | birth done, two sources on | "What I see so far"; the review queue's first four minutes | first value (§7.7) |
| 1 | one synthesis item marked | **the first governed action**: one reversible, low-risk act (a draft reply to the open commitment, a private task, a meeting brief) shown as preview → permission class → approval → execution → verification → receipt (NS-9.4-1); the receipt is opened and read aloud once | governance unchanged |
| 2 | first action done | **the first durable goal**: one outcome for the week or month, written together (outcome, done-condition, constraints, deadline, authority limits, check-in cadence, evidence), created only when the person approves the wording (NS-9.5-1); this is the first user of `goals.py:365-395` | proposal, not creation |
| 3 | three days of idle extraction | the review queue reaches the merges and pages; the Knowledge workspace shows the timeline | queue only |
| 4 | a pattern seen three times (mail read at 07:40) | **a routine, from an observed pattern**: the morning briefing at that time, two minutes, spoken or read, local model; offered with the evidence for the pattern | opt-in |
| 5 | one routine on | the weekly review is scheduled for the day and hour the person picks; nothing is scheduled without that | opt-in |
| 6 | the queue has a "rule I could learn" | **the first grant**: narrow, per workflow ("file newsletters and receipts"), expiring in 30 days, use-capped, revocable from the card; created through `create_grant` (`action_gate.py:603-677`) with those limits | never broader than one workflow |
| 7 | week one done | **the weekly review**: what she learned, what she did (receipts), the Doctor report, what she would like to try; the weekly ask about emailing the Doctor report (A3) appears here and only here | ten minutes |

Onboarding is complete, in the north star's sense (NS-9.6-1), when one synthesis is reviewed, one action has a receipt, the person has seen where it ran, one memory has been confirmed or corrected, and one goal has been created or explicitly skipped. The `.setup_complete` marker stops meaning "done" and starts meaning "birth finished"; Home shows the real state.

### 8.2 Week two: rhythm

- **Mail triage as a workflow:** rules learned from the queue, each a grant with a receipt count; the triage card shows what was filed and lets the person undo a day.
- **News and the local podcast:** offered from feeds the person already reads; runs in idle time on the local model.
- **Teach the scanner:** the label queue (`laya_labels.py`) surfaces in the weekly review as five items; agreement between Laya and the rules is shown as a number the person can watch rise.
- **Backups:** a Friday Bundle (NS-27.2) to a folder or drive the person chooses, encrypted, credentials excluded, nightly, retention shown; then the **restore rehearsal**: Friday restores last night's bundle into a scratch folder, reads its manifest, opens ten random pages and compares, and reports pass or fail in Health and the weekly review (NS-27.4-3). A backup that has never been restored is shown as "untested", never as "protected".
- **Persona proposal:** if adaptation has drifted (user_model's EMA), the drift arrives as a proposal with a diff and a preview, never a silent change (A2, NS-13.7).

### 8.3 Week three: reach

- **Device pairing:** the phone, through the person's own Tailscale or Cloudflare account (§4.2); per-device permissions; approval cards answerable from the phone; the vault never leaves the PC.
- **Household principals and minor mode:** "Is anyone else going to use this PC?" leads to a second principal with isolated memory, credentials, goals and audit (NS-25.1), a lock screen with the active principal always shown (NS-25.11), and for a child the minor relationship: guardian permissions, no adult vault, no spending, constrained messaging, no covert monitoring, and no promise of confidentiality the guardian policy does not give (NS-25.7). Today's whole-install `minor_mode` toggle (`setup_chat.py:372`) becomes a principal type.
- **Avatar evolution:** the first weekly step lands; the change is announced with undo, rollback and "evolve now" (A2). The sigil does not move.

### 8.4 Week four: the wider architecture

- **The salon** (vibe-coding, per its spec's announce posture) and **federation** are offered as "things Friday can do with other Fridays and with code", each with its own consent card and each off until then.
- **Modes:** work, travel, off the record, each a named bundle of settings shown as a diff before it is applied (§9.5).
- **The thirty-day review:** what she knows (counts and a sample), what she did (receipts), what left the machine (the ledger's month), what she would drop if the person wants less, and a re-run link for every setup step. The pilot metrics the north star asks for (NS-34.18) are computed here, from local data, and shown to the person; they go nowhere.

### 8.5 Own your tools (weeks two to four, re-runnable any time)

The owner's brief, verbatim: "we're also going to need to try to figure out what tools the user makes use of most on their computer and find open source alternatives that they can own, then gently suggest implementing or building or trying new stuff within the Friday ecosystem. and when we build new stuff, these can go on the federation to earn positrons. we're not building the federation piece or the currency piece yet, but keep it in mind."

- **Opt-in, all local.** Offered once the knowledge graph is established (day 10 or later), as one card: "Want me to look at which tools you lean on, and see what you could own instead?" With consent, Friday reads signals that never leave the machine: installed applications (the per-user and machine uninstall registry, Start Menu entries), Windows usage and jump-list data, browser history domains (counts by domain only, read from the local profile, never the URLs themselves), file types by count under the folders already connected, and, if finance data is connected, recurring subscriptions. The result is a ranked list of the person's top tools, stored locally with the same fields as any memory item (source, observed date, confidence), and shown before anything else happens.
- **A public catalog, solved locally.** The list is matched against a public catalog of open-source and self-hostable alternatives (awesome-selfhosted, the open-source-alternatives lists and similar open data), fetched like an update manifest and solved on the machine the way the Model Soup solves its stack (§3.6). Nothing about the person's tools is sent anywhere. The catalog entry carries licence and telemetry facts; **only software without phone-home can be recommended**, per rule 1, and an alternative whose telemetry cannot be turned off or is undocumented is shown as "not recommended: sends usage data", never hidden.
- **Three paths per tool.** (1) Try an open alternative from the catalog. (2) Use or extend a Friday workspace that already covers it (notes, tasks, briefings, the wiki, the salon). (3) Build your own in the Vibe Coding Salon from a template seeded by what you use (the features you actually touch, from the usage signals). Every suggestion states **what you gain** (ownership, privacy, cost per year), **what you lose** (features, sync, the people who share the tool with you), **migration effort** (an estimate in hours, labelled as such, with the import path for the person's data named: export format in, Friday importer out), and a "why this one" that opens the evidence.
- **No pestering.** Suggestions surface only in the weekly review or in Home's "worth a look", one at a time. Each can be dismissed forever per application; "not now" waits a month. Never a pop-up, never a notification, never a sound.
- **The seam for later.** Anything built through path 3 is recorded with a manifest (what it replaces, what it imports, its licence) so that it can later be published to the federation to earn positrons. **The federation piece and the currency piece are not built here**; they are parked with the federation sprint, and this section only keeps the manifest shape compatible with `vibe-coding-salon.md`.
- **Re-runnable.** From the control room's Getting to know you, by voice ("what tools could I own?"), or from the thirty-day review. Re-running re-reads the signals with the same consent card.

### 8.6 Pacing rules

- One offer per precondition, once. "Not now" waits a week; "never" is final until the person reopens it.
- Home's "what's waiting" is the only place offers accumulate. It has a count, not a badge that pulses.
- No email, no push, no sound for any apprenticeship step. The daily digest may mention that something is waiting, in one line.

## 9. Settings and the other surfaces, dreamed big

### 9.1 The control room

Settings becomes a **control room**: one searchable page, plain language, organised by what the person wants to control rather than by subsystem. Sections: **Getting to know you** (Home's progress and every re-runnable step), **Models and seats**, **Permissions and rules**, **Connections**, **Routines**, **What Friday knows about you**, **Devices**, **Backups**, **Health**, **Advanced**, and a link to the **Privacy map**. Today's tabs (General, Accounts, Privacy, Voice, Models, Providers, Spending, Connectors, Appearance, About) map into these; voice and appearance become sub-pages of Models and of Getting to know you, spending sits under Models.

### 9.2 Four lines on every setting

Every setting renders: **meaning** (what it does, in a sentence a journalist would write), **consequence** (what changes if you change it, including money and what stops working), **who last changed it and when** (you, Friday by a proposal you accepted, a mode, a default), and **undo** (one click for thirty days, with the history behind it). A setting without a consequence sentence does not ship. This is the fix for NS-28.4-2 (silent fallback to defaults) at the surface: a fallback becomes a visible "who changed it: a reset, because the file was unreadable".

### 9.3 Voice-editable, through a diff

"Stop reading my work email after 7" becomes: Friday's reading of the sentence, the settings it touches as a diff (old and new values, in the prototype's red and green), the consequence sentence, and a confirmation. Nothing applies until the yes. The same path serves typed sentences in the search box. Ambiguity ("which account is work?") is one question back, not a guess. The diff and the yes are a receipt.

### 9.4 The privacy map and the ledger

A live map: sources on the left, processors in the middle (Laya, the local seat, the egress gate and scrub, the vault zone), destinations on the right (the wiki and graph, each cloud provider, cloud voice, FutureSpeak with its permanent "nothing"). Lines are drawn from the local egress log (`~/.friday/vault/egress-log.jsonl`, `egress_gate.py:114`) and the activity ledger, never sampled. Clicking any box gives the card contract's lines (§6.1). Under the map, **what left this machine today**: when, to whom, what (with "see exact text"), what was scrubbed, why (the routine, the ask, the approval id), and the day's holds. The route already exists and has no UI (`routes/research.py:107-133`, NS-26.22-1). Egress-gate layer status is shown honestly at the top; if a layer is down, cloud calls are held and the map says so (`gotcha: layer 3 down means fail-open` is the incident this line prevents from recurring quietly).

### 9.5 Modes

**Work** (personal sources muted, work routines on, until a time), **Travel** (local only, no cloud, briefings by voice, until you land), **Off the record** (nothing saved; the existing switch, `off_record.py:1-35`, plus the missing notice that tools may still create external records, NS-15.8-3). A mode is a named diff over settings; entering it shows the diff; leaving it restores. Modes are voice-callable ("go off the record").

### 9.6 "Why did you do that?" on everything

Every action, message, setting change, routine run and review answer carries a "why" that opens: the rule or grant that allowed it, the evidence it used, who asked (you, a routine, a proposal), the model and where it ran, and the receipt. It reads from the decision log (`services/decisions.py`), the decision BOM, the egress log and the goal receipts, which all exist; the surface is the Phase 0 item of the Laya spec (`laya-across-the-harness.md:125-130`). It cannot be turned off.

### 9.7 What Friday knows about you

A browser over memory items with the north star's fields (NS-15.11-1): search, filters by type and source, "why do you know this?", correct (supersede with history, NS-15.6-1), forget (fact, person, source, conversation, with a deletion receipt that traverses indexes, graph, summaries, caches and proposals, NS-15.7), export (Markdown and JSON), and an influence view ("this fact shaped these three briefings").

### 9.8 Home's "getting to know you", and onboarding that never ends

Home shows a progress strip: facts confirmed, waiting for review, sources fully read, first action done, goal created, backup tested, devices paired, household set up. Each is a link to the step. Any setup step can be re-run or added later from here (`setup_chat.py:722-755` already supports it); connecting a source in month six reopens the same import path and the same queue. The strip is data, not a checklist that nags: when everything is done it becomes one line, "You're set up. Here's what's waiting: nothing."

### 9.9 Accessibility, performance and the flat path, as settings

Speech rate, repetition on request, confirmation style, text fallback, interruption, larger text, reduced motion, 3D off, captions, and a screen-reader mode that reads the four lines of a setting as a list. The budgets the north star sets (shell under 3 s, approval card under 300 ms, health view under 2 s) are printed in Health as measured numbers on this machine, so a slow PC is told rather than left guessing.
## 10. Walkthrough: a new user, from download to day 30

Maya is a journalist. She is not technical. She has a laptop with a 12 GB GPU and a Gmail account.

- **Minute 0.** She downloads one file, 146 MB, from the release page. The SHA-256 is printed beside it. Windows shows a blue box; the publisher line reads the company name; she clicks Run.
- **Minute 1.** One window. "Install Agent Friday. Installs for you, on this PC, in your own folder. No administrator password. No account to create." Two toggles. Continue.
- **Minute 1.5.** Preflight, four seconds, no network: Ready now / Available after download / Cloud available / Unavailable on this device. "Camera: none, head tracking unavailable." "Windows Hello: fingerprint enrolled, can unlock Friday's vault." Install.
- **Minute 3.** The bar moves only as steps finish: signature, runtime, files, hashes, shortcuts, receipt, start. "Installed, 1 min 48 s. She has not met you yet."
- **Minute 3.5.** Friday speaks, captions on. Her mark forms beside her name. "What should I call you?" Maya says her name. She keeps "Friday". The address preview reads `https://agent.friday`; she takes the one Windows prompt, which names what it changes.
- **Minute 6.** The vault. Three ways to lock, none ticked; Windows Hello carries a "recommended" tag because her fingerprint is enrolled. She picks it. Twenty-four words appear once; she prints the kit with the sigil on it. Friday locks the vault and asks her to open it and to say words 7 and 19. "Protected, recovery verified."
- **Minute 8.** Thinking and data. Nothing preselected. She picks Local preferred, which carries the recommended tag on this PC; Bonsai2 27B, this PC's tier, starts downloading in the background with its size from the tier table, "time measured after the first 100 MB". No other model is mentioned. Below it, two more: where the picture of her is built (she picks "on this PC only", recommended) and how the wiki is kept (she reads both, pauses, and picks readable Markdown with encrypted sections, against the recommendation, because she wants her notes app to see the pages; Friday says what that means and moves on).
- **Minute 11.5.** The setup summary, the setup receipt, and one card: the Model Soup. She says not now; it waits in Models and seats.
- **Minute 10.** Sliders, a preview sentence, a local voice sample. On the "how loud she is" screen she presses **Use recommended for the rest**: Friday lists what it chose (Quiet, Ask before every action, check for updates, weekly look changes with undo) and waits for her to continue. Quiet hours read from Windows.
- **Minute 12.** Connect your life. Every card says the self-knowledge base is optional and that whatever she connects stays on this PC, encrypted where it is sensitive. The Gmail card shows all four routes with their trade-offs and a recommended tag on the Takeout import; she picks it, because she already has one from last year, and drops it in. She adds IMAP with an app password in three minutes for live mail. The "your other AIs" card finds her Claude Code logs on disk and walks her through the ChatGPT export; she requests it and moves on. Two sources. "That's enough."
- **Minute 14.** The scan reports 41,208 messages, 2019 to now, 6 h 20 min of idle extraction on the local model, "or about 40 minutes and about $6 in the cloud; I won't unless you say so." She leaves it local. The galaxy starts to grow.
- **Minute 20 to 27.** "What I see so far": five modest things, one of them an open promise to her editor. She marks four useful, one wrong. The review queue runs four minutes by voice: a merge, a commitment, a health message to the vault, a page to publish. She stops. Home shows what's waiting: 41.
- **Day 1.** The ChatGPT export arrived by email overnight; she drops the zip in. Friday shows "what ChatGPT knew about you" and she corrects two lines. Then the special clearance card, once: local reads of the four sources she named, local writes, overnight hours, nothing outward, four weeks, revocable. She approves it by voice. Friday offers one governed action: draft the reply to the editor. Preview, permission class, approval, execution, verification, receipt, read aloud once.
- **Day 2.** One goal for the month, written together, created when she approves the wording.
- **Day 4.** "You read mail at 07:40 most days. Want a two-minute briefing then?" She says yes.
- **Day 6.** The first grant: file newsletters and receipts, 30 days, 400 uses. The card shows the two rules.
- **Day 7.** The weekly review, ten minutes. The Doctor report is on the PC; Friday asks once whether Maya wants to email it herself. "Never ask again" is remembered.
- **Day 10.** A Friday Bundle to her external drive, then a restore rehearsal that passes in 41 s. Backups now read "tested Tuesday", not "protected".
- **Day 12.** "Want me to look at which tools you lean on?" She says yes. The ranked list appears first. In the next weekly review, one card: her note-taking app, an open alternative with no telemetry, what she gains, what she loses, about two hours to migrate, her notes importable. She dismisses a second suggestion forever.
- **Day 16.** Her phone, through her own Tailscale account. Approval cards work from the couch.
- **Day 19.** Her partner gets a principal with a lock screen. The kids get minor mode, with the guardian rules read aloud to both of them.
- **Day 3.** The daily question set, five minutes: two merges, one "which project is still active". The Home card reads 31% complete, 2,140 facts, finish about 14 October.
- **Day 21.** Friday's first weekly look change is announced, with undo. The sigil is unchanged.
- **Day 30.** The thirty-day review: 2,140 facts, 312 confirmed by her, 61 receipts, 41 cloud calls with every scrubbed line listed, nothing to FutureSpeak, and a re-run link beside every setup step.

## 11. Build plan

| Phase | Scope | Effort | Depends on |
|---|---|---|---|
| P0 Doctor and manifests | signed `release-manifest.json`, install ledger, preflight with the four buckets and vendor-neutral GPU read, `install-receipt.json` | 6 to 8 days | self-patching-installer Phase 0 |
| P1 Installer 6.0 | single signed bootstrapper, embedded runtime and content-addressed wheels, version directories, delta, rollback with health check, repair, uninstall with export and revocation, Sandbox and VM matrix, winget listing, Bonsai2 tier lookup and the published floor | 15 to 20 days plus certificate lead time |
| P1s App shell | Electron shell over the Python server: window, tray, hotkey, notifications, deep links, permission handler, telemetry hardening, packet-capture egress test in CI; adblock-rust in the fetcher | 11 to 17 days | P0, decision D1, `bonsai2-tiers.json` |
| P2 Birth | voice-first setup on today's `setup_chat`, naming with the address card at setup, seed and sigil at birth, Windows Hello wrap, recovery kit, restore drill, five routing profiles, four extra persona axes, quiet hours and autonomy profiles, setup receipt | 15 to 20 days | avatar genome §3 |
| P3 Connect | card contract and single Health verdict, outcome groups, read and write split, Takeout / mbox / PST / bookmarks / chat importers with scan and staging, IMAP app-password connector, BYO Google walkthrough, expiry countdown | 20 to 25 days | connector-ecosystem phases 1 and 2 |
| P4 First major task | staging store, Laya pre-classification, local extraction with commitment and role types, entity resolution with evidence, timeline store, sensitive zoning, cited page drafting, idle scheduler with measured estimates, growth events and processing states, review queue with voice tools, first synthesis, tier scaling and the quality checks of §7.8 | 30 to 40 days | P3, processing states PS1 to PS5, `bonsai2-tiers.json` |
| P4a Getting to know you | the durable goal on goals-and-receipts, the `standing_local` clearance grant with expiry and Ledger revocation, the question inbox with daily ranking, the surface and Home card, resource yielding through the arbiter | 18 to 25 days | P4, goals-and-delivery-receipts, standing grants, idle-window policy, per-goal token ceilings, seat persistence |
| P3a AI histories and borrowed servers | provider export guides as a data file, importers for ChatGPT, Claude.ai, Gemini Takeout, Grok, Copilot CSV, Perplexity, Meta; local Claude Code, Codex, Cursor and Gemini CLI log readers with secret dropping; MCP-configuration discovery and re-authenticated adoption; "what the other AIs knew about you" pages | 15 to 20 days | P3 |
| P5 Apprenticeship | Home progress strip, first governed action flow, first goal UI on `goals.py`, pattern-based routine offers, per-workflow grants UI, weekly review with the Doctor ask, Friday Bundle backups and restore rehearsal, device pairing, principals and minor mode | 20 to 25 days | P4; multi-user is v6 P9 |
| P5b Own your tools | consent card, local usage signals, public-catalog fetch and local solver with licence and telemetry facts, three-path suggestion cards with gain, loss, migration and import path, per-app dismissal, salon template seeding, the federation manifest seam (not the federation) | 12 to 15 days | P4, model-soup solver, salon templates |
| P6 Control room and privacy map | sections, four lines per setting, voice-to-diff, modes, "why did you do that", memory browser, privacy map and ledger UI over the existing routes | 20 to 25 days | P2 |
| P7 Mac and Friday Linux | notarised DMG, keychain, Touch ID, first-boot flow on the image | later | P1 |

### 11.1 Phase 1 of the first install: the pinned scope

The owner made the first install run the priority. Phase 1 is the smallest thing that is the whole experience once, end to end, on a clean machine. In and out are fixed here so the build session has one target.

| # | In Phase 1 | Done when |
|---|---|---|
| 1 | **Installer**: one signed file, per-user, embedded runtime, model-free preflight with the four buckets, install receipt, repair, uninstall with export | signed, under 250 MB, under two minutes on the clean-machine matrix; NS-34.4-1, -2, -10 green |
| 2 | **Shell**: the Electron window of §3.7 with hotkey, notifications, deep links, single instance, permission handler, egress CI test | §3.7.4 passes |
| 3 | **Bonsai2 selection**: tier lookup from `bonsai2-tiers.json`, the published floor, download in the app with pause and checksum, no other model named | preflight prints the tier row; below-floor machines are told before install |
| 4 | **Birth and naming**: voice-first conversation, seed and sigil at birth, the address card with its one explained prompt, five routing profiles, persona, quiet hours, setup receipt | every step keyboard-complete and captioned; the setup receipt written |
| 5 | **Vault**: Windows Hello or passphrase, the 24-word kit, the restore drill, honest "no recovery" | the drill gates "protected"; the passphrase invariant of §3.4 tested |
| 6 | **Connect your life**: the card contract, outcome groups, read and write split, the Gmail truth card, Takeout and mbox import with scan and staging, IMAP with app password, the AI-history imports for ChatGPT, Claude.ai and Gemini plus the local Claude Code and Codex logs (§6.6), MCP-configuration borrowing from installed AI apps (§6.5), the opt-in framing of §6.7 on every card | two sources connectable with no OAuth at all; nothing from an import is durable before review; no token or secret from another app is ever read |
| 7 | **First knowledge-graph pass**: staging, Laya pre-classification, local Bonsai2 extraction with provenance, the shallow-first pass of §7.8, hard-key merges plus queued "same person?" questions, sensitive zoning, the first cut of the Getting to know you surface (source map, growth, question inbox, coverage meter, pause and delete-with-receipt) | facts carry citations; Tier 3 never reaches a cloud model; the galaxy grows on `node_ignited`; the meter reads from real counters |
| 8 | **First value**: "What I see so far" with at most five items and the seven marks, the four-minute voice review queue | reached within fifteen minutes of the second source on the local path, measured on the reference machine and recorded locally |

**Out of Phase 1, on purpose:** the durable goal and the special clearance of §7A (Phase 2, right after, because they depend on goals-and-receipts and standing grants; Phase 1's pass runs in ordinary idle windows under the normal ceilings), delta updates and rollback (Phase 1b), the Model Soup advisor (offered as a card that says "coming"), Own your tools, the first governed action, goals, routines, grants, backups, devices, principals, the control room and privacy map beyond today's Settings, Mac and Friday Linux. Phase 1 ships behind the owner's release conversation (A7).

About 130 to 165 engineer-days for Windows, or roughly three months with the program's parallel pieces. Each phase ends with its rows of NS-34.4 green on the clean-machine matrix.

## 12. Decisions: what stays with the owner, and what every user decides

The owner's principle (rule 9): the user is the North Star; all of this is presented at setup; the user decides. Only two things stay with the owner, because they happen before any user exists.

### 12.1 The owner's two decisions, both money

1. **D1: whether FutureSpeak pays for code signing.** Prices verified 2026-09-30. **Recommended: Microsoft Artifact Signing** (formerly Trusted Signing), $9.99 a month for up to 5,000 signatures, no hardware token, identity validation of the organisation required, integrates with GitHub Actions; Microsoft's own developer page calls it "the recommended code signing service for non-Store distribution". Alternatives: an OV certificate at about $129 (SSL.com) to $220 (Sectigo) to $439 (DigiCert) a year plus a required hardware token ($120 to $379, sometimes included), or an EV certificate from about $279 a year. **What each does for SmartScreen, per Microsoft (page dated 2026-05):** nothing outside the Store avoids the first-download warning; OV, EV and Artifact Signing all build reputation the same way, over "several weeks and hundreds of clean installs"; "EV certificates no longer bypass SmartScreen... Paying a premium for EV solely to avoid SmartScreen warnings is no longer justified"; only Microsoft Store distribution never warns. Apple's $99 a year comes with the Mac build.
2. **D2 (company half): whether FutureSpeak pays for Google's verification and assessment so that one-tap Gmail can exist at all.** Facts verified 2026-09-30. Sensitive scopes (calendar): about 10 business days, no fee. Restricted scopes (Gmail read): about 6 weeks plus an annual CASA assessment by an authorised lab; Google charges nothing, the lab does: TAC Security, Google's preferred partner, $540 per app per year for Tier 2 at the Google-negotiated rate; other labs $800 to $1,500; Leviathan $3,000 to $6,000 by turnaround. Without it, one-tap Gmail does not exist and the card offers the three free routes only. **Recommended:** start the free calendar verification and the $540 Tier 2 assessment now.

### 12.2 What every user decides at setup

These were D2 (the user half), D3, D4 and D5, plus the other product-intent defaults the audit found. Each is presented in plain words with what it means, what it costs and what changes later; the recommended option is labelled and never pre-selected; each is changeable later in the control room with a receipt; each is voice-callable under the contract.

| Choice | Options (recommended in bold, still not pre-selected) | Where asked | Changed later in |
|---|---|---|---|
| Vault lock | **Windows Hello** (where enrolled) · passphrase · no vault for now | birth, lock and recovery | Permissions and rules → Vault |
| Recovery | **print or save the 24-word kit and run the drill** · skip, recorded as "no recovery" | birth, lock and recovery | same |
| Where Friday thinks | on this device only · **local preferred** (at or above the floor) · ask each time · **cloud preferred** (below the floor) · custom | birth, thinking and data | Models and seats |
| Where the picture of you is built | **on this PC only** · cloud per source when I say so · cloud by default for extraction | birth, thinking and data | Models and seats |
| How the wiki is kept | **encrypt the whole wiki** (when a lock exists) · readable Markdown with encrypted sections | birth, thinking and data | Backups → Storage |
| Her voice | **local** · cloud (PII-free summary only) | birth, how she talks | Models and seats → Voice |
| How to talk about her | assistant · colleague · family framing (no recommendation) | birth, how she talks | Getting to know you |
| Proactivity | **quiet** · helpful · proactive | birth, how loud she is | Routines |
| Autonomy | **ask before every action** · draft only · observe only | birth, how loud she is | Permissions and rules |
| Update checks | **check for updates** (signed manifest only; installing is always a card) · never check | birth, how loud she is | Health |
| Look evolution | **weekly, announced, with undo** · approve each step · off | birth, how loud she is | Getting to know you |
| Her address | **agent.<name>** with the one explained prompt · stay on localhost | birth, name and address | Advanced → Local address |
| Gmail route | **Takeout import** today, **one-tap** once verified · IMAP with app password · bring-your-own Google client | connect, the Gmail card | Connections |
| Each source, and its scopes | connect · not now · never; per-source folders, years, people, third-party facts | connect and Getting to know you | Connections |
| Archive after import | **delete** · keep | the scan card | Connections |
| Vault-zone items | **flag and hold, don't read** · read locally, keep in the vault | Getting to know you → scopes | same |
| The special clearance | approve (scope, hours, weeks shown) · not now | after first value | the Ledger |
| Model Soup | accept the stack, item by item · not now | end of birth | Models and seats |
| Own your tools | opt in · not now · never | day 10 or later | Getting to know you |

What is **not** a choice, and why: telemetry (none, ever, rule 1); the local model named at first setup (Bonsai2 only, the owner's binding rule, §3.6; any model afterwards is the person's choice); the approval gate on outward actions (rule 8); the hologram as her identity (A5; it can be switched off, which is the person's accessibility choice, but not replaced).

## 13. Acceptance

The bar is the best shipped onboarding in each area, plus the brand check: Apple's Setup Assistant for pacing and honesty about hardware, 1Password for the recovery kit and drill, Superhuman for the concierge tone and the four-minute review habit, plus WCAG 2.2 AA on every flat path, the north star's NS-34.4 checklist green, and the hologram present and still except on real events from the installer's first pane to the thirty-day review.

## Appendix A. The expert panel (STORM)

- **Apple Setup Assistant designer:** one question per screen, read the machine before asking, never preselect a privacy choice, let the person skip and return. Landed in §3.2, §4.4, §5.2.
- **1Password onboarding lead:** the recovery kit is shown once, printable, with the identity mark; the drill happens before you say "protected"; never claim recoverability you cannot keep. Landed in §4.3.
- **Superhuman concierge lead:** first value inside the first session with the person's own data; a daily habit measured in minutes; the coach names the next step, once. Landed in §7.6, §7.7, §8.
- **Windows packaging and signing engineer:** one signed file with a stable identity; per-user version directories, delta and rollback; a hashed manifest for repair; MSIX set aside for the loopback and certificate needs. Landed in §3.
- **Privacy lawyer:** scopes on the card, read and write apart, revocation with a receipt, no inferred traits about third parties, the Gmail verification facts stated rather than hidden. Landed in §6, §7.4.
- **Knowledge-graph engineer:** staging before durability, provenance on every fact, hard-key merges only, evidence-backed questions for the rest, a real timeline store. Landed in §7.
- **Accessibility advocate:** captions and transcripts for every spoken line, keyboard-complete cards, focus management, reduced motion, 3D off from step one, speech-rate and repetition controls. Landed in §5.1, §9.9.
- **A first-time non-technical journalist:** "Tell me what you can see and where it goes, in my words; don't make me choose things I don't understand; let me stop." Landed in the card contract, the four lines, and the walkthrough.

## Appendix B. North-star rows this spec closes

NS-8.1-1, -3, -8, -12, -13, -14; NS-8.2-1, -2; NS-8.3-1, -2; NS-8.4-2, -3, -4; NS-8.5-2, -3; NS-8.6-1, -2, -3; NS-8.7-1, -2; NS-8.8-2; NS-8.9-1, -2, -3; NS-8.10-1, -2, -3; NS-8.11-1; NS-8.12-2, -3; NS-8.13-1, -2, -3; NS-8.14-1, -2; NS-8.15-1; NS-9.1-1, -2; NS-9.2-1, -2; NS-9.3-1; NS-9.4-1; NS-9.5-1; NS-9.6-1; NS-13.7-1, -2; NS-15.5-1; NS-15.6-1; NS-15.7-1..3; NS-15.8-3; NS-15.11-1; NS-23.1-4; NS-23.2-2; NS-25.1-1; NS-25.7; NS-25.11; NS-26.22-1; NS-27.2-1; NS-27.4-1, -3; NS-27.6-1; NS-27.10-1; NS-28.1-2; NS-28.4-2; NS-28.6-1; NS-28.7-1; NS-33.1-2, -3; NS-33.3-1, -2; NS-34.3-2, -4, -5, -7; NS-34.4-1..9; NS-34.9-7; NS-34.14-1, -2; NS-34.18.

## Appendix C. Voice tools this spec introduces

Each follows the five steps of the voice contract: `setup_step(step)`, `review_answer(kind, item, answer)`, `review_why(item)`, `control_room_change(sentence)` (returns the diff; applying is a second, spoken yes), `set_mode(mode)`, `pause_learning()`, `resume_learning()`, `what_left_today()`, `model_soup()` (returns the recommended stack; installing is a card per item), `own_your_tools()` (returns the ranked tools and the next suggestion; nothing is installed or built without its own card), `picture_status()` (the coverage meter, the next question count and the finish estimate, spoken), `answer_question(id, answer)`, `pause_learning(source?)`, `delete_source(source)` (raises the deletion card and returns the receipt), `import_ai_history(provider)` (starts the guided export steps aloud), `setup_choice(group, option)` (makes or changes any §12.2 choice by voice, reading back meaning, cost and what changes; applying is a second spoken yes and a receipt), `use_recommended(group)` (fills a group's unanswered choices with the labelled recommendations and reads the list back before continuing). None reaches outside the machine; all are ring-1 candidates with a comment saying so. `control_room_change` never applies without the second yes.

## Appendix D. Prototype index

`docs/design/prototypes/onboarding/`: `index.html` (map), `installer.html`, `birth.html`, `connect.html`, `knowledge.html`, `review.html`, `control-room.html`, `privacy-map.html`, `friday.css`, `friday.js`. Open any file in a browser; no server needed. Every number on them is illustrative.
