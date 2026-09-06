# Sensitivity classifier — known gaps after the 2026-08-25 contact-PII fix

Status: **open, deliberately not fixed.** Recorded so they are decided on rather
than rediscovered in three months as a surprise.

Context: on 2026-08-25 vault contact details were found reaching Anthropic
unredacted. Two files in Stephen's real `~/.friday/wiki` classified TIER_1 and
`vault_access.gate_content` returned them verbatim. Fixed in `sensitivity_classifier.py`
Layer 1a (`66fb53e`) and in the `model_router` context fallback. What follows is
what that fix does **not** cover.

## The shape of what remains

The fix added deterministic detectors for *structured* shapes — phone numbers,
street addresses, masked account tails, issued identifiers. Those work because
the shape itself is the signal.

Everything below needs to recognise that a **word is a name** — a person, a drug,
a clinic. That is named-entity recognition, and we do not have it.

| Gap | Example | Current tier | Should be |
|---|---|---|---|
| Bare drug names | `she started sertraline 50mg last month` | TIER_1 | TIER_3 |
| `Dr. Lastname` | `follow-up with Dr. Alvarez about the scan` | TIER_1 | TIER_3 |
| Bare given-name possessives | `Emma's school pickup is at 3:15` | TIER_1 | TIER_2 |

These are only reachable where a fail-closed default does not already cover
them. Since the `model_router` fix, the wiki and smart-context sections default
to TIER_2, so wiki-sourced instances of these are withheld regardless of the
classifier. The residual exposure is content classified ad hoc elsewhere —
principally tool results passing through `egress_gate`.

## Why each was left

**Presidio is not the answer, and this is measured, not assumed.** Evaluated
2026-08-24: it scored TIER_2 where existing regex already returns TIER_3, and
escalated **6 of 12 entirely benign prompts** — "What is the weather going to be
like tomorrow?" among them — because `DATE_TIME` and `LOCATION` fire on ordinary
prose. It also auto-downloads a 590 MB model. It now runs in shadow mode only
(`presidio_shadow.observe()`), enforced only under `FRIDAY_PRESIDIO_ENFORCE=1`.

**Drug names** would need a pharmacological vocabulary. A dosage-shape regex
(`\d+\s?mg`) was considered and rejected: it fires on health journalism, and CDC
guidance being withheld as if it were a medical record is one of the four
over-redaction incidents this file's history already carries.

**`Dr. Lastname`** was the closest call. Adding `dr\.` to the weak TIER-3 words
is two characters of work — and it re-breaks "Dr. Seuss", which is exactly the
storybook-prompt shape that `b69acb2` was written to fix. A weak hit routes
local, the storybook prompt then does not fit the local context window, and the
turn dies. Not worth it for the recall gained.

**Given-name possessives** cannot be done without knowing which capitalised
words are people. Note the classifier lowercases before keyword matching, so
even the capitalisation signal is gone by then.

## Also still over-triggering (pre-existing, untouched)

Verified present both before and after the fix, so the fix neither caused nor
worsened them:

- `the financial district skyline at dusk` → TIER_2 (egress) / TIER_3 (routing)
- `Supreme Court ruling coverage in the news` → TIER_2 / TIER_3
- `loaded from the Sovereign Vault` → TIER_3 in **routing** mode

The third is intentional: product vocabulary is excluded from egress matching
(`_EGRESS_EXCLUDED`) but deliberately retained for routing, so "what's in my
vault?" still force-routes local.

## A process note worth more than any single gap

The xfail pinning the JSON-descent variant cited, as grounds to defer, that a
concurrent session "added a `vault=` mode for this same family of bug."

**No `vault=` parameter has ever existed on `classify()`** — not in any branch,
worktree, stash, or reflog. The claim originated in a session's memory, was
written into a test annotation as established fact, and was then read by a later
session as evidence that the fix was already handled elsewhere. It delayed the
real fix.

Cross-session claims about work existing elsewhere are **leads to verify, not
facts to build on** — particularly when the claim is the reason not to act.
Verifying this one cost about a minute of `git log --all -S`.

## If these are ever chased

The tractable route is a local NER pass at Layer 4 (the existing Ollama hook),
not Presidio. It runs on-device, so it does not violate the "never classify in
the cloud" rule, and unlike Presidio it can be given the specific instruction to
ignore dates and locations. Cost is latency inside a voice turn, which is why it
is currently gated to the ambiguous band only.
