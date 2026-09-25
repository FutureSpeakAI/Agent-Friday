# Known Issues

**As of 2026-09-24, for 5.14.0.**

This file lists what is broken, unverified, or deliberately limited in a way
you may hit. Each entry was checked against the code on that date. Fixed
problems move to the [CHANGELOG](CHANGELOG.md). If something here is wrong, or
you hit something that is not here, please open an issue.

---

## 1. Upgrading and moving

- **Upgrading keeps your data.** The installer replaces only the program in
  `%LOCALAPPDATA%\AgentFriday`; `%USERPROFILE%\.friday` and the vault
  passphrase in Windows Credential Manager are not touched.
- **Moving `.friday` to a new PC or Windows account holds outward actions**
  until you re-confirm Friday's rules. The governance signing key lives in
  Credential Manager, so the new account gets a new key and the pinned
  signature of Friday's rules no longer matches. Reads keep working. Settings
  → Privacy & Approvals → Friday's rules on this PC shows the state and
  re-pins after you confirm. The same hold follows any release that changes
  the rules text. See [backup and restore](docs/user-guide/backup-and-restore.md).
- **Installers 5.6.0 to 5.6.5** had upgrade defects (files not replaced; the
  vault passphrase deleted when it lived only in `start.bat`). Running the
  current installer repairs such an install. There is no recovery for data
  encrypted under a passphrase you no longer have. If you connected a
  credentialed MCP server on an affected install, rotate that credential.

## 2. Open defects

- **Built-in scheduled jobs do not run on a cloud-only install.** They are
  local-only by default and are skipped when no local model is serving. There
  is no switch in Workflows to allow a job the cloud; edit `schedules.json`.
  See [Scheduled jobs](docs/user-guide/scheduled-jobs.md).
- **No receipt viewer.** Signed receipts are written to
  `.friday\decision-bom.jsonl` but there is no screen for them.
- **The provenance ledger is in memory.** Where an argument came from is
  forgotten on restart, and a value that was paraphrased or re-encoded, or
  summarised by a model, is not tracked. The approval checkpoint still applies
  to every outward action.
- **Friday does not always search when asked to search.** A request phrased as
  "search the web for …" can return a confident answer with an empty tool
  trace and citations for pages never fetched. The provenance layer renders
  such citations inert. Cause not established.
- **Settings keys the code reads but does not declare are always dropped**, so
  those reads always fall back to their built-in default: `owner_email`,
  `owner_identities`, `music_models`, `creative_models`, `daily_creation_hour`,
  `daily_creation_minute`, `self_improvement_hour`, `task_timeout_seconds`,
  `timezone`, and others. Setting them in `settings.json` has no effect.
- **A partial settings write replaces most blocks whole.** Only
  `capability_routing`, `model_routing`, `content`, `turn_budget` and
  `local_address` are merged field by field.
- **Some declared settings are not read**: `setup`, `onboarding`,
  `dock_layout`, `channels` (the bridges read `channels.json`) and
  `model_routing.local_inference_slots`.
- **Local models without native tool calling cannot act.** The planner offers
  only models that call tools natively. The `function_manager` seat that would
  let a smaller model handle tool selection is declared but nothing consults it.
- **Chain retry has a budget race.** `_retry_chain_step` spawns the retry and
  only then records the retry count, so a step that fails before that write
  can retry past its budget.
- **`print()` output from the chat path may not reach the log** when the server
  is started by the tray. Use the structured logger for anything you need
  later.
- **`ui_parts/app.html` drifts from `index.html`.** The served file is the
  source of truth; see [docs/development/ui-build.md](docs/development/ui-build.md).
- **Seat contention on 12 GB cards.** A resident 12B model leaves little room,
  so a second GPU seat or local image generation may fail to allocate. "I need
  my machine" in Settings → Models releases the GPU.

## 3. Deliberate behaviour that can surprise

- **`local_only` refuses rather than falls back.** With no local model
  serving, a chat turn is refused with an offer to answer in the cloud.
- **`cloud_only` does not override a local model you pick yourself.** It stops
  Friday choosing a local model on its own; the model picker is authoritative.
- **A conversation that arrives by phone is read-only.** Anything that comes in
  by text or call is untrusted input, including from your own cell.
- **Every email needs its own card.** Grants for scheduled jobs never cover
  email.
- **One key per machine.** A provider key entered on a machine is used there
  until replaced; nothing records whose account it belongs to. If you set
  someone else up, give them their own key.
- **Presidio runs in shadow mode and reports as inactive.** Measured against
  the built-in classifier it was weaker on real PII and escalated ordinary
  prose, so it changes nothing unless `FRIDAY_PRESIDIO_ENFORCE=1`. See the
  [threat model](docs/security/threat-model.md).

## 4. Unverified

Listed separately from "broken". These are not claims that things work.

- **No clean-machine install** has been performed for this release on a PC
  that has never seen the code.
- **The phone has not carried real traffic.** Texts, voicemail and approvals
  by text are unit-tested; live calls have run only against a simulated
  stream.
- **Push-to-transcribe** was verified in Notepad and Chrome; other
  applications, including Word, are untested.
- **Local voice end to end** has not been confirmed from the egress log to send
  nothing off the machine.
- **CPU-only inference speed** is unmeasured for every model.
- **8 GB of VRAM** is unmeasured; the fixture for it carries figures from a
  12 GB card.
- **The KV-cache estimate** the seat planner rests on is inferred from two
  anchors on two backends.

## 5. Things that leave your machine

No telemetry, analytics, crash reporting or license check. The connections
Friday makes on its own are listed, with how to turn each off, in
[docs/user-guide/background-network.md](docs/user-guide/background-network.md).
Friday ships its font files, so the page makes no font request. MediaPipe
loads from jsDelivr only when you turn on head or hand tracking.

Also outside the egress gate: OfficeCLI's preview renderer may fetch Mermaid,
KaTeX, three.js or web fonts from public CDNs for documents that use them.

## 6. Platform limits

Agent Friday is supported on Windows 10 and 11 with an NVIDIA GPU for local
models. macOS and Linux run the server, the web UI, cloud providers and local
chat through Ollama, without the tray, the residency layer, GPU-aware planning
or Windows credential protection.

- Apple Silicon is refused by the residency planner: no MLX or Metal backend.
- AMD and Intel GPUs are not detected; `nvidia-smi` is the only probe.
- 16 GB of system RAM is the floor for a local model.

## 7. Security posture

- **One key can live in `settings.json` in plain text** when set there:
  `model_routing.openai_api_key`. Prefer the environment or the encrypted
  store. An ElevenLabs or Inworld key found in `settings.json` is moved into
  the encrypted store the first time it is read, and the plaintext copy is
  blanked.
- **The credential keystore is unwrapped by default.** Its root key sits in
  `.friday\security\keystore.json` behind an owner-only file ACL, so anything
  running as you can decrypt stored credentials, and a folder copy of
  `.friday` carries the key with it. With a vault passphrase set, Settings →
  Privacy & Approvals → Stored keys wraps the root key with it. Friday then
  unwraps it with the passphrase from Credential Manager at start-up, so
  anything running as you can still reach it through Credential Manager; the
  wrap protects copies of the file, not a live session.
- **Linux OS mode has no durable secret store.** Credential and passphrase
  storage fail closed there; the supported path is `FRIDAY_VAULT_PASSPHRASE` in
  the environment.
- **`web_safety.py`, the SSRF guard, has no tests of its own.**
- **Dependencies are declared with `>=` floors.** A `uv.lock` is committed
  but nothing installs from it.
- **Open dependency advisories, none reachable in the default install:**
  - *ChromaDB (two critical, three high).* The advisories concern ChromaDB's
    HTTP server and its multi-tenant authorisation. Friday uses ChromaDB only
    as an embedded library (`PersistentClient` in `conversation_memory.py`) and
    never starts that server. No fixed version exists yet.
  - *Lightning and Hydra (high).* Code execution when loading an untrusted
    model checkpoint or config. Both arrive only with NVIDIA NeMo, the optional
    GPU voice extra (`voice-local-gpu`), which the Windows installer does not
    install. NeMo loads NVIDIA's own published voice models by name; do not
    point it at a `.nemo` or `.ckpt` file from anywhere else. The fixed
    versions (Lightning 2.6.6, Hydra 1.3.4) cannot be installed with the NeMo
    versions Friday supports.
  - *NLTK (high).* Path checks in its model-artifact helpers. It also arrives
    only with the GPU voice extra, for NeMo's English pronunciation step;
    Friday's own code does not import it. No fixed version exists yet.
- **The 5.12.0 and 5.13.0 installer zips include files that are not part of
  the repository**, copied from a build machine's working tree. They are inert.
  The build now refuses any payload that is not the committed tree.

## 8. Licensing

The application is MIT-licensed. The Windows installer's default tiers install
some copyleft packages on your machine (`piper-tts` and its espeak-ng, GPLv3;
`mutagen`, GPLv2+; the `ffmpeg` binary inside `imageio-ffmpeg`, a GPLv3 build;
`pynput` and `pystray`, LGPLv3). [NOTICE](NOTICE) and
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) list every component, how it
is delivered, and its license, including model licenses (Gemma, Stable
Diffusion 3.5, the Piper voices). The Lessac voice, whose training data is for
non-commercial research only, is no longer offered.
