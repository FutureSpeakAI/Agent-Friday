# Changelog — Agent Friday

All notable changes to this project are documented here.  
Format: [Semantic Versioning](https://semver.org) · Date: YYYY-MM-DD

> **Note:** Pre-1.0 releases have been archived. Current version: **5.9.0**

---

## [5.9.0] - 2026-08-31

The OS-mode sequence for Friday Linux (the sealed, portable OS image project):
five PRs, each behind `FRIDAY_OS_MODE` so Windows behavior is unchanged, each
merged only after its own CI matrix (windows-latest + ubuntu-latest, both
Python versions) passed green.

### Added

- **`core/os_mode.py`**, a single `is_os_mode()` query point every other
  change below reads from, instead of scattering `os.environ.get("FRIDAY_OS_MODE")`
  checks around the codebase. Six behaviors now gate on it: the system tray
  is skipped, logging routes to stderr in a journald-friendly format,
  computer-control tools (mouse/keyboard/screenshot) and the clipboard tool
  report themselves honestly unavailable with "no desktop to control in
  kiosk mode" instead of failing opaquely, `_open_app` refuses honestly
  instead of attempting a Windows-only launch, and voice-asset lookup checks
  a baked-in asset directory before ever reaching out to the network.
- **`/api/health` gains a documented boot-critical health contract**
  (`health_schema_version`, `boot_critical_ok`, `boot_status`, `subsystems`,
  `deployment`), additive to the existing `status`/`inference` fields these
  responses already carried. Four subsystems are boot-critical (config,
  credential store, memory database, HTTP serving); model seats, voice, and
  cloud providers are explicitly not — a kiosk with no cloud key configured
  yet is a normal state, not a boot failure. Every boot-critical check has a
  paired test that actually forces it to fail and asserts the response
  reflects that, not just a check that a function returns something.
  `friday health --exit-code` exposes the same contract as a process exit
  code, for an OS boot-health script to consume.
- **`agent_friday.paths`**: `friday_home()`, `models_dir()`, `runtime_dir()`,
  `voice_assets_dir()` — the previously-scattered `Path.home() / ".friday"`
  literal, unified into one place across the 23 files that computed it
  inline, with `FRIDAY_HOME` now an honored override everywhere those files
  touch. (Deliberately not nested under `core/` — `agent_friday/core/__init__.py`
  has real import-time side effects, including a legacy-directory migration
  that touches the real home directory regardless of `FRIDAY_HOME`, and
  nesting there would have dragged that into all 23 previously side-effect-free
  call sites.)
- **Career-pipeline routes now work in an installed package, not just a
  source checkout.** `data/job_tracker_schema.py` and
  `skills/{application_engine,job_scanner}` move into the package proper as
  `agent_friday.seed.*`, with a first-run step copying the bundled skills
  into the user's own skills folder. This was a real, measured defect, not
  a theoretical one: a wheel built from the prior release 404's four of six
  job-pipeline endpoints because they depended on a repo-root layout no
  `pip install` ever has.

### Fixed

- **Credential storage now fails closed under `FRIDAY_OS_MODE`, instead of
  silently writing plaintext or silently reporting success with nothing
  written.** With no vault key and no DPAPI available (DPAPI is
  Windows-only), `credential_store.protect()` and `vault_passphrase.store()`
  now raise, and nothing touches disk. Windows-default behavior (OS mode
  off) is unchanged — same warning, same plaintext fallthrough as before.
  Also corrected `KNOWN_ISSUES.md` §7's stale claim that `FRIDAY_SECRET_KEY`
  ships as a known default string — it doesn't, and never has; it's a
  persisted random secret, though that persisted copy currently ignores
  `FRIDAY_HOME` (a separate, smaller, documented gap).

### Known gaps carried forward, not fixed here

- On Linux, `vault_passphrase.store()`'s fail-closed path has nowhere
  durable to succeed at persisting a passphrase yet: `keyring` isn't in
  this project's Linux deployment extras, and no Secret Service provider is
  assumed present either. Documented in `KNOWN_ISSUES.md` §7 with the three
  concrete options for whoever picks this up next.

---

## [5.8.0] - 2026-08-30

### Added

- **Friday now tells you when there is a newer version, once a week.** Windows
  desktop only — Friday Linux updates atomically through bootc and is not
  affected.

  Once a week, Friday asks GitHub whether a newer stable release of Agent
  Friday has been published. If there is one, you get a dismissible
  notification with a link to it. That is the whole feature. Friday never
  downloads anything and never installs anything; updating remains what it has
  always been — download the zip, unzip it, run `Install Agent Friday.cmd`.

  **It sends nothing about you.** This is one unauthenticated `GET` of a public
  GitHub URL, the same request anyone's browser makes by visiting the releases
  page. No install identifier, no usage data, no version string in a query
  parameter, and no `User-Agent` beyond what an ordinary HTTP client sends by
  existing. `tests/unit/test_update_check.py` reconstructs the entire outbound
  request — method, URL, query string, headers, body — and fails if it contains
  the hostname, the username, the home directory, this install's version, or
  anything shaped like an identifier. The test directly below it feeds that
  same scanner a request that *does* leak and fails if the scanner shrugs.

  **If the network is down, you will not hear about it.** Offline, DNS failure,
  GitHub rate-limiting, GitHub having a bad day: all logged, none surfaced. A
  check that nags because the wifi is off is worse than no check.

  **You will not be told twice about the same release.** One notification per
  version, dismissible, and it does not come back until there is something
  newer.

  Switch it off in **Settings → About → Updates**, which also shows the version
  you are running, when the last check happened, and a **Check now** button.

- **`friday status` now reports which version you are actually running** — and
  says so if that disagrees with `install-manifest.json`.

  The version is read from `app\pyproject.toml`, the same file the installer
  reads back in `Get-InstalledAppVersion`. It is deliberately **not** read from
  the manifest, because before 5.6.5 the manifest could be wrong: `app.copy`
  ran its verify before its action, so 5.6.0–5.6.4 upgraded 0 of 489 files
  while writing the new version number down anyway. Any install that went
  through one of those upgrades has been reporting a version it is not running.

  If the disk and the manifest disagree, `friday status` and Settings → About
  both say so in plain language and tell you to re-run the latest installer.
  An update check that trusted the manifest would tell those users they were
  current, which is worse than having no check at all.

### Changed

- **One version-detection implementation, not three.** `cli.py` had a regex
  over `pyproject.toml`, `/api/health` had a separate `tomllib` parse of the
  same file, and the two had different fallbacks — `/api/health` fell back to
  the literal string `"5.0.0"`, so an install that could not read its own
  version reported one it had invented. Both now delegate to
  `services/app_version.py`, unknown stays unknown, and
  `tests/unit/test_one_version_source.py` fails if a fourth reader appears.

### A decision made on your behalf — please overrule it if you disagree

Stephen asked for the weekly check to be **on by default for new
installations**. It is.

Existing installs upgrading into this release **also get it switched on**,
because the scheduler seeds newly-registered built-in tasks onto installs that
predate them. Nobody asked for that, and quietly switching on a
network-touching behaviour for someone who already installed is exactly the
kind of thing this product should not do. It is called out here rather than
left to be discovered, the toggle is one click away in Settings → About, and
the check sends nothing regardless.

If the right answer is "off for existing installs, on only for fresh ones",
that is a one-line change to the seeding call — say the word.

---

## [5.6.6] — 2026-08-29

**If you upgraded using 5.6.5, your vault passphrase may have been deleted. Read
the first item. If you have not upgraded yet, take 5.6.6 and skip 5.6.5.**

### Fixed

- **An in-place upgrade could destroy the vault passphrase, silently.** The
  passphrase is written to `start.bat` inside Friday's app folder and — unless
  `friday vault-setup` was run — exists nowhere else; `setup_wizard.py`
  deliberately refuses to put it in a settings file. `app.copy` deletes the app
  folder wholesale, and `start.bat` is excluded from the shipped payload, so it
  does not come back.

  Before 5.6.5 this was latent: `app.copy`'s verify passed before its action on
  every upgrade, so the delete never ran and the file survived by accident.
  5.6.5 correctly fixed that short-circuit and thereby made this reachable for
  the first time. From 5.6.5, every in-place upgrade deleted it.

  `~/.friday/vault` is AES-256-GCM under an Argon2id key derived from that
  passphrase. The vault is never deleted; only the key is. The ciphertext stays
  on disk, permanently unreadable.

  Measured on the two published zips. Published 5.6.3 installed, a passphrase
  minted through the wizard's own `_write_start_bat`, a note encrypted under it,
  then upgraded:

  | | → 5.6.5 | → 5.6.6 |
  |---|---|---|
  | installer exit | 0 | 0 |
  | code updated | yes | yes |
  | `start.bat` survived | **no** | yes |
  | vault decrypts | **no** | yes |

  `app.copy` now moves the payload-excluded, credential-bearing files aside
  before the delete and restores them after the copy, in a `finally` so a failed
  copy cannot cost the passphrase. The held copy is deleted immediately on
  restore rather than being left as a second plaintext credential on disk. The
  preserved set is exactly `Get-PayloadExcludes`' secret-bearing list, which is
  what makes restoring safe: the payload can never contain those names, so
  putting them back cannot overwrite a shipped file.

  **There is no recovery for data already affected.** See KNOWN_ISSUES §0.

- **The setup wizard could mint a new passphrase over an existing vault.**
  `step_vault_password` opened with "Generate a random passphrase for me?"
  defaulting to Yes and never checked whether a vault existed. The installer
  runs the wizard on every run including every upgrade, so pressing Enter
  generated a fresh passphrase over data encrypted under the old one. Measured
  against 5.6.5 with Enter-only answers: returned a new random passphrase, and
  the vault then failed to decrypt with `IntegrityError`.

  The step now reads the passphrase from the environment, `start.bat`, or the OS
  keychain — `_load_config()` can never supply it, which is why `existing`
  arrived empty on every run — and verifies it against real ciphertext. An
  existing vault with a recoverable passphrase keeps it. An existing vault
  without one **stops and explains**; the default is to leave it unset, and
  starting a new vault requires typing `abandon`. Fresh installs are unchanged.

- **Add/Remove Programs kept showing the old version after an upgrade.**
  `Register-Uninstaller` writes `DisplayVersion`; `Test-UninstallerRegistered`
  never read it back, so the previous install's entry satisfied the verify, the
  action was skipped, and Windows reported the old version indefinitely. Same
  defect shape as `app.copy`'s, on the surface a user checks to learn what they
  are running. The check is now version-aware.

- **`install-manifest.json` recorded intentions rather than outcomes.** Because
  `Invoke-Step` skips steps whose verify already passes, things the manifest
  claimed had not happened. Three now measured after the fact: `version` is read
  back from the installed `pyproject.toml` (with `installer_version` beside it —
  a disagreement means the copy did not take); `shortcuts` is enumerated from
  what exists, so the uninstaller stops orphaning four shortcuts after an
  upgrade; `autostart_enabled` reflects the measured state. Answering "No" to
  autostart on a machine that already had it enabled now actually disables it,
  instead of leaving it on and recording `false`. `schema_version` → 2.

### Known, unchanged, and Stephen's to decide

- The vault passphrase still lives inside the app directory — the property that
  made the above possible. 5.6.6 preserves the file; it does not relocate the
  credential. Options and migration consequences:
  `docs/design/vault-passphrase-location.md`.

---

## [5.6.5] — 2026-08-29

**If you have ever upgraded Agent Friday in place, read the first item. Your
install may have been reporting a version it was not running.**

### Fixed

- **In-place upgrades never delivered any code, and said they had.** Re-running
  a newer installer over an existing install replaced nothing at all. The
  installer printed "Friday is installed", wrote the new version number into
  `install-manifest.json`, and exited 0, while every file of Friday's own code
  on disk remained the previous release.

  `Invoke-Step` runs a step's `-Verify` block *before* its action and skips the
  action when verify passes. The `app.copy` verify asked only whether four
  files — `cli.py`, `server.py`, `setup_wizard.py`, `index.html` — *existed*.
  Any previous install satisfies that, so the copy short-circuited every time.
  The one line in the log that said so was `app.copy : verify passed before
  action - already in place, nothing to do.`

  Measured on a real 5.6.3 → 5.6.4 upgrade from the two published zips:
  **0 of 489 files updated**. `install-manifest.json` read `5.6.4` beside a
  `pyproject.toml` that still read `5.6.3`. `connector_secrets.py`, the only
  file new in 5.6.4, was absent — so `GET /api/mcp/servers` on that "5.6.4"
  install still returned connector credentials to the browser in plaintext,
  which is the precise exposure 5.6.4 announced it had closed. Every other
  5.6.4 fix was equally absent.

  **This defect shipped with the installer itself.** It is present in 5.6.0
  through 5.6.4 identically, so no in-place upgrade has ever delivered code.
  Anyone whose install came from an upgrade rather than a fresh install has
  been running the version they first installed.

  The `app.copy` verify now also requires the installed `pyproject.toml`
  version to equal the version being installed. An older install reports an
  older version and is replaced; an install too old to have a readable
  `pyproject.toml` reports nothing and is also replaced — so this repairs an
  upgrade from *any* prior release, not only from 5.6.4. The fast path
  survives for the case it was for: re-running the *same* installer, where
  skipping is correct.

  **What to do.** Download the 5.6.5 zip and run it over your existing
  install. It will replace the app files this time and keep everything under
  `~/.friday` — notes, settings, conversations, connected accounts. Then check
  `friday status`. If you had connected Airtable, Gmail, GitHub or any other
  credentialed MCP server on an install that only *believed* it was 5.6.4,
  treat those tokens as having been readable and rotate them.

- **`friday update` pointed at a repository that does not exist, from the one
  place that could not use it.** The packaged install deliberately ships no
  `.git`, so every installer user hits the "not a git repository" branch —
  and that branch printed `github.com/FutureSpeakAI/friday-desktop`, which
  returns 404. The repository is `Agent-Friday`. The command now prints the
  correct releases URL, shows the installed version beside the running one,
  gives the three steps that actually update a packaged install, and says
  plainly that an in-place upgrade before 5.6.5 may not have applied.

- **The uninstaller addressed the author by name.** Its closing line read
  `For Stephen: …LAST-UNINSTALL-REPORT.md` on every machine it ran on. It now
  reads `Details:`.

### Corrections to the 5.6.4 release notes

Everything in this section is a documentation fix. No behaviour changed.

- **The known-issue test count was wrong: seven claimed, sixteen measured.**
  A full-suite run on a clean `v5.6.4` checkout fails 16 tests, not 7. The
  5.6.4 notes named five `test_residency_arbiter`, `test_gate_harness_integrity`
  and `test_local_image`. Of those, the five `test_residency_arbiter` and
  `test_local_image` do fail; **`test_gate_harness_integrity` passes** and
  should not have been listed. Unnamed and also failing: five
  `test_routing_resolver`, `test_model_router::test_local_preferred_uses_local_when_available`,
  `test_model_plan::test_the_indexer_falls_back_to_a_tool_capable_model`,
  `test_nemo_voice::test_nemo_models_ready_false_when_uncached`, and two
  `test_egress_adversarial::test_tier2_keyword_batch` cases.

  The distinction the old note missed is **isolation versus whole-suite**.
  Run their own files alone, only the two `test_egress_adversarial` cases
  fail, and they fail identically at `v5.6.3` and `v5.6.4` — those two are
  genuinely deterministic and pre-existing. The other thirteen pass in
  isolation and fail only in a whole-suite run, so they are order- and
  environment-dependent rather than attributable to a release. `v5.6.3` fails
  the same *number* (16) with a partly different *membership*: it fails four
  that `v5.6.4` does not (`test_ambient_predictive_routes`, two
  `test_google_accounts`, `test_inverted_provider_default`) and passes the
  five `test_routing_resolver` that `v5.6.4` fails. All nine of those pass in
  isolation at both tags. The count is stable; which tests make it up is not.

  These remain test defects rather than product defects, and that part of the
  old note stands.

- **The `cloud_only` guarantee was stated too strongly.** 5.6.4 said "a *local*
  model chosen under `cloud_only` is still refused." It is not. `_is_local_choice`
  guards a `task_overrides` entry and a bound seat, but not the model handed in
  as the turn's chosen model, which is the path the picker uses — so picking a
  local model while in `cloud_only` runs it on-device.

  That behaviour is deliberate, not a bug: `routing/model_router.py` has said
  since before this release that "the model picker is authoritative … the user
  explicitly chose a local brain", and `test_registry_local_claude_name_never_cloud`
  asserts exactly it. It also fails safe — on-device is the more contained
  destination, not the leakier one. So the sentence is corrected rather than
  the code.

  **What `cloud_only` actually promises:** Friday will not choose a local model
  on her own, and will not fall back to one when a cloud call fails. It does
  not override a local model you pick yourself; that runs on your machine. If
  you want the mode to be the last word regardless of the picker, that is a
  behaviour change and is not in this release.

- **The pricing fix was filed under Performance and understated.** It belongs
  under Fixed, because it is a correctness defect with a bill attached: Opus 5
  was metered at $15/$75 per MTok against a real $5/$25, so **the model Friday
  defaults to was charged at three times its real price**, and Fable 5 at
  $3/$15 against a real $10/$50 was charged as the cheapest model in the
  lineup when it is the most expensive. Both errors flowed into the Cost &
  Usage panel, the daily and monthly budget tripwires, and the per-schedule
  breakdown. Separately, `claude-haiku-4-5` priced at exactly $0 because the
  table was keyed only on the dated id — a cloud call that read as free. See
  the 5.6.4 entry for the full account.

- **"A partial settings write no longer resets its siblings" was too broad.**
  The deep merge covers two named blocks, `capability_routing` and
  `model_routing`, out of the 20 top-level blocks a live `settings.json`
  carries. Every other block — `cost_budget`, `tool_hooks`, `qa_gates`,
  `context_pruning` and the rest — is still replaced wholesale by a partial
  write. The two that were fixed are the two with a known blast radius; the
  general case is open.

- **A privacy fix shipped in 5.6.4 undocumented.** The sensitivity classifier's
  Layer 4 POSTed a hardcoded `gemma4:latest` to Ollama, a tag that was never
  installed, so every call 404'd and the layer returned 0 — which is also its
  value for "no opinion", so a dead layer and an unopinionated one were
  indistinguishable and nothing ever surfaced it. Resolving the name against
  the merged local registry was not enough either, because that registry
  includes models served by llama-server that Ollama cannot answer for; it now
  intersects the two and takes the largest model Ollama actually serves.

---

## [5.6.4] — 2026-08-29

> **Four statements in this entry were corrected in 5.6.5** — the known-issue
> test count (seven claimed, sixteen measured), the `cloud_only` guarantee, the
> filing and severity of the pricing fix, and the breadth of the partial-write
> fix. The original text is kept below as published; see **Corrections to the
> 5.6.4 release notes** in 5.6.5 for what each one should have said.
>
> **Also: if you reached 5.6.4 by upgrading in place, you did not receive any
> of it.** Installers before 5.6.5 skipped the file copy entirely. See 5.6.5.

Six defects found by watching a second install behave, plus the cost work that
matters the moment you stop running models locally. Everything here was live in
5.6.3.

### Fixed

- **`cloud_only` no longer throws away the model you picked.** `_route_basic`
  returned the default cloud model the instant the mode was `cloud_only` —
  above the `task_overrides` lookup and above the seat lookup, neither of which
  ever ran. `cloud_only` is the factory default, so on a stock install the
  model a user chose was written to disk, read back, rendered in the picker,
  and discarded at dispatch; what actually answered was
  `settings.orchestrator_model`. The mode now classifies the turn, honours a
  task override, then the bound seat, then the default — the same precedence
  every other mode already used. The guarantee is unchanged: a *local* model
  chosen under `cloud_only` is still refused, and `_is_local_choice` checks all
  three ways a model can be local, including a custom Ollama tag that the name
  heuristic reads as cloud.

  > **Corrected in 5.6.5.** That last sentence is wrong. `_is_local_choice`
  > guards a task override and a bound seat, but not the turn's chosen model —
  > which is the path the picker uses. A local model picked under `cloud_only`
  > runs on-device. The behaviour is deliberate and fails safe; the sentence
  > was not. See 5.6.5 for what the mode actually promises.

  This is a different site from the `cloud_only` work in 5.6.3, which filtered
  local legs out of the *fallback ladder*. That one governs where a failed call
  retries; this one governs where the first call goes.

- **The Local-Only Mode switch was inert in both directions, and displayed the
  opposite of its own state.** The server gates on
  `model_routing.vault_local_only` and defaults it to ON when absent. The
  toggle read a bare *top-level* key of the same name, which does not exist —
  so `!!undefined` rendered the switch OFF on every install whose settings had
  never been hand-edited, while the gate was in fact ON. Having shown the wrong
  state it then wrote that same top-level key, which nothing reads, leaving a
  decoy in `settings.json` that looks authoritative. It now reads and writes
  the key the server actually gates on.

- **A partial settings write no longer resets its siblings.** Wiring the
  toggle above exposed the more dangerous problem underneath: `_save_settings`
  deep-merged exactly one block, `capability_routing`. Every other key in a
  delta replaced its value wholesale, so
  `{"model_routing": {"vault_local_only": false}}` would have dropped `mode`,
  `vault_cloud_fallback` and `ollama_url` — a privacy switch silently moving
  every future turn to a different provider. `model_routing` now deep-merges
  too, via a named list rather than a second hardcoded key.

  > **Narrower than the heading suggests; corrected in 5.6.5.** The named list
  > holds two blocks out of the 20 a live `settings.json` carries. Every other
  > block — `cost_budget`, `tool_hooks`, `qa_gates`, `context_pruning` and the
  > rest — is still replaced wholesale by a partial write.

### Security

- **Connector credentials are no longer stored in a readable file.** An
  Airtable personal access token and a Gmail app password went into
  `~/.friday/mcp_servers.json` as plaintext when you connected those services.
  Two exposures, not one: `GET /api/mcp/servers` returned the config verbatim,
  so every connector token was also handed to the browser on request. Values
  are now ciphertext from the moment they are written until the child process
  is spawned — `_load_mcp_servers` deliberately returns them still encrypted,
  and only `MCPServerProcess._spawn` decrypts, into the child's environment.

  Existing installs are migrated, not just protected going forward: a plaintext
  value is read correctly on first load and rewritten encrypted on the way
  past. `ALLOWED_DIRS` and `SLACK_TEAM_ID` stay readable — they are
  configuration, not credentials, and encrypting them would blind
  `extension_security.assess_config`. The envelope now names the mechanism that
  wrote it (`friday-enc:v1:<method>:<b64>`) so a blob carried to another
  machine is refused with a message that says to reconnect, rather than decoded
  to binary noise and sent as the token.

### Performance

- **Prompt caching sends a second breakpoint.** Anthropic's cache lookback is
  20 blocks and Friday sends more than that, so a single breakpoint could fall
  outside the window on a long turn and silently cache nothing.

- **The cost meter charges what Anthropic charges.** Three separate places
  guessed at prices; all three now read one table.

  > **Misfiled and understated; corrected in 5.6.5.** This is a Fixed-section
  > defect, not a performance note. Opus 5 — Friday's default — was metered at
  > $15/$75 per MTok against a real $5/$25 and so billed at **three times** its
  > real price; Fable 5 was billed as the cheapest model in the lineup when it
  > is the most expensive; and `claude-haiku-4-5` metered at exactly $0. The
  > Cost & Usage panel and both budget tripwires inherited all three.

- **`/api/intelligence` stops paying to re-learn that Ollama is off.**
  Connecting to a *closed* localhost port on Windows costs ~2,005 ms — the
  stack retries the SYN before giving up — not the microseconds loopback
  implies. The Intelligence panel polls on a timer and the probe had no memory,
  so a machine with Ollama switched off paid that on every render. The probe
  now remembers a success for 25s and a refusal for 20s: backoff rather than
  removal, because the daemon can be started at any moment. A failure is
  recorded as a failure rather than cached as an empty success, which would
  render as "Ollama is running and has no models" — a different and wronger
  claim. The client abort goes from 12s to 30s, because the cold path is where
  the variance lives.

### Tests

Four assertions that were failing against correct product behaviour are
re-pinned: three stale doubles, and one assertion that prompt caching broke
when it landed by changing the shape of a message body. The `cloud_only` tests
now exercise the router rather than whichever test last wrote settings.

Known, and pre-existing since before 5.6.3: seven unit tests fail on this line.
Five are `test_residency_arbiter` against a `FakeLlama` double that predates
`adopt_or_reap`; two more are `test_gate_harness_integrity` and
`test_local_image`. Two `test_ollama_manager` assertions additionally flake
with GPU load — they assert on the *last* call made, and `chat_completion`
legitimately issues a seat-release call afterwards when the display is under
its memory reserve, so they fail whenever the card is busy. All of these are
test defects, not product defects, and none is introduced by this release.

> **The count is wrong; corrected in 5.6.5.** A full-suite run on a clean
> `v5.6.4` checkout fails **16**, not seven, and `test_gate_harness_integrity`
> — named here — passes. See 5.6.5 for the measured list and for the
> isolation-versus-whole-suite distinction this paragraph is missing.

### Privacy

- **Layer 4 of the sensitivity classifier asks a model that exists.** It
  POSTed a hardcoded `gemma4:latest` to Ollama — a tag never installed here —
  so every call 404'd and the layer returned 0, which is also its value for
  "no opinion". A dead layer and an unopinionated one were indistinguishable,
  and nothing ever surfaced it. Resolving the name against the merged local
  registry was not sufficient either: that view includes models served by
  llama-server which Ollama cannot answer for, so the daemon still 404'd. It
  now intersects the two registries and takes the largest model Ollama
  actually serves.

  *(This shipped in 5.6.4 and was omitted from its notes; recorded here in
  5.6.5.)*

---

## [5.6.3] — 2026-08-26

The first release built for someone who isn't the author. Four things a new
user actually hits, found by installing Friday on hardware and an account
that weren't hers.

### Added

- **The local model ladder now scales with the card.** It used to top out at
  `gemma4:12b` — a 16 GiB card, a 4090, and a 5090 all got handed the same
  7.5 GB model, because "largest that fits" had nothing left to reach for.
  Now it runs `qwen3:4b` (2.5 GB) through `qwen3:32b` (20.2 GB) by hardware,
  and `friday models` shows every rung a machine could run, with the default
  marked, instead of announcing one decision. Tool calling is now a hard
  gate on selection rather than a preference — `gemma3:4b` cannot be chosen
  at any tier because it cannot call tools, and the flag is re-verified
  against the daemon's own capabilities after every install rather than
  trusted from a table. **The `qwen3:14b` and `qwen3:32b` fits are
  UNMEASURED** — arithmetic, not a timed run — and are marked that way in
  the code and in `friday models`' own output, not just here.
- **A real arithmetic bug is fixed alongside it.** `vram_gib` was hand-typed
  per model and already carried ~2 GiB of unstated overhead; a second,
  separate overhead constant took another 1 GiB off the card on top of that.
  Together they demanded a 13 GiB card for `gemma4:12b`, a model measured to
  run fully resident in 11 — so a 12 GiB card was refused the one model
  built for it. Overhead is now counted once, from a measurement.
- **API keys are manageable from Settings.** View, replace, and remove any
  provider's key without touching a launch script. A dead key and an
  out-of-credit one used to look identical from Settings; `/test` now
  distinguishes them the same way `Test-AnthropicKey` has since `5.6.2`.
- **Google connects without a JSON file.** A guided, in-app walkthrough
  replaces "go create a Cloud project and drop credentials.json here." A
  one-click path is also built and takes precedence when available, but it
  ships **inert** — the shared client ID and secret are empty until Stephen
  mints them for his own Google Cloud project, so today everyone still uses
  the walkthrough. Full reasoning, including why this had to publish in
  Production rather than Testing mode, is in the commit and
  `THREAT_MODEL.md` §4.
- **Cloud-only mode is now honored everywhere, not just in chat.** A
  keyless safety net was silently routing cloud-only turns to a local model
  whenever no Anthropic key was present — correct on a developer machine
  that always has a key, wrong on the first machine that never did. Fixed
  across chat, agentic/tool turns, briefings, and scheduled work; the
  mirror-image bug (local-only turns quietly falling back to the cloud) is
  fixed at the same time, in the same rule, used by both.

### Fixed

- `cli.BUNDLED_MODEL` still pointed at `gemma3:4b` — `friday doctor` was
  recommending the one model in the ladder that cannot use Friday's tools.
  Now derived from the same ladder as everything else.
- `ollama_manager.recommend_models` was a second, disagreeing model ladder
  with its own VRAM thresholds; it now shares the planner's arithmetic.
- The Settings UI could link to provider tabs that don't exist.

### Changed

- Installer documentation pass: `INSTALLATION.md` and `README.md` no longer
  describe a single bundled model or a fixed RAM floor — both now describe
  the question the installer actually asks and the ladder it actually
  offers.

---

## [5.6.2] — 2026-08-26

**The published `v5.6.1` artifact does not contain the API-key check.** The
commit that adds it (`d272a61`) landed a few hours after the `v5.6.1` tag was
cut, so the zip anyone actually downloaded promises self-repair on a bad key
and does not deliver the check that was supposed to catch one. This release
is that fix, published. If you have a `5.6.1` install, replace it — everything
else in it is unchanged and confirmed unaffected (see below).

### Added

- **`Test-AnthropicKey`**: one request at `max_tokens = 1`, made the moment
  the key is entered — before self-repair is armed, and while the person who
  typed it is still looking at the screen. `200` → ok. `401`/`403` → rejected.
  A `400` naming credit → no_credit. Everything else — no network, a 5xx, a
  timeout, an unrelated `400` — is `unknown`, logged, and **fails open**: an
  optional pre-flight must never be the thing that blocks setup.
- **`KNOWN_ISSUES.md` §3b**, "One key per machine, and no way to tell whose it
  is." The installer now says, at the point a key is entered, that a shared
  key carries no notion of whose machine is using it and no way to revoke one
  install without revoking all of them.

### Confirmed unaffected

- **The local-model question (Step 2b) and the `qwen3:4b` small-card default
  are already in the published `v5.6.1` artifact.** Checked directly against
  the tagged tree, not assumed from the commit graph — this release does not
  touch either.

---

## [5.6.1] — 2026-08-25

**This release exists because the installer had never been run** — not
recently, but *ever*, on any machine, including the one it was written on.
There was no `AgentFriday` folder in `%LOCALAPPDATA%` to prove otherwise and
the self-repair loop had executed exactly zero times. It was then run end to
end in an isolated profile, and four things broke.

No new features. Everything here is about the first twenty minutes on a machine
that is not the author's. Full notes in `RELEASE_NOTES.md`.

### Fixed

- **`Write-Log 'PLAN'` killed the install at step 3 of 16.** An invalid log
  level aborted the run outright under `Set-StrictMode`. Now covered by a test
  in `packaging/windows/tests/Test-Installer.ps1`.
- **One missing known folder cost all four shortcuts.** A single unresolvable
  path took the whole shortcut step down with it rather than the one shortcut.
- **The self-repair healer was diagnosing blind**, and paying for truncated
  answers.

### Changed

- **The local-model default is now a question, not a flag.** Asked once, before
  the twenty minutes of downloading rather than after. This matters most on an
  8 GB card, where the old default was actively wrong in a counterintuitive
  way: Windows and the residency layer leave ~4.5 GiB of an 8,188 MiB card, and
  `ModelRouter._route_vault` pins vault turns *on-device with no cloud
  fallback* — so a struggling local model fails those turns outright, while
  having **no** local model routes them to Claude via the `redact` branch and
  works. Zero local models is safer than one that struggles.

### Added

- A test that stops a dependency which phones home from being added quietly.

---

## [5.6.0] — 2026-08-25

Two days (24–25 August) that were mostly spent finding out that things this
project already claimed were not true. Four of those things were live security
defects in the egress gate — the mechanism the whole product rests on. They are
listed first and in plain language, because a sovereignty tool whose users are
not told when its gate was leaking has nothing left to sell.

### Security — the gate was leaking, in four separate ways

All four are fixed. All four were live in shipped code. None of them were
theoretical, and the first three were found by measuring against real data
rather than test fixtures.

- **The classifier had no phone, address, or account-number regex — ever.**
  Not a regression: there had *never* been one. Phone numbers in every common
  shape, street addresses, masked account tails ("Chase account ending 4417")
  and issued identifiers ("policy number BX-99120384") classified as **TIER_1
  (public)** and were sent to cloud providers verbatim. Seventeen of twenty
  realistic vault shapes classified TIER_1 in both the routing and egress
  paths. These shapes were nominally covered by Layer 2 (Presidio, which was
  installed in no environment) and Layer 3 (embeddings, excluded from the
  frozen build *and* disabled outright in the vault path) — so **the shipped
  binary had no detector for them at all.** The only thing between "emergency
  contact: 555-1234" and a cloud provider was the literal phrase "phone
  number" happening to appear nearby. Now covered by Layer 1a regexes, which
  are mode-independent and so fix routing and egress together.

- **The wiki context section failed OPEN.** This is what made the gap above
  fatal rather than merely wrong. Nearly every personal section of the context
  prompt is hard-tagged TIER_2, so a classifier miss still results in the
  content being withheld. The wiki/briefing section was the exception: it
  passed a TIER_1 fallback, so a miss degraded to *sent in full* rather than
  *withheld*. Measured against the real corpus: **of 95 files in
  `~/.friday/wiki`, 90 reached the cloud in full under the old fallback. Zero
  do now.** The same corpus was already travelling fail-closed by the other
  loader — one body of personal data under two different policies depending on
  which code path reached it first.

- **Voice egress failed open at the strongest verdict.** `routes/voice.py`
  wrapped the gate in one broad `except`, so `NeverSendBlocked` — the gate's
  most severe result, the one that means *never send this* — fell through to
  the **pre-gate, ungated payload being sent to Google.** On both the
  tool-result and typed-text legs. Both now fail closed through extracted,
  directly-tested helpers: every exit is either gated text or a withheld
  placeholder, never the raw value.

- **Background tasks skipped vault gating entirely.** Task workers and a batch
  of briefing/draft/compose sites built their system prompts through
  `_get_friday_system_prompt` with no `provider` or `vault_control` — the
  function's own documented "legacy ungated" default — so TIER_2 vault content
  rode into prompts in the clear. 22 call sites in total. They now predict the
  destination provider and gate accordingly. The parameters are now **required**,
  and a pre-commit static check enforces it, because two of the 22 were
  background jobs that would not have raised until their next scheduled run.

- **The anti-fabrication directive was itself being redacted.** The honesty
  directive — the instruction that stops Friday inventing tool results and
  narrating over failures — was one 3,583-character paragraph whose items were
  joined by single newlines. The egress gate splits on `\n\n`; having none of
  its own, the whole directive rode inside one merged paragraph and was
  **withheld as a unit from every cloud call**, tripped by a worked example that
  contained the words "asked for its phone number". So the instruction against
  fabrication was deleted from the prompt by the privacy filter — which is the
  mechanism behind a morning of fabricated file reads, invented task
  confirmations, and claimed actions that never happened. Fixed by
  paragraph-splitting the directive, rewording the item that tripped the
  classifier, and registering it (and `SELF.md`) as trusted text, since neither
  was ever the user's data. The withheld-content placeholders now say what to
  do — don't retry, don't invent it, tell the user — instead of only what
  happened.

### Documentation — we were claiming protections the binary does not provide

- **`THREAT_MODEL.md` said "four-layer classifier". It shipped two.** The
  document now states, per layer, what is in force and in which build, and
  explains that **Presidio was evaluated and deliberately rejected** rather than
  merely missing: measured 2026-08-24, it scored TIER_2 where the existing regex
  returns TIER_3, and escalated 6 of 12 entirely benign prompts — *"What is the
  weather going to be like tomorrow?"* among them. It is installed by the
  Windows installer and runs **observe-only**; enforcement needs an explicit
  `FRIDAY_PRESIDIO_ENFORCE=1` and is not recommended.
- The egress guarantee now names its own limits, including the fact that a file
  grant is a deliberate hole, and that the classifier is only as good as its
  patterns.
- New [docs/FILE_GRANTS.md](docs/FILE_GRANTS.md) documenting the permission
  model properly.
- `docs/INSTALLATION.md` now states plainly that the `.exe` and the Windows
  installer **are not equivalent privacy products**, and that no model is
  downloaded behind the user's back.

### Fixed — the honesty report was itself over-reporting

On 24 August we shipped `services/privacy_layers.py` for one reason: the
sensitivity classifier's docstring advertised four layers while the process ran
two, and we wanted a component whose whole job was to refuse to repeat a claim
it had not checked. It probes at startup and reports what is genuinely loaded.

On 25 August that module was found doing precisely the thing it was written to
prevent.

It decided a layer was active by asking whether the layer's module could be
**imported**. That is not the same question as whether the layer does anything.
The Windows installer *does* install `presidio-analyzer` — a fact the previous
day's work had itself got wrong — so `find_spec` succeeded, and a fresh install
printed **"4/4 layers active"** while Presidio sat deliberately inert, because
`classify()` routes it to observe-only shadow mode unless
`FRIDAY_PRESIDIO_ENFORCE=1` is set. The module built to stop the docstring
overstating protection was overstating protection itself, one level up, in
exactly the same shape: a claim inherited from a plausible proxy rather than
measured against the thing it actually asserts.

The lesson is the rule, and the rule is now the code: **a layer counts as active
only if it can change a decision.** Importability is a proxy for that, and the
proxy fails precisely where it matters most — when a dependency is present but
deliberately disarmed. The honest form of "is this protection on?" is never "is
the library installed?", and anything that reports on a safety property deserves
the same suspicion as the property itself.

- `privacy_layers.probe_layers()` now gates the Presidio layer on
  `enforcement_enabled()` rather than on `find_spec` alone, and says so in its
  reason string. Reproduced before fixing; two regression tests, one in each
  direction.

### Added

- **Local file discovery.** `search_files` on both the text and voice surfaces:
  searches Documents, Downloads, Desktop and Friday's creations by name, or
  inside extractable text. Never searches the vault.
- **Real text extraction.** PDFs via `pdfplumber`, `.docx` via stdlib zip+XML.
  Friday no longer hands a model raw bytes, and says so honestly when a PDF has
  no text layer rather than guessing at its contents.
- **User-granted cloud permissions for files** — content-pinned, HMAC'd,
  append-only ledger at `~/.friday/privacy/file_grants.jsonl`, with four
  endpoints under `/api/privacy/`. File grants pin a SHA-256 and may be
  permanent; folder and glob grants must expire within 30 days; deny beats
  grant; no model on any surface can create one; a corrupted ledger can only
  tighten. See [docs/FILE_GRANTS.md](docs/FILE_GRANTS.md).
- **KV cache quantization on the local seat**, with automatic fallback to f16
  when the server does not support `--cache-type-k/v`.
- **Configurable Gmail window** — `gmail_window_days` setting and a `?days=`
  override, replacing a hardcoded window.
- **Boot audit for seats** — startup now names the resolved endpoint and the
  model actually answering, instead of the model that was requested.
- The llama-server banner now goes to `runtime/logs/` instead of being
  discarded.

### Changed — packaging

- `pdfplumber` promoted from an optional extra to a **core dependency** in
  `pyproject.toml`. It was already marked "NOT OPTIONAL" in `requirements.txt`;
  the two manifests disagreed, so a lean `pip install -e .` produced a Friday
  whose PDF branch told the *user* to install Friday's own dependency.
- `nemo`, `nemo_toolkit`, `lightning`, `pytorch_lightning`, `lightning_fabric`
  and `torchmetrics` added to the PyInstaller `excludes`. Nothing imports them;
  they arrive in a dev venv only because the Tier-2 voice installer puts them
  there at the user's request, and PyInstaller then sweeps whatever is in
  site-packages. They are dead weight twice over, since NeMo needs torch, which
  is already excluded.
- `file_extraction`, `file_grants` and `file_search` pinned explicitly in the
  spec (belt-and-braces — `collect_submodules` was verified to enumerate them).
- `.docx` support deliberately adds **no** dependency.

### Contributor-facing

- Pre-commit hook now runs `scripts/check_gated_prompt_callers.py`. See
  [CONTRIBUTING.md](CONTRIBUTING.md) for why the required arguments exist and
  why passing `provider='cloud', vault_control=None` to silence it *is* the bug.
- **`index.html` is the UI source of truth, not `ui_parts/`.** They have
  diverged: 18 top-level components, including the whole conversations feature,
  exist only in the built file. `build_ui.py` now refuses a build that would
  drop components. JSX precompilation was also silently disabled from 18 August
  until 25 August.
- The forensics snapshotter is a Windows scheduled task (`AgentFridayForensics`);
  `ops/forensics-down.ps1` stops it.

---

## [5.5.0] - 2026-08-21

A release that fixes things that were quietly broken and documents what still
is. Full notes in `RELEASE_NOTES.md`; open defects in `KNOWN_ISSUES.md`.

### Fixed — the server could not start, and said nothing

- `agent.py` had a module-level use-before-definition that killed every start
  ~2s in. The tray spawned the server with `stderr=DEVNULL`, so six overnight
  failures left no trace. Stderr is now captured to
  `~/.friday/server_stderr.log`; a ~3.5s import check runs at launch, on
  pre-commit and in CI.
- `_wait_for_health` 30s -> 300s against a measured ~143s cold start. The tray
  had reported `FAILED TO START` on every successful start.
- Blueprint failures are tiered: required exits loudly, optional starts and
  announces in the log, a notification and `~/.friday/startup-report.json`.
  New `GET /api/startup-report`. The career pipeline had been unregistered for
  seven weeks behind a warning nobody read.
- `routes/jobs.py` resolves the repo root explicitly, so it imports outside the
  tray launch path.

### Fixed — silent success

- Background tasks that received a provider error as prose reported "Task
  complete". Every voice session's wiki distillation had been discarded this
  way for weeks. Any task whose reply is a provider failure is now marked and
  reported failed.
- `tool_results.append` was indented inside an `except`, so tool results were
  never appended on the normal path.
- CLI commands did not propagate exit codes. `friday models --install`
  computed a failure, printed it, and exited 0. All 14 branches now propagate;
  `cmd_health` returned a bool, and `sys.exit(False)` is 0 — a failed health
  check reporting success.
- `/api/mcp/status` bound `_MCP_MANAGER` at import, reporting a working
  subsystem with ~99 tools as dead for the life of every process.
- `friday doctor` matched a model family instead of a tag, reporting
  `gemma4:e2b` installed when only `gemma4:12b` was.

### Fixed — privacy and safety

- Tool descriptions were classifier-gated and blanked, so cloud-fallback turns
  handed the model unreadable tools. Now scoped to MCP tools only; first-party
  descriptions are static documentation.
- TIER-2 sensitivity split into strong phrases and context-gated common words.
- The secret scanner no longer flags config reads or prose; no known key shape
  is exempted.
- Package import no longer prints the names of secrets loaded from launch
  scripts, and its count matches its list.
- Local voice pins a resident brain via `local_seats.resolve()` and refuses to
  start when none exists. Fixed in code, unproven in practice — see
  `RELEASE_NOTES.md`.
- Tag `v4.4.0` (pre-scrub) deleted from origin; 93 local `archive/*` tags
  deleted. All 42 origin tags audited.

### Fixed — install

- `REPO_URL` pointed at `friday-desktop.git`, which does not exist. Every
  one-line install died at the clone.
- `setup_wizard.py` was invoked from the repo root in both installers.
- All three installers and `friday update` regenerated `index.html` from a dead
  mirror, deleting 17 components and downgrading to CDN Babel.

### Added

- `friday models` / `friday models --install`: hardware-grounded model
  selection. Every refusal names its rule and shows the arithmetic. Vault
  memory and tools are CPU-only and need no GPU.
- `docs/TUTORIAL.md` — clone to first conversation.
- `KNOWN_ISSUES.md` — open defects, unverified claims, and what leaves your
  machine.

### Changed

- Documented RAM floor 8 GB -> 16 GB, matching rule R2, which always disagreed
  with the old figure.
- Platform support stated plainly as Windows-first.
- `package.json` ISC -> MIT; `pyproject.toml` and `package.json` agree at 5.5.0.
- `.github/SECURITY.md` supported versions 1.0.x -> 5.5.x.

### Known limitations

- A wheel install cannot run the career pipeline: `data/` and `skills/` are not
  packaged. Install from a clone.
- CPU generation throughput is unmeasured for every model.
- No announced release should go out until the Google API key,
  `FRIDAY_PASSWORD` and the Discord invite are rotated.

---

## [5.4.0] - 2026-07-06 "Second Brain"

### Knowledge System Overhaul — the wiki becomes a galaxy

`docs/KNOWLEDGE_SYSTEM_SPEC.md` (Opus 4.8 STORM spec): a two-tier knowledge
graph over the wiki, SOUL.md, conversation memory, and cognitive memory,
plus a 3D "explore your second brain" workspace.

- **Tier A — structural graph** (`services/knowledge_graph/`): the wiki *is*
  the graph. `[[wikilink]]`/markdown-link/title-mention edges (mention pass
  is one combined-alternation scan, O(corpus bytes)), communities via
  Leiden→greedy label-propagation fallback (ported from obsidian-wiki, MIT)
  with section-aware auto mode, deterministic server-side 3D layout
  (Fibonacci shells + spring relaxation), god-nodes/dead-ends/surprising-
  connections analysis, and a zero-LLM structural query router with
  `should_read` shortlists. Path-qualified page keys (colliding stems in
  different sections both survive). Rebuilds on wiki save (~200 ms today).
- **Tier B — GraphRAG semantic index** (`indexer.py`, `retrieval.py`,
  `prompts/` vendored verbatim from graphrag-workbench, MIT): entity +
  relationship extraction, description merging, community reports, local
  MiniLM embeddings; local/global/drift search with an auto-router.
  **Local-only by default** — every LLM call rides `_generate_text` behind
  the egress gate; classify-before-extract pins TIER_2/3 chunks to local
  models in every mode; a failed gate self-test disables cloud indexing;
  TIER_2/3-derived artifacts are vault-encrypted at rest (fail-closed when
  no key). Adversarial suite: `tests/security/test_kg_egress_adversarial.py`.
- **Knowledge Galaxy workspace** (`KnowledgeGraphWS`, dock: 🌌 Knowledge,
  plus a Galaxy button in the Wiki header): vanilla Three.js r128 —
  InstancedMesh stars, merged LineSegments filaments, soft-particle nebulae
  with community labels, UnrealBloom; cinematic entrance (camera fly-in,
  constellations igniting in sequence, edges weaving in), hover-highlight-
  neighbors, double-click → the page opens in Wiki, search lights up
  constellations, live SSE ignite events, fps meter with auto-degradation,
  community-LOD super-nodes above 2.5k nodes. Measured 39–40 fps at 2,000
  nodes / 6,000 edges under headless ANGLE; the full flow verified with all
  external requests dead (offline-first: fonts + MediaPipe now load async).
- **Flask API** (`routes/knowledge_graph.py`): summary / graph / node /
  neighbors / query / search / reindex (+status) / SSE events.
- **Agent tools**: `knowledge_query`, `knowledge_related`,
  `knowledge_communities` (Ring 0, structural, offline).
- **Integration**: graph-aware AUTO-CONTEXT injection; memory-dreaming and
  learning-loop post-steps ignite new facts/skills as graph nodes live;
  nightly 03:30 reindex job (after dreaming); Settings → Knowledge Graph
  tab (indexing mode, sources, grouping, manual reindex).
- Tests: 78 new (unit/api/security) + `tests/knowledge_galaxy.spec.ts`
  (Playwright: mount, data contract, fps floor, click→wiki).

---

## [5.3.0] - 2026-07-06

### Content Pipeline — Friday becomes a publishing platform

The full social-media management system specified in
`docs/CONTENT_PIPELINE_SPEC.md`: create → compose → schedule → publish →
monitor → learn, across eleven platforms plus the Friday Federation.

- **Data model & store** (`services/content_pipeline.py`): `ContentPost` /
  `PlatformTarget` entities, SQLite store (WAL), the full §3.2 status machine
  (DRAFT→SCHEDULED→PUBLISHING→PUBLISHED/PARTIAL/HELD/FAILED, sticky-safe
  holds, re-arm that never double-posts), append-only publish log.
- **Composition engine** (`services/content_composer.py`): one canonical body
  → platform-native versions (thread splitting, grapheme-aware limits,
  platform hashtag norms, format conversion matrix), voice injection via
  SOUL.md + user-editable per-platform voice cards, all LLM calls
  engine-direct and egress-gated.
- **Publication engine** (`services/publisher.py`): a 1-minute scheduler
  builtin claims due targets and runs the gate chain — H1–H4 moderation scan,
  sensitivity classification with **hold-for-review** semantics (never silent
  redaction; explicit human release honored, hard harm floor never
  releasable), adapter prepare/publish, retry ladders with Retry-After,
  verify-before-retry double-post protection, per-platform rate budgets,
  native-schedule delegation (YouTube `publishAt`, Mastodon `scheduled_at`),
  recurrence templates, signed provenance publication entries, ψ earn.
- **Eleven platform adapters** (`services/platforms/`): LinkedIn, X/Twitter,
  Instagram, YouTube, TikTok, Bluesky (byte-offset facets), Mastodon
  (instance-aware), Reddit (per-subreddit rules surfaced at compose),
  Substack + Medium (honest assisted handoff — no fake automation), and the
  Friday Federation (marketplace listing + encrypted CONTENT_OFFER + ψ) —
  each behind one contract with capabilities declaration, encrypted
  credential storage, local rate budgeting, and a shared contract test
  battery.
- **Analytics collector** (`services/analytics_collector.py`): decaying-poll
  engagement collection normalized into one metrics shape (absent ≠ zero),
  local-only storage, weekly insights (attribute lift with Wilson bounds,
  learned best-times that outrank seed tables at n≥5), engagement→ψ minting
  (idempotent, daily-capped), strict untrusted-input discipline — platform
  text never reaches an LLM prompt.
- **ContentWS v2** (Content workspace): Compose (platform chips, live
  per-platform previews, hashtag rows, variants, alt-text nags) · Calendar
  (drag-reschedule, optimal-slot suggestions) · Queue (held-with-evidence,
  release/edit, history, pause-all) · Analytics (unified dashboard, insight
  cards) · Accounts (one-click OAuth or token paste — verified live at
  connect, plain-language scopes with the "cannot" list, rate budgets,
  disconnect = revoke + purge) · Ideas (the v1 kanban, graduated).
- **API**: 23 new `/api/content/*` routes (documented in `docs/API.md`);
  OAuth loopback callbacks on localhost only; full local data export.
- **Chat/voice tools**: `content_create_post`, `content_schedule_post`,
  `content_post_status`, `content_repurpose` — "Friday, post this to
  LinkedIn and Bluesky tomorrow morning" works end to end.
- **Scheduler**: new `once` trigger (one-shot schedules, auto-disable).
- **Provenance**: `add_publication()` — publication events signed into each
  asset's manifest history; the local ledger remains the source of truth.
- **Review hardening**: a 10-finding adversarial review pass (all confirmed
  real, all fixed with regression tests) — egress gates now cover title
  fallbacks and hashtag/tag side-channels, HELD release actually publishes,
  recurrence idempotency is pagination-immune, native-schedule declines
  defer instead of publishing early, YouTube uploads verify-before-retry,
  analytics observations are once-ever per post.

### Voice System Overhaul — systemwide bug hunt + out-of-the-box hardening

Root-caused and fixed the "voice is broken again" report across all three
tiers. Spec: `docs/VOICE_SYSTEM_SPEC.md` (new, STORM-derived); findings
verified against the live Gemini API by real `bidiGenerateContent` connects.

**Fixed — Tier 3 (Gemini Live) fluidity: raspy, non-interruptible, hours-long
(verified against Google's CURRENT Live API docs, 2026-07):**
- **Barge-in now works by default.** The default "speaker" mode set
  `activity_handling = NO_INTERRUPTION`, so Gemini's VAD fired but the model
  never stopped — voice was uninterruptible. Per Google's current docs,
  barge-in is `START_OF_ACTIVITY_INTERRUPTS`; that is now the default for every
  mode except an explicit "no interruption (open speakers)" opt-out. On the
  interrupt the client already flushes the playback ring, so barge-in cuts
  audio within a frame. Echo mitigations kept (LOW start sensitivity + browser
  echoCancellation) so Friday doesn't cut herself off on open speakers.
- **Raspy-over-time fixed in the playback worklet.** Output is confirmed 24kHz
  PCM16 mono (not the regression). The worklet had no jitter cushion — it
  played the instant the first sample landed, so every network gap underran the
  ring, and each underrun click compounded into progressive rasp over a long
  call. Added a 120ms prefill (re-primed after any underrun so playback only
  ever runs off a cushion), and enlarged the ring to 180s with an anti-wrap
  guard that prevents the write pointer from ever lapping the reader (silent
  corruption) without truncating a legitimately long, faster-than-realtime
  response.
- **Hours-long stability.** `context_window_compression` (sliding window) is
  on by default — without it the session hits a hard ~15-min cap and
  terminates. Combined with the existing session-resumption handle capture and
  GoAway→reconnect drain loop (survive the independent ~10-min per-connection
  cap while carrying full context), multi-hour conversations continue without
  perceptible context or quality loss. UI Interruption-Mode and
  Context-Compression controls relabeled to match the new defaults.

**Fixed — Tier 3 (Gemini Live):**
- `/friday-live`, its manifest, and service worker 404'd after the v5 `src/`
  restructure (`send_from_directory('.')` resolved against the package dir) —
  the Tier-3 PWA client was completely unreachable. Anchored like `/static`.
- The model story was BACKWARDS: live connect probes show
  `gemini-3.1-flash-live-preview` and the 12-2025 preview still work, while
  the previously "verified" fallback `gemini-2.5-flash-preview-native-audio`
  does not exist (1008). New verified chain: `native-audio-latest` →
  `preview-09-2025` → `3.1-flash-live-preview`; explicit
  `_RETIRED_LIVE_MODELS` denylist checked BEFORE the marker heuristic, so
  `validate_live_model` reports `retired` and the auto-correct (now firing on
  `retired` + `unknown`, persisting only the delta — never the offline
  routing overlay) actually corrects the IDs it was built for.
- `DEFAULT_SETTINGS.voice_model` (which always wins over `LIVE_MODEL`) updated
  to the `-latest` alias everywhere (core, setup wizard, registry, UI consts);
  the new IDs are selectable in Settings and priced in the cost meter.
- `LiveConnectConfig` construction moved inside the per-attempt try (a
  pydantic ValidationError on older SDKs was silent); the top-level runner
  crash now sends an error frame instead of a debug-only log line.
- `friday_live.html`: capped exponential reconnect backoff, fatal-error stop
  (no more 1.5 s reconnect storms into a bad key), sticky actionable error
  banner, WS auth token support, and caught `getUserMedia` rejections.

**Fixed — egress gate (was killing cloud voice + chat):**
- The keyword layer's bare substring matching classified Friday's own system
  prompt ("Sovereign Vault") and everyday turns ("doctor appointment",
  "courtesy" via 'court') as TIER-3, emptying `system` and message content on
  EVERY sealed cloud call — the Anthropic API then 400s and the voice user
  hears "Sorry, I hit an error." Now: word-boundary matching, strong/weak
  keyword split (2+ distinct weak hits escalate), product-architecture terms
  excluded, span-level paragraph redaction instead of whole-field drops, a
  trusted-constant registry (the shipped system prompt survives sealing),
  never-empty message substitution, and a false-positive leg in the startup
  self-test. Leakage posture unchanged: flagged content still never leaves.
- Closed the Gemini Live gate BYPASS: tool results (email snippets, wiki
  excerpts), typed turns, and voice-context openers now pass the egress gate
  before reaching Google.

**Fixed — Tier 1 (local CPU):**
- Silero VAD judged each ~85 ms mic chunk by only its first 32 ms, discarding
  utterance onsets and endpointing mid-sentence — now scores every 512-sample
  window (max-pool) and keeps a ~250 ms pre-roll, so first syllables reach
  Whisper.
- `/api/voice/setup/test` 500'd on every call (`b64encode` on a BytesIO) —
  the first-run TTS test could never pass on any tier; it now returns real
  audio (and the wizard actually PLAYS it) or an actionable 503.
- Piper voice download: streamed with a 30 s timeout (was `urlretrieve` with
  none, hanging all voice sessions forever on a stalled connection while
  holding the engine lock); model-load failures now surface the real cause.

**Fixed — Tier 2 (local GPU):**
- `gpu_status()` now falls through to nvidia-smi when torch is CPU-only, so a
  physical RTX GPU is detected and the "install a torch-CUDA wheel" hint can
  actually surface; an explicit `local-gpu` preference that degrades to CPU
  is announced with the reason instead of silent; `voice-local-gpu` dep-group
  status requires real CUDA, not mere torch importability.

**New — in-UI install + diagnostics (out-of-the-box requirement):**
- `services/voice_installer.py` + `POST /api/voice/setup/install[/status|/cancel]`:
  allowlisted background installs (Tier-1 deps, Tier-1 model download, Tier-2
  torch-CUDA + NeMo) with streamed logs, disk preflight, and cancellation — no
  180 s cap, no pip incantations.
- Voice Setup Wizard: per-step Install/Download buttons, live install log, a
  GPU-tier step, honest step statuses (derived from real health fields — a
  fresh machine no longer shows green checks), and stale-model results gate
  readiness.
- Mic/speaker Test buttons (live level meter, AirPods zero-PCM detection)
  wired into the audio-device popup — the implementations existed but nothing
  rendered them. Voice Tools toggle and device selections now persist
  (`voice_tools`, `audio_input_device_id`, `audio_output_device_id` were
  missing from `DEFAULT_SETTINGS`, so every save silently reverted on reload).
- Per-tier smoke gate `tests/smoke/test_voice_tiers.py`: Tier-1 real TTS→STT
  loopback, Tier-2 detection contract + runnable-or-actionable-skip, Tier-3
  chain sanity + opt-in live connect probe (`FRIDAY_SMOKE_CLOUD=1`).

**Fixed — restructure fallout:**
- Root `server.py` shim works from any cwd; `friday start`/`setup` inject
  `PYTHONPATH` (they crashed instantly in non-pip-installed checkouts) and
  report the child's exit code; `friday update` uses repo-root paths again
  (it always claimed "not a git repository"); `pystray`/`pillow`/`pyttsx3`
  added to manifests; stale v4.5.0 flat-layout `agent_friday.egg-info`
  removed; the index.html 404 message names the real build command.

### Content Pipeline — self-knowledge & docs

- **Friday knows her publishing stack.** `SELF.md` gains a Content Pipeline
  capability section (compose → schedule → publish → measure → learn, the
  sovereignty invariants — hold-for-review, no engagement automation, honest
  Substack/Medium handoff — plus demo talking points), and `VOICE_DEMO.md`
  gains a "How I publish" section so voice demos describe publishing with
  the same lucidity as creation.
- **API reference.** `docs/API.md` documents the `/api/content/*` v2 routes
  (posts, compose, schedule, publish-now, cancel/release, preview,
  repurpose, queue, calendar, best-times, analytics, insights, platform
  connect/disconnect, voice cards, export) per
  `docs/CONTENT_PIPELINE_SPEC.md` §11.
- **Manual test procedures.** Per-platform first-connect + first-publish
  checklists (spec §15) appended to `tests/MANUAL_TEST_PROCEDURES.md` —
  real platform OAuth can't be CI'd.

### Model Selector

- **The top-bar pill is just the model name.** It now shows the active
  orchestrator's short label plus a caret — no cloud/home emoji, no
  "+ Local" suffix. Fresh installs still read "Sonnet 5".
- **Clicking it opens a compact 320px panel instead of a wall of models.**
  *Quick Switch* pins the current model first and offers up to 4 available,
  provider-diverse alternatives — one click switches the orchestrator and
  closes the panel. *By Role* collapsible rows (one open at a time, each a
  short capped list: 5 options for Orchestrator/Subagent, 4 per Creative
  sublist, the available Voice engines) cover Orchestrator, Subagent,
  Creative — split into an Image model (flat `creative_model`) and a Video
  model (`capability_routing.creative_video` `{model, provider}`) — and
  Voice, which selects the engine and shows the Gemini Live model sublist
  only while the gemini engine is active. A *Routing Mode* row toggles
  Cloud Only / Smart / Local Pref. / Local Only with compact stats, a
  *Local Models* section appears only when Ollama is actually running
  (top 4 with size badges, current default pinned into view, "▸ N more
  installed" expands the full list in-panel), and a *Browse All Models*
  footer button deep-links Settings straight to the Providers tab (via
  `window.__fridaySettingsTab` + the `friday:settings-tab` event).
- **No dead rows.** The panel never shows more than ~15 model entries, and
  unavailable models simply don't appear — nothing is grayed out.
- **`GET /api/models` role lists are now curated.** Only descriptor-declared
  statics plus live Ollama models make the role lists; the discovery long
  tail (OpenRouter's 300+) stays in the flat `models` list. Every entry
  carries a new boolean `curated` field, and `selected` gained
  `creative_video_model` (read from `capability_routing.creative_video` —
  video has no flat `*_model` key).
- **`GET /api/models/search` learned modality, local, and sort.** New
  `modality` param (exact member of the modalities list — one of
  vision/image/video), `local=1` (on-device providers only), and `sort`
  (`price` | `price_desc` | `context`), all applied BEFORE the limit
  truncation; price sorts put unpriced entries last, since unknown ≠ free.
  Result rows gained `modalities` (list) and `local` (bool), static rows
  resolve their label + modalities from provider `model_meta`, and
  Ollama-backed providers list their live installed models. Negative wire
  prices (OpenRouter's "pricing varies" sentinel) now read as unpriced —
  never as the cheapest model in a sort or a negative spend figure.
- **Settings → Providers Model Browser is a real browser.** It
  auto-populates on open (no empty state) and adds a search box, a provider
  filter dropdown, capability filters (Tool calling / Vision / Image gen /
  Video gen / Free / Local), a sort dropdown (available first / cheapest /
  highest price / largest context), a result count line, per-row modality
  icons, pricing as "$X in / $Y out per 1M", and assign buttons —
  Orchestrator, Subagent, plus Image/Video buttons on models with those
  modalities.
- **Off-menu assignments keep their names.** A model assigned from the
  Model Browser that sits outside the curated role list now renders in the
  AI Models tab selects with its catalog label + "(via Model Browser)"
  instead of "(no longer offered)".

---

## [5.2.1] — 2026-07-04 — "Found It (wiki restore + voice fixes)"

### Fixed

- **The personal wiki is back.** Since the 2026-06-27 security refactor moved
  `WIKI_DIR` to `~/.friday/wiki`, the one-shot migration from the legacy
  `~/wiki` was guarded by `not WIKI_DIR.exists()` — always False on long-lived
  installs (auto-briefings had created the directory years of commits earlier)
  — so it silently no-oped and the user's real wiki (46 files: personal,
  people, journalism, identity, professional, research, ai-personality, meta)
  was orphaned while the UI showed only briefings. The migration is now a
  per-file, never-overwrite, idempotent merge that renames the legacy dir only
  after a fully successful copy. (The encrypted `vault/wiki-*` dirs are stale
  April snapshots from the predecessor app's vault — kept as a backup, never a
  serving path.)
- **"Open settings" opened the System workspace.** The spoken-navigation alias
  table mapped settings/preferences/config to `system`, and neither `settings`
  nor `marketplace` existed as navigation targets — while the voice tool's
  hard-coded workspace list predated both, so Gemini couldn't even name them.
  Settings and Marketplace are now first-class targets, "…menu" phrasing
  resolves, the tool's workspace list is derived from the resolver's alias
  table (single source of truth), and VOICE_DEMO.md stops teaching that
  settings lives inside System.
- **Voice turn-desync ("two parallel conversations").** Two renewal-seam bugs:
  the liveness watchdog could false-fire in the middle of a long user
  monologue (models that don't stream mid-turn transcription look "quiet"),
  forcing needless session renewals; and mic audio buffered during any
  renewal seam was burst-fed into the fresh session, making Gemini respond to
  utterances from half a minute earlier. The watchdog now fires only after
  speech has ENDED with no reply, and every renewed/resumed leg drains stale
  buffered audio before listening.

---

## [5.2.0] — 2026-07-04 — "Always Listening (voice continuity + creation tools)"

Voice mode you can trust through an hours-long conversation — and interrupt
mid-sentence — plus real slide-deck and website generation from chat or voice.

### Fixed

- **You can interrupt Friday again (speaker-mode barge-in).** Speaker mode
  runs Gemini Live with `ActivityHandling.NO_INTERRUPTION` (the echo-safety
  fix), which — per the Live API reference — means the model NEVER stops on
  its own; and the old client-side interrupt detector had been removed, so no
  layer implemented barge-in and Friday talked straight through the user. The
  bridge now detects deliberate talk-over itself (`LiveBargeDetector`: seeds a
  speaker-bleed RMS baseline from the quietest quartile of a per-response
  grace window — capped, so a user who talks through the grace can't raise
  the bar against themselves — then fires on ≥200 ms of speech above
  max(550, 3× the bleed)). The detection window tracks CLIENT PLAYBACK
  (`{type:'speaking'}` transitions from the browser, with a bytes÷48000
  estimate fallback for the PWA) because Gemini streams faster than
  real-time and users interrupt during playback, long after streaming ends.
  On fire: the rest of the turn's audio is swallowed server-side, the client
  is flushed via `{type:'interrupted'}`, and the in-flight generation is
  cancelled with the documented `client_content` interrupt. Escape is a
  deterministic manual barge hotkey; headphones mode keeps native
  `START_OF_ACTIVITY_INTERRUPTS`. Verified end-to-end against a live
  session: `interrupted` fired 0.19 s after talk-over began, zero straggler
  audio. An adversarial multi-agent review then hardened the seams: explicit
  client barges are trusted unconditionally (the Escape flush could close
  the play window ahead of its own barge frame), barge/tool/playback state
  resets at every session-leg start, the liveness watchdog stands down while
  a voice tool call runs, zombie WS handlers are generation-fenced away from
  the resume cache, stopping voice mid-reconnect can no longer leak a hot
  mic, and the phone PWA now actually silences scheduled audio on interrupt.

- **Hours-long Gemini Live voice sessions.** The "voice randomly goes silent
  while the mic still shows live" dropout is fixed on both ends. Server
  (`/ws/live`): a per-leg liveness watchdog force-renews the session when the
  user is audibly speaking but Gemini has gone quiet (the hung-receive case that
  used to freeze a call forever); GoAway now drains the in-flight sentence
  before renewing; renewal connects ride a retry ladder (handle → handle →
  fresh) instead of falling back to an amnesiac session; conversation state is
  hoisted above the model-fallback loop so a mid-call fallback no longer
  re-greets or drops the transcript; and the newest resumption handle is cached
  across WebSocket connections so a reconnecting browser resumes the SAME
  conversation. Client: voice mode now auto-reconnects with capped backoff when
  the socket dies unexpectedly, and a heartbeat-based stall watchdog force-cycles
  half-open sockets; a deliberate stop sends `{type:'bye'}` so the next session
  starts fresh.
- **Friday knows itself again.** `SELF.md` / `VOICE_DEMO.md` silently stopped
  loading after the `src/` restructure (they stayed at the repo root while core
  resolved them against the package root) — every system prompt shipped with
  ZERO self-knowledge. `_res_file()` now falls back to the repo root, and both
  docs were rewritten to match the real dock (Sites, Content, Trust,
  Marketplace, the full Studio suite) and the real creation tools.

### Added

- **`create_presentation` + `create_website`** — chat/voice agent tools, POST
  `/api/create/presentation` and `/api/create/website`, and
  `services/showcase_engine.py`. The routed text model writes a strict JSON
  spec; a deterministic template renders a polished, self-contained HTML
  artifact into the Studio gallery (deck: keyboard nav, speaker notes,
  print-to-PDF; site: multi-page hash routing, responsive, deploys anywhere).
  The LLM never writes HTML — same output quality every run, offline-safe.

### Docs

- Repo cleanup for release: internal working artifacts (storm reports, review
  logs, UI test results, competitive analyses, stale release-note files, the
  release plan) removed from the tree; design specs consolidated under
  `docs/`; `CHANGELOG.md` + GitHub Releases are now the single history.

---

## [5.1.1] — 2026-07-04 — "Gemini July-2026 model lineup"

Registry/catalog refresh against the live Gemini API surface (verified
against ai.google.dev model cards, pricing, and deprecation tables,
2026-07-04).

### Added

- **Gemini 3.5 Flash** (`gemini-3.5-flash`, stable), **Gemini 3.1 Pro**
  (`gemini-3.1-pro-preview`) and **Gemini 3.1 Flash-Lite**
  (`gemini-3.1-flash-lite`) in the google-gemini provider, catalog meta,
  and cost tables. Text roles stay `[]` until a google text dispatch
  exists in `routing/model_router.py` — same "never offer what can't
  dispatch" deal as Gemini 2.5 Pro. (Gemini 3.5 Pro is not yet in the
  public API as of 2026-07.)
- **Gemini Omni Flash** (`gemini-omni-flash` → `gemini-omni-flash-preview`):
  any-to-any conversational video generation/editing (I/O 2026), wired
  end-to-end — creative role in the registry, offline-fallback picker
  entry, pricing ($1.50/1M in; $17.50/1M video out ≈ $0.10/s of 720p),
  and a new Interactions-API dispatch branch in
  `creative_engine.generate_video()` (Omni renders synchronously — it is
  not a Veo-style long-running operation).
- Nano Banana Lite aliases → `gemini-3.1-flash-lite-image`.

### Fixed

- **Every Veo alias pointed at a dead endpoint**:
  `veo-3.0-*-generate-preview` shut down 2025-11-12, and the `veo-3.0` /
  `veo-2.0` GA models retired 2026-06-30. All aliases now resolve to the
  live `veo-3.1-*-generate-preview` family, with new `veo-3.1`,
  `veo-3.1-fast`, and `veo-3.1-lite` ids exposed.
- **Nano Banana Pro resolved to `gemini-3-pro-image-preview`** — shut
  down 2026-06-25 — now the stable `gemini-3-pro-image`. Nano Banana 2
  now maps to the actual NB2 model (`gemini-3.1-flash-image`) instead of
  the 2.5-era original, which stays reachable as plain `nano-banana`
  (sunsets 2026-10-02).
- New Gemini 3.x text ids added to `_FORBIDDEN_CREATIVE` so a text model
  can never be picked as a creative target.

---

## [5.1.0] — 2026-07-04 — "Model-Agnostic (provider layer P0–P2)"

The first three phases of `docs/MODEL_AGNOSTIC_PROVIDER_SPEC.md`: Friday routes
by REGISTRY, not by model-name guessing, speaks to any number of
OpenAI-compatible providers concurrently, and ships OpenRouter first-class.

### Added

- **Provider descriptor schema v2** (`routing/provider_descriptors.py`):
  adapter/type aliasing, auth env-var chains with aliases (`HF_TOKEN` /
  `HUGGINGFACE_API_KEY`), per-provider network/discovery/pricing/budget/feature
  blocks, and real validation with actionable errors — a bad
  `~/.friday/providers/*.json` is skipped, logged, and surfaced in
  `/api/health/full` + Settings instead of vanishing. YAML descriptors accepted.
- **Ten new built-in providers**: OpenRouter (enabled, first-class), Hugging
  Face Inference Providers router, Groq, Together, Fireworks, Mistral,
  DeepSeek, xAI, Perplexity, Cohere — one key press each in Settings.
- **OpenRouter first-class** (GAP-1): live model discovery from
  `/api/v1/models` (pricing, context, modalities, tool support, `:free`
  detection) cached with TTL + stale-while-revalidate; usage accounting
  (`usage.cost` is the authoritative billed figure in the ledger); server-side
  `models[]` fallback support; 429 `Retry-After` etiquette.
- **Model resolver** (`resolve_model`, GAP-4 fix): registry-first model→provider
  attribution. `meta-llama/llama-4-maverick:free` resolves to OpenRouter (the
  `:` no longer misroutes aggregator ids to Ollama), `gemma3:4b` to the local
  daemon, `claude-x:latest` (installed) to Ollama even with a cloud-looking
  name, `provider::model` is always explicit.
- **Multi-provider dispatch** (GAP-3 fix): `_call_openai(provider=…)` reads the
  endpoint, credentials, and headers from THAT provider's descriptor — Groq
  subagent + OpenRouter orchestrator + local Ollama vault in one session. The
  single-slot `model_routing.openai_*` settings keep working (legacy path).
- **Provider health measurement plane**: per-provider rolling p50/p95 latency,
  error rate, last success/failure, and a 5-failure circuit breaker with 60s
  cooldown — recorded from every adapter call, surfaced in
  `GET /api/providers(/health)`, and consulted by the generation ladders
  (a 'down' provider is tried last, never first).
- **Provider management API**: `POST /api/providers/validate` (dry-run),
  `PATCH /api/providers/<name>` (enable/disable/edit),
  `POST /api/providers/<name>/test` (latency + models_seen + optional 1-token
  ping), `POST /api/providers/<name>/models/refresh`, and
  `GET /api/models/search` across every provider's statics + discovery cache.
- **Settings → Providers tab**: health-dot provider list (measured, not
  assumed), encrypted key management, Test Connection, model-list refresh,
  per-provider spend today + budget cap display, Add Provider from templates
  (classification radio gated to private hosts), and a cross-provider Model
  Browser with free/tools/price metadata.
- **Pricing service** (`services/pricing.py`): discovery-cache → descriptor →
  dataset → v1-blended lookup; unknown price is `None`, never treated as $0.

### Security

- **Egress classification is registry-driven** (GAP-9 fix): the gate's local
  bypass now requires `classification: "local"` + a local-capable adapter + a
  loopback/RFC1918/`.local` base_url **re-verified at call time**. A descriptor
  *typed* `ollama` pointing at a remote URL is classified cloud and sealed; a
  genuine LAN vLLM/LM Studio finally gets the legit local bypass. The old
  `{"ollama", "local"}` set survives only as the fallback for non-registry
  family names.
- Descriptors are data, never secrets: raw `api_key` fields are rejected with a
  400 pointing at the encrypted key endpoint, and `extra_headers` cannot set
  `Authorization`.

---

## [5.0.1] — 2026-07-01 — "Super Agent (hardening)"

The post-release hardening pass — the full H1–H10 backlog from
`docs/FABLE5_INTEGRATION_STORM_REPORT.md`, closing the egress gaps the boundary
reviews missed and the fresh-user onboarding gaps the install audit found.

### Security

- **The agent tool loop and the user-text Gemini paths now pass the egress gate.**
  `tool_result` blocks pulled mid-loop are classified before re-send (a withheld
  result becomes an explanatory marker, never silent empty output); the creations,
  outreach-draft, QA-vision-intent, image-gen, and voice-TTS Gemini calls gate
  their user-authored text. Sensitive TTS routes to on-device voice instead of the
  cloud. Image/camera bytes remain the documented can't-text-classify caveat.

### Added

- **Onboarding vault passphrase (H4).** The first-run wizard offers an optional
  passphrase that arms AES-256-GCM before launch; Settings → Privacy shows an
  "Encrypt Vault" prompt whenever the vault isn't armed. New `/api/vault/passphrase`.
- **First-run Gemma pull (H5).** The wizard's hardware step offers a one-click
  `gemma3:4b` download when Ollama is running but the model isn't present, so the
  zero-key local path is real on first run.
- **Data rights UI (H6).** Settings → Privacy → "Your Data": export everything as
  a ZIP (`/api/data/export`) and a typed-ERASE-guarded wipe (`/api/data/erase`),
  mirroring `friday export` / `friday erase`.
- **SQLite migration helper (H10).** `services/db_util.py` adds forward-only
  additive column migration so upgrading users' DBs gain new columns instead of
  silently keeping the old schema.

### Changed

- **`friday setup` is the documented key path (H2).** README + INSTALLATION stop
  steering users at plaintext `start.bat` env vars and correct the docs that
  wrongly listed an Anthropic key as *required* — `gemma3:4b` is the zero-key default.
- **Voice model id is validated at startup and in Settings → Voice (H8).** A
  stale/renamed Gemini Live id (the opaque "voice broken" that looks like an auth
  error) is now caught up front.

### Fixed

- **Accessibility (H9):** a global keyboard `:focus-visible` ring (the holographic
  UI had none) and ARIA labels on icon-only close buttons.
- **Cross-platform tests (H7):** the hermetic-home conftest now redirects POSIX
  `HOME` too, not just Windows `USERPROFILE`.

---

## [5.0.0] — 2026-07-01 — "Super Agent"

The developer-tool → sovereign-consumer-product transformation. Adds a local,
closed-loop learning system, overnight memory consolidation, user modeling, an
editable personality file, a bundled zero-key local model, voice-first
onboarding, and messaging-channel bridges — every one of them local-first and
routed through the existing cLaws governance + egress gate.

### Added

- **Learning Loop Engine** (`services/learning_loop.py`). Observes task outcomes,
  mines successful (task-type, tool-strategy) patterns into text *heuristics*,
  scores them with a Wilson lower bound blended with satisfaction, and promotes
  the best into the system prompt. Local-only, SQLite-backed (`learning.db`),
  bounded by `max_active_skills`. **Skills are advisory text, never executable
  code** — no new tool surface. Weekly `learning_epoch` scheduler job. API under
  `/api/learning/*`.
- **Memory Dreaming** (`services/memory_dreaming.py`). Nightly (03:00) local
  consolidation: reviews the day's ChromaDB conversation turns, extracts topics
  and durable facts (preferences/decisions/bio), feeds high-confidence facts to
  the user model, tags noise, and writes `~/.friday/dreams/<day>.md`. Never
  touches cloud. API under `/api/memory/dream*`.
- **User Modeling** (`services/user_model.py`). Tracks communication style
  (formality/verbosity), per-domain expertise, and workflow patterns from each
  turn; injects a compact **TIER_1** `== USER MODEL ==` block into every system
  prompt. SQLite-backed with a GDPR-style `forget()`. API under `/api/user-model/*`.
- **SOUL.md personality config** (`services/soul.py`). Friday's personality now
  lives in a user-editable `~/.friday/SOUL.md` (seeded from the shipped default,
  versioned in `soul_history/`). `core._load_agent_personality()` reads it first.
  API under `/api/soul*`.
- **Bundled Gemma / no-API-key mode.** Default local model is now **`gemma3:4b`**
  (Google's open Gemma 3 4B-IT, ~8 GB RAM). `install.{sh,ps1,bat}` auto-install
  Ollama and pull the model (best-effort, skippable via `FRIDAY_SKIP_MODEL=1`).
  Chat works fully offline with zero cloud keys; creative/voice degrade
  gracefully. `friday doctor` / `friday health` now report Ollama + Gemma + a
  "no-key mode ready" status.
- **Voice-First Onboarding** (`services/onboarding.py`). First-run state machine
  — greet → name → voice test → optional keys → Ed25519 identity → SOUL.md —
  spoken via the local voice engine (no cloud key required). API under
  `/api/onboarding/*`.
- **Channel bridges** (`services/channels/`). Discord (`discord.py`, graceful
  no-op when absent) and Telegram (stdlib, zero-dep) bots. Every inbound message
  runs the shared agent loop; every reply passes the **egress gate** before send.
  Disabled by default, allowlist-gated, bot tokens in the credential store. API
  under `/api/channels/*`.

### Fixed

- **Blueprint auto-discovery registered zero routes in two shipping paths** —
  the entire API 404'd. (1) The repo-root `server.py` shim `exec()`s the package
  server, so `__file__`-relative discovery globbed a nonexistent `<repo>/routes`.
  (2) The packaged **AgentFriday.exe** never bundled `routes/*` at all: the spec's
  `collect_submodules('agent_friday')` silently returned `[]` because `src` wasn't
  on `sys.path` at spec-eval time, and the dynamically-imported route modules were
  invisible to PyInstaller's static analysis. Fixed by enumerating routes via
  `pkgutil` with an explicit `ROUTE_MODULES` fallback for the frozen build
  (drift-guarded by `tests/api/test_blueprint_discovery.py`) and adding `src` to
  the spec's path so the route modules are bundled. Verified: `python server.py`
  and the frozen `.exe` both serve 200 on every endpoint.
- **The entire UI silently failed to mount** — an unclosed
  `<div style={{display:'none'}}>` in `FamilyWS` left the component's outer JSX
  element open, so the single inline Babel script died at parse time: blank
  screen, bare holo scene, empty console. Also repaired ~1,390 double-encoded
  UTF-8 (mojibake) runs and a stray BOM in `ui_parts/app.html` (dock emoji,
  comment rules, license-picker hints).
- **JSX is now precompiled at build time.** `build_ui.py` compiles the app
  bundle with `@babel/standalone` under node when available (in-browser Babel
  remains as fallback). Cold-load `DOMContentLoaded` dropped from ~17.5 s to
  ~0.35 s, and index.html no longer needs the Babel CDN at runtime.
- **UI libraries are vendored — the shell and holo scene now load offline.**
  React 18.3.1, ReactDOM, marked 9.1.6, highlight.js 11.9.0 (+theme CSS), and
  Three.js r128 with its six post-processing files moved from unpkg/jsdelivr/
  cdnjs into `static/vendor/`, each verified against the SRI hash the old CDN
  tag pinned. Remaining external fetches are Google Fonts and the optional
  MediaPipe camera libs, both of which degrade gracefully.
- **`/api/vault/status` was never implemented** — the Settings → Privacy
  "Sovereign Vault" card 404'd forever (silently). New endpoint reports live
  encryption state (AES-256-GCM vs None), entry/encrypted counts, and a
  `locked` flag (encrypted blobs on disk with no derivable key).
- **`/static/*` and the dock icon set never served.** `send_from_directory`
  with a relative path resolves against Flask's `root_path` (inside the
  package since the src/ move), so `/static/favicon.ico` 404'd — and
  `/assets/*` had no route at all, so the dock's designed SVG icon set
  (`assets/icons/*.svg`) had never once rendered; the emoji fallback always
  showed. Both routes now anchor to the process cwd like `serve_ui` does, and
  the two missing icons (`marketplace.svg`, `settings.svg`) were drawn in the
  set's neon line-art style. The dock now ships its intended icons.
- **`friday doctor` misdiagnosed keys and crashed on legacy consoles** — it
  now reads API keys from `start.bat`-style launch scripts (same precedence as
  the server's env bootstrap) and degrades ✓/✗ glyphs instead of dying with
  `UnicodeEncodeError` on cp1252 consoles.

### Notes

- All v5 subsystems are **local-only** and pass through cLaws governance and the
  egress gate. Nothing new introduces a default cloud dependency.
- 3,162 tests pass (64 new). See `docs/SUPER_AGENT_BUILD_SPEC.md` for the full
  design and `docs/RELEASE_NOTES_v5.0.md` for the release summary. (The suite
  grew to 3,629 once the v5 test files were committed — see [5.0.1].)

---

## [Unreleased]

### Removed

- **Removed the personal Co-Parent/OFW workspace and `ofw_monitor` skill from the
  public release.** The co-parenting platform monitor, its custody-calendar
  tracking, and the related draft mode were personal to the original author and
  are not part of the open-source distribution.

---

## [4.5.0] — 2026-06-06

The public-release hardening pass. Prunes the surface area down to the core
general-purpose workspaces, makes the powerful-but-risky subsystems opt-in, and
strips the founder's personal content out of source so a fresh user starts clean.

### Removed

- **Stub workspaces.** `FinanceWS` and `HealthWS` (vault-gated placeholders with
  no real integrations) are removed, UI + routes (`/api/finance/*`,
  `/api/health/*`). They can return later as Seeds/plugins.
- **Personal Co-Parent workspace, removed entirely.** The dedicated workspace
  component, its API routes, the platform message loader + notification monitor,
  the related calendar keywords, the message-classification lane, and its draft
  mode are all gone. (Sensitive personal data was always gitignored and never
  shipped.)
- **Redundant dock entries.** `FamilyWS`, `TrustWS` (trust is now a tab in
  News + Contacts), and `StudioWS` (functions live in Dev Studio and the Sites
  workspace) are no longer separate dock entries.
- **Content workspace pipeline.** `ContentWS` and its kanban endpoints
  (`/api/content/pipeline|idea|draft`) are removed; writing is consolidated into
  the Draft workspace (reachable via News → Share to Draft) and the chat pipeline.
  The draft library serving routes (`/api/content/drafts*`) stay.
- **FutureSpeak business pipeline.** The personal-CRM endpoints
  (`/api/futurespeak/{pipeline,revenue,legal,assets}`) and their UI tabs are
  removed. The workspace remains as a general-purpose **Sites** portfolio/deploy
  manager (projects + scan + scaffold).

### Changed

- **Dock pruned to 10 core icons:** Home, News, Messages, Calendar, Career, Code,
  Wiki, Contacts, Sites, System. (Settings remains the gear-button slide-out.)
- **Computer Control is now opt-in.** New setting `computer_control_enabled`
  defaults to **false**. The feature is surfaced under Settings as **Experimental**
  with a clear warning; the Ring-3 runtime grant and the kill switch are unchanged,
  and the grant endpoint now refuses unless the feature is enabled.
- **SkillOpt nightly job disabled.** The 3:30 AM auto-research job is commented
  out for general release (marginal value while the skill library is small); the
  infrastructure stays for when there are 50+ skills.
- **Voice debug logging gated.** Per-chunk voice logs are off by default — client
  logs behind `window.FRIDAY_VOICE_DEBUG`, server `_vlog` behind the
  `FRIDAY_VOICE_DEBUG` env var.
- **De-personalized for new users.** Hardcoded author-specific content (name,
  family, bio, local news feeds, personal keyword lanes, and seeded personal
  portfolio sites) has been replaced with generic, settings-driven defaults across
  the news editor, draft, and message subsystems.

### Security

- **Vault encryption-at-rest, wired into the running app.** The `vault_crypto.py`
  primitives (AES-256-GCM + Argon2id, already present and tested) are now actually
  used by `server.py`. A vault key is derived once from `FRIDAY_PASSWORD` at startup
  (`_get_vault_key`); sensitive files (finance, health, and
  `vault/{legal,finances,family}`) are transparently encrypted on write
  and decrypted on read (`_vault_write_text` / `_vault_read_text`); and any existing
  plaintext is encrypted in place on first boot (`_migrate_vault_plaintext`, verifies
  a decrypt round-trip before replacing each file). With no `FRIDAY_PASSWORD` set the
  vault stays plaintext (logged at startup) — behaviour is unchanged for keyless
  local-dev. New tests: `tests/test_vault_at_rest.py`. This closes the gap documented
  in `docs/SITE_VS_REPO_DISCREPANCIES.md` (vault was previously plaintext at rest).

---

## [4.4.0] — 2026-06-06

The trust-and-portability release. Hardens authentication, adds a third
(OpenAI-compatible) provider with a full agentic tool loop, gates every tool
call behind a sandbox policy, ships a portable SKILL.md registry, and closes the
loop on skill learning so real chat usage feeds the optimizer.

### Added

- **OpenAI-compatible provider** — A third cloud provider alongside Anthropic and
  Ollama. Opt-in via `model_routing.cloud_provider = "openai"` plus
  `openai_base_url` (defaults to OpenRouter), `openai_model`, and `openai_api_key`
  (or env `OPENAI_API_KEY` / `OPENROUTER_API_KEY`). Unlocks OpenRouter's hundreds
  of models and any `/v1` endpoint. Ships a **full agentic tool loop** at parity
  with the Anthropic path. Vault / sensitive requests still never route here —
  they stay local or on Anthropic.
- **Portable skill registry** (`skill_registry.py`) — A portable "SKILL.md
  folder" format: YAML frontmatter plus a markdown body, agentskills.io-compatible.
  Import/export across folder, zip, legacy-YAML, and OpenClaw formats. New HTTP
  routes `/api/skills`, `/api/skills/import`, `/api/skills/<name>/export`, and
  `/api/skillopt/state`. Matched skills are injected into the system prompt each
  turn, so newly learned skills take effect without a restart.
- **Closed-loop learning** (`skill_capture.py`) — Captures turn trajectories to
  CognitiveMemory and JSONL, feeds real chat usage into the SkillOpt optimizer,
  and runs a nightly `skillopt-nightly` auto-research job. Connects the
  previously-dormant SkillOpt machinery to live usage.

### Security

- **Auth hardening** — The session secret is now a persisted random value
  (`~/.friday/secret_key`, mode `0600`) instead of a hardcoded default. Credential
  checks are constant-time (`hmac.compare_digest`). A per-IP login throttle caps
  attempts at 8 per 5 minutes. New env toggles: `FRIDAY_TRUST_LOOPBACK` (default
  on; set `0` to require login even on localhost), `FRIDAY_WS_TOKEN` (optional
  token gating the `/ws/live` voice WebSocket), and `FRIDAY_COOKIE_SECURE` (Secure
  cookie for HTTPS / tunnel). Session cookies are now `SameSite=Lax` and
  `HttpOnly`.
- **Tool-execution sandbox** — Every agent tool call passes through a policy gate
  controlled by `FRIDAY_SANDBOX_MODE` (`off` / `confine` [default] / `strict`)
  and `FRIDAY_SANDBOX_ROOT`. `confine` keeps `write_file` inside a root (default
  `HOME`) and runs `run_command` against a destructive-command blocklist;
  `strict` additionally allowlists commands.

### Fixed

- **Command injection in the vibe-code launcher** — Closed a command-injection
  hole in the vibe-code terminal launcher.

---

## [v4.3] — 2026-05-28

The self-evolving interface release. Adds Liquid UI and the Seeds & Gardens
workspace architecture.

### Liquid UI

- **`liquid_ui.py`** — Friday's self-evolving interface engine.
  - `LiquidUIRequest` captures intent — explicit ("I wish I could…") or
    behavioral (workspace ping-pong, repeated filters, error loops,
    dwell-time collapse).
  - `FeatureSpecGenerator` produces structured specs with complexity
    tier classification: trivial (<1m, auto), simple (1–5m), medium
    (5–30m), complex (30–120m), epic (2h+).
  - `LiquidUIBuilder` writes React + backend artifacts to
    `~/.friday/liquid_ui/features/<id>/`, snapshots state, emits a
    hot-reload token. Source tree stays pristine.
  - `SuggestEngine` runs four behavioral detectors and surfaces
    proactive `Suggestion` objects with confidence scores.
  - `SnapshotManager` — HMAC-irrelevant but path-stable rollback. Every
    change snapshots touched files; Ctrl+Z eligibility = within 30s.
    60-day retention; Settings exposes the full chain.
  - Every Liquid UI feature is also a SkillOpt skill — usage events
    update accuracy / satisfaction / completeness.
- **`ui_parts/liquid_ui_panel.html`** — React management panel with
  build queue, feature cards, proactive suggestions, snapshot history,
  ✨ Wish modal.

### Workspace architecture

- README documents the **Seeds & Gardens** model and the new stock
  workspace layout:
  - Personal: Messages (unified inbox + outbound drafts), Family, Health
  - Professional: Career, Finances, Business, News
  - Creative: Studio (was "Content"; "Draft" rolls into Messages)
  - Infrastructure: Wiki, Trust, Code, Skills Observatory
  - Dashboard home with KPI cards, today's agenda, activity feed, alerts
  - ➕ Add Garden gallery: Smart Home, Travel, Education, Legal,
    Fitness, Entertainment, Real Estate, Pets …
- Design principles: pick 4–5 workspaces at setup; reorder by frequency;
  auto-minimize after 30 days unused; every menu has ✨ Suggest +
  right-click "Improve this workspace"; complete rollback via Liquid UI
  snapshots.

---

## [v4.2] — 2026-05-28

Self-improving skills release. Adds a SkillOpt-inspired engine, two
production skills, and a holographic Observatory UI.

### Skills system

- **`skillopt_engine.py`** — Versioned skills with composite scoring,
  validation gate (5% regression tolerance), and a Karpathy-style
  AutoResearch loop that proposes patches when rolling scores drop ≥ 10%
  below the all-time best. JSONL execution log per skill; `best_skill.md`
  artifact per champion. CLI: `python skillopt_engine.py status`.
- **`skills/job_scanner/`** — Autonomous LinkedIn discovery every 4h
  during active hours. Round-robin keyword rotation, score-weighted
  notifications (title × 3, salary × 2, remote × 2, skills × 2,
  seniority × 1.5, company × 1), dedup against `JobTracker`, daily cap
  of 6 priority alerts.
- **`skills/application_engine/`** — Full-cycle: intel → resume tailor →
  cover letter → ATS form plan → submission → tracker log. Epsilon-greedy
  resume A/B bandit. Quality gates: salary floor ($150K), confirmation
  above $300K, dedup-apply, brand-voice ≥ 0.75, cover-letter word count
  bounds. Greenhouse / Lever / Workable / SmartRecruiters field maps.
- **`data/job_tracker_schema.py`** — `JobListing`, `ApplicationRecord`,
  `JobTracker` dataclasses with atomic JSON writes, pipeline status
  tracking (discovered → triaged → applied → screening → interview →
  offer → closed/rejected/withdrawn), and 30-day response-rate analytics.
- **`notifications.py`** — Friday-Chat-ready templates: priority job
  alerts (🔴), daily digests (🟡), weekly reports (📊), interview
  detection (📞), skill improvement announcements (🧠), skill regression
  notes.

### UI

- **Skills Observatory** (`ui_parts/skills_observatory.html`) — React +
  Recharts workspace. Skill cards with sparkline trends, version history
  with inline diff, execution scatter plot with reference lines, active
  experiments panel, research log, champion-vs-challenger comparison.
  Holographic dark theme (`#0a0e1a` base, cyan `#00d4ff`, blue `#3b82f6`,
  magenta `#ff0080` accents, glass cards).

### Setup & onboarding

- **Existing-user detection** — Setup wizard and `friday` CLI now skip
  re-setup when any of these are present: `.setup_complete` marker,
  API keys in config or environment, or a generated `start.bat`. Use
  `setup_wizard.py --force` to redo setup from scratch.
- **Branded onboarding banner** — New users see the FutureSpeak.AI boxed
  ASCII art banner on first run.

### Cleanup & hygiene

- Removed one-shot scripts (`merge_gemini.py`, `patch_career.py`,
  `write_scene.py`), base64 chunk fragments (`chunks/`, `combine.b64`,
  `p0.b64`, `temp_b64.txt`), legacy PowerShell decoders, and stale
  install logs.
- Untracked `.asimovs-mind/vault/bridge-token` and `port` — these are
  per-machine secrets and should never have been in git history.
- Strengthened `.gitignore`: now covers `.env*`, `.claude/`, `*.pyc`,
  `settings.json`, `credentials.json`, skill-state JSONs, all editor
  backup variants.

---

## [v4.1] — 2026-05-26

Major feature release. Built in a single focused session. Everything below was designed, implemented, and shipped today.

### Governance & Security

- **Governance gate with privilege rings** — Every tool call passes through `_evaluate_policy()` before execution. Four rings (0=read-only, 1=local-write, 2=network, 3=OS-control) with distinct permission requirements.
- **Decision BOM audit chain** — HMAC-SHA256 signed decision records appended to `~/.friday/vault/decision-bom.jsonl`. Tamper-evident; covers every allow/deny decision with timestamp, tool, ring, policy, reason, and signature.
- **Computer control with kill switch** — Ring 3 (`move_mouse`, `click`, `type_text`, `press_key`, `screenshot`, `scroll`) enabled by user toggle. Rate-limited to 20 actions/second. Blinking red indicator in top bar. Kill switch button always visible in UI for instant suspension.
- **Blocked operations list** — Hard-coded deny list for destructive shell commands regardless of ring level: `rm`, `del`, `rmdir /s`, `format`, `shutdown`, `reg delete`, `taskkill`, and others.

### Voice Mode

- **Live WebSocket audio** — `/ws/live` endpoint connects to Gemini 3.1 Flash Live Preview for real-time bidirectional audio. Mic button in UI opens the WebSocket session.
- **Chat transcript persistence** — Voice conversations are transcribed and saved to chat history alongside text conversations, with `[voice]` provenance tag.
- **Context-log persistence** — Voice turns logged to `~/.friday/vault/context-log/` like text turns.
- **Adaptive voice/text mode** — UI auto-detects when a voice session is active and switches TTS response format (1–3 sentences, no markdown) for the Claude system prompt.
- **Audio device selector** — Settings panel shows available audio input/output devices, lets user switch without restart.
- **Fixed audio extraction path** — Resolved `chunk.data` vs `part.inline_data.data` extraction bug that caused silent audio responses.
- **Fixed Gemini Live API version** — Corrected `http_options` to use `v1alpha` (was using wrong version causing 404s).

### Chat UI

- **Rich markdown rendering** — Chat responses render full GitHub-flavored markdown: headers, bold, italic, inline code, fenced code blocks with syntax highlighting, bulleted and numbered lists, tables, blockquotes.
- **Code block copy button** — Each fenced code block has a copy-to-clipboard button in the top-right corner.
- **Message pinning** — Pin any chat message; pinned messages are excluded from the 30-day retention purge.
- **Chat history search** — Search bar filters chat history by message content.
- **Source citations** — Chat responses from tool-augmented turns show a "sources" section with links.

### Model Selector

- **Model selector UI** — Top bar shows model pills (orchestrator + subagent + creative). Click any pill to change model without restarting.
- **All Claude 4.x models** — Claude Opus 4.7, Sonnet 4.6, Haiku 4.5 available.
- **Gemini models** — Gemini 2.5 Flash, 2.0 Flash, 1.5 Pro, Lyria, Veo 2.0.

### Tool Expansion (12 → 30 tools)

**New tools added:**
- `query_calendar` — Check upcoming calendar events
- `get_career_pipeline` — Read job search status from wiki
- `get_briefing` — Fetch most recent daily briefing
- `learn_skill` — Create/modify/delete/list skill YAML workflows in `~/.friday/skills/`
- `search_email` — Search Gmail via connector
- `draft_email` — Draft email via connector
- `open_url` — Launch URL in Chrome
- `install_package` — pip/npm package installer
- `move_mouse` — Ring 3: move cursor
- `click` — Ring 3: mouse click
- `type_text` — Ring 3: keyboard injection
- `press_key` — Ring 3: key/chord press
- `screenshot` — Ring 3: screen capture (base64 PNG)
- `scroll` — Ring 3: mouse wheel
- `correct_wiki` — Global find-replace across entire wiki + vault JSONs
- `propose_wiki_update` — Queue wiki edit for user approval
- `describe_screenshot` — Gemini vision describes a screenshot
- `analyze_file` — Gemini multimodal file analysis

### Quick Draft with Background Tasks

- **`spawn_task` tool** — Agent can delegate deep work to a background thread with full tool access. Task runs in a Claude agent context; results appear in Task Tray.
- **Task Tray** — Bell-icon dropdown in top bar shows all active/completed tasks with live status, elapsed time, spinner, and collapsible log lines.
- **Cancel tasks** — Stop button kills a running background task.
- **Tool trace** — Each task stores a trace of every tool call it made, visible in the task detail panel.

### Holographic Scene

- **Scene persistence** — Preferred scene index stored in `~/.friday/personality.json`. Survives server restarts.
- **`POST /api/evolution`** — Set `{ preferred_scene_index: N }` to pin a scene; `null` to return to auto-rotation.
- **Terminal flash fixes** — Eliminated flash/flicker on scene transitions by fixing animation interpolation timing.
- **13 named structures** — Genesis Lattice, Sacred Sphere, Shannon Network, Geodesic Cathedral, Lovelace Astrolabe, Von Neumann Tesseract, Dirac Probability, Mandelbrot Set, Turing Möbius, Ocean of Light, Fibonacci Nerve, Transcendence, Giga Earth (Rez).

### Setup Wizard

- **CLI setup wizard** (`setup_wizard.py`) — Interactive rich terminal UI for first-run configuration. Covers agent name, orchestrator, creative engine, API keys, voice, scene selection, and writes `start.bat`.
- **Web setup wizard** — Glassmorphism overlay shown on first visit if `~/.friday/.setup_complete` is missing. Now includes API key entry step and scene picker (was previously just name/model/voice).
- **API key hot-reload** — Keys entered in the web wizard are live-loaded into the running process without restart.
- **`/api/setup/status`** — Returns `{ initialized: bool }` based on presence of `~/.friday/.setup_complete`.
- **`/api/setup/complete`** — Accepts all wizard choices including `anthropic_api_key`, `gemini_api_key`, `preferred_scene_index`.

### Privacy Shield

- **PII auto-redaction** — SSN, credit cards, phone numbers, email addresses, street addresses scrubbed before reaching Claude.
- **Smart tagging mode** — PII tagged as `[PII:type:hash]` with in-memory rehydration table; model never sees raw values, user sees restored responses.
- **Custom watchlist** — `~/.friday/privacy_shield.json` for project codenames, client names, and other sensitive tokens.
- **User email bypass** — Addresses in `user_email` and `owner_identities` settings pass through clean.

### Smart Context Loader

- **Keyword-routed wiki loading** — Message analysis routes relevant wiki sections into context automatically:
  - Career/job/resume → `~/wiki/professional/`
  - Family/kids/custody → `~/wiki/family/` + `~/wiki/legal/`
  - Named people → trust graph lookup → person's wiki file
  - Finance/budget → `~/wiki/finance/`
  - Health/medication → `~/wiki/health/`
- **Project context files** — Drop `.friday-context.md` or `AGENTS.md` in any project directory; automatically injected when messaging from that directory (Hermes-inspired).
- **200KB context cap** — Total context trimmed to prevent token overruns.

### Other Improvements

- **Append-only context logging** — Daily JSONL files in `~/.friday/vault/context-log/`, configurable retention.
- **Off-record mode** — Toggle to suspend chat logging without disabling tool-call logging.
- **Trajectory compression** — When chat history exceeds 2MB, old turns are summarized via a Claude call.
- **Wiki proposal workflow** — All agent-initiated wiki edits queue for user approval. Bell icon shows pending count.
- **Wiki global search** — Full-text search across all `.md` and `.txt` files in `~/wiki/`.
- **Epistemic scoring** — `/api/epistemic` endpoint scores independence across calibration, sourcing, uncertainty acknowledgment, bias resistance, and correction rate.
- **Personality traits** — `/api/personality` endpoint exposes maturity, curiosity, skepticism, humor, loyalty, directness, empathy, contrarianism.
- **Vibe Code terminals** — `/api/vibe-code/` endpoints spawn Claude tasks in new CMD windows with configurable workflow presets.
- **Camera mode** — Live video PIP with frame capture and auto-describe via Gemini vision.

---

## [v4.0] — 2026-04-14

### Added
- Initial Flask server with Anthropic Claude integration
- Personal wiki read/write with `read_wiki`, `search_wiki`, `propose_wiki_update`
- Three.js holographic scene (6 initial structures)
- Chat with persistent history (30-day retention, 500-message cap)
- PII scrubbing (basic SSN + CC patterns)
- Background task runner (first implementation)
- Trust graph integration
- Career ops tracker (parses `application-log.md`)
- Gemini creative endpoints: image, music, code art, poem, video
- TTS with 5 Gemini voice personas
- Settings panel with model selection, temperature, response length
- Daily briefing generation and serving
- Finance, health, vehicle workspace endpoints (template data)
- Countdowns endpoint
- Wiki pending approval workflow (first implementation)
- Mobile responsive layout

---

*Older history is available in git log.*
