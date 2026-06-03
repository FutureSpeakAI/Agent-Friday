# Configuration Reference

All configuration lives in `~/.friday/settings.json`. Settings can be updated via the UI, the `POST /api/settings` endpoint, or by editing the file directly (restart required for some changes).

---

## API Keys

| Key | Type | Description |
|-----|------|-------------|
| `anthropic_api_key` | string | Anthropic API key for Claude (`sk-ant-...`). Required. |
| `gemini_api_key` | string | Google AI Studio key (`AIza...`). Optional — enables TTS, creative tools, and voice mode. |

Keys can also be set via environment variables (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`), which take precedence over the settings file.

---

## Model Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `orchestrator_model` | string | `claude-sonnet-4-6` | Default Claude model for chat. Options: `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-7`. |
| `default_cloud_model` | string | `claude-opus-4-7` | Cloud model used by the router when no override is specified. |

---

## Model Routing

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `cloud_only` | Routing mode: `cloud_only`, `local_preferred`, `smart`. |
| `fallback_to_cloud` | boolean | `true` | Fall back to cloud when Ollama is unavailable. |
| `ollama_url` | string | `http://localhost:11434` | Ollama API endpoint. |
| `vault_cloud_fallback` | string | `redact` | Behavior when vault access is needed but no local model is available: `redact` (proceed with gated content), `deny` (refuse), `warn` (refuse and notify). |
| `task_overrides` | object | `{}` | Per-task-type routing overrides. Keys: `simple`, `tool_use`, `code`, `research`, `voice`, `vault_access`. Values: `{"provider": "local"|"cloud", "model": "..."}`. |

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
- Credit card numbers: 13-19 digit sequences
- Phone numbers (US format)
- Email addresses (except owner's)
- Street addresses (US format)

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
| `FRIDAY_SECRET_KEY` | `friday-default-secret-change-me` | Flask session secret. Change this in production. |

---

## Server

| Variable | Default | Description |
|----------|---------|-------------|
| `FRIDAY_PORT` | `3000` | Server port. |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Default Claude model (env var override). |

---

## Full Settings Example

```json
{
  "anthropic_api_key": "sk-ant-...",
  "gemini_api_key": "AIza...",
  "orchestrator_model": "claude-sonnet-4-6",
  "default_cloud_model": "claude-opus-4-7",
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
