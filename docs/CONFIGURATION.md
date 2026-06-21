# Configuration Reference

All configuration lives in `~/.friday/settings.json`. Settings can be updated via the UI, the `POST /api/settings` endpoint, or by editing the file directly (restart required for some changes).

---

## API Keys

| Key | Type | Description |
|-----|------|-------------|
| `anthropic_api_key` | string | Anthropic API key for Claude (`sk-ant-...`). Required. |
| `gemini_api_key` | string | Google AI Studio key (`AIza...`). Optional — enables TTS, creative tools, and voice mode. |

Keys can also be set via environment variables (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`), which take precedence over the settings file.

When the OpenAI-compatible cloud provider is enabled (see [Model Routing](#model-routing)), an API key may also be supplied via environment variables:

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | Fallback API key for the OpenAI-compatible provider (used when `model_routing.openai_api_key` is blank). |
| `OPENROUTER_API_KEY` | Alternate fallback API key for the OpenAI-compatible provider (e.g. OpenRouter). |

---

## Model Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `orchestrator_model` | string | `claude-sonnet-4-6` | Default Claude model for chat. Options: `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-8`. |
| `default_cloud_model` | string | `claude-opus-4-8` | Cloud model used by the router when no override is specified. |

---

## Model Routing

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `cloud_only` | Routing mode: `cloud_only`, `local_preferred`, `smart`. |
| `fallback_to_cloud` | boolean | `true` | Fall back to cloud when Ollama is unavailable. |
| `ollama_url` | string | `http://localhost:11434` | Ollama API endpoint. |
| `vault_cloud_fallback` | string | `redact` | Behavior when vault access is needed but no local model is available: `redact` (proceed with gated content), `deny` (refuse), `warn` (refuse and notify). |
| `task_overrides` | object | `{}` | Per-task-type routing overrides. Keys: `simple`, `tool_use`, `code`, `research`, `voice`, `vault_access`. Values: `{"provider": "local"|"cloud", "model": "..."}`. |
| `cloud_provider` | string | `anthropic` | Provider for cloud turns: `anthropic` (default) or `openai` (route through an OpenAI-compatible endpoint). |
| `openai_base_url` | string | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible endpoint. Works with OpenRouter and any `/v1` endpoint (Together, Groq, vLLM, LM Studio, OpenAI). |
| `openai_model` | string | `anthropic/claude-3.7-sonnet` | Model name passed to the OpenAI-compatible endpoint. |
| `openai_api_key` | string | _(empty)_ | API key for the OpenAI-compatible endpoint. Blank → falls back to env `OPENAI_API_KEY` / `OPENROUTER_API_KEY`. |

> **OpenAI-compatible provider.** When `cloud_provider` is `openai`, cloud turns route through the configured `/v1` endpoint with a full agentic tool loop (parity with the Anthropic path) when the model supports tool-calling. The default settings leave Anthropic behavior unchanged. Vault and TIER_2/TIER_3 requests always stay on the local/Anthropic path and are never sent to the OpenAI endpoint.

### Routing configuration example

```json
{
  "mode": "smart",
  "ollama_url": "http://localhost:11434",
  "fallback_to_cloud": true,
  "vault_cloud_fallback": "deny",
  "task_overrides": {
    "code": { "provider": "local", "model": "qwen3:32b" }
  }
}
```

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
(AES-256-GCM + Argon2id, requires `FRIDAY_PASSWORD`):

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `wiki_encrypted_sections` | string[] | `[]` | Wiki top-level sections to encrypt at rest, e.g. `["health", "legal", "family"]`. Existing files are encrypted in place on the next server start; reads, search, smart context, and the wiki UI work transparently. The Google Drive mirror receives ciphertext, never plaintext. Direct file editing of listed sections is no longer possible — use the wiki UI. |

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
| `FRIDAY_PASSWORD` | _(empty)_ | Login password. Empty = no auth required. |
| `FRIDAY_SECRET_KEY` | _(auto-generated)_ | Flask session secret. If unset, a random secret is generated once and persisted to `~/.friday/secret_key` (mode `0600`). Set this to pin a fixed value (e.g. across instances). |
| `FRIDAY_TRUST_LOOPBACK` | `1` | When `1`, same-machine (loopback) requests are auto-authenticated. Set to `0` to require login for loopback requests too (only matters when `FRIDAY_PASSWORD` is set). |
| `FRIDAY_WS_TOKEN` | _(empty)_ | Optional shared token required on the `/ws/live` WebSocket regardless of loopback trust (defense-in-depth for voice when remotely exposed). Pass as `?token=…`. |
| `FRIDAY_COOKIE_SECURE` | _(unset)_ | Set to `1`/`true` to mark the session cookie `Secure` (use behind HTTPS / a tunnel). |

---

## Server

| Variable | Default | Description |
|----------|---------|-------------|
| `FRIDAY_PORT` | `3000` | Server port. |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Default Claude model (env var override). |

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

```json
{
  "anthropic_api_key": "sk-ant-...",
  "gemini_api_key": "AIza...",
  "orchestrator_model": "claude-sonnet-4-6",
  "default_cloud_model": "claude-opus-4-8",
  "mode": "smart",
  "ollama_url": "http://localhost:11434",
  "fallback_to_cloud": true,
  "vault_cloud_fallback": "redact",
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
