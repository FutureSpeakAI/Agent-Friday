# Avatar visual genome: Friday's look evolves weekly, on every structure, reversibly

> **Status:** proposed (spec only; nothing in this document is built). The
> owner decided all three open questions on 2026-09-29 (§12): on by default;
> a frontier model authors by default, and the user may choose any model; one
> shared palette within ±30° of cyan. This is
> the converged design. §13 adds the processing-state vocabulary (owner
> approved 2026-09-29), and §14 maps the spec onto the north star. §15 puts
> Giga Earth on a set track of Rez forms that no model changes (owner request
> 2026-09-30). It replaces the 2026-09-22 version of this file and the
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
> - `privacy/cloud_consent.py` (`resolve`)
> - `services/agent.py` (`CLAUDE_TOOLS`) and `services/voice_engine.py`
>   (`_VOICE_SHARED_TOOLS`)
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
   what moved, why, and which model made it. By default Friday picks a
   frontier cloud model each week and credits it by name.
   - **You can choose any model instead:** your local model, a different cloud
     model, or no model at all (Friday's own seeded change). Your choice
     sticks until you change it.
   - **She never switches on her own.** If the default cloud model isn't
     available, she waits and tells you. The notice offers both fixes: connect
     a cloud model, or use your local model instead.
7. **She shows what she's doing (§13).** The lattice becomes an instrument:
   - a cube steps forward when she needs your OK;
   - a wave rolls once per reasoning round;
   - a block twists once per tool call;
   - a vent opens only when something actually goes to the cloud.

   Every movement comes from something really happening, with the same words
   on screen and in voice. Nothing moves for show.
5. **Her colours are the same on every structure.** All 13 share one palette,
   so switching from the lattice to the Möbius strip never changes her colours.
   Over her lifetime she drifts at most 30° either way from today's cyan (teal
   to azure). She never turns green, amber or pink, because those mean status.
6. **All of it works by voice.** "Friday, evolve now." "Undo that look." "Go
   back to last month's look." "Turn evolution off." "What changed?" (§8.4)

**What it costs.**

| | |
|---|---|
| | Cloud model (the default) | Local model or no model (your choice) |
|---|---|---|
| **Privacy** | About 40 numbers a week go to the model, and you can see all of them. No words, names or titles are ever sent. | Nothing leaves the computer. |
| **Money** | A few cents a week (INFERRED; §9). It shows in Costs like any other cloud call. | Nothing. |
| **Speed** | The scene is capped at today's drawing cost. Friday measures it on your machine and undoes any change that slows her down. | Same. A local model call takes a few seconds, while you're away. |
| **Effort** | None. It is on from the start. | One choice in the menu, or by voice. |

Either way, nothing is reported to anyone else, ever.

**Decided 2026-09-29** (§12): on by default; a frontier model authors by
default and the user may pick any model; one shared palette within ±30° of
cyan.

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
> server. There is no Friday server, and there is no metrics endpoint, now or
> later. (A frontier model is the default author, owner, 2026-09-29, so by
> default the weekly call is the one thing that leaves the machine. A user who
> wants nothing to leave picks a local model or the seeded author, and then
> nothing does.) A test pins that the step module imports no network client
> except through the cloud author's single call site.
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
> credit the 2026-09-22 design wanted. The owner decided (2026-09-29) that a
> **frontier model is the default author**, and that the user may choose
> **any** model, or the seeded engine with no model at all. So all three
> authors exist, one is the default, and the user's choice sticks. The seed
> makes installs differ whichever author is chosen (it seeds the builders and
> the sigil).
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
| Who authors | A frontier model by default, picked by Friday and credited; the user may choose any model or the seeded author; never a silent switch | §5.3 |

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
| `palette.anchor_hue` | fixed | 221° | never | today's resting colour, IDLE's `#1e54c7` (decision 3; see note below the table) |
| `palette.base_offset` | continuous | −30° … +30° from today's colours, lifetime (decided) | 4° | turns the cool identity moods (§6.2) at constant brightness; the validators keep them out of green, amber and pink, which in practice stops the teal end near −8° (−16° with a turned accent) |
| `palette.scheme` | discrete | v1 / cool / violet (accents turned a further 0°, 20° or 40°) | change ≤ once per 8 steps | complementary and triadic schemes were dropped: they put an accent on amber or pink |
| `palette.accent_share` | continuous | 0.20 … 0.35 | 0.03 | the share of elements drawn in the accent |
| `palette.saturation` | multiplier | 0.85 … 1.10 | 0.03 | the floor keeps net-offline desaturation visible |
| `luma.bloom` | multiplier | 0.85 … 1.10 | 0.03 | on the mood's bloom; never above the v1 maximum |
| `luma.grain` | multiplier | 0.6 … 1.1 | 0.05 | |
| `form.density` | multiplier | 0.85 … 1.10 | 0.04 | per-structure element counts, capped at v1 +10%; MANDELBROT ≤ 1.0 |
| `form.coherence` | continuous | 0 … 1 | 0.08 | ordered orbits versus free drift: jitter and tilt spread |
| `form.symmetry` | discrete | 5 … 8 (v1 is 8) | ±1, ≤ once per 4 steps | ring counts, pillar counts, sigil arms |
| `speech.tempo` | multiplier | 0.85 … 1.15 | 0.05 | scales **speaking** motion only |
| `speech.amplitude` | multiplier | 0.85 … 1.10 | 0.05 | scales **speaking** displacement only; reduced motion caps it at 0.5 |
| `sigil` | fixed at birth | derived from the seed | never | a small mark (arm count, tilt, accent placement) every structure draws identically |
| `facets` | count | 0 … 12 | +1 per shipped self-change | thin rings around the structure; the 13th merges the two oldest |

**There are no idle-motion genes.** `evolve-genome.md` §7 proposed idle
breathe, think rotation, task rebuild, message ripple and an uncertainty
shimmer. Each of those is motion outside speaking, which the owner's standing
rule forbids (§1.4). They are **refused**, not deferred. If any comes back, it
comes back as a colour/state shift, or not at all.

**One palette, every structure (decided 2026-09-29).** The palette section is
the only source of colour for all 13 structures and the background. No
structure section may carry a hue, an accent bias or an accent share of its
own; the §3.3 genes are form only. So switching structures never changes her
colours. Two v1 exceptions are resolved in the genome's favour:

- QUANTUM's free rainbow (`index.html:5430-5446`) becomes a sweep between the
  shared base and accent.
- MANDELBROT's base→accent gradient already uses the shared pair.

EDEN's white player figure stays white, because it stands for the user, not
Friday. With an empty genome (v1), both exceptions draw as they do today.

**Note on the anchor (2026-09-30).** At rest the scene is not cyan: it is
IDLE's `#1e54c7` (221°); cyan appears in CURIOUS and some state moods. The
decided "±30° from today's cyan" is implemented as ±30° from today's colours,
so every Friday starts exactly as she looks now and stays recognisably her.

The 2026-09-22 `structure` gene (an index into `EVOLUTION_PATH`) is
**dropped**. Which structure is shown is the user's choice, and "whichever the
user is running" is what evolves.

### 3.3 Per-structure genes (expressed only by that structure)

Each structure has a section of two to four local genes over literals that
exist today. A structure with no section draws exactly as v1.

| Structure | Local genes (v1 value → bounds) |
|---|---|
| CUBES | `spacing` 1.6 → 1.4…1.9; `sparsity` 0.15 → 0.05…0.30 (seeded dropout). The grid stays 3×3×3: a 4×4×4 lattice is 64 cubes against today's 27, which no sparsity brings inside the +10% budget |
| ICOSAHEDRON | `shells` 3 → 2…4; `detail` 3/2/1 → ±1 each, total ≤ v1 |
| NETWORK | `nodes` 120 → 100…132; `link_distance` 6 → 5…7 |
| DOME | `pillars` 8 → `symmetry`-linked 6…10; `crystals` 6 → 4…7 |
| ASTROLABE | `rings` 8 → 6…9; `tilt_spread` random → seeded, scaled by coherence |
| TESSERACT | `w_ratio` 0.5/0.3 → 0.3…0.7 (speaking only) |
| QUANTUM | `wave` 10 → 8…12 (colour comes from the shared palette, never its own band) |
| MANDELBROT | `max_iter` 40 → 32…40 (down only); `step` 0.012 → 0.012…0.015 |
| MOBIUS | `twists` 1 → 1…3 (odd); `width` 1.5 → 1.35…1.65 (wider breaks the budget) |
| GRID | `wave_scale` → 0.8…1.2 |
| CABLES | `tubes` 80 → 64…88 |
| NONE | `lines` 100 → 80…110 |
| EDEN | none by model: `stage` 0…6 moves only along its set track (§15); the player stays white (reserved as "you") |

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
- **The author gate: the chosen author, or wait.** A step runs with the author
  in `state.json` (§5.3), and only that one.
  - **The default author is "frontier".** It is available when there is a
    working key for at least one frontier provider, and a cloud-consent answer
    that allows cloud calls (`privacy/cloud_consent.resolve`). A user who
    chose local-only mode is not offered a cloud call.
  - **A named model** (local or cloud) is available when its seat or key
    answers. A local model also waits for `idle_work_blocked_reason` to open.
  - **The seeded author** is always available.
  - **If the chosen author is unavailable when a step falls due, no step
    runs, and Friday never switches authors on her own.** `last_step_at` does
    not move. She shows one plain notice with both fixes, one click each:
    *"Evolution is waiting for a cloud model."* **[Connect a cloud model]**
    **[Use <local model name> instead]**. The second button appears when a
    local seat exists, and names it.
    - "Connect" opens Settings → Accounts & Keys, or the consent question if
      that is what is missing.
    - "Use … instead" sets that local model as the author (the choice
      sticks), and runs the waiting step at once.
    - For a chosen author other than frontier, the notice names that author
      and offers "try again" and "choose another model".
  - The notice repeats at most once a week. The waiting state shows in the
    scene menu (§8.1) and in voice ("what changed?" answers "nothing yet:
    I'm waiting for a cloud model", and offers the local model by name).
  - When the author becomes available, the next hourly check runs **one**
    step. That is the same catch-up rule as a machine that was switched off.
  - A cloud call goes through the spend guard and the egress gate (§5.3).

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

## 5. Signals, dream-rsi, and the author

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

### 5.3 The author: frontier by default, any model by choice (decided)

The owner decided on 2026-09-29, verbatim: *"those users should be able to run
evolution on any model they wish, but default to frontier"*.

- **The author is a user setting.** It is `author` in `state.json`, set from
  the scene menu, from Settings, or by voice (§8.4). It keeps its value until
  the user changes it.
- **The default is `frontier`.** Friday picks among frontier models with a
  working key, favouring one that hasn't made any of the last few steps, and
  records a one-line reason ("Chose <model>: it hasn't shaped Friday since
  W31").
- **The user may instead choose any model they have:** a specific cloud
  model, a local model (any seat the arbiter can serve), or `seeded`
  (Friday's own change, no model).
- **Friday never changes the author herself,** not even when the chosen one
  is unavailable (§4.1).

| Author | Credit shown | How it runs | What leaves the machine |
|---|---|---|---|
| **Frontier (default)** | "<model the provider reported>, picked by Friday", with her reason | A direct, pinned call: `_call_claude(..., model=...)` or `_call_openai(..., provider=..., model=..., fallback_models=None)`. Never `_generate_text`, which falls through to other providers. | The allowlisted payload below (about 40 numbers), through spend guard → `seal_outbound` (fail-closed) → `cost_meter` (`avatar_evolution`, so it appears in Costs) → `attribution.record_generation` |
| **A cloud model the user chose** | "<model the provider reported>, your choice" | The same pinned call, with the user's model; Friday does not pick | The same payload, the same chain |
| **A local model the user chose** | "<seat name>, on this computer" | One call through `local_call` under `local_only`, with the same payload, gated by `idle_work_blocked_reason` | Nothing |
| **Seeded (no model)** | "Friday, on this computer (no model)" | A deterministic mutation from `seed ⊕ step_number` and the weighted signals. The reason line is templated ("tighter lattice: a focused week"). | Nothing |

- **Every model author returns** `proposed_genome`, `rationale` (≤ 280
  characters, shown as the model's own words) and `name`. One reformat retry
  goes to the *same* model.
- **Credit for cloud calls** is the model the provider *reports* it used. If
  it differs from the one requested (a router substituting silently), both are
  shown, with a warning.
- **Every author works inside the same bounds and validators** (§4.2, §6), so
  the choice changes who proposes, never how far a step may go.

**The first time.**

- **New installs:** the announcement is part of first-run setup.
- **Existing installs (the owner's included):** a one-time notice at the first
  launch after this ships.

Either way, the announcement shows three things: the exact example payload,
the list of fields that are never sent, and a note that the cost shows in
Costs. It has one-click **Turn off**. `last_step_at` starts at the moment of
the announcement, so the first step comes a week later, and there is a week to
decide.

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
- **Connect a different model** is offered alongside it when the failure is
  the key or the consent.
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
   to the **cool identity moods**: IDLE, CALM, CURIOUS, FOCUSED and REFLECTIVE
   (`avatar_genome.IDENTITY_MOODS`). They do **not** apply to the **state
   moods** SPEAKING, EXECUTING, REASONING and LISTENING, which may take only
   the `saturation` and `luma` multipliers. Executing stays amber and speaking
   stays green, as the owner's rule requires.
   - **Found while building (2026-09-30).** Several v1 ambient moods already
     use status colours: PROTECTIVE's accent is the approve green `#00ff80`,
     SOCIAL is amber, CREATING and CREATIVE are pink and magenta. The genome
     leaves those five at their v1 colours rather than turn them further; whether
     they should change is a separate, owner-facing question.
   - **Turning keeps brightness.** A hue turn re-solves lightness so relative
     luminance is unchanged; blue at a fixed HSL lightness is far darker than
     cyan, and the lattice would dim as it turned.
   - **Checks are relative to v1.** Each colour must clear the reserved
     distance, contrast and colour-blind thresholds, or be no worse than its
     v1 colour already is.
3. **Reserved-hue distance.** No identity-mood base or accent may come within
   ΔE2000 20 of a reserved colour. The reserved colours are amber `#f59e0b`,
   approve `#00ff80`, deny `#ff0080`, and error red `#ff0033`/`#ef4444`. A
   candidate that fails is rejected like a frame-budget failure. So an idle
   Friday never looks like a pending approval.
   - **The one exception is the approval cube (§13.3).** It is drawn in the
     approval hue because it *is* the pending-approval signal. It appears
     only while a card is actually pending.
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
- **Measured baseline.** The page times calm frames all the time: nothing
  gesturing and the tab visible. The last 600 of them are the baseline for
  the look that is showing. That baseline is recomputed on every machine,
  never stored.
- **Live check after applying.** After a step's geometry is on screen, the
  page samples 600 calm frames. If their p95 is more than 10% slower than
  the baseline and more than 1 ms slower, it:
  - puts the previous look back;
  - asks the server to undo the step (`POST /api/avatar/undo`), which
    weights that kind of change down for the next steps;
  - says so in the status line: "That look was too heavy for this computer;
    I went back".

  Built as `FridayGenome.sample` and `held` in `index.html`.
- **Local brain residency.** The step never loads a model and never touches
  the GPU. Authoring is a cloud call. Only the *scene's* cost competes with
  the resident brain.
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
  "author": { "path": "cloud",
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
- **Author:** *Frontier (Friday picks)* (the default), plus each cloud model
  with a key, each local seat, and *No model (seeded)*. The choice sticks.
- the author line: "Made by <model>, picked by Friday" (or "your choice"),
  or, when the chosen author is unavailable, *"Waiting for a cloud model"*
  with **Connect a cloud model** and **Use <local model> instead**.

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
- a "What was sent" disclosure with the exact payload.

It can filter by model, and each step offers Restore, Hide and Delete.
Recently deleted steps are listed separately.

### 8.4 Voice: everything works by voice, with no voice-only limits

The owner's standing rule is that every feature works fully by voice. There is
one tool, `avatar_evolution`. It is registered once in `CLAUDE_TOOLS`
(`services/agent.py`) and named in `_VOICE_SHARED_TOOLS`
(`services/voice_engine.py:469-481`), exactly as `navigate_to` is, so text
and voice share one implementation and cannot drift. The action gate lists it
beside `navigate_to` (`governance/action_gate.py:164-166`): it changes only
the owner's own desktop.

| The user says | Action | Friday does and says |
|---|---|---|
| "Friday, evolve now." | `evolve_now` | Runs a step at once with the chosen author (§4.1). While the call runs she says "Asking <model> for this week's look." Then she describes the change (below). If the author is unavailable, she says so and offers both fixes: "I can open Accounts & Keys, or use <local model> instead. Which?" |
| "Use my local model for evolution." / "Let Claude do it." / "Go back to the default." / "No model." | `set_author` | Sets the author (any model the user has, `frontier`, or `seeded`) and says what she set: "From now on, <model> will make my looks." The choice sticks, exactly as on screen. |
| "Undo that look." | `undo` | Rolls back to the parent: "Done. I'm back to *<name>*." |
| "Go back to last month's look." | `rollback` with `when: "last month"` | Resolves the phrase to the step that was active on that date (a named step, "the one Claude made", or "the week of the 7th" also resolve). She switches, and says which look it was and who made it. A second "undo that" returns to where she was. Rollback is reversible, so she doesn't ask for confirmation. Where a phrase matches more than one look, she names the two closest and asks which. |
| "Turn evolution off." / "on" | `set_enabled` | Flips the switch: "Evolution is off. I'll keep this look." Turning it on while the cloud-consent question is unanswered makes her read the disclosure (about 40 numbers, never words, names or titles; the cost goes in Costs) and ask yes or no. The same card appears on screen. That is how the voice payload card already works. |
| "What changed?" / "Who made this look?" | `describe` | A spoken description of the active step, written for the ear. For example: "This week I got a little warmer, about four degrees, and my lattice pulled in tighter. There's a new ring for the fix I shipped on Tuesday. Claude Opus made it. I picked it because it hadn't shaped me since August." It names the model the provider reported. When she is waiting, she says what she's waiting for. |

- **Every spoken answer comes from the same record the notice shows**: the
  step's `diff`, `reason`, `author`. So voice can't say something the screen
  doesn't. The descriptions use words ("a little warmer", "about four
  degrees") rather than raw gene names, because Gemini speaks numbers as words.
- **A step started by voice** changes the scene only after she finishes
  speaking (§4.1), so her face never changes mid-sentence.
- **Tests:** each row has a text-path test and a voice-path test (the voice
  tool list declares `avatar_evolution`; its handler returns the same payload).
  "Last month" resolves correctly across a month boundary, and across a
  rolled-back tree.

---

## 9. Costs and failure modes

| | Estimate | Register |
|---|---|---|
| Cloud call size | ~1.5K tokens in, ~0.3K out, once a week | INFERRED from the payload shape |
| Cloud money | about 1-5 cents a week at current frontier prices; about $0.50-$2.50 a year | INFERRED; shown per step in Costs |
| Scene cost | at most v1 +10% elements; no new passes | bounded by schema; measured in §10.3 |
| Disk | ~3 KB per step plus a ~40 KB thumbnail; ~2.2 MB a year | INFERRED |

| Failure | What the user sees |
|---|---|
| The machine was off for weeks | One step on return; the notice says "caught up after 5 weeks away" |
| The chosen author is unavailable (for the default: no key, no consent, or local-only mode) | No step runs, and the author never changes by itself. One notice, "Evolution is waiting for a cloud model", with **Connect a cloud model** and **Use <local model> instead**, repeated at most weekly |
| The cloud call fails | A `skipped` entry naming the model and the reason, plus Try again |
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
- **Frontier by default, never a silent switch.**
  - A fresh `state.json` has `author: "frontier"`.
  - With no frontier key, or with consent unanswered or local-only: a due step
    does not run, `last_step_at` does not move, and no other author is called
    (asserted by spies). Exactly one waiting notice is raised per week, and it
    carries both actions. When a key appears, exactly one step runs.
  - "Use <local model> instead" sets `author` to that seat, persists it, and
    runs the waiting step with it.
  - Each author choice (a named cloud model, a local seat, `seeded`) survives
    a restart, and only that author is called.
- **One palette.** For a random genome, the colours `express(id)` returns are
  identical for all 13 structures. No structure section passes schema
  validation if it carries a colour field. The lifetime hue stays within
  ±30°, and never enters the green, amber or pink bands.
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

A headless script runs 1,000 seeds × 52 steps with synthetic signal weeks. The
author is a test stub that proposes random in-bounds changes; no model is
called. It reports:

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
6. Confirm the egress log shows exactly one line per step, naming the model,
   and that its payload matches "What was sent".
7. By voice: "evolve now", "what changed?", "undo that look", "go back to last
   month's look", "turn evolution off". Each spoken answer matches the notice.
8. With the frontier key removed in a scratch home: "evolve now" says it is
   waiting and offers both fixes, and nothing runs. Choose "use the local
   model instead": one step runs with the local model's credit, and the
   choice is still set after a restart.

---

## 11. Phased build plan

Effort is in focused agent-days, with review. Each phase is shippable alone.

| Phase | What | Effort | Depends on |
|---|---|---|---|
| **A0** | Scene safety, useful with or without evolution: the scene honours `prefers-reduced-motion`; `metamorphosisFlash` is capped to stay under the WCAG 2.3.1 threshold (none under reduced motion); time-lapse is off under reduced motion; `/api/evolution` bounds-checks `preferred_scene_index`; the freeze hook for screenshots | 1 | nothing |
| **A1** | Genome schema, seed, the 13 expression sections, the loader, seeded PRNG in the builders, the "empty = v1" screenshot and pipeline gates, the signed step store, the tree, `GET /api/avatar/genome`, and rollback | 4 | A0 (freeze hook) |
| **A2** | The authors: frontier by default (Friday's pick, pinned call, credit, "What was sent", costs), a user-chosen cloud model, a local model through `local_call`, and seeded; the sticky author setting; the waiting state and its two-action notice; nightly signal rows (off-record excluded), the catch-up job, "Evolve now", the reserved-hue / contrast / colour-blind / static-budget validators, the in-page frame check, and the simulated year with its contact sheet | 3-4 | A1 |
| **A3** | UI: the Evolve section in the scene menu and Settings, the first-run and existing-install announcement, the change notice with Undo, history (restore, hide, delete, trash), ask-first mode, and the pending dot. `index.html` and `ui_parts/` both | 3 | A2 |
| **A4** | Voice: the `avatar_evolution` tool in both registries, the spoken descriptions, relative-date rollback, and the voice-path tests (§8.4) | 2 | A2 (tool), A3 (for parity with the notice) |
| **A5** | Card export and import, and the share hook stopping before the market | 2 | A1 |
| — | Dream-rsi nightly scheduling (§5.4), a separate item | 4-5 (its own spec) | nothing here |
| — | Market plumbing and ratings (Appendix A) | after federation un-defers | — |

**Total for A0-A4: about 13-14 agent-days**, about three calendar weeks with
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
3. **A1-A4 wait for tonight's `index.html` work to land.** That is the
   Codex, workspace and podcast sessions. After it lands, A1-A4 can run
   alongside FridayWeaver-2. Training is GPU-bound and long; this is UI,
   bookkeeping and one cloud call. The §10.3 tokens-per-second gate should be
   measured once FridayWeaver-2 is the resident seat on Bonsai2, so it
   measures the brain users will actually run.
4. **CLM research and the Salon are independent of this.** Nothing here blocks
   them or is blocked by them. If they are commitments with dates, they go
   first.

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

**Replaced or confirmed by the owner on 2026-09-29:**

- "A frontier model makes each change, never a local one": **amended**. A
  frontier model is the **default** author, and the user may choose any model
  or none. The author never switches on its own; when it is unavailable, the
  step waits visibly (§4.1, §5.3).
- "Off by default": **replaced by on by default** (below).
- "Nightly or weekly": **weekly**, as recommended.
- "Moving backwards": **dissolved**. There is no worse look (§2.2).
- "Clock and pin": **resolved**. The four-day structure clock is untouched and
  separate (§3.1).

**Decided by the owner, 2026-09-29.** Verbatim: *"Evolutions on by default:
Yes, but only frontier models, and try to keep the color scheme consistent
across all the different 3D avatars please. You pick though."* Corrected the
same day on the author: *"those users should be able to run evolution on any
model they wish, but default to frontier"*. Where the owner said "you pick",
the calls were made as follows.

1. **On by default,** for new installs and for existing ones, the owner's
   included. Existing installs get the one-time announcement with one-click
   off, and the first step comes a week after it (§5.3).
2. **A frontier model by default; any model by choice.**
   - By default, Friday picks the frontier model and credits each look.
   - The user may switch the author to any model they have: a local model, a
     different cloud model, or the seeded author with no model. The choice
     sticks.
   - When the chosen author is unavailable (for the default: no key, no
     consent, or local-only mode), the step waits with a plain notice. It
     offers both fixes in one step: connect a cloud model, or use the named
     local model instead.
   - Friday never switches authors quietly on her own (§4.1, §5.3).
3. **One shared palette across all 13 structures,** so switching never changes
   her colours. Lifetime drift is ±30° from cyan (teal to azure), and never
   green, amber or pink, because those mean status (§3.2).

**Also required (2026-09-29):** every feature works fully by voice with no
voice-only limits (§8.4).

**Still open, not blocking:**

- the default licence for shared cards, and whether the rationale is included
  by default (2026-09-22 decision 8);
- the dream-rsi night window hours (§5.4).

---

## 13. Processing states: the lattice as an instrument

Approved by the owner on 2026-09-29, verbatim: *"I love it. approved."* This
is the processing-state vocabulary for the Genesis lattice and the other 12
structures. Code citations in this chapter are on main `2656aeca`.

### 13.1 The rule

**Every state is driven by a real event from Friday's own loop, and the same
event always makes the same move.** The lattice is an instrument the owner
learns to read. It is never decoration and never faked.

- **No event, no motion.** A gesture starts only when its event arrives, and
  ends when that event's end arrives, or when a stated timeout expires. The
  gesture engine has no timers of its own that invent activity. At rest the
  lattice is still.
- **Counts are counts.**
  - One cube per source actually retrieved.
  - One wave layer per agent round, the same round number the status line
    prints.
  - One twist per tool call.
  - One orbiter per background job.
  - Build progress is the job's reported fraction.

  Where the loop reports no fraction, there is no build (§13.3).
- **The owner's motion rule, restated.** The standing rule was that motion
  means Friday is talking (§1.4, `index.html:5133-5153`). With this approval
  it becomes: **motion means a real event is happening.** Speaking is one
  such event. Idle breathing, ambient drift and a faked "busy" pulse stay
  refused (§3.2). The EXECUTING colour shift stays as it is.
- **Dormant until real.** Some states have no event today (§13.5). Those
  gestures are built, but wired to nothing. They stay dormant until the event
  exists. Nothing borrows a nearby signal to look busy.

This is north-star §6.8 (line 385): *"MUST NOT imply that a graph, orb,
avatar state, or reasoning path is evidence unless it is derived from actual
trace data"*. It is also amendment A1: "Motion never poses as evidence."

### 13.2 Anatomy: one language, thirteen bodies

Every gesture is written against five anatomy slots. Each structure maps
the slots onto its own geometry, so switching structures never changes the
language (the owner's guardrail). Only the rendering changes.

| Slot | Meaning | CUBES (richest) | Other structures |
|---|---|---|---|
| **unit** | the smallest movable piece | one cube of the 3×3×3 grid (`coreCubes`, `index.html:4739-4752`) | ICOSAHEDRON: a vertex cluster on a shell. NETWORK: a node. DOME: a crystal. ASTROLABE: a ring. TESSERACT: a vertex. QUANTUM: a loop. MANDELBROT: a band of points. MOBIUS: a strip segment. GRID: a row of the ocean. CABLES: a tube. NONE: a line. EDEN: a tile of the Rez boss (§15.5) |
| **block** | a group of units that turns together | a 2×2 block of cubes | a pair of adjacent units, or one ring (ASTROLABE) |
| **layer** | an ordered slice that a wave passes through | one of the 3 grid layers (a 4th after growth) | shells, ring index, band index, or depth rows. Each structure lists its layers front to back |
| **core** | where things arrive and settle | the centre cube position | the innermost shell, hub or centre point |
| **face** | the side toward the user, or the side that opens | the grid face nearest the camera | the camera-facing hemisphere, ring arc or segment |

The shell is the outer layer. The 800-point background particle field is
**not** part of the anatomy. It never carries meaning, so it can't be
mistaken for a signal.

Each structure implements the slots in one adapter:
`structureAnatomy[id] = {units(), blocks(), layers(), core(), face()}`. The
gesture engine never names a structure. A structure with no adapter shows
only the status line and colour; that is the flat equivalent (north-star
§21.22, line 3219).

### 13.3 The vocabulary

"Event" names the real signal. §13.5 says where each one comes from today, or
that it doesn't exist yet. "Reduced motion" is the swap required by §13.7.

| State | Gesture (on CUBES) | Event | Count or measure | Reduced motion |
|---|---|---|---|---|
| **Listening** | Rings ripple across the facing side toward the user, timed to the user's voice amplitude | Mic level during a voice session or push-to-talk | Ripple amplitude = mic level. No voice, no ripple | The face brightens with the mic level (smoothed, ≤ 2 Hz) |
| **Memory or KG search** | Inner cubes light up and drift to the core, **one per source actually retrieved** | Retrieval finished, with a count per layer | n cubes = n sources (capped at the unit count; the status line gives the true n) | Those n cubes brighten in place |
| **Reasoning** | A slow wave rolls through the lattice, **one layer per round** | Agent round n started | Wave index = round number = the status line's "round N" | The layer for round n brightens, and the previous one fades |
| **Where the thinking happens** | **Local:** the wave stays inside. **Cloud:** the top opens like a vent, and a thread of light rises out of it. It is the top, not the far side: a vent on the far side would be hidden behind the structure from the camera | The round's routing decision (local seat or cloud provider), **and** the egress gate actually sealing an outbound call | Vent opens on the egress event, never on the routing guess alone | The face brightens and a static thin line appears; no opening motion |
| **Tool call** | A 2×2 block twists 90° like a Rubik's move and snaps back when the tool returns. **One twist per call** | Tool call started, then tool call returned (same `call_id`) | One block per in-flight call. Up to 4 twist at once; more calls queue visibly (§13.6) | The block brightens until return |
| **Laya reflex or instant command** | A tiny, fast snap twist of one cube | A reflex-class decision, or an instant command fast path, ran | One per command | One cube pulses once (brightness, no motion) |
| **Waiting for approval** | One cube steps forward out of the grid and holds, gently breathing, until the user decides. It uses the **approval hue** | An approval card is pending, then resolved | One stepped-out cube per pending card, up to 3; the status line gives the count | The cube lights in the approval hue and holds, with slow brightness breathing (≤ 0.3 Hz, ≤ 15% amplitude) |
| **Blocked or needs input** | The lattice freezes mid-twist with one face turned | A typed blocker was raised, then cleared | One frozen turn | All units dim to 60%, except one face |
| **Verifying** | A scan plane sweeps through, and each cube it passes locks with a tiny settle | Verification started, then finished, with a result | One sweep per verification pass | A slow brightness sweep (≤ 1 pass per 2 s) |
| **Long generation** | The lattice builds layer by layer, filled in proportion to **true** progress | A process registered with a real `progress` fraction | Filled units = round(progress × units). No fraction, no build: the status line shows stages instead (north-star §29.3) | Filled units lit, the rest dim |
| **Saving to memory** | A cube drifts into the core and dims into place | A memory fact was written (`ingest_fact` succeeded) | One per fact | A cube at the core brightens, then dims |
| **Private handoff** | The outer shell frosts while the core works, then one small cube floats out. That cube is the scrubbed summary, the only thing that leaves | Local-context request started; later, the scrubbed text was actually sent | Frost on request, and the float-out only on the send event | Shell opacity up during the work; one small static cube at the edge on send |
| **Error** | A cube knocks out of alignment and slowly corrects. **No red, no flash** | A turn, tool or process ended in error | One knock per error | A cube dims to 40%, then recovers over 2 s |
| **Background or scheduled work** | One faint cube orbits the lattice | A scheduled or background process is registered, then ends | One orbiter per process, up to 4 (a count after that) | A faint static satellite, one per process |
| **Subagents or helpers** | Small clusters split off and return | A subagent task started, then ended, with a parent in this turn | One cluster per live subagent, up to 4 | Clusters shown as dimmed satellite groups |

**Speaking** keeps its existing behaviour (§1.4). It layers on top of
whatever else is showing.

### 13.4 How the other 12 structures say it

**As built (2026-09-30).** Three adapter families map each structure onto
the §13.2 slots:
- **objects:** shells, rings, pillars and crystals, tubes, lines and loops;
- **point clouds, split into stable clusters:** network, Mandelbrot,
  Mobius and ocean;
- **the tesseract's sixteen corners**, with their edges following;
- **Giga Earth's tiles** (§15.5): its own adapter over the Rez boss.

The lattice's own cube turns amber for an approval. On every other
structure, a waiting approval is one warm light in the approval hue at the
unit nearest the middle of the screen:
- it is drawn over the structure, so a solid shape in front cannot hide it;
- it uses normal blending, so it stays amber over bright geometry.

Every gesture was captured on all thirteen structures and the frames were
looked at. Transcendence, the network and the small tesseract are the
faintest.

The gestures are defined on slots (§13.2), so every structure expresses them
automatically. Four need explicit renderings, because their slots have no
natural twist or step:

- **ASTROLABE.** A tool call tilts one ring 90° about its own axis and back.
  Waiting for approval pushes one ring toward the user.
- **NETWORK.** A tool call swaps the positions of two linked nodes and swaps
  them back. Waiting for approval brings one node forward, with its links
  held.
- **QUANTUM.** A tool call rotates one loop's phase a quarter turn. The
  build fills loops.
- **GRID** (the ocean). A reasoning wave is one swell per round, rolling
  away from the user. The vent is a gap in the far edge, with a thread rising
  from it.

The per-structure tables go in the adapter's code comments. Each structure
has a test that every gesture yields some change on it (§13.10).

### 13.5 Where each event comes from

Reuse, don't add. The page already has one cross-tab stream: the approvals
feed.

- **Server:** `GET /api/approvals/events` (`routes/goals.py:239`) streams
  whatever `approval_feed.publish()` (`services/approval_feed.py:69`) sends.
- **Client:** `fridayApprovalFeed` (`index.html:50709`). One tab holds the
  Web Lock `friday-approvals-feed` and the EventSource, and relays every frame
  to other tabs on `BroadcastChannel('friday-approvals')`.

Processing events ride **the same connection** as a new frame type,
`{"type":"presence", ...}`.

- **Server.** A small `services/presence.py` validates each event against an
  allowlist, then calls `approval_feed.publish`.
- **Client.** `apply()` ignores unknown types today. It gains a second
  listener set, `onPresence(fn)`, which the scene subscribes to.

No tab opens a new connection. Presence frames are **not** added to the
approvals snapshot; they are momentary.

**The presence frame:**

```json
{"type": "presence", "state": "tool", "phase": "start",
 "turn": "<turn id>", "call": "<call id>", "n": 1, "of": null,
 "route": "local|cloud|null", "at": 1727640000.123}
```

- It carries **no text**: no tool arguments, titles, queries, model output,
  or names.
- `state` and `phase` come from fixed enums. `n`/`of` are integers; `route`
  is an enum.
- A test pins the allowlist, the same pattern as the §5.3 payload.
- Frames are never written to disk, so off the record writes nothing
  (main `0597f9e2`).

| State | Real event today | Where it is raised (server) | Reaches the page today | Status |
|---|---|---|---|---|
| Listening | Mic peak during live voice | Browser only: `v.micPeakRecent` → `setMicLevel` (`index.html:52578-52631`) | React state only; no window signal | **Wire:** publish `window._fridayMicLevel` next to `setMicLevel`. It is the same tab, so no server frame is needed |
| Memory or KG search | Retrieval inside `_build_context_prompt` (`services/model_router.py:3458`), plus `search_wiki` and memory tool calls | Only layer names in the end-of-turn `sources` | No count anywhere | **Instrument:** count the results per layer there, and emit `retrieval` with `n`. Dormant until then |
| Reasoning | Round n: `process_update(step_n=…)` (`agent.py:10360`, `10910`, `11153`) → `turn_pet` (`core/__init__.py:1757-1790`) | `turn_liveness` (`core/__init__.py:1833`), polled only after 90 s | Only on long turns | **Emit** `round` from `process_update` when `step_n` changes. It is the same number `fridayTurnStatusText` (`index.html:8738`) prints |
| Where the thinking happens | Route: the trace `model_call` (`services/reasoning_trace.py:364`, with seat and model). Egress: `egress_gate.seal_outbound` (`services/egress_gate.py:1521`) sealing a real outbound call | Chat `done` payload `served_by` (unused client-side). Voice `egress_notice`/`egress_receipt` (`routes/voice.py:3875`, `4045`) | Route after the fact; egress only in voice | **Emit** `route` per round from the `model_call` site, and `egress` from `seal_outbound` on an actual send (not on local, not on a blocked call). **This touches `egress_gate.py`, a sensitive subsystem: extra review.** The vent opens only on `egress` |
| Tool call | `announce_tool` (`services/model_router.py:1065`), `_orb_tool_trace` (`services/agent.py:9991`) | Chat stream `{tool}` (start only, sending tab only). Trace feed polled | Partial | **Emit** `tool` start and end with `call_id` at those two sites |
| Reflex or instant | Chat fast paths: nav intent and open-path (`routes/chat.py:870-894`). Laya is a shadow scorer (`services/laya_backend.py`) | Nothing | None | **Emit** `reflex` from the fast paths. Laya stays dormant until it decides anything live |
| Waiting for approval | `card_pending` / `card_resolved` (`services/approval_feed.py:80-84`) | The same feed; `window.__fridayPendingApprovals` | **Yes, today** | Use as is |
| Blocked | Typed blockers (`goals-and-delivery-receipts.md`; not built) | — | None | **Dormant** until typed blockers ship |
| Verifying | Task evidence gate (`services/agent.py:3875-3878`); delivery receipts (not built) | Task status via `/api/tasks` | After the fact | **Emit** `verify` start and end at the evidence gate. It widens when receipts ship |
| Long generation | `process_update(progress=)` from `local_image.py:1067`, `local_video.py:684`, `creative_pipeline.py:806` | `/api/processes` poll, 2 s | Yes, polled | **Emit** `progress` on change. Podcast and report have no progress today, so they stay dormant (stages only) |
| Saving to memory | `ingest_fact` (`services/knowledge_graph/integration.py:89`) → `node_ignited` | KG SSE; only the Knowledge view listens | Not to the scene | **Emit** `memory_saved` at `ingest_fact` success |
| Private handoff | `local_context.request` (`services/local_context.py:247`) and `_send` (`:224`) | The card via the feed; the send emits nothing | Card only | **Emit** `handoff` start at `request`, and `handoff` sent at `_send` success |
| Error | Chat stream `{error}` (`routes/chat.py:771`); process error; voice `error` | Various, sending tab only | Partial | **Emit** `error` at turn end with failure, and at process error |
| Background or scheduled | `scheduler.dispatch` (`services/scheduler.py:1000`) → `sched-*` process | `/api/processes` poll | Yes, polled | **Emit** `background` start and end at `process_register` for background categories |
| Subagents | `_spawn_task` (`services/agent.py:4225`); the worker end | `/api/tasks` poll; trace parents | Polled | **Emit** `subagent` start and end with the parent turn |

### 13.6 Precedence and blending

States overlap; for example, a tool twist during reasoning. The engine
treats the body as **four channels**. Each gesture claims one:

| Channel | Gestures | Rule |
|---|---|---|
| **Whole body** | blocked freeze; long-generation build; verifying scan; reasoning wave | One at a time, by priority: **blocked > build > verify > wave**. A lower one resumes where it was when the higher one ends. A freeze holds every channel except approval |
| **Units** | tool twist; reflex snap; memory gather; saving; error knock | Additive on **disjoint** units. The allocator never gives one unit two gestures. Twists take blocks away from the facing side; the gather takes inner units. Excess tool calls wait in a visible queue, shown by a small count in the status line, and twist in order, so it stays one twist per call |
| **Shell and face** | listening ripple; cloud vent and thread; handoff frost | Listening owns the facing side while the mic is live. The vent uses the face opposite the user when listening is live. Frost overrides the vent: during a handoff nothing else leaves |
| **Satellites** | background orbiter; subagent clusters; the approval cube | Always additive. The approval cube is in front and outranks everything for attention. It keeps breathing during a freeze, because the user's decision is what unfreezes |

- **Speaking** multiplies the existing speech energy on top of all channels.
- **Tempo under load.** When more than 3 unit gestures start within a
  second, their durations compress, down to 40% at most, so bursts stay
  readable. The count is never merged: every event gets its gesture.

### 13.7 Guardrails

- **Status line and spoken equivalent.** Every state has both (§13.8). The
  lattice never carries information that the words don't (north-star §33.3,
  line 5077; §21.22, line 3219).
- **Reduced motion.** Each gesture has the §13.3 swap: brightness or opacity
  only, with no translation or rotation. It uses the `SceneMotion.reduced()`
  switch from phase A0 (`<scene-motion>` block), and follows it live.
- **Palette.**
  - Every gesture uses the genome's identity palette (§3.2, §6.2).
  - **The approval cube is the one sanctioned exception:** it uses the
    approval hue (amber `#f59e0b`, §1.3). Nothing else may.
  - Error uses no red, and the cloud thread uses the palette accent, not a
    warning colour.
  - The reserved-hue rule in §6.2 is amended to say: *except the approval
    cube, which is the reserved signal itself.*
- **No flashes.**
  - A gesture's brightness change is capped per frame, by the same luminance
    step limit as §6.3.
  - Repeated events coalesce their *brightness* so the scene never
    oscillates above 3 Hz. Their *count* still shows.
  - Snaps and knocks move geometry; they don't brighten it.
- **Budget.**
  - Gestures move existing meshes: twists, steps and orbits are transforms
    on `coreCubes`. They add no geometry except:
    - one thread line (≤ 64 vertices);
    - one scan quad;
    - an orbiter and cluster pool (≤ 16 small meshes).

    That is well inside §6.4's +10%.
  - The engine's target is ≤ 0.3 ms of CPU per frame for 20 simultaneous
    gestures (UNMEASURED; §13.10 measures it).
- **All 13 structures** speak the language through the adapters (§13.2,
  §13.4).
- **Evolution changes style, never meaning.** The genome may set three style
  genes, clamped like the others (§3.2):
  - `gesture.tempo`, 0.85-1.15;
  - `gesture.ease`, one of spring, snap or glide;
  - `gesture.trail`, 0-0.5.

  It may **not** change which event makes which gesture, the counts, the
  direction of the vent (outward means leaving), or the approval cube's
  step-forward. A test runs every gesture under the genome extremes and
  checks that the meaning (units moved, count, direction) is unchanged.

### 13.8 Status lines and spoken equivalents

- **Where the status line appears.** In the scene's HUD (`#mood-text`,
  `index.html:4556-4562`), and in the chat status where one exists.
- **The spoken form.** It goes through `check_situation` (`voice_engine.py`,
  voice shared tools `:469-481`), the path the voice contract names for "what
  are you doing". It follows the contract's manners: short, concrete, a
  sentence and not a table, and never claiming an outcome before it is
  verified (north-star §22.3, line 3273; §6.5).
- **Content.** Neither form carries sensitive content (north-star §22.5,
  line 3292): tool names are allowed, arguments are not.

| State | Status line | Spoken (on "what are you doing?") |
|---|---|---|
| Listening | "Listening" | (not spoken; she is listening) |
| Memory search | "Found 4 sources" | "I pulled four things from memory." |
| Reasoning | "Thinking, round 3" | "I'm on my third pass." |
| Local vs cloud | "Round 3 on this computer" / "Round 3 sent to <provider>" | "This part's staying on your computer." / "I've sent this part to <provider>." |
| Tool call | "Using search_files (2 waiting)" | "I'm searching your files, then two more steps." |
| Reflex | "Opened Settings" | (the command's own confirmation) |
| Approval | "Waiting for your OK (1)" | Per the contract's four-part card read-back |
| Blocked | "Blocked: needs your input" | "I'm stuck until you tell me <blocker type, in plain words>." |
| Verifying | "Checking the result" | "I'm checking it worked before I say it did." |
| Long generation | "Rendering 42%" / "Rendering: stage 2 of 4" | "About forty percent through." / "On stage two of four." |
| Saving to memory | "Saved to memory" | "I've saved that." |
| Private handoff | "Working privately on this computer" → "Sent a scrubbed summary" | "I'm doing that part on your computer; only a scrubbed summary will go out." |
| Error | "That step failed; retrying" / "…; stopped" | "That step failed. <what next>." |
| Background | "2 jobs in the background" | "Two things are running in the background." |
| Subagents | "3 helpers working" | "Three helpers are on it." |

### 13.9 Build plan for processing states

It starts only after phase A0 is on main, because it reuses A0's
`SceneMotion` and flash limits.

| Phase | What | Effort |
|---|---|---|
| **PS1** | Presence frames: `services/presence.py` (allowlist, enums, no text), the `approval_feed` relay, and client `onPresence`. The emit points that exist today: round, route, egress, tool start and end, approval (already there), progress, memory saved, handoff, error, background, subagent, reflex. Tests pin each emit point to its event (§13.10) | 3 days |
| **PS2** | The gesture engine: slots, the four channels, precedence, the allocator, the reduced-motion swaps and flash limits. The CUBES adapter, the richest one. The HUD status line | 4 days |
| **PS3** | Adapters for the other 12 structures, including the §13.4 four. Frame captures on at least three structures | 3 days |
| **PS4** | Spoken equivalents through `check_situation`. Voice parity tests. Mic level for listening | 1-2 days |
| **PS5** | The instrumentation that doesn't exist yet: retrieval counts in `_build_context_prompt`, verify events, and the podcast/report stages. Blocked stays dormant until typed blockers ship | 2 days, plus waiting on goals |

About 13-14 days.

### 13.10 Verification

- **Each state fires only on its real event (server).** For every emit point,
  one test drives the real code path and asserts exactly one frame of the
  right state. For example: `_orb_tool_trace` with a finished call emits one
  `tool/end` with the same `call_id`. A second test drives the neighbouring
  path that must **not** emit. Examples:
  - a local seat emits no `egress`;
  - a blocked `seal_outbound` emits no `egress`;
  - a process with no progress emits no `progress`;
  - `ingest_fact` failing emits no `memory_saved`;
  - an off-record turn's frames carry no text (the allowlist test).
- **Each gesture happens only on its event (client).** Under node, the engine
  runs 600 frames with no events: every unit's transform is unchanged
  (motion needs an event). Then each event type runs alone: exactly the
  expected units move, the expected number of times. For example, 3
  `tool/start` frames give 3 twists, and 3 `tool/end` frames bring all 3
  blocks home. Both UI files are run, as in `tests/unit/test_scene_motion_safety.py`.
- **Meaning under evolution.** Every gesture is run at the genome's style
  extremes. Units moved, count and vent direction are identical.
- **Frames you look at.**
  - **How it's driven.** Playwright uses the tree-swapped page (the §10.3
    recipe) and feeds the real client a scripted
    `/api/approvals/events` stream of presence frames by routing the SSE
    endpoint. There is no inject hook in the page.
  - **What's captured.** For each of the 15 states, on CUBES, ICOSAHEDRON and
    ASTROLABE: a 3-frame strip (start, middle, end) and the per-frame
    luminance record. With reduced motion on, the same strips.
  - **Reviewed.** A person looks at every strip. That review, and the program
    lead's visionOS and "unmistakably Friday" critique, gate the merge.
  - **Measured.** The flash check (§10.3) runs over the whole sequence.
- **Budget.** CPU per frame for 20 simultaneous gestures, and fps, on the
  owner's machine with the resident model loaded. Recorded here with the
  date.

---

## 14. North-star mapping

The target is `docs/design/north-star/agent-friday-ideal-product-spec.md`.
The owner's rulings on it are in `AMENDMENTS.md`, and the requirement IDs are
`GAP-MATRIX.md` rows. Where a ruling and the spec differ, the ruling wins.
This spec's build phases (A0-A5, PS1-PS5) are not amendments; amendments
below are written "amendment A<n>".

### 14.1 What this spec implements

| North star | Requirement | Where this spec meets it |
|---|---|---|
| §6.8, NS-6.8-1/2; amendment A1; NS-21.22-4 | Avatar states only from trace data; motion never poses as evidence | §13.1 (no event, no motion; dormant until real); §13.5 (the event for each state); §3.2 (idle-motion genes refused) |
| §29.1, NS-29.1-1; §29.4 | The owner can always answer "what is Friday doing, which model, did anything leave" | §13.3 vocabulary; §13.8 status lines; the task-journal events §29.4 lists map onto §13.5 |
| §12.3, NS-12.3-2; §12.5; §6.4, NS-6.4-6; §33.6 | Route shown; fail-closed egress; fallbacks visible; zero unannounced egress | §13.3 "where the thinking happens": the vent opens only on a real `seal_outbound` send; §5.3 the author never switches silently |
| Amendment A4, NS-22.2-2 | No raw private data to a cloud voice model | §13.3 private handoff: frost while local, one cube out only on the scrubbed send |
| §29.3, NS-29.3-1 | No invented percentages | §13.3 long generation: a fraction or stages, never a guess |
| §6.5; §22.3, NS-22.3-1/2; §21.23 | "Done" is verified; honest, specific copy | §13.3 verifying; §13.8 lines never claim before verification |
| Amendment A4; §22.1; §22.2; §33.3, NS-33.3-6 | Voice parity; critical information visual and audible | §8.4; §13.8 spoken equivalents through `check_situation` |
| §33.1, NS-33.1-9; §33.2, NS-33.2-1; §21.22, line 3219 | WCAG 2.2 AA; reduced motion for avatar motion; a flat equivalent | Phase A0 (`SceneMotion`); §6.3; §13.7 swaps; the status line as the flat equivalent |
| §18.4, §22.5, NS-22.5-1/2; §33.4 | Approval cards; nothing sensitive spoken | §13.3 approval cube (the only approval-hue element); §13.8 contract read-back |
| §17.14, NS-17.14-2; §29.2; §11.4 | Subagents and background work visible | §13.3 subagent clusters and orbiters from real process and task events |
| §17.8 | Typed blockers | §13.3 blocked (dormant until goals ship) |
| Amendment A1; amendment A2; amendment A5; LEDGER check 3 | The scene is how Friday is recognised; weekly evolution; one brand; "unmistakably Friday" | §3 one genome; §13.2 one language on every body; §13.7 evolution changes style, never meaning |
| §33.4, §33.7, §34.15 | Responsive UI; budgets; GPU contention tests | §6.4; §13.7 budget; §10.3, §13.10 measured |
| §15.8 | Off the record indicates what is still recorded | §5.1; §13.5 presence frames carry no text and are never written |

### 14.2 Where this spec amends it

Nothing here departs from the north star, so no new amendment is recorded.
Two notes:

- **§6.8's "decorative animation clearly decorative".** This spec keeps no
  decorative gesture. The existing ambient MOODS (NS-6.8-2) are outside this
  spec.
- **No flash rule.** The north star has no explicit flash rule. This spec
  anchors its rule on §33.1 (WCAG 2.2 AA, which includes 2.3.1).

---

## 15. Giga Earth: the Rez track

**Status:** built (2026-09-30), on branch `feat/avatar-lattice-gestures`.

The owner's request (2026-09-30, verbatim):

> "For Giga Earth (Rez), we're going to do some refinement. This one will not
> evolve with the frontier models. This one will only evolve on a set track,
> because it is a reference to the videogame Rez for Dreamcast. […] The disco
> ball has several forms that reveal themselves as the player blasts away the
> tiles on the surface of the ball. Underneath is an x-shaped robot that
> begins manipulating the tiles in a variety of ways, including arms that
> swirl around, and rings that throw projectiles at the player. These will all
> be part of the expression set of this avatar mode. BTW: Do keep our branding
> and color schemes for the enhanced Giga Earth avatar, but also try your best
> to make it look as close to the Rez boss as possible."

### 15.1 In plain words

Giga Earth is now the Area 1 boss from Rez, drawn in Friday's own colours:
- a ball of 200 tiles in five sections that turn against each other;
- tiles are blasted away over the weeks, and an X-shaped robot is revealed;
- its loose tiles swirl in arms, and rings carry tiles it can throw.

It changes only along a **set track** of seven forms. No model, local or
cloud, ever proposes a change to it.

### 15.2 The track

| Stage | Form | What is on screen |
|---|---|---|
| 0 | **Sealed** | The whole ball. This is v1, what every install starts with |
| 1 | **Cracked** | About 15% of the tiles blasted away in patches. A faint core glow shows through |
| 2 | **Lock-on** | About 30% gone. A white octagonal lock-on frame sits round the core |
| 3 | **Unveiled** | Half gone. The X-shaped robot shows: four glassy blades round a faceted core |
| 4 | **Arms** | The blasted tiles come back as four spiral arms swirling round the robot |
| 5 | **Rings** | Two tilted rings orbit, each carrying six tiles it can throw |
| 6 | **Final form** | Only about 10% of the ball is left. The robot opens into an eight-point star, with the arms and rings round it |

The track only ever takes tiles away. A tile blasted at one form stays
blasted, or swirls in an arm, at every later form.

### 15.3 How it evolves

- **When it moves.** A step moves Giga Earth one form along its track only
  while Giga Earth is the structure on screen (`current_structure()`, as for
  every structure). That covers the weekly step, catch-up after time away
  (one step), and "evolve now" by button or voice. After the final form, a
  step is skipped with "Giga Earth is already in its final form".
- **No model.** It runs with no cloud model and no local seat. The author
  setting (frontier, a chosen model, seeded) does not apply, and it never
  waits for a model. Nothing leaves the machine: `sent` is `null`, and the
  input digest is a hash of the track position.
- **Everything else stays the same.** The step is signed like any other
  (§7). It appears in the history as "Giga Earth: *Form*", credited to "the
  set track". It can be:
  - undone;
  - rolled back to by name or date;
  - reset;
  - held for approval in ask-first mode.
  Evolution off means no weekly step. The owner can still move it by hand.
- **No model reaches it.** `clamp_step` never moves Giga Earth's section:
  - not on a step for another structure;
  - not when a model proposes while Giga Earth is on screen.
  Giga Earth no longer has any count gene, so the shared `form/density`
  gene does not change it either. Its one gene is
  `structures/EDEN/stage` (0-6, marked `track`), and only `track_step` moves
  it.
- **Built as:**
  - `avatar_genome.TRACKS`, `track_step` and `track_form`;
  - `avatar_growth._track_step`;
  - voice replies such as "Giga Earth moved on to its unveiled form. It
    follows Giga Earth's set track, so no model was asked."

### 15.4 The look: Rez's shapes, Friday's colours

- **Colours come from the palette, never the game.** The Rez boss is orange
  and white. Here:
  - tiles are silver tinted with the mood's base colour, each tile its own
    shade;
  - blades, core, rings and the core glow take the accent colour;
  - the lock-on frame is white.
  The shared palette (§3.2, §6.2) applies to Giga Earth like every other
  structure. It follows moods and drifts with the genome.
- **Amber is only ever an approval.** The old boss sphere was orange
  (`0xff6600`), close to the reserved approval amber. It is gone.
- **Every install cracks differently.** The patches that open first are
  seeded from the install's sigil (`FridayGenome.rand('EDEN')`). Every
  install follows the same track but breaks open in its own way.
- **Kept from before:** the tunnel, the vertical rails (the "spines", now a
  fixed 15), the white player figure (reserved as "you"), and the debris.
- **Drawing:**
  - the tiles are one instanced mesh (one draw call);
  - the lights sit inside Giga Earth's group, so they are gathered only
    while it is on screen;
  - the element budget is the same at every form, because each form shows
    or places the same parts.

### 15.5 The expression set

The events and the rules are §13's. On the boss, the same events make Rez
moves:

| Event (§13.3) | On Giga Earth |
|---|---|
| **Speaking** (§1.4) | The ball swells slightly with the voice. The sections, arms and rings turn faster |
| **Listening** | Tiles on the side facing the user ripple with the voice |
| **Reasoning, one round** | One section of the ball lifts and glows. From the Arms form on, the rounds also roll through the arms, one arm per layer |
| **Tool call** | A 2×2 block of tiles flips 90°, like the robot turning tiles over, and flips back when the tool returns. On an arm, it is four neighbouring tiles along that arm |
| **Cloud send** | The top of the ball opens and the thread rises. From the Rings form on, a ring also throws one tile out along the thread |
| **Waiting for approval** | The tile nearest the middle of the front steps forward in the approval amber, inside an amber octagonal lock-on frame, the Rez lock-on. The frame follows its tile as the ball turns and fades when the approval is decided |
| **Verifying** | The scan plane sweeps across the boss |

At rest, the sections turn against each other, the arms swirl and the rings
spin at the structure's idle rate. That is the same rate the old sphere
turned at, so the rest motion is the baseline (§1.4), not busy motion. It
goes faster only while Friday speaks. A tile is thrown only on a real cloud
send; nothing is thrown on a timer.

Under reduced motion nothing turns. The gestures are brightness only (§13.7),
and no tile is thrown.

### 15.6 Verification

- **Tests:**
  - `tests/unit/test_avatar_rez_track.py` covers the server side (8 tests).
    They failed before the change and pass after it.
  - `tests/unit/test_rez_boss.py` runs the page side under node with the
    vendored three.js, from both scene files. It checks that:
    - every form builds with finite geometry;
    - blasted tiles never return;
    - the pattern is per install;
    - the robot appears from Unveiled and the rings from Rings;
    - a block never straddles two sections or an arm;
    - nothing turns under reduced motion.
  - Four deliberate breaks of the boss (tiles returning, the robot on the
    sealed ball, turning under reduced motion, a block across two sections)
    each turn a test red.
- **Captures:** headless Chrome with `--use-gl=angle` (§10.3):
  - every form captured and looked at;
  - every gesture captured and looked at on the Unveiled and Final forms.
- **Brightness and frame time, per frame:**

  | Form | Largest step up | Largest step down | Steps over 20% | p95 frame |
  |---|---|---|---|---|
  | Unveiled | 1.10× | 0.88× | none | 20.8 ms |
  | Final form | 1.12× | 0.91× | none | 19.0 ms |

- **Not built:** the §13.3 states that are not built anywhere yet
  (subagents, background work, errors, blocked, saving to memory, private
  handoff). Giga Earth will express them when the engine does, without
  changing this section.

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
