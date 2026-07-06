# Agent Friday v5.3.0 — "Ship It"

*Release date: 2026-07-06 · FutureSpeak.AI · Asimov's Mind*

Friday can already make things — images, video, music, decks, websites, prose.
As of this release she can **ship them to where your audience actually lives**,
and she does it while you sleep. v5.3.0 also lands a ground-up overhaul of the
voice system across all three tiers.

---

## The Content Pipeline

A full social-media management system built into the sovereign desktop:
**create → compose → schedule → publish → monitor → learn.**

- **Compose once, native everywhere.** Pick a Studio creation (or start from a
  prompt) and Friday writes platform-native versions — a LinkedIn post, an X
  thread, an Instagram caption, a YouTube description — each in *your* voice
  (SOUL.md plus a per-platform voice card you can edit), each shown as a
  pixel-honest preview with character meters before anything ships.
- **Schedule it and walk away.** A calendar with drag-to-reschedule, optimal
  posting times (seeded from best practice, replaced by times learned from
  *your* audience), recurring templates, and conflict warnings. The internal
  scheduler publishes at the instant — no user present needed. YouTube and
  Mastodon get true platform-side scheduling, so even a powered-off machine
  can't miss those slots.
- **Eleven platforms + the Federation.** LinkedIn, X/Twitter, Instagram,
  YouTube, TikTok, Bluesky, Mastodon, Reddit — real API automation with
  honest rate budgets. Substack and Medium get an assisted handoff (we don't
  pretend to automate what their platforms don't allow). And everything can
  simultaneously list on the Friday Federation marketplace, where engagement
  earns Positrons and provenance is cryptographic.
- **Connect accounts in one click.** Content → Accounts: OAuth for the big
  platforms (localhost loopback only — no third-party redirect service),
  token paste for Bluesky/Mastodon (verified live before we claim success),
  plain-language scopes including what Friday *cannot* do with your account.
- **Sovereignty carries through.** Every outbound post passes the H1–H4 harm
  floor and the sensitivity classifier. Anything that looks private is
  **held for your review** — never silently redacted into a mangled public
  post. A held post publishes only on your explicit release, and the hard
  harm floor is never releasable. Platform tokens live in the encrypted
  credential store. Analytics stay on your machine. No engagement automation,
  ever — Friday publishes; she does not manipulate.
- **Proof of authorship, end to end.** Every published piece extends its
  Ed25519 provenance chain: creation → adaptation → publication → federation
  listing → sale. Even if a platform strips your metadata, your local signed
  ledger proves what you made, where it went, and when.
- **It learns.** Local analytics normalize engagement across platforms,
  surface honest insights ("video posts get 4.1× the engagement of text,
  n=23"), learn your audience's active hours, and feed what works back into
  composition — all statistics computed locally, with sample sizes shown.
- **Talk to it.** "Friday, post this to LinkedIn and Bluesky tomorrow
  morning" — the chat and voice tool layer drives the same pipeline.

## Voice System Overhaul

The "voice is broken again" report got a systemwide root-cause pass across
all three tiers, verified against live APIs:

- **Tier 3 (Gemini Live):** the `/friday-live` PWA is reachable again, the
  live-model fallback chain is verified-current (with a retired-model
  denylist that self-corrects stale settings), and the client got capped
  reconnect backoff with actionable error banners.
- **The egress gate stopped eating your words.** Overzealous substring
  matching was classifying everyday phrases as sensitive and blanking cloud
  calls. Now: word-boundary matching, span-level redaction instead of
  whole-message drops, and a false-positive leg in the startup self-test —
  with the leakage posture unchanged (flagged content still never leaves).
- **Tier 1 (local CPU):** voice activity detection no longer clips your first
  syllable; the first-run TTS test actually plays audio; Piper downloads
  can't hang the engine forever.
- **Tier 2 (local GPU):** a physical NVIDIA GPU is detected even when torch
  is CPU-only, and the wizard tells you exactly what to install.
- **In-UI installs:** the Voice Setup Wizard now installs Tier-1/Tier-2
  dependencies and models itself — live logs, disk preflight, cancellation.
  Mic and speaker test buttons with a live level meter. Honest step statuses.

## Also in this release

- Model selector redesign: name-only pill, curated quick-switch panel,
  role-aware catalog with browse-all deep link.
- Scheduler gains a `once` trigger for one-shot jobs.
- 23 new `/api/content/*` routes documented in `docs/API.md`; per-platform
  manual test procedures in `tests/MANUAL_TEST_PROCEDURES.md`.
- Full offline test suite: ~4,700 tests green.

---

*Every capability in this release is governed by Asimov's cLaws, gated by the
sovereign egress classifier, and provable via Ed25519 content credentials.
Friday distributes; you own.*
