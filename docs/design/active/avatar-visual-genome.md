# Avatar visual genome: an explained, credited, permanent evolution of Friday's look

> **Status:** proposal (nothing built). Owner answered the model choice,
> nightly dream-rsi and user control of history on 2026-09-22, and
> **DECIDED** the rating model (§7) the same day.
> **Last verified:** 2026-09-22
> **Implementation:** none yet. Builds on:
> - `index.html`, the holographic scene (`MOODS`, `EVOLUTION_PATH`,
>   `HolographicShader`, `setEvolution`)
> - `routes/insights.py` (`/api/evolution`)
> - `services/introspection.py`
> - `services/memory_dreaming.py`
> - `services/model_router.py` (`_call_claude`, `_call_openai`)
> - `services/egress_gate.py` (`seal_outbound`)
> - `services/scheduler.py`
> - `services/arbiter.py`
> - `services/provenance.py`
> - `governance/proof_of_integrity.py`
> - `services/marketplace.py`
>
> **Written:** 2026-09-22

## The ask

The holographic scene changes over time in a way that reflects what Friday
actually did and learned.

- **Off by default.** It sits behind a Settings switch.
- **A frontier model makes each change, never a local one.**
  - Friday chooses which frontier model, and may vary it from one
    generation to the next.
  - Every generation is credited to the model that made it. The credit is a
    feature, shown prominently in the history and on shared cards, because
    part of the fun is seeing which model made which artistic contribution.
- **Full transparency.** The user sees exactly what left the machine.
- **The user's preference is paramount.** Every generation is kept until the
  user decides otherwise: nothing is pruned automatically, and the user can
  hide or delete any look.
- **Each generation is a portable, signed artifact.** It can be exported now
  and later listed on the federation market. Sharing is always an explicit
  user action.
- **Dream-rsi becomes a nightly job** that runs during inactive late-night
  hours (§5). The avatar draws on what those nights produce.

## What exists today

- **The scene.**
  - `MOODS` defines 14 moods: base and accent colour, rotation, bloom,
    particle speed and grain.
  - `EVOLUTION_PATH` lists 13 structures, from GENESIS LATTICE to GIGA EARTH.
  - There is an 800-particle field and `HolographicShader` (chromatic
    offset, grain, scanlines).
  - Moods change moment to moment with what Friday is doing.
- **Evolution by clock.** `/api/evolution` advances the structure one step
  every four days since first launch, unless `preferred_scene_index` pins
  one. It reflects elapsed time, not anything Friday did.
- **Dream-rsi is a spec, not a job.**
  - The dream-rsi pass is written up as a skill definition (weekly,
    idle-gated).
  - Its idle gate has a contract but no module: `services/idle_gate.py`
    does not exist.
  - Nothing schedules it.
  - Each run's planned output is one history line: date, target, cost,
    minutes, and the verdict SHIPPED, HOLLOW or FAILED.
- **What does run.**
  - Nightly memory dreaming (03:00) writes `dreams.db`.
  - The Sunday self-improvement report writes
    `self_improvement/<week>.json`: epistemic scores, the sycophancy index,
    pushback rate and focus areas.
  - The knowledge graph records each page's last edit (`updated`).

## 1. The genome

The genome is a small JSON object of bounded visual parameters. Moods still
drive moment-to-moment state; the genome moves the baseline the moods sit
on.

| Gene | Range | Moves at most, per generation |
|---|---|---|
| `hue_shift` (palette rotation) | −24° to +24° | 4° |
| `saturation` | 0.80 to 1.10 | 0.03 |
| `particle_count` | 600 to 1400 | 60 |
| `coherence` (ordered orbits vs free drift) | 0 to 1 | 0.08 |
| `structure` (index into `EVOLUTION_PATH`) | 0 to 12 | 1 step |
| `bloom_baseline` (multiplier on the mood's bloom) | 0.8 to 1.2 | 0.04 |
| `grain_baseline` (multiplier) | 0.6 to 1.2 | 0.06 |
| `facets` (rings on the structure, one per shipped self-change) | 0 to 24 | +1 per SHIPPED dream-rsi verdict |

The model proposes and the bounds decide. The server clamps every proposed
value to its range and to the per-generation step before anything is
stored.

## 2. The evolution step: a frontier model, chosen by Friday and credited

### Which model, and how Friday chooses

1. **The candidates** are every frontier cloud model the user has a working
   key for (Anthropic, OpenAI-compatible providers, and so on). Local
   models are never candidates.
2. **Friday picks one before the call.**
   - The pick favours variety: it prefers a model that hasn't made one of
     the last few generations.
   - Friday records a one-line reason, for example: *"Chose <model>: it
     hasn't shaped Friday since W31."*
3. **The pick is fixed for that call.** The call goes directly to that
   provider's function with the model pinned:
   - `_call_claude(..., model=...)`, or
   - `_call_openai(..., provider=..., model=..., fallback_models=None)`.
   - Never `_generate_text`, which falls through every provider, local
     ones included.
   - Server-side fallback lists are disabled.
4. **Credit comes from the provider's answer.** The model credited is the
   one the provider *reports* it used, not the one requested. If they
   differ (a router substituting silently), the generation shows both, with
   a warning.

### What leaves the machine

The input is built by a single function against an **allowlist schema**:
numbers, booleans and fixed enums only. Any free-text field fails
validation before the call is made. A unit test pins the schema.

```json
{
  "schema": "friday.avatar.evolve/1",
  "period": "2026-W39",
  "genome": { "...current genome..." },
  "bounds": { "...the table above..." },
  "signals": {
    "knowledge": { "pages_added": 12, "pages_edited": 49,
                   "growth_by_community": [{"palette_index": 0, "pages": 31}] },
    "calibration": { "epistemic_overall": 0.71, "delta": 0.04,
                     "weakest_dimension": "source_attribution" },
    "spine": { "sycophancy_index": 0.12, "pushback_rate": 0.18, "danger_zone": false },
    "maturity": 0.46,
    "nights": { "dreamed": 7, "turns_reviewed": 214, "facts_consolidated": 9, "topic_count": 23,
                "rsi_runs": 6, "rsi_shipped": 2, "rsi_hollow": 1, "rsi_failed": 1,
                "rsi_skipped": 1, "rsi_targets": ["retrieval", "voice"] }
  }
}
```

**Never sent**, although each of these sits next to the numbers above:

- page titles and topic names (colour is sent as a palette index);
- quoted replies (`lowest_samples`, `flagged_responses`);
- the weekly `reflection` text;
- personality settings text;
- dreaming's verbatim `consolidated[].text`;
- dream-rsi spec or diff text (`rsi_targets` is a fixed enum of subsystem
  names);
- anything from the vault.

The call goes through the normal chain:

- spend guard;
- `seal_outbound`, which fails closed;
- `cost_meter`, under the attribution `avatar_evolution`;
- `attribution.record_generation`.

So it appears in "left the machine" and in the Costs panel like any other
cloud call.

### What comes back

The model returns JSON with three fields:

- `proposed_genome`;
- `rationale`: at most 280 characters, shown as the model's own words;
- `name`: a short title for the generation.

The server validates the schema, clamps every gene, and records both the
raw proposal and the clamped result.

### Failure is visible and skips the period

These end the step:

- no key;
- the network is down;
- the egress gate blocks;
- the spend cap is hit;
- the model errors;
- the response is invalid after one reformat retry to the *same* model.

When that happens:

- a history entry of kind `skipped` is written, naming the model that was
  tried and the reason in plain words;
- a notice appears;
- the avatar stays as it is.

Friday does not hop to another model on its own. **Try again** lets Friday
pick again, and that new pick is recorded and shown like any other.

### Telling the user

- **First switch-on.** A consent screen shows the candidate models, a real
  example of the exact JSON, the list of fields that are never sent, and a
  note that cost is metered in the Costs panel.
- **Every generation's history entry** includes:
  - the model credit, prominently;
  - Friday's reason for picking that model;
  - a *What was sent* disclosure with the exact payload;
  - the model's rationale;
  - any clamping.
- **The timeline can filter by model,** so each model's contributions can
  be viewed side by side.

## 3. How often the avatar evolves: weekly, from the accumulated nights (recommended)

Dream-rsi runs nightly (§5). The avatar could evolve every night, or once a
week from the seven nights together. **The recommendation is weekly**:

- **It stays legible.** The per-step bounds are small, so a nightly change
  would be too small to notice, and loosening the bounds to make it
  noticeable would make the look jumpy. A week of accumulated change is
  visible without being jarring.
- **One trip off the machine per week, not seven.** The payload is tiny,
  but seven cloud calls and seven egress entries a week is noise in the
  transparency log.
- **Each look means something.** Permanent history is the point: about 52
  looks a year, each summarising a real week, is a history worth browsing.
  365 near-identical nightly looks is not.
- **Model credit stays interesting.** One model per week makes "which model
  made this" a meaningful attribution. Nightly credits would blur together.

- **When.** Weekly evolution runs Sunday after the self-improvement report
  (09:00), using that report plus the week's nightly aggregates.
- **Evolve now** in Settings runs one extra generation on demand. It is
  credited and recorded the same way.
- **Nightly is an option.** If the owner prefers it, the same pipeline runs
  nightly after dream-rsi, with `period` set to a date. Nothing else
  changes.

## 4. History: kept until the user decides otherwise

- **Where generations live.** `~/.friday/avatar/generations.jsonl`, append
  only. Friday never removes or rewrites a line on its own, and there is no
  pruning, size cap or retention policy.
- **User state is kept separately.** Favourites, pins, custom names, hidden
  flags and the active generation live in `~/.friday/avatar/state.json`.
- **Restore** sets the active pointer. Evolving from a restored look forks
  the lineage, so history is a tree.
- **Hide** is instant and reversible. A hidden look drops out of the
  timeline, and "Show hidden" brings it back.
- **Delete** is always a deliberate, user-only action:
  1. A confirmation names the look and its model credit and says the
     deletion is permanent after the undo window.
  2. The look is tombstoned: moved to `~/.friday/avatar/trash/` with the
     deletion time. It stays restorable from "Recently deleted" for 30
     days.
  3. After 30 days, the tombstoned lines, thumbnail and card are removed.
     This is the only code path that removes history, and it only acts on
     looks the user deleted.
- **What deletion does to lineage.** Deleting a look that has descendants
  keeps the descendants. Their `parent` hash still names the deleted look,
  and the timeline shows "parent deleted". Deleting the active look first
  asks which look to switch to.
- **Deletion is local only.** It never reaches cards already exported or
  shared. The confirmation says so if the look was ever exported.
- **Thumbnails.** One PNG per generation, rendered locally at creation and
  regenerable from the genome.

## 5. Nightly dream-rsi during inactive hours (design; scheduling it is a separate build item)

### When it may run

The night already holds these jobs (default times, from `scheduler.py`):

| Time | Job |
|---|---|
| 23:30 | session summary |
| 03:00 | memory dreaming (local, regex only, no model) |
| 03:30 | knowledge-graph reindex |
| 04:00 | context-log retention (daily); learning epoch (weekly) |
| 06:00 | repo sync |
| 06:30 | *(free — The Friday Edition ran here until 2026-09-24)* |
| 06:45 | brutalist.report scrape |
| 07:00 | **Front Page morning slot** (uses the local seat) |

Proposed window:

- **Starts** are allowed from **00:30 to 05:15** local time.
- **No new step starts after 05:30**, and a watchdog stops the run by
  **06:15**, so the GPU and the seat are clear well before the 06:30–07:00
  jobs.
- **Around 03:00–03:45,** the run holds new *GPU* steps and lets memory
  dreaming and the reindex go first. Cloud-only steps may continue.
- The window's hours are settings, so a night owl can move them.

### The gate (extends the existing idle-gate contract; opens only when ALL hold)

1. **`rsi_paused` is false.** It is checked first, outranks everything, and
   is shown in Settings. A missing probe shuts the gate.
2. **The user has been inactive for at least 60 minutes.** Evidence:
   - Windows input idle, via `GetLastInputInfo`;
   - no chat or voice turn in that time;
   - no open voice session.
3. **No GPU work is running.**
   - No lease is held in `services/arbiter.py`, and **no foreign hold is
     declared**. This is how long jobs such as model training and
     evaluations announce themselves.
   - The residency arbiter reports no heavy or image lease.
   - The local seat has no running task.
4. **The GPU has at least 11 GB of VRAM free,** measured on the live card,
   not assumed.
5. **Time.** It is inside the window, and at least 20 hours have passed
   since the last completed run.
6. **No scheduled GPU job is due** within the run's time budget. Friday
   checks its own schedule for the next GPU-using job (the Front Page
   slots, daily creation) and does not start if that job would overlap.

Every probe fails closed: an error, a `None` or a missing key shuts the
gate, with the reason `probe-fault:<name>`.

### While it runs

- **Preemption: finish the step, don't start the next.** If the user comes
  back, a lease is requested, or `rsi_paused` flips, the current step
  completes under its watchdog and the run re-queues for the next night.
- **It holds an arbiter lease** for the whole run, so a training job or a
  user task that wants the GPU sees the GPU as taken rather than colliding
  with it.

### Failing visibly

Every night produces exactly one outcome line in the dream-rsi history:

- ran, with its verdict;
- **skipped**, with the gate reason (for example, "user active at 02:10",
  "foreign GPU hold: training", "VRAM 7.2 GB < 11 GB");
- **failed**, with the error.

The morning briefing carries one plain line about last night, including
skips and cost. A silent night is itself a defect: the supervisor raises it
if no outcome line exists by 07:00.

### What scheduling it needs (separate build item)

1. **`services/idle_gate.py`** to satisfy the existing red-first contract
   test, extended with:
   - the arbiter foreign-hold probe;
   - the "next scheduled GPU job" probe;
   - the window hours.
2. **The `rsi_paused` setting.** A `DEFAULT_SETTINGS` entry, a reader (the
   settings guard requires both) and a visible switch.
3. **A scheduler job** (`dream_rsi_nightly`) that polls the gate every few
   minutes inside the window, starts at most one run per night and writes
   the outcome line.
4. **The run itself.** Wire the dream-rsi workflow (spec → build → verify)
   to run unattended under the finish-the-step preemption policy.
5. **The morning briefing line** and the 07:00 missing-outcome check.
6. **Tests** for: every probe failing closed, each skip reason, preemption
   between steps, the 05:30 start cut-off and the 06:15 watchdog.

## 6. The portable, signed avatar card

Every generation can be exported as `<name>.fridayavatar.json`:

```json
{
  "format": "friday.avatar/1",
  "genome": { "hue_shift": 6, "saturation": 0.97, "particle_count": 940, "coherence": 0.58,
              "structure": 8, "bloom_baseline": 1.04, "grain_baseline": 0.82, "facets": 2 },
  "render": { "engine": "friday-holo/1", "structure_id": "MOBIUS",
              "palette": ["#1e54c7", "#5fa8ff"], "particles": 940,
              "shader": { "chromatic": 0.003, "grain": 0.033 } },
  "provenance": {
    "name": "Tidewater Lattice",
    "made_by": { "provider": "anthropic", "model": "<model the provider reported>",
                 "requested": "<model Friday picked>", "why_this_model": "…" },
    "period": "2026-W39",
    "created_at": "2026-09-27T09:05:12Z",
    "rationale": "…",
    "input_digest": "sha256:…",
    "parent": "sha256:<parent card's content_hash>",
    "lineage_depth": 14,
    "lineage_models": ["<model>", "<model>", "…"],
    "generator": "agent-friday/<version>"
  },
  "content_hash": "sha256:…",
  "signature": { "alg": "ed25519", "pubkey": "…", "value": "…" }
}
```

- **Model credit is part of the signed content,** so it can't be altered
  without breaking the signature. `lineage_models` lists the models along
  the card's ancestry, so a card shows which models shaped it over time.
  A card viewer shows `made_by` as the headline credit.
- **Hashing and signing reuse the manifest conventions:** canonical JSON
  via `provenance._deterministic`, and the Ed25519 identity via
  `IntegrityEngine`.
- **The card carries only a digest of the input,** never the input itself,
  so the weekly metrics stay private. The share screen lets the user strip
  `rationale`.
- **Import** verifies the signature, validates the schema, and clamps
  render values (a card is untrusted data). Adopting an imported card
  records it as the parent.

**Sharing hook** (built with the feature):

1. Share shows a preview of the exact card.
2. The user confirms explicitly.
3. The card is exported to a file.
4. `share_intent` runs the publisher's gate chain: moderation, egress
   classification (the card must be PUBLIC), then `approvals.gate_action`.
5. It stops with "Exported. The market isn't available yet".

Nothing is published by default. The market button stays hidden while
federation is deferred (V6 §1.1).

## 7. The market side and ratings (not built; federation is deferred)

**Plumbing the market lacks:**

1. **A listing type.** `media_type = "avatar-genome"`, with
   `content_credential_hash` set to the card's `content_hash` (it is always
   empty today).
2. **A handler for offered listings.** `CONTENT_OFFER` has none, so listings
   offered by peers are never stored.
3. **Verifying what arrives.** Nothing verifies a listing's signature, and
   peer-card verification accepts failures anyway. Listings and manifests
   use two different serializations and need one canonical form.
4. **Moderating preview images.**

### Ratings: DECIDED (owner, 2026-09-22)

Today ψ is currency, η is cost or penalty, and there is no rating record at
all. That changes as follows.

**Negatrons (η) are the measured cost of adopting a module.**

- What is measured: compute, VRAM, disk, tokens per use, and permissions
  requested.
- The **installer** computes it on the adopting machine and signs the
  measurement. The publisher never supplies it, so it cannot be faked.
- It is **not votable.** Nobody can add or remove negatrons by opinion.
- A negatron is a price tag, not a punishment. For an avatar card it is
  small but real: the particle count's GPU cost, plus disk.

**Positrons (ψ) are proven value.** They are **not a like button.**

- **Retained installs:** a module still active N days after adoption,
  attested by the adopter's signed heartbeat.
- **Signed endorsements:** one per identity per module, from an identity
  with standing.
- Never self-minted, never minted from raw engagement counts, and never
  from a caller-supplied identity.

**Negative experiences are a separate channel.**

- A negative experience is a signed flag with a stated reason (a fixed list
  plus optional text).
- One flag per person per module.
- Weighted by the flagger's standing, using the trust-times-spam-penalty
  weighting defederation already uses.
- Flags are never converted into negatrons, and negatrons are never read
  as flags.

**How market cards show it.** Every card shows **value versus cost**:

- positrons, broken down into retained installs and endorsements;
- negatrons, as the measured adoption cost itemised by resource;
- the flags **alongside**, with their reasons and weighted count, kept
  separate from both numbers.

There is no single blended score.

This keeps two positions the existing specs already take: positrons are not
a price, and adjudication is never a plain vote.

### Follow-up: existing specs and code that define ψ/η differently (not changed in this pass)

**Code:**

1. **`services/economy.py` module docstring (lines 1-25) and constants.**
   - ψ is "earned by creating content, receiving likes/shares, completing
     tasks, early adopter bonus".
   - η is "spent on API calls, purchases, bandwidth, minted by system for
     violations".
   - `PSI_CREATE_CONTENT`, `PSI_LIKE` and the genesis bonus mint ψ from
     activity, and `mint_negatron` makes η a penalty. All of this contradicts
     "ψ = proven value, not a like button" and "η = measured adoption cost,
     not votable".
2. **`routes/federation.py` `/api/economy/earn` and `/api/economy/transfer`**
   (about lines 442-507).
   - They accept any caller-supplied `agent_id`/`from_agent`, and earning
     has no caps, so anyone can mint ψ for any identity.
   - They must become signed, identity-bound and capped, or be removed from
     the public surface.
3. **`services/budget_enforcer.py` and `services/orchestrator.py`
   (`budget_mψ`).** Task budgets are denominated in milli-Positrons, so ψ is
   spent to run work. Under the decision, what work costs is η; budgets need
   their own unit (USD or η).
4. **`services/platforms/federation_pub.py`**, and the pricing UI in
   `index.html` near lines 11720 and 11823 ("milli-Positrons"), let a piece
   be *priced* in positrons. That contradicts both "positrons are not a
   price" and the decision.
5. **The `index.html` Settings copy near line 37601:** "earn by contributing
   compute, spend on orchestrated tasks. Q-score = reputation charge."
6. **Q = Σψ − Ση** (`economy.py`, `get_leaderboard`). With η as adoption
   cost, subtracting it from value no longer means "reputation". The
   leaderboard needs redefining or retiring.

**Specs and docs:**

7. **`docs/design/implemented/content-pipeline-spec.md`**
   - §8.7 "Engagement → Positrons": ψ from likes and shares crossing
     thresholds.
   - Decision D10.
   - The `PSI_CREATE_CONTENT`/`PSI_LIKE` findings.

   All of these are the like-button model the decision rejects. The
   engagement collector that writes `psi_awards` would stop minting ψ.
8. **`docs/design/active/workspace-ecosystem.md` §4.6.** Mostly aligned: it
   already rewards "sustained installed use by distinct machines" and says
   positrons are not a price. Two reconciliations are needed:
   - it meters η as the *cost to run* (brokered consumption through
     `cost_meter`), where the decision measures the *cost to adopt*
     (installer-measured compute, VRAM, disk, tokens per use, permissions);
   - it adopts Q as "contribution minus consumption".
9. **`src/agent_friday/SELF.md`** ("Earn Positrons (ψ) from engagement")
   and **`src/agent_friday/VOICE_DEMO.md`** ("engagement even earns
   Positrons"). Friday's self-description would be wrong once the economy
   changes.
10. **`docs/design/active/v6-wholeness-spec.md` (line 49)** describes the
    "Positron / compute-provider" stack as in-scope infrastructure. Its
    wording should follow item 3 once budgets get their own unit.

## Decisions

**Answered (2026-09-22):**

1. **Model.** Friday's choice, and it may vary. Credit is prominent (§2,
   §6).
2. **Dream-rsi.** Nightly, during inactive late-night hours (§5).
3. **User preference is paramount.** Looks can be hidden or deleted.
   Deletion is deliberate, confirmable, with a 30-day undo, and never
   automatic (§4).
4. **Ratings (DECIDED).** Negatrons are the installer-measured adoption
   cost; positrons are proven value; negative experiences are separate
   signed, standing-weighted flags; cards show value against cost with
   flags alongside (§7).

**Still open:**

5. **Nightly or weekly avatar evolution.** Recommended: weekly (§3).
6. **Clock and pin.** Should growth replace the four-day clock, and what
   happens to the currently pinned structure?
7. **Moving backwards.** If calibration falls or sycophancy rises, may the
   look regress?
8. **Sharing defaults.** The default licence for a shared card, and whether
   the rationale is included by default.
9. **Night window.** Are 00:30–05:15 for starts and 06:15 for the hard stop
   right?

## Build outline (after the open decisions)

1. Genome, bounds, the allowlist input schema, and history with hide,
   delete, tombstone and 30-day purge. Tests pin that the schema admits no
   free text, the clamping, and that only user-deleted looks are ever
   removed.
2. The cloud step: Friday's model pick with its reason, the direct
   pinned-provider call, credit from the reported model, the skipped-period
   path, and the cost and egress entries.
3. Settings: the switch, the consent screen, and the timeline with model
   credit, a model filter, *What was sent*, restore, favourite, pin, hide,
   delete and "Recently deleted".
4. Scene integration.
5. The card: export, import, verify, lineage with `lineage_models`.
6. The share-intent hook, stopping before the market.
7. **Separate item:** nightly dream-rsi scheduling (§5).
8. **Later, after federation un-defers:** the market plumbing, the decided ratings, and the §7 reconciliation list
   (§7).
