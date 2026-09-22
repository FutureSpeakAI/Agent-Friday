# Laya across the Friday harness

**Status:** design, pending Stephen's go
**Written:** 2026-09-22
**Question asked:** "What else can Laya do across the Friday harness? I think this is
a very good thing and maybe even a dependency for the Friday desktop's new installs
going forward."

---

## The short answer

Laya is aimed at a real and large gap. Friday makes roughly forty typed judgments,
the three most consequential are substring scans, and two of those three sit in the
path of actions that cannot be undone. That is exactly the shape of problem a small
calibrated classifier is for.

But Laya **as measured on this machine cannot take any of those decisions today**,
and the reason is not a tuning detail. On 40 real messages it put 39 under 0.5
confidence and collapsed half of them into one lane. The model card says why: the
published checkpoint is near-chance zero-shot and expects fine-tuning. Fine-tuning
needs labelled examples of Stephen's decisions, and as of today there are none,
because `~/.friday/decisions.jsonl` **does not exist on disk** — not one gate
decision has been recorded yet.

So the answer to "should it be a dependency for new installs" is **not yet, and not
in that form**. The answer to "what else can it do" is a ranked list, below, where
the top two items turn out not to need Laya at all — and doing them is what makes
Laya evaluable later.

---

## 1. What the harness actually decides

A survey of `src/agent_friday/services/` found ~40 places that answer a typed
question about some text or state. Ranked by volume times cost-of-being-wrong:

| Judgment | How it decides today | Runs | Wrong costs |
|---|---|---|---|
| `sensitivity_classifier.classify` | regex + keyword lists | per field, per paragraph, per JSON node, per cloud call | private material egresses, or a turn dies on a local seat too small for its payload |
| `dissent_gate.classify_severity` | ~40 substrings + one regex patch | every approval card, including every outbound email | mail sends with no human, or legitimate work parks in a queue until it expires |
| `moderation._apply_harm_floor` | 50 regexes, first match wins | every approval card | an unliftable block, or a paraphrase walks through |
| `routing/model_router.classify_task` | keyword lists + a 200-char length rule | every chat turn | wrong model, wrong cost, dead turn |
| `message_triage.classify` | **weighted scoring, derived confidence, online per-sender learning** | per message per inbox load | actionable mail lands in `noise` and is never seen |
| `qa_gates.evaluate_text` | LLM judge, ungrounded self-score | every goal milestone | milestone marked done on a hallucinated pass |
| `agent._is_affirmative` | ~30 anchored regexes | every turn with a pending confirmation | executes a gated tool the user did not approve |

Below that line sit the epistemic scorers, the ambient-state model, and the
introspection rubrics, all of which present hand-picked constants as measurements.
They are low-consequence and high-dishonesty: the fix there is labelling, not
modelling.

### Two things the survey got wrong, corrected by running them

**The harm floor is not failing open.** `content_policies.evaluate_content` and
`moderation.scan` call each other with no depth guard: a single approval card makes
**250 mutual calls**, terminating in a swallowed `RecursionError`. That looks like a
Law-1 hole. It is not. Measured directly: genuinely harmful content blocks at **stack
depth 2**, because the H1–H4 regexes fire before the recursion begins. Only the
benign path recurses. So it is waste on every card, not a gap. Fix it, do not panic
about it.

**`judgment_gate` is the best-built classifier here and it is switched off.** It is
an LLM appeals court over the keyword rules, it can only ever *rescue* a blocked span
and never authorise one, a deterministic first-person floor sits above the model, and
every overturn is logged with the span hashed rather than stored. `enabled()` returns
False by default. Turning it on is a better day's work than installing anything.

---

## 2. What was measured about Laya

Installed 0.3.5, run on CPU against 40 real inbox messages with Stephen's own six
lanes as the criteria (`tools/triage_laya.py`):

- **39 of 40 answers scored under 0.5 confidence.**
- **20 of 40 collapsed into `career`** regardless of content.
- **~3.5 seconds per message** on CPU.

The model card is not surprised by this. The published checkpoint is a typed-decision
encoder trained with RLCD on general data; it reports near-chance zero-shot
performance and expects fine-tuning on the target decision. Its calibration claim
(ECE) is about the fine-tuned case.

Read plainly: **Laya did not fail. It has not been taught anything yet.** Taking
those numbers as a verdict on Laya would be the same error as taking a keyword
scanner's silence as evidence it is right.

The latency also rules out the highest-volume site on its own. `sensitivity_classifier`
runs per paragraph and per JSON node inside every cloud call; 3.5 s each is not a
thing that can go there at any accuracy.

---

## 3. Why the corpus is empty, and what that forces

`services/decisions.py` was built three days ago precisely to fix this: one seam,
`decide(question, state)`, logging every judgment to `~/.friday/decisions.jsonl` so
that a classifier could later be evaluated against something. It fronts two questions
today, `action_severity` and `policy_class`.

**The log file does not exist.** Zero rows. `approvals.json` holds one record. So
there is currently no way to answer "how often is the gate right", for the keyword
scan or for anything proposed to replace it.

This is the whole ballgame. Every proposal below that involves a model is gated on it,
and the sequencing is not negotiable: **a classifier swapped in before there is a
corpus cannot be shown to be an improvement, only asserted to be one.**

---

## 4. The plan, in the order it should happen

### Phase 0 — make the decisions visible (no model, days)

Route the remaining judgments through `decisions.decide` with their existing logic
untouched, as `approvals.classify` already does. Priority order: `classify_task`,
`needs_vault_access`, `sensitivity_tier`, `message_triage.classify`.

The verdicts must not move. `decisions.decide` already fails to the incumbent on any
backend error, and the existing test suite re-derives every verdict the old way and
asserts agreement — keep that discipline for each new question.

Ship alongside it a **"why did Friday decide that?"** surface in the UI, reading
`decisions.history()`. Its real job is not explanation. It is to make a wrong decision
*cheap for Stephen to correct in one click* — which is what turns ordinary use into
labelled data, the same way `message_triage`'s reclassify button already does.

**Done when:** a week of normal use yields a few hundred labelled rows.

### Phase 1 — collect the corrections that already exist

Three sources of ground truth are being thrown away right now:

- `message_triage` logs its *corrections* but never its *classifications*, so the
  learned sender priors cannot be evaluated. One-line fix, large payoff.
- `forensics/tasks.jsonl` holds ~1,777 labelled task outcomes nobody scores against.
- `judgment_gate`'s overturn log has the right shape and is empty because the gate is
  off.

### Phase 2 — fine-tune Laya on one question, and only one

The right first target is **`action_severity`**: two classes, the highest consequence
in the system, modest volume (so 3.5 s is affordable — this decision happens before an
action, not inside a render loop), and `decisions.py`'s `register_backend()` already
accepts it with a documented fail-open to `keyword`.

Deliberately *not* `sensitivity_classifier`, despite being top of the volume table.
Latency rules it out, and there is a cheaper fix (§5).

Acceptance, fixed before training rather than after:

1. Beats the keyword scan on held-out rows from the corpus, on **recall of `hard`** —
   a missed outward action is the expensive error, a false `hard` is an annoyance.
2. Calibrated: an abstain band where low confidence falls back to `keyword`, with
   temperature fitted on the log, not assumed from the model card.
3. Slower is acceptable; **unavailable must degrade, never block.**
4. Every swap recorded, so the rate at which the model overrides the incumbent is
   visible rather than inferred.

### Phase 3 — decide about new installs

See §6. Not before Phase 2 produces a number.

---

## 5. The cheaper fix that outranks all of this

`sensitivity_classifier` is the highest-volume, highest-consequence judgment in the
harness, and it advertises five layers while **running two**:

- **Layer 2 (Presidio):** observe-only by measurement — it scored TIER_2 where the
  regex returns TIER_3 and escalated 6 of 12 benign prompts.
- **Layer 3 (MiniLM embeddings):** excluded from the PyInstaller build, and switched
  off on the vault path with `use_embeddings=False`.
- **Layer 4 (local LLM):** `use_llm=False`, and no caller enables it.

Layer 3 is the important one, and here is the part that matters: **`_embedding_tier`
computes a real cosine similarity and `classify()` throws it away**, keeping only the
thresholded tier. There is already a genuine continuous score in the highest-stakes
classifier in the system, being discarded at the last step.

Surfacing that score and fitting its two thresholds on the egress and
`presidio_shadow` logs is a **calibrated classifier for the top-ranked decision,
using code that already exists, with no new dependency and no 3.5-second latency**.
The four documented over-redaction scars in that file — `courtesy`→`court`,
`incoming`→`income`, Friday's own system prompt nuked by "Sovereign Vault", a
storybook turn killed by "family picture-book aesthetic" — are precisely the symptom
a calibrated score fixes and a keyword list cannot.

**This should happen before any Laya work.** It is smaller, it is higher-value, and it
does not need a corpus that does not exist.

---

## 6. Should Laya be a dependency for new installs?

**No — but the cost is far lower than it first appears, and this is worth revisiting
once Phase 2 has a number.**

The PyInstaller spec excludes `torch`, `transformers` and `sentence_transformers`,
measured at 4.4 GB against a 152 MB .exe. Quoting that as the cost of Laya would be
wrong, because **the spec is not the shipping installer.** `packaging/windows/` builds
the real artifact, and its **memory tier already installs `sentence-transformers`,
which already pulls torch** — announced to the user as roughly 2.5 GB, with an offer
to skip.

So on any install that took the memory tier, Laya's marginal cost is `transformers`
plus the ModernBERT-large checkpoint (~800 MB, lazily fetched on first use, exactly as
`all-MiniLM-L6-v2` already arrives). That is a real cost, not a prohibitive one.

The reasons it still should not be a dependency:

1. **It would make torch mandatory.** The memory tier is optional and skippable by
   design. A hard Laya dependency silently reverses that, which is the same failure
   the installer already documents and fixed once, when `headroom-ai[all]` quietly
   dragged in the entire memory tier and made `-SkipMemory` skip nothing.
2. **A dependency that changes no decision is not a dependency, it is a download.**
   Until a fine-tuned checkpoint beats the incumbent on a measured corpus, shipping
   Laya adds 800 MB and alters nothing. Presidio is already in the install on exactly
   those terms, and the README had to be corrected for overstating what it bought.
3. **A new install has no corpus at all.** A zero-shot Laya on a fresh machine is the
   39-of-40-under-0.5 behaviour measured above — worse than the keyword scan it would
   replace, on day one, for every new user.

**The recommendation instead:** a fourth, opt-in **judgment tier** in
`packaging/windows/requirements/`, alongside core / recommended / memory, carrying
`transformers` and a Friday-fine-tuned checkpoint — offered only once the checkpoint
exists and has a number attached. Same consent pattern the memory tier already uses,
with the size stated out loud.

The honest default for a fresh install stays: keyword scan, fail-closed, **with the
decision log on from the first boot** — so that machine starts building its own corpus
immediately, and can be offered a real classifier three months later on the strength
of its own data.

---

## 7. What is being recommended, in order

1. Fix the `content_policies` ↔ `moderation` recursion. 250 calls per card, no depth
   guard. Cheap.
2. Surface `_embedding_tier`'s discarded similarity score and calibrate its thresholds
   on existing logs. **Biggest single win in the system.**
3. Turn on `judgment_gate`, or state plainly that it is off. Right now the docstring
   describes an appeals court that does not sit.
4. Route the remaining judgments through `decisions.decide`, verdicts unchanged, and
   ship the one-click correction surface.
5. Log `message_triage`'s classifications, not only its corrections.
6. After a few hundred labelled rows: fine-tune Laya on `action_severity` alone,
   against the acceptance criteria in Phase 2.
7. Only then: the opt-in judgment tier for new installs.

Items 1–5 need no new dependency and are worth doing whether or not Laya is ever
adopted. Item 6 is where Laya earns its place, or does not.

---

## Appendix — provenance

Every number in this document was produced on this machine on 2026-09-22 and can be
re-run:

- Judgment inventory: survey of `src/agent_friday/services/`, with file and line
  citations retained in the session transcript.
- Recursion depth and the depth-2 harmful-content block: instrumented call counting
  against `content_policies.evaluate_content` in the repo venv.
- Laya's 39/40, 20/40 and 3.5 s: `tools/triage_laya.py` against
  `~/.friday/eval/triage_set.jsonl`.
- Empty corpus: `~/.friday/decisions.jsonl` absent; `approvals.json` one record.
- Installer tiers and sizes: `packaging/windows/requirements/*.txt`,
  `packaging/windows/lib/Deps.ps1`, `AgentFriday.spec`.
- Layer status: `services/privacy_layers.probe_layers`, which reports it honestly and
  should be the thing anyone quotes.
