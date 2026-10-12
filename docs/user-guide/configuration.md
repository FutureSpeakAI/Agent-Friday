# Configuration Reference

Defaults on this page are those of `DEFAULT_SETTINGS` in
`src/agent_friday/core/__init__.py`. Where this page and the code disagree,
the code is right; please open an issue.

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
  `turn_budget`, `local_address`, `scheduled_cloud` and `tracking` are merged
  field by field. Every other
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
| `orchestrator_model` | `"claude-sonnet-5"` | The main model. Mirror of `capability_routing.reasoning`. Choose it in Settings > Models. |
| `subagent_model` | `"claude-sonnet-5"` | Background tasks, drafts and compaction. Mirror of `capability_routing.subagent`. |
| `creative_model` | `"gemini-nano-banana-2"` | Image generation. |
| `music_model` | `"lyria-clip"` | `lyria-clip` (up to 30 s) or `lyria-pro`. |
| `voice_model` | `"gemini-3.8-live"` | The Gemini Live model, used only when the voice engine is `gemini`. |
| `custom_models` | `[]` | Your own `{"provider", "id"}` entries, shown in the picker as unverified. |
| `temperature` | `0.7` | Sampling temperature for chat. |
| `workspace_temperatures` | per workspace, 0.2 to 0.75 | Temperature by workspace (for example `research` 0.25, `studio` 0.75). |
| `turn_budget` | no limits (`rounds`, `wall_clock_s` and `tokens` are empty) | Optional limits for one turn. A figure appears only when you set one; a seat-specific entry (for example `{"local": 200}`) overrides one seat. The same budget applies to local and cloud seats. |
| `local_reasoning_effort` | `"auto"` | How hard a local reasoning model thinks. `auto` lets the shape of the turn decide; `medium`, `xhigh` or `none` pin one effort; `default` leaves the model to its own. |
| `local_reasoning_effort_policy` | `deep_xhigh` on, `reflex_thinking_off` off | What `auto` may do: let deep coding or analysis turns think at `xhigh`, and let quick turns skip thinking. |
| `display_reserve_mode` | `"adaptive"` | How much graphics memory is kept free for the desktop. `adaptive` uses the measured idle draw plus 512 MiB (never under 1 GiB on Windows); `fixed` keeps 2,560 MiB. |
| `seat_prefix_warm` | `true` | When the main local model answers its health check, send it a one-token request so the first real turn after a restart is fast. |
| `background_idle_minutes` | `10` | Background work on a local model (wiki notes, the daily creation, any call nothing is waiting on) starts only after this many minutes with no keyboard or mouse, and waits again when you are back. Choose it in Settings > Advanced. |
| `turn_queue_enabled` | on | Every call to a local model waits its turn, in the order voice, chat, Local API, jobs you asked for, background. Off, calls go straight to the model as before. |
| `turn_queue_tool_release_s` | `60` | A turn that has been in one slow tool this long lets the next person in line have the local brain, then goes back to the front of its line. |
| `turn_queue_seat_slots` | none | How many turns a local model seat runs at once, by seat name. Empty means Friday reads the seat's own `-np`, and one when it says nothing. |
| `local_scheduler_ram_floor_gb` | `4` | No model load starts if it would leave less free memory than this. The job waits, and the queue says so. The Library reading your folders loads no model but keeps the same floor. |
| `local_scheduler_lease_wait_s` | `120` | A GPU job (image, heavy model) that finds the card in use waits this long for its turn instead of being refused. A voice call waits 20 seconds. |
| `local_scheduler_admit_timeout_s` | `900` | A local model call stuck behind other work this long goes ahead without its place, and the numbers count it. |
| `local_scheduler_resident_s` | `300` | A model the scheduler ran this recently counts as still loaded, so its jobs go together. |
| `local_scheduler_wrap_background` | on | A background call that no job claims becomes a waiting job in the Local AI queue, with a card that names the model. Off, such a call goes straight to the model. |
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
| `voice` | `google-gemini` / `gemini-3.8-live` |
| `asr` | `local-voice-lite` / `whisper-small` |
| `tts` | `local-voice-lite` / `piper-en_US-amy-medium` |
| `embedding` | `local` / `all-MiniLM-L6-v2` |
| `local` | `ollama-local` / `gemma4:e2b` (the smallest model in the local plan) |

A capability that is not in the defaults is removed on the next save.

### Where your words go (`model_routing`)

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"cloud_only"` | `cloud_only`, `local_preferred` or `local_only`; `cloud_preferred` is also accepted but is not offered in Settings yet. First-run setup asks. A blank `mode` means you have not chosen: it behaves as `cloud_only`, every part of Friday reads it that way, and setup can ask you. `cloud_preferred` is a cloud mode: the cloud answers, background work runs as in `cloud_only`, cloud voice is allowed, and if the cloud cannot be reached the local model may answer, opening with "Answering locally: no connection to <provider>." `smart`, an older fourth mode, was the same as `local_preferred`; a settings file that still says `smart` is renamed to `local_preferred` the first time it is read. In both local modes the cloud is reached only when you ask for it: background tasks use the local model unless a task or a scheduled job is pinned to a cloud model, and a turn the local model cannot take is refused with an offer to answer it in the cloud, never sent on its own. In `local_only`, asking for the cloud for one turn needs a confirm that names the mode. |
| `default_cloud_model` | `"claude-sonnet-5"` | The router's default cloud model. |
| `local_model` | `"gemma4:e2b"` | The default local model. |
| `ollama_url` | `"http://localhost:11434"` | Ollama endpoint. |
| `fallback_to_cloud` | `true` | When no local model can take a turn in a local mode, offer to answer that turn in the cloud. `false` makes no offer. It never sends anything by itself. |
| `task_overrides` | `{}` | Per-task routing: keys `simple`, `tool_use`, `code`, `research`, `voice`, `vault_access`; values `{"provider": "local"\|"cloud", "model"}`. |
| `vault_local_only` | `true` | Private and sensitive vault content goes only to local models. |
| `vault_cloud_fallback` | `"redact"` | When a vault request cannot run locally: `redact` (the cloud gets a placeholder), `deny`, or `warn` (refuse and say why). |
| `cost_tracking` | `true` | Meter the cost of chat turns. |
| `cloud_provider` | `"anthropic"` | Older single-slot path: `anthropic`, or `openai` to send cloud turns to an OpenAI-compatible endpoint. |
| `openai_base_url` | `"https://openrouter.ai/api/v1"` | Endpoint for that path. |
| `openai_model` | `"anthropic/claude-sonnet-5"` | Model for that path. |
| `openai_api_key` | `""` | Key for that path, **stored in plain text**. Leave blank and use the encrypted store or `OPENAI_API_KEY` / `OPENROUTER_API_KEY` instead. |
| `cloud_context_slicing` | `""` | What a cloud model is sent besides your message. `""` means you have not chosen: it gets everything, as before. `slice` sends only the context each turn needs and holds the rest back (a model can ask for it, and you decide on a card); `everything` records that you chose the old behaviour. Local models are never affected. Offered once, at the first cloud turn; nothing is preselected. |
| `cloud_context_slicing_offer` | `""` | *Internal.* Where that one-time choice stands: `""`, `pending` or `done`. |
| `local_voice_switch_offer` | `""` | *Internal.* Where the one-time offer to move cloud voice to local voice stands: `""` (never made), `pending` (shown, not answered), `accepted` or `declined`. It is made once, only after local voice has been measured on this computer and runs well, and only if you are on cloud voice. Nothing is preselected and nothing switches by itself; `declined` means it is never offered again. |
| `work_orders_enabled` | `false` | *Internal.* Lets a cloud model hand this computer a plan (a work order) that this computer then carries out locally. While this is off, the two tools that take an order (`submit_work_order`, `work_order_status`) are not offered to any model, and turning it off cancels orders that are queued or waiting and stops a running one before its next step. There is no Settings control for it. See [Cloud work orders](cloud-work-orders.md). |
| `work_order_privacy_class` | `""` | How much of a finished work order may go back to the cloud model. `""` means you have not chosen: rough counts (none, 1-4, 5-19, 20 or more) and fixed words only (P1). `P1_aggregate` is the same, chosen. `P2_summary` lets a summary with personal details removed go back, after you approve its exact text on a card (a share you allowed earlier does not stand in for it), in every mode that uses a cloud model. `P3_local_only` sends back only a handle. Material from the vault or marked private is always P3, whatever this says. Nothing is preselected; there is no Settings control for it yet. |
| `cloud_consent` | not answered | *Internal.* Your recorded answer to the unrestricted-cloud screen. The only thing that can turn the egress gate's safeguards off. |
| `unrestricted_cloud` | `false` | *Internal.* Legacy; read once to migrate into `cloud_consent`. |

#### Who answers, in each mode

Laya plans, picks tools, runs quick actions (open, select, play, timers, reads) and checks every action in all four modes. The mode only decides who writes the answer and what may leave this PC.

| | `local_only` | `local_preferred` | `cloud_preferred` | `cloud_only` |
|---|---|---|---|---|
| Text chat | The local model answers. Nothing leaves. | The local model answers; the cloud only when you ask. | The cloud answers. If it cannot be reached the local model answers and says so. | The cloud answers. Never a local answer; an outage is said plainly with a retry. |
| Cloud voice (Gemini Live) | Blocked. | Allowed because you chose it. | Allowed. | Allowed. |
| Local voice | Local model speaks. | Local model speaks. | Local model speaks. | Local model speaks, because you chose a local voice engine. |
| Background jobs | Local. | Local unless a job is pinned to a cloud model. | As `cloud_only`. | Cloud, after Laya has sorted and batched them. |
| Laya's own words | Free-form answers allowed. | Free-form answers allowed. | Fixed lines, plus the "Answering locally" notice. | Fixed lines only: confirmations like "Done: opened it", questions, and errors. Each is marked as Laya's. |

### Offline

| Key | Default | Meaning |
|---|---|---|
| `offline_auto_local` | `true` | Route to local models while this PC is offline. |
| `web_fonts_from_google` | `true` | While Friday's font files are not in `static/fonts`, load the page's typefaces from Google Fonts. `false` uses fonts already on the PC. Ignored once the files are there. A settings-file key only; Settings has no row for it. |
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
| `news_edition_window_hours` | front page 36, briefing 36 | How many hours a story stays eligible for each routine's edition. |
| `notification_mutes` | `[]` | Notification kinds you muted, as `kind\|source`. Muted notifications go to the activity log. Approvals can never be muted. |
| `interruption_budget_per_hour` | `6` | How many things that need you may interrupt (pop up, speak or text) in an hour, 0 to 60; 0 means no limit. Settings offers 3, 6 (Recommended), 12 and no limit. Over the limit, items wait in the Needs-you badge, which says how many. Safety-critical items (spend halt, credential problem, budget alert, seat change, a cLaw block) are exempt and do not use the limit. See [Notifications](notifications.md). |
| `quiet_hours_enabled` | off | Turn quiet hours on. Off by default; Settings offers 22:00 to 07:00 as the recommended window. In quiet hours only safety-critical items interrupt, and background jobs that would raise approvals wait until they end. |
| `quiet_hours_start`, `quiet_hours_end` | `22:00`, `07:00` | The quiet window, as `HH:MM` in your local time. It may cross midnight. Read only when `quiet_hours_enabled` is on. |
| `quiet_until_ts` | `0` | "Quiet until morning": an epoch time. Set by saying "quiet until morning" or from the Needs-you tab; 0 means not set. It ends by itself at the time; nothing resets it. |
| `honour_focus_assist` | off | Follow Windows Focus Assist (Do Not Disturb). Off until you choose it. When on and Windows reports it is on, only safety-critical items interrupt. |
| `news_local_area` | `""` | The place the Local news beat covers, as free text (a city or region). Friday searches Google News for it. Set it in News → Customize Briefing → Local beat. |
| `news_local_sources` | `[]` | Outlet domains for the Local beat, for example `["springfield-local.example"]`. Each is fetched as a Local feed and trusted like the built-in high-trust outlets. With this and `news_local_area` both empty there is no Local beat; nothing ships with a city. |
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
| `tool_output_retention_days` | `7` | How long the full text of a cut-short tool result stays on disk. 0 keeps it forever. |
| `off_record_stops_storage` | `true` | While `off_record` is on, nothing about the conversation is written to disk. Receipts and governance logs keep only the tool, class, decision and time. |
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
| `decision_backend` | `"keyword"` | What decides whether an ambiguous action needs your sign-off: the keyword rules, or `laya-union`, the rules plus the local Laya model (which can only add a card, never remove one). |
| `decision_shadow` | `"laya"` | A second backend scored alongside and logged to `~/.friday/decisions.jsonl`, never changing a decision. `""` is off. The default pair is **Shadow** in Settings > Privacy & Data > What needs your sign-off; Off and On are the other positions. |
| `outward_reads` | `"observe"` | A plain read at a service you connected runs without a card and is receipted. `card` asks about every one. |
| `laya_pilot_enabled` | `false` | An optional experiment that alternates ordinary chat turns with turns Laya prepares. Laya only prepares read tools and never grants actions. |
| `reflex_approval_replies` | `true` | A plain "yes" to a question Friday is waiting on runs the stored action straight away, with no model round. The outcome is the one the ordinary turn gives: same approval, same receipt. Only a whole, unconditional yes counts. "Yes, but use the other folder" is a change request and goes to the model. Only typed in the app on this PC; never from a tunnel, a messaging channel, a call or the Local API. `false` sends every reply through the ordinary turn. |
| `chat_tool_routing` | `""` | Who picks the tool when you type a plain lookup in chat, like "what's on my calendar". `""` means you have not chosen: the chat model picks its own tools, as before. `laya` lets a small model on this PC read your line first; for a lookup it is sure of it runs the tool itself through the same approvals and receipts, and your chat model answers from the result. Anything it is unsure of, or any change to your data, goes to the chat model as usual, and a cloud model keeps its whole toolbox. `model` records that you chose the old behaviour. Nothing is preselected; the choice is in Settings. |
| `reflex_open_enabled` | `null` | "Open the go-live email from Dana" resolved and opened with no model. Unset until you choose; only `true` turns it on, and it is never on by default. The command goes through the same approvals and receipts as a model's tool call, and the reply says it was instant. If Laya is not available the request goes to the model as usual. The same switch covers pointing at rows on your screen: "open the second one", "select these" and "select the ones from Dana" are settled by rules with no model, and Friday says what the page confirmed. When it cannot tell which rows you mean, it asks one short question. Archive, move, label, star and mark are never run this way; they go to the model, which raises its one card. |
| `laya_tool_head` | `shadow` | A tool chooser trained for you can sit beside Laya's fast routing of reads (your calendar, mail, notes, news, past chats). `off` does nothing. `shadow`, the default, records what it would have chosen next to what Laya chose, as hashes and numbers only (no words), and changes nothing: the file is `runtime/laya2/router_head_shadow.jsonl`. `on` lets it choose for a turn only when it is sure at a level it was tested to on held-back examples. It only exists once a tested chooser is in your profile's folder; with none, every value behaves as `off`. Fixed rules, the guard that sends every change to the deeper model, and the approval cards are not touched. It never writes the words of an email or a note: it picks the action and the people, and a writer composes the text. |
| `laya_runtime` | `"auto"` | How Laya runs on the processor: `auto` takes the fastest engine checked on this PC; `torch-fp32`, `torch-int8` or `onnx-int8` pin one. |
| `laya_personal_learning` | `true` | Laya learns from your corrections on this PC. When you correct her or answer a question she asks in a local voice call, or typed on Friday's own page while Laya picks the tool for typed chat (`chat_tool_routing`) on a local model, she keeps those words, sealed in your vault, and checks them against her shipped test questions (and a set of your own earlier answers she holds back) before she uses them as examples. When the deeper mind's background task acts on a request you said in a private (local-only) voice call, its first lookup tool of the kind she can choose herself is listed on the report card as waiting for your OK and teaches her only after you confirm it. On by default, with a notice and a report card under Settings > Privacy & Data > What Laya learns about you, where you can undo, forget, reset, roll back or wipe it. She keeps nothing she can tell is sensitive (and nothing when she cannot tell), nothing while you are off the record or in a multi-person voice room, nothing for a minor profile, and nothing when the vault has no key. She can only make herself more careful on her own. `false` stops new learning and reading; what she already learned stays until you forget, reset or wipe it. |
| `laya_personal_retention_days` | `180` | How long Laya keeps what she learned from your corrections before deleting it (7 to 730 days). Forgetting an item deletes it at once. |
| `laya_personal_shadow_days` | `3` | How many days a retuning of how sure Laya must be runs unseen, beside the live one, before it takes over. It still has to pass its checks, and it can only tighten. |
| `laya_safe_writes` | `off` | Lets Laya raise an approval card for a reversible mail change to the rows you pointed at ("archive these", "star the ones from Mara", "mark these as read", "label these as X", "move this to X"). `off` does nothing. `shadow` writes what she would have proposed (counts and hashes only) to `runtime/laya_safe_writes/shadow.jsonl` and changes nothing. `on` raises the card, always, even for the changes that run with no card when a model asks; you approve it, and Undo puts it back exactly. It turns up only from the Settings control (Privacy & Data > Safe writes), by your own click on this PC's page: a value put in `settings.json` any other way reads as off, and turning it down (from anywhere) turns the record down too. A `friday config` or setup-wizard write whose `config.yaml` does not carry the key cannot keep it on: it turns it off and says so, and you turn it back on in Settings. The card on screen names the sender's address (what is spoken or kept in chat uses the name alone), covers only the messages it lists, and is voiced in a fixed line before a bare spoken yes can approve it. Never trash, spam, send, delete, calendar, files or money. |
| `approvals_policy` | outward, irreversible, spend and external messages gated, cards expire after 24 h; internal not gated | Which classes of action wait for approval. |
| `tool_hooks` | every built-in hook on | Switches for the tool hooks, including `call_verifier`. The governance, confirmation and vault hooks are critical and cannot be switched off. |
| `call_verifier` | on; repeat cache, loop stop after 3 identical failures, argument checks, injection flag | Friday checks every tool call a model proposes before the safety checkpoint sees it. A bad date, a made-up path or an address that is not an address comes back to the model as a fix-it note, and you are not asked. A read repeated in the same turn returns the first answer. The same call failing three times in a row is stopped. Text in a page or an email that tries to give Friday orders, details that look private on their way out, and an address nobody gave her each add a question for you; this check can only add a question, never approve or skip one. Each part has its own switch: `argument_sanity`, `repeat_cache`, `loop_breaker` (with `loop_limit`), `injection_flag`, `privacy_vote`. |
| `rate_limiter` | 60 ring-2 and 20 ring-3 calls per minute | Per-minute limits on tool calls; 0 means unlimited. |
| `computer_control_enabled` | `false` | Allow mouse and keyboard control. Each use is still confirmed. |
| `creative_policy` | harm floor enforced; refusals in plain words | What Friday will not generate. The harm floor cannot be turned off. |
| `minor_mode` | `false` | An age-appropriate filter on generation. |
| `hang_watchdog` | on; heartbeat 15 s; stall after 90 s | Writes a thread dump to `~/.friday/logs/` when the server stalls. |

Grants for scheduled jobs are not a setting; they are kept in
`~/.friday/governance/grants.json` and managed in Settings > Privacy & Data.

### Spending

| Key | Default | Meaning |
|---|---|---|
| `cost_budget` | alert at $5/day and $50/month (both off); hard stop $0 (off); loop guard on | The alert cap warns at 80% and alerts at 100% and never blocks. The hard stop, when enabled, refuses further cloud calls for the rest of its period. Local models are never affected. The loop guard (`loop_guard_enabled`) stops a model that repeats the same call without the conversation moving on; it fires on that pattern, never on an amount. |
| `daily_creation_free_choice` | `true` | Daily creation chooses freely across media. |
| `daily_creation_budget_usd` | `0.50` | Soft cap on a day's creative spend. |

### Scheduled and background work

| Key | Default | Meaning |
|---|---|---|
| `idle_work` | on; after 600 s idle; between 09:00 and 23:00 | Work that runs once a day while you are away, such as daily creation. |
| `scheduled_cloud.answered` | `false` | Whether you have answered "may daily creation and the heartbeat use a cloud model when this PC has no local model?". Skipping the question leaves it `false`. |
| `scheduled_cloud.allow` | `false` | Your answer. When `true` and no local model is serving, daily creation and the heartbeat run on the cloud models below. A local model that is serving is always used first. News is not covered: it always runs on this PC. Ignored until `answered` is `true`. |
| `scheduled_cloud.at` | `null` | When you answered (ISO time). |
| `scheduled_cloud.heartbeat_model` | `claude-haiku-4-5-20251001` | The one cloud model the heartbeat may use. |
| `scheduled_cloud.job_model` | `claude-haiku-4-5-20251001` | The one cloud model daily creation may use. |
| `scheduled_cloud.heartbeat_every_minutes` | `240` | How often the heartbeat runs while it runs in the cloud (60, 120, 240, 360, 480, 720 or 1440). |
| `scheduled_cloud.heartbeat_from_hour`, `heartbeat_to_hour` | `8`, `20` | The heartbeat runs in the cloud only between these hours. |
| `away_drain` | off | Drain queued heavy GPU work on a timer. |
| `away_drain_after_s` | `900` | Idle time before queued work may take the GPU. |
| `repo_sync` | no repositories | Git working trees the repo-sync job pulls. |
| `learning_loop` | on; up to 50 active skills; weekly epoch on Sunday | Learns heuristics from task outcomes. |
| `memory_dreaming` | on; 03:00; 12 topics | Nightly local consolidation of conversations. |
| `podcasts` | routines on; solo for briefing, front page and editorial, duo for weekly; short or standard length | Local podcasts made from your briefings. Written by the local model and spoken by the Kokoro voice. `cloud_voice` is `false`, so no cloud voice is used unless you switch it on, and never for an episode built from private material. |
| `scheduling` | weekdays 09:00 to 17:00; 12 h notice; 15 min buffer; time zone from this computer | Working hours, working days, minimum notice and buffer used when Friday offers meeting times. See [Calendar](calendar.md). |
| `career_ops` | `path` empty | The folder of your career-ops checkout. Empty means `Projects/career-ops` in your user folder. See [Career](career.md). |
| `user_modeling` | on; summary in the prompt | A model of how you work, used to tailor replies. |

The jobs themselves are in `~/.friday/schedules.json` and are managed in the
Workflows workspace. See [scheduled jobs](scheduled-jobs.md).

### Voice

| Key | Default | Meaning |
|---|---|---|
| `voice_engine` | `"local"` | `local`, `local-gpu` (NVIDIA NeMo, experimental), `gemini`, `elevenlabs` or `inworld`. `auto` is still accepted and read as `local`. |
| `local_voice_asr_model` | `"auto"` | faster-whisper size for the Whisper listener and for dictation. `auto` loads `base` on the processor (int8) and `small` on an NVIDIA GPU; `tiny`, `base`, `small` or `medium` is used as given. |
| `local_voice_tts_engine` | `"piper"` | `piper`, or `kokoro` (graphics card when it fits; the processor when `local_voice_kokoro_allow_cpu` is on). |
| `local_voice_tts_voice` | `"en_US-amy-medium"` | Piper voice. |
| `local_voice_kokoro_voice` | `"af_heart"` | Kokoro voice. |
| `local_voice_kokoro_allow_cpu` | `true` | Let Kokoro run on the processor when the card is busy (slower; the session says so) instead of Piper speaking. |
| `local_voice_gpu_asr_model` | `"nvidia/nemotron-3.5-asr-streaming-0.6b"` | Speech recognition on the GPU tier. |
| `local_voice_gpu_tts` | `"fastpitch-hifigan"` | Speech on the GPU tier. |
| `voice_silence_ms` | `500` | Silence that ends your turn on the local voice. |
| `voice_front_model` | `"auto"` | The small fast model that answers live voice turns: `auto` (the first that can run on this PC, in the order `ternary-bonsai:1.7b`, `qwen3-4b-instruct-2507`, `qwen3-1.7b`), or one of those three by name. An explicit name wins; Ternary Bonsai 1.7B needs the PrismML runtime that comes with a Bonsai deep thinker. The main model takes deep work in the background. |
| `voice_brain_during_calls` | `"auto"` | What the main local model does during a voice call: `auto` keeps it loaded beside the fast model when the card holds both and parks it otherwise; `parked`; `resident`. |
| `voice_live_reflex` | `"shadow"` | Laya beside a Gemini Live call: `off`; `shadow` (she works out what she would do from the call's own transcript and writes it next to what Gemini did; nothing runs); `on` (she runs the classes in `voice_live_reflex_classes` herself, then tells Gemini so it does not repeat the action). Saying "stop doing things yourself" on a call sets it to `off`. |
| `voice_live_reflex_classes` | `[]` | Which kinds of request Laya may run herself when `voice_live_reflex` is `on`: `open`, `select`, `play`, `timer`, `read`. Empty until you clear one. A class also has to pass `tools/live_reflex_agreement.py` (95% agreement with Gemini and no unwanted action over at least 30 requests) before it runs. |
| `voice_live_idempotency_s` | `8` | The same Gemini Live tool call with equal arguments inside this many seconds runs once; the second caller gets the first result. `0` turns it off. |
| `voice_live_reduce_over_chars` | `3000` | A tool result larger than this (mail, documents) is cut on this PC to the parts that answer your question before it goes to Google. Every call writes a signed receipt of what was sent: kinds and sizes, never content. Ask "what did you send Google this call?". |
| `voice_live_reduce_local_model` | `"off"` | `seat_if_up` lets a local model that is already running help cut a result; Friday never loads one for this. |
| `voice_async_routing` | `"local_only"` | Where a deep question asked by voice goes: `local_only` (the main local model, or after the call) or `follow_model_routing` (your routing, cloud included, behind the same gates). |
| `voice_response_depth` | `"adaptive"` | `adaptive`, `concise` or `detailed`. |
| `voice_speaking_pace` | `"adaptive"` | `adaptive`, `measured`, `natural` or `brisk`. |
| `voice_room_mode` | `"one"` | `one` person talking to Friday, or `room` (several people; she answers only when addressed). |
| `voice_room_approvals_require_name` | `true` | In `room` mode a spoken approval counts only when it names Friday. Turning it off lets anyone within earshot approve. |
| `voice_tool_hard_limit_s` | `20` | How long a direct voice tool may hold the line before its work moves to the background. 0 means no limit. |
| `voice_first_token_filler_s` | `0` | On the local voice, seconds without a first word before Friday says "Hang on." 0 is off. |
| `keep_brain_warm_between_sessions` | `false` | Keep the main local model loaded after you quit Friday. Off releases its memory. |
| `call_mode` | `"automatic"` | When another app takes the camera or microphone: `automatic` (Friday steps back and returns when the call ends), `ask` (a chip asks once per call) or `off`. |
| `meeting_consent_line` | `""` | The sentence the Meetings start card offers to copy for a call's chat. Empty means Friday's default wording; edit it to make it yours. Friday never posts it into another app. |
| `meeting_consent_mode` | `"ask_everyone"` | `ask_everyone` or `owner_decides`. Some places require everyone's consent to record; the default is to ask everyone. Either way, recording starts only when you press start and confirm. |
| `meeting_speak_consent` | `false` | Say the consent line aloud, through the local voice, when recording starts. |
| `meeting_suggestions` | `true` | Offer "Your call is starting. Take notes?" at most once per calendar event. It switches itself off after two declines; you can turn it back on from Meetings. |
| `meeting_audio_retention` | `""` | Nothing is chosen until you choose. `delete_after_notes` (the Recommended choice), `30_days` or `keep`. Deleting audio never touches the transcript or notes. Empty deletes nothing on a timer. |
| `meeting_text_retention` | `""` | Transcripts and notes: `keep`, or `days` (deletes the whole meeting after `meeting_text_retention_days`). Empty deletes nothing on a timer. |
| `meeting_text_retention_days` | `90` | Used only when `meeting_text_retention` is `days`. |
| `meeting_archive` | `""` | Whether your meetings are searchable from the Library. Nothing is chosen until you choose. `on` indexes each meeting's transcript and notes on this PC, in a Library scope of their own, so "what did we decide about the budget?" is answered with citations. `off` indexes nothing and removes what was indexed. Meeting text never goes to a cloud model from the Library, is never drafted into wiki notes and is not read out on a cloud voice call. `on` is refused while the Library index is not encrypted, and is chosen in the Meetings view (the settings write ignores this key). |
| `voice_interruption_mode` | `"auto"` | What talking over Friday does on the local voice. `auto`: she stops once you are clearly louder than her own voice coming back through the mic. `no-barge`: Esc only. Esc stops her in every mode. Gemini Live reads it too (below). |
| `voice_local_barge_sustain_ms` | `170` | How long you must talk over her before she stops (two of the local voice's ~85 ms mic frames). |
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
`voice_barge_sustain_ms` (`200`), and `voice_interruption_mode` (`"auto"`:
she stops when Gemini hears you start talking, best with headphones;
`no-barge`: speaker-safe, she stops only when you are clearly louder than
her own voice coming back through the mic; Esc stops her in both).

What a Gemini Live call is told and may call (every turn re-sends it, so it is
the cost of the call; `tools/voice_session_tokens.py` measures it on this PC):

| Setting | Default | What it does |
|---|---|---|
| `voice_live_trim` | `"shadow"` | One switch for both trims. `"off"`: today's session. `"shadow"`: today's session exactly, and a log kept on this PC (counts and names only, never text) of what trimming would have dropped and whether you then needed it. `"on"`: a capped voice-only briefing and a short kit of about 20 tools chosen from your workspace, the hour, what you use and what you said. Switch it in Settings, Voice, Gemini Live options; nothing is preselected for you. Say "give me everything" or "use fewer tools" to change the toolset mid-call. |
| `voice_live_instruction_max_tokens` | `9000` | The cap "on" applies to the call's system instruction (estimated tokens). The text-chat-only parts go first, then optional context in a fixed order. Her cLaws, the action policy, honesty text, persona and the voice rules are never dropped to meet it; if they alone are over the cap they are sent and the log says so. `0`: no cap even when on. |
| `voice_live_tool_kit_size` | `20` | About how many tools the kit declares when trim is on (8 to 40). |
| `voice_live_compression_trigger_tokens` | `24000` | The prompt size at which the oldest turns are folded away. `0`: Google's default window. Applied only when trim is "on"; needs `voice_context_compression`. |
| `voice_live_compression_target_tokens` | `12000` | What is kept after a fold. |
| `voice_live_soft_budget_usd` | `0` (off) | One spoken heads-up per call when it reaches this estimated cost. It never cuts a call. The voice panel shows this call's cost and today's voice total whatever this is set to. |

Cloud voices: `elevenlabs_model` (`"eleven_flash_v2_5"`), `elevenlabs_voice_id`,
`inworld_model` (`"inworld-tts-2-flash"`), `inworld_voice_id`,
`inworld_plan_tier` (`"on_demand"`). The two API keys are stored encrypted
when you add them on the setup checklist or in Settings > Connections. An
`elevenlabs_api_key` or `inworld_api_key` found in `settings.json` is still
read, and is moved into the encrypted store the first time it is used. The
`ELEVENLABS_API_KEY` and `INWORLD_API_KEY` environment variables take
precedence over both.

### Appearance, dock and tracking

| Key | Default | Meaning |
|---|---|---|
| `show_all_workspaces` | `true` (`false` on a new install) | The full dock, or the core set. |
| `dock_custom` | no changes | Your own dock order and hidden workspaces. Once you arrange the dock, it wins over `show_all_workspaces`. |
| `landing_mode` | `"smart"` | When the start screen's cluster shows: `smart`, `always` or `never`. |
| `big_mode` | `"auto"` | Large targets for the hand cursor: `auto`, `on` or `off`. |
| `workspace_layouts` | `{}` | Workspaces you set to fill the screen with the chat tray docked beside them. |
| `held_features` | `federation` and `trust_agents` off | Features held back from this release. While a switch is off, nothing offers the feature. |
| `studio_dazzle` | `"full"` | 3D intensity: `off`, `subtle` or `full`. |
| `tracking` | parallax and depth 1.0; pinch to click; snap on | Camera head and hand tracking tuning, including the hand cursor's gain, region, dwell time and magnetic snap. |
| `camera_interval_sec` | `3` | Camera capture interval: 1, 3 or 5 seconds. |
| `audio_input_device_id`, `audio_output_device_id` | `""` | Preferred microphone and speaker. |
| `pause_warnings_off` | `false` | "Don't warn me again" when pausing a seat. |

### Library, artifacts and publishing

| Key | Default | Meaning |
|---|---|---|
| `library_search` | `true` | Friday can search the documents you added to the Library. |
| `library_cloud_answers` | `false` | Whether a cloud model may write Library answers. Off keeps Library text on this PC. |
| `library_cloud_char_cap` | `6000` | The most characters of your documents one cloud answer may send (about three pages). |
| `library_index_on_battery` | `false` | Read and index documents while on battery. |
| `library_floor_tier` | `false` | A small PC: fewer, shorter passages per search. |
| `library_kg_learn` | `""` | Whether the knowledge graph learns from Library documents: `""` (not chosen), `on` or `off`. |
| `artifact_panel_enabled` | `true` | The artifact panel beside every chat. |
| `publish_default_adapter` | `"this_pc"` | Where published pages go by default: `this_pc`, `cloudflare_pages` or `github_pages`. |
| `publish_mark` | `true` | The small "Made with Friday" mark on published pages. |
| `publish_this_pc_enabled` | `true` | The kill switch for hosting on this PC. `false` takes every page published there offline at once. |
| `publish_this_pc_tunnel` | `true` | `false` keeps pages published on this PC on its loopback only, with no public tunnel. |

### Network, address and content

| Key | Default | Meaning |
|---|---|---|
| `local_address` | off; ports 443 and 80; name from `agent_name` | The optional `https://agent.<name>` address. Set up in Settings > General. See [getting started](getting-started.md#open-friday-at-a-local-address). |
| `google_oauth` | no override | `redirect_base_override`, only for a reverse proxy that terminates HTTPS. |
| `content` | on; 2-hour conflict window | The publishing pipeline. |

### Not currently read

These keys exist in `DEFAULT_SETTINGS` but nothing acts on them: `dock_layout`
and `channels` (the channel bridges read `~/.friday/channels.json` instead).

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
| `~/.friday/setup_chat.json` | Where the setup chat is and your non-personal choices in it; no secrets or answers. See [the setup chat](setup-chat.md). |

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
| `FRIDAY_TASK_TIMEOUT` | unset (no limit) | Seconds before a background task times out. |
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

The recommended place for a key is **Settings > Connections**, which
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
store. If plaintext copies of a key exist in `~/.friday/config.yaml`,
`~/.friday/settings.json` or `start.bat`, the next run of `friday setup` moves
them into the encrypted store and removes the copies. It leaves a copy in
place, and says so, when the key cannot be stored or differs from a key
already stored; re-enter that key in Settings > Connections and delete the
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
| `vault/decision-bom.jsonl` (older receipts), `vault/access-log.jsonl`, `vault/egress-log.jsonl`, `vault/context-log/` | Governance and privacy logs | Plain text |
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
| `SOUL.md`, `personality.json` | Friday's persona, including the first-run style section | Plain text |
| `profile/` | Your setup-chat answers, the setup conversation, research waiting for review | Encrypted |

Backing up and restoring this folder, and what is lost without the vault
passphrase, is covered in [backup and restore](backup-and-restore.md).
