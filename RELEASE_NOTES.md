# Agent Friday v5.13.0

*2026-09-06 · FutureSpeak.AI*

This release merges two branches that had drifted apart since 5.10.0, closes
a privacy control that was reversed twice in one day before it shipped
correctly, and fixes the last surviving public reference to a model this
product no longer uses. Full technical detail lives in
[CHANGELOG.md](CHANGELOG.md).

---

## The thing we'd rather not have had to report

**A privacy control was wrong for a few hours, in production, before it
shipped.** Earlier the same day, `is_unrestricted_cloud()` — the one flag
capable of turning off every safeguard this codebase has for cloud sends —
was changed to treat selecting "cloud only" as a provider preference as
itself sufficient consent to send everything unrestricted. That reasoning
sounded right in isolation: if you've picked cloud-only, why ask twice? It
falls apart against one fact: **cloud-only is this app's factory default.**
The change meant a stranger who never opened Settings inherited "no privacy
safeguards" the instant they picked cloud-only as their provider — which is
what most people do, since it needs no local model and no setup.

We didn't catch this by inspection. We caught it because the full test
suite went from 7 failures to 123 the moment the change landed, all in
tests that had assumed cloud-only-with-nothing-else-configured was still a
protected state. It was — until that afternoon.

## What actually shipped instead

**Unrestricted cloud access is now earned, never inherited.** A new,
explicit `cloud_consent` record is the only thing the egress gate reads for
this decision — not `mode`, not any value you can set through the general
settings API. Two screens, depending on your own hardware:

- **If your machine can genuinely run local models well enough** —
  reasoning, voice, image, and video, including the resource planner's own
  check that it can juggle between them — you get a real, durable choice
  between private-local and unrestricted-cloud.
- **If it can't** — no local option is offered at all, because that would
  be a promise the hardware breaks. The screen says plainly what won't run
  locally and asks you to explicitly accept unrestricted cloud instead.
  Nothing defaults to it; silence changes nothing.

The capability check looks at what your machine can *actually* serve, not
which specific runtime is installed — Ollama, a GGUF fetched directly into
Friday's own runtime store, and ComfyUI (for image and video) are all
checked the same way, because a check that only recognized one of them
would call a real, working setup "incapable" for no reason.

The setting itself is written by exactly one code path, which is not
reachable from the general settings API a model's own tools can already
call — the same class of fix as removing `enterprise_consent_grant` a few
days ago, applied here before this one ever shipped for real.

## Also in this release

- **`main` and the release-integration branch are merged** for the first
  time since 5.10.0 — the OS-mode subsystem, `FRIDAY_HOME` isolation, and
  fail-closed credential storage now sit alongside the gauntlet audit's 82
  fixes and the Gemma 4 model rebuild, in one tree, with the full suite
  green.
- **The public README no longer names Qwen.** It was found still describing
  the retired model family — the third independent place this same fact
  had to be fixed since the 2026-09-03 removal decision, after the setup
  wizard and the installer's own ladder. A standing check
  (`scripts/check_stale_model_names.py`, wired into pytest and the
  pre-commit hook) now catches the next one automatically instead of
  needing a fourth documentation pass to find it by hand.

## What this release does not fix

The repository's git history contains a real vault passphrase from earlier
in this project's life, and it has been reachable from a published branch
on GitHub since 2026-08-30 — not merely sitting in local history. That is a
publication and rotation decision, not a code fix, and it remains
explicitly not acted on here: no history rewrite, no force push, no branch
or tag deletion. Those stay yours to decide.
