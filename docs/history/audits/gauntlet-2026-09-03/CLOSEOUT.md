# Gauntlet Audit — Close-Out (2026-09-03 through 2026-09-05)

> **Historical record — 2026-09-03 through 2026-09-05.** Kept as an engineering record of the state of the tree on that date. Claims here describe that date, not the current code; the current status of any subsystem is in the documents linked from [docs/README.md](../../../README.md) (one level deeper for the gauntlet subdirectory: `../../../README.md`).

Written for someone who wasn't here. If that's you in a month, this is the
page to start on — the other three (`findings.jsonl`, `coverage.md`,
`progress.md`) are the working ledger, the seam map, and the round-by-round
diary, in that order of decreasing polish and increasing detail.

Branch: `gauntlet-audit-2026-09-03`, off `integration/release-2026-09-03` at
`f000f07`. Never pushed. An autonomous, standing-authority audit of
`agent_friday` — security and correctness, self-directed within a seam map,
judgment calls logged inline as "JUDGMENT CALL" rather than asked about
individually.

## The one-paragraph version

Two days, ~112 findings, 82 shipped with a real red-green-red-on-revert
proof cycle, ~7 accepted as documented non-issues, ~8 confirmed as holding
observations rather than defects, and a handful still genuinely open as
design questions only the maintainer can settle. Three separate real credential/
data-boundary breaks were found and closed (a repeat pattern, not one
lucky catch). One incident happened during the audit's own work — a real,
live, low-cost Gemini API call using the maintainer's real key, caught and
disclosed rather than left in a transcript. One test still fails
intermittently and is honestly left open rather than force-closed. 1 of 13
seams met the audit's own closure bar; the other 12 are in various states
of "swept N times, still finding things the (N+1)th time."

## How this audit worked

A "seam" is a place data or a decision crosses a boundary (settings write
→ settings read, prompt assembly → provider payload, router request → seat
actually served, and so on — 13 named in `coverage.md`). A seam closes only
when two consecutive fresh-context sweeps find nothing new AND every claim
on it has a verdict backed by an artifact — either a HOLDS observation
(an `H*` entry: the call graph that established it) or a BROKEN finding
with a red probe under `tests/gauntlet/` (green with real revert evidence
if fixed, red with a documented reason if queued). Green-everywhere was
explicitly rejected as the bar; judged-everywhere was the bar.

A parallel "claim corpus" (`claims.jsonl`, 243 entries) extracted specific,
checkable factual assertions from docs and UI copy — `THREAT_MODEL.md`,
`KNOWN_ISSUES.md`, `README.md`, both `index.html` and `ui_parts/app.html`'s
disclosure strings, and every `src/agent_friday/services/*.py` and
`routes/*.py` module docstring — and checked each one against current code
rather than trusting what it says about itself.

## What was examined

- **All 13 seams got at least one sweep**; most got several (`coverage.md`
  has the per-seam round history). The seam that most consistently kept
  finding things across sweeps was cost/budget accounting (5 rounds,
  culminating in Q26's root-cause synthesis) and settings→UI copy
  agreement (6 sweeps).
- **The full claim corpus** — every doc file listed above, all ~153
  `services/*.py` modules' docstrings, all 61 `routes/*.py` modules at the
  docstring level, and both HTML files' full disclosure-string surface.
- **A real, live dynamic boot** of `src/agent_friday/server.py` (Round 14,
  2026-09-04) — not just static reading — in an isolated `.friday` home,
  which is what surfaced F67/F68/F69/F70 and, incidentally, the Gemini
  incident this document discloses below.
- **The documented test invocation**, eventually. A second independent
  cold-verification pass (external to this run) caught that every prior
  "suite green" claim in this ledger had been measured with `pytest
  tests/unit/ tests/api/` and `tests/gauntlet/` as separate invocations,
  never the bare `pytest` that `pytest.ini` and `tests/README.md` actually
  document. That gap is closed — see "Honest limits" below for what it
  cost to close.
- **This audit's own prior work**, twice: once when a first cold
  verification found a wrong fix (F37) and several weak probes; once when
  a second found the suite-invocation gap above. Both rounds of external
  correction were accepted and acted on, not argued with.
- **An externally-commissioned review** (the maintainer's own initiative, not
  this audit's sweep) of the SkillOpt subsystem (seam 13, opened
  2026-09-04) — its 4 claims were independently re-verified against
  current code before anything was fixed or trusted, and one (F75) turned
  into the design question that produced this run's most consequential
  ruling.

## What was NOT examined (honest limits)

- **1 of 13 seams formally closed** ("MCP and connector registration,"
  2 consecutive clean sweeps). The other 12 are open at various sweep
  counts — a seam being "swept N times" is not the same claim as "closed,"
  and none of the open 12 should be read as low-risk just because they've
  had several clean-ish passes. Per-seam detail and exact sweep counts are
  in `coverage.md`'s table, not restated here since that table is the
  more current copy of this fact.
- **Sweep counts in `coverage.md` are current as of this file's own last
  sync (afternoon, 2026-09-04) for the "needs an Nth sweep" *number*, but
  the finding-disposition tags layered on top (`[SYNC ...]`) are current
  through this close-out.** In plain terms: trust the `[SYNC]` annotations
  for "is this specific finding fixed," don't trust the sweep-count prose
  around it for "how fresh is this area" without checking the date.
- **The claim corpus stopped growing at 90 entries for most of one night**
  before the three specifically-named gaps were closed one by one
  (`routes/*.py` docstrings, `THREAT_MODEL.md` in full, `ui_parts/
  app.html`'s full disclosure surface, 6 more service modules). It is now
  at 243 and every module has been walked *once*. A second, independently-
  fresh confirming pass over the full 243-entry corpus has not happened.
- **`tests/unit/test_nemo_voice.py`'s `test_nemo_models_ready_false_when_
  uncached` still fails intermittently, and is still not isolated to a
  specific pair of interacting files.** It reproduces only under the full,
  documented bare `pytest` invocation — every narrower combination tried
  (unit alone, unit+api, unit+gauntlet, unit+api+gauntlet) stayed green.
  Left open deliberately rather than force-closed with a guess; the maintainer
  has separately confirmed this disposition is the right one.
- **13 entries had `status: fixed` while their `verdict` field still read
  "queued"** — stale prose left over from each finding's original triage,
  never updated after the fix landed. All 13 checked individually against
  their own `fix_note` (all genuinely fixed with real evidence) and
  corrected in place 2026-09-05; original text kept verbatim underneath
  the correction, nothing deleted. List: F9, F10, F11, F18, F21, F22, Q11,
  F24, F30, Q8, Q7, Q19, F37.
- **Q26 carries no red-green-red-on-revert narrative of its own** — by
  construction, not oversight: it's a root-cause synthesis that shipped no
  code under its own ID. Its actionable items are the fixes recorded under
  Q6, Q7 and Q11's own entries, all three confirmed 2026-09-05 to carry
  real revert evidence directly. A cross-reference note now lives on Q26
  itself so this isn't a hidden fact.
- **Five early fixes (F1, F2, F8, F11, F12) shipped without a documented
  revert step**, before the discipline was tightened partway through the
  first night. Retroactively verified since (and one, F11, turned up a
  real bug in its own probe when it was). A second cold-verification pass
  later found six *more* with the same gap (F3, F6, F10, F13, F18, Q16) —
  also since backfilled, one (F13) via a revert that briefly, genuinely
  overwrote the real `index.html` before being caught and restored.
- **Cost-metering dollar figures for Firecrawl, Brave, Veo, Lyria and
  Gemini TTS are unverified estimates**, explicitly labeled low/moderate-
  confidence in their own fix notes because a verified real-world rate was
  never available to this audit. The wiring that records them is correct;
  the *numbers* are not independently confirmed against each provider's
  actual billing. Nothing in the product UI should present them as
  authoritative pending that verification — this was checked, not just
  asserted (see F50's self-finding, below).
- **One self-caused incident during the audit's own work**, disclosed in
  full below and in the ledger as F77.

## Findings by severity

The ledger has no structured severity field — verdicts are prose. The
grouping below is my own judgment, made for this document, of what
actually matters if you're deciding how much to trust the app right now.
Full detail for every one of these lives in `findings.jsonl`.

### Critical — real credential, data-boundary, or safety-floor breaks (all fixed)

- **F32 / F44 / F67 — the same defect class, found and re-found three
  times.** Every stdio MCP connector inherited Friday's *entire* decrypted-
  secrets environment (F32); the fix left a live gap the same night (F44);
  a real, live dynamic boot then proved 6 more currently-registered
  provider credentials were still missing from the blocklist (F67). Fixed
  the third time by inverting the mechanism — an explicit allowlist of
  what a sandboxed connector *may* see, rather than a hand-maintained
  denylist of what it may not — which is the only version of this fix that
  can't be reopened by a 4th provider being added later. This is the
  pattern the maintainer's own closing message called out by name.
- **F54 — the content-policy harm floor (H1-H4) was silently bypassed**
  for category-only content matches. Fixed.
- **F37 — a TIER_3, encryption-flagged knowledge-graph page's real content
  could reach any provider, including cloud**, via an always-on "related
  pages" context path that never checked tier gating at all. Fixed twice —
  the first fix compared section names case-sensitively against an
  always-lowercased exclusion set and didn't actually work; a cold
  verification pass caught it, and it was redone correctly.
- **F34 / F10 / Q19 — Friday's own "local only" promise was false** for
  the single most common interaction (ordinary interactive chat with tool
  use), in three related but distinct ways across three findings. Fixed
  via Q19's re-triage, per the maintainer's direct ruling on what the product
  should do when it can't honor the promise (fail with an error and offer
  cloud-only mode, rather than silently switching).
- **F68 — a provider key that exists on disk but cannot be decrypted
  reports "connected," identical to a working key** — reproduced the maintainer's
  own real situation (3 undecryptable keys) exactly, with zero indication
  in the boot log that anything had failed. Fixed: `provider_key_status()`
  now attempts a real decrypt and reports three distinct states.
- **F76 — this audit's own test suite made real, live network requests**
  to whatever was listening on this application's own default port
  (very likely the maintainer's real, running Friday instance) and to a real
  third-party site, because a Google-OAuth-gate test suite passed those
  URLs to a function that reaches the network by default and was never
  mocked. Self-found while re-verifying an unrelated fix; fixed by mocking
  the one collaborator these tests were never actually about.
- **F77 — this audit's own dynamic-boot harness leaked the maintainer's real
  `GEMINI_API_KEY`** into an isolated test server, which made one real,
  live Gemini API call before being caught. Full account in its own
  section below — this is the incident the maintainer's wind-down message asked
  to have recorded plainly.

### High — real defects with real, if narrower, impact (fixed unless noted)

F30 (fallback ladder reused a system prompt gated for the wrong provider,
systemic across 4 call sites); F40 (a Federation trust-control panel was
completely fake — no backend enforcement at all); F41 (builtin scheduled
task errors were silently discarded under the packaged runtime); F65 (a
real, live production cost leak — the incident that interrupted this audit
overnight and was fixed within the hour); F42/F43 (`THREAT_MODEL.md`
claims that no longer matched code). **F75 — open by design, not by
omission**: the mechanism that scores whether a chat turn "succeeded" was
a reply-shape heuristic that could score a fabricated success claim
identically to a real one, feeding Friday's own skill-learning loop a
false signal. Escalated to the maintainer rather than patched unilaterally;
ruling below.

### Medium / Low

The remaining ~70 fixed findings are copy/UI mismatches (index.html vs.
app.html divergence, onboarding claims that overstated what the app does),
scheduler and concurrency gaps (Q24), dead code removed rather than fixed
(F58, F59, prompt_manager.py), and cost-metering completeness gaps closed
with honestly-labeled estimated rates (Q6, Q7, Q11). None of these
independently change whether the app is safe to run; several of them
change whether its own UI can be trusted to describe itself accurately,
which is why they're in the ledger rather than skipped as cosmetic.

### Accepted as non-issues, or refuted

~7 findings were reviewed and deliberately left as documented, accepted
behavior rather than fixed (Q12, Q13, Q15, F7, F48 — the last one is in
hand in a separate session and wasn't re-escalated here). At least one
suspected defect was checked and found not to be real at all (Q6(b) — an
unrelated internal virtual-economy system, not a real-dollar gap) —
recorded as refuted rather than quietly dropped.

## The disclosed incident — F77 (Gemini API call)

**What happened.** During Round 14's dynamic-boot investigation
(2026-09-04, ~18:14 local time), an isolated copy of `server.py` was
launched to observe real MCP-connector and credential-decryption behavior
that static reading alone couldn't settle. The launch script redirected
`USERPROFILE`/`HOME` to a throwaway `.friday` home but never cleared the
real `GEMINI_API_KEY` (or `GOOGLE_API_KEY`, `ANTHROPIC_BASE_URL`) from the
environment the launching shell already had. `core/__init__.py`'s
module-level `GEMINI_API_KEY` reads directly from `os.environ` at import
time, independent of Friday's own encrypted credential vault — so the
isolated server's in-memory key was the maintainer's real one from the moment it
booted, regardless of the (deliberately corrupted, for an unrelated test)
vault-stored key.

**The trigger was mundane**: routine `curl http://.../api/health` calls,
used only to check whether the isolated server was up. That endpoint
unconditionally runs a real one-shot generation probe against every
provider with a usable key — by explicit, pre-existing design (`provider_
health.py`'s own comment: "a key is CONFIGURATION, not health. Prove
inference") — not a special "boot self-test" step and not something this
audit's harness caused Friday to do differently than a real installation
would. For Gemini specifically, that meant one real call to
`generate_content`, prompt the literal two-character string "hi," capped
at 16 output tokens.

**Caught, not discovered later.** The assistant noticed the ambient-key
risk mid-investigation, said so explicitly in its own reasoning before
taking the next step, killed the isolated process immediately, and
relaunched a second stage with every real provider-key environment
variable explicitly nulled first. No further exposure occurred in that
investigation or any later round of this audit — every subsequent dynamic
check in this ledger runs under `pytest` with `FRIDAY_TESTING=1`, which
gates the entire daemon cascade off at import, or inside `tests/
conftest.py`'s isolated-home fixture. Neither boots a real server process
against a real ambient environment the way this one launch did.

**Cost**: bounded, not zero, and stated honestly rather than precisely — a
two-word prompt and a 16-token cap on Google's lowest-cost Gemini tier is
a small fraction of one cent at public pricing. This audit will not print
a more specific figure it cannot verify against actual billing, the same
discipline applied to the other unverified provider rates noted above.
Separately worth knowing: the code path that made this call does not call
`cost_meter` at all, so even a normal production boot's equivalent call is
currently invisible to Friday's own cost ledger — a pre-existing,
general gap, not something this incident introduced.

**What prevents a repeat**: this is not a code defect — the always-real
health probe is intentional, documented behavior, and a real installation
with real keys is supposed to do exactly this on every boot. The actual
gap was this investigation's own harness hygiene, already corrected within
the same session (every launch after this one explicitly nulled the real
keys first) and now written down as a standing rule: any future dynamic
boot of a real `server.py` process in this audit must clear
`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`,
`ANTHROPIC_BASE_URL` and `OPENAI_API_KEY` from the launching environment
first — redirecting `USERPROFILE`/`HOME` alone is not sufficient isolation
for provider credentials, the same lesson already on record for the
Windows keychain and GPU state, now extended to cover this too.

Full technical trace (env-inheritance mechanics, exact code lines, exact
transcript evidence) is in `findings.jsonl` under **F77**, not only here.

## What remains open, and why

- **F75** (success-detector false-positive) — the minimum honest fix
  landed per the maintainer's direct ruling (a third "unverified" state replacing
  the false "success" default); the real fix (genuine completion
  verification, per task/skill type) is specified as follow-up work
  directly in `skill_capture.py` and deliberately not built. This is a
  product design question, not a defect queue item.
- **F69 / F70** (VRAM-reserve fallback accuracy; residency-boot ordering)
  — checked directly against F75's "remove the false assertion, specify
  the real fix separately" shape and found NOT to fit it: neither has an
  active, consumed false claim today. F69's finding text is somewhat
  overstated (an undocumented secondary VRAM-reading fallback exists and
  likely reduces real-world exposure, though its actual success rate on
  real hardware is unchecked). F70's gap is a missing synchronization
  primitive with no smaller "stop lying" step available, since nothing is
  currently lying.
- **F56** — same check, also does not fit: the enforcement functions in
  question have zero live callers anywhere, so there is no consumed false
  signal to stop asserting.
- **Q10** (KG correction semantics + unbounded growth) and **Q6(a)**
  (USD budget alerts-only-by-design) remain open exactly because the maintainer
  asked to keep them as specific, named escalations rather than have an
  eviction policy or a budget-enforcement default invented unilaterally.
- **Q24** (scheduler Run Now can race into concurrent double-execution)
  needs a design decision on intended semantics before it can be fixed,
  not more investigation.
- **F48** is in hand in a separate session and intentionally wasn't
  duplicated here.

## Worktree and stash state

`git status` is clean. Everything in this document and this round is
committed (see the commit landing this file).

This repo's stash stack is shared across every worktree and concurrent
session on this machine. Six stashes existed when the maintainer's wind-down
message was written; five remain now:

- **Dropped**: `gauntlet-scene-name-removal-revert-check`, a stale
  leftover from an earlier red-on-revert cycle in *this* audit's own
  branch whose final cleanup step never completed. Confirmed safe to drop
  by two independent checks — `grep` found zero remaining trace of the
  reverted feature in current committed files, and `git merge-base
  --is-ancestor` confirmed the stash's base commit is already an ancestor
  of current HEAD.
- **Left untouched** (all five belong to other branches/worktrees and are
  not this audit's to act on): a hand-off explicitly addressed to
  whoever owns it, on `fix/f30-background-fallback-prompt-regate`; an
  unrelated in-progress fix on `fix/scheduler-retry-concurrency-2026-09-
  04`; a deliberate, permanent non-shipping exclusion for Breeze TTS2's
  non-commercial license, on `integration/release-2026-09-03`; an old
  working-tree snapshot already backed up elsewhere, on `wip/vibe-
  terminal-persistence`; an unrelated CI fix, on `fix/ci-green`.

None of the remaining five are stale by any check available here — they
belong to work this audit has no visibility into, which is the correct
reason to leave them alone rather than a reason to assume they're safe to
drop.

## On stopping

Asked directly whether the reasoning to stop was right, rather than to
just comply with it: agreed, and for the stated reason. Findings went flat
at 111 (now 112, only because of this close-out's own self-incident
disclosure) while commit velocity fell by roughly an order of magnitude
from overnight — that's a specific, checkable signal that the *hunting*
phase found what it was going to find with this method, not a vaguer
feeling that the session was tired. The items still open are genuinely
design questions (what should "success" mean, what should an eviction
policy look like, what should Run Now's concurrency semantics be) rather
than places nobody has looked yet. A fresh-context sweep next week is
likely to find something a tired 30-hour session wouldn't — seam coverage
says so directly, at 1 of 13 formally closed — but that argues for
*stopping and returning*, not for continuing past the point where this
pass's marginal finding rate had already dropped to zero.

Stopping now, per that instruction.
