# Public query text is not private because of its topic

**Status:** implemented (branch fix/public-query-policy). Sensitive subsystem
(privacy/, the egress gates).
**Decided:** 2026-10-15, by the owner (decision (a) in the 2026-10-14 field install
report, item 17).

---

## The rule

Public web/news search text is NOT classified private because of its topic.
With or without cloud consent, provider-authored public query text is checked
only against the never-send floor and the owner's personal identifiers. Health
and civil-rights topics are no longer blocked from cloud seats.

Public query text means the values of these tool arguments (the list lives in
`privacy/public_query.PUBLIC_QUERY_TOOLS`):

| Tool | Field(s) |
|---|---|
| `search_web` | `query` |
| `search_news` | `query` |
| `deep_research` | `question`, `sub_questions` |

`browse_web.url` is deliberately NOT on the list. The decision covers search
text; a URL is an address the model composes, and its query string can carry
anything from the context to whatever host it names (a prompt-injected cloud
seat could exfiltrate that way). A URL keeps the ordinary topic classification
in `check_action` and in the Firecrawl scrape gate, exactly as before.

## Whose text: the author, not the search backend

The rule is about text a CLOUD provider authored. That provider already holds
every word of its own query. A query written by a local seat, or by a
background job from local context (the deep-research harness's follow-up
queries, setup research), was never seen by a cloud provider, and the topic
check is what keeps it on the device. So:

- `check_action` applies the floor to a cloud provider's tool call; the
  provider it judges is the author.
- The relaxation covers only the EXACT strings the cloud seat wrote, never a
  whole tool call. `_execute_tool` puts real values back in place of the
  `[PII:kind:hash]` tags a cloud seat uses (`_restore_placeholders`) before
  the search layer gates the query; the restored query carries text (a
  contact's name) the cloud seat never saw, so it is not provider-authored.
- `public_query.for_tool_call(provider, model, tool, args)` wraps tool
  execution in exactly three places: the Anthropic agent loop, the
  OpenAI-compatible agent loop (`services/agent.py`) and the live voice
  dispatcher (`services/voice_engine`, voice parity). It is handed the tool
  input AS THE CLOUD SEAT SENT IT (`tu.input` / `targs` / the voice args,
  before rehydration) and records the public-field strings, only for a tool on
  the list above and only when `VaultAccessControl.can_access(provider, model)`
  is False (a cloud seat, including a cloud-tagged Ollama model). The provider
  is the loop's own (`_LOOP_PROVIDER`), falling back to the session's. The
  voice rule layer's call on the owner's own spoken words is not
  cloud-authored and is not marked.
- The search layers (`web_search._gate_search_query`, Firecrawl's search gate)
  are handed the *backend* as `provider`, which says nothing about the author.
  They apply the floor only when `public_query.cloud_author_of(query)` finds
  the outgoing query to be exactly one of the recorded strings (after the same
  outer-whitespace strip). Anything else -- a rehydrated query, a query a tool
  built -- keeps the egress gate's topic-classifying `_gate_text` path,
  unchanged, with its old refusal text and `NeverSendBlocked` handling.
- The deep-research harness runs its searches on a background thread, outside
  the mark, so its sub-question and follow-up searches keep the topic gate.
  (`judgment_gate.dry_run` still decides whether the cloud scoper sees a
  question at all; that is routing, not a refusal, and is unchanged.)

## Known limits

- **Cloud-asked deep research.** `check_action` lets a cloud seat's
  `deep_research.question` / `sub_questions` through on the floor, but the
  harness's own searches (sub-question searches and follow-ups) run on a
  background thread outside the author mark, so they still take the ordinary
  topic gate and a health or civil-rights sub-search can still be withheld.
  Deliberately left as is: those query strings are composed by the harness,
  not the cloud seat. Carrying exact cloud strings into the harness is a
  separate change.
- **A tagged query.** A cloud query containing a `[PII:...]` tag is gated by
  topic after rehydration, so "<contact> + health topic" is withheld. That is
  the intent; the cloud seat can search the topic without the name.

## What still blocks

| Reason | What it catches |
|---|---|
| `never_send_list` | a token on the owner's never-send list, a raw vault-document marker, or a deny-marked span (`judgment_gate.never_send_hits`); a file-grant override still covers exactly the paragraphs it registered |
| `credential` | keys, tokens, passwords, private keys (`secret_patterns.contains_secret`, the judgment gate's api_key shape) |
| `id_or_account_number` | SSN, 9-digit routing/account number, Luhn-valid card (`judgment_gate.hard_identifier_hits`) |
| `owner_identifier` | the privacy watchlist, `user_email` / `owner_email` / `owner_identities`, the phone `owner_cell` |
| `floor_unavailable` | the floor itself failed; the text is refused (fail closed) |

The text is checked in every form it could take on the wire: as written, after
each pass of URL/form decoding (`unquote_plus` until stable, at most three
passes, so a double-encoded token is still seen), and in the digits-only form
of every digit run separated by dots, dashes, underscores or slashes (so
`123.45.6789` is checked as the SSN it spells), each form also NFKC-normalised
with format characters (Unicode category Cf: zero-width spaces and joiners,
soft hyphen, word joiner) removed, so fullwidth digits and a zero-width-split
never-send token are seen. Space-separated numbers are not
joined, because the SSN and card patterns already accept spaces and joining
"top 100 of 2024" would invent IDs.

Every other argument of these tools (for example `deep_research.context`) and
every other tool keep the ordinary topic classification, exactly as before.
Local seats were never gated here and still are not.

### What counts as the owner's personal identifiers

`owner_identifier` matches only what Friday has been told: the privacy
watchlist, the `user_email` / `owner_email` / `owner_identities` settings and
the phone config's verified `owner_cell`. The owner's name, home address and
date of birth count as owner identifiers **only when they are on the privacy
watchlist or in `owner_identities`**; nothing infers them. First-run setup
should fill `owner_identities` (name, address, date of birth) so the floor
knows them from day one; that belongs to the first-run spec, not to this
change.

## The matching layers

- **History replay.** `egress_gate._gate_tool_use` / `_gate_tool_calls` gate
  the arguments of tool calls replayed in history on the next cloud turn. A
  public field of a tool on the list goes to `public_query.check` (the floor)
  ONLY when `public_query.is_cloud_query(provider, value)`: the provider the
  history is being sent to wrote exactly that string and `check_action`
  passed it. That provenance registry (bounded, oldest evicted first) is
  written only by `check_action`'s allow path for a cloud provider's public
  query, under the loop's provider name (`author_provider`). A replayed query
  a local seat wrote, one another provider wrote, and one that was refused
  keep the ordinary `_gate_arg_values` path, exactly as on main. A floor
  refusal withholds the value (`[withheld by egress gate]`, logged with
  component and reason). Other arguments and other tools are gated as before.
- **Search result rows.** The `search_web` tool renders each result row as its
  own paragraph (`N. title` / snippet / url, a blank line between rows) and
  registers that EXACT rendered paragraph with
  `egress_gate.register_public_text(origin="web-search:<backend>")`. The span
  pass splits on blank lines and lets a paragraph through only when it is
  exactly a registered string; nothing exempts a paragraph because each of its
  lines is registered on its own (a private list of published titles is still
  classified). Raw titles/snippets/urls are not registered. The result's
  header line repeats the query: it is registered as a provider echo for the
  cloud provider only when the query is exactly what that provider wrote
  (`cloud_author_of`); otherwise it is ordinary text.

## Transparency

Every refusal names its component (`privacy.public_query` or
`privacy.vault_access`) and its reason, never the matched value. It is logged
at WARNING to friday.log through the `friday.privacy.*` loggers, kept in
`vault/access-log.jsonl` (check_action) or `vault/egress-log.jsonl` (search,
Firecrawl and replay gates), and the model's tool result tells it to report the
skipped step to the user instead of leaving it out silently. The old `print()`
verdicts are gone because a pythonw launch has no console. The research
harness no longer writes a query that returned nothing into the commission
log; it logs the status and reason only.

## Where it is enforced

- `privacy/public_query.py`: the floor (`check`, `gate_outbound`), the
  exact-string author mark (`for_tool_call`, `cloud_authored`,
  `cloud_author_of`) and the replay provenance registry
  (`record_cloud_query`, `is_cloud_query`).
- `privacy/vault_access.VaultAccessControl.check_action`: the per-tool-call
  gate. For a cloud provider, public query fields go to the floor and the rest
  of the arguments are classified as before. Details are `public_query_ok` or
  `public_query_refused:<reason>`.
- `services/web_search._gate_search_query`: the query before Brave, DuckDuckGo,
  Firecrawl or wigolo (floor when cloud-authored, topic gate otherwise).
- `services/firecrawl._gate_outbound`: the Firecrawl search query (same split);
  the scrape URL always takes the topic gate.
- `services/egress_gate._gate_tool_args`: replayed public query fields the
  receiving provider itself wrote.
- `services/agent._tool_search_web`: row paragraphs and the header echo.
