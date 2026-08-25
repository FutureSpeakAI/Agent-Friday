# Egress envelope audit — 2026-08-24

Triggered by `tests/unit/test_egress_gate.py::test_tool_definitions_scanned`
failing at clean HEAD, reported as "tool descriptions containing PII reach the
cloud unredacted". Scope was: verify the finding, map what the gate actually
covers, close real gaps with the existing redaction mechanism, and pin the
whole envelope with tests.

## Verdict on the reported finding: not a leak

The test asserts that a **first-party** tool (`vault_read`) has its description
redacted. `_gate_tools` deliberately declines to do that, and has since
2026-08-21 — the reasoning is in its docstring.

First-party tool descriptions are static text authored in this repository
(which is public) and shipped in the binary. They are documentation, not user
data: they cannot leak the vault because they were never in it. Classifying
them meant any description containing an ordinary word like "contact",
"family", or "calendar" was blanked, and the model was handed a tool list it
could not read — on every cloud-fallback turn, for an unknown length of time.

Verified independently: no first-party tool description is built from user
data. A sweep for interpolated descriptions (`description=f"…"`) across
`src/agent_friday/` finds only orb and task labels, never tool definitions.

**So the test was stricter than the threat, and it was the test that was
wrong.** It has been rewritten to assert the contract the code actually
implements — first-party passes, MCP is gated — with both halves pinned so
neither can drift. A second stale copy of the same assertion in
`tests/test_egress_adversarial.py::TestSealOutboundTools` was corrected the
same way. Neither was a live vulnerability.

## What the audit around it did find

The worry that prompted the wider look — "the gate scans the obvious field and
misses the rest of the envelope" — was correct. Four real gaps, all confirmed
by execution rather than by reading, all now closed.

### G1 · OpenAI tool shape bypassed tool gating entirely

`anthropic_to_openai_tools` (`routing/model_router.py:939`) nests
`name`/`description` under a `function` key. `_gate_tools` read them at the top
level, so `name` came back `""`, failed the `mcp_` prefix test, and the
description travelled ungated.

This hit **every openai-compatible cloud provider** (openai, openrouter, any
openai-shaped cloud seat) via `services/model_router.py:883`. The Anthropic
path withheld the very same string. Not a policy difference — the gate was
reading the wrong envelope.

Closed by `_tool_view()`, which resolves either shape before policy runs.

### G2 · Tool `input_schema` / `parameters` were never gated on either path

An MCP server authors its input schema as well as its description, and the
schema is prose-bearing: `description` and `title` on every property, nested
arbitrarily deep. Only the top-level description was ever replaced.

Closed by `_gate_schema_prose()`, scoped deliberately to the two prose keys.
Types, `required`, property names and `enum` **values** are left untouched:
redacting an enum member does not make a tool private, it makes it uncallable.

### G3 · `tool_use` arguments in replayed history were neither gated nor scrubbed

The agent loop echoes each assistant `tool_use` back into the conversation
(`services/agent.py:6181`). Those arguments are real user data — what was
written to the vault, who a message was addressed to. `tool_use` was not one of
the block types `_gate_messages` handled, and `input` is not one of the keys
`_scrub_all` recurses into, so they passed through raw.

Blast radius is narrower than it first looks: on a same-provider turn this
re-sends what that provider itself authored. The leak is a history assembled
against one seat and replayed to another — a local seat falling back to cloud
carries its own tool calls with it.

Closed by `_gate_tool_use()` / `_gate_arg_values()`, which gate every string
value at any depth and preserve every key, `id`, `name` and `type` (Anthropic
pairs `tool_use` to `tool_result` by id; breaking that turns a redaction into a
400).

### G4 · `system` as a list of content blocks was scrubbed but not tier-gated

`seal_outbound` gated `system` only under `isinstance(…, str)`. Anthropic also
accepts a list of text blocks — the shape prompt caching requires, because
`cache_control` rides on the block.

Nothing builds it that way today, so this was latent rather than live. But the
context-assembly/caching work would have introduced it, and the failure would
have been silent and partial: the scrub still ran (it recurses `text` keys) so
identifiers were masked, while the tier gate never saw the field and prose
travelled whole. Covered now, before anything can adopt it unnoticed.

## Latency: a pre-existing problem, found by measuring the fix

Widening coverage ~7x would have cost ~19s per cloud call, because tool
definitions were being re-classified from scratch on every request despite
being byte-identical every time.

Measured with 112 MCP tools at six schema properties each, steady state
(classifier models warm — a first measurement is dominated by a ~25s one-time
lazy model load and tells you nothing about per-call cost):

| | per call |
|---|---|
| before, gating descriptions only | 2,930 ms |
| after, gating descriptions **and** schema | 700 ms |

4.2x faster while classifying 7x more text. `_TOOL_TIER_CACHE` memoises the
tier for tool-definition text **only** — user content (messages, tool
arguments, tool results) is never cached, since it is unbounded and it is the
sensitive material. The never-send floor stays outside the cache and re-runs on
every call, because a floor a stale cache can hold open is not a floor. Audit
logging still fires on cache hits; verified.

Separately worth knowing: sealing a 60-message agentic history costs ~1.3–1.8s
steady state, unchanged by this work. The eye-catching 25s figures in any
one-shot measurement are the classifier's lazy model load, not per-call cost.

## Where a real PII library would help

Recorded here and in the `egress_gate` module docstring. **Nothing in this
change presumes the outcome of the separate Presidio evaluation** — no new
dependency was added, and the fix reuses `_gate_text` / `_classify_cloud`.

The classifier is keyword- and pattern-driven, and the deterministic identifier
scrub (`core._scrub_pii`, §5.5 step 1) covers only `system` / `messages` /
`prompt`. The tool paths added here therefore get the tier gate but not the
scrub, so they **fail closed**: an argument containing one email address is
withheld whole rather than having the address masked and the rest preserved.
That is safe but blunt.

A real entity recogniser would let these paths mask spans instead of
withholding fields. That is a **capability** win, not a safety one — the safety
property does not depend on it. If one is adopted, the seams are `_gate_text`
(span decisions) and `_scrub_all` (identifier masking), not new call sites.

## Still open (deliberately)

- **Image and document blocks are not content-gated.** A text classifier cannot
  judge them; `record_binary_egress` accounts for them instead. Pre-existing and
  documented.
- **`tests/test_egress_adversarial.py::test_tier2_keyword_batch`** fails at HEAD
  for two phrases ("contact information on file", "family gathering this
  weekend") which classify Tier 1 where the test expects Tier 2. Confirmed
  pre-existing and unrelated — reproduced with this change stashed. Classifier
  tuning, left alone.
- **Schema `enum` values** are sent verbatim by design (see G2).
