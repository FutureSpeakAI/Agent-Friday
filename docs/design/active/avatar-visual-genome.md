# Avatar visual genome: a weekly, explained, reversible evolution of Friday's look

> **Status:** proposal (nothing built)
> **Last verified:** 2026-09-22
> **Implementation:** none yet. Builds on `index.html` (holographic scene: `MOODS`, `EVOLUTION_PATH`, `HolographicShader`, `setEvolution`), `routes/insights.py` (`/api/evolution`), `services/introspection.py` (weekly self-improvement report), `services/memory_dreaming.py`
> **Written:** 2026-09-22

## The ask

The holographic scene should be able to change slowly from week to week in a
way that reflects what Friday actually did and learned, so the change means
something. It is off by default and has a Settings switch. The user always
knows what changed and why, can browse every past look, and can roll back.

## What exists today

- **The scene.** `index.html` renders one Three.js scene. Its look is set by
  `MOODS` (14 moods, each a base colour, accent colour, rotation speed, bloom
  strength, particle speed and film grain), `EVOLUTION_PATH` (13 geometric
  structures, from GENESIS LATTICE to GIGA EARTH), an 800-particle field and
  `HolographicShader` (chromatic offset, grain, scanlines). Moods change
  moment to moment with what Friday is doing. Motion follows mood only while
  Friday is speaking.
- **An evolution that is only a clock.** `/api/evolution` moves the
  structure one step along `EVOLUTION_PATH` every four days since first
  launch, unless the user has pinned one with `preferred_scene_index`. It
  reflects elapsed time, not anything Friday did.
- **Dream-rsi is a spec, not a job.** The weekly "dream rsi" pass is written
  up as a skill definition in the user's skill folder. Nothing schedules it,
  and the idle gate it depends on (`services/idle_gate.py`) does not exist.
  Designing the avatar around its output would design around something that
  has never run.
- **What does run.**
  - Memory dreaming runs daily at 03:00 (`services/memory_dreaming.py`). It
    writes one row per day to `dreams.db`: turns reviewed, topics, facts
    consolidated.
  - The weekly self-improvement report runs Sundays at 09:00
    (`services/introspection.py`). It writes
    `self_improvement/<YYYY-Www>.json` with the epistemic score and its five
    dimensions, the sycophancy index and pushback rate, a personality
    snapshot, and the focus areas.
  - The knowledge graph records each page's last edit (`updated`), which
    gives weekly growth per community.

## Proposal

### The genome

The genome is a small JSON object of bounded visual parameters. Moods still
drive moment-to-moment state; the genome moves the baseline the moods sit on.

| Gene | Range | Moves at most, per week |
|---|---|---|
| `hue_shift` (palette rotation) | −24° to +24° | 4° |
| `saturation` | 0.80 to 1.10 | 0.03 |
| `particle_count` | 600 to 1400 | 60 |
| `coherence` (ordered orbits vs free drift) | 0 to 1 | 0.08 |
| `structure` (index into `EVOLUTION_PATH`) | 0 to 12 | 1 step |
| `bloom_baseline` (multiplier on the mood's bloom) | 0.8 to 1.2 | 0.04 |
| `grain_baseline` (multiplier) | 0.6 to 1.2 | 0.06 |
| `facets` (small rings on the structure, one per shipped self-change) | 0 to 24 | +1 per shipped change |

The per-week limits keep any single week's change subtle. Several weeks
together are noticeable.

### What drives each gene

Only counts and scores are used, never content. Every change carries a
one-line reason built from those numbers.

- **Knowledge growth → `particle_count`.** Pages added or edited this week.
  *"Particles 820 → 880: 61 wiki pages new or edited this week."*
- **Where the learning went → `hue_shift`.** The palette drifts a few degrees
  toward the colour of the knowledge-graph community that grew most. The
  avatar picks up a tint of what the user has been working on.
- **Calibration → `coherence`.** Particle orbits become more ordered as the
  weekly epistemic score rises, and loosen if it falls.
- **Spine → `grain_baseline` and `saturation`.** A healthy pushback rate and a
  low sycophancy index give a crisper image. A "danger zone" week softens it.
- **Maturity → `structure`.** The structure advances one step when the
  personality maturity score crosses the next threshold. This replaces the
  four-day clock.
- **Shipped self-changes → `facets`.** Once dream-rsi actually runs, each
  SHIPPED verdict adds one facet. HOLLOW and FAILED verdicts add nothing.
  Until then, `facets` stays at zero.

The genome is computed deterministically: the same inputs give the same
genome. There is no randomness, so what the user sees can always be
explained.

### Mechanics

- **When it runs.** A scheduler job runs right after the Sunday report. It
  reads that week's report, the week's `dreams.db` rows and the graph's
  weekly growth, then appends one entry.
- **Where it is stored.** `~/.friday/avatar/genome_history.jsonl`, append
  only. Each entry holds the week id, the full genome, the deltas with their
  reasons, and a digest of the inputs. A separate pointer names the active
  week, so a rollback never deletes history.
- **API.**
  - `GET /api/avatar/genome` returns the current genome plus history.
  - `POST /api/avatar/genome/activate {week}` rolls back or forward.
  - `POST /api/avatar/genome/freeze` stops evolution but keeps the current look.
- **Settings → Appearance.**
  - A switch, *Let Friday's look evolve week to week*, defaulting to OFF. It
    is a new `DEFAULT_SETTINGS` key; `check_settings_readers.py` requires the
    entry and a reader.
  - Beneath it, a timeline with one row per week: a swatch of the palette
    and particle density, the week's reasons, a *Preview* button that plays
    the scene with that genome for ten seconds, and *Use this look*.
- **Telling the user.** On the first load after a change, a small notice
  reads: *"Friday's look changed this week: more particles (61 pages
  learned), slightly more ordered (calibration up 4 points). [Why] [Undo]"*.
  Nothing changes silently.
- **Scene integration.** The scene reads the genome once at boot and when it
  is activated. It applies `hue_shift`, `saturation` and the baselines on
  top of the current mood's values, sizes the particle field to
  `particle_count` (the buffers are rebuilt only on change), blends particle
  motion between orbit and drift by `coherence`, and calls the existing
  `setEvolution()` for `structure`.

### Privacy

The genome and its reasons contain counts, scores and colour values only. No
conversation text, page titles or topic names are stored in it or shown in
its reasons. It never leaves the machine.

## Decisions for the owner

1. **Signals.** Which signals may drive the look? Proposed: knowledge growth,
   calibration and pushback, but not mood (mood already moves the scene
   minute to minute).
2. **Dream-rsi timing.** Start now from the weekly report, which runs, and
   add facets when dream-rsi runs? Or wait until dream-rsi exists?
3. **The four-day clock and the current pin.** With evolution on, the
   structure advances on maturity instead of the calendar. What should
   happen to an existing pinned structure?
4. **What "off" means.** Return to the original look, or freeze at the
   current evolved look?
5. **Moving backwards.** If calibration falls or sycophancy rises, should the
   look visibly regress (honest, but can feel like a penalty), or only ever
   move forward?
6. **Pace.** Are the per-week limits above subtle enough, or too subtle?

## Build outline (after the decisions)

1. Genome compute and history store, with unit tests pinning the mapping and
   the bounds.
2. API and the Settings switch with the history timeline.
3. Scene integration: baseline modifiers, particle count, coherence and
   structure.
4. The change notice.
5. Facets from dream-rsi verdicts, once dream-rsi runs.
