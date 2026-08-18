# App-level tests

The rest of this repository's tests check that functions return the right
values. These check that **the application works**. Every defect they were
written from shipped with a green test suite and was found by a person using
the app.

## Running them

    npm run test:smoke    # ~30s — does the app work at all
    npm run test:app      # full suite, including machine-vision judging
    npm run test:vision   # just the vision tier

The Friday server must already be running on :3000. Nothing here starts or
stops it.

## The two tiers

**Smoke** is everything that answers "if I opened Friday right now, would it
work?" It has to stay fast, because a slow suite is one nobody runs.

**Full** adds liveness tests that watch work happen over time, and vision tests
that screenshot the app and have a model judge it. The vision tier takes a few
minutes; that is the cost of catching things no assertion can express.

## What makes these different

They assert **liveness**, not just correctness. The dominant failure in this
codebase is not a wrong answer — it is a subsystem that runs, reports success,
and produces nothing. `liveness.ts` holds the reusable form of that idea:

| Assertion | The defect it generalises |
|---|---|
| `assertVaries` | a progress bar with exactly two possible values, shown as a percentage |
| `assertGrewDuring` | task logs replayed after the call returned, so they read "— waiting for activity —" throughout |
| `assertCreatedByThisTest` | a gallery that fetched once on mount, so new work never appeared |
| `assertClaimBackedByArtifact` | "I've opened the file for you", having called no tool |
| `assertNewestFirst` | newest work sorted to the bottom, 7,880px down the page |
| `assertImagesDecoded` | a broken-image glyph, which is in the DOM exactly like a real image |
| `assertRenderedOnce` | a home icon rendered twice |
| `waitForSettled` | a panel that says "Loading…" forever |
| `assertReadableContrast` | text too faint to read -- the measurable half of "sloppy and bad" |

## Your data is not touched

Tests never write to the real calendar, wiki, or gallery. The one test that
must create something writes a single file prefixed `zz-test-` and deletes it
in `afterEach`, even when the test fails. Cleanup only ever removes files
carrying that prefix.

Tests that would start real work (image generation) are **skipped by default**
and need `FRIDAY_TEST_MUTATE=1`.

## Machine vision

Screenshots are judged by a local model over Ollama, never a cloud API — a
screenshot of Friday contains real calendar, message, family, health and
finance data, and must not leave the machine to satisfy a test.

Judging is against **stated intent, not a golden image**. A golden would have
frozen the reverse-sorted gallery and the two-value progress bar as correct,
since they were what the app did the day it was captured. Each screen declares
what it is supposed to achieve and the model is asked to find fault with it.

Two things to know when a vision test fails:

- **State the intent as the screen actually is.** An inaccurate intent produces
  a confident complaint that is your error, not the app's.
- **Never judge a loading screen.** `waitForSettled` exists for this.

## Environment

| Variable | Effect |
|---|---|
| `FRIDAY_BASE` | server URL (default `http://localhost:3000`) |
| `FRIDAY_TEST_SHIM=1` | apply known-break shims — see below |
| `FRIDAY_TEST_MUTATE=1` | include tests that start real work |
| `FRIDAY_VISION=off` | skip vision judging |
| `FRIDAY_VISION_MODEL` | judge model (default `gemma4:12b`) |

### The known-break shim

As of `b3bf550` the app does not start: `const DEFAULT_AGENT_SETTINGS` was
deleted from `index.html` while three references to it remain, so the React app
throws on mount and the page renders blank.

`FRIDAY_TEST_SHIM=1` restores that constant **in the browser only** — it edits
no file — so the rest of the suite can still be exercised while the fix is
outstanding. It is off by default and must never be set in CI, because it hides
a blank screen. Delete the shim once the constant is restored.

## Vision proposes, code disposes

The first thing the vision judge found was that two buttons looked too faint to
read. A quick contrast check appeared to confirm it at a ratio of 1.0 -- white
text on white. Both were wrong: the check was treating a translucent overlay,
`rgba(255,255,255,0.06)`, as solid white, so it reported the dark theme as
white-on-white.

With the layers composited properly, the same screen passes at 3:1, and at the
stricter 4.5:1 the only finding is muted source-and-timestamp text at 4.21 --
real, minor, and worth a small colour bump.

The lesson is built into the suite. A vision finding is a **lead**, not a
verdict; where a measurement is possible, `liveness.ts` measures it, and the
number wins. Vision is for the things no number can express.

## Known residual noise in the vision tier

Two findings survive every calibration pass and are recorded here so nobody
re-litigates them from scratch:

- **Long model ids truncate with an ellipsis** in the picker. True, and already
  handled — every row carries the full id as a hover title. A vision judge
  cannot hover, so it will keep reporting this.
- **Occasional invented specifics.** A local 12b judge sometimes describes
  elements that are not on the screen (a model name, a button). Confirmation
  now requires two passes to describe the SAME problem, which filters most of
  it; what remains is the price of judging with a small local model, accepted
  so screenshots of real personal data never leave the machine.

Treat a red vision test as a lead to check by hand, not a verdict. The tier
earned its keep by catching the blank screen, the missing dock, unreadable
buttons, and never-resolving spinners — all real.
