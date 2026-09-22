# Avatar visual genome: a weekly, explained, permanent evolution of Friday's look

> **Status:** proposal (nothing built)
> **Last verified:** 2026-09-22
> **Implementation:** none yet. Builds on:
> - `index.html`, the holographic scene (`MOODS`, `EVOLUTION_PATH`, `HolographicShader`, `setEvolution`)
> - `routes/insights.py` (`/api/evolution`)
> - `services/introspection.py`
> - `services/memory_dreaming.py`
> - `services/model_router.py` (`_call_claude`)
> - `services/egress_gate.py` (`seal_outbound`)
> - `services/provenance.py`
> - `governance/proof_of_integrity.py`
> - `services/marketplace.py`
>
> **Written:** 2026-09-22

## The ask

The holographic scene should change from week to week in a way that
reflects what Friday actually did and learned.

- **Off by default.** It sits behind a Settings switch.
- **A frontier model proposes each change**, never a local model. The user
  sees which model did it and exactly what left the machine.
- **Every generation is kept for good.** Any generation can be browsed,
  restored and favourited. Nothing is ever pruned automatically.
- **Each generation is a portable, signed artifact.** It can be exported
  now and later listed on the federation market. Sharing is always an
  explicit user action.

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
  - The weekly dream-rsi pass is written up as a skill definition.
  - Nothing schedules it, and the idle gate it depends on
    (`services/idle_gate.py`) does not exist.
  - Its planned output per run is one history line: date, target, cost,
    minutes, and the verdict SHIPPED, HOLLOW or FAILED.
- **What does run.**
  - Nightly memory dreaming writes `dreams.db`: turns reviewed, topic
    keywords with counts, facts consolidated.
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
stored, so no model output can produce a jarring jump or an unrenderable
scene.

## 2. The evolution step: a frontier model under Friday's transparency rules

### Which model

- `avatar_evolution_model` is a new setting. It names one frontier cloud
  model, which the owner chooses.
- The step calls `model_router._call_claude(messages, system,
  model=<pinned>)` directly.
- It does **not** use `_generate_text`, which by design falls through every
  provider, local ones included.
- `_call_claude` raises when there is no key, when a gate blocks, or when
  the spend cap is hit. It never tries a local model, so that behaviour is
  the no-substitution guarantee.

### What leaves the machine

The input is built by a single function against an **allowlist schema**:
numbers, booleans and fixed enums only. Any free-text field fails
validation before the call is made, so the rule "no raw vault or memory
content" is structural, not a convention. A unit test pins the schema.

```json
{
  "schema": "friday.avatar.evolve/1",
  "week": "2026-W39",
  "genome": { "...current genome..." },
  "bounds": { "...the table above..." },
  "signals": {
    "knowledge": { "pages_added": 12, "pages_edited": 49,
                   "growth_by_community": [{"palette_index": 0, "pages": 31},
                                           {"palette_index": 1, "pages": 9}] },
    "calibration": { "epistemic_overall": 0.71, "delta": 0.04,
                     "weakest_dimension": "source_attribution" },
    "spine": { "sycophancy_index": 0.12, "pushback_rate": 0.18, "danger_zone": false },
    "maturity": 0.46,
    "dreaming": { "nights": 7, "turns_reviewed": 214, "facts_consolidated": 9, "topic_count": 23 },
    "self_change": { "ran": false, "shipped": 0, "hollow": 0, "failed": 0,
                     "targets": [] }
  }
}
```

**Never sent**, although each of these sits next to the numbers above:

- page titles and topic names (colour is sent as a palette index);
- `epistemic.lowest_samples` and `sycophancy.flagged_responses`, which
  quote Friday's replies;
- the weekly `reflection`, which is free model text;
- personality settings text;
- dreaming's `consolidated[].text`, which is verbatim user sentences;
- anything from the vault.

`self_change.targets`, once dream-rsi runs, is a fixed enum of subsystem
categories. It never carries a spec's text.

The payload still goes through `_call_claude`'s normal chain:

- spend guard;
- `egress_gate.seal_outbound`, which fails closed;
- `cost_meter`, under the attribution `avatar_evolution`;
- `attribution.record_generation`.

So the call shows up in the existing "left the machine" log
(`/api/privacy/left-the-machine`) and in the Costs panel like any other
cloud call.

### What comes back

The model returns JSON with three fields:

- `proposed_genome`;
- `rationale`: at most 280 characters, shown as the model's own words;
- `name`: a short title for this generation, such as "Tidewater Lattice".

The server:

- validates the schema;
- clamps every gene to its bounds;
- records both the raw proposal and the clamped result, so the user can see
  when the bounds overrode the model.

### Failure is visible and skips the week

Any of these ends the step:

- no key;
- the network is down;
- the egress gate blocks;
- the spend cap is hit;
- the model errors;
- the response is invalid after one reformat retry to the *same* model.

When that happens:

- a history entry of kind `skipped` is written, with the reason in plain
  words;
- a notice appears ("This week's evolution was skipped: …");
- the avatar stays as it is.

A **Try again** button re-runs the step against the same pinned model. No
fallback model is ever tried.

### Telling the user

- **First switch-on.** A consent screen shows the model name, a real
  example of the exact JSON that would be sent, the list of fields that
  are never sent, and a note that the cost is metered in the Costs panel.
- **Every generation's history entry** includes:
  - the model id;
  - a *What was sent* disclosure with the exact payload;
  - the model's rationale;
  - any clamping.
- **First load after a change.** A notice names the change and the model
  and offers [Why] [Undo].

### What drives it: dream-rsi, as it stands

The instruction is to use "an abstracted summary of the week's dream-rsi
output". Dream-rsi has no runner yet. The proposal is to treat the week's
automated self-review as the dream-rsi summary:

- nightly dreaming aggregates;
- the Sunday report's scores;
- dream-rsi verdict counts, once dream-rsi runs.

Until dream-rsi runs, `self_change.ran` is `false` and `facets` do not grow.
This is decision 2 below.

## 3. History is permanent

- **Where generations live.** `~/.friday/avatar/generations.jsonl`, append
  only, one line per generation or skipped week. Nothing in the code path
  deletes or rewrites a line. Size is on the order of 2–4 KB per week.
- **User state is kept separately.** Favourites, pins, custom names and the
  active generation live in `~/.friday/avatar/state.json`, so the history
  file itself is never edited.
- **Restore** sets the active pointer.
- **Evolving from a restored generation forks the lineage.** The next
  generation's parent is the restored one, so history is a tree, not a
  line, and no branch is lost.
- **Thumbnails.** One PNG per generation, rendered locally from the genome
  at creation time, is stored beside the history. The genome renders
  deterministically, so a missing thumbnail can always be regenerated.
- **No automatic pruning, ever.** Whether the user may hide or delete a
  generation is decision 5.

## 4. The portable, signed avatar card

Every generation can be exported as a self-contained file,
`<name>.fridayavatar.json`:

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
    "week": "2026-W39",
    "created_at": "2026-09-27T09:05:12Z",
    "model": { "provider": "anthropic", "id": "<pinned model id>" },
    "rationale": "…",
    "input_digest": "sha256:…",
    "parent": "sha256:<parent card's content_hash>",
    "lineage_depth": 14,
    "generator": "agent-friday/<version>"
  },
  "content_hash": "sha256:…",
  "signature": { "alg": "ed25519", "pubkey": "…", "value": "…" }
}
```

- **`render` is self-contained.** A viewer can draw the avatar without
  knowing Friday's genome-to-scene mapping, and `genome` still lets another
  Friday keep evolving it.
- **Hashing and signing reuse the existing manifest conventions.**
  `content_hash` is `"sha256:"+hex` over canonical JSON (sorted keys,
  compact separators, the signature field removed). That is the
  serialization of `provenance._deterministic` and `verify_manifest`. The
  signature is the install's Ed25519 identity, via `IntegrityEngine`.
  Listings currently use a *different* serialization; see §6.
- **The card carries `input_digest`, never the input.** The weekly metrics
  that went to the model stay private even when the avatar is shared. The
  share screen shows the full card, and lets the user strip `rationale`
  (which can mention those metrics) before export.
- **`parent` makes lineage verifiable across installs.** A card imported
  from someone else and then evolved records their card's hash as its
  parent.

## 5. The sharing hook (built with the feature; the market side is not)

**Share** on a generation is always an explicit click.

1. The user clicks Share and sees a preview of the exact card and where it
   will go.
2. The user confirms.
3. The card is written to a file. This works today.
4. `share_intent(card)` runs the gate chain the publisher already uses:
   - moderation;
   - egress classification, where the card must classify PUBLIC;
   - `approvals.gate_action`, since publishing is outward.
5. If the market is not available, the intent stops with "Exported. The
   market isn't available yet". Nothing is sent.

- **Nothing is published by default.**
- **No background process ever shares.**
- **No setting turns on automatic sharing.**
- **The market button stays hidden until federation ships.** V6 says the
  deferred products must not be surfaced. Export to file is not a
  federation product and can ship with the feature.

**Import.** Opening someone else's `.fridayavatar.json`:

1. verifies the signature;
2. validates the schema;
3. clamps every render value to the same bounds, because a card is data and
   is never trusted;
4. shows a preview.

Adopting it adds a new generation to your history, with their card as its
parent.

## 6. What the market side would need (not built; federation is deferred)

Federation, the marketplace and the ψ/η economy have code and registered
routes, but V6 stages them as future work. "The marketplace does not ship
in V6", and phases "must not surface the deferred products". Listing avatar
cards would need:

1. **A listing type.**
   - Mapping: `media_type = "avatar-genome"`, `asset_id` =
     `content_credential_hash` = the card's `content_hash`, `creator_pubkey`
     from the card, `preview_url` = the thumbnail, price 0, and a licence
     choice.
   - `content_credential_hash` is currently always written empty.
2. **Receiving listings at all.** `CONTENT_OFFER` has no handler in
   `routes/federation.py`; it falls through to a generic reply, so offered
   listings are never stored.
3. **Verifying what arrives.**
   - Nothing verifies a listing's signature.
   - Peer-card verification logs a bad or missing signature and accepts
     the card anyway.
   - Listings and manifests are signed over two different JSON
     serializations; one canonical form is needed.
4. **A rating model, which does not exist.**
   - Positrons (ψ) are currency. There is a per-like mint amount
     (`PSI_LIKE`), but no rating record.
   - Negatrons (η) are metered cost and penalty, not a downvote.
   - `/api/economy/earn` and `/api/economy/transfer` accept caller-supplied
     identities with no caps, so ratings built on them could be forged or
     inflated.
   - Ratings would need to be signed, one per identity per card,
     rate-limited, and stored beside the listing.
   - `workspace-ecosystem.md` says "positrons are not a price" and that
     adjudication is "never a vote". A negatron downvote contradicts that
     and needs a spec decision.
5. **Moderation of previews.** Card thumbnails need the same moderation
   floor as any other published image.

## Decisions for the owner

1. **Which frontier model** is pinned for the weekly step?
2. **Dream-rsi.** Proceed now with the week's self-review aggregates as the
   "dream-rsi summary", adding facets when dream-rsi runs? Or wait for a
   dream-rsi runner?
3. **Clock and pin.** With evolution on, should growth replace the
   four-day clock, and what happens to the current pinned structure?
4. **Moving backwards.** If calibration falls or sycophancy rises, may the
   look regress?
5. **"Never lose one" versus user choice.** May the user hide a generation
   (kept on disk, hidden from the timeline), delete one, or neither?
6. **Ratings.** When the market un-defers, should positrons be a like (a ψ
   tip to the creator) and negatrons a downvote? The current specs define
   both differently.
7. **Licence.** What is the default licence for a shared card, and should
   the rationale be included when sharing by default?

## Build outline (after the decisions)

1. Genome, bounds, the allowlist input schema and permanent history, with
   unit tests pinning the schema (no free text can pass), clamping and
   append-only behaviour.
2. The cloud step through `_call_claude` with a pinned model, the
   skipped-week path, and the cost and egress entries.
3. Settings: the switch, the consent screen, the history timeline with
   *What was sent*, restore, favourite and pin.
4. Scene integration: baseline modifiers, particle count, coherence,
   structure, facets.
5. The card: export, import, verify and the lineage fork.
6. The share-intent hook, stopping before the market.
7. Market items 1–5 of §6, once federation is un-deferred.
