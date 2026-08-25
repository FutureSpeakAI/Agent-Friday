# Two calls for Stephen — 2026-08-24

Both surfaced during the egress envelope audit. Neither is urgent; neither is
mine to decide. Plain language, no code required.

---

## Decision 1 — "family gathering this weekend" is Tier 1. A test says it should be Tier 2.

### What's happening

The sensitivity classifier sorts text into three tiers. Tier 1 goes to the
cloud freely. Tier 2 gets replaced with a placeholder. Tier 3 is dropped
entirely.

Two phrases sit on a disputed line:

| phrase | classifier says | test expects |
|---|---|---|
| "family gathering this weekend" | Tier 1 (send it) | Tier 2 (withhold) |
| "contact information on file" | Tier 1 (send it) | Tier 2 (withhold) |

These tests have been failing for a while. They are not new, and nothing I did
caused them.

### The tradeoff

**If you side with the test** (make these Tier 2): the word "family" and the
word "contact" start pulling ordinary sentences into redaction. Friday says "I
can't discuss that with a cloud model" when you ask about a barbecue. This is
the same over-classification that has already bitten this project three times —
it's the reason first-party tool descriptions stopped being classified, the
reason news headlines needed a provenance exemption, and it's the root of the
voice/news bug in the report accompanying this memo.

**If you side with the classifier** (delete or amend the tests): "family
gathering this weekend" travels to Anthropic. On its own it says nothing about
you — it has no name, no date, no address. But it is genuinely a fact about
your life, and the tests were written by someone who thought that mattered.

### What I'd recommend

Side with the classifier and rewrite the tests. The phrases carry no
identifier, and the cost of the alternative is a Friday that redacts ordinary
conversation — which historically doesn't make you safer, it just makes her
useless and pushes you toward turning gating off. The protection that actually
matters (names, SSNs, account numbers, medical and legal specifics) is
unaffected either way.

**But this is a values call about your own privacy, not a technical one, which
is why I'm asking rather than choosing.** If you'd rather Friday err toward
silence on anything touching family, say so and I'll move the keywords instead.

---

## Decision 2 — Tool schema `enum` values go to the cloud verbatim

### What's happening

When Friday offers a tool to a cloud model, the tool comes with a schema
describing its arguments. I now redact the prose in that schema (the
human-readable descriptions) if it classifies as private.

I deliberately did **not** redact `enum` values — the fixed list of allowed
choices for an argument, like `["fast", "slow"]` or `["inbox", "archive",
"spam"]`.

### The tradeoff

**Why I left them alone:** an enum value is not a label, it's the literal
string the model must send back for the tool to work. Redact `"archive"` and
the model can no longer archive anything — it doesn't know the word. A withheld
description costs the model some understanding; a withheld enum makes the tool
uncallable. Redaction that breaks the feature isn't privacy, it's an outage.

**The risk I'm accepting:** a third-party MCP server could put something
revealing in an enum — a list of your actual mailbox folder names, project
codenames, or account labels. That would go to the cloud verbatim. I have not
seen a connector that does this, but I can't rule it out for connectors added
later.

### What I'd recommend

Leave it as is, and revisit only if you add a connector whose enums carry your
own vocabulary. If you want belt-and-braces now, the middle option is to log a
warning when an MCP enum value classifies above public — you'd get told it
happened without anything breaking. That's maybe an hour of work and it turns
an unknown into a monitored known.

**Say the word if you want the warning; otherwise I'll leave this alone.**

---

## Neither of these blocks anything

Cloud seats are safe to use today. Decision 1 is about whether Friday is
currently slightly too permissive or about right. Decision 2 is about a
hypothetical connector you don't appear to have.
