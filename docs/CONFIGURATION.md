# Configuration Reference

All configuration lives in `~/.friday/settings.json`. Settings can be updated via the UI, the `POST /api/settings` endpoint, or by editing the file directly (restart required for some changes).

---

## API Keys

API keys are stored encrypted in Friday's credential store (one encrypted file per provider under `~/.friday/providers/keys/`), never as plaintext in `settings.json`. The recommended way to add keys is through the **setup wizard** (first-run) or **Settings → API Keys** in the UI.

You can also supply keys as environment variables (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`), which take precedence over the stored credentials.

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude (`sk-ant-...`). Required for cloud reasoning. |
| `GEMINI_API_KEY` | Google AI Studio key (`AIza...`). Optional — enables TTS, creative tools, and voice mode. |

When the legacy OpenAI-compatible cloud provider is enabled (see [Model Routing](#model-routing)), an API key may also be supplied via environment variables:

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | Fallback API key for the OpenAI-compatible provider (used when `model_routing.openai_api_key` is blank). |
| `OPENROUTER_API_KEY` | Alternate fallback API key for the OpenAI-compatible provider (e.g. OpenRouter). |

Each of the other built-in providers has its own env-var key — see [Providers](#providers).

---

## Model Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `orchestrator_model` | string | `claude-sonnet-5` | Model for the main agent brain. Pick from Settings → Models (catalog-driven via `GET /api/models`): Claude Sonnet 5 / Opus 4.8 / 4.7 / 4.6 / Sonnet 4.6 / Fable 5, GPT-4o family, or any installed Ollama model. |

---

## Model Routing

Settings under the `model_routing` key (top-level copies of these keys are ignored):

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `cloud_only` | Routing mode: `cloud_only`, `smart`, `local_preferred`, `local_only`. |
| `default_cloud_model` | string | `claude-sonnet-5` | Cloud model used by the router when no override is specified. |
| `local_model` | string | `gemma3:4b` | Default Ollama model for local routes — Friday's zero-cloud-key default brain (runs on ~8GB RAM; upgrade to `gemma3:12b` / `gemma3:27b` with more RAM). |
| `fallback_to_cloud` | boolean | `true` | Fall back to cloud when Ollama is unavailable. |
| `ollama_url` | string | `http://localhost:11434` | Ollama API endpoint. |
| `vault_local_only` | boolean | `true` | When `true`, vault TIER_2/TIER_3 content reaches local models only; vault-touching requests are force-routed to Ollama. |
| `vault_cloud_fallback` | string | `redact` | Behavior when vault access is needed but no local model is available: `redact` (proceed with gated content), `deny` (refuse), `warn` (refuse and notify). |
| `task_overrides` | object | `{}` | Per-task-type routing overrides. Keys: `simple`, `tool_use`, `code`, `research`, `voice`, `vault_access`. Values: `{"provider": "local"|"cloud", "model": "..."}`. |
| `cloud_provider` | string | `anthropic` | Provider for cloud turns: `anthropic` (default) or `openai` (route through an OpenAI-compatible endpoint). Legacy single-slot path — see [Providers](#providers) for the multi-provider layer. |
| `openai_base_url` | string | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible endpoint (legacy single-slot path). Works with OpenRouter and any `/v1` endpoint (Together, Groq, vLLM, LM Studio, OpenAI). |
| `openai_model` | string | `anthropic/claude-3.7-sonnet` | Model name passed to the OpenAI-compatible endpoint (legacy single-slot path). |
| `openai_api_key` | string | _(empty)_ | API key for the OpenAI-compatible endpoint (legacy single-slot path). Blank → falls back to env `OPENAI_API_KEY` / `OPENROUTER_API_KEY`. |

> **OpenAI-compatible provider.** When `cloud_provider` is `openai`, cloud turns route through the configured `/v1` endpoint with a full agentic tool loop (parity with the Anthropic path) when the model supports tool-calling. The default settings leave Anthropic behavior unchanged. Vault and TIER_2/TIER_3 requests always stay on the local/Anthropic path and are never sent to the OpenAI endpoint.

### Routing configuration example

```json
{
  "model_routing": {
    "mode": "smart",
    "default_cloud_model": "claude-sonnet-5",
    "ollama_url": "http://localhost:11434",
    "fallback_to_cloud": true,
    "vault_cloud_fallback": "deny",
    "task_overrides": {
      "code": { "provider": "local", "model": "qwen3:32b" }
    }
  }
}
```

---

## Providers

Beyond the legacy single-slot `cloud_provider` path above, Friday ships a model-agnostic provider layer with 16 built-in providers, managed via the `/api/providers/*` routes and Settings → Providers.

### `providers` settings key

Per-provider configuration: `name → {"enabled": bool, "base_url"?: string}`. API keys are **never** stored here — they always go to the encrypted credential store (see [API Keys](#api-keys)).

```json
{
  "providers": {
    "openrouter": { "enabled": true },
    "groq": { "enabled": true }
  }
}
```

### `capability_routing` settings key

The canonical capability → provider/model map: `capability → {"provider": "...", "model": "..."}`. Capabilities include `reasoning`, `subagent`, `creative_image`, `creative_video`, `creative_music`, `voice`, `asr`, `tts`, `embedding`, and `local`. The flat keys (`orchestrator_model`, `creative_model`, `voice_model`, …) are derived mirrors kept in sync automatically — edit `capability_routing`, not the mirrors.

### Built-in providers and env-var keys

The six original providers: `anthropic`, `openai`, `ollama-local`, `google-gemini`, `local-voice-lite`, `nvidia-nemo`. Ten additional OpenAI-compatible cloud providers (OpenRouter ships enabled; the rest are one-click templates):

| Provider | Env-var key |
|----------|-------------|
| `openrouter` | `OPENROUTER_API_KEY` |
| `huggingface` | `HF_TOKEN` (aliases: `HUGGINGFACE_API_KEY`, `HUGGING_FACE_HUB_TOKEN`) |
| `groq` | `GROQ_API_KEY` |
| `together` | `TOGETHER_API_KEY` |
| `fireworks` | `FIREWORKS_API_KEY` |
| `mistral` | `MISTRAL_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `xai` | `XAI_API_KEY` |
| `perplexity` | `PERPLEXITY_API_KEY` |
| `cohere` | `COHERE_API_KEY` |

### Custom providers

Drop a provider descriptor (JSON or YAML) into `~/.friday/providers/` and it is loaded automatically — any OpenAI-compatible `/v1` endpoint can be added this way.

---

## Context Pruning

Settings under the `context_pruning` key:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `model` | string | `all-MiniLM-L6-v2` | Sentence-transformer model for embeddings. |
| `max_turns` | integer | `50` | Number of turn pairs before pruning kicks in. |
| `keep_recent` | integer | `4` | Always keep this many recent turn pairs verbatim. |
| `top_k` | integer | `10` | Number of semantically relevant archived turns to retrieve. |

### Example

```json
{
  "context_pruning": {
    "max_turns": 40,
    "keep_recent": 6,
    "top_k": 15
  }
}
```

---

## Context Compression

Settings under the `context_compression` key:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | boolean | `true` | Enable Headroom compression. |
| `min_tokens_to_compress` | integer | `1000` | Minimum estimated token count before compression is attempted. |

### Example

```json
{
  "context_compression": {
    "enabled": true,
    "min_tokens_to_compress": 500
  }
}
```

---

## Privacy Shield

Configuration lives in `~/.friday/privacy_shield.json`:

| Key | Type | Description |
|-----|------|-------------|
| `watchlist` | string[] | Tokens to redact from cloud-bound messages. Add names, account numbers, or other sensitive strings. |

### Example

```json
{
  "watchlist": [
    "John Q. Public",
    "ACCT-12345"
  ]
}
```

Built-in patterns (always active, no configuration needed):
- SSN format: `XXX-XX-XXXX`
- Credit card numbers: 13-19 digit sequences that pass the Luhn checksum
- Phone numbers (US/NANP and international `+country-code` formats)
- Email addresses (except owner's)
- Street addresses (US format)

Watchlist tokens match on word boundaries ("Smith" never corrupts
"SmithKline"); tokens with non-word edges (account numbers) match literally.

PII in Friday's spoken replies never transits Gemini TTS: text containing PII
is synthesized with the local engine, and when that is unavailable Gemini
speaks the scrubbed text only.

---

## Wiki Encryption (opt-in)

The personal wiki (`~/wiki/`) is hand-editable and stays plaintext by
default. To encrypt specific sections at rest with the vault key
(AES-256-GCM + Argon2id, requires `FRIDAY_VAULT_PASSPHRASE` or a passphrase
stored via `friday vault-setup`; `FRIDAY_PASSWORD` works as a legacy fallback):

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `wiki_encrypted_sections` | string[] | `[]` | Wiki top-level sections to encrypt at rest, e.g. `["health", "legal", "family"]`. Existing files are encrypted in place on the next server start; reads, search, smart context, and the wiki UI work transparently. The Google Drive mirror receives ciphertext, never plaintext. Direct file editing of listed sections is no longer possible — use the wiki UI. |

---

## Voice

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `voice_engine` | string | `local` | Voice engine: `local` (Tier-1 on-device, faster-whisper + Piper on CPU — private/offline), `local-gpu` (Tier-2 NVIDIA NeMo on GPU, falls back to Tier-1 without CUDA), `gemini` (Gemini Live cloud voice — needs a key + network), `auto` (GPU tier when ready, else CPU; local preferred over cloud). |
| `voice_model` | string | `gemini-3.1-flash-live-preview` | Gemini Live model used when `voice_engine` is `gemini`. |
| `voice_interruption_mode` | string | `speaker` | `speaker` = no barge-in (echo-safe — Friday always finishes her turn); `headphones` = true barge-in (only safe with no speaker bleed). |
| `local_voice_asr_model` | string | `small` | Tier-1 faster-whisper model size: `tiny`, `base`, `small`, `medium`. |
| `local_voice_tts_voice` | string | `en_US-amy-medium` | Tier-1 Piper voice id. |
| `voice_silence_ms` | integer | `800` | Trailing silence (ms) that ends a local-voice turn. |

---

## Owner Identity

| Key | Type | Description |
|-----|------|-------------|
| `user_email` | string | Owner's primary email (passed through PII scrubber unscrubbed). |
| `owner_email` | string | Alias for `user_email`. |
| `owner_identities` | string[] | Additional email addresses belonging to the owner. |

---

## Context Logging

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `context_logging_enabled` | boolean | `true` | Enable append-only context logging to `~/.friday/vault/context-log/`. |

---

## Authentication

Set via environment variables (not in `settings.json`):

| Variable | Default | Description |
|----------|---------|-------------|
| `FRIDAY_USERNAME` | `admin` | Login username (only for remote access). |
| `FRIDAY_REMOTE_KEY` | _(empty)_ | Login password for non-loopback clients (e.g. via Cloudflare Tunnel). Empty = falls back to `FRIDAY_PASSWORD`; if that is also empty, no auth is required. |
| `FRIDAY_VAULT_PASSPHRASE` | _(empty)_ | Vault-encryption passphrase (AES-256-GCM key derivation via Argon2id). Also settable via `friday vault-setup` (OS keychain). Empty = falls back to `FRIDAY_PASSWORD`. |
| `FRIDAY_PASSWORD` | _(empty)_ | Legacy fallback used for both HTTP auth and the vault KDF when the dedicated variables above are unset. |
| `FRIDAY_SECRET_KEY` | _(auto-generated)_ | Flask session secret. If unset, a random secret is generated once and persisted to `~/.friday/secret_key` (mode `0600`). Set this to pin a fixed value (e.g. across instances). |
| `FRIDAY_TRUST_LOOPBACK` | `1` | When `1`, same-machine (loopback) requests are auto-authenticated. Set to `0` to require login for loopback requests too (only matters when a login password is set). |
| `FRIDAY_WS_TOKEN` | _(empty)_ | Optional shared token required on the `/ws/live` WebSocket regardless of loopback trust (defense-in-depth for voice when remotely exposed). Pass as `?token=…`. |
| `FRIDAY_COOKIE_SECURE` | _(unset)_ | Set to `1`/`true` to mark the session cookie `Secure` (use behind HTTPS / a tunnel). |

---

## Server

| Variable | Default | Description |
|----------|---------|-------------|
| `FRIDAY_PORT` | `3000` | Server port. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Default Claude model (env var override). |

---

## Sandbox

Constrains the `write_file` and `run_command` tools. Set via environment variables (not in `settings.json`):

| Variable | Default | Description |
|----------|---------|-------------|
| `FRIDAY_SANDBOX_MODE` | `confine` | Sandbox enforcement level: `off`, `confine`, or `strict`. |
| `FRIDAY_SANDBOX_ROOT` | _(user HOME)_ | Root directory that `write_file` is confined to. |

**Modes:**
- `off` — No sandbox restrictions.
- `confine` (default) — `write_file` is confined to `FRIDAY_SANDBOX_ROOT`, and `run_command` is filtered through a destructive-command blocklist.
- `strict` — Everything `confine` does, plus `run_command`'s leading command must be on an allowlist.

---

## Full Settings Example

API keys are intentionally absent from this example — they live in the encrypted credential store, not `settings.json` (see [API Keys](#api-keys)). Legacy `anthropic_api_key` / `gemini_api_key` fields found in `settings.json` are migrated into the encrypted store on save.

```json
{
  "orchestrator_model": "claude-sonnet-5",
  "model_routing": {
    "mode": "smart",
    "default_cloud_model": "claude-opus-4-8",
    "ollama_url": "http://localhost:11434",
    "fallback_to_cloud": true,
    "vault_cloud_fallback": "redact"
  },
  "user_email": "you@example.com",
  "context_logging_enabled": true,
  "context_pruning": {
    "max_turns": 50,
    "keep_recent": 4,
    "top_k": 10
  },
  "context_compression": {
    "enabled": true,
    "min_tokens_to_compress": 1000
  }
}
```
