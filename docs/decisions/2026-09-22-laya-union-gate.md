# The Laya union gate — off by default, shadow first

**Status:** built, wired, defaulting to OFF. Awaiting Stephen's go to enable.
**Written:** 2026-09-22
**Supersedes nothing.** Extends `docs/design/active/laya-across-the-harness.md`,
which ranked the harness's judgments and recommended the corpus work first.

---

## What this is

`dissent_gate.classify_severity` decides whether an action needs Stephen's
sign-off. Since 2026-09-20 that includes sending mail as him. It is a scan for
~40 substrings, and its own docstring records the failure it could not avoid:
`spend` and `order ` are hard markers and also ordinary nouns, so "Analyze our
spend trends" gated, and the fix was a hand-written regex for leading drafting
verbs. A keyword classifier accumulating patches.

This adds a second opinion — Laya, a 421M typed-decision encoder, on CPU — and
combines the two by **OR**: gate if *either* votes to gate.

---

## The measurement, rerun today rather than inherited

A previous session reported these numbers and then died. Every one was
re-derived from scratch on 2026-09-22 by rerunning `tools/severity_eval.py`
against a freshly written set. They reproduce exactly.

30 cases, 27 firm and 3 marked `arguable` and excluded from scoring:

| scorer | correct | **missed `hard`** | false `hard` |
|---|---|---|---|
| rules (incumbent) | 17/27 (63%) | **5** | 5 |
| laya | 23/27 (85%) | **1** | 3 |
| **union (either)** | 22/27 (81%) | **0** | 5 |

**The finding is the disjointness, not the percentages.** The five outward
actions the keyword scan missed, Laya caught. The one Laya missed ("Reply to
Jere with the revised quote"), the keyword scan caught. No firm `hard` case was
missed by both — the harness now prints that count on every run rather than
leaving it as a claim in a comment.

The union scores *lower* than Laya alone, and is still the right mode, because
the two error classes are not equal. A missed `hard` sends mail with no human in
the loop. A false `hard` costs one approval card. Taking either vote drives the
expensive error to zero and pays for it in the cheap one.

**Cost, measured on this machine:** ~400 ms per decision on CPU (mean of 30, max
604 ms), and 45.6 s of one-time background load at startup. Earlier notes said
~300 ms and ~36 s; the honest figures are the ones above. CPU, not GPU — bonsai2
owns ~10 GB of the 12 GB card, and taking VRAM from the model doing the actual
work for a 0.4B encoder would be a bad trade.

### The caveat that must travel with the 63%

**The set is adversarial by construction and 63% is not the keyword scan's
real-world accuracy.** It deliberately includes five outward actions phrased
with no marker word at all, which a substring scan cannot catch by design, plus
every documented false-positive. On Stephen's actual traffic — mostly
unambiguous — the incumbent does considerably better. The set locates the
boundary; it does not measure the field.

**The labels are mine, not Stephen's.** They encode the rule the gate already
claims to implement. Three genuinely arguable cases are marked and scored
separately rather than being resolved by fiat.

---

## Why OR, and not "replace the scanner"

The argument for shipping this is **structural, not statistical**.

`keyword`'s verdict is one of the two inputs to the OR. So there is no input on
which enabling Laya *removes* an approval card. The worst case of a bad
fine-tune, a corrupted download, a model that answers badly, or a model that
does not load at all is **more approval cards, never a silent send**.

That is the same shape as `judgment_gate`, which may rescue a blocked span and
may never authorise one.

It also means the decision to enable does not depend on trusting the 85%. If
Laya turns out to be worthless on real traffic, the cost is noise, and the
remedy is a settings change.

---

## The safety property is tested, and the test can fail

`tests/unit/test_laya_union_gate.py` asserts:

> `gated(keyword)` implies `gated(union)`, for every case in the set, under five
> different adversarial fakes — a model that says `soft` to everything (the
> corrupt-checkpoint case), one that inverts the incumbent, one that returns a
> value outside the label set, one that raises, and one that never loaded.

It drives `approvals.classify(...)["gated"]` — **the seam, not the primitive.**
What Stephen experiences is a card, and a card is the end of a chain running
`union_backend → decide() → the policy table`, any link of which could drop an
escalation while `union_backend` itself stayed correct.

**Verified by mutation.** Adding a de-escalating branch to the policy_class arm
(`if severity == "soft": return "internal"`) fails the property under two of the
five adversaries, losing 14 cards under `inverts_the_incumbent` — including "Pay
the AWS invoice with the card on file" and the literal `gmail_send` card shape.

Two further tests pin that the property is not vacuous: a model that always says
`hard` must gate *everything* (so the seam is live), and the incumbent must
leave several cases ungated (so the baseline is not already "gate everything").

### The mutation the tests could NOT see, which is the more useful half

The first probe — make the `action_severity` arm return Laya's answer directly —
**passed**. Not because the tests are weak, but because `approvals.classify`
asks `policy_class`. `decide("action_severity")` has **no production caller at
all** today; only tests call it. The arm that reads like the heart of the union
is, at present, dead weight at the gate.

It is covered anyway, since the question is registered and a future call site
would reach it — but labelled, so nobody reads a green suite as evidence that
arm is load-bearing.

---

## Shadow mode, and where it lives

    FRIDAY_DECISION_SHADOW=laya          # or settings: decision_shadow

With that set, **`keyword` still decides** — the verdict is byte-identical — and
Laya scores the same state on a background thread, with both answers written to
`~/.friday/decisions.jsonl` carrying `shadow: true`, `decided_by`, and `agreed`.
Disagreements become data on real traffic instead of an argument.

Shadow scoring was moved **out of `laya_backend` and into `decisions.py`**. It
had been written in the backend, reaching across the module boundary for
`_record`, `_clip`, `_scrub` and `_state_digest` — four private names, which is
how a seam stops being one. Running a candidate alongside the incumbent is the
seam's job for *any* backend, not a favour this one does for itself.

Three properties, all tested: it never blocks a verdict (the verdict has already
returned), it never raises into the gate, and it never queues — a backend still
warming simply writes no row, because a burst of decisions landing at once after
warm-up would be scored against a moment that has passed.

---

## What is on, and what is off

| | state |
|---|---|
| `decisions.DEFAULT_BACKEND` | `keyword` — **unchanged** |
| `laya` / `laya-union` backends | **registered** at server start, selectable, not selected |
| Model load at startup | **only** if `decision_backend` or `decision_shadow` names a Laya backend |
| Shadow mode | **off** unless `FRIDAY_DECISION_SHADOW` / `decision_shadow` is set |

Registering is cheap and unconditional: `laya_backend` imports `laya` — and
therefore torch and transformers — lazily inside `_load_now`, so importing it
pulls no ML stack. A machine without torch logs one line and behaves exactly as
it does today.

### Turning it on

1. **Shadow first, for a week.** `decision_shadow: "laya"`. Costs 45 s of load
   and ~800 MB resident; changes no verdict.
2. **Read the log.** `decisions.jsonl` rows where `shadow` is true and `agreed`
   is false are the entire argument, on real traffic rather than on my 30 cases.
3. **Then, if the disagreements look right,** `decision_backend: "laya-union"`.

---

## What this does not do

- It does not fine-tune anything. The checkpoint is the published one, which its
  own model card describes as near-chance zero-shot. It works here because the
  severity question is a good fit for the phrasing, not because it was taught.
- It does not fix `sensitivity_classifier`, which the design doc ranks higher
  and which needs no new dependency — that finding stands and is untouched.
- It does not make Laya a new-install dependency. Still no.
- `~/.friday/decisions.jsonl` **is still empty** — the file does not exist. Until
  shadow mode runs on real traffic, there is no corpus, and every number in this
  document comes from a 30-case set I wrote by hand.

---

## Provenance

Recovered from untracked working-tree files in the main checkout after the
authoring session crashed (`laya_backend.py` sha1 `e8efbb7`,
`tools/severity_eval.py`), committed unmodified as `48d29be` before anything was
changed. Numbers rerun, not inherited. Nothing pushed.
