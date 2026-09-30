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
- **The weekly ask, the one refinement.** In the owner's words: *"maybe Friday
  should ask if it's ok to email those to futurespeak once a week."* The user
  decides every single time, so this stays within the no-telemetry rule:
  - **An ask, never a background send.** Friday asks once, and remembers one
    of three answers: "Yes, ask me weekly", "Not now", or "Never ask again".
    "Never ask again" ends it until the user turns it back on in Settings. A
    "yes" never becomes a silent automatic send.
  - **What each week looks like.** The ask is a card showing the exact report,
    with **Send**, **Skip this week** and **Stop asking**.
  - **No nagging.** The ask sits quietly in the weekly review and in Home's
    "what's waiting" list. It never interrupts a task, and it never repeats
    within a week.
  - **The payload is the local Doctor report and nothing else.** That means
    versions, failing checks, error signatures, settings drift and performance
    numbers. It never includes conversation content, file names, email
    addresses, contacts, or anything from the vault. The report is scrubbed by
    the existing PII gate and the local model. The user then sees the literal
    text that will leave, exactly as it will be sent.
  - **It sends from the user's own email account,** so it appears in their Sent
    folder. It is an outward action, so it goes through the normal approval
    card and leaves a receipt. If Friday has no send permission, she prepares a
    draft and the user presses send. Friday never asks for wider email
    permissions just for this.
  - **It goes to a FutureSpeak address,** named in one constant. The card
    carries a one-paragraph, plain-language promise of what FutureSpeak does
    with the report.
  - **Local-only mode and off-the-record.** In local-only mode it never runs
    unless the user explicitly says yes there too. Nothing from an
    off-the-record session enters the report.
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

## A8. The salon's box, and who may change the constitution

**Amends:** §26.11 (tool sandbox network allowlists) and §26.13 (network
policy) for sandboxed codebase traffic only, and §13.1 (constitution) in
part. Decided by the owner on 2026-09-29. The design is in
`docs/design/active/vibe-coding-salon.md` (§4.5, §7.4, §14).

- **The box announces; it does not block reads.** In a codebase's box,
  reads (GET, HEAD, OPTIONS) and package installs to hosts without a rule go
  ahead. Each one is announced in the chat and recorded on the step's
  receipt.
  - Writes to outside hosts ask, as every outward action does.
  - Anything carrying private data asks, and shows the exact payload.
  - The install scan, the 24-hour cooldown and exact pins still apply.
  - The owner can switch any codebase to "ask", or set an owner rule.
  - This covers only the box's own traffic. Friday's egress gate,
    connectors and providers keep §26.13 as written.
- **The constitution is not editable by Friday on her own authority. The
  owner may change it through the salon:**
  - only on a copy of Friday, never the running one;
  - only through the loud approval: a separate card, a fresh challenge word
    spoken or typed, and a signed notice afterwards;
  - never through a grant;
  - always with §13.2's readable diff and re-attestation before outward
    actions resume.
