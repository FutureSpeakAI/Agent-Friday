# Status — for Stephen, reading this cold

*Written 2026-09-05. Everything below reflects branch `integration/release-2026-09-03`,
now at commit `6db3bd7`, tag `v5.12.0` (local only, not pushed).*

You asked for one document you could sit down with. This is it. Four sections:
what's already installed and checked on your machine, what's staged and
waiting only on your git credentials, what needs a decision from you (each as
one question), and what's still genuinely broken. Nothing here is optimistic
where the evidence was mixed — a few things below are honestly unresolved,
and I've said so rather than picked the answer that sounds better.

---

## Do this first: rotate the vault passphrase. It is not "in history awaiting
## a push" — it is already on GitHub.

An earlier version of this document treated the leaked passphrase (full
detail at Q2, §3) as a local-history problem, contained by the fact that
nothing had been pushed. That was wrong, and it was corrected by an
independent check after this document first went out. The actual state,
verified directly:

Commit `58492fe` — the commit that introduced the real, working passphrase
into `RELEASE_NOTES.md` in plain text — is an **ancestor of
`origin/feat/update-check`**, a real branch on GitHub. `git log
origin/feat/update-check -S"<the leaked value>"` finds it directly, reachable
right now by anyone with read access to this repository. A later commit on
that same branch (`c47ce33`, a 5.8.0 release-notes rewrite) removed the value
from that branch's *current* file content, the same way my own fix removed it
from this branch's HEAD — but removing it from a later commit's file content
does not remove the earlier commit or its blob from history. Both are still
there, on GitHub, and have been since `c47ce33` was pushed — six days, not
zero.

**This changes the priority, not the instruction.** Nothing about the
passphrase has been acted on beyond removing it from this branch's current
file — no history rewrite, no force push, no branch or tag deletion, all
still yours to decide (Q2, §3). Rotating the passphrase is the first thing
to do when you sit down, ahead of anything else in this document, including
the release.

---

## 1. Installed and verified on your machine

Your Friday was stopped by exact PID (never a broad taskkill), backed up in
full first, and relaunched via `friday_startup.vbs` — twice: once for the
main merge, once more to pick up a gap found while verifying the first
restart (the video-model picker, below). Both times confirmed against the
running app, not the filesystem.

- **Backup**: `%USERPROFILE%\Desktop\friday-backup-2026-09-05-pre-release-merge`,
  103.6 GB, verified complete (101,538 files on both sides, exact match). It
  swept in `~/.friday/runtime` (ComfyUI, llama.cpp, voice models, venvs —
  102 GB of it) without my checking its size first; that's most of why your
  free disk dropped from ~149 GB to ~48 GB. Nothing is wrong — this is the
  backup doing its job — but see §3 for the "can I delete part of it" question.
- **Version**: the running process still reports `5.11.0`. Checked precisely
  rather than assumed: the process was last restarted at 12:46 (picking up
  the video-model-picker fix below, which was already on disk and verified
  live at that point); the version-bump commit and every commit after it
  landed later, 13:22–13:30. Walked every one of those later commits —
  `pyproject.toml`'s version bump is a display string nothing in the running
  server reads for behavior; the `install.ps1` fix is Windows-installer code
  the running Flask process never touches; the documentation commits are
  static files the app doesn't read at runtime. **Nothing functional landed
  after the 12:46 restart. The version mismatch is cosmetic only** — the
  running process is behaviorally identical to what's on disk now, it just
  displays an old number until the next restart.
- **6 new local creative models** confirmed live in the picker (`GET
  /api/intelligence`) with licences: 3 image (already working before this
  session), 3 video (`wan2.2-ti2v-5b`, `wan2.2-14b-a14b-gguf`, `cogvideox-2b`,
  all Apache 2.0) — the video ones were merged but never wired into the
  picker endpoint; found and fixed live, restart-verified.
- **Privacy page**: confirmed the EGRESS GATE / Cloud Mode control is absent
  from the served `index.html`, as intended.
- **Knowledge-graph conversation indexing — partial**. The original bug (a
  broken method call silently returning zero conversation chunks) is
  provably fixed: a live delta reindex gathered **1,569 real chunks** where
  it used to gather none. But the reindex then failed outright with
  `no_local_model` — **this machine has no Gemma 4 model installed via
  Ollama**, and the new ladder correctly refuses to fall back to an old Qwen
  model even if one happens to still be on disk. This is a direct,
  live consequence of the Qwen→Gemma4 rebuild landing this session. See §3.
- **Provider keys (OpenRouter, AtlasCloud, Firecrawl) — mixed evidence, not
  fully resolved.** You predicted these would break on restart. What I
  actually found:
  - The live app itself reports OpenRouter as working (boot log: "Provider
    keys: 1/1 decrypted") and **proved** Firecrawl working with a real,
    successful live search during verification.
  - A separate standalone script, checking the same credential store
    directly, reported all three as `present_but_unreadable`.
  - The boot log explains the likely reason: **your OS keychain and
    `start.bat` currently hold two different vault passphrases** ("two
    different passphrases are stored... the start.bat copy opens the vault;
    the other is stale"). The live server's real boot sequence finds and
    uses the working one; a bare script call to the same function, without
    replicating that boot-time reconciliation, picks the keychain's stale
    one and fails. I did not fully trace the exact mechanism that lets boot
    succeed where a plain call doesn't, and I did not touch either
    passphrase — that's your call, see §3.
  - AtlasCloud has no live status endpoint I could find (it's a creative
    provider, not in the LLM provider registry `/api/providers/health`
    covers) — I could not independently confirm its state either way.
- **A recurring disk leak, found and cleaned, root-caused.** 42 `friday_*`
  temp directories (5.86 GB, including three `friday_judgment_*` at up to
  2.76 GB each, created today) had accumulated in `%TEMP%`, on top of the
  103.6 GB backup — free disk had fallen to ~48 GB, and a full disk crashed
  this app yesterday, so this got immediate attention rather than a mention.
  Root cause: this is the *same* leak class the gauntlet already found and
  fixed once (`findings.jsonl` F49 — an isolated test home minted with no
  cleanup) — but several of the worktrees kept in §3's Q6 (real, unmerged
  work) branch from *before* that fix landed, so their copy of
  `tests/test_judgment_gate.py` still has the bug. Running their test suites
  to verify the claims in Q6 reproduced it. All 42 directories were safe to
  delete (hours-old, no live process using them) and have been; disk is back
  to ~54 GB free. The worktrees themselves are untouched — deleting old test
  artifacts is not the same as deciding what to do with the branches — but
  running tests inside any of them again will very likely reproduce this
  until whichever of them get merged or discarded.

## 2. Ready, staged, one command away

Everything is committed to `integration/release-2026-09-03`, working tree
clean, full bare `pytest` green except the one known flake (§4). Tagged
`v5.12.0` locally. Nothing has been pushed — your git credentials are broken,
and that's yours to fix, not mine to work around.

**The installer artifact itself has been built** —
`packaging\windows\dist\AgentFriday-Setup-5.12.0.zip` (22.1 MB, SHA-256
`bd904892d5f23d30b55356b53fdd22851ec668a896ede503462979d4ab085c1e`), produced
by `build-installer.ps1` running entirely locally (it fetches Python from
python.org and builds a handful of pure-Python wheels — no git, no push,
nothing that needed your credentials). The build's own payload-scan step
checked for exactly the kind of leak described in Q2 (§3) and found none. This
zip is the thing you'd actually attach to a GitHub release or send someone
directly — the previous newest artifact on disk was `AgentFriday-Setup-5.6.1.zip`
from 26 August, six releases stale.

When they're fixed, the release is:
```
git push origin integration/release-2026-09-03
git push origin v5.12.0
```
(That assumes you want this branch to *become* the thing you push to — see
§3's first question before running it. If you decide to merge into `main`
first, do that instead and the tag moves with it.)

What's in it: 9 commits of work found sitting uncommitted from earlier this
session (model ladder rebuild, KG mode-choice, unrestricted-cloud-mode, tray
crash-notification, news-timeout, vault re-encryption), the gauntlet audit
merged in full (61 commits, 82 fixes), `feat/headroom-build` merged (11
commits), a scheduler/heartbeat cost fix, three real bugs found and fixed
*during* the merges themselves (a `pinned`-variable `NameError` in the KG
indexer, a tier-computation staleness risk in `recommend_models`, and the
installer's own model ladder never having been updated for Gemma 4), a
security fix (`proof_of_integrity.py`'s fail-open bug), the passphrase leak
fix from earlier today, documentation reconciled against the gauntlet's own
findings, and updated `CHANGELOG.md`/`RELEASE_NOTES.md`. Full detail in both
of those files.

## 3. Needs a decision from you

**Q1 — main vs. integration: reconcile now, later, or not for this release?**
They share a base 5 weeks back. Main has 39 commits integration lacks: an
`os-mode` subsystem (a `paths.py`/`agent_friday.paths` refactor, six OS-mode
gates, a fail-closed credential + vault-passphrase storage rewrite), the
OpenRouter Auto Router, streaming chat, `update_check.py`, and the real,
tagged v5.9.0/v5.10.0 releases. Integration has 115 commits main lacks — all
of this session's security/vault/gauntlet/model-ladder work. I did not
attempt this merge: main's credential/vault-passphrase rewrite (PR-5) and
this session's own credential-store changes touch the *same* subsystem, so
this isn't a mechanical fast-forward, it's a real merge with a real risk of
semantic conflicts in exactly the area most worth getting right. I'd rather
hand you the evidence than a guess.

**Q2 — the vault passphrase in git history: not local, not contained, already
public.** Confirmed reachable from `origin/feat/update-check` on GitHub (see
the top of this document) — this has been publicly fetchable for six days,
not sitting in local history waiting for a push. Untouched, as instructed:
no history rewrite, no force push, no branch/tag deletion. Those remain your
call, and none of them un-expose a secret that's already been fetchable for
nearly a week regardless — rotating the passphrase is the one action that
actually changes anything, and it doesn't require touching git at all.

**Q3 — the stale OS-keychain passphrase (separate from Q2): worth cleaning up?**
This is a *different* passphrase problem — your keychain holds an old value
that doesn't match `start.bat`'s current one (§1's provider-key finding). A
maintenance capability built earlier this session
(`POST /api/vault/reencrypt-stale-keys`) exists specifically for this: it
re-encrypts whatever the current process can still decrypt under a fresh
key, with a backup taken first. I did not run it — it touches vault key
material, and I treated that as covered by your standing instruction not to
act on anything passphrase-related without asking. If you want it run, say
so and I will, with the backup step shown to you first.

**Q4 — install a local model, or accept cloud-only KG indexing?**
`ollama pull gemma4:e2b` (7.2 GB) fixes §1's KG blocker directly. Alternative:
switch Settings → Knowledge Graph to Cloud mode, which costs real (if
Sonnet-cheap) money per indexing pass instead.

**Q5 — the 96 local git tags carrying a real API key and password fragment.**
Found via an old, *untracked, gitignored* local audit file
(`docs/audits/release-readiness.md`) that documents 96 local-only
`archive/*` git tags whose blobs contain a real-shaped Gemini API key and
what looks like a real password, in full, in `start.bat`/`friday_startup.bat`
snapshots. Confirmed not reachable from any published ref today — but one
`git push --tags`, `--all`, or `--mirror` away from being so. I did not touch
these (same reasoning as Q2/Q3 — this is passphrase-adjacent territory).
The audit's own recommendation, which I'm relaying rather than acting on:
delete the archive tags locally, or rename the namespace so no future push
glob catches them.

**Q6 — which of the finished-but-unmerged branches do you want pulled in?**
None deleted — all still exist as worktrees or branches. Real, apparently
complete work, by my read:
- `fix/dependabot-2026-09-01` (worktree `friday-dep-audit`) — a security
  audit doc + regression test for 5 unfixable Dependabot alerts. Small, safe.
- `docs/version-truth-audit` (worktree `updchk`) — one doc, documents three
  divergent "what version is this" code paths. Small, safe.
- `claude/trusting-ardinghelli-d8fbe4` — a real seat-reachability fix for
  `local_seats.py`. Small, no test coverage added — worth a look before
  merging blind.
- `deep-research-gate` (worktree `friday-desktop-research`) — 4 defect fixes
  (vault receipts not persisting, a port-reuse leak, a reasoning-content
  parsing gap, MoE misdetection), validated by a real 40-minute live run, no
  new unit tests.
- `model-suite-determination` (worktree `friday-desktop-suite`) — fixes a
  measured 3.4x prompt-token discrepancy in the tool-seat context budget.
  Real, looks complete.
- `conversations-and-concurrency` (worktree `conv-build`) — mostly superseded
  by what actually shipped (a different multi-conversation implementation),
  but one piece, `task_ledger.py` ("background work that survives the
  process that spawned it") plus 6 test files, doesn't exist anywhere in
  integration today and I couldn't determine whether the shipped
  "work reports back after a restart" feature already covers the same need
  through different code. Needs someone who knows that subsystem to compare
  — genuinely unclear, not confirmed either way.
- `worktree-spec-grow-button` and `worktree-spec-onboarding-interview` — both
  docs-only design specs, no code. Low cost to keep, low urgency to act on;
  listed so nothing gets tidied away by accident.
- `feature/crew-0` (worktree `friday-desktop-crew`) — 3 of its 4 original
  defect fixes are real and current (X2: a missing auth-gated route; X3: a
  workflow-chain auth gap, though likely low-severity given the global
  `before_request` auth gate; X4: a fail-open bug in `proof_of_integrity.py`
  — **already fixed independently in this release**, so X4 specifically is
  redundant now). X1 is confirmed superseded/dead — the seat-gating
  mechanism it patches was deliberately removed the next day.
- `verify/gauntlet-fixes-2026-09-04` and `-05` (worktrees `gauntlet-verify`,
  `gauntlet-verify2`) — **not code, corrections to the audit's own ledger**:
  a first cold-verification pass found F37's fix didn't actually work
  (case-sensitivity bug, now really fixed in this release) and 2 other weak
  probes; a second found the suite was never run with the real bare `pytest`
  invocation until then. Worth folding into `docs/audits/gauntlet-2026-09-03/`
  for the historical record, even though the *fixes* those passes prompted
  are already in this release.

**Q7 — the `amazing-heisenberg` fix: who finishes it?**
See §4 — real, needed, not safe to ship as-is.

**Q8 — can I (or you) delete the `runtime` subfolder from today's backup?**
102 GB of the 103.6 GB backup is regenerable local-AI infrastructure
(ComfyUI, llama.cpp builds, voice models), not irreplaceable data. Once
you've used Friday for a bit and are confident this release is solid, that
subfolder is safe to delete to get most of your disk space back. (Disk is at
~54 GB free as of this document, after the temp-leak cleanup in §1 — not
critical, but this and that cleanup are the two real levers if it gets
tighter again.)

## 4. Genuinely still broken

- **`amazing-heisenberg` (branch `claude/amazing-heisenberg-005602`,
  uncommitted, at risk of loss if that worktree is ever cleaned up)**: a real
  usability fix — ordinary tool-call arguments containing words like
  "contact" or "family" get wrongly hard-blocked with `[VAULT ACCESS
  DENIED]`, because the sensitivity classifier used the same broad rule for
  "does this text contain personal data" (right for stored vault content)
  and "is this request about something private" (wrong for a tool call's own
  arguments). **Not a security hole** — it fails safe, just clumsily — and
  its own test suite currently has 2 failing tests, so it was never a
  candidate to ship in this release.
- **`test_nemo_voice.py::test_nemo_models_ready_false_when_uncached`** — the
  known flake. Fails only under the full bare `pytest` run, passes every
  narrower subset tried. Left open per your own earlier ruling on this exact
  test; still open.
- **F75 (skill-optimization "success" scorer)** — the minimum honest fix
  shipped (a third "unverified" state, replacing a false default of
  "success"); the real fix, genuine per-task completion verification, is
  specified as follow-up work in `skill_capture.py` and deliberately not
  built.
- **F56, F69, F70** — confirmed-pending items from the gauntlet ledger:
  dead tool-permission-enforcement code in `scoped_agents.py` (the cleanup
  half of that finding is fixed; the enforcement half is a policy question);
  a VRAM-reserve fallback running on a hardcoded floor instead of live data
  on this exact machine; residency booting on an unjoined daemon thread with
  no guaranteed ordering against the first HTTP request. None are acted on;
  all are real.
- **Q10, Q6(a)** — open by design, per your own earlier instruction to keep
  them as named escalations rather than have a policy invented for them:
  knowledge-graph correction/eviction semantics, and budget-alerts-only
  (vs. enforcement) for cost controls.
- **Firecrawl's egress-gate coverage** — genuinely unresolved, not confirmed
  either way. `web_fetch.py` routes Firecrawl fetches through
  `register_public_text`, which marks *inbound* fetched text as
  gate-exempt — that isn't obviously the same claim as "the *outbound*
  Firecrawl call itself is gated." Flagged as unverified in
  `KNOWN_ISSUES.md` rather than asserted.
- **The provider-key-status discrepancy from §1** — the live app and a
  standalone script disagree about whether your 3 provider keys are
  readable, and I have a strong hypothesis (the stale-keychain-vs-start.bat
  mismatch, Q3) but did not fully trace the exact code path that makes the
  live server succeed where the script fails. Worth someone with more time
  confirming precisely, not just accepting the hypothesis.

---

*Housekeeping note: `git worktree list` showed 49 entries before cleanup this
session, not 33 or 27 — I went by that direct count rather than either
estimate. 36 were removed (confirmed either fully merged already or
superseded, each checked against current `integration/release-2026-09-03`
before deletion — none removed blind). 13 remain: the main worktree, the 9
listed in §3's Q6 (including the two docs-only specs and the genuinely
unclear `conversations-and-concurrency` piece), `amazing-heisenberg` (§4),
and `gauntlet-verify`/`gauntlet-verify2` (also §3's Q6).*
