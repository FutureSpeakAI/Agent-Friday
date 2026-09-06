# Startup receipts — an input to the voice-failures spec

*Written 2026-08-25 for the session cataloguing the 35 voice failures. Not a
proposal to build separately — this belongs inside that spec's mechanism if the
shapes agree, and the point of handing it over is to get one mechanism instead
of three.*

## The paragraph

Three of today's bugs were invisible for the same structural reason: a subsystem
resolved its configuration at startup and then never said what it resolved to,
so the only way to learn the answer was to reproduce the failure. GPU voice
reported "NeMo GPU voice ready" while its TTS half could not load at all;
the privacy classifier's docstring claimed four layers while two had never run;
the audio player silently chose between a clean worklet and a legacy scheduler
known to rasp, and logged which only behind an env var that also had to survive
a tray that sends stdio to DEVNULL. In each case the *declared* configuration
was available everywhere and the *resolved* one was available nowhere, and every
hour spent was spent rediscovering a fact the process already knew. The fix is
a startup receipt: each subsystem registers a callable returning what it
actually resolved — not what was requested — and one pass at boot writes them
all to a single artifact, unconditionally, with no debug flag and no reproduction
required. Friday already has the artifact (`~/.friday/startup-report.json`,
written at every boot, served by `routes/startup_report.py`); it currently
carries only which blueprints registered, so the work is adding a reporter
registry beside `BLUEPRINT_REPORT` in `server.py` and folding it into the same
write, not building a mechanism. The discipline that makes it worth anything is
the distinction the bugs all turned on: `voice_max_tokens: 0` is a *configured*
value and tells you nothing, `reply cap = 300` is a *resolved* one and would have
caught this week — so a reporter that echoes settings back is worse than no
reporter, because it looks like verification.

## Why this is offered rather than built

The thesis in the voice-failures spec — tool calls that never reach the executor,
and a model narrating a result it never observed — is the same defect one layer
up: state that is asserted rather than confirmed. A receipt that says which tools
were *handed to the API* (not which the prompt advertises), and a tool trace that
distinguishes *executed* from *claimed*, are the same mechanism as a receipt that
says which audio path is live. If that spec defines the shape, this folds into it.

## Concrete first reporters, if wanted

Ranked by how much time their absence has already cost:

1. **Privacy layers** — which layers actually ran, by name, with the reason any
   are absent. Replaces a docstring that overclaimed by two.
2. **Seats** — for each capability role: the model requested, the endpoint
   resolved, and the model that *answered* when asked. `local_call.log_dispatch_table()`
   already produces exactly this; it just isn't part of the receipt.
3. **Voice** — resolved tier, ASR/TTS backends, the reply-token cap, and the tool
   count actually passed to the API.
4. **Client audio** — playback path and context sample rate. Already shipped as an
   unconditional console line; it wants to reach the server-side receipt too, since
   the console is exactly where a desktop user cannot see it.

## The one rule

Report what resolved, never what was requested. If a reporter can be satisfied by
reading the same settings file the subsystem read, it is not a receipt.
