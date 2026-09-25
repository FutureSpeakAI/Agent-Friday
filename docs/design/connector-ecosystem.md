# Connectors — one kind of thing, one answer to "is it working"

> **Status:** specified, not built
> **Last verified:** 2026-09-19
> **Implementation:** none yet. Survey covers `services/connectors.py`,
> `services/google_accounts.py`, `mcp_oauth.py`, `services/platforms/`,
> `services/channels/`, `services/credential_store.py`,
> `services/provider_registry.py`, `services/capability_state.py`
> **Supersedes / superseded by:** —
> **Written:** 2026-09-19

Commission (the maintainer, verbatim): *"Friday should have its own connector
ecosystem too."* Said immediately after *"I don't want a dependency on the
Windows credential store. Friday should have its own credential store"* — which
was built the same day and is the floor this stands on.

---

**Evidence registers:**
- **VERIFIED** — the cited line, file, or runtime behaviour was read or executed
  during the audit run for this document (2026-09-19).
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

---

## 0. What this layer is for, in one paragraph

Friday connects to external services through **seven structurally distinct
mechanisms** that do not know about each other (**VERIFIED**, §1). There is no
type called "connector": there is a Google account, and an MCP server, and a
platform adapter, and a channel bridge, and a provider key, and an OAuth record,
and a legacy token file, and each was built as if it were the only one. The cost
is not aesthetic. "Is this connected?" is computed in **twenty-six places** in
**six incompatible vocabularies** (**VERIFIED**, §2.2), which is why the
connectors page can say a Google account is fine while every Drive call returns
403, why four API keys sat unreadable for weeks while the health surface
reported them as *missing*, and why social platforms are invisible to the
connectors page entirely. This document specifies **one connector protocol, one
health verdict, one credential path and one registry**, and — more important
than any of them — a migration that adapts the seven rather than rewriting
them, because six of the seven currently work and the one thing this change
must not do is break them.

---

## 1. The repository you just landed in

Seven mechanisms (**VERIFIED** — each read during the audit):

| # | Mechanism | Owner | Credentials | Multi-account |
|---|---|---|---|---|
| 1 | Google accounts | `services/google_accounts.py` | `google_accounts/tokens/*.token.enc` via `write_secret` | **yes** |
| 2 | MCP connector registry | `services/connectors.py` | inside `mcp_servers.json`, own `friday-enc:v1:` envelope | no |
| 3 | MCP remote OAuth | `mcp_oauth.py` | `mcp_oauth/*.oauth.enc` via `write_secret` | no |
| 4 | Publishing platforms | `services/platforms/base.py` | `platforms/*.cred` **and** `providers/keys/platform_*.key` | no |
| 5 | Provider API keys | `services/credential_store.py` | `providers/keys/*.key` | no |
| 6 | Channel bridges | `services/channels/` | `providers/keys/channel_*.key` | no |
| 7 | Legacy Google | `services/calendar_engine.py` | `google_token.json`, **plaintext** | no |

Mechanism 1 is the reference implementation and the only one that has had the
health lesson applied. Its own header records why (`google_accounts.py:128-135`,
**VERIFIED**): accounts can sit at `needs_reauth` while a connectors page says
"connected", if the page renders *the presence of the record* — which produces
days of confidently wrong calendar answers, with busy days reported as empty.

Everything in §2 is that same lesson, unlearned somewhere else.

---

## 2. The four faults

### 2.1 A connector is not a thing

There is no `Connector` type, no protocol, no shared base. `PlatformAdapter`
(`platforms/base.py`) and `ChannelAdapter` (`channels/base.py`) are two
independent abstract classes with overlapping jobs; `platforms/__init__.py:6`
says outright that it "mirrors `services/channels/manager.py`" (**VERIFIED**) —
one registry pattern, copied and then improved on only one side. Google accounts
and MCP servers have no base class at all.

The visible consequence: `services/connectors.py` never imports `platforms`
(**VERIFIED**). Social platforms are therefore absent from `/api/connectors`,
`connectors_health()`, `connected_keys()`, `workspace_connectors()`, and the
connector-down notification monitor. A LinkedIn connection that dies is a
connection nothing watches.

### 2.2 Twenty-six answers to one question

"Is this connected?" is computed in 26 distinct places (**VERIFIED**), in six
vocabularies that do not map onto each other:

- `connected | connecting | error | disconnected | needs_setup | blocked_by_policy` — `connectors.py`
- `connected | needs_reauth | revoked | error | disconnected | unreadable` — `google_accounts.py`
- `working | present_unverified | present_failing | unconfigured | absent` — `capability_state.py`
- `connected | present_but_unreadable | missing` — `credential_store.py`
- `ok | down | missing | needs | unknown` — `provider_health.py`
- `ok | rejected | no_credit | unknown` — `key_verdict.py`

Plus `stopped | starting | ready | error | needs_auth | disabled` in `mcp_client.py`.

Three ways they materially disagree today:

**(a) Narrower than they present.** `routes/calendar.py:71` and
`routes/news.py:153` both answer Google connectivity with
`_google_credentials() is not None` (**VERIFIED**). That resolves through
`primary_credentials()` — a live check, but of **the primary account only**
(`calendar_engine.py:233-237`, **VERIFIED**). `news.py` then reports *Gmail* and
*Calendar* as connected off that single check, consulting neither account's
`services` map nor whether the API in question works at all. Measured the same
day: both accounts carry `drive: true` and every Drive call returns
`403 Google Drive API has not been used in project 449982820564` (**VERIFIED**).
The surface says the service is on; the service is off at Google.

**(b) Presence read as health.** `PlatformAdapter.status()` (`base.py:249`,
**VERIFIED**) computes `connected = creds is not None or bool(simple_secret())`.
It stores and displays `expires_at` and never consults it. This is the
2026-09-09 bug, still live, in a mechanism nobody has revisited.

**(c) Two implementations in one file.** `ChannelAdapter.status()`
(`channels/base.py:154`) and `manager.status()` (`channels/manager.py:165`)
answer the same question differently depending on whether the adapter has been
instantiated — the second drops `dependency_ok` entirely (**VERIFIED**).

The cost of six vocabularies is not that they are ugly. It is that **no caller
can ask one question and trust the answer**, so every surface invents its own,
and each new surface is a new opportunity to invent it wrong.

### 2.3 Three credential conventions on one primitive

All three bottom out in `credential_store.protect()`, and nothing enumerates
them together (**VERIFIED**):

1. `write_secret(path)` with a bespoke path — Google tokens, MCP OAuth, `.cred` files
2. `set_provider_key(name)` with a name prefix — `platform_*`, `channel_*`, `google_oauth_client_*`, `brave`, `firecrawl`
3. `connector_secrets` base64-in-JSON envelope — MCP server env vars

Convention 2 is a flat namespace with prefixes doing the work of structure:
`list_provider_keys()` returns AI providers, platform secrets, channel tokens,
Google client credentials and search keys mixed together (**VERIFIED**).

This directly produced today's incident. `credential_store._credential_files()`
— written this morning for the keystore migration — is the **first** code in the
repo that enumerates every credential Friday holds, and the first run of it found
five that had been silently unreadable: four provider keys and one MCP OAuth
token (**VERIFIED**, recovered). The health surface had been reporting the
Firecrawl key as *missing*. It was present and unopenable. Nothing could tell
the difference because nothing was looking at all of them at once.

There are also at least four API-key resolution orders (`provider_descriptors`,
`web_search.brave_key`, `firecrawl`, `cloud_voice._api_key`), and the last of
them **skips the credential store entirely** (**VERIFIED**).

### 2.4 A catalogue that isn't

Nine separate registries of what *could* be connected (**VERIFIED**):
`CONNECTOR_DEFS` (6 entries, the only one with a field schema for credential
entry), `ADAPTER_MODULES` (12), `DEFAULT_PROVIDERS` + `PROVIDER_TEMPLATES` (16),
`_default_mcp_servers()`, `capability_state.PROBES` (a fixed 5-tuple),
`capability_router.CAPABILITIES`, `capability_preflight.CAPABILITIES`,
`cloud_voice.PROVIDERS`, and — for channels — no registry at all, just the
literal `("telegram", "discord")` repeated in three functions.

Adding a connector therefore means knowing which of nine lists to edit, and the
answer depends on what kind of thing it is, which is exactly the distinction
§2.1 says should not exist.

---

## 3. The design

### 3.1 One protocol

A `Connector` is a thing with an identity, a way to authenticate, a way to say
whether it is working, and a way to be used. Formally — `services/connectors/protocol.py`:

```python
class Connector(Protocol):
    id: str                  # "google", "slack", "linkedin", "telegram"
    label: str
    category: str            # productivity | publishing | messaging | intelligence
    auth: AuthSpec           # how to connect (§3.6)
    multi_account: bool

    def accounts(self) -> list[AccountRef]: ...
    def health(self, account_id: str | None = None) -> Health: ...
    def connect(self, ...) -> ConnectResult: ...
    def disconnect(self, account_id: str | None = None) -> None: ...
```

The seven existing mechanisms become **adapters onto this protocol**, not
rewrites. That is the whole migration strategy and §4 is about nothing else.

### 3.2 One health verdict

One vocabulary, derived — never stored as the answer:

```python
@dataclass(frozen=True)
class Health:
    state: str        # working | degraded | needs_user | unreadable | absent | unknown
    healthy: bool     # may this be used right now
    actionable: bool  # is there something the USER can do
    action: str|None  # "reconnect" | "enable_api" | "add_key" | "unlock" | None
    summary: str      # one sentence, for a human
    detail: str       # the machine's own words, never dropped
    checked_at: float
    stale: bool
```

Four rules, each one bought with a specific bug:

1. **Fails closed.** An unrecognised, missing or unmapped state is `unknown`
   with `healthy=False`. Derived from `account_health`'s `_UNKNOWN_PRESENTATION`,
   which exists because the connectors page once rendered record-presence as
   health.
2. **Distinguishes whose problem it is.** `needs_user` (reconnect at the
   provider) is a different state from `unreadable` (Friday cannot decrypt this
   locally) is a different state from `degraded` (connected, but this specific
   capability is off — Drive). Today's Google fix established the first
   distinction; Drive's 403 is the case that requires the third.
3. **A verdict can improve.** Fixed this morning in `credentials_for`: only a
   successful token *refresh* ever wrote "connected" back, so an account marked
   bad by one transient failure stayed bad while its token remained valid. A
   verdict that can only get worse is not a health check.
4. **The backend's own words survive.** `detail` carries the 403 body, the
   `IntegrityError`, the HTTP 202. Every incident today was made harder by a
   surface that replaced a specific error with a generic one.

`healthy` is the **only** field any caller may gate on. The six existing
vocabularies become mappings into this one, kept for now (§4) and deleted later.

### 3.3 One credential path

Every connector credential goes through `credential_store.write_secret` /
`read_secret`, which already funnel into the keystore built today. Two changes:

- **A structured namespace.** `credential://<connector_id>/<account_id>/<purpose>`
  resolving to `~/.friday/credentials/<connector_id>/<account_id>/<purpose>.enc`.
  Replaces prefix-as-structure (`platform_bluesky`, `channel_telegram`).
- **An enumerable inventory.** `credential_store.inventory()`, generalising
  `_credential_files()`. Every credential Friday holds, with its connector, its
  account, and whether it currently *opens*. That function found five dead
  credentials on its first run; it should be a permanent surface, not a
  migration helper.

`connector_secrets`' `friday-enc:v1:` envelope stays for `mcp_servers.json`,
because that file is served to the browser still-encrypted on purpose
(**VERIFIED**) and that property is load-bearing.

### 3.4 One registry

One catalogue, `services/connectors/registry.py`, holding for each connector:
identity, category, auth spec, field schema for credential entry, capabilities,
docs URL, setup hint. `CONNECTOR_DEFS` is the closest existing model and is the
only registry with a field schema — it is the right starting shape.

Registration is by declaration, not by editing a list in the middle of a
function. `ADAPTER_MODULES`' tolerant-import behaviour is the pattern to keep: a
connector whose module fails to import becomes `available: False` with the
import error attached, never a crash on startup.

### 3.5 Multiple accounts, for everything

`multi_account` becomes a property of the connector rather than a privilege
Google alone has. Even where a connector supports one account today, the API
shape is the multi-account one (`accounts()`, `health(account_id)`), so adding
a second Slack workspace later is a change inside one adapter rather than a
change to every caller.

### 3.6 OAuth as shared infrastructure

There are currently **two** OAuth state registries with identical 900-second
TTLs and no shared code, and **at least three** PKCE implementations
(`mcp_oauth._pkce_pair`, `x_twitter`, `tiktok`, plus Mastodon's) (**VERIFIED**).
One `services/connectors/oauth.py` owning: state issue/consume with TTL, PKCE
S256, loopback redirect, dynamic client registration, token refresh with
per-connector locking. `mcp_oauth.py` is the most complete of the existing
implementations and should be the donor.

---

## 4. Migration — adapt, do not rewrite

**Six of the seven mechanisms currently work.** The failure mode of this project
is a rewrite that breaks Google accounts to make a diagram tidier.

**Phase 1 — the verdict, alone.** Add `Health` and map all six vocabularies onto
it. Change no storage, no auth, no routes. `/api/connectors/health` gains a
unified view; every existing surface keeps its current output. Ends with every
mechanism answerable in one vocabulary and nothing else moved.

**Phase 2 — the liars.** Fix the verdict sites that answer a narrower question
than they present: `routes/calendar.py:71`, `routes/news.py:153`,
`PlatformAdapter.status`, `channels.manager.status`, `/api/health/full`'s bare
record count. Each becomes a `Health` call. This is where the owner stops being
told Drive works.

**Phase 3 — the protocol.** Adapters for all seven onto `Connector`. Still no
storage change. Ends with `/api/connectors` listing social platforms and channel
bridges for the first time.

**Phase 4 — the registry.** One catalogue; the nine become views over it or are
deleted.

**Phase 5 — credential paths.** The structured namespace, with a migration in
the shape of this morning's: round-trip verified, originals backed up, anything
unreadable left alone and reported, second run free.

**Phase 6 — OAuth consolidation.** Highest risk, least user-visible benefit,
therefore last.

Each phase ships independently and leaves the system working. Any phase can be
where we stop.

---

## 5. What this is not

- **Not a plugin system.** Third-party connectors, sandboxing and a marketplace
  are a different document. This unifies what exists.
- **Not a change to the Sovereign Vault.** Untouched.
- **Not an MCP replacement.** MCP stays the transport for tool-bearing
  connectors; this is the layer that knows whether one is working.
- **Not multi-user.** One person's connectors on one machine.

---

## 6. Open questions for the maintainer

1. **Does a connector own its data, or only its access?** Today Google tokens
   live in `google_accounts/` and Gmail message caches live elsewhere. If a
   connector owns both, disconnecting can offer to forget the data too. That is
   a privacy posture, not an architecture choice. **UNKNOWN.**
2. **What happens to a connector that is healthy but unused for months?**
   Silently keep refreshing its tokens, or surface it for pruning? Standing
   access nobody is using is the thing a sovereignty-minded product might
   notice. **UNKNOWN.**
3. **Is `/api/channels` dead?** It has a complete HTTP surface and zero calls
   from `index.html` (**VERIFIED**). Wire it into the new connectors page, or
   remove it? **UNKNOWN.**
4. **Phase 2 changes what the briefing believes.** Once Drive reports `degraded`
   instead of connected, briefings that quietly returned nothing will start
   saying why. Right, but louder. Confirm that is wanted. **UNKNOWN.**

---

## 7. Build order

Phases 1 and 2 are the ones that pay for themselves immediately: they are small,
they are testable without touching credentials, and they end the specific class
of failure that has cost the most this month. Phase 2 in particular fixes bugs
the owner is hitting today.

Phases 3–6 are the actual unification and can wait behind anything more urgent.

Recommended first commit: `Health` plus the six mappings plus their tests, with
every existing surface unchanged — provable by the existing suite staying green.
