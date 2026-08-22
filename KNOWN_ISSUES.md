# Known Issues

**As of 2026-08-21, for v5.5.0-rc.**

This file lists what is broken, what is unverified, and what we do not know. It is
maintained because a defect you can read about is cheaper than one you discover, and
because a project that publishes its bug list is easier to trust than one that doesn't.

If something here is wrong or you hit something that isn't here, please open an issue.
We would rather add to this file than defend a claim.

---

## 1. The failure mode this project actually has

Everything below is downstream of one pattern, so it is worth stating before the list.

**Friday's dominant failure mode is confident wrongness, and her second is hiding her
own injuries.** Not crashes. Not wrong answers she flags as uncertain. Silent successes
that were not successes.

The evidence is not theoretical. Every one of these was found by *using* Friday, not by
reading her code, and each had been live for weeks:

- The server spawned its child with `stderr=DEVNULL`. Six overnight startup failures left
  no trace anywhere. The cause was a one-line import error that a two-second check would
  have caught.
- A whole API surface — the career pipeline — failed to register for **seven weeks and
  ~70 restarts**. It logged one warning per boot. Nobody read it. `/api/health` returned
  200 the entire time.
- Every voice conversation's wiki distillation was discarded for weeks. The task routed
  to a model that was not resident, got HTTP 404, and reported **"Task complete."**
- The system tray reported `FAILED TO START` on every *successful* start, because it
  waited 30 seconds against a measured 143-second boot. It recovered only because a
  watchdog later noticed the port was open.
- Local voice transcribed speech, emitted `status: thinking`, and never spoke. The
  readiness gate certified the microphone and the speaker and never asked whether the
  brain in between existed.
- A working MCP subsystem with ~99 registered tools reported itself dead for the life of
  every process, because an endpoint imported a value at module load instead of reading
  it live.

None of these looked broken. All of them reported success.

### The rule

> **Nothing in Friday may claim success it has not verified.**

A component that cannot verify its own success must say so. "I could not check" is an
acceptable answer. "Done" when nothing was done is not. This is the rule the codebase
most needs and the one the fixes below are all applications of.

### The specific bug class: comparisons that discard the meaning

Seven instances were found in a single night, in code written years apart by different
authors, including in tooling written that same night to investigate the others. They
look unrelated and are not:

| What was compared | What was thrown away | What it cost |
|---|---|---|
| `_has_model("gemma4:e2b")` matched any `gemma4:*` | the tag | `friday doctor` reports a model ready; the next call 404s |
| `split(":")[0]` in a benchmark harness | the tag | every measurement 404'd and reported `+0 MiB` as success |
| a creation filename `20260819-140523` | that it's a date | matched the credit-card detector; Friday's own output was withheld from her |
| `api_key=core.GEMINI_API_KEY` | that it's an identifier, not a literal | the secret scanner blocked a correct config read |
| a code comment containing `token:` | that it's prose | same scanner, same commit |
| tool descriptions containing "family", "contact" | that they're static documentation | the model was handed a tool list it could not read |
| a VRAM delta of `-653 MiB` | the sign | "SOMETHING LANDED ON THE GPU" when memory had been *freed* |

The shape is always the same: **a check compares a convenient projection of a value
instead of resolving what the value actually means.** A name's prefix instead of the
name. A number's magnitude instead of its sign. A string's shape instead of its role.

Every one produced a *confident* answer. None produced an error. That is what makes this
class expensive — it never announces itself, and the wrong answer is indistinguishable
from the right one until something downstream fails for an unrelated-looking reason.

**If you are reviewing this codebase, this is the thing to grep for.**

### The second class: an assertion loose enough to accept a failure it wasn't testing for

The first class is a check that throws away the part of the value carrying the
meaning. This one is its pair: a check so wide it cannot tell what it caught.

Both examples below are from tests written during this release, to verify fixes
for the bugs above. Both passed. Neither was evidence.

| The test | Why it passed | What it actually proved |
|---|---|---|
| A benchmark harness asserted a `+0 MiB` VRAM delta meant CPU-only placement held | Every model request had 404'd, so nothing ran on any processor | Nothing. A green light manufactured by a workload that never executed. |
| A test asserted a failed install `exited non-zero` | The subprocess crashed on `ModuleNotFoundError` before reaching the code under test | Nothing. It passed identically with the fix reverted. |

In both cases the assertion was true and meaningless. `abs(delta) < 200` is
satisfied by a run that did no work. `returncode != 0` is satisfied by a crash.
The narrow forms — "did the workload complete, *and* was the delta flat", "did
the command print its verdict, *and* was the code exactly 1" — catch both.

The rule:

> **A test that could not have failed for the reason you think it passed is not
> evidence.**

The cheapest way to check is to break the fix and re-run the test. If it still
passes, it was never testing the fix. That takes about a minute and it caught
both of these.

### The third class: evidence about a component that isn't in the path

> **A measurement of something the system does not use is not a measurement of
> the system.**

The sharpest instance of the week, and it survived the longest because every
individual step was done well.

Two small models — `embeddinggemma:300m` and `functiongemma:270m` — were
benchmarked against the live daemon. Real numbers came back: 57–328 ms to embed
a chunk, 358 ms for a function call, both returning genuine results rather than
timing out. A seating decision was built on them (cap the embedder, don't cap
the function seat), a thread-saturation profile was measured to support it, and
the installer was written to download both and describe what they do.

Nothing in `src/` loads either model.

The real embedder is `all-MiniLM-L6-v2`, reached through sentence-transformers.
`local_seats.py:48` sets `_MIN_USEFUL_GB = 1.5` *specifically to exclude*
`functiongemma` from seat selection, and says so in a comment. Both facts were
one grep away throughout.

Every check performed was a check on the *component*: does it exist, does it
respond, how fast is it, does it use the GPU. None was a check on the *system*:
does anything call this. The installer was about to have a first-time user
download 1.17 GB of weights and tell her they indexed her vault and handled her
tool calls.

The question that catches it is one line and it is not about performance:

```
grep -rn "<model-or-component>" --include=*.py src/ | grep -v "^src/.*/<the-thing-itself>"
```

If the only hits are comments and your own new code, you have measured
something that is not in the path.

### Why these classes are worth naming together

They are the same root failure at different layers. One is a comparison that
discards meaning; the other is an assertion too broad to detect that discarding
happened. A codebase with the first and without a guard against the second
produces confident wrong answers *and* a green test suite, which is how a defect
survives seven weeks and ~70 restarts.

Both recurred *inside the fixes for themselves* during this release — the
installer's model planner reproduced defect H3, the benchmark harness shipped
the name-shape bug twice, and while fixing a command that exited 0 on failure we
found `sys.exit(False)`, which is also 0. Treat them as live, not historical.

---

## 2. Fixed in this release

Listed because several were long-lived and you may have hit them.

- **Server could not start** — a module-level use-before-definition in `agent.py`.
  Now guarded by a ~3.5s import check wired into launch, pre-commit and CI.
- **Silent startup failures** — the tray now captures child stdout/stderr to
  `~/.friday/server_stderr.log` and distinguishes "failed to start (exit N)" from
  "stopped". `_wait_for_health` widened 30s → 300s against a measured 143s cold start.
- **Blueprint registration failures were invisible** — now tiered. A required module
  failing exits loudly; an optional one starts but announces in `friday.log`, a
  high-priority notification, and `~/.friday/startup-report.json`. `GET /api/startup-report`
  serves it. Proved by fault injection, not the happy path.
- **Background tasks reported success on provider errors** — any task whose reply is a
  provider failure is now marked `failed` and notified as failed.
- **Tool results were never appended on the normal path** in `_call_claude_agent` — a
  block had been spliced into the middle of a loop, leaving the append inside an `except`.
- **Creation filenames tripped the PII detector** — timestamps moved to ISO form, so
  Friday can see her own output again.
- **Tool descriptions were stripped before reaching the model** — now scoped to MCP
  tools only; first-party descriptions are static documentation and are never gated.
- **`/api/mcp/status` reported a working subsystem as dead.**
- **`friday doctor` reported a model installed when a sibling tag was installed.**
- **The one-line installer cloned a repository that does not exist** (`friday-desktop.git`
  → `Agent-Friday.git`), and pointed at `setup_wizard.py` in the wrong directory. Both
  fatal, in both the PowerShell and shell installers. Nobody had run them end to end.
- **The installer regenerated `index.html` from a dead mirror**, deleting 17 components
  that exist only in the served file and silently downgrading to CDN Babel.
- **TIER-2 sensitivity over-triggering** — ordinary words like "family" now require a
  possessive or personal frame.
- **Local voice had no brain** — the local session now pins a resident seat and refuses
  to start if none exists.

---

## 3. Still broken

### Blocking for a packaged release

**The career pipeline cannot work in a pip install.** `pyproject.toml` packages only
`src/`, but `routes/jobs.py` imports `data.job_tracker_schema`, `skills.application_engine`
and `skills.job_scanner` — all top-level directories at the repo root, none of which ship
in the wheel. A user who runs `pip install agent-friday` gets four `/api/pipeline/*`
endpoints that cannot function under any circumstances, because the modules are not on
their disk.

They will at least now be *told* — the blueprint policy announces "Career pipeline
unavailable" rather than logging a warning nobody reads. But being reliably informed that
a shipped feature is permanently broken is not the same as shipping a working feature.

**Status: source installs work, pip installs do not.** Fixing it properly means deciding
whether bundled skills are Python package internals or first-class installed content, and
that is a product decision about what the skills system *is*, not a packaging typo. It is
deliberately not being answered inside a bug fix.

### Local Friday converses and remembers. She does not act.

**`function_manager` is a role in the contract that nothing consults.**

It exists in `residency_policy.py` with a residency class (`RESIDENT`, commented
*"sits inside the tool loop"*), a context budget, and a slot in `ROLE_RESIDENCY`.
It has a default settings entry at `core/__init__.py:1661` — with an empty model
string. It has a UI label, "Tool calling", in `routes/intelligence.py:85`.

It appears **nowhere** in `agent.py`, `routes/chat.py`, `routing/`,
`local_seats.py` or `local_call.py`. `local_seats._ROLE_TO_CAPABILITY` cannot
even resolve a `"function"` role — it knows `brain`, `judge`, `sidekick`,
`extractor`, `heavy`.

The consequence, stated plainly because it is the honest summary of local-only
Friday today:

> **With a local model as the brain, Friday can hold a conversation and use her
> memory. She cannot use her tools — no reading files, no searching, no
> calendar. Tools require a cloud API key.**

This is **not** a hardware limitation and must not be described as one. A local
model that supports native tool calling would not help, because nothing
delegates the decision to a function seat in the first place. Someone told
"your machine is too weak for tools" would buy a better machine and get the
same result.

Wiring it is real work and is not scheduled. `functiongemma:270m` — the model
the role was presumably intended to use — genuinely does emit tool calls, and
does so in 358 ms on CPU, so the model side is not the obstacle.

### Other open defects

- **The `egress_mode` setting is read by nothing.** Settings → Privacy → EGRESS GATE
  (Audit/Enforce) is a dead control. The direction is safe — the gate always enforces —
  but a privacy toggle that does nothing is a credibility problem regardless.
- **Two safety gates have no callers**: `boot_guard.check_self_edit()` and
  `check_scope()`.
- **The model picker shows a hardcoded list of three models** while hundreds may be
  available.
- **Settings keys absent from `DEFAULT_SETTINGS` are silently discarded on save**, and
  the API returns success. Hit three times so far.
- **The sampling progress bar never advances** during image generation.
- **Cancel loses a race** — cancelling between the orb registering and the arbiter
  granting a lease says "nothing to interrupt", and the job completes anyway.
- **Chain retry has a race and a false-complete.** The retry counter is written after the
  worker thread starts, so a fast-failing step can retry past its budget; and an exhausted
  retry logs but never flips status, so a step that shipped nothing reports `completed`.
- **`ui_parts/app.html` is a hand-maintained mirror of `index.html` that nothing builds
  from.** `index.html` is the source of truth — it is a strict superset, containing 17
  components the mirror lacks. The mirror is kept in sync by hand and will drift.
- **Chain seat overrides are advisory**, not enforced against the capability router.

### Seat contention

The GPU is effectively full: a resident 12b holds ~10.2 GB of a 12 GB card, leaving
~618 MiB. Any image generation or second GPU seat will fail to allocate. The mitigation
being adopted is moving the embedder and function seats to CPU (see §5), which does not
evict anything but does not create headroom on the card either.

---

## 4. Unverified — we do not know

Listed separately from "broken" on purpose. These are not claims that things work.

- **No clean-machine install has been performed.** Everything below "the installers now
  point at the right repository" is untested on a machine that has never seen this code.
  The clone URL fix in particular is verified only by checking that the corrected URL
  resolves and the old one does not.
- **Local voice end to end is unverified.** The fix pins a resident brain and the code
  compiles, but no one has yet spoken to Friday, received a spoken answer, and confirmed
  from the egress log that nothing left the machine. Until that happens, treat local
  voice as unproven rather than working.
- **CPU-only inference throughput is unmeasured for any generation model.**
- **8 GB VRAM is unverified.** The hardware fixture claiming to represent it carries
  measurements copied from a 12 GB card. Nobody has run it.
- **The KV-cache slope that the entire seat-planning model rests on is INFERRED**, from
  two anchors taken on two different backends. The code's own note calls it "the weakest
  number here and everything downstream leans on it." A directly measured slope on one
  model was an order of magnitude away from it.
- **Whether all Telegram/Discord sends route through the sealing manager**, or whether
  the bridge modules are also called directly.
- **Which ffmpeg build `imageio-ffmpeg` ships** — LGPL or GPL. This has licensing
  consequences for any binary release.
- **Whether earlier server deaths shared the cause found in this one.** The import error
  explains the failures after it landed. Deaths before it had a different cause that this
  investigation does not reach, and stderr was discarded in both cases, so the two failure
  modes are indistinguishable in the surviving evidence.

---

## 5. Things that leave your machine

Stated here because a sovereignty product owes you the enumeration, and because none of
this was documented before.

There is **no telemetry, analytics, crash reporting, phone-home, update check, or license
check** in this codebase. That was verified by exhaustive grep and it is a real result.

But four things do leave on a default install with zero keys configured:

1. **A TCP connect to `dns.google:443` every 30 seconds, forever** (fallbacks `8.8.8.8`,
   `1.1.1.1`). No payload — but it reveals your IP and continuous uptime to Google and
   Cloudflare roughly 2,880 times a day.
2. **HTTP GETs to ~39 news feeds every 5 minutes, forever, by default.** No user data in
   the request; the pattern is the signal.
3. **`fonts.googleapis.com`** on every launch, from `index.html`.
4. **Three MediaPipe bundles from `cdn.jsdelivr.net`**, none carrying SRI hashes.

Items 3 and 4 are the largest inconsistency in the product: a "nothing leaves your
machine" application that contacts Google on startup. Self-hosting them is cheap and is
not yet done.

The egress gate itself is strong, and narrower than the README implies. It covers
Anthropic, OpenAI-compatible providers including OpenRouter, and Gemini. It does **not**
cover: web search queries (sent to Brave or DuckDuckGo), Firecrawl, ElevenLabs TTS text,
images and audio sent to Gemini, Google Calendar event content, or a content hash sent to
`freetsa.org`. Those are real third parties receiving user text, and they are outside the
guarantee as written.

---

## 6. Platform reality

**Agent Friday runs on Windows 10/11 with an NVIDIA GPU.**

macOS and Linux can run the server, the web UI, cloud providers, and local chat through
Ollama. They cannot run the system tray, the residency layer (llama-server seats),
GPU-aware seat planning, or OS-protected credential storage.

- On non-Windows, **credentials fall back to plaintext** unless `FRIDAY_PASSWORD` is set.
- **Apple Silicon is explicitly refused** by the residency planner — no MLX or Metal
  backend exists in the tree.
- **AMD GPUs are invisible on every platform** — `nvidia-smi` is the only probe.
- On Linux, no llama-server seat can load at all (the engine candidates are `.exe`), so
  you are Ollama-only.

**Minimum: 16 GB system RAM.** Not a recommendation — at 8 GB, Friday's own budget rule
resolves to zero available memory and refuses every model seat. Earlier documentation
claimed 8 GB; the code always disagreed.

---

## 7. Security posture

- **Keys on disk.** The setup wizard has historically written API keys and the vault
  passphrase as plaintext `SET` lines into launch scripts, and those plaintext values
  *override* the encrypted store. Documentation claiming keys are "never stored as
  plaintext" was false as written. Treat any launch script as containing live secrets.
- **`FRIDAY_SECRET_KEY` ships as a known default string.** A known Flask session secret
  means forgeable sessions on any exposed instance. Generate a real one.
- **The vault crypto tests are excluded from CI**, along with the MCP client, integration,
  regression and edge-case suites.
- **`web_fetch.py` and `web_safety.py` — the SSRF guard — have zero tests.**
- **`calendar_write.py` writes to a live Google Calendar, including deletions, with zero
  tests.**
- **Dependencies are unpinned.** Every requirement is `>=`, there is no lockfile, and CI
  installs unpinned. You cannot assert what you ship if you cannot assert what versions
  you ship.

---

## 8. Licensing, unresolved

- **`mutagen` is GPL-2.0** and is currently reachable in the `[all]` extra. Statically
  bundling it into a distributed MIT binary triggers GPL obligations on the combined work.
  It is not in the PyInstaller excludes list.
- **`pynput` and `pystray` are LGPL-3.0**, and `pynput` is in `hiddenimports` — i.e.
  deliberately frozen in. LGPL §4 requires relinkability, which a onefile build removes.
- **Model licenses are not surfaced anywhere.** Gemma models carry use restrictions and
  a downstream pass-through obligation, and Gemma is the *default* local model. Llama
  models carry the 700M-MAU clause. Stable Diffusion 3.5 carries a revenue-conditioned
  license with an attribution requirement — a fact that currently lives only in a Python
  dict comment.
- **There is no `NOTICE` file.** Vendored JavaScript in `static/vendor/` — three.js,
  React, marked, highlight.js — is checked in with zero attribution.

These block a **binary** release. A source release can proceed.

---

*If this file seems long, that is the point. It is shorter than the list of things that
work.*
