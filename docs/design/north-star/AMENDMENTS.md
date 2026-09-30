# Amendments to the north-star spec

**Status:** in force.
**Written:** 2026-09-29
**Authority:** the owner's standing rulings. These override
`agent-friday-ideal-product-spec.md` wherever the two conflict.

Each amendment names the sections of the spec it changes. Anything an
amendment does not touch stays as the spec wrote it.

---

## A1. The hologram is Friday's identity layer

**Amends:** §21.22 (holographic and spatial interface) and §37.8 (UI migration).

The holographic scene is **not** an optional skin or a "presentation mode"
over a flat product. It is how Friday is recognised, and it ships on by
default.

These rules of the spec's stay:

- **A full flat path.** Every essential action has a complete flat, voice and
  keyboard path. Spatial navigation is never required for an essential
  operation.
- **Reduced motion.** Motion honours reduced-motion settings, and 3D rendering
  can be switched off entirely (§33).
- **Motion is not evidence.** Motion never poses as evidence. An orb, graph or
  avatar state that claims to show reasoning, memory or activity must come from
  real trace data. Decorative motion is plainly decorative (§6.8).

## A2. Visual evolution weekly; personality changes as proposals

**Amends:** §13.7 (personality evolution) and §13.8 (growth timeline).

- **Visual avatar evolution** runs **weekly by default**. It has undo, rollback
  to any step, "evolve now", and an off switch. The author defaults to a
  frontier model, and any model may be chosen, local included. The design is
  in `docs/design/active/avatar-visual-genome.md`.
- **Proposal-only applies to personality and behaviour.** The spec's rule
  covers changes to Friday's **personality and behaviour**: tone, verbosity,
  proactivity, vocabulary and rituals. Each such change arrives as a proposal
  with a diff, evidence, a preview and a rollback. Nothing about who Friday is
  changes silently.
- **Visual steps are not proposals.** Visual evolution steps are reversible
  and credited, and they are announced rather than put to a vote. The owner
  can switch evolution to ask-first mode.

## A3. No telemetry, full stop

**Amends:** §26.23 (no telemetry by default), §35 (success metrics), and every
other place that assumes data leaves the machine for measurement.

- **Nothing leaves the machine automatically.** Friday sends nothing about its
  use, crashes, performance or health off the machine, and this holds even
  with opt-in. There is no product analytics, crash reporting, usage telemetry
  or licence check, whether on by default or behind a toggle.
- **Diagnostics are local.** Diagnostics are local Doctor reports. The owner
  can read them in full and can choose to share one **by hand**, for example
  by saving a file and attaching it themselves. Friday never uploads one.
- **Metrics stay on the machine.** Success metrics and pilots are measured on
  the user's own machine, where they stay, or come from our own test
  infrastructure (the test suite, clean-machine runs and gauntlet
  measurements).
- **§35's crash-free rate** is therefore measured by the test infrastructure
  and by local Doctor history. It is never collected from users' machines.

## A4. Voice-first parity

**Amends:** §22 (voice, audio, camera and multimodality) and every section
that limits what voice can do.

- **No voice-only limits.** Anything Friday can do in text, she can do by
  voice. The only exception is a limit the user sets.
- **Same approval cards.** Voice actions pass through the same approval cards
  as text. A card can be answered by voice or on screen.
- **Cloud voice sees summaries, not private data.** When voice runs on a cloud
  model, it reaches private data only through a summary that a local model has
  made PII-free. Raw private data does not go to a cloud voice model.

## A5. Brand integrity

**Amends:** §21 (workspaces and interaction design) and every surface section.

- **One brand system.** Every surface strengthens the single Friday brand
  system. That covers the desktop and workspaces, News, podcasts, spoken
  replies, approval cards, notifications, onboarding and the installer, the
  phone line, market cards, and the public README and release.
- **Where it lives.** The brand system is codified in `docs/brand/` and in the
  code's tokens.
- **The bar is not a look.** Quality bars measure quality. They are never a
  look to copy.

## A6. Both paths, and the user picks

**Amends:** §12 (model runtime and routing).

Local and cloud paths both exist for every capability where a local path is
feasible, and the user picks between them. There is no silent substitution in
either direction (this reinforces §6.4).

## A7. Deploys and releases

**Amends:** §28 (updates) and §34 (release acceptance).

- **Continuous deploys go only to the owner's own Friday.** Each deploy is
  guarded, health-checked, and rolled back automatically on failure.
- **A public release needs a discussion with the owner first.** That covers a
  version tag, a GitHub release, a published installer and an announcement.
- **§34's release acceptance is the readiness checklist.** Meeting it does not
  authorise a release.
