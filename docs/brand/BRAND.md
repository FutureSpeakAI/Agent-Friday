# Friday brand

Status: current. This page describes the brand as it ships. It codifies what
the product already does; it does not propose a new look. Where the shipped UI
disagrees with a rule on this page, the disagreement is listed under
[Known debt](#known-debt), not hidden.

One source of truth per medium, and nobody keeps a copy:

| Medium | Source of truth | Guard |
|---|---|---|
| Python | `src/agent_friday/brand.py` | `scripts/check_brand_tokens.py` |
| UI | the `--fr-*` `:root` block in `index.html` and `ui_parts/head.html`, generated from `brand.css_root_block()` | same script; the block must be byte-identical to the generated text (`--write` regenerates it) |
| People | this page | `tests/unit/test_brand_tokens.py` requires every token to be documented here |

To change a colour: edit `brand.py`, run `python scripts/check_brand_tokens.py
--write`, update the table below. The guard fails until all three agree.

## Palette

Cyan is the brand. Everything else either completes the holographic triad or
carries a fixed meaning.

| Token | Value | Use |
|---|---|---|
| `--fr-cyan` | `#00d4ff` | Primary. Focus, active, links, the wordmark's "AGENT FRIDAY". |
| `--fr-cyan-soft` | `rgba(0,212,255,0.12)` | Selected and hover ground behind cyan. |
| `--fr-violet` | `#7b61ff` | Triad, middle stop. The colour of work in progress. |
| `--fr-magenta` | `#ff00ff` | Triad, last stop. |
| `--fr-violet-soft` | `#a78bfa` | Category accents beside the triad (business news, the FutureSpeak lane, the career calendar). |
| `--fr-cat-teal` | `#2dd4bf` | Science news. |
| `--fr-cat-pink` | `#f472b6` | Media news, the family lane. |
| `--fr-cat-blue` | `#60a5fa` | Politics news. |
| `--fr-cat-sand` | `#d6c7a1` | Local news, the finance lane. A warm neutral, deliberately not amber. |

The triad is a gradient: cyan, then violet, then magenta. It is the fill for
icons, the dock and the window sweep. `#00ffff` remains only in the HUD arcs
and the cursor.

The two category tokens are colours the workspaces already used, given names.
They are scoped decoration, not part of the status language.

### Reserved status hues

Four hues carry status. The values are fixed, and they are the values the
product shipped before the tokens existed.

| Token | Value | Meaning |
|---|---|---|
| `--fr-ok` | `#00ff80` | Done, connected, approved, speaking. |
| `--fr-warn` | `#f59e0b` | Needs you: waiting on an approval or a decision. |
| `--fr-deny` | `#ff0080` | A rule refused it, or you said no. |
| `--fr-error` | `#ef4444` | Something failed. |
| `--fr-neutral` | `#7a8699` | Disconnected, idle, unknown. No opinion. |

**The semantic rule.** A failure is error red. A refusal is deny magenta.
Amber only means "needs you". Violet = working: thinking, running and
connecting are violet, because work in progress needs nothing from you.
Status is never carried by colour alone; pair it with a word or an icon.

`--fr-neutral` is the grey the connector states already used for
"disconnected". `--fr-wordmark-amber` (`#f59e0b`) shares its value with
`--fr-warn` on purpose, and the rule that keeps them apart is placement: amber
is an accent only inside the "FutureSpeak.AI" wordmark. Everywhere else amber
means "needs you". Do not use it as decoration.

The avatar's mood hues are a separate palette, judged against these reserved
hues by the avatar spec; the reserved hues do not move to satisfy that spec.

## Surfaces and inks

| Token | Value | Use |
|---|---|---|
| `--fr-surface` | `#0a0e1a` | Solid panels, `theme-color`. |
| `--fr-glass` | `rgba(10,14,26,0.75)` | Glass panels. |
| `--fr-glass-blur` | `blur(16px) saturate(1.2)` | The `backdrop-filter` that goes with the glass. |
| `--fr-glass-edge` | `rgba(255,255,255,0.06)` | Hairline border on glass. |
| `--fr-text` | `rgba(255,255,255,0.86)` | Body text. |
| `--fr-label` | `rgba(255,255,255,0.78)` | Field labels. |
| `--fr-dim` | `rgba(255,255,255,0.46)` | Secondary text. |
| `--fr-faint` | `rgba(255,255,255,0.3)` | Placeholders, disabled. |

Glass is the panel language: `background: var(--fr-glass)`, `backdrop-filter:
var(--fr-glass-blur)`, a `--fr-glass-edge` border. The scene's ground stays
near-black.

## Type

Three faces, self-hosted in `static/fonts`.

| Token | Face | Job |
|---|---|---|
| `--fr-font-display` | Orbitron, then `sans-serif` | The wordmark, the greeting, state chips, card titles. Display only. |
| `--fr-font-body` | Inter, then `system-ui`, `sans-serif` | All UI text. This is the `body` font, on the login page as well. |
| `--fr-font-mono` | JetBrains Mono, then `ui-monospace`, `monospace` | Data: times, ids, paths, numbers, code. |

Orbitron always falls back to `sans-serif`, never to `monospace`; the guard
fails on any Orbitron declaration that does not.

The type scale is the set of sizes the interface already uses most:

| Token | Size | Typical use |
|---|---|---|
| `--fr-text-2xs` | 9px | Micro-labels, badges. |
| `--fr-text-xs` | 10px | Captions, chips. |
| `--fr-text-sm` | 11px | Dense rows, pills. The most used size. |
| `--fr-text-md` | 12px | Body in panels. |
| `--fr-text-base` | 13px | Body in reading views. |
| `--fr-text-lg` | 15px | Wordmark, section titles. |
| `--fr-text-xl` | 18px | Panel headings. |
| `--fr-text-2xl` | 26px | The greeting. |

Tracking: `--fr-track-display` (`0.06em`) for Orbitron; `--fr-track-label`
(`0.15em`) for uppercase micro-labels. Existing `px` literals in the interface
are not rewritten here; new code uses the tokens.

## The mark and the wordmark

The mark is the neon rocket: `assets/icons/futurespeak.png` (a real alpha
channel, transparent around the rocket), `assets/icons/futurespeak.ico` and
`assets/friday.ico`. The installer and autostart shortcuts read
`app\assets\friday.ico`. The lightning bolt in `assets/icons/futurespeak.svg`
is the Sites workspace icon and is not the mark.

The wordmark reads **AGENT FRIDAY** *by* **FutureSpeak.AI**:

- "AGENT FRIDAY": Orbitron 900, `--fr-cyan`, `--fr-track-display`, a soft cyan glow.
- "by": JetBrains Mono, white at 72%.
- "FutureSpeak.AI": Orbitron 900, `--fr-wordmark-amber`. This is the only place amber is a brand accent.

Write the company as **FutureSpeak.AI**, with that capitalisation.

## Iconography

Workspace icons live in `assets/icons/*.svg`. The template:

- 32 by 32 viewBox, outline only, stroke 1.2 to 1.3, round caps and joins.
- Stroke is the triad gradient, cyan to violet to magenta.
- A soft glow: a Gaussian blur of about 0.7 merged under the stroke.
- One idea per icon, no fill, no text.

## Holographic language

The scene is Friday's face, and the brand's most distinctive surface. Change it
with care.

- Bloom: Unreal bloom around strength 0.9, radius 0.55, threshold 0.25, scaled by mood.
- Shader: a chromatic offset of about 0.003, film grain of about 0.04, faint scanlines.
- Moods: each sets a base colour, an accent, a rotation speed, a bloom strength and a grain level. The table lives in `index.html` (`MOODS`). Speaking is green.
- Structures: thirteen, in order of evolution, from the Genesis Lattice to Giga Earth (Rez). Each is named for a person or idea from computing and mathematics.
- The scene animates while Friday is speaking or working, and is still when she is idle.

## Vocabulary

Use these terms exactly and consistently.

- **Proof of Integrity**: how agents establish trust without a central authority. Each verifies that the other's cLaws are intact through HMAC-SHA256 signatures and Ed25519 peer attestation.
- **Asimov's cLaws**: the compiled laws that bound what Friday will do. They are signed and verified before every action. Say "the cLaws" when they are what is meant, not "the rules".
- **Positrons and negatrons**: the economy's two currencies. Positrons (ψ) are value created; negatrons (η) are cost and obligation. Amounts are kept in milliPositrons.
- **Asimov's Mind**: the FutureSpeak.AI platform Friday is built on. It appears in file headers and the CLI as "FutureSpeak.AI · Asimov's Mind".
- **FutureSpeak.AI**: the maker.
- **Sovereign Vault**: where your private material lives.

## Voice

Friday speaks to you in the second person: "you", "your", and now and then
"boss", as equals. She never says "the user".

- Calm, perceptive, dry warmth. Signal over noise.
- Answer first. Evidence next: what she checked, what she found, how sure she is.
- She says what she did not do, and why. She does not claim success she has not verified.
- When something needs you, she says so plainly and says what she needs.
- News is read in an anchor's register: calm authority, context for each story, the connections between stories, then analysis built from the evidence. It is a set of traits, not a person. Friday never claims to be, or imitates, any real journalist or broadcaster, and product copy never names one.

User-facing text should sound like her: short, direct, kind, a little dry.

## Audio identity

Friday has no signature sound yet. The slots are defined so the sounds can be
added without touching call sites:

| Slot | When | Status |
|---|---|---|
| intro | Friday comes up and is ready | not yet made |
| outro | Friday shuts down or you sign off | not yet made |
| notification | Something needs you | not yet made |

Constraints for whoever fills them: under two seconds; the intro and outro
share a motif; the notification is quiet, never alarming; every sound obeys the
volume and mute settings; nothing plays while off the record.

## Per-workspace identity

The shared workspace registry, `DOCK_GROUPS` in `index.html`, is the brand
system's front door for per-workspace identity. Each entry declares a
workspace's `id`, `ico` and `label` once, and the dock, window chrome and tabs
read them from it. Today it declares no accent; a workspace that needs one adds
it to its registry entry, and the accent is a member of the palette above. The
reserved status hues are never used as a workspace accent. The scoped sets that
exist now (`.news-ws`, `.msg-ws`, `.cal-ws`, `.st-root`) already point at the
tokens.

## Consolidations

Where the shipped UI had several values for one role, they now share one token.
Each visible change is a brand decision, recorded in
`docs/decisions/2026-09-29-program-delegated-decisions.md` as B7 to B15.

| Decision | Surface | Before | After |
|---|---|---|---|
| B7 | Status dots (`.status-dot`, defined twice) | 6px and 8px; green `#00ff66`, red `#ff0033`, yellow `#ffcc00`; disconnected `#666`; connecting, error, needs-setup, blocked and unknown states had no colour at all | one 8px dot; ok `#00ff80`, error `#ef4444`, warn `#f59e0b`, disconnected and unknown `#7a8699` with no glow, connecting `#7b61ff`, needs-setup `#f59e0b`, blocked `#ff0080` |
| B8 | Settings danger (failure text, danger button) | `#ff6b8a` | `#ef4444` |
| B9 | Connector states (Python) | error `#ff5470`, blocked `#ff8c42`, connecting `#f59e0b`, unknown `#888888` | error `#ef4444`, blocked `#ff0080`, connecting `#7b61ff`, unknown `#7a8699` |
| B10 | Notification priority colours (Python) | critical `#ff3366`, high `#ff8a00`, medium `#ffd23f`, low `#00d4ff` | critical `#f59e0b` (needs you; nothing failed), high `#00d4ff`, medium `#a78bfa`, low `#7a8699` |
| B11 | Push-to-talk indicator (Python) | arming `#64748b`, recording `#ef4444`, thinking `#e0a030`, done `#22c55e`, clipboard `#e0a030`, idle `#94a3b8` | arming and idle `#7a8699`, recording `#00d4ff`, thinking `#7b61ff`, done `#00ff80`, clipboard `#f59e0b` |
| B12 | Connected-account palette (Python) | cyan, purple, `#22c55e`, amber, pink, teal, red | cyan, purple, teal, pink, blue, sand, soft violet: no status hue (new accounts only) |
| B13 | Type | body `Helvetica Neue`; Orbitron falling back to `monospace`; the login page in Orbitron throughout | body Inter; Orbitron falls back to `sans-serif` everywhere; the login page is Inter with an Orbitron heading |
| B14 | The mark | a dark box baked into the PNG and the icon | a real alpha channel; `assets/friday.ico` added for the installer |

The reserved hues themselves (`#00ff80`, `#f59e0b`, `#ff0080`, `#ef4444`) did
not move. The re-pointed scoped sets (news categories, message lanes, calendar,
settings) keep their current values.

## Known debt

The semantic rule above is the target. The shipped UI still breaks it in the
places below; the semantic migration (with its own before and after captures)
is the next brand piece, and nothing here is changed by this one. Counts are
occurrences in `index.html`.

- **Failure that is not error red.** Failure and error text is spelled in other reds and pinks: `#ff6b8a` (11), `#ff5470` (13), `#f87171` (16), `#ff3c5a` (7), `#ff8fae` (3), `#ff8fb0` (1). The connector dot in a workspace title bar (`ConnectorDot`) uses `#ff5470` for its failing state.
- **Failure that is deny magenta.** Task cards mark `failed` and `stalled` in `#ff0080`, and workflow runs mark `failed` the same way. A failure is error red; magenta is for a refusal.
- **Amber that means running.** `.task-card.running`, `.thread-status-badge.running`, the `connecting` state of `ConnectorDot` and the `EXECUTING` mood all use amber for work that needs nothing from you. Working is violet.
- **Green that is not ok.** `#3effa1` (11) and the camera indicator's `#00ff66` (2) stand in for ok green.
- **Other one-offs.** The decorative amber `#e0a030` (1) and the knowledge workspace's `kw-danger` pink sit outside the token set.
- **Literals in general.** `#00d4ff`, `rgba(0,212,255,…)`, `#f59e0b`, `#00ff80` and `#ff0080` are still spelled as literals throughout `index.html` and `ui_parts/app.html`. The tokens exist so those can move to `var(--fr-*)` without a value changing.
