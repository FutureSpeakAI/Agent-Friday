# Widening the key-injecting proxy to the other providers

Status: plan only (A3 of the Compressor eval, finding K2). No code in this document's PR. Owner of the build: whoever the
release lead assigns; the BURN-DOWN lane wrote the plan.

## Where we are

- `services/salon_proxy.py` (`KeyProxy`) is the one working proxy. Loopback only, provider API paths only, the key is
  read at request time and never leaves the process, standard library only. Used by `claude_engine.py` for Anthropic.
- Today 54 modules read a raw key (the list is `scripts/raw_key_readers.allow.json`, held down by
  `scripts/check_raw_key_readers.py`), through the environment, `get_provider_key`, `provider_api_key`, or a getter such as `api_key()` or a channel's `token()` (a few may be false positives; the list is a ceiling, not a verdict). Keys are decrypted into the environment at boot.
- The interactive-session child no longer inherits the environment (A1, PR `lane/burndown-child-env`).
  Other spawn sites that still copy the whole environment: `voice_workers.py`, `media_convert.py`, `podcast_render.py`,
  `g2p_fallback.py` (the first four are PR #861), `cli.py` (two), `setup_wizard.py`, and the ones that pass the
  environment minus a few names: `repo_intake.py` (git, minus GIT_*), `career_ops.py` and `task_eval/runner.py`
  (deny-list shaped; an allow-list is the safer form). Each is a separate small PR: first decide whether the child
  needs any key; if not, use `sanitize_env_for_mcp(..., "sandboxed")`.

## What the proxy cannot do yet (found while reading it)

1. The header is fixed: it always sets `x-api-key`. Other providers name it differently (a subscription-token header,
   a bearer `Authorization`, a vendor header). The key placement must come from a table row, not from code.
2. One upstream per instance. A table of providers needs one listener per provider (simplest, keeps each listener's
   allow-list tiny) or one listener that routes by a path prefix it owns (`/p/<provider>/...`). Recommendation: one
   listener, prefix routing, because callers then need one loopback port.
3. The prefixes and methods are constructor parameters already (default `/v1/`, GET and POST), so a row only supplies its own.
4. Streaming works (chunked relay). WebSocket upgrade does not: `upgrade` is stripped. The realtime voice engines
   (`openai_realtime.py`, `voice_engine.py` for Gemini live) use sockets, so they are a separate slice.

## The table

One row per provider, in code, reviewed like a settings schema:

| field | meaning |
|---|---|
| `name` | `brave`, `firecrawl`, `elevenlabs`, `openai`, ... |
| `upstream` | one https origin; no redirects followed to another host |
| `prefixes`, `methods` | the only paths and verbs that pass |
| `key_header`, `key_format` | where the key goes (`X-Subscription-Token: {key}`, `Authorization: Bearer {key}`) |
| `key_source` | the credential-store name, read per request |
| `max_body` | request size cap |

Invariants kept from `salon_proxy.py`, each with a test per provider: loopback only; unknown provider is 404; path
outside the prefixes is 403 and never sent upstream; no key means 401 and nothing sent; the caller's own credential
headers are stripped and replaced; no body or header value is recorded; the key appears nowhere but the outgoing
header (a test greps the capture, the logs and the response). A redirect is never followed (`http.client` does not follow one); keep a test
that says so, so a later change of client cannot make a key follow a redirect.

## Built so far

- `services/key_gateway.py`: the provider table (`PROVIDERS`), one `KeyProxy` listener per provider (chosen over one
  prefix-routed listener: each listener's allow-list stays tiny and the proxy's invariants carry over unchanged),
  `url()`, `available()`, `sources()`, `stand_in()`. `KeyProxy` gained `key_header` / `key_format`.
- Brave search (`web_search.py`, `news_engine.py`, `routes/news.py`) is migrated and off the allow-list. `brave_key()`
  now returns the stand-in, never the key.

## Order of work

1. **Brave search first** (`web_search.py`, `news_engine.py`, `routes/news.py`): a plain GET, no streaming, three
   callers, one header (`X-Subscription-Token`). Proves the table and the per-provider header.
2. **Firecrawl** (`firecrawl.py`): one caller, bearer header, POST with JSON body.
3. **ElevenLabs REST** (`elevenlabs_tools.py`, `cloud_voice.py`): audio bodies, so the body cap and binary relay get
   tested.
4. **OpenAI / OpenRouter chat** (`model_router.py`): streaming, the highest-traffic path; do it last of the REST ones.
5. **Realtime voice sockets**: its own design (upgrade handling, per-session expiry). Not started here.

Each step is a small PR with a red twin, and ends by removing that module from `raw_key_readers.allow.json`
(the guard then keeps it from coming back). Hot files (`agent.py`, `approval_executor.py`, egress gate) are claimed on
the LEDGER first.

## After the proxy covers a provider (A4, not planned in detail)

Stop decrypting that provider's key into the environment at boot; callers get the loopback URL and the dummy key.
Do it provider by provider so a regression costs one provider, and keep the boot decryption for a provider until its
last raw reader is off the allow-list.

## Open questions for the release lead

- One listener with prefix routing (recommended) or a listener per provider?
- Who owns the provider table, and does a new row need a card the owner presses (a new provider can spend money)?
- Settings that hold a key today (the `*_api_key` settings) are read by the UI; the settings schema is a hot file.
  Confirm the order before touching it.
