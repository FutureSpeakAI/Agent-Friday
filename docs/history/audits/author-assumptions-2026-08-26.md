# Where Friday still assumes her operator wrote her

Swept 2026-08-26, after the first install on a second person's machine.
Fixed items are listed for completeness; the open ones are the point.

The framing, which is Stephen's: **every path that requires knowing where a
JSON file goes, or opening a code editor to set a key, is a path that was
never walked by someone who didn't build it.** Each finding below is that
same sentence wearing different clothes.

---

## Fixed today

| What | Where | Commit |
|---|---|---|
| Cloud-only mode was overruled by a keyless local fallback | `routes/chat.py` | `0734377` |
| The API-key panel existed and was unreachable | `index.html` | `0734377` |
| No way to find out where a key comes from | `provider_descriptors.py`, `index.html` | `9bac402` |
| The wizard reported Google connections it never made | `setup_wizard.py` | `597f1ea` |
| Provider choice never reached the router | `setup_wizard.py` | `d29785f` |
| 14 signposts naming Settings tabs that do not exist | docs, installer, services | `65f70ce` |
| Google OAuth: the wall, the options, the numbers | `docs/design/google-oauth-onboarding.md` | `570c538` |

---

## Open — user-facing errors that require a terminal

Each of these is shown to a user, in the product, and cannot be acted on
without a shell. They are the same defect as "place a JSON file at
~/.friday/credentials.json", and they are all small.

1. **`routes/voice.py:1167`** — sent over the voice WebSocket:
   > Local voice needs the Tier-1 deps. Install with
   > `pip install -e .[voice-local-lite]`, then reload.

   A person who pressed the microphone is told to run a package manager.
   Friday installs her own dependencies elsewhere (`services/agent.py:776`
   has a pip runner); this should offer a button, or degrade silently to
   the cloud voice path and say so in one sentence.

2. **`services/agent.py:3704`** — returned as a tool result:
   > pyautogui not installed. Run: pip install pyautogui

3. **`services/creative_engine.py:1104`**:
   > update the SDK: pip install -U google-genai

4. **`services/capability_preflight.py:86-103`** — install hints are pip
   commands, surfaced through `/api/health`. The manifest's own text already
   names the problem, and has for a while:

   > routes/core_routes.py::analyze_file returns a stub telling the user to
   > pip-install a library that is Friday's own dependency, not theirs

   The codebase diagnosed this one itself and then shipped it.

5. **`services/elevenlabs_tools.py:87`**:
   > no ElevenLabs API key configured. Set ELEVENLABS_API_KEY (start.bat or …)

   Names a batch file. Now that Settings -> Providers is reachable, this
   should point there like the others do.

6. **`README.md:71`** — "Run `friday models` to see what your machine can
   hold". True and useful for Stephen. There is no in-app equivalent.

---

## Open — two more tabs built and never wired

`SettingsTabProviders` was not alone. Checking every `SettingsTab*`
component against the render chain:

| Component | Size | Rendered |
|---|---|---|
| `SettingsTabModels` | 176 lines (CAPABILITY ROUTING / REASONING / RESPONSE) | no |
| `SettingsTabOrchestrator` | 22 lines (CAPABILITY CARD / WORK LOG) | no |

Both correspond to the two orphaned `false,` entries left in the Settings
render chain. Both appear superseded by `SettingsTabIntelligence`, which
carries WHERE WORK RUNS / WHAT RUNS EACH JOB / THE MACHINE / PROVIDERS.

Unlike Providers — which docs, error messages and two live buttons all
pointed at — nothing points at these two any more, now that the "Settings ->
Models" signposts have been repointed at Intelligence.

**Recommended: delete them.** Reviving a stale settings surface that writes
to `capability_routing` is how the seat-clobbering class of bug gets a new
door. Dead code that renders nothing is safer than dead code that renders.

---

## Open — stale signposts in comments

Eight remain, deliberately unfixed: `Settings -> Cost & Usage`,
`Scheduled Tasks`, `Experimental`, `Hardware`, `Active Hooks`. They are
comments and docstrings, so they mislead a developer rather than a user.
`tests/unit/test_settings_signposts_are_real.py` scopes itself to string
literals for exactly this reason and will not catch them. Worth a pass, not
worth a release.

---

## Open — the Google OAuth wall

Specced separately in `docs/design/google-oauth-onboarding.md`. It is the
largest remaining instance of this pattern and the only one that is a
business decision rather than an implementation. Q-G1 (is Friday a product
for strangers, or a tool for people Stephen knows?) decides the rest.

---

## The check worth keeping

The single most useful artefact from this sweep is not any individual fix.
It is `tests/unit/test_settings_signposts_are_real.py`, which parses the
real tab list out of `index.html` and asserts that every place Friday tells
a user to go is somewhere they can actually get to.

It would have caught the Providers bug on the day it was introduced. The
general lesson: **the product needs tests that navigate the way a stranger
navigates**, because the author never will.
