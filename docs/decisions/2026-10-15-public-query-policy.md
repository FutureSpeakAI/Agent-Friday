# Public query text is not private because of its topic

**Status:** implemented (branch fix/public-query-policy). Sensitive subsystem
(privacy/, the egress gates).
**Decided:** 2026-10-15, by the owner (decision (a) in the 2026-10-14 field install
report, item 17). **Amended:** 2026-10-16, by the owner: a search that shares
private text from the conversation asks with a preview instead of following a
blanket rule (see "Ask with preview").

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
- **A tagged query.** A cloud `search_web` / `deep_research` query containing
  a `[PII:...]` tag that would be filled in raises the ask-with-preview card
  below, showing the filled-in text. A query that reaches the search layer
  without passing through the agent loops' hold keeps the topic gate after
  rehydration, as before.

## Ask with preview (amendment, 2026-10-16)

The owner: "No more blanket rules; the user is the determining factor, so long
as we've been transparent with them." A cloud seat's query is one of three
things:

- **Public topic, nothing private shared:** goes through, no card.
- **Shares private text from the conversation:** an approval card shows, in
  plain language, the exact text that would go out and the search engine(s)
  it goes to, by name and in the order the search layer tries them
  (`web_search.backend_chain`: Wigolo, Firecrawl, Brave Search, DuckDuckGo),
  with **Send** and **Don't send**. Nothing is sent before Send. Don't send
  gives the model a plain `[DECLINED]` note for that call and posts a plain
  note into the conversation; no query text is in either.
- **Carries a floor item** (the table below): refused, no card. The floor is
  the owner's own standing choice; an approved card's text meets it again at
  the search gate.

"Private text" is (1) a run of six or more consecutive words
(`public_query.MIN_SHARED_RUN_WORDS`) that appears verbatim in non-public
context sent to that seat in the request it is answering -- the system prompt,
the owner's messages and tool results, less the paragraphs the egress gate
holds as published (search rows, news, fetched pages), as Friday's constants,
or as that seat's own echo -- e.g. an email line or a health note; or (2) a
`[PII:...]` tag the seat used that would be filled in with the real value (a
name, a number). Words are compared folded and lower-cased.

A paraphrase does not trigger a card: the check is verbatim runs and
filled-in tags, so a seat that restates private context in its own words is
not caught. That is a known limit of a deterministic check.

It applies to `search_web` and `deep_research` (`public_query.CARD_TOOLS`),
judged by the tool that will actually run (a near-miss name such as
`search.web` is resolved first, in both agent loops). `search_news` does not
ask: its query is matched against the news feed already on this computer and
is not sent anywhere. A cloud voice seat asks too: the card waits on screen
and the seat is told so. Local seats never ask.

The card shows all of what Send sends, faithfully, or it is not raised: every
public query string, and for `deep_research` its `context` ("Background:");
nothing longer than 1000 characters (what the approvals store keeps); no
invisible format characters (Unicode Cf, bidi controls). Such a query is
`[NOT RUN]` with the reason. An approved card runs only while its stored text
is still exactly what its input would show (`public_query.approved_texts`).
For `deep_research`, Send starts the job with the text shown; the searches the
job derives from it each go through the standard privacy gate, and the card
says so.

Only the Send button on screen approves a search card
(`approvals.PUBLIC_QUERY_CARD_APPROVERS` = `owner:ui`): not voice, not a text
message (it is never offered by text, and a YES code is refused), not a chat
claim, not a batch. Declining is open to every channel. The report posted
after Send names the card, not the query, and is PII-scrubbed.

Mechanism: the existing approval path, nothing new. Both agent loops call
`agent._public_query_hold` after `check_action` passes and before the tool
runs; it raises an ordinary `tool_confirm` card whose payload carries the
input as it would run plus `public_query` (provider, model, engine, the exact
texts). `approval_executor` runs an approved card's input inside
`public_query.approved_scope`, so the search gates apply only the floor to
exactly the approved strings. The card's title and description, which a
pending-card notification carries, never hold the query; the text is the
card's action line only. In the UI the generic approval card shows that text
in a monospace block, a "Goes to:" line and Send / Don't send.

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
  ONLY when `public_query.is_cloud_query(provider, value, model)`: the seat
  the history is being sent to (that provider and that model) wrote exactly
  that string and `check_action` passed it. That provenance registry
  (bounded, oldest evicted first) is written only by `check_action`'s allow
  path for a cloud provider's public query, under the loop's provider name
  and model (`author_provider`, `author_model`); `seal_outbound` checks it
  against the model the payload names. A replayed query a local seat wrote,
  one another provider or another model wrote, and one that was refused
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
- `services/agent._public_query_hold` / `_public_query_card`: the
  ask-with-preview card, at both agent-loop dispatch sites
  (`public_query.needs_approval` decides).
- `services/approval_executor`: runs an approved search card inside
  `public_query.approved_scope`; posts the Don't send note.
