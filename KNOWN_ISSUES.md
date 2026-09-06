# Known Issues

**As of 2026-09-06, for v5.13.0.**

This file lists what is currently broken, unverified, or deliberately limited in
a way you may hit. Resolved problems move to the [CHANGELOG](CHANGELOG.md) when
they are fixed; the engineering lessons behind several of them are in
[docs/development/failure-classes.md](docs/development/failure-classes.md).
If something here is wrong, or you hit something that is not here, please open
an issue.

---

## 1. Upgrading from older versions

- **Upgrades performed with the 5.6.5 installer deleted the vault passphrase**
  when it lived only in `start.bat` inside the app folder. Fixed in 5.6.6;
  since 5.7.0 the passphrase lives in the OS keychain and a DPAPI-wrapped file
  under `~/.friday/security/`, which no installer touches. There is no recovery
  for data encrypted under a passphrase you no longer have. Whatever version you
  run, `friday vault-setup` stores the keychain copy.
- **Installers 5.6.0–5.6.4 did not replace application files on an in-place
  upgrade** while still reporting the new version. Fixed in 5.6.5. Running the
  current installer over the top repairs any prior install and keeps everything
  under `~/.friday`. If you connected a credentialed MCP server while on an
  affected install, rotate that credential: it was stored in plaintext.

## 2. Open defects

- **Friday does not always search when asked to search.** A request phrased as
  "search the web for …" can return a confident answer with an empty tool trace
  and citations for pages that were never fetched. The provenance layer renders
  such citations inert, which treats the symptom. Cause not established.
- **A partial settings write still resets most blocks.** `_save_settings`
  deep-merges `capability_routing`, `model_routing` and `content`; every other
  top-level block in `settings.json` is replaced wholesale by a partial write.
- **Settings keys absent from `DEFAULT_SETTINGS` are silently discarded on
  save**, and the API reports success. A static guard now catches keys the UI
  writes; keys written by other paths are not covered.
- **Some settings controls persist a value that nothing consumes**:
  `stream_responses`, `auto_open_chat`, `compact_mode`, `startup_workspace`.
  Each needs a product decision about what it should do (recorded in
  [docs/decisions/2026-09-04-five-dead-settings.md](docs/decisions/2026-09-04-five-dead-settings.md)).
- **Local models without native tool calling cannot act.** A local model with
  native tool calling (the default Gemma 4 ladder) uses tools fully offline.
  The `function_manager` seat that would let a smaller specialist handle tool
  selection for models that lack it is declared but wired to nothing.
- **Chain retry has a budget race**: `_retry_chain_step` spawns the retry and
  only then records the retry count, so a step that fails before that write
  reads a count of zero and can retry past its budget. (The false-complete
  half of this entry is fixed: an exhausted retry now flips the status.)
- **The worker's quality evaluator has never run.** It imports
  `_vault_local_only` from `agent_friday.core`, a name that lives in
  `services/model_router.py`, so the import fails and the evaluator fails
  closed on every task. The task journal records this as a `decision` at
  point `evaluate` with the ImportError as the reason. Repairing it
  re-enables one cloud call per background task, so it awaits the
  maintainer's decision rather than a silent fix.
- **Chain seat overrides are advisory**, not enforced against the capability
  router.
- **`print()` output from the chat path may not reach the log** when the server
  is launched from the tray. Use the structured logger for anything you need
  later.
- **`ui_parts/app.html` drifts from `index.html`.** The served file is the
  source of truth and is a strict superset; the build tool refuses to
  regenerate `index.html` in a way that drops components. See
  [docs/development/ui-build.md](docs/development/ui-build.md).
- **Seat contention on 12 GB cards.** A resident 12B brain leaves roughly
  600 MiB, so a second GPU seat or local image generation may fail to allocate.

## 3. Deliberate behaviour that can surprise

- **`cloud_only` does not override a local model you pick yourself.** It stops
  Friday choosing a local model on her own and stops fallback to one; the
  model picker is authoritative.
- **One key per machine, and no notion of a borrowed key.** A provider key
  entered on a machine is used there until replaced; nothing records whose
  account it belongs to, and revoking it revokes every install using it.
  Setup says this at the point the key is entered. If you set someone else up,
  give them their own key.
- **Presidio runs in shadow mode and reports as inactive.** Measured against
  the built-in classifier it was weaker on real PII and escalated ordinary
  prose, so it changes no decision unless `FRIDAY_PRESIDIO_ENFORCE=1` is set.
  See the [threat model](docs/security/threat-model.md).

## 4. Unverified

Listed separately from "broken". These are not claims that things work.

- **No clean-machine install has been performed** for the current release on a
  machine that has never seen this code.
- **Local voice end to end is unverified**: nobody has spoken to Friday,
  received a spoken answer, and confirmed from the egress log that nothing left
  the machine.
- **CPU-only inference throughput is unmeasured** for any generation model.
- **8 GB VRAM is unverified.** The hardware fixture representing it carries
  measurements copied from a 12 GB card.
- **The KV-cache slope the seat planner rests on is inferred** from two anchors
  on two backends; a direct measurement on one model was an order of magnitude
  away.
- **A stack-overflow crash of the server on 2026-09-04 has no confirmed cause.**
  The crash dump caught the news archiver mid-shutdown with a worker inside a
  feed parse; the leading hypothesis is a malformed feed reaching native XML
  parsing. The feed timeout that let a worker wedge is fixed; the feed itself
  was not identified. The hang watchdog's timeout backstop did not fire.

## 5. Things that leave your machine

There is no telemetry, analytics, crash reporting, phone-home, or license check
in this codebase. Four things do leave on a default install with no keys:

1. A TCP connect to `dns.google:443` every 30 seconds (fallbacks `8.8.8.8`,
   `1.1.1.1`) as a connectivity probe. No payload; it reveals your IP and uptime.
2. HTTP GETs to about 40 news feeds every 5 minutes by default.
3. `fonts.googleapis.com` on every launch, from `index.html`.
4. Three MediaPipe bundles from `cdn.jsdelivr.net`. The script tags carry
   SRI hashes; the wasm and model assets MediaPipe fetches at runtime cannot.

Items 3 and 4 contradict the local-first posture; self-hosting them is not yet
done. Every one of these can be disabled: see
[docs/user-guide/background-network.md](docs/user-guide/background-network.md).

The egress gate covers Anthropic, Gemini, every OpenAI-compatible provider
including OpenRouter, web search queries, Firecrawl search and page fetches,
and media sent for inspection. Outside the guarantee as written: ElevenLabs
text-to-speech text, Google Calendar event content written through the
housekeeper tools, and the content hash sent to `freetsa.org` for timestamping.

## 6. Platform limits

Agent Friday runs on Windows 10/11 with an NVIDIA GPU. macOS and Linux run the
server, the web UI, cloud providers, and local chat through Ollama, without
the system tray, the residency layer, GPU-aware seat planning, or OS-protected
credential storage.

- On non-Windows systems provider keys fall back to plaintext unless a vault
  passphrase is set.
- Apple Silicon is refused by the residency planner: no MLX or Metal backend.
- AMD GPUs are not detected on any platform; `nvidia-smi` is the only probe.
- 16 GB of system RAM is the floor. At 8 GB the budget rule leaves about
  1.2 GB for a resident model, which no rung on the ladder fits, so every
  model seat is refused.

## 7. Security posture

- **Keys written by the `friday setup` wizard are plaintext** in
  `~/.friday/settings.json`, `~/.friday/config.yaml` and the checkout's
  `start.bat`. The in-app Settings → Providers path uses the encrypted store.
  See [SECURITY.md](SECURITY.md).
- **Linux OS-mode has no durable secret store.** `vault_passphrase.store()`
  and `credential_store.protect()` fail closed under `FRIDAY_OS_MODE=1`, which
  is correct, but nothing can persist a passphrase there yet: `keyring` is an
  optional extra and no Secret Service provider is in the planned image. The
  supported path today is `FRIDAY_VAULT_PASSPHRASE` in the environment.
- **`web_safety.py`, the SSRF guard, has no tests of its own.**
- **Dependencies are declared with `>=` floors.** A `uv.lock` is committed
  but nothing consumes it: CI, the Windows installer and the documented
  install paths all resolve fresh from `pyproject.toml` or the installer's
  requirement files, and the lock is behind the current `pyproject.toml`.
  Its only live use is Dependabot alerting.
- **The 5.12.0 and 5.13.0 installer zips include files that are not part of
  the repository** (workspace files from the build machine, copied from the
  working tree rather than from a clean checkout). They are inert on a user's
  machine but should not be there. The build now refuses any payload that is
  not the committed tree; replacing the published assets is a maintainer
  action.

## 8. Licensing

Facts as of 2026-09-06, from package metadata and the shipped 5.13.0 zip.
None of these is a problem for a source checkout; all of them need a
decision before the Windows installer is called a clean binary distribution.

- The installer's default tiers `pip install` several copyleft packages on
  the user's machine: `piper-tts` (GPL-3.0-or-later since 1.3; the `>=1.2`
  floor crossed over from MIT), `mutagen` (GPL-2.0-or-later; default via the
  recommended tier, not only the `provenance` extra), and `imageio-ffmpeg`,
  whose bundled `ffmpeg` binary is a **GPLv3** build (`--enable-gpl
  --enable-version3`, libx264/libx265). `pynput` and `pystray` (LGPL-3.0) are
  in the core tier.
- Two wheels are physically inside the zip's wheelhouse with GPLv3+ metadata
  and no or contradictory license text: `mouseinfo` and `pymsgbox`
  (pyautogui dependencies).
- `NOTICE` is not in the shipped payload, and its text says mutagen, pynput
  and pystray "are not bundled into any binary distribution" and that the
  installer displays model licenses before download. The installer installs
  all three by default and has no license display step; the only license
  surfacing in the product is the image-model picker.
- Model terms: Gemma 4 is published under Apache-2.0 on Ollama and Hugging
  Face; the Gemma Terms of Use (with a required Notice file) still apply to
  `embeddinggemma`, which remains on the installer's heal allowlist. Stable
  Diffusion 3.5 carries a revenue-conditioned community license with an
  attribution requirement (the picker shows it). The Piper voice
  `en_US-lessac-medium` is offered under the Blizzard 2013 dataset license,
  which is not an open-source license.
- Third-party attribution for the vendored JavaScript is in [NOTICE](NOTICE),
  which needs to travel with the artifact.
