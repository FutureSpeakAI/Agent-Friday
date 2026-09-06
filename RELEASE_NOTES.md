# Agent Friday v5.12.0

*2026-09-05 · FutureSpeak.AI*

This release exists because we spent two days looking for places where what
we told you and what the code actually does had come apart, and then fixed
82 of the 112 things we found. This document is written the way we'd want
one written about us: it says what was wrong before it says what's new,
including the parts that don't make us look good.

Full technical detail lives in [CHANGELOG.md](CHANGELOG.md); the complete,
unedited ledger — every finding, every verdict, every fix, including the ones
still open — is in
[`docs/audits/gauntlet-2026-09-03/`](docs/audits/gauntlet-2026-09-03/).

---

## The three findings we'd rather not have had to report

**A credential-sandboxing gap took three attempts to actually close.** Every
sandboxed connector Friday spawns inherited her entire decrypted-secrets
environment — API keys, the vault passphrase, all of it. The first fix was a
list of secret names to block. That list was wrong the same night (it named
two environment variables that don't exist in this codebase, while leaving
the real one exposed). A live test then found six more currently-used
provider credentials the list had simply never been told about. We stopped
trying to enumerate what to hide and inverted the mechanism: a connector now
gets an explicit list of what it's *allowed* to see, built from what it
actually needs, and nothing else. A name nobody thought to add can no longer
leak by omission.

**A child-safety check had been silently bypassable for over two months.**
Content already tagged by an upstream classifier as CSAM, a real-person
deepfake, doxxing, or violence incitement would pass our content-policy gate
with `blocked: False`, as long as it arrived with no title or description
text attached — one of two independent checks skipped itself in exactly that
condition, on the assumption the other one already had it covered. It didn't.
This existed from the module's first commit in June and every test stayed
green the entire time, because nothing had ever exercised that specific
combination. We found it by reading the docstring against the code, not by
running the suite one more time. Fixed, and we're not proud it took reading
the fine print to catch.

**"Local-only" did not mean local-only for ordinary conversation.** The
single most common thing people do with Friday — chat, with tool use, mode
set to "never leaves this machine" — could still route to the cloud under
specific conditions, in the tool-use path, the voice pipeline, and the
knowledge-graph indexer, independently, three separate times. Fixed
everywhere it was found, and the product's own behavior changed as a result:
when Friday genuinely can't honor a local-only promise now, she says so and
offers cloud as an explicit choice, rather than quietly switching for you.

We also found, disclosed, and fixed two incidents the audit's *own* tooling
caused while looking for exactly this class of bug: one test made a real,
live API call to Google using a real key from an improperly-isolated test
process, and a separate batch of tests made real network requests — to this
application's own local port and to a real third-party website — because a
helper function reaches the network by default. Both are closed. We're
naming them because a security review that never implicates itself isn't one
you should fully trust.

## What's actually new

- **A real choice on cloud privacy.** Settings now has an explicit
  "unrestricted cloud" toggle — off by default — for when you'd rather trade
  every privacy safeguard for full cloud capability, deliberately, rather
  than have Friday negotiate it for you.
- **6 new local creative models** — 3 image, 3 video — added and
  hardware-verified with measured generation times and their actual
  licenses shown in the picker.
- **The local brain is Gemma 4 now, not Qwen.** Qwen is removed from the
  local model ladder entirely, replaced by the Gemma 4 family (e2b through
  26b) as a placeholder until FutureSpeak's own model ships. If you have an
  older Qwen model installed via Ollama, Friday will not offer to use it
  again — pull a Gemma 4 rung instead (`friday models --install` picks the
  right one for your hardware automatically).
- **The hourly heartbeat got cheaper**, twice over: a job that was spending
  roughly $12 on a single unwatched run was removed entirely, and the
  heartbeat itself now uses prompt caching on the path that actually serves
  it (previously wired to a path it never used) and a tool registry sized to
  what a liveness check actually needs instead of the full 75-tool set.
- **A stale vault key no longer strands your provider keys.** If a
  rehearsal or test run overwrites the shared OS keychain entry mid-session,
  a new maintenance path re-encrypts whatever the current process can still
  decrypt under a fresh key, so it survives the next restart too.

## What's still broken, and said so plainly

- One test (`test_nemo_voice.py`) fails intermittently under the full suite
  and passes every narrower subset we've tried. Left open rather than
  force-closed with a guess.
- A skill-optimization "success" scorer can't yet tell a genuinely completed
  action from a confidently fabricated claim of one. The honest minimum fix
  shipped (it no longer defaults to "success" when it has no evidence); the
  real fix — actually verifying completion — is specified as follow-up work,
  not built yet.
- A vault-classification fix (correcting false "access denied" errors on
  ordinary tool arguments containing words like "contact" or "family") is
  real and needed, but its own test suite currently has 2 failing tests. Not
  shipped in this release; tracked for the next one.
- A handful of findings are recorded as open product-design questions, not
  bugs with an obvious answer: what "success" should mean for autonomous
  skill learning, what a knowledge-graph eviction policy should look like,
  what a scheduler manual-run's concurrency semantics should be. See the
  ledger for the full list — we'd rather hand you a question than a guess.

## One thing this release does not fix

The repository's git history still contains a real vault passphrase from
earlier in this project's life. That is a publication decision, not a code
fix, and it is explicitly not acted on here — no history rewrite, no force
push — until the passphrase itself has been rotated. Rewriting history around
a still-live secret protects nothing and breaks every existing clone.
