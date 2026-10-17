# Adding a connection

> **Status:** current · **Last verified against the code:** 2026-10-17 (main b605f2216)

"Connect your services" is one screen shown in three places: the setup chat's
Connect step, the top of **Settings > Connections**, and step 5 of the
terminal wizard. Each service is a card with a verified sign-up link, three
steps, a cost note where the vendor states one, what the connection can
reach, a masked key field and **Test connection**. This page is how a
developer adds a card. The user's view is in the
[Settings guide](../user-guide/settings.md) and the
[setup chat guide](../user-guide/setup-chat.md).

What a card shows is data. A new keyed AI model provider gets its card, its
Test connection probe, key saving and "turns on when a key is saved" from
existing code; you add a registry entry, a guide entry, a verified link and
its spoken names.

## The rules the data keeps

These come from the docstring of `src/agent_friday/services/connections_guide.py`
and the tests that enforce them:

- **Only services the code can use are connectable.** A service Friday cannot
  use yet goes in `COMING_SOON` with no key field and no sign-up link, so
  nobody pays for a key Friday cannot use.
- **Every link is checked by hand.** Open the vendor's own page, confirm it is
  where a key or account is made, then add the URL to `VERIFIED_URLS` in
  `tests/unit/test_connect_your_services.py`. Nothing fetches URLs at test
  time; the allowlist is the record that a person checked it.
- **Cost notes say only what the vendor states** (free tier, prepaid, per
  use), or "see their pricing page". A currency sign followed by a digit fails
  `test_cost_notes_never_invent_a_price`.
- **"Recommended" is a label with a reason, and nothing is preselected.**
  Only `local` and `provider:anthropic` carry it, and
  `test_recommended_carries_a_reason` pins that exact set; adding a third is
  a product decision, not a data edit.
- **The key is never shown back and never logged.**

## Steps for a keyed AI model provider (OpenAI-compatible)

1. **Registry entry.** Add a `_mk(...)` call to `BUILTIN_EXTRA_PROVIDERS` in
   `src/agent_friday/routing/provider_descriptors.py`:
   `_mk(name, label, base_url, env_key, *, aliases=(), enabled=False, priority=50, ...)`.
   It builds an `openai-compatible` cloud descriptor whose key comes from
   `env_key` (or the credential store). `base_url` must be https for a cloud
   provider (`validate_descriptor`). Models come from discovery against
   `/models` by default, or a static `models=(...)` list. Leave
   `enabled=False` unless the provider should be on before it has a key.
2. **Sign-up link.** Add the provider's key page to `SIGNUP_URLS` in the
   same file.
3. **Guide entry.** Add `GUIDE["provider:<name>"]` in `connections_guide.py`
   with at least `signup` (the same URL) and `cost`. Optional fields:
   `signup_label`, `recommended`, `how` (replaces the three generic key
   steps), `extra` (steps added after them), `unlocks`. Without `how`, the
   card gets `_KEY_STEPS`. The card's "what it can reach" lines come from the
   category (`access_for`); its category comes from the checklist group, or
   from `ITEM_CATEGORY` if it belongs elsewhere.
4. **Verify the link** as above and add it to `VERIFIED_URLS`.
5. **Spoken names.** Add the name or names people say to `SPOKEN_NAMES` in
   `connections_guide.py`, so "connect <name>" opens its card with the key
   field focused (`spoken_connection()`, `laya_router._connect_names()`,
   `desktop_targets.resolve_settings`). Voice opens the card; it never types
   a key.
6. **Key shape (optional).** If the vendor's keys have a recognisable prefix,
   add a row to `SHAPES` in `src/agent_friday/services/secret_shapes.py`
   (`(id, label, regex, connection id)`), so a key pasted into chat is
   withheld and the secure form names the right card. The regex must read the
   same in Python and JavaScript, and must require key characters after the
   prefix, so a placeholder hint such as `xoxb-...` on a card does not match.
   `tests/unit/test_secret_shapes.py` checks both.
7. **Terminal wizard.** If the provider is offered in the terminal wizard
   (`setup_wizard.CLI_PROVIDERS`), it must also have a `provider:` guide
   entry (`test_the_terminal_offers_local_ai_first_and_preselects_nothing`).

The card then appears on its own: `setup_connections._ai_items` lists every
registry provider whose `auth.type` is `env_var`, merges in the guide fields
and the test URL (`_add_guide`), and marks a provider that ships with
`enabled` false so that saving a key turns it on. The key is saved through
`POST /api/providers/<name>/key` (`routes/platform.py`), which stores it in
`credential_store`, hot-reloads it and returns a status, never the key.

## Test connection

`services/connections_setup.test(item_id)` answers every card's **Test
connection** and never raises. It returns one of four states, shown as **Test
passed**, **Test failed** (with **How to fix**), **Not connected yet** or
**Could not tell**.

A new provider usually needs no new probe. `_test_provider(name)` reports
not connected when there is no key, fails when the stored key cannot be read,
and otherwise checks by type: Anthropic and OpenRouter through
`one_key.verify`, `openai-compatible` and `google` types by listing
`/models`, and anything else as "Could not tell" (no quick check yet).

A service that needs its own check (as Brave, Firecrawl and ElevenLabs do)
gets a `_test_<name>` function and its own branch in `test()`, placed before
the generic `provider:` branch. A probe:

- sends the smallest read the vendor offers, never a paid call;
- puts nothing from the key into its result text or a log line;
- maps an unreachable service to "Could not tell", not to a failure.

## Never log the key

`tests/unit/test_no_key_in_logs.py` fails if any four-character slice of a
key reaches a log or stdout, and `test_no_source_file_slices_a_secret` scans
the whole source tree for code that slices a secret. A new probe or save path
logs fixed words and the provider name only. `tests/unit/test_secret_shapes.py`
and the pasted-secret tests cover the chat side.

## Other kinds of connection

- **Coming soon.** Add a dict to `COMING_SOON` with exactly four keys:
  `{"id": "soon:<name>", "category", "label", "note"}`. No link, no key field,
  and the id must not also be in `GUIDE`
  (`test_coming_soon_entries_offer_nothing_to_buy`). Move it to a real card
  only when the code can use the service.
- **A creative or search service** (ElevenLabs, Brave, Firecrawl, Kie and the
  like) follows the same guide, link and probe steps; add an `ITEM_CATEGORY`
  override if its checklist group lands it in the wrong category.
- **A curated MCP server** goes in `CURATED_MCP`; its URL and docs link must
  be in `VERIFIED_URLS` too. Adding an MCP server by command is the owner's
  click only, never by voice.
- **Local AI.** **I'll just use local AI** writes
  `<Friday home>\connections_choice.json` (`connections_setup.set_local_only`)
  and changes no privacy or routing setting.

## Run the checks

```powershell
pytest tests/unit/test_connect_your_services.py tests/unit/test_secret_shapes.py tests/unit/test_no_key_in_logs.py tests/unit/test_wizard_connect_services.py
pytest tests/api/test_connect_your_services_routes.py
npx playwright test --config=playwright.app.config.ts --workers=1 connect_services.spec.ts
```

The route tests check a fresh install's cards (https sign-up, three steps,
access lines, cost, a test URL, nothing connected, coming-soon ids not
cards), that a provider which ships off is turned on by its key, and that
Test connection moves from not connected to failed to passed. CI runs all
three (the spec in the `app specs` job).
