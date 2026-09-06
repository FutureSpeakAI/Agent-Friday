# Outside review: professionalism across the repo

**Date:** 2026-08-17
**Branch assessed:** `residency-policy` (122 commits ahead of `main`)
**Reviewer:** a session that wrote none of this code and had no part in today's work
**Method:** read-only. The branch was exported to a scratch directory with `git archive`; nothing in the working tree, the index, or any branch was touched. The only file written is this one.

---

## How to read this

I looked for one thing above all others: **a subsystem that reports healthy while producing nothing, or produces something nothing consumes.** That was the signature of nearly every defect found today, and the instruction was to assume more remain. They do — eight of them, listed below.

Three labels are used throughout, and I have tried to be strict about them:

- **Evidence** — I ran something or read something that settles it. The command or the file:line is given.
- **Inference** — I am reasoning from what I read and could be wrong.
- **UNKNOWN** — I could not determine it and am not going to guess.

Findings are ranked by what would actually cost you, not by how neatly they explain. Bugs are separated from untidiness because you have limited time and the two deserve different amounts of it.

---

## The verdict, in one paragraph

This is a serious codebase and it is in better health than the day's defect list would suggest. The test suite is real — 5,982 tests, all passing, hermetic, with almost no vacuous ones. There are no secrets in the tracked tree or in git history. The residency arbiter, the headline feature, is genuinely load-bearing and well tested. What is wrong is narrower and more specific than "it's a mess": a handful of things were built and never connected, two safety gates cannot fire, a privacy control in your settings panel does nothing, a write capability against your real calendar shipped untested, and one of today's own fixes was built on a false diagnosis and is itself half-dead. The pattern you identified has not been eliminated. It has been reduced to a list short enough to finish.

---

# Part 1 — Bugs

## 1. Today's workspace-rollback fix was built on a false premise, and half of it is unreachable

**This is the most important finding, because it is a repetition of the exact failure it was meant to fix.**

Commit `2fbe1bf` ("the rollback net's first half") added `src/agent_friday/routes/workspace_undo.py`. Its docstring says:

> "The snapshot machinery for this already existed in services/workspace_studio.py and NOTHING could reach it — `revert_customization` had no route, no tool and no button anywhere in the tree."

**That claim is false.** `revert_customization` has had a route since **27 June** — seven weeks before the commit that says it had none:

```
src/agent_friday/routes/workspace_studio.py:79
    @ws_studio_bp.route('/api/workspace/<ws_id>/revert', methods=['POST'])
    def ws_revert(ws_id):
        ...
        doc = revert_customization(ws_id, version_id)
```

Introduced in `b15f1fd`, 2026-06-27. (Evidence: `git log -S"api/workspace/<ws_id>/revert"`.)

The consequence is not just an inaccurate comment. Two blueprints now register **the same URL with the same method**:

| Method | Rule | Registered by |
|---|---|---|
| POST | `/api/workspace/<ws_id>/revert` | `workspace_studio.py`, `workspace_undo.py` |
| POST | `/api/workspace/<ws_id>/reset` | `workspace_studio.py`, `workspace_undo.py` |

Flask accepts this silently — no error, no warning. Both rules land in the URL map and the **first-registered one wins**. `ROUTE_MODULES` in `server.py:88` lists `workspace_studio` before `workspace_undo`, so the old handler serves and the new one is dead code.

**Evidence** — I reproduced the exact registration faithfully and asked Flask which one answers:

```
url_map rules for that path:
    /api/workspace/<ws_id>/revert -> workspace_studio.ws_revert_studio
    /api/workspace/<ws_id>/revert -> workspace_undo.ws_revert_undo

POST /api/workspace/demo/revert  => {'served_by': 'workspace_studio'}
```

So: of the five routes in the new rollback net, `revert` and `reset` are unreachable. `history`, `undo` and `restore-as-of` are unique and do work. The two handlers that are shadowed also return a *different JSON shape* than the ones that actually answer, so anything written against the new module's documented contract will get the old response.

**And the user-facing half is still missing.** The commit describes these routes as "the button's half." There is no button. The string `api/workspace` with `undo`, `revert`, `history` or `restore-as-of` appears nowhere in `ui_parts/` or `index.html`. Stephen's original request — "rolled back either by telling Friday to roll it back or through a UI element" — is satisfied on the spoken half only.

**Why this matters more than its size:** the day's lesson was "verify the fact, not the shape of the answer." This commit asserted a fact about the codebase (`revert_customization` has no route) that a single grep would have refuted, then built 111 lines on top of it. Worth checking whether the same session made other "X was never wired" claims.

---

## 2. A privacy control in your settings panel is read by nothing

Settings → Privacy → **EGRESS GATE** shows:

> "Controls what data leaves the device. Tier 1 (public) passes through. Tier 2 (sensitive) is redacted. Tier 3 (private) is blocked."
>
> **Cloud Mode:** [ Audit ] [ Enforce ]

(`ui_parts/app.html:7994-7998`.) Clicking either button writes `egress_mode` via `save({egress_mode:m})`.

**Nothing reads `egress_mode`.** It appears in zero Python files. `egress_gate.py` has no audit/enforce mode — the concept does not exist in it. (Evidence: `grep -rl "egress_mode" src/` returns nothing; `grep -n "audit\|enforce" services/egress_gate.py` returns only prose in docstrings.)

**The direction of the failure is safe** — the gate always enforces, so selecting "Audit" cannot weaken anything. The harm is that the most safety-critical panel in the product contains a control that does nothing, presented beside controls that do. If you ever reasoned about your own exposure using that toggle, the reasoning was unfounded.

**Fix is a judgment call, not a code call:** either implement audit mode, or delete the control. Do not leave it.

---

## 3. Two safety gates exist and cannot fire

`services/boot_guard.py` defines three sibling gates. One is wired. Two are not.

| Gate | Purpose | Callers |
|---|---|---|
| `check_blast_radius()` | a UI/workspace patch may not touch routing, egress, safety rules | `workspace_studio.py:203` ✅ |
| `check_self_edit()` | refuse self-edits to boot-critical files | **none** ❌ |
| `check_scope()` | pause when one request would change >5 files | **none** ❌ |

(Evidence: `grep -rn "check_self_edit\|check_scope" src/ tests/ ui_parts/` returns only the definitions.)

`check_scope` is documented against a specific incident:

> "The nine-identical-images batch is the pattern: the model did what it thought was asked, at a scale nobody wanted, and nothing stopped to check."

That guard was written in response to a real event, reasoned about carefully, and cannot fire. `check_self_edit` is the guard that stops Friday from editing the files that let her boot — "a Friday that cannot start cannot undo it."

**Inference, not evidence:** I did not find an alternative mechanism enforcing either constraint elsewhere. It is possible one exists under a different name and I missed it. But on the plain reading, both protections are decorative.

---

## 4. Calendar writes against your real calendar shipped with zero tests

`services/calendar_write.py` — 347 lines, added today — has **no test file and is referenced by no test.** (Evidence: `grep -rln "calendar_write" tests/` returns nothing.)

It *is* properly wired (five call sites in `services/agent.py`), so it is live, not dead. It includes destructive paths:

- `update_event(..., allow_clearing=True)` — clearing a field, guarded by a `needs_confirmation` return (`:331`)
- a delete path
- a `dry_run` mode (`:188`)
- additive edits that proceed **without** confirmation by design

The design reasoning is sound and clearly argued in the docstring — additive edits are reversible from the receipt, destructive ones ask. But the boundary between "additive, proceed" and "destructive, confirm" is exactly the kind of logic that a test suite exists to pin down, and there is nothing holding it in place. A future refactor can move that line and nothing will notice.

This is the highest-consequence untested code in the repo: it writes to a real Google account, and the whole feature exists because a read-only token silently failed once already.

---

## 5. An entire module — the escape from `ollama pull` — is never imported

`services/model_fetch.py`, 313 lines. Its docstring:

> "Agent Friday — acquire a model without Ollama. The last thing `ollama pull` was needed for."

It fetches a GGUF from Hugging Face, verifies it against the publisher's own sha256, resumes interrupted downloads, and checks disk before starting. It is careful, well-reasoned work.

**The module name `model_fetch` appears nowhere in the repository outside its own file.** Not in `src/`, not in `tests/`, not in the UI, not in docs. Its two public functions, `fetch_gguf()` and `search_hf()`, have no callers. (Evidence: `grep -rn "model_fetch\|fetch_gguf\|search_hf" --include=*.py --include=*.html --include=*.md .`)

Also uncalled: `model_store.import_all_from_ollama()`.

**Why it matters:** you retired Ollama's *scheduler* today, but Ollama became **more** load-bearing, not less — the arbiter borrows Ollama's own `llama-server.exe` binary (`residency_arbiter.py:246`) and reads Ollama's blob store for GGUF paths. `model_fetch.py` is the module that would let you actually leave. It is finished and unplugged.

---

## 6. Your security test suite never runs in CI

`.github/workflows/tests.yml:48` runs:

```
pytest tests/unit tests/api -q
```

But `pytest.ini` sets `testpaths = tests` — the whole directory. So the default local run and the CI run are **different suites**. What CI never executes:

| Directory | Tests | In CI? |
|---|---|---|
| `tests/unit` | 3,886 | ✅ |
| `tests/api` | 1,239 | ✅ |
| **`tests/security`** | **106** | ❌ |
| `tests/edge_cases` | 21 | ❌ |
| `tests/integration` | 12 | ❌ |
| `tests/regression` | 10 | ❌ |
| `tests/smoke` | 6 | ❌ |

`tests/security/` contains `test_egress_gate_adversarial.py`, `test_kg_egress_adversarial.py`, `test_vault_crypto_hardening.py`, `test_auth_hardening.py`. The README advertises exactly this:

> "…an adversarial egress test suite guarding the cloud boundary."

It guards nothing automatically. **Good news:** I ran it and it is green — 160 tests, all pass. The suite is real; the enforcement isn't.

**Compounding this:** CI triggers on `push: [main]` and `pull_request`. `residency-policy` is 122 commits ahead of `main` and unpushed. **No CI has run on any of today's work.** That is expected for local branches, but it means the only thing standing behind today's ~150 commits is a local `pytest` run, which is a thing a person has to remember to do.

---

## 7. The documented key-storage model is not the operative one

`docs/INSTALLATION.md:160` tells the user:

> "Keys are stored encrypted per provider under `~/.friday/providers/keys/` (vault-passphrase or Windows DPAPI protection) — **never as plaintext in `settings.json` or source files**."

The encrypted store exists and works. But `core/__init__.py:713`:

```python
# API keys ALWAYS come from start.bat — stale Windows User-scope env vars
# ... would otherwise shadow the fresh key
_FORCE_OVERRIDE = {
    'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY',
}
```

For the four providers that matter, the **plaintext values in `start.bat` forcibly override everything else**, including the encrypted keystore. The comment is honest about why — a stale env var caused real auth failures — and the decision is defensible. The documentation is not. A reader of `INSTALLATION.md` would conclude their Anthropic key is encrypted at rest. In practice the operative copy is plaintext in `start.bat`.

**Mitigating (verified, and worth stating plainly):**
- `start.bat` is **not** tracked — `.gitignore:7` matches `*.bat`. (Evidence: `git ls-files --error-unmatch start.bat` → not known to git.)
- **No live credentials exist anywhere in the tracked tree.** Every match for `sk-`, `AIza`, `sk-ant-`, `ghp_`, `xoxb-` is an obviously fake test fixture, each carrying `# pragma: allowlist secret`.
- **No real key has ever been committed.** I scanned every reachable commit across all 880 revisions for real-shaped Gemini and Anthropic keys and found none. The earlier concern about a leaked key in history does not reproduce against this tree.

So this is a **documentation-honesty** finding, not a leak. The plaintext file is a deliberate, gitignored, local mechanism. Just don't tell the user it's encrypted.

---

## 8. The residency layer is invisible to the person who owns the machine

The word **"residency" appears zero times in the entire UI.** Not in `ui_parts/app.html`, `head.html`, `liquid_ui_panel.html`, `skills_observatory.html`, `styles_and_scene.html`, or `index.html`. (Evidence: `grep -c -i residency` on each → 0.)

This is 2,451 lines across `residency_arbiter.py`, `residency_policy.py`, `residency_catalog.py`, plus a route module. It is the thing that owns your GPU, decides which model is resident, spawns and kills `llama-server` processes, and grants leases.

**To be fair to it, this is not the dead-subsystem pattern.** It is genuinely consumed — by `model_router`, `pause_forecast`, `local_image`, `model_catalog`, `liveness_audit`, `tasks`, `work_plan` — and it is genuinely tested: 92 unit tests plus 5 opt-in live integration tests. It works and things depend on it.

The gap is that its two endpoints, `/api/residency/status` and `/api/residency/replan`, are reachable **only by curl**. When a request is slow because a model is being swapped, when a lease is held, when the plan has degraded — you have no way to see it. Today's monitor kill was diagnosed by hand precisely because there is no surface that would have shown it.

**This is the biggest gap between what has been built and what you can use.**

---

# Part 2 — Untidy, not broken

These cost you nothing today. Fix them when you're near them.

**Four inert cosmetic settings.** `auto_open_chat`, `compact_mode`, `scene_name`, `startup_workspace` are each written by a settings control, echoed back by that same control, and read by nothing else — no backend, no other UI code. Choosing a holographic scene or a startup workspace has no effect beyond the button appearing selected. (Same mechanism as finding #2, but cosmetic rather than safety-relevant.)

**Two files named `model_router.py`.** `routing/model_router.py` (807 lines, *where* to send) and `services/model_router.py` (2,575 lines, *dispatch*). The split is deliberate and both docstrings cross-reference each other, so this is not a mistake — but a contributor grepping `model_router` gets two answers, and every import site has to be read carefully to know which layer it's in. Renaming one (`routing/dispatch_target.py`, say) would remove a permanent papercut.

**A duplicated vision block in `chat.py`.** Roughly 15 identical lines at `:201` and `:923` — same Gemini client construction, same model, same prompt. The copy at `:188-196` carries a thoughtful comment explaining that image bytes cannot be text-classified by the egress gate and why that is an accepted tradeoff. The copy at `:923` has no such comment. Same behaviour, half the honesty. Two copies of a privacy-relevant decision will drift.

**A comment citing a document that doesn't exist.** `chat.py:193` — "the tradeoff is stated in the Data Security Guarantee, not hidden." There is no "Data Security Guarantee" anywhere in the repo. (Evidence: `grep -rn "Data Security Guarantee"` → no matches.) The tradeoff is in fact *not* stated anywhere user-facing, so the comment's claim that it isn't hidden is itself wrong.

**`INSTALLATION.md` lists Ollama as "Optional."** Line 15: `| **Ollama** | Latest | Optional — for local model routing |`. After today it is closer to required — the arbiter looks for `lib/ollama/llama-server.exe` and raises `TransitionError("no llama-server binary found")` without it, and `residency_catalog` resolves GGUF paths out of Ollama's blob store. Meanwhile `llama-server` and `llama.cpp` appear **nowhere** in `README.md`, `INSTALLATION.md`, `CONFIGURATION.md` or `CONTRIBUTING.md`. Someone setting up from the docs would not learn that Friday now runs her own inference processes.

**A work queue that never purges.** `work_queue.purge_finished()` has no callers; the queue grows without bound. Low impact, trivially fixed.

**One genuinely vacuous test.** `tests/unit/test_work_log.py:71` — `test_log_finish_records_tokens` calls `log_finish` with `tokens=200` and asserts nothing at all. It cannot fail except by exception, and it is named for the one thing it does not check. Its neighbours in the same class do assert. (The other 15 assertion-free tests are legitimate "does not raise" smoke tests or Playwright screenshot captures.)

**`/api/liveness` has no UI.** The output-liveness audit written today — the tool built specifically to catch this whole class of problem — is itself reachable only by typing a URL. It is registered correctly (`server.py:85`) and works. But it is not mentioned in the README, has no button, and nothing schedules it. It will be forgotten by the same mechanism it exists to detect.

---

# Part 3 — What is genuinely good, and worth not breaking

I want to be as specific about this as about the faults, because a report that only lists problems misrepresents the thing.

**The test suite is real evidence, not decoration.** I ran the full offline suite: **5,982 tests collected, all pass, exit 0.** I checked whether they could fail, by parsing every test function's AST:

- 3,189 test functions analysed across 265 files
- **0** contained an `assert <constant-true>`
- **16** had no assertion at all — and 15 of those are legitimate (Playwright screenshot captures, and explicit "should not raise" smoke tests that *would* fail on exception)

That is a far better ratio than the day's defect list would predict. Whatever else is true, the tests are not theatre.

**The test infrastructure is thoughtfully built.** `tests/conftest.py` redirects `Path.home()` to a throwaway temp dir before anything imports `server`, so tests cannot touch your real vault, settings or creations. It explains *why* it uses `setdefault` (pytest imports conftest twice, and a module constant would silently disable every live test). `pytest.ini` documents which suites are excluded and how to run them. This is better than most commercial repos.

**No secrets, anywhere.** Tracked tree clean; 880 commits of history clean; `*.bat` gitignored. Verified, not assumed.

**The residency arbiter's core defect is fixed.** "A residency plan that existed only as JSON nothing read" is no longer true — there are a dozen real consumers across routing, forecasting, imaging and cataloguing.

**`liveness_audit.py` is honest about its own subjects.** It doesn't just check "did it run." Its verdicts include `ORPHANED`, and for memory-dreaming it records the consumer as `"/api/memory/dreams (display only) — nothing feeds it back"`. That is a system telling the truth about its own weakness, which is rare.

**The music tool no longer lies.** Demo mode returns `status: "demo"`, sets `demo: True` on the tool record, labels the orb "Music — demo", and names the file `friday-music-demo-*`. The placeholder is now unmistakably a placeholder.

**Commit messages are unusually good.** They describe what changed and why in plain language. `git log` is readable as a narrative, which is why finding #1 was findable at all.

---

# Part 4 — Where the other branches diverge

Both other branches are **strict descendants** of `residency-policy`, so nothing assessed here is stale relative to them.

**`model-suite-determination`** = `residency-policy` + 1 commit. Documentation only: `docs/audits/model-suite-determination.md`, 441 lines added, no code. No conflict risk.

**`deep-research-gate`** = `residency-policy` + 5 commits. 2,481 insertions across 10 files. New: `judgment_gate.py` (641 lines) with `tests/test_judgment_gate.py` (323 lines) — a healthy ratio, and notably the *only* major new subsystem today that shipped with its own adversarial tests. Also `web_search.py`, `web_fetch.py`, `web_safety.py`, and `docs/design/switchyard-position.md`.

**⚠️ One merge risk worth naming now:** that branch adds **+263 lines to `services/egress_gate.py`**. That is the single most safety-critical file in the repo and the one place the privacy architecture depends on being coherent. A conflicted or hand-resolved merge there is the highest-risk operation on your near horizon. When it lands, the right check is not "does it still import" but "does `tests/security/` still pass" — which, per finding #6, CI will not tell you.

It also touches `calendar_write.py` (+12) and `agent.py` (+162/-69), so finding #4 (untested calendar writes) applies to that branch too, with more surface.

---

# Part 5 — What a new contributor trips over

**What works:** `CONTRIBUTING.md` gives a real setup path, names the exact test command, documents the project layout, and flags sensitive areas. `pyproject.toml` declares `requires-python = ">=3.10"`. Blueprint discovery is guarded by `tests/unit/test_blueprint_discovery.py` so the route list can't silently go stale. The CI install list explains *why* it omits the heavy ML stack. These are all signs of someone who has thought about the next person.

**What they trip over:**

1. **`CONTRIBUTING.md` says to run `pytest tests/unit tests/api`; `pytest.ini` runs everything.** A contributor following the docs gets a different, smaller answer than a contributor typing `pytest`. Neither is wrong, but they will disagree about whether the tree is green.

2. **The runtime story is undocumented.** Nothing in the setup docs mentions `llama-server`, `llama.cpp`, the arbiter, or that Friday now spawns her own inference processes. A contributor reads "Ollama — Optional" and builds a wrong mental model of the most important subsystem in the repo.

3. **There is no map of `services/`.** 90 modules in one flat namespace, several with near-identical names (`provenance.py` vs `response_provenance.py`; `model_router.py` in two packages; `notifications.py` in both `services/` and `services/notifications_engine.py`). `response_provenance.py`'s docstring explicitly warns about the first collision — which is generous, but it's a warning where a naming convention should be.

4. **`index.html` is 1.5 MB of generated output and is tracked.** The real source is `ui_parts/*.html`. It is not obvious which to edit, and no doc I found says. (**UNKNOWN:** I did not locate the script that assembles `ui_parts/` into `index.html` — `scripts/*.py` has no reference to `ui_parts`. If that build step is undocumented or missing, it is a bigger problem than it looks, because it means UI changes may be being made in both places.)

---

# The short list

If you do five things, do these. Ranked by cost to you, not effort.

**1. Resolve the duplicate workspace routes.** *(finding #1)*
Two blueprints claim the same URL; today's new handlers are dead; and the reasoning that justified writing them was factually wrong. Delete the duplicated pair from one module — almost certainly `workspace_undo.py`, since `workspace_studio.py`'s versions are the ones actually serving — and correct the docstring. **Then check whether that session made other "X was never wired" claims**, because the diagnostic method that produced this one was unsound.

**2. Decide what `egress_mode` means, then make the panel true.** *(finding #2)*
Implement audit mode or remove the toggle. A dead control anywhere is untidy; a dead control in the egress panel undermines the one guarantee this product is actually built around. Ten minutes either way.

**3. Wire `check_scope` and `check_self_edit`, or delete them.** *(finding #3)*
`check_scope` is the guard against the nine-identical-images event. It exists, it is correct, and it cannot fire. Its sibling `check_blast_radius` shows exactly what wiring looks like — one import and one call in the right path.

**4. Put `tests/security` in CI.** *(finding #6)*
Change one line in `.github/workflows/tests.yml` from `pytest tests/unit tests/api -q` to `pytest -q`, or add `tests/security` explicitly. The suite passes today — I ran it — so this costs nothing now and catches the egress regression that finding #4's and the deep-research branch's changes could introduce. Given that the judgment gate branch adds 263 lines to `egress_gate.py`, do this **before** that merge.

**5. Write tests for `calendar_write.py`.** *(finding #4)*
Specifically the additive-vs-destructive boundary: that clearing a field returns `needs_confirmation`, that additive edits proceed, and that the receipt really does carry the prior value of every field touched. That last one is the entire basis for letting additive edits skip confirmation, and nothing currently holds it true. This is the only untested code in the repo that writes to a real account you care about.

---

**Two things deliberately left off that list**, because they are larger decisions rather than fixes, and they are yours to make:

- **The residency layer has no UI** (#8). This is the biggest gap between what exists and what you can use, but it is a feature to design, not a bug to patch.
- **`model_fetch.py` is unplugged** (#5). 313 lines of finished work standing between you and actually leaving Ollama. Connecting it is a decision about direction, not a repair.

---

*Read-only review. No files were modified, no branches touched, no commits made. This report is the only artifact.*
