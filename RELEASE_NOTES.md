# Agent Friday v5.2.0 — "Always Listening"

*Release date: 2026-07-04 · FutureSpeak.AI · Asimov's Mind*

This release is about trust in a conversation: voice mode now survives an
hours-long call without a single silent gap, you can interrupt Friday
mid-sentence just by talking over her, and she can produce real deliverables —
slide decks, websites, video — from a spoken or typed request.

---

## Voice you can actually talk to

**Interrupt her any time.** On open speakers, just start talking over Friday —
she stops within a fraction of a second and listens. The bridge detects
deliberate talk-over with an echo-aware detector (it learns what *her own
voice* sounds like leaking back into your mic, and only ever triggers on
speech well above that), so she never clips herself off the way early
barge-in builds did. Escape is a guaranteed manual interrupt; headphone users
keep Gemini's native instant barge-in.

**Hours-long sessions, no dead air.** Gemini Live caps individual connections;
Friday now rides through every cap invisibly. Sessions renew with resumption
handles before you can hear a seam, a liveness watchdog force-recovers the
"talking into a void" hang, the browser auto-reconnects with backoff if the
socket dies, and a reconnect resumes the *same* conversation — no re-greeting,
no lost context.

**Friday knows herself again.** A packaging regression had silently emptied
Friday's self-knowledge from every prompt. Fixed — and the knowledge itself
was rewritten to match today's UI, workspaces, and tools.

## Create real things from chat or voice

- **Slide decks** — "make me a presentation about X" produces a polished,
  self-contained HTML deck in the Studio gallery: keyboard navigation,
  speaker notes, print-to-PDF. Works offline.
- **Websites** — "build me a website for X" produces a multi-page, hash-routed
  site in a single HTML file that deploys anywhere.
- **Video** — Veo generation, timeline editing, and the full production
  pipeline now run on the live July-2026 model lineup (Veo 3.1 family,
  Gemini 3 Pro Image, Nano Banana 2/Lite, Gemini Omni Flash).

## Provider layer (5.1.x, first shipped in this release)

Friday routes by registry, not model-name guessing: ten new OpenAI-compatible
providers (OpenRouter first-class with live model discovery and real usage
accounting), multi-provider dispatch in one session, per-provider health
measurement with circuit breakers, and registry-driven egress classification
so only genuinely local endpoints bypass the privacy gate.

---

**Install:** download `AgentFriday.exe` from this release, or
`pip install -e .` from source — see `docs/INSTALLATION.md`.
**Upgrade note:** no settings migration required; voice improvements apply on
first launch.
