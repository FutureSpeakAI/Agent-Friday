# Configuration Reference

> Status: current for 5.14.0. Last verified against `DEFAULT_SETTINGS` in
> `src/agent_friday/core/__init__.py` on 2026-09-24. Where this page and the
> code disagree, the code is right; please open an issue.

Most people never need this page: everything here that matters day to day has
a control in Settings. This reference is for checking a default, reading
`settings.json`, or configuring Friday by environment variable.

- [How settings are stored](#how-settings-are-stored)
- [Settings keys](#settings-keys), grouped by what they control
- [Settings kept in their own files](#settings-kept-in-their-own-files)
- [Environment variables](#environment-variables)
- [Provider keys](#provider-keys)
- [What lives in `~/.friday`](#what-lives-in-friday)

---

## How settings are stored

Settings live in `~/.friday/settings.json` (or `$FRIDAY_HOME/settings.json`).
You change them in Settings, through `POST /api/settings`, or by editing the
file while Friday is stopped.

- **Only known keys survive.** On every read, a top-level key that is not in
  `DEFAULT_SETTINGS` is dropped. A misspelt key is silently ignored.
- **A file that will not parse** makes Friday run on factory defaults and log
  an error; saving is then refused so the file is not overwritten.
- **Partial saves.** `capability_routing`, `model_routing`, `content`,
  `turn_budget` and `local_address` are merged field by field. Every other
  block is replaced whole by a partial write, so send the complete block.
- **Some values are written by exactly one code path.**
  `model_routing.cloud_consent` is set only by the consent screen and is
  removed from any other save.
- **Mirrored keys.** `orchestrator_model`, `subagent_model`, `creative_model`,
  `music_model` and `voice_model` are kept in step with `capability_routing`
  (`reasoning`, `subagent`, `creative_image`, `creative_music`, `voice`).
- **Offline overlay.** While the PC is offline and `offline_auto_local` is on,
  Friday routes as `local_only` without writing that to disk.

Keys marked *internal* below are listed for completeness; do not edit them by
hand.

---

## Settings keys

### Identity

| Key | Default | Meaning |
|---|---|---|
| `agent_name` | `"AGENT FRIDAY"` | The agent's display name, used in prompts and to derive the local address (`AGENT FRIDAY` → `agent.friday`). |
| `user_email` | `""` | Your own email address, which the PII scrubber leaves alone. |
| `distribution` | `"default"` | The active persona preset. Display only. |

### Models and seats

| Key | Default | Meaning |
|---|---|---|
| `orchestrator_model` | `"claude-sonnet-5"` | The main model. Mirror of `capability_routing.reasoning`. Choose it in Settings → Models. |
| `subagent_model` | `"claude-sonnet-5"` | Background tasks, drafts and compaction. Mirror of `capability_routing.subagent`. |
| `creative_model` | `"gemini-nano-banana-2"` | Image generation. |
| `music_model` | `"lyria-clip"` | `lyria-clip` (up to 30 s) or `lyria-pro`. |
| `voice_model` | `"gemini-2.5-flash-native-audio-latest"` | The Gemini Live model, used only when the voice engine is `gemini`. |
| `custom_models` | `[]` | Your own `{"provider", "id"}` entries, shown in the picker as unverified. |
| `temperature` | `0.7` | Sampling temperature for chat. |
| `workspace_temperatures` | per workspace, 0.2 to 0.75 | Temperature by workspace (for example `research` 0.25, `studio` 0.75). |
| `turn_budget` | rounds 999 (scheduled 300), wall clock 1800 s, tokens 1,000,000 | Limits for one turn. A seat-specific entry overrides the default for that seat. The same budget applies to local and cloud seats. |
| `runtime_dir` | `""` | Where local runtimes and model weights live. Empty means `~/.friday/runtime`. `FRIDAY_RUNTIME_DIR` overrides it. |
| `demo_mode` | `null` | `null` shows canned replies only when no provider is set up; `true`/`false` forces it. |
| `providers` | `{}` | Per-provider `{"enabled", "base_url"}`. Never holds keys. |

`capability_routing` maps each capability to `{"provider", "model"}`:

| Capability | Default |
|---|---|
| `reasoning`, `subagent` | `anthropic` / `claude-sonnet-5` |
| `heavy_hitter`, `orchestrator`, `sidekick_fast`, `function_manager`, `memory_manager`, `researcher` | `ollama-local` / empty until you choose a local model |
| `creative_image` | `google-gemini` / `gemini-nano-banana-2` |
| `creative_video` | `google-gemini` / `veo-3` |
| `creative_music` | `google-gemini` / `lyria-clip` |
| `voice` | `google-gemini` / `gemini-2.5-flash-native-audio-latest` |
| `asr` | `local-voice-lite` / `whisper-small` |
| `tts` | `local-voice-lite` / `piper-en_US-amy-medium` |
| `embedding` | `local` / `all-MiniLM-L6-v2` |
| `local` | `ollama-local` / `gemma4:e2b` (the smallest model in the local plan) |

A capability that is not in the defaults is removed on the next save.

### Where your words go (`model_routing`)

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"cloud_only"` | `cloud_only`, `smart`, `local_preferred` or `local_only`. First-run setup asks. In `local_only`, a turn with no local model running is refused with an offer to answer it in the cloud. |
| `default_cloud_model` | `"claude-sonnet-5"` | The router's default cloud model. |
| `local_model` | `"gemma4:e2b"` | The default local model. |
| `ollama_url` | `"http://localhost:11434"` | Ollama endpoint. |
| `fallback_to_cloud` | `true` | Use the cloud when no local model is available. |
| `task_overrides` | `{}` | Per-task routing: keys `simple`, `tool_use`, `code`, `research`, `voice`, `vault_access`; values `{"provider": "local"\|"cloud", "model"}`. |
| `vault_local_only` | `true` | Private and sensitive vault content goes only to local models. |
| `vault_cloud_fallback` | `"redact"` | When a vault request cannot run locally: `redact` (the cloud gets a placeholder), `deny`, or `warn` (refuse and say why). |
| `cost_tracking` | `true` | Meter the cost of chat turns. |
| `cloud_provider` | `"anthropic"` | Older single-slot path: `anthropic`, or `openai` to send cloud turns to an OpenAI-compatible endpoint. |
| `openai_base_url` | `"https://openrouter.ai/api/v1"` | Endpoint for that path. |
| `openai_model` | `"anthropic/claude-sonnet-5"` | Model for that path. |
| `openai_api_key` | `""` | Key for that path, **stored in plain text**. Leave blank and use the encrypted store or `OPENAI_API_KEY` / `OPENROUTER_API_KEY` instead. |
| `cloud_consent` | not answered | *Internal.* Your recorded answer to the unrestricted-cloud screen. The only thing that can turn the egress gate's safeguards off. |
| `unrestricted_cloud` | `false` | *Internal.* Legacy; read once to migrate into `cloud_consent`. |
| `local_inference_slots` | `3` | *Internal.* Not currently read. |

### Offline

| Key | Default | Meaning |
|---|---|---|
| `offline_auto_local` | `true` | Route to local models while this PC is offline. |
| `network_probe` | `"route"` | How Friday tells whether it is offline, every 30 seconds. `"route"` checks this PC's routing table and sends nothing; `"internet"` connects to public DNS resolvers (`dns.google`, `8.8.8.8`, `1.1.1.1`); `"off"` does not check. |
| `offline_queue_cloud_tasks` | `true` | Queue cloud content tasks while offline. |

### Chat and context

| Key | Default | Meaning |
|---|---|---|
| `response_length` | `"standard"` | `concise`, `standard` or `detailed`. |
| `communication_style` | `"professional"` | `professional`, `casual` or `technical`. |
| `include_sources` | `true` | Ask the model to include sources. |
| `cite_sources` | `false` | Inline citation on every factual claim. |
| `news_priorities` | `["AI/Tech", "Politics", "Media", "Local", "Business"]` | News topics Friday prioritises in conversation. Does not control the news feeds. |
| `memory_recall_enabled` | `true` | Recall from past conversations. |
| `compaction` | on; trigger at 70% of a 200,000-token window; keep 3 head and 10 tail messages | Summarises the middle of a long transcript. |
| `context_pruning` | on; over 50 turns keep the 4 most recent and the 10 most relevant | Keeps relevant past turns by meaning (MiniLM embeddings). |
| `context_compression` | on above 1,000 tokens | Compresses kept turns with Headroom when installed. |
| `qa_gates` | on; threshold 0.7; 1 retry; mode `improve` | Friday scores its own output before showing it (`improve` rewrites, `flag` marks). |
| `auto_open_created_files` | `false` | Open files Friday creates as soon as they are done. |
| `confirm_before_opening` | `false` | Ask before opening files or links. |

### Privacy and records

| Key | Default | Meaning |
|---|---|---|
| `off_record` | `false` | Do not log chat at all. |
| `context_logging_enabled` | `true` | The append-only context log in `~/.friday/vault/context-log/`. |
| `context_retention_days` | `0` | 0 keeps the log forever; otherwise prune after 30, 90, 180 or 365 days. |
| `wiki_encrypted_sections` | `[]` | Wiki sections to encrypt with the vault key, for example `["health", "legal", "family"]`. Needs a vault passphrase. Encrypted sections are also kept out of the cloud knowledge block. |
| `wiki_mirror_dir` | `""` (off) | An absolute path to an existing folder you choose. Every wiki write and delete is copied there. Encrypted sections are copied as ciphertext, everything else as plain text. If the folder is synced by OneDrive, Google Drive, Dropbox or similar, every mirrored page leaves this computer through that client. |
| `judgment_gate` | off; model `gemma4:e2b` | A local model that judges ambiguous privacy cases. |
| `task_journal` | keep forever; capture reasoning; encrypted | The background-task journal in `~/.friday/tasks/`. |
| `reasoning_traces` | capture on; keep forever | The reasoning-trace archive in `~/.friday/traces/`. See [reasoning traces](../reference/reasoning-traces.md). |
| `knowledge_graph` | see below | The knowledge graph behind the Knowledge workspace. |

`knowledge_graph` defaults: `enabled: true`; `indexing_mode: "local"` (the
semantic layer runs on a local model; `"cloud"` sends it through the egress
gate); `power_indexer: "native"`; all sources indexed (wiki, conversations,
cognitive memory, soul); `nightly_reindex: true`; `max_visible_nodes: 2000`.

### Approvals and safety

| Key | Default | Meaning |
|---|---|---|
| `decision_backend` | `"laya-union"` | What decides whether an ambiguous action needs your sign-off: the keyword rules, or the rules plus the local Laya model (which can only add a card, never remove one). Off / Shadow / On in Settings → Privacy & Approvals. |
| `decision_shadow` | `""` | A second backend scored alongside and logged, never changing a decision. |
| `approvals_policy` | outward, irreversible, spend and external messages gated, cards expire after 24 h; internal not gated | Which classes of action wait for approval. |
| `tool_hooks` | every built-in hook on | Switches for the tool hooks. The governance, confirmation and vault hooks are critical and cannot be switched off. |
| `rate_limiter` | 60 ring-2 and 20 ring-3 calls per minute | Per-minute limits on tool calls; 0 means unlimited. |
| `computer_control_enabled` | `false` | Allow mouse and keyboard control. Each use is still confirmed. |
| `creative_policy` | harm floor enforced; refusals in plain words | What Friday will not generate. The harm floor cannot be turned off. |
| `minor_mode` | `false` | An age-appropriate filter on generation. |
| `hang_watchdog` | on; heartbeat 15 s; stall after 90 s | Writes a thread dump to `~/.friday/logs/` when the server stalls. |

Grants for scheduled jobs are not a setting; they are kept in
`~/.friday/governance/grants.json` and managed in Settings → Privacy & Approvals.

### Spending

| Key | Default | Meaning |
|---|---|---|
| `cost_budget` | alert at $5/day and $50/month (both off); hard stop $0 (off) | The alert cap warns at 80% and alerts at 100% and never blocks. The hard stop, when enabled, refuses further cloud calls for the rest of its period. Local models are never affected. |
| `daily_creation_free_choice` | `true` | Daily creation chooses freely across media. |
| `daily_creation_budget_usd` | `0.50` | Soft cap on a day's creative spend. |

### Scheduled and background work

| Key | Default | Meaning |
|---|---|---|
| `idle_work` | on; after 600 s idle; between 09:00 and 23:00 | Work that runs once a day while you are away, such as daily creation. |
| `away_drain` | off | Drain queued heavy GPU work on a timer. |
| `away_drain_after_s` | `900` | Idle time before queued work may take the GPU. |
| `repo_sync` | no repositories | Git working trees the repo-sync job pulls. |
| `learning_loop` | on; up to 50 active skills; weekly epoch on Sunday | Learns heuristics from task outcomes. |
| `memory_dreaming` | on; 03:00; 12 topics | Nightly local consolidation of conversations. |
| `user_modeling` | on; summary in the prompt | A model of how you work, used to tailor replies. |

The jobs themselves are in `~/.friday/schedules.json` and are managed in the
Workflows workspace. See [scheduled jobs](scheduled-jobs.md).

### Voice

| Key | Default | Meaning |
|---|---|---|
| `voice_engine` | `"local"` | `local` (CPU), `local-gpu` (NVIDIA NeMo), `gemini`, `auto`, `elevenlabs`, `inworld`. |
| `local_voice_asr_model` | `"small"` | faster-whisper size: `tiny`, `base`, `small` or `medium`. |
| `local_voice_tts_engine` | `"piper"` | `piper`, or `kokoro` (needs an NVIDIA GPU unless `local_voice_kokoro_allow_cpu`). |
| `local_voice_tts_voice` | `"en_US-amy-medium"` | Piper voice. |
| `local_voice_kokoro_voice` | `"af_heart"` | Kokoro voice. |
| `local_voice_kokoro_allow_cpu` | `false` | Let Kokoro run on the CPU. |
| `local_voice_gpu_asr_model` | `"nvidia/nemotron-3.5-asr-streaming-0.6b"` | Speech recognition on the GPU tier. |
| `local_voice_gpu_tts` | `"fastpitch-hifigan"` | Speech on the GPU tier. |
| `voice_silence_ms` | `800` | Silence that ends your turn. |
| `voice_ear_gpu`, `voice_mouth_gpu` | `"if_free"` | GPU use for listening and speaking: `never`, `if_free` or `required`. |
| `voice_idle_unload_s` | `600` | Unload idle GPU voice workers after this long. |
| `voice_tools` | `true` | Let voice sessions use tools (through the same approval checkpoint). |
| `offline_voice_fallback` | `true` | Fall back to the system voice when the cloud voice cannot be reached. |
| `push_to_transcribe` | `true` | The system-wide hold-to-dictate key, run by the tray. |
| `push_to_transcribe_hotkey` | `"alt+t"` | The key. |
| `push_to_transcribe_hold_ms` | `150` | A shorter tap is passed through to the window as a normal keypress. |

Gemini Live (`voice_engine: "gemini"`) tuning: `tts_voice` (`"Aoede"`),
`voice_language` (blank: server default), `voice_style_prompt`,
`voice_temperature` (`null`: SDK default), `voice_max_tokens` (0: unlimited),
`voice_affective` (`true`), `voice_proactive` (`true`),
`voice_context_compression` (`true`), `voice_barge_grace_ms` (`800`),
`voice_barge_sustain_ms` (`200`), and `voice_interruption_mode` (`"auto"`;
`headphones` also allows barge-in; `no-barge` turns it off).

Cloud voices: `elevenlabs_model` (`"eleven_flash_v2_5"`), `elevenlabs_voice_id`,
`inworld_model` (`"inworld-tts-2-flash"`), `inworld_voice_id`,
`inworld_plan_tier` (`"on_demand"`). `elevenlabs_api_key` and `inworld_api_key`
are **stored in plain text** in `settings.json`; prefer the
`ELEVENLABS_API_KEY` and `INWORLD_API_KEY` environment variables.

### Appearance, dock and tracking

| Key | Default | Meaning |
|---|---|---|
| `show_all_workspaces` | `true` (`false` on a new install) | The full dock, or the core set. |
| `dock_custom` | no changes | Your own dock order and hidden workspaces. |
| `studio_dazzle` | `"full"` | 3D intensity: `off`, `subtle` or `full`. |
| `tracking` | parallax and depth 1.0; pinch to click | Camera head and hand tracking tuning. |
| `camera_interval_sec` | `3` | Camera capture interval: 1, 3 or 5 seconds. |
| `audio_input_device_id`, `audio_output_device_id` | `""` | Preferred microphone and speaker. |
| `pause_warnings_off` | `false` | "Don't warn me again" when pausing a seat. |

### Network, address and content

| Key | Default | Meaning |
|---|---|---|
| `local_address` | off; ports 443 and 80; name from `agent_name` | The optional `https://agent.<name>` address. Set up in Settings → General. See [getting started](getting-started.md#open-friday-at-a-local-address). |
| `google_oauth` | no override | `redirect_base_override`, only for a reverse proxy that terminates HTTPS. |
| `content` | on; 2-hour conflict window | The publishing pipeline. |

### Not currently read

These keys exist in `DEFAULT_SETTINGS` but nothing acts on them: `setup`,
`onboarding`, `dock_layout`, `channels` (the channel bridges read
`~/.friday/channels.json` instead) and `model_routing.local_inference_slots`.

---

## Settings kept in their own files

| File | What it configures |
|---|---|
| `~/.friday/phone/config.json` | The phone: on/off (off by default), Twilio account and number, your verified cell, what Friday may do by text and call, limits. Secrets are stored separately and encrypted. See [Phone](phone.md). |
| `~/.friday/privacy_shield.json` | `watchlist`: extra strings (names, account numbers) to redact from anything bound for the cloud. |
| `~/.friday/schedules.json` | Scheduled jobs. |
| `~/.friday/governance/grants.json` | Grants for scheduled jobs. |
| `~/.friday/mcp_servers.json` | MCP servers Friday starts. |
| `~/.friday/channels.json` | Telegram and Discord bridges. |
| `~/.friday/providers/*.json` or `*.yaml` | Custom OpenAI-compatible provider descriptors, loaded automatically. |
| `~/.friday/onboarding.json` | Your first-run answers, including the update-check choice. |

The Privacy Shield always redacts, with no configuration: SSNs, card numbers
that pass the Luhn check, phone numbers, email addresses (except yours) and US
street addresses. Watchlist entries match on word boundaries.

---

## Environment variables

Set these for the process that starts Friday. Provider keys are listed under
[Provider keys](#provider-keys).

### Server and authentication

| Variable | Default | Meaning |
|---|---|---|
| `FRIDAY_PORT` | `3000` | Port. If it is busy the server tries the next ten. The server records the port it bound in `~/.friday/friday_server.port`, and the tray follows it. |
| `FRIDAY_BIND_HOST` | `127.0.0.1` | Bind address. Binding to anything else needs a login key unless `FRIDAY_ALLOW_KEYLESS_BIND` is set. |
| `FRIDAY_TLS_CERT`, `FRIDAY_TLS_KEY` | unset | Serve HTTPS directly. `FRIDAY_REQUIRE_TLS` refuses to start remotely without it; `FRIDAY_SKIP_TLS_WARN` silences the warning. |
| `FRIDAY_USERNAME` | `admin` | Login username for remote access. |
| `FRIDAY_REMOTE_KEY` | falls back to `FRIDAY_PASSWORD` | Login key for remote access. |
| `FRIDAY_TRUST_LOOPBACK` | `1` | Treat direct requests from this PC as the owner. `0` requires a login locally too. Proxied requests are never trusted. |
| `FRIDAY_COOKIE_SECURE` | unset | Mark the session cookie `Secure`. |
| `FRIDAY_WS_TOKEN` | unset | Token required on the voice WebSocket. |
| `FRIDAY_SECRET_KEY` | stored in `~/.friday/secret_key` | Session secret. |
| `FRIDAY_API_TOKEN_ROTATE_HOURS` | `24` | How often the page's API token rotates; 0 disables rotation. |
| `FRIDAY_MAX_REQUEST_MB` | `25` | Largest request body. |

### Paths

| Variable | Default | Meaning |
|---|---|---|
| `FRIDAY_HOME` | `~/.friday` | Friday's data folder itself. |
| `FRIDAY_RUNTIME_DIR` | `~/.friday/runtime` | Local runtimes and model weights. |
| `FRIDAY_MODELS_DIR` | `$FRIDAY_HOME/models` | Model files. |
| `FRIDAY_VOICE_ASSETS` | `$FRIDAY_HOME/voice_assets` | Voice assets. |
| `FRIDAY_SANDBOX_MODE` | `confine` | `off`, `confine` (file writes confined to `FRIDAY_SANDBOX_ROOT`) or `strict` (also a command allowlist). |
| `FRIDAY_SANDBOX_ROOT` | your user folder | The confinement root. |

### Vault

| Variable | Meaning |
|---|---|
| `FRIDAY_VAULT_PASSPHRASE` | The vault passphrase. Friday looks for it in this order: an environment variable you set, Windows Credential Manager, the DPAPI file in `~/.friday/security/`, then a launch script (legacy). |
| `FRIDAY_PASSWORD` | Legacy fallback for both the vault passphrase and the remote login key. |

### Behaviour

| Variable | Default | Meaning |
|---|---|---|
| `FRIDAY_SAFE_MODE` | unset | Turn off self-modification. |
| `FRIDAY_NO_ARBITER` | unset | Skip the GPU residency arbiter at startup. |
| `FRIDAY_TOOL_CATALOGUE` | on | `0` sends every tool's full schema to the model instead of an index. |
| `FRIDAY_DECISION_BACKEND`, `FRIDAY_DECISION_SHADOW` | unset | Override `decision_backend` and `decision_shadow`. |
| `FRIDAY_TASK_TIMEOUT` | `1800` | Seconds before a background task times out. |
| `FRIDAY_EGRESS_CLASSIFY_RATE` | `40` | Egress classifier calls per second. |
| `FRIDAY_PRESIDIO_SHADOW`, `FRIDAY_PRESIDIO_ENFORCE` | unset | Presidio PII detection: observe only, or enforce. Enforcing is not recommended; see the [threat model](../security/threat-model.md). |
| `FRIDAY_DISTRO` | `default` | Default persona preset. |
| `FRIDAY_LIVE_MODEL`, `FRIDAY_LIVE_VOICE` | Gemini Live defaults | Used only when the matching settings are empty. |
| `FRIDAY_OS_MODE` | unset | Sealed Linux kiosk mode: credentials never fall back to plain text. |
| `FRIDAY_LOG_TARGET` | unset | `stdout` sends logs to journald (OS mode). |
| `FRIDAY_DEPLOYMENT_ID` | `unknown` | A label reported by the health check. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | The built-in default Claude model. |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint for the CLI. |

### For developers and tests

`FRIDAY_TESTING` (no background threads at import), `FRIDAY_NO_SYSTEM_CHANGES`
(the local-address feature never touches the hosts file or certificates),
`FRIDAY_VOICE_DEBUG`, `FRIDAY_VOICE_FAKE_TEXT`, `FRIDAY_VOICE_FAKE_SLOW_MS`,
`FRIDAY_PERSONA_EVAL_LIVE`, `FRIDAY_PERSONA_GOLDEN_DIR`,
`FRIDAY_PERSONA_FIXTURES_DIR`, and the voice installer's `FRIDAY_TORCH_PIN`,
`FRIDAY_TORCHAUDIO_PIN`, `FRIDAY_TORCH_CUDA_INDEX`. Friday sets
`FRIDAY_SESSION_DEPTH`, `FRIDAY_SESSION_ID` and `FRIDAY_WORKER` for its own
child processes.

---

## Provider keys

The recommended place for a key is **Settings → Accounts & Keys**, which
encrypts it under Friday's keystore (`~/.friday/providers/keys/`). Where every
credential lives is in [SECURITY.md](../../SECURITY.md#where-secrets-live).

| Provider | Environment variable |
|---|---|
| Anthropic | `ANTHROPIC_API_KEY` |
| Google Gemini | `GEMINI_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Hugging Face | `HF_TOKEN` (or `HUGGINGFACE_API_KEY`, `HUGGING_FACE_HUB_TOKEN`) |
| Groq | `GROQ_API_KEY` |
| Together | `TOGETHER_API_KEY` |
| Fireworks | `FIREWORKS_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| xAI | `XAI_API_KEY` |
| Perplexity | `PERPLEXITY_API_KEY` |
| Cohere | `COHERE_API_KEY` |
| kie.ai | `KIE_API_KEY` |
| ElevenLabs, Inworld | `ELEVENLABS_API_KEY`, `INWORLD_API_KEY` |
| Brave Search, Firecrawl | `BRAVE_SEARCH_API_KEY`, `FIRECRAWL_API_KEY` |
| Google OAuth client (your own) | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` |

**Which wins.** For Anthropic and Gemini, an environment variable you set
yourself wins over the stored key. For the OpenAI-compatible providers, the
stored key wins. A key read from a launch script (`start.bat`,
`launch_now.bat`, `friday_startup.bat` in the application folder) loses to the
stored key.

**The terminal wizard.** `friday setup` stores keys only in the encrypted
store. Earlier versions also wrote plaintext copies to `~/.friday/config.yaml`,
`~/.friday/settings.json` and `start.bat`; the next run of `friday setup` moves
those into the encrypted store and removes the copies. It leaves a copy in
place, and says so, when the key cannot be stored or differs from a key
already stored; re-enter that key in Settings → Accounts & Keys and delete the
copy by hand.

---

## What lives in `~/.friday`

`~/.friday` (or `$FRIDAY_HOME`) holds everything Friday knows and keeps. It is
yours: open it, back it up, or delete it. The application folder
(`%LOCALAPPDATA%\AgentFriday`) holds only the program. "Encrypted" below means
encrypted at rest on this disk.

| Path | Holds | At rest |
|---|---|---|
| `settings.json` | Settings | Plain text (includes any plaintext keys listed above) |
| `config.yaml` | Keys written by the terminal wizard | Plain text |
| `wiki/` | Your wiki pages | Plain text, except sections in `wiki_encrypted_sections` |
| `memory/`, `chat_history.json` | Conversations and conversation memory | Plain text |
| `knowledge-graph/` | The graph derived from your pages; safe to delete, it is rebuilt | Private and sensitive records encrypted |
| `finance/`, `health/`, `vault/legal/`, `vault/finances/`, `vault/family/` | The vault | Encrypted when a vault passphrase is set |
| `security/keystore.json` | The credential root key | Owner-only file; unwrapped by default |
| `security/vault-passphrase.dpapi` | A backup copy of the vault passphrase | Windows DPAPI (this Windows account only) |
| `providers/keys/`, `google_accounts/tokens/`, `mcp_oauth/`, `platforms/` | Provider keys and connected-account tokens | Encrypted |
| `phone/` | Phone settings, encrypted Twilio secrets, message log | Secrets encrypted; the rest plain text |
| `decision-bom.jsonl` | Signed receipts of approval and privilege-ring decisions | Plain text, HMAC-signed |
| `governance/` | Grants and the pinned constraint hash | Plain text |
| `vault/decision-bom.jsonl` (history from earlier versions), `vault/access-log.jsonl`, `vault/egress-log.jsonl`, `vault/context-log/` | Governance and privacy logs | Plain text |
| `vault/.governance-key` | Governance signing key (fallback copy) | Owner-only file |
| `traces/ledger.jsonl` | Reasoning traces | Each record encrypted; hash-chained and signed |
| `tasks/` | Background-task journal | Encrypted by default |
| `decisions.jsonl` | Verdicts of the approval scanner | Plain text |
| `privacy/file_grants.jsonl` | File grants | Plain text, signed |
| `costs.db`, `spend_halts.jsonl` | Spend records | Plain text |
| `schedules.json`, `schedule_runs.jsonl` | Scheduled jobs and their runs | Plain text |
| `documents/` | Word, Excel and PowerPoint files Friday makes | Plain files |
| `runtime/`, `local_voice/`, `models/` | Local runtimes and model weights | Not personal data |
| `local-address/`, `tls/` | Local-address certificate authority and certificate | Private keys stored as plain files |
| `logs/`, `friday.log`, `server_stderr.log` | Logs | Plain text |
| `SOUL.md`, `personality.json` | Friday's persona | Plain text |

Backing up and restoring this folder, and what is lost without the vault
passphrase, is covered in [backup and restore](backup-and-restore.md).
