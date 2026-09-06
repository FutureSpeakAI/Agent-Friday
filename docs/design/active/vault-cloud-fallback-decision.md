# `vault_cloud_fallback`: measured, and deliberately left alone

**Date:** 2026-08-29
**Status:** a product decision for Stephen. **No default was changed.**
**Answers:** `Q-V4` in `docs/design/vault-first-onboarding.md`.

---

## The question

The vault-first onboarding spec asked whether `vault_cloud_fallback` should
default to `warn` instead of `redact` on installs with no local model, because
`redact` is the setting that transmits.

Stephen's instruction on this was explicit: *"Whatever the screen says has to be
what the code does. If you change a default to make the copy true, that is a
product change — flag it, do not slip it in."*

So the order of work was: measure first, then write the copy to the measurement,
and only then ask whether the default should move.

## What was measured

Re-run at this commit rather than trusted from the spec, with
`ModelRouter._local_candidates()` forced empty to simulate a machine with no
local seat. That detail is load-bearing: the first attempt returned
`provider=local` from a dead Ollama port, because the router asks Friday's own
seat store before the daemon and this machine has seats loaded. **A privacy
default cannot be verified on the machine that would never hit it.**

Routing a TIER_3 vault question, factory settings otherwise:

| `vault_cloud_fallback` | provider | refuse |
|---|---|---|
| `redact` *(shipped default)* | cloud | **False** |
| `warn` | cloud | True |
| `deny` | cloud | True |

And what the egress gate does to the same question on its way out, via
`seal_outbound` against Anthropic:

| Input | Tier | What Anthropic receives |
|---|---|---|
| `Remind me what my Chase account balance was last month.` | 3 | **withheld** |
| `Call Dave at 555-0142 about the Hartley contract.` | 2 | **withheld** |
| `Emma's school pickup is at 3:15 and her teacher is Ms. Alvarez.` | 1 | the sentence, verbatim |
| `She started sertraline 50mg last month.` | 1 | the sentence, verbatim |

## The finding that decided it

Under the shipped default, the router does **not** refuse — but the gate
withholds the message anyway. So the user-visible behaviour on a machine with no
local model already **is** "she declines rather than answers", which is exactly
what screen 3b says. The copy is true as shipped.

That is why nothing changed. Changing a default to make copy true, when the copy
is already true, is a product change with no user-visible benefit — and this
release already carries a relocation, a delete path and a rewritten onboarding.

## The argument for changing it anyway, which is Stephen's to weigh

`redact` and `warn` produce the same outcome for the user but not the same
safety margin:

* Under `warn`, the turn is refused **at the router**, before any payload is
  assembled. Two independent things would have to fail for vault content to
  leave.
* Under `redact`, the turn is assembled and sent, and the **egress gate is the
  only thing** standing between the user's finances and the network. It is a
  pattern matcher with three documented recall gaps
  (`docs/audits/privacy-classifier-known-gaps-2026-08-25.md`), two of which are
  demonstrated in the table above.

The gate held on every vault-shaped sentence tested. The question is whether the
product should depend on it holding, when a one-word default gives a second
barrier for free.

**My recommendation is `warn`, and I did not make the change.** It is a
one-line edit to `core/__init__.py:1684` plus one line of screen 3b copy
("she will decline" becomes true *always* rather than *because the gate caught
it"), and it wants to be a decision rather than a side effect of a build.

## What did change, and why it was not optional

The same audit found a claim in the drafted copy that was not true, and that one
was a defect rather than a preference:

**Tier B of the knowledge graph produced nothing on a cloud-only machine, and
said nothing about it.** `indexing_mode` defaults to `local_only`, which pins
every extraction call to a local model; with no local model every call raises,
is caught per-chunk, and the chunk is skipped. Tier A still runs, so the index
completed and **reported success**. A user was told the map was built.

`indexer.py` now counts the failures, names the first one, sets `degraded` in
the result, and says so on the progress channel. Screen 3b gained a paragraph
saying the map's second layer needs a model on this computer — because a screen
listing "the map" under *what stays the same* would otherwise be technically
true about location and misleading about substance.
