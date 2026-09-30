# Avatar visual genome: Friday's look evolves weekly, on every structure, reversibly

> **Status:** proposed (spec only; nothing in this document is built). This is
> the converged design. It replaces the 2026-09-22 version of this file and the
> uncommitted `docs/design/evolve-genome.md` (2026-09-28). That draft is
> preserved verbatim in Appendix B so it is in git.
> **Last verified:** 2026-09-29 against main `41ef21fd`
> **Implementation:** none yet. Builds on:
> - `index.html`: `MOODS`, `EVOLUTION_PATH`, `buildAllStructures`,
>   `updateGroupColors`, `animate`, `HolographicShader`, `setEvolution`,
>   `window.fridayVibe`
> - `routes/insights.py` (`/api/evolution`)
> - `services/scheduler.py`
> - `services/provenance.py` (`_deterministic`, `sign_manifest`)
> - `governance/proof_of_integrity.py` (`IntegrityEngine.sign_payload`)
> - `services/egress_gate.py` (`seal_outbound`)
> - `services/local_call.py`
> - `services/off_record.py`
> - `services/residency_arbiter.py` (`exclusive_lease`)
> - `services/model_router.py` (`_call_claude`, `_call_openai`)
>
> **Supersedes:** the 2026-09-22 text of this file, and `evolve-genome.md`
> (never committed). Owner decisions from 2026-09-22 carry forward, except
> where the owner's 2026-09-29 request replaces them (see §12).
> **Written:** 2026-09-22; converged 2026-09-29

The owner's request (2026-09-29, verbatim):

> "We'll also want visual evolution for each 3D representation of Friday on
> the holographic desktop. Whichever the user is running should be subject to
> a weekly visual evolution routine that can be switched on or off, rolled
> back, or progressed manually instead of weekly. This way, everyone's Friday
> ends up looking visually distinct over time."

Code citations are `path:line` on main `41ef21fd`. Paths are relative to
`src/agent_friday/` unless they start with `index.html`, `ui_parts/`, `docs/`
or `tests/`. `index.html` line numbers drift; the identifiers are
authoritative.

**Evidence registers**, as in `docs/design/ROADMAP.md`:

- **VERIFIED**: read in the tree during this pass.
- **INFERRED**: a conclusion drawn from verified facts.
- **UNMEASURED**: a number this spec needs that nobody has measured yet. §10
  names the measurement.

---

## 0. Summary for the owner (one page)

**What changes for you.**

1. **Once a week, Friday's hologram changes a little.** It might be a slightly
   different blue, a lattice that sits a little tighter, or a new thin ring
   that marks something she learned to do. Each change is small. Over months
   they add up, so your Friday stops looking like anyone else's.
2. **She stays recognisably herself on every structure.** There is one set of
   visual traits, called the genome. The lattice, the sphere, the Möbius strip
   and the other ten structures all draw from it. If you switch structures,
   she keeps her colours and her personal mark.
3. **You are in charge of it.**
   - **One switch** turns it on or off.
   - **"Evolve now"** makes the next change today instead of waiting.
   - **"Undo"** on the change notice puts the last look back with one click.
   - **The history** lets you go back to any earlier look.
   - Nothing is ever deleted unless you delete it.
4. **You can see what changed and why.** Every change says, in plain words,
   what moved, why, and who made it. That can be "Friday (on this computer)",
   the local model by name, or a named cloud model.

**What it costs.**

| | Local path (default) | Cloud path (your choice) |
|---|---|---|
| **Privacy** | Nothing leaves the computer. Nothing is reported anywhere, ever. | About 40 numbers a week, shown to you in full, go to the model you allow. No words, names or titles are ever sent. |
| **Money** | Nothing. | A few cents a week (INFERRED; §9). It shows in Costs like any other cloud call. |
| **Speed** | The scene is capped at today's drawing cost. Friday measures it on your machine and undoes any change that slows her down. | Same. |
| **Effort** | None. You can ignore it entirely. | None after one consent screen. |

**Three decisions for you** (§12, with recommendations):

1. Is it on by default for new installs, or off until someone turns it on?
2. When it's on, who makes the changes by default: Friday on your computer, or
   a cloud model?
3. How far may she drift from today's cyan over a lifetime?

---

## 1. What exists today (grounded)

### 1.1 The two earlier designs, and how much of each is built

| Design | What it proposed | Built |
|---|---|---|
| This file, 2026-09-22 | 8 genes, a frontier model picks and is credited, weekly step (recommended), nightly dream-rsi, append-only history with hide/delete, signed shareable cards, market ratings | **Nothing.** No genome, `avatar`, `dream_rsi` or `idle_gate` code exists in `src/`, `index.html`, `tests/` or `scripts/`. `services/idle_gate.py` does not exist. **VERIFIED** |
| `evolve-genome.md`, 2026-09-28, uncommitted | Genesis-lattice genome, seeded per install, local signals, weekly growth, `off`/`auto`/`approve` modes, version tree, frame-time check, shared palette with brand bounds, per-structure sections | **Nothing.** Its lattice literals are accurate (`index.html:4739-4752`). **VERIFIED** |

What does exist and bears on this design:

- **`/api/evolution`** (`routes/insights.py:174-231`) is the *structure clock*.
  It advances the displayed structure one step every four days from
  `first_launch`, unless `preferred_scene_index` pins one. It stores to
  `~/.friday/evolution.json` (`:161`). It is not a genome.
  - **Defect:** it has no bounds check. An index of 13 or more makes
    `names[idx]` raise a 500 (`:221-223`). **VERIFIED**
- **New installs are pinned to structure 0.** `services/setup_chat.py:692`
  writes `preferred_scene_index: 0` on every non-rerun setup, so the four-day
  clock never runs unless the user picks "Reset to auto (evolution)"
  (`index.html:55115`). **VERIFIED**
- **The scheduler has a weekly trigger with no catch-up.** `_is_due`
  (`services/scheduler.py:510-564`) fires a weekly job only on its weekday. A
  machine that is off that day skips the week silently, and the job waits a
  full week for the next one. **VERIFIED**
- **The scheduler has an idle gate.** `idle_work_blocked_reason`
  (`services/scheduler.py:412-473`) covers the user's away time, stand-down,
  busy runs and `exclusive_lease()`. Its default window is 09:00-23:00, "so it
  never runs overnight" (`:393`). **VERIFIED**
- **Signing exists.** `provenance._deterministic` (`services/provenance.py:128`)
  and `sign_manifest` (`:233`) sign with the governance Ed25519 key through
  `IntegrityEngine.sign_payload` (`governance/proof_of_integrity.py:188`).
  **VERIFIED**
- **Per-install randomness exists, but none of it is usable as the seed.**
  - The governance key (`proof_of_integrity.py:401,467`) is an identity key.
  - `agent_id` (`services/federation.py:123-150`) is public.
  - `.shadow_salt` (`services/presidio_shadow.py:65-70`) belongs to privacy.
  - `tests/unit/test_update_check.py:129-190` pins that no install identifier
    leaks to update checks.

  The genome needs its own seed (§3.1). **VERIFIED**

### 1.2 Every 3D representation of Friday on the desktop

**One WebGL context.** `init()` creates it (`index.html:4603-4686`). The setup:

- `WebGLRenderer`, antialiased, `high-performance`, pixel ratio capped at 2
  (`:4611-4613`).
- The composer chain is RenderPass → `UnrealBloomPass(0.9, 0.55, 0.25)` →
  `HolographicShader` (chromatic offset, grain, scanlines) (`:4079-4094`,
  `:4624-4630`).
- The frame rate is **uncapped**: plain `requestAnimationFrame` (`:5019`).

The knowledge-graph galaxy (`:10823`) is a second context, and **it is not
Friday**. **VERIFIED**

**Where colour comes from.** Every material is created with a hardcoded hex.
Every frame, `updateGroupColors` (`:4966-4995`) repaints it from
`moodLerpValues.baseColor`/`accentColor`, which lerp toward the 14-entry `MOODS`
table (`:4054-4069`). Nothing else feeds the scene's palette: no theme tokens,
no CSS variables. So a genome palette has **one** injection point. **VERIFIED**

**The 13 structures** (`EVOLUTION_PATH`, `:4071-4077`; built in
`buildAllStructures`, `:4733-4955`; animated in `animate`, `:5018-5556`):

| # | id / name | v1 form (the literals a genome would parameterise) | Colour path |
|---|---|---|---|
| 0 | CUBES / Genesis Lattice | 3×3×3 grid, 1.4 boxes, spacing 1.6, ~15% dropped with unseeded `Math.random()`, scale 1.5, `LATTICE_SPREAD` 0.65 (`:4734-4752`, `:5281-5305`) | faces base×0.15, edges accent |
| 1 | ICOSAHEDRON / Sacred Sphere | 3 nested wire icosahedra, r 5/3.5/2, detail 3/2/1, opacity .15/.3/.6 (`:4754-4760`) | base, accent, white |
| 2 | NETWORK / Shannon Network | 120 nodes in 20³, link distance 6(+audio), ≤4 links (`:4854-4865`) | base/accent |
| 3 | DOME / Geodesic Cathedral | r35 hemisphere, 8 pillars, 6 octahedra, 2,000-point funnel (`:4762-4783`) | base, accent crystals |
| 4 | ASTROLABE / Lovelace Astrolabe | 8 rings r=2i, random tilt (`:4826-4838`) | base, accent dashes |
| 5 | TESSERACT / Von Neumann Tesseract | 16-vertex hypercube, scale 3 (`:4840-4852`) | base/accent |
| 6 | QUANTUM / Dirac Probability | 64×64 point sphere, 30 loops of 300 points (`:4876-4888`) | **its own rainbow** via `setHSL`; `updateGroupColors` skips it (`:4987`, `:5430-5446`) |
| 7 | MANDELBROT / Mandelbrot Set | escape-time point cloud, step .012, maxIter 40, **rewritten on the CPU every frame** (`:4805-4824`, `:5328-5340`) | base→accent by iteration |
| 8 | MOBIUS / Turing Möbius | point strip R3 r1.5, scale 4 (`:4867-4874`) | base/accent |
| 9 | GRID / Ocean of Light | 81×81 point ocean, 100×100 plane (`:4797-4803`) | base/accent |
| 10 | CABLES / Fibonacci Nerve | 80 tubes from a r30 Fibonacci sphere (`:4785-4795`) | base/accent |
| 11 | NONE / Transcendence | 100 rising 10-unit lines (`:4890-4899`) | base/accent |
| 12 | EDEN / Giga Earth (Rez) | tunnel, boss sphere, 15 spines, a white player figure, 60 debris lines (`:4901-4954`) | special flags; the player is always white (`:4979-4986`) |

**Present on every structure** (`buildBackgroundEnvironment`, `:4688-4720`):

- an 800-point particle shell (r 8-23, accent);
- 20 energy flares;
- 15 nebula sprites;
- fog `0x000205`.

**Other representations**, and whether evolution touches them:

- **Process orbs** (`ProcessOrbManager`, `:4197-4516`). Up to 8 task orbs in
  the same scene, with hardcoded category colours (`:4240-4308`). **Reserved:
  these are status.** The genome never touches them.
- **The condensed widget and the Picture-in-Picture avatar.** They are the same
  canvas, captured with `captureStream(24)` (`:57256-57266`). Evolution applies
  automatically, with the composer skipped in low-cost mode (`:5553`).
- **HUD corners.** `--hud-*` are derived from the mood base colour
  (`:5157-5164`). They follow the genome's palette implicitly, which is
  acceptable: they are decoration, not status.
- **The tray icon.** A static PNG (`friday_tray.py:54,528`). Out of scope.
- **The network-state canvas filter.** `body.net-offline`/`net-degraded`
  desaturate the scene (`:3225-3227`). **Reserved:** the genome must keep
  enough saturation for "offline" to stay visibly different (§6).

### 1.3 The reserved status signals

All of these are **DOM above the canvas** (`#ui-root` z-index 60, `:3276`), so
the genome cannot repaint them structurally. The risk is *confusion*, not
repainting: several of them share hues with the scene. **VERIFIED**

| Signal | Where | Colours |
|---|---|---|
| Approval cards and popups | `ApprovalCardBody` `:50819`, `ApprovalPopups` `:50890` (z 9800), widget `#condensed-approval` `:3237-3243` | amber `#f59e0b`, approve `#00ff80`, deny `#ff0080` |
| Gate chips | **only in `ui_parts/app.html:18583-18599`**, absent from `index.html` (the mirrors have drifted) | `#00ff80`, `#f59e0b`, `#8b93a7`, `#00d4ff` |
| "Liveness dot" | No element by that name. Turn liveness is a poll (`:30000-30011`). The dots nearby are `.status-dot` (`:97-100`, `:2388-2391`), `ConnectorDot`, `ProviderStatusDot` (`:46732`), and the recording/live dots (`:24736`, `:29153`) | `#00ff66`, `#ff0033`, `#ffcc00`, `#00ff80`, `#f59e0b`, `#ef4444` |
| Mood-as-status in the scene | SPEAKING, EXECUTING, REASONING, LISTENING (`:4054-4069`), chosen by the 1 s mood loop (`:53338-53359`) | SPEAKING base `#00ff80` is the approve green; EXECUTING is amber |

The last row matters most. The owner's standing rule is that executing is a
**colour shift only** ("the cube changes color to indicate state"), and motion
means "Friday is talking to me". So the scene's *state moods are themselves a
status signal*. The 2026-09-22 design's `hue_shift` rotated every mood; this
design does not (§6.2).

### 1.4 Motion, flashing and reduced motion today

- **Motion is allowed only while speaking.** `_speaking` is true when
  `ttsActive` is set or the amplitude is above 0.12 (`:5133-5134`). Motion
  follows the mood only while speaking; otherwise it lerps toward the IDLE
  mood (`:5143-5153`). This is the owner's rule, and it binds evolution.
  **VERIFIED**
- **The scene ignores `prefers-reduced-motion`.** Only the DOM dock, window
  animations and one message animation read it (`:956`, `:1349`, `:1733`,
  `:3650`, `:54067`). **VERIFIED**
- **Every structure change flashes.**
  - `setEvolution` sets `metamorphosisFlash = 1` (`:4586`), which adds **+4.0
    bloom** decaying over about 3.3 s (`:5114`, `:5534-5542`).
  - Time-lapse cycles a structure every 10 s (`:5109-5112`).
  - QUANTUM cycles hue continuously.
  - There is no photosensitivity guard. **VERIFIED**
  - This is a defect today, independent of evolution. Phase A0 (§11) fixes it.

---

## 2. STORM: questioning it from seven perspectives

Each voice asked its hardest question of the owner's request and of both
earlier designs. The synthesis (§2.8) cites which section answers which
question.

### 2.1 The generative artist

> **Q.** Where does uniqueness come from if nothing phones home?
> **A.** From a seed, the way hash-seeded generative art works: Art Blocks
> projects feed a token hash into a deterministic script, so the same hash
> always draws the same piece and different hashes diverge. Friday's seed is
> random per install and never leaves the machine. The *history* then
> compounds the divergence: two installs with the same seed would still
> diverge by week 3 because their activity differs.
>
> **Q.** Won't bounded small steps just produce 13 slightly different cyans?
> **A.** Only if every gene is a scalar. The distinctive thing in seeded art is
> *structural choice*: palette scheme, symmetry, the placement of the accent.
> So the genome needs a few discrete genes (accent scheme, symmetry order,
> sigil) beside the continuous ones, and a *signature mark* fixed at birth.
>
> **Q.** Should the unseeded `Math.random()` in the builders stay?
> **A.** No. Today the lattice drops a different 15% of cubes on every page
> load (`:4741`). That is noise, not identity. Seed it, and the lattice becomes
> hers.

### 2.2 The character-progression designer (games)

> **Q.** What makes a weekly change feel earned rather than random?
> **A.** Visible cause. Pokémon evolution is legible because it is tied to
> something the player did. Creatures (1996) went further with real genomes,
> and players loved reading lineage. So each step names its reason in one
> line: "a thin ring for the self-fix that shipped Tuesday".
>
> **Q.** What about accumulation? Games bloat.
> **A.** Keep `evolve-genome.md`'s rule: **every fourth step simplifies**. And
> the facet rings (one per shipped self-change) need a cap, with the oldest
> merging, so year 3 is not a hedgehog.
>
> **Q.** Rollback in games is usually cheating.
> **A.** Here it's taste, not score. There is no "better" look, so no look is
> a regression. The 2026-09-22 open question "may the look regress if
> calibration falls?" goes away: signals steer *direction*, never *grade*.

### 2.3 The accessibility specialist

> **Q.** What flashes?
> **A.** Today, every structure change does: bloom +4 (§1.4). WCAG 2.3.1
> ("Three Flashes or Below Threshold") is the floor, and an evolution step must
> never flash at all. Apply it as a slow crossfade (≥ 12 s, luminance change
> rate-limited per frame). Never reuse `metamorphosisFlash`.
>
> **Q.** Reduced motion?
> **A.** Honour `prefers-reduced-motion: reduce` in the scene itself: an
> opacity-only crossfade for steps, no flash on structure change, time-lapse
> disabled. Speaking motion stays, but its amplitude scales down (WCAG 2.3.3,
> animation from interactions). Add a Friday setting that overrides the OS in
> both directions.
>
> **Q.** Colour-only state?
> **A.** State moods keep their hue family (§6.2). Palette drift must also keep
> a contrast floor against the dark background in both themes. And no genome
> may push base and accent to within a colour-blind-confusable distance.

### 2.4 The GPU and performance engineer

> **Q.** What is the budget on a 12 GB RTX 4070 with the local brain resident?
> **A.** The resident FridayWeaver seat measured 4,291 MiB at 131K context,
> 6,082 MiB total on the card (`docs/design/active/model-soup.md`, 2026-09-12
> measurement). VRAM is not the constraint: the scene's geometry is small and
> its render targets depend on the window size, not on the genome
> (INFERRED). **The constraint is GPU time shared with token generation.**
> The Kokoro "hang" was GPU contention, so a heavier scene costs the user's
> tokens per second.
>
> **Q.** So what does the genome cap?
> **A.** Element counts (vertices, points, line segments) at **v1 +10%** per
> structure, and no new render passes, render targets, textures or per-frame
> CPU loops. MANDELBROT's CPU rewrite is already the heaviest path, so its
> density gene may only go *down*. Then measure: p95 frame time and local-seat
> tokens per second with the maximum genome versus v1 (§10.3).
>
> **Q.** And if a step still costs too much on some machine?
> **A.** The page self-checks after applying a step (§6.4). If p95 frame time
> passes the local baseline +10%, the page reverts to the parent's
> *expression*, marks the step "held: frame budget", and the next step must
> simplify.

### 2.5 The privacy and sovereignty reviewer

> **Q.** Is a weekly cloud call telemetry?
> **A.** Not if it is (a) the user's choice, (b) to the user's own key, (c) an
> allowlisted numeric payload shown in full, (d) never sent to a Friday
> server. There is no Friday server. The *local* path sends nothing, and there
> is no metrics endpoint, now or later. A test pins that the step module
> imports no network client except through the cloud author's single call
> site.
>
> **Q.** What about off the record?
> **A.** Off the record writes nothing (main `0597f9e2`). So no signal row may
> count an off-record turn, and no step may run from an off-record session's
> activity. `activity_ledger` already marks `off_record` rows
> (`services/activity_ledger.py:94-99`), so the aggregator skips them.
>
> **Q.** Can the genome identify someone?
> **A.** A shared card lets others recognise an install's look, the way a
> signed card is meant to. That is why sharing is always explicit, and why the
> seed, which could regenerate the sigil, is never exported.

### 2.6 The brand designer

> **Q.** When is it no longer Friday?
> **A.** When the anchor goes. Keep `evolve-genome.md`'s anchor: v1 cyan (hue
> ≈ 180°) is stored and never mutates, and lifetime drift stays within ±30°.
> What identifies her across structures is (1) the palette family, (2) the
> sigil, a small seeded mark every structure renders the same way, and (3)
> the facet rings.
>
> **Q.** Per-structure hues?
> **A.** No. One family. A structure may lean toward an accent, never pick its
> own base. That is `evolve-genome.md`'s rule, kept.

### 2.7 The skeptical engineer

> **Q.** Why a model at all?
> **A.** A seeded mutation engine is enough to produce divergence, and it
> costs nothing. A model adds *authorship*: a rationale, taste, and the fun
> credit the 2026-09-22 design wanted. So the seeded engine is the floor, and
> a model is an optional author on top. The owner now requires both a local
> and a cloud author. Every author works inside the same bounds.
>
> **Q.** Thirteen expression functions is a lot of surface.
> **A.** Thirteen small ones. Each maps shared genes onto literals that
> already exist in `buildAllStructures`. The test that matters is that an
> empty genome draws v1 exactly (§10.1).

### 2.8 Synthesis

| Question | Answer | Section |
|---|---|---|
| Uniqueness without telemetry | Local seed plus local history; no endpoint | §3.1, §8 |
| Identity across structures | One genome; shared traits expressed by all 13; a fixed sigil; one palette family | §3 |
| Legible cause | One-line reason per step, author credit | §5, §7 |
| Bloat | Simplify every 4th step; facet cap with merge | §4.3 |
| Flashing and reduced motion | Crossfade, never flash; the scene honours reduced motion (A0) | §6.3, §11 |
| Status colours | State moods keep their hue; reserved-hue distance; never touch orbs or DOM | §6.2 |
| GPU | Element caps v1+10%; no new passes; in-page frame check; measured against tok/s | §6.4, §10.3 |
| Off the record | Excluded from signals | §5.1 |
| Both authors | Seeded (floor), local model, cloud frontier; all credited | §5.3 |

---

## 3. The genome

### 3.1 Files

All under `~/.friday/avatar/`. This replaces `evolve-genome.md`'s
`~/.friday/genome/`; there is one location.

| File | What | Leaves the machine |
|---|---|---|
| `seed` | 32 random bytes, `os.urandom`, written once, mode 0600. Never derived from the governance key, `agent_id` or any install id. | **Never**, not even in an export |
| `steps/<content_hash>.json` | One signed step (§7). Immutable. | Only in a user-exported card |
| `state.json` | The active pointer, apply mode, author choice, hidden/favourite/names, the `declined` list, `last_step_at`, and the local frame-time baseline | Never |
| `signals/<YYYY-MM-DD>.json` | Nightly numeric aggregates (§5.1). Deleted once a step consumes them. | Never (the cloud author receives a *sum*, §5.3) |
| `trash/` | User-deleted steps, restorable for 30 days | Never |

`evolution.json` and `/api/evolution` keep their meaning: which structure is
shown. The genome never changes the structure index, `preferred_scene_index`
or time-lapse.

### 3.2 Shared genes (expressed by every structure)

| Gene | Kind | Range | Max per step | Notes |
|---|---|---|---|---|
| `palette.anchor_hue` | fixed | 180° | never | v1 cyan; brand anchor (decision 3) |
| `palette.base_offset` | continuous | −30° … +30° from the anchor, lifetime | 4° | applies to *identity moods* only (§6.2) |
| `palette.scheme` | discrete | analogous / split-complementary / triadic | change ≤ once per 8 steps | the accents derive from the base |
| `palette.accent_share` | continuous | 0.20 … 0.35 | 0.03 | the share of elements drawn in the accent |
| `palette.saturation` | multiplier | 0.85 … 1.10 | 0.03 | the floor keeps net-offline desaturation visible |
| `luma.bloom` | multiplier | 0.85 … 1.10 | 0.03 | on the mood's bloom; never above the v1 maximum |
| `luma.grain` | multiplier | 0.6 … 1.1 | 0.05 | |
| `form.density` | multiplier | 0.85 … 1.10 | 0.04 | per-structure element counts, capped at v1 +10%; MANDELBROT ≤ 1.0 |
| `form.coherence` | continuous | 0 … 1 | 0.08 | ordered orbits versus free drift: jitter and tilt spread |
| `form.symmetry` | discrete | 3 … 8 | ±1, ≤ once per 4 steps | ring counts, pillar counts, sigil arms |
| `speech.tempo` | multiplier | 0.85 … 1.15 | 0.05 | scales **speaking** motion only |
| `speech.amplitude` | multiplier | 0.85 … 1.10 | 0.05 | scales **speaking** displacement only; reduced motion caps it at 0.5 |
| `sigil` | fixed at birth | derived from the seed | never | a small mark (arm count, tilt, accent placement) every structure draws identically |
| `facets` | count | 0 … 12 | +1 per shipped self-change | thin rings around the structure; the 13th merges the two oldest |

**There are no idle-motion genes.** `evolve-genome.md` §7 proposed idle
breathe, think rotation, task rebuild, message ripple and an uncertainty
shimmer. Each of those is motion outside speaking, which the owner's standing
rule forbids (§1.4). They are **refused**, not deferred. If any comes back, it
comes back as a colour/state shift, or not at all.

The 2026-09-22 `structure` gene (an index into `EVOLUTION_PATH`) is
**dropped**. Which structure is shown is the user's choice, and "whichever the
user is running" is what evolves.

### 3.3 Per-structure genes (expressed only by that structure)

Each structure has a section of two to four local genes over literals that
exist today. A structure with no section draws exactly as v1.

| Structure | Local genes (v1 value → bounds) |
|---|---|
| CUBES | `grid` 3 → 3…4; `spacing` 1.6 → 1.4…1.9; `sparsity` 0.15 → 0.05…0.30 (seeded dropout) |
| ICOSAHEDRON | `shells` 3 → 2…4; `detail` 3/2/1 → ±1 each, total ≤ v1 |
| NETWORK | `nodes` 120 → 100…132; `link_distance` 6 → 5…7 |
| DOME | `pillars` 8 → `symmetry`-linked 6…10; `crystals` 6 → 4…7 |
| ASTROLABE | `rings` 8 → 6…9; `tilt_spread` random → seeded, scaled by coherence |
| TESSERACT | `w_ratio` 0.5/0.3 → 0.3…0.7 (speaking only) |
| QUANTUM | `hue_band` full rainbow → ±60°…±180° around the base (v1 is ±180°) |
| MANDELBROT | `max_iter` 40 → 32…40 (down only); `step` 0.012 → 0.012…0.015 |
| MOBIUS | `twists` 1 → 1…3 (odd); `width` 1.5 → 1.2…1.8 |
| GRID | `wave_scale` → 0.8…1.2 |
| CABLES | `tubes` 80 → 64…88 |
| NONE | `lines` 100 → 80…110 |
| EDEN | `spines` 15 → 12…16; the player stays white (reserved as "you") |

### 3.4 Expression

- **One loader.** `window.fridayGenome.load()` fetches
  `GET /api/avatar/genome` (the active step, resolved and clamped server-side)
  and exposes `express(structureId)` → `{literals, palette, sigil, facets}`.
- **Builders read literals from it.** `buildAllStructures` reads its literals
  from `express(id)` instead of inline constants, and seeds every
  `Math.random()` from `seed ⊕ structureId` through a small PRNG.
- **Palette.** `updateGroupColors` receives the genome palette as an
  *overlay on identity moods* (§6.2). It is the scene's single colour
  injection point, as §1.2 established.
- **Empty or invalid genome ⇒ v1.** A missing, unparseable, schema-invalid or
  signature-failing genome gives the v1 literals, with one exception: the
  seeded dropout, which is identical on every load. §10.1 pins this.
- **The API stays.** `window.fridayVibe.setStructure(i)` keeps its signature.
  A `previewGenome(g)` hook renders a candidate for the before/after card and
  the frame check.

---

## 4. The weekly step

### 4.1 Cadence, catch-up and "Evolve now"

The step is **not** a scheduler `weekly` trigger. That trigger skips missed
days (§1.1). Instead:

- **An hourly interval job, `avatar_growth_check`, decides whether a step is
  due.** It is due when the switch is on and
  `now ≥ last_step_at + 7 days`. It runs once and sets
  `last_step_at = now`.
- **Missed weeks catch up with one step, not a burst.** A machine off for five
  weeks takes **one** step on its first hour up. Its signal window covers the
  gap, capped at 28 days of rows. Its per-step bounds are the ordinary ones, so
  a long gap never produces a bigger jump. The next step is due seven days
  later.
- **"Evolve now"** runs the same step immediately and resets `last_step_at`.
  So a manual step never lands hours before a scheduled one.
- **A step waits while Friday is being watched.**
  - The *step* (authoring and signing) runs whenever it is due.
  - The *visual transition* waits until she is not speaking and no voice
    session is open, up to 10 minutes, then crossfades. Her face never
    changes mid-sentence.
- **Author gates.**
  - The seeded author has no gate: it is arithmetic.
  - The local-model author uses `idle_work_blocked_reason`. If the gate stays
    shut for 24 hours past due, that week falls back to the seeded author and
    says so.
  - The cloud author goes through the spend guard and the egress gate (§5.3).

### 4.2 The step pipeline

1. **Signals.** Sum the unconsumed `signals/*.json` rows (§5.1).
2. **Target.** The running structure, which is `/api/evolution`'s
   `structure_index` at step time. Time-lapse does not count.
3. **Author** (§5.3). The author proposes changes to shared genes and to the
   **target structure's** section. Other structures' sections do not change;
   they still express the shared genes, so every structure reflects the step.
4. **Clamp.** Every gene to its range, to its per-step maximum, and to the
   **step budget**: normalised change `Σ |Δ|/max_step ≤ 2.5`, with at most
   three genes moving.
5. **Validate** (§6). This covers the reserved-hue distance, the contrast floor,
   the colour-blind separation, and the static element-count cap. If a
   candidate fails, the step tries another, up to 5 attempts. If all fail, the
   week records a `skipped` entry with the reason.
6. **Sign and store** (§7). Write the step and advance the active pointer
   (auto mode), or store it as pending (ask-first mode).
7. **Tell the user** (§8.2).

### 4.3 Keeping it mature, not cluttered

- **Every fourth step simplifies.** It moves a gene toward its v1 value, merges
  facets, or narrows the QUANTUM hue band.
- **Declined mutation types fade back.** A declined or undone step adds its
  mutation type to `declined` in `state.json`. That type is strongly
  down-weighted, and the weight decays back over about 12 weeks. This is
  `evolve-genome.md`'s rule, kept.
- **In ask-first mode, one pending candidate at most.** The next week's
  candidate replaces it, built from the *active* genome, so unapproved changes
  never compound.

---

## 5. Signals, dream-rsi, and the three authors

### 5.1 Signals: counted nightly, numbers only

A nightly local job, `avatar_signals_nightly`, runs after memory dreaming. It
writes one row of **integers and bounded floats**:

- the turns and tasks that day;
- subagent peak concurrency;
- the workspace mix, as counts per fixed workspace enum;
- knowledge pages added and edited;
- the dream-rsi verdict counts (`shipped`, `hollow`, `failed`, `skipped`);
- the self-improvement scores when the Sunday report exists (epistemic overall,
  sycophancy index, pushback rate).

The row has:

- **no** titles, topics, names, quotes or free text;
- **no** off-record activity: the aggregator skips `off_record` ledger rows,
  and writes nothing on a day that is entirely off the record.

**Reconciling nightly dream-rsi with the weekly step.** Dream-rsi (§5.4)
improves Friday's *code*, not her look. The nights feed the week like this:

- **Nights count; the week spends.** Nightly jobs produce signal rows, and
  dream-rsi's SHIPPED verdicts are counted there. The weekly step consumes
  them.
- **Nights do not prepare candidate looks.** A candidate built on Tuesday from
  Tuesday's genome goes stale if the user undoes or rolls back before Sunday.
  Seven nightly candidates would also mean seven GPU or cloud calls to use one.
  The step is cheap enough to author at apply time.
- **The bridge is `facets`.** Each SHIPPED dream-rsi verdict earns one ring at
  the next step. That makes "what Friday learned to do this week" visible,
  which was the 2026-09-22 intent.

### 5.2 How signals steer (direction, never grade)

The author sees the week's signal sums and the seed's pseudo-random draw.
Suggested weightings, which a model author may depart from within bounds:

- Heavy parallel work favours coherence and the sigil's arms.
- Creative-workspace use favours a scheme or accent-share move.
- Long focus favours tightening: spacing down, coherence up.
- A quiet week favours a simplifying step.

Low calibration scores never make her look worse. There is no worse.

### 5.3 The three authors: the user picks, every step is credited

| Author | Credit shown | What runs | What leaves the machine |
|---|---|---|---|
| **Seeded** (the floor, always available) | "Friday, on this computer (no model)" | A deterministic mutation from `seed ⊕ step_number` and the weighted signals. The reason line is templated ("tighter lattice: a focused week"). | Nothing |
| **Local model** | "<seat id>, on this computer" (for example, the resident FridayWeaver seat) | One call through `local_call` under `local_only`, with the §5.3 payload. It returns `proposed_genome`, `rationale` (≤ 280 chars) and `name`. One reformat retry, then the seeded author with a note. | Nothing |
| **Cloud frontier** | "<model the provider reported>" plus Friday's one-line reason for picking it | The 2026-09-22 design, unchanged: Friday picks among frontier models with a working key, favouring variety, and calls the pinned provider directly (`_call_claude` / `_call_openai` with `fallback_models=None`, never `_generate_text`). Credit comes from the reported model; a mismatch shows both, with a warning. | The allowlisted payload below, through spend guard → `seal_outbound` (fail-closed) → `cost_meter` (`avatar_evolution`) → `attribution.record_generation` |

The payload is built by one function against an allowlist schema: numbers,
booleans and fixed enums only. A test pins the schema.

```json
{
  "schema": "friday.avatar.evolve/2",
  "step": 14,
  "target_structure": "MOBIUS",
  "genome": { "...active genome, sigil omitted..." },
  "bounds": { "...§3.2 and the target's §3.3 row..." },
  "signals": { "turns": 214, "tasks": 31, "subagent_peak": 6,
               "workspace_mix": {"code": 12, "creative": 3, "mail": 9},
               "pages_added": 12, "pages_edited": 49,
               "rsi": {"shipped": 2, "hollow": 1, "failed": 1, "skipped": 1},
               "epistemic_overall": 0.71, "sycophancy_index": 0.12 },
  "simplify": false
}
```

**Never sent:** the seed, the sigil, titles, topics, quotes, reflection text,
personality text, dream text, anything from the vault, and anything off the
record.

**Failure is visible and skips nothing silently.**

- A cloud failure (no key, network, gate block, spend cap, invalid output after
  one same-model retry) writes a `skipped` history entry naming the model and
  the reason, and the look stays.
- **Try again** lets Friday pick again.
- **"Use the local path this week"** is offered alongside it.
- Friday never hops providers on her own. That was the 2026-09-22 rule, and it
  is kept.

### 5.4 Nightly dream-rsi (carried from 2026-09-22 §5; a separate build item)

The nightly dream-rsi design stands as written on 2026-09-22 (this file's
§5 at main `33357802`; `git show 33357802:docs/design/active/avatar-visual-genome.md`). It is summarised here with one conflict
the pass found.

- **The window.**
  - Starts are allowed 00:30-05:15.
  - Nothing starts after 05:30.
  - A watchdog stops the run by 06:15.
  - GPU steps hold during 03:00-03:45 so memory dreaming and the reindex go
    first.
- **The gate.** It opens only when *all* hold, and every probe fails closed:
  - `rsi_paused` is false;
  - the user has been inactive for 60 minutes or more;
  - no arbiter lease or foreign hold is declared;
  - VRAM free is at least 11 GB;
  - the time is inside the window, and at least 20 hours have passed since the
    last run;
  - no scheduled GPU job is due within the run's budget.
- **Preemption:** finish the current step, don't start the next.
- **One outcome line per night:** ran (with its verdict), skipped (with the
  reason) or failed. The morning briefing carries it. A missing line by 07:00
  is raised as a defect.
- **Conflict (VERIFIED).** The scheduler's shared idle gate defaults to
  09:00-23:00 and says it "never runs overnight"
  (`services/scheduler.py:392-393`). Dream-rsi therefore needs its **own
  window setting**, as the 2026-09-22 design proposed. It cannot reuse
  `idle_work`'s window.
- **The build list is unchanged.** `services/idle_gate.py`, the `rsi_paused`
  setting and its reader, the `dream_rsi_nightly` job, unattended workflow
  wiring, the briefing line, and tests for every probe and cut-off.

The avatar feature does **not** depend on dream-rsi. Without it, `facets`
simply never grows, and everything else works.

---

## 6. Guardrails

### 6.1 Identity continuity

- **What never changes:**
  - the anchor hue;
  - the sigil;
  - the structure each builder draws (cubes stay cubes, a Möbius stays a
    Möbius).
- **What is bounded:**
  - per-step change (§3.2, and the §4.2 budget);
  - lifetime drift (±30° hue; the gene ranges).
- **A year-simulation gate before shipping (§10.2).** Across 1,000 seeds × 52
  steps:
  - no genome may leave its bounds;
  - the maximum single-step perceptual change (ΔE2000 on the dominant colour)
    must stay under a threshold. The owner's screenshot review sets that
    threshold.

### 6.2 Reserved status signals

1. **DOM is out of reach.** The genome is consumed only by the scene. It
   never sets CSS variables other than the existing mood-derived `--hud-*`.
   Approval cards, gate chips, status dots and process orbs keep their
   hardcoded colours. A test greps the genome loader's output sinks.
2. **State moods keep their hue.** The genome's `base_offset` and `scheme` apply
   to the **identity moods**: IDLE, CALM, CURIOUS, CREATIVE, CREATING, EXCITED,
   PROTECTIVE, FOCUSED, SOCIAL, REFLECTIVE. They do **not** apply to the
   **state moods** SPEAKING, EXECUTING, REASONING and LISTENING, which may take
   only the `saturation` and `luma` multipliers. Executing stays amber and
   speaking stays green, as the owner's rule requires.
3. **Reserved-hue distance.** No identity-mood base or accent may come within
   ΔE2000 20 of a reserved colour. The reserved colours are amber `#f59e0b`,
   approve `#00ff80`, deny `#ff0080`, and error red `#ff0033`/`#ef4444`. A
   candidate that fails is rejected like a frame-budget failure. So an idle
   Friday never looks like a pending approval.
4. **Offline stays visible.** The saturation floor (0.85) keeps the
   `net-offline` filter visibly distinct. §10.4 checks it by screenshot.
5. **Process orbs are untouched.** Their category colours are status.

### 6.3 Accessibility

- **Never a flash.** A step applies as a crossfade of at least 12 s. Per frame,
  the change in mean scene luminance is capped so that no 1 s window can
  contain a WCAG 2.3.1 general flash. The step never sets `metamorphosisFlash`.
- **Reduced motion is honoured** (Phase A0 adds it to the scene, §11). Under
  `prefers-reduced-motion: reduce`, or the Friday override:
  - a step uses a 2 s opacity-only crossfade;
  - `speech.amplitude` is capped at 0.5;
  - structure changes do not flash;
  - time-lapse is unavailable.
- **Colour is never the only carrier.** Base and accent must stay separable
  for deuteranopia, protanopia and tritanopia (simulated ΔE ≥ 15). A candidate
  that fails is rejected.
- **The notice is accessible.** The "what changed" notice is text, reachable
  by keyboard, and read by screen readers (`aria-live="polite"`).

### 6.4 Render and VRAM budget

- **Static caps (server-side, before signing):**
  - element counts at v1 +10% per structure;
  - MANDELBROT at v1 or below;
  - no gene may add a render pass, render target, texture or per-frame CPU
    loop, which the schema enforces by having no such genes;
  - bloom never above the v1 maximum.
- **Measured baseline.** On first enable, the page records the p95 frame time
  of the v1 genome on the active structure over 20 s of idle. It stores that
  locally as `baseline_p95`.
- **Live check after applying.** After a step, the page samples 20 s of idle.
  If p95 > `baseline_p95 × 1.10`, it:
  - reverts the *expression* to the parent (history is untouched);
  - marks the step `held: frame budget`;
  - requires the next step to be a simplifying one.
- **Local brain residency.** The step never loads a model.
  - The local author uses whatever seat is resident, through the arbiter's
    normal path.
  - The scene's cost is independent of the author, because authoring is a
    server call.
  - §10.3 measures tokens per second with the scene at the maximum genome.

---

## 7. Signed, versioned steps; rollback to any step

Each step is one JSON document:

```json
{
  "format": "friday.avatar.step/1",
  "step": 14,
  "parent": "sha256:<parent content_hash or null for v1>",
  "created_at": "2026-10-04T09:05:12Z",
  "kind": "growth | simplify | manual | skipped | held",
  "target_structure": "MOBIUS",
  "genome": { "...full clamped genome, sigil included..." },
  "diff": [ {"gene": "palette.base_offset", "from": 6, "to": 10} ],
  "reason": "Warmer by 4°: a creative week.",
  "author": { "path": "seeded | local | cloud",
              "model": "<reported model or null>", "requested": "<picked model or null>",
              "why_this_model": "…", "rationale": "…", "raw_proposal_digest": "sha256:…" },
  "input_digest": "sha256:<digest of the signal sums>",
  "generator": "agent-friday/<version>",
  "content_hash": "sha256:…",
  "signature": { "alg": "ed25519", "pubkey": "…", "value": "…" }
}
```

- **Signing.** `content_hash` is SHA-256 over `provenance._deterministic(body)`,
  signed by `IntegrityEngine.sign_payload`. These are the same conventions as
  media provenance manifests, with no new signing code.
- **Verification on load.** A step that fails verification is not expressed.
  The renderer walks up to the nearest verified ancestor and the notice says
  so.
- **The history is a tree.**
  - **Rollback to any step** sets the active pointer. The next step forks from
    there, and nothing later is discarded.
  - **Undo** is rollback to the parent. The undone step is labelled "undone" in
    history and counts as a decline (§4.3).
  - **Reset to Genesis** activates the empty genome (v1). The sigil stays,
    because the seed is permanent.
- **Hide and delete are carried from 2026-09-22 §4 unchanged.**
  - Hide is instant and reversible.
  - Delete asks for confirmation and moves the step to `trash/`, restorable
    for 30 days. After that the purge removes it. The purge is the only code
    path that removes history, and it acts only on user-deleted steps.
  - The descendants of a deleted step keep their `parent` hash and show
    "parent deleted".
- **The portable card** (`<name>.fridayavatar.json`) is carried from
  2026-09-22 §6 unchanged: the signed genome, the model credit, `lineage_models`
  and the input digest, with the seed never included. Import verifies, then
  clamps. The share hook stops at "Exported. The market isn't available yet".

---

## 8. UI (existing elements first)

### 8.1 Where the controls live

**The top-bar Holographic Scene menu** (`index.html:54971-55125`) gains an
**Evolve** section:

- the switch;
- "Evolve now";
- "Undo last change";
- "History…";
- the author choice: *On this computer* / *Local model* / *Cloud model*.

The existing "Reset to auto (evolution)" item is relabelled **"Rotate
structures automatically"**, so the two ideas stop sharing a word. Code uses
the `genome.*` / "growth" vocabulary, and `EVOLUTION_PATH` keeps its meaning,
as `evolve-genome.md` proposed.

**Settings → Appearance** mirrors the same controls, plus:

- apply mode: *Apply and tell me* / *Ask me first*;
- the Friday reduced-motion override.

### 8.2 The change notice

When a step lands, a small card appears beside the scene menu, with the same
look as other notices. It has:

- before and after thumbnails, rendered locally;
- "What changed", in plain words ("A little warmer. The lattice sits tighter.
  A new ring for Tuesday's self-fix.");
- **who made it**, prominently;
- **Undo** (one click) and **Keep**.

In ask-first mode, the same card reads **Apply** / **Not this one**, and a
small dot on the scene menu shows that a step is waiting. That is
`evolve-genome.md`'s rule: pending changes are visible where the avatar is,
not only in Approvals. This dot is **not** amber, because amber is reserved;
it uses the identity accent.

### 8.3 History

A timeline of steps with:

- a thumbnail;
- the name;
- the author credit;
- the reason;
- a "What was sent" disclosure (for cloud steps, the exact payload; for local
  and seeded steps, "nothing left this computer").

It can filter by author or model, and each step offers Restore, Hide and
Delete. Recently deleted steps are listed separately.

---

## 9. Costs and failure modes

| | Estimate | Register |
|---|---|---|
| Cloud call size | ~1.5K tokens in, ~0.3K out, once a week | INFERRED from the payload shape |
| Cloud money | about 1-5 cents a week at current frontier prices; about $0.50-$2.50 a year | INFERRED; shown per step in Costs |
| Local model call | one short call on the resident seat, seconds | UNMEASURED |
| Seeded step | milliseconds | INFERRED |
| Scene cost | at most v1 +10% elements; no new passes | bounded by schema; measured in §10.3 |
| Disk | ~3 KB per step plus a ~40 KB thumbnail; ~2.2 MB a year | INFERRED |

| Failure | What the user sees |
|---|---|
| The machine was off for weeks | One step on return; the notice says "caught up after 5 weeks away" |
| Cloud path fails | A `skipped` entry, Try again, and "use the local path this week" |
| Local seat busy all week | The seeded author steps and says so |
| The frame budget is exceeded | The look reverts by itself; "held: this change was too heavy for this machine" |
| A signature fails | The nearest verified ancestor is shown; the history row says "could not verify" |
| The genome file is corrupt | v1 is drawn; the history still lists the steps; the notice offers restore |

---

## 10. Verification plan

Everything is fail-first: each test must fail on main `41ef21fd` before its
change and pass after it, run in both directions (AGENTS.md).

### 10.1 Pipelines

- **Empty genome = v1.** With the seeded PRNG replaced by v1's dropout pattern
  captured at a fixed seed, `express(id)` returns exactly the v1 literals for
  all 13 structures. The snapshot is taken from `buildAllStructures` on main.
- **Clamping and budgets.**
  - Every gene is clamped to its range and to its per-step maximum.
  - The step budget holds.
  - MANDELBROT density never exceeds 1.0.
  - Property-based: 10,000 random proposals never produce an out-of-bounds
    genome.
- **Catch-up.** With `last_step_at` 5 weeks ago, one tick produces **one**
  step, with ordinary bounds, and `next_due = now + 7d`. "Evolve now" resets
  the clock.
- **The payload allowlist** rejects any string field outside the enums. The
  seed and sigil are never in the payload. The cloud author's call site is the
  only network path in the module (import-graph check).
- **Off the record.** A day with only off-record turns writes no signal row,
  and a mixed day counts only on-record rows.
- **Signing.** A tampered step fails verification and the renderer falls back.
  Rollback to any step followed by a new step produces a fork, and nothing is
  lost.
- **Reserved hues.**
  - The reserved-distance validator rejects a candidate at ΔE < 20 from each
    reserved colour.
  - State moods are unchanged by `base_offset`.
- **Guards.** `scripts/check_settings_readers.py` covers the new settings, and
  the rest of the AGENTS.md required checks pass.

### 10.2 The simulated year (numbers and pictures)

A headless script runs 1,000 seeds × 52 steps with synthetic signal weeks. It
reports:

- the pairwise genome distance at weeks 4, 26 and 52. **Distinctness gate:**
  at week 26, fewer than 1% of seed pairs are within the "looks the same"
  distance;
- the maximum single-step ΔE;
- the lifetime hue excursion;
- the facet count distribution.

It also writes a **contact sheet**: 12 seeds × weeks {0, 4, 13, 26, 52} ×
three structures (CUBES, MOBIUS, EDEN).

### 10.3 Screenshots and GPU on the real card

**Rendering.** Playwright, headless Chrome with `--use-gl=angle` (the only
flag that renders this scene headless), against a worktree copy of the page
served on `127.0.0.1`. It never touches the live server's `index.html`.

**Freezing time.** A debug hook, `fridayDebugScene.freeze(t)`, freezes time so
frames are comparable.

**Screenshots:**

1. **v1 versus the empty genome,** all 13 structures: pixel-diff ≤ 0.5%.
2. **Each structure at the minimum and maximum of every gene.** A human looks
   at all of them.
3. **Identity across structures.** One genome at week 26, all 13 structures,
   side by side. The question is whether it reads as one Friday.
4. **Reserved-signal overlap.** An approval card, the widget approval, a status
   dot and a process orb over the scene, at the palette extremes nearest each
   reserved hue.
5. **Offline.** `net-offline` at the saturation floor versus normal.
6. **Reduced motion on.** A step transition captured frame by frame.

**Flash analysis.** Frames are recorded every 16 ms across a step transition
and a structure change. The script computes relative luminance change per
frame and counts WCAG 2.3.1 general flashes per second. The gate is zero for
the step, and zero for the structure change after A0.

**GPU**, on the owner's machine with the resident seat loaded. Frame p95 and
`nvidia-smi` VRAM are measured with the scene at v1 versus the maximum genome
on the three heaviest structures (MANDELBROT, DOME, EDEN), while the local
seat generates a fixed 512-token reply. **Gate:** tokens per second within
3% of v1, and VRAM delta under 64 MiB. Both numbers are recorded in this
section with the date.

**The owner reviews the contact sheet** (10.2) and screenshots 2-3 before the
switch ships. The ΔE threshold in §6.1 is set from that review.

### 10.4 Live check after merge

After the owner's restart:

1. Enable the feature and run "Evolve now" three times.
2. Take a screenshot after each.
3. Undo twice.
4. Roll back to step 1.
5. Switch structures and confirm the palette and sigil persist.
6. Confirm the egress log shows **no** line for local and seeded steps, and
   exactly one line for a cloud step.

---

## 11. Phased build plan

Effort is in focused agent-days, with review. Each phase is shippable alone.

| Phase | What | Effort | Depends on |
|---|---|---|---|
| **A0** | Scene safety, useful with or without evolution: the scene honours `prefers-reduced-motion`; `metamorphosisFlash` is capped to stay under the WCAG 2.3.1 threshold (none under reduced motion); time-lapse is off under reduced motion; `/api/evolution` bounds-checks `preferred_scene_index`; the freeze hook for screenshots | 1 | nothing |
| **A1** | Genome schema, seed, the 13 expression sections, the loader, seeded PRNG in the builders, the "empty = v1" screenshot and pipeline gates, the signed step store, the tree, `GET /api/avatar/genome`, and rollback | 4 | A0 (freeze hook) |
| **A2** | Seeded author, nightly signal rows (off-record excluded), the catch-up job, "Evolve now", the reserved-hue / contrast / colour-blind / static-budget validators, the in-page frame check, and the simulated year with its contact sheet | 3-4 | A1 |
| **A3** | UI: the Evolve section in the scene menu and Settings, the change notice with Undo, history (restore, hide, delete, trash), ask-first mode, and the pending dot. `index.html` and `ui_parts/` both | 3 | A2 |
| **A4** | Model authors: the local-model path through `local_call`, and the cloud path (Friday's pick, pinned call, credit, consent screen, "What was sent", costs) | 3 | A2; ideally after FridayWeaver-2 is the resident seat |
| **A5** | Card export and import, and the share hook stopping before the market | 2 | A1 |
| — | Dream-rsi nightly scheduling (§5.4), a separate item | 4-5 (its own spec) | nothing here |
| — | Market plumbing and ratings (Appendix A) | after federation un-defers | — |

**Total for A0-A4: about 14-15 agent-days**, about three calendar weeks with
review and the owner's screenshot sessions.

### 11.1 Where it slots in the queue

The queued items are:

- CLM research;
- the DeerFlow `/goal` evaluator plus delivery receipts (`goals-and-delivery-receipts.md`);
- FridayWeaver-2 on Bonsai2;
- the Vibe Coding Salon;
- the owner-rules build (`owner-rules-and-anomaly-detection.md`).

Only the second and fifth have specs in the tree. CLM research, FridayWeaver-2
and the Salon are not specified in `docs/`, so their sizes are the owner's to
state.

Recommended order:

1. **A0 now, as filler.** It fixes live accessibility defects: no reduced
   motion in the scene, and a bloom flash on every structure change. It is one
   day and touches nothing else.
2. **Owner rules, then goals and receipts, keep their places.** Both are
   governance on outward actions, which outranks appearance.
3. **A1-A3 run alongside FridayWeaver-2.** Training is GPU-bound and long; A1-A3
   are UI and bookkeeping work that needs the GPU only for the §10.3
   measurement. That measurement should wait until training is not holding the
   card.
4. **A4 after FridayWeaver-2 lands on Bonsai2,** so the local author is the new
   resident seat and the tokens-per-second gate measures the brain users will
   actually run.
5. **CLM research and the Salon are independent of this.** Nothing here blocks
   them or is blocked by them. If they are commitments with dates, they go
   ahead of A4.

A useful coupling: once delivery receipts exist, each weekly step can emit a
receipt, so "Friday changed her look" appears in the same place as her other
unattended work.

---

## 12. Decisions

**Engineering calls made in this spec** (not the owner's to make):

- one genome for all structures, with a shared section and per-structure
  sections;
- the step never changes which structure is shown;
- nights count and the week spends; no nightly candidates;
- catch-up by `last_step_at`, not the weekly trigger;
- no idle-motion genes;
- state moods keep their hue;
- the reserved-hue distance, the frame budget, and the flash cap;
- signing with the existing provenance conventions;
- "Rotate structures automatically" as the relabel.

**Carried from 2026-09-22 unchanged:**

- the credit is prominent;
- the user's history is paramount (hide, delete with a 30-day undo, never
  automatic removal);
- the ratings model (Appendix A);
- the nightly dream-rsi design (§5.4).

**Replaced by the owner's 2026-09-29 request:**

- "A frontier model makes each change, never a local one." It is now **both
  paths**, with the user picking the author (§5.3).
- "Nightly or weekly": **weekly**, as recommended.
- "Moving backwards": **dissolved**. There is no worse look (§2.2).
- "Clock and pin": **resolved**. The four-day structure clock is untouched and
  separate (§3.1).

**For the owner (at most three):**

1. **On or off by default for new installs?**
   *Recommended: on, using the on-this-computer path, with the first change
   announced and one-click off.*
   - Off by default (the 2026-09-22 answer) means most Fridays never diverge,
     which defeats "everyone's Friday ends up looking visually distinct".
   - On costs nothing in privacy or money on the local path.
   - Existing installs, including yours, get a one-time question instead of a
     silent switch.
2. **Default author when it's on?**
   *Recommended: Friday on this computer (seeded, or the local model when one
   is resident).* The cloud author is one click away and credited when chosen.
   Starting local keeps the no-egress promise for anyone who never opens the
   menu.
3. **How far from cyan may she drift over her lifetime?**
   *Recommended: ±30° hue (teal to azure; never green, amber or pink, because
   those mean status).* Wider means more distinct Fridays and a looser brand.

**Still open, not blocking:**

- the default licence for shared cards, and whether the rationale is included
  by default (2026-09-22 decision 8);
- the dream-rsi night window hours (§5.4).

---

## Appendix A. The market side and ratings (carried from 2026-09-22 §7; not built; federation is deferred)

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

---

## Appendix B. `evolve-genome.md` (2026-09-28), preserved verbatim

This draft was written on 2026-09-28 and never committed. It is kept here so
it cannot be lost. Where it and the body above disagree, the body wins. The
disagreements are:

- the storage location (`~/.friday/avatar/`, not `~/.friday/genome/`);
- the idle and state animations of its §7, which are refused (§3.2);
- growth starting on Genesis only, which is replaced by all 13 structures
  expressing one shared genome (§3).

Its seed, apply modes, the declined list, the tree, the frame check, the
simplify-every-fourth rule and the brand-bounded palette are adopted.

````markdown
# Evolve: a cumulative, reversible avatar genome

**Status:** proposed design. Not implemented. The lattice build step has been
checked against `index.html` (see "Current renderer"). The per-frame animation
block has not been checked; hooks into it are unverified.

## Intent

Each Friday's holographic avatar (the Genesis lattice) grows over time into
something unique to that installation. Growth is cumulative, driven by local
activity, bounded by performance and identity limits, and fully reversible.
Evolve is opt-in and off by default.

## Naming

`index.html` already uses "evolution" for `EVOLUTION_PATH`, the ordered list of
13 hologram structures selected through `preferred_scene_index`. This feature
changes the contents of a structure, not which structure is shown. To avoid the
collision, code and settings for this feature use the `genome.*` / "growth"
vocabulary, and "evolution" keeps its existing meaning. The user-facing toggle
may still be labelled "Evolve".

## Principles

- **Data, not code.** A growth step changes a genome file of visual parameters.
  The renderer's code never changes as a result of growth.
- **Genesis v1 is the floor.** With no genome, or with an invalid one, the renderer
  draws exactly the current Genesis lattice. However far a genome grows, the
  result stays recognisably a lattice.
- **Local and private.** Growth comes from on-device activity signals. The genome
  holds visual traits only, never usage data, so it is safe to export or share.
- **Reversible.** Every accepted change is a new version. Rolling back never
  destroys history.

## Current renderer (checked)

The Genesis lattice is structure 0 (`CUBES`, "GENESIS LATTICE") in
`EVOLUTION_PATH`, rendered in `index.html`. Line numbers are approximate anchors
and drift as the file changes; the identifiers are authoritative.

| Location (approx.) | What it is |
|--------------------|------------|
| ~4071 | `EVOLUTION_PATH` definition; entry 0 is `CUBES`. |
| ~4734–4752 | Lattice build: a `THREE.Group` (`gCubes`) of `BoxGeometry` meshes, each also pushed into `coreCubes`. |
| ~4989 | Material and visibility pass that applies to the cubes. |
| ~5111 | `timeLapseTimer`, which cycles structures on a 10-second interval. |
| ~5281–5300 | Per-frame animation: audio-, voice- and interaction-driven spread, capped by `LATTICE_SPREAD = 0.65`, plus voice-volume scaling. **Not yet read in full.** |
| ~5590 | `window.fridayVibe.setStructure(i)`, the public structure API. |

`tests/gauntlet/test_dead_scene_name_setting_removed.py` guards the
`preferred_scene_index` / `setStructure` wiring.

v1 values found in the build block, which become the genome defaults:

| Code | v1 value | Genome trait |
|------|----------|--------------|
| `gridSize` | 3 (3×3×3) | `lattice.grid` |
| `spacing` | 1.6 | `lattice.spacing` |
| `BoxGeometry` edge | 1.4 | `lattice.cube_size` |
| `0.85` keep threshold | ~15% of cubes omitted at random | `lattice.sparsity` |
| `gCubes.scale` | 1.5 | `lattice.scale` |
| `0x00ffff`, opacity 0.85 / 0.8 | cyan | `palette.*` |
| per-cube `speed` | 0.5–1.0 | `motion.tempo` |
| `LATTICE_SPREAD` | 0.65 | `motion.spread_cap` (unverified: animation block) |

Constraints this places on the design:

- Growth changes only what structure 0 draws. It never changes the structure
  index, `preferred_scene_index`, or `timeLapseTimer` behaviour.
- Subagent orbits reuse existing meshes in `coreCubes`: an orbiting cube is a
  position change on an existing mesh, not new geometry.
- The genome loader hooks into two places: the build block and the per-frame
  animation block. The second hook is designed only after that block is read.

## 1. Genome file

Location: `~/.friday/genome/`. The current genome is `current.json`. Versions are
`v0001.json`, `v0002.json`, and so on.

```json
{
  "schema": 1,
  "version": 7,
  "parent": 6,
  "seed": "<random per install>",
  "lattice": { "grid": 3, "spacing": 1.6, "cube_size": 1.4, "sparsity": 0.15, "scale": 1.5 },
  "palette": { "primary": "#00ffff", "opacity": 0.8, "glow": 0.6 },
  "motion": { "tempo": [0.5, 1.0], "spread_cap": 0.65 },
  "animations": {
    "idle_breathe": { "enabled": true, "amplitude": 0.04, "period_s": 6 },
    "think_slice_rotate": { "enabled": false },
    "task_rebuild": { "enabled": false },
    "message_ripple": { "enabled": false }
  },
  "orbits": { "max_rings": 1, "radius": 2.2, "speed": 0.3 },
  "states": {
    "focused":   { "intensity": 0.5 },
    "uncertain": { "intensity": 0.5 },
    "blocked":   { "intensity": 0.5 },
    "satisfied": { "intensity": 0.5 }
  },
  "changelog": "Grew a second orbit ring: 40 parallel subagent tasks this week."
}
```

Every numeric trait has a schema-defined minimum and maximum. The loader clamps
out-of-range values and rejects unknown keys.

The seed is set once at install time, so genomes differ from the start. The
seed may drive which cubes the sparsity step omits, replacing the current
unseeded random dropout, so a given genome always draws the same lattice.

## 2. Weekly growth

A scheduled job runs weekly when Evolve is enabled:

1. Aggregate the past week's local signals: subagent count and concurrency,
   workspace usage mix, focus-session length, task completions and failures.
   The aggregates are used and then discarded. They are never written to the genome.
2. Choose one or two mutations. Signals weight the choice: heavy parallel work
   favours orbit traits, creative-workspace use favours new motion, long focus
   favours structural tightening. The seed drives the random choice among the
   weighted candidates.
3. Every fourth growth step is a simplifying mutation: it merges, removes, or
   softens a trait. This keeps the lattice maturing instead of accumulating
   features.
4. Validate the result (section 5). On failure, try a different mutation, up to
   N attempts. If every attempt fails, skip the week.
5. Hand the validated candidate to the apply step (section 3), together with a
   human-readable changelog line saying what changed and why.

## 3. Applying a growth step

Apply behaviour is a user preference, stored in `settings.json` as
`genome.apply_mode`. It is not part of the genome, because the genome holds
visual traits only and stays safe to share.

| Mode      | Behaviour |
|-----------|-----------|
| `off`     | No growth job runs. The lattice stays on its current version. This is the install default. |
| `auto`    | The candidate is written as a new version and becomes current immediately. The user gets a notification with the changelog line and a one-click rollback. This is the default when the user first turns Evolve on. |
| `approve` | The candidate is stored as pending, with a before/after preview and the changelog line. Nothing changes until the user approves it. |

Rules:

- **One pending candidate at most.** In `approve` mode, an unanswered candidate
  is replaced by the next week's candidate, which is built from the current
  genome, not stacked on the pending one. Unapproved changes never compound.
- **Declines are remembered, then fade.** A declined candidate adds its mutation
  type to a local `declined` list with a timestamp. That type is strongly
  down-weighted at first, and the penalty decays back to normal over about three
  months. Declining the same type again restarts the decay. The list is local
  preference data and is never written to the genome.
- **Mode changes affect only future growth.** Switching modes never rewrites
  or removes existing versions.
- **Pending candidates are visible where the avatar is.** Approval must not
  depend solely on the System → Approvals list. The pending candidate shows an
  indicator on or next to the lattice that opens the preview, so the user sees
  it without going looking.

## 4. Versions and rollback

- Each version records its `parent`. History is a tree, not a line.
- Rolling back sets `current.json` to an earlier version. The next growth step
  branches from that point, and later versions are kept.
- Rolling back uses the same interaction model as Liquid UI workspace snapshots
  (`list_workspace_history` / `revert_workspace`). Where practical, it reuses that code.
- A "reset to Genesis" option always exists.

## 5. Guardrails

- **Identity bounds:** schema limits keep the lattice structure intact. For example,
  the grid cannot drop below the v1 size, and cubes stay cubes.
- **Performance budget:** before a candidate genome is accepted, the renderer runs
  it offscreen for a short sample. The candidate is rejected if the p95 frame time
  exceeds the budget on this machine.
- **Accessibility:** states are conveyed primarily through motion and form, not
  colour alone. A reduced-motion preference caps animation amplitude.

## 6. Renderer contract

- The lattice renderer reads `current.json` through one loader function and
  nothing else.
- A missing, unparseable, or schema-invalid genome falls back to the Genesis v1
  defaults listed under "Current renderer".
- The loader's output replaces the literals in the build block (`gridSize`,
  `spacing`, `BoxGeometry` edge, dropout threshold, `gCubes.scale`, colour and
  opacity, `speed`). The animation-block hook is specified after that block
  has been read.
- `window.fridayVibe.setStructure(i)` keeps its current signature. Structure 0
  reads the genome when it is built; other structures are unaffected.
- The renderer exposes a `previewGenome(genome)` hook for the performance check,
  for the before/after preview of a pending candidate, and for Settings.

## 7. Genesis v2 content (first growth targets)

Animation sequences:

- **Idle breathe:** the cubes expand and contract slowly.
- **Think:** slices rotate like a Rubik's cube while the model reasons.
- **Task complete:** the lattice splits and rebuilds.
- **New message:** a ripple propagates through the cubes.

Subagents:

- Each subagent pulls one cube from `coreCubes` out of the lattice. The cube
  orbits a task sphere shared by all subagents on the same task, and snaps back
  into place when its subagent finishes.
- The lattice's missing cubes show at a glance how much work is in flight.

Emotional and task states (motion first, colour second):

- **Focused:** the cubes pull tight together.
- **Uncertain:** the cubes sit slightly out of alignment and shimmer.
- **Blocked:** one rotation axis freezes.
- **Satisfied:** a slow outward bloom.

## Build order

1. Read the per-frame animation block (~5270–5320) and record its variables.
2. Genome loader and renderer contract, with v1 defaults. The renderer must
   draw v1 identically from an empty genome. A test compares the empty-genome
   lattice against the current literals.
3. Genesis v2 animations and states, driven by genome parameters.
4. Subagent orbit behaviour.
5. Version store and rollback UI.
6. Weekly growth job, performance check, and changelog.
7. Apply modes (`off` / `auto` / `approve`), pending-candidate indicator, and
   the declined list.
8. Optional: genome export and import.

## Open questions

- Which activity signals exist today, and where are they aggregated?
- How strict should the performance budget be on low-end GPUs? Should the budget
  scale with the hardware?
- Should the other 12 structures in `EVOLUTION_PATH` each get their own genome
  section later, so growth persists when the user switches structure?


## Per-structure growth

Every structure in `EVOLUTION_PATH` can grow, if the user chooses. The first
build ships growth for Genesis (`CUBES`) only; the schema leaves room for the
others from the start so no migration is needed later.

### Schema

The genome is one file with one section per structure, plus shared traits:

```json
{
  "schema": 1,
  "seed": "<per-install>",
  "palette": { "...": "shared, see Palette below" },
  "structures": {
    "CUBES":     { "enabled": true,  "lattice": { "...": "..." }, "motion": { "...": "..." } },
    "TESSERACT": { "enabled": false }
  }
}
```

- A structure section holds only that structure's traits. Its v1 defaults are
  taken from that structure's code in `index.html`, the same way the Genesis
  defaults were (see "Current renderer").
- A structure with no section, or with `enabled: false`, draws exactly as it
  does today.
- A structure cannot be enabled for growth until its v1 defaults have been
  read from code and recorded here. Each of the remaining 12 structures needs
  that reading before it can grow.

### User control

- One Evolve switch per structure in Settings. Only Genesis is on at first.
- The global `genome.apply_mode` (off / auto / approve) applies to all
  enabled structures.

### Which structures grow each week

- The weekly job only changes structures that were actually on screen during
  the week, in proportion to time shown. Structures the user never looks at
  stay as they are.
- The same per-week change budget applies across all structures together,
  not per structure, so enabling more structures does not speed up growth.
- `timeLapseTimer` cycling does not count as the user choosing a structure;
  only time on the user's preferred structure (`preferred_scene_index`) and
  deliberate switches count as use. (Open: confirm this signal is
  distinguishable in code.)

### Versions and rollback

- There is one version history for the whole genome. Each version is a
  snapshot of every structure section and the shared palette.
- Rolling back a single structure copies that structure's section from an
  earlier version into a new version. Other structures and later history are
  kept.
- Rolling back the whole genome loads an earlier version, and new weeks
  continue from there, as described in "Versions".

## Palette: shared, evolving within brand bounds

All structures share one palette so Friday reads as a single entity. The
palette evolves, but within colour-theory bounds that keep it recognisably
on brand.

### Anchor

- The brand anchor is v1 cyan (`0x00ffff`, hue about 180°). It is stored as
  `palette.anchor_hue` and never changes.
- `palette.base_hue` starts at the anchor and may drift at most ±30° from it
  over the genome's lifetime, and at most 5° in any one week.

### Accents

- Accents are derived from the base hue by harmonic relationships, not chosen
  freely: analogous (±30°), split-complementary (base + 150° / + 210°), or
  triadic (± 120°). The genome stores which scheme is in use and up to two
  accent colours.
- The scheme may change at most once every eight weeks.
- The base colour carries most of the visual weight; accents are limited to
  a share of cubes or elements (starting at 20%, capped at 35%).

### Saturation, lightness and contrast

- Saturation and lightness drift within fixed bands around the v1 values, so
  the hologram keeps its glow and does not wash out or go muddy.
- Every palette must meet a minimum contrast against the UI background in
  both themes; a candidate that fails is rejected like one that fails the
  frame-time check.
- Emotional and task states continue to be expressed mainly through motion,
  not colour (see v2 content), so palette drift never hides state.

### Per-structure colour

- Structures may not have their own base hue.
- A structure section may carry an `accent_bias` (which accent it leans
  towards) and an `accent_share` within the global cap. This lets structures
  feel slightly different while staying one family.

### Resolved and open questions

- Resolved: all structures may grow if the user enables them.
- Resolved: structures share one palette, bounded as above.
- Open: are ±30° lifetime drift and 5° per week the right bounds? Needs a
  visual test across a simulated year of weekly changes before shipping.
- Open: should the anchor ever be user-editable (for users who want a
  different brand colour), and if so does that reset the drift bounds?
````
