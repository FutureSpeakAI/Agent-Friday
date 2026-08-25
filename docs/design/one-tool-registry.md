# One tool registry — how filesystem action works on every surface

**Date:** 2026-08-25
**Branch:** `higgsfield-integration`
**Status:** design, with the first increment BUILT (§7). The rest is specified, not built.
**Commit state (2026-08-25 09:50):** §7.4–§7.6 are committed. §7.1–§7.3 — the voice
tool-surface work — are BUILT AND RUNNING but deliberately **not committed**, because
`routes/voice.py` is simultaneously held by another session's governance-ring and
audio-threading work (§2.2). The pair `voice_engine.py` + `routes/voice.py` cannot be
split: committing the former alone would declare fifteen tools to the Live API while
the latter's surface note still named nine, which is this document's defect inverted.
Sequencing is Stephen's call.
**Scope:** the tool surface across all four model/modality combinations — local model
and cloud model, text and voice — and specifically how local filesystem action works
for a cloud model given the egress gate.

**Supersedes nothing.** It generalises
[`voice-tools-and-transcript-collision.md`](voice-tools-and-transcript-collision.md)
(2026-08-24), which diagnosed one instance of this defect on one surface. Where the two
touch, that document supplies the voice-specific detail and this one supplies the rule.
It also depends on [`context-assembly.md`](context-assembly.md) §3.1–§3.7 for the
CORE/DEFERRED split and seat arithmetic, which stand unamended.

**Evidence registers:** **MEASURED** (run on this machine, output quoted) /
**VERIFIED** (read in the code, path traced) / **INFERRED** / **UNKNOWN**.

---

## 0. The answer, up front

**Stephen's question was "is this Friday, or Gemini, or both?" The answer is Friday,
almost entirely.** Gemini's Live API did what it was told. It was told about nine tools
and it called two of them. Everything else Friday narrated, she narrated because her
system prompt told her she had roughly thirty-five tools and her actual surface
contained nine.

One qualification, and it is small: `gemini-3.1-flash-live-preview` — the model that was
seated (**MEASURED**, `settings.json` `voice_model`) — supports *synchronous* function
calling only; Google's Live API tools page states asynchronous function calling is not
yet supported on it. That constrains a future design decision (§6). It is not why the
file never appeared on the desktop.

**The rule this document proposes:**

> There is one tool registry. Every surface derives its inventory from that registry by
> explicit policy. Every system prompt is generated from the resolved inventory. A
> capability whose dependency is missing is **absent from the registry**, not present
> and broken.

Stephen asked for that position to be tested rather than agreed with. §5 tests it. Three
of its four clauses survive intact. The fourth — "each surface derives by explicit
policy" — needs one amendment: the policy must be a *filter over the registry*, never a
*second list*, because a second list is exactly what the voice surface already was.

---

## 1. What was actually true, measured

### 1.1 The running process was 21 hours stale

At the time Stephen's transcript was produced, the process serving Friday was PID 28404,
`venv\Scripts\python.exe server.py`, started **2026-08-24 11:49:44** (**MEASURED**,
`Get-CimInstance Win32_Process` + `Get-NetTCPConnection` on port 3000).

Dating the code inside it precisely: the log line `core tools trimmed` was introduced at
`a6fdad0` (08-24 11:28), 21 minutes before that process started, and it appears in
today's log. `_surface_override` — yesterday's tool-budget fix — has never been committed
and did not appear. So the running process contained code as of roughly `efc7d01`
(08-24 11:46) and **nothing after it** (**MEASURED**).

That process therefore did *not* contain:

| Fix | Where it lived | In the running process? |
|---|---|---|
| `spawn_task` on the voice surface | uncommitted working tree | no |
| `_voice_tool_surface_note` (the prompt override) | uncommitted working tree | no |
| `_surface_override` (the text-path equivalent) | uncommitted working tree | no |
| five privacy fixes | committed 08-25 07:37–08:08 | no |
| New Chat fix | committed 08-25 08:08 | no |

Stephen hard-reloaded the browser for a frontend fix. A hard reload replaces the client;
it does not replace the Python process. **The first correct answer to a large part of
that transcript was "restart", not "redesign."**

**This is now historical.** Friday restarted at **08:55:43** today (**MEASURED**; the new
server is PID 26616, and it owns port 3000). Everything in that table is live. §8 states
exactly what that changed.

### 1.2 The voice surface had nine tools; the prompt advertised about thirty-five

`services/voice_engine.py::_VOICE_LIVE_TOOLS` at HEAD is a hand-written table of nine
entries: `query_calendar`, `check_email`, `search_news`, `search_web`, `open_url`,
`get_source_trust`, `get_article_deep_dive`, `search_wiki`, `navigate_workspace`
(**MEASURED**, enumerated from `git show HEAD:`). The historical log confirms the count
the session actually shipped: `voice tools enabled: 9 declarations`.

The system instruction handed to that session is built from
`_get_friday_system_prompt()` — the **text-chat** prompt — whose `== AVAILABLE TOOLS ==`
block is a compile-time constant in `services/model_router.py:1892` naming `read_file`,
`write_file`, `open_path`, `search_email`, `screenshot`, `run_command` and about thirty
more (**VERIFIED**). It also instructs, of the browser and file tools, that she must
never tell the user she cannot do those things.

A model told it has `write_file`, not given `write_file`, and told never to deny that it
can write files, has exactly one move left. It says "writing that to your desktop now."

### 1.3 The transcript matches that hypothesis exactly

`_voice_tool_run` gates every result through
`egress_gate._gate_text(result, "google-gemini", f"live.tool.{fname}")`
(`routes/voice.py:1784`), and the gate logs every call (**VERIFIED**). That makes
`friday.log` a complete per-call receipt for the voice surface.

For the whole of Stephen's session, the log contains (**MEASURED**):

```
08:18:03 ALLOW provider=google-gemini field=live.tool.navigate_workspace tier=TIER_1
08:23:12 ALLOW provider=google-gemini field=live.tool.open_url          tier=TIER_1
08:23:28 ALLOW provider=google-gemini field=live.tool.open_url          tier=TIER_1
08:24:07 ALLOW provider=google-gemini field=live.tool.navigate_workspace tier=TIER_1
```

Four calls. Two tool names. **Exactly the two Stephen confirmed worked.** There is no
`live.tool.read_file`, no `live.tool.search_wiki`, no `live.tool.write_file`, because
those tools were never declared and so could never be called. Every other action in that
transcript was narration.

### 1.4 Correction to a lead: orbs are not the ground truth on voice

Stephen's premise was that a process orb appears whenever a tool executes, so no orb
means nothing ran. **The premise does not hold on the voice surface.** `voice_engine.py`
and `routes/voice.py` contained **zero** calls to `process_register` / `process_update` /
`process_log` (**MEASURED**, grep). The text agent loop has emitted an orb per tool call
for a long time (`agent.py::_orb_tool_trace`); the Gemini Live path emitted none.

So orb-absence was not evidence of non-execution — it was evidence of nothing at all. The
conclusion Stephen drew was right; the instrument he drew it from was not connected. The
egress log was the real receipt. §7.3 connects the instrument.

### 1.5 The wiki search did not fail — it never ran

`_tool_search_wiki` is a pure filesystem walk over `WIKI_DIR` and `~/.friday/wiki`
(`agent.py:1143`). It touches no model and no seat (**VERIFIED**). So the dead
`:8090` seat could not have made it return empty. And there is no `live.tool.search_wiki`
line in the log. It was never called. "Found nothing" was narration, not a failure
reported as an empty result — which is a cleaner and less alarming answer than the
lead suggested.

**This conclusion survives the restart** (§8): it never depended on the seat's state.

### 1.6 The PDF gap is the Presidio gap again

Friday replied "Install pdfplumber for full analysis: pip install pdfplumber". Measured:

* `pdfplumber` was in **no** requirements file (**MEASURED**, grep over `requirements.txt`).
* It was in **no** PyInstaller hidden-import list (**MEASURED**, `AgentFriday.spec`).
* It was **not installed** in the venv (**MEASURED**, `importlib.util.find_spec`).

The PDF branch of `/api/analyze-file` had therefore never worked in this environment,
and its failure message handed the user a pip command for *Friday's own* missing
dependency, as though it were their chore. Identical in shape to the 08-24 finding that
the sensitivity classifier's Layer 2 had never once run.

### 1.7 `open_url` refuses local paths — correctly, but with no way forward

`_tool_open_url` rejects anything not `http(s)://` (**VERIFIED**). That is right: it is a
web tool. But asked to show a briefing it had just written, the model had no local-file
tool on the voice surface, so it invented a placeholder URL and opened that. The
capability it needed — `open_path(path, in_browser=true)` — already exists in the text
registry and was simply not on the voice surface.

---

## 2. The four surfaces, and how far apart they were

| Surface | Where | Tool inventory | Source of that inventory |
|---|---|---|---|
| Text chat, cloud seat | `_call_claude_agent` | 67, budget-trimmed | `CLAUDE_TOOLS` |
| Text chat, local seat | `_call_ollama` → `_call_openai` | 67, trimmed **to 5–12** | `CLAUDE_TOOLS` + `tool_budget` |
| Voice, local (`/ws/voice-local`) | `_generate_agent` | 67, budget-trimmed | `CLAUDE_TOOLS` |
| Voice, cloud (`/ws/live`) | Gemini Live | **9, hand-written** | `_VOICE_LIVE_TOOLS` |

All four received the **same** system prompt block naming ~35 tools.

Two things in that table are worth pausing on.

**First, the local voice surface was more capable than the cloud voice surface.**
`/ws/voice-local` runs the ordinary agent pipeline, so it had `read_file` and
`write_file` all along. The Gemini Live surface — the better model, the nicer voice, the
one Stephen actually uses — was the impoverished one. Nobody decided that.

**Second, the local text seat's trimming is severe and was measured today**
(**MEASURED**, `friday.log`):

```
08:26:07 gemma4:12b: core tools trimmed 67 -> 6  (~671 of ~11131 tokens) ... ~27477-token prompt
08:35:47 gemma4:12b: core tools trimmed 67 -> 12 (~1224 of ~11131 tokens) ... ~26877-token prompt
08:18:52 gemma4:12b: core tools trimmed 67 -> 62 (~9009 of ~11131 tokens) ... ~18986-token prompt
```

A 32,768-token seat, a prompt already consuming 27k, and six tools survive. The prompt
still says thirty-five. `_surface_override` (uncommitted at the time) is the correction,
and it is the right shape — but note *what* it corrects: it patches the prompt to match
the surface after the fact. §4 argues the generation should run the other way.

### 2.1 On the parallel session's local-path finding

The echoOLlama / NVIDIA VoiceChat evaluation reported that on local voice paths,
can't-call-tools is usually **the tool schema not being passed into the completion call
at all**, while the text path passes it fine.

**Checked against Friday's code, and it does not hold here** (**VERIFIED**, path traced
end to end): `routes/chat.py:835` calls `_call_ollama(tools=_local_tools, ...)`;
`_call_ollama` forwards to `_call_openai`; `_call_openai:850` sets
`payload["tools"] = _oai_tools`; the loop sends it at `model_router.py:886`. The schema
is passed.

But the *equivalent* defect is present, latently, and it is worth having found. Both
conversion sites read:

```python
try:
    oai_tools = anthropic_to_openai_tools(tools)
except Exception:
    oai_tools = None
```

A conversion failure produces `None`, which does not raise, does not log, and simply
omits the `tools` key. The turn runs tool-free and the model narrates. That is precisely
the failure the parallel session describes, arriving by a different route — a *silent
drop* rather than a *never-wired*. It is fixed in §7.5.

So the architecture argument does hold across all three surfaces; the mechanism differs
per surface, which is itself the point. **Voice-cloud lost its tools to a second
hand-written list. Text-local loses them to arithmetic. Both paths could lose them to a
swallowed exception.** Three mechanisms, one defect: *the model is told it has
capabilities its actual surface does not contain.*

### 2.2 A third instance, found independently: the missing session context

While this document was being written, the parallel voice session root-caused a
separate report — Friday claiming *"the vault prohibited it"* — and landed on the same
structural defect through a **third** mechanism.

`_governance_check` decides ring-2 access with
`is_auth = ctx.get("authenticated") or ctx.get("is_background_task")`. The live voice
handler passed **no session context at all**. The gate therefore saw `{}`, concluded
"ring-2 network op requires authenticated session", and denied `search_news`,
`search_web` and their neighbours. `routes/chat.py` has always passed
`session_ctx`. **Voice was the only tool-using surface that did not.**

This is worth more than the two failures that prompted this document, because it is the
first one neither of us was looking for, and it lands on a *different gate*. The
inventory was not the problem there; the **execution context** was. Same shape all the
same: a surface that grew its own path to a shared mechanism, and drifted from it
because nothing forced the two to agree.

So the count is now three mechanisms, three gates:

| Surface | What it lost | To what |
|---|---|---|
| voice-cloud | its tool *declarations* | a second hand-written list |
| text-local | its tool *declarations* | seat arithmetic |
| voice-cloud | its *authorisation* | an unpassed `session_ctx` |
| any | its tool *declarations* | a swallowed conversion exception (§2.1) |

**This sharpens R2.** "Derives its inventory from the registry" is not sufficient. A
surface must derive its **whole relationship to the tool layer** — declarations *and*
execution context *and* receipts — from one place. §7.1's fall-through does this by
construction: it routes through `_execute_tool` and passes `authenticated: True`, so
the ring gate, the vault gate, the sandbox and the rate limiter all see a real context.
But it does that for the five *borrowed* tools only. The nine native voice tools still
dispatch directly to their handlers and still bypass `_execute_tool` entirely — which
is why the parallel session's fix is needed, and why the two changes belong in one
sequence rather than one file.

**The end state that both fixes point at:** `_voice_tool_run` should not have a dispatch
chain at all. Every voice tool — native and borrowed alike — should resolve from the
registry and execute through `_execute_tool` with a session context built once at
connect time. That deletes the hand-written table, the hand-written dispatch, and the
unpassed-context bug in a single move. It is the natural §9 item 6, and it should not be
attempted while two sessions hold the file.

---

## 3. The rule

> **R1.** `CLAUDE_TOOLS` is the single source of truth. Nothing else declares a tool.
>
> **R2.** A surface's inventory is a **filter over R1**, expressed as a list of names
> plus a policy, never a second table of declarations.
>
> **R3.** The declaration a surface ships is **rendered from R1's schema**, so a
> description edited once reaches every surface in the same commit.
>
> **R4.** The system prompt's tool section is **generated from the resolved inventory**,
> after filtering, per turn.
>
> **R5.** A tool whose dependency is missing is **removed from R1**. Not disabled, not
> failing at call time — absent.
>
> **R6.** A surface that cannot render a tool's schema **drops that tool and logs it**.
> It never ships a lossy declaration.
>
> **R7.** Every executed tool call emits a receipt on every surface.

R5 deserves its own sentence, because it is the one that also covers the Presidio and
pdfplumber class of bug: **"present but broken" is a lie with extra steps.** A model
handed a `screenshot` tool that always returns "pyautogui not installed" will announce
the screenshot first and discover the problem second. A model never handed the tool says
it cannot take screenshots, which is true.

---

## 4. Where Stephen's starting position needs amending

He proposed four clauses. Testing each:

**"One tool registry is the single source of truth."** Survives. `CLAUDE_TOOLS` already
is, for three of four surfaces. The work is subtraction — deleting the second list — not
construction.

**"Each surface derives its inventory from that registry by explicit policy."** Survives
*with an amendment*. The danger is that "policy" gets implemented as a per-surface
config that redeclares the tools, which is `_VOICE_LIVE_TOOLS` again with better
manners. The policy must be **names only**; the declarations must be resolved. That is
R2 and R3, and it is why §7.1 stores `_VOICE_SHARED_TOOLS` as a tuple of strings and
nothing else.

**"The system prompt is always generated from the resolved surface, never
hand-maintained."** Survives, and is the clause with the most work left. Both existing
override notes (`_voice_tool_surface_note`, `_surface_override`) are *corrections
appended after* a hand-maintained constant. They work, but they leave the false list in
the prompt and argue with it. The end state deletes `== AVAILABLE TOOLS ==` from
`FRIDAY_SYSTEM_PROMPT` entirely and generates that section per turn. §7.2 makes the
generation real; the deletion is §9's first item, deliberately not done today because it
touches the honesty golden tests.

**"A capability whose dependency is missing is absent from the registry."** Survives,
and generalises further than proposed: the same table should also drive what the *user*
is told (`/api/health/capabilities`), because a packaging gap the user cannot see is how
both the Presidio and pdfplumber gaps survived as long as they did.

**One thing the position does not cover, and needs to:** *what happens when the filtered
inventory still does not fit the seat.* Trimming is not going away — a 32k local seat
cannot hold 67 tools next to a 27k prompt. So R4's "generated from the resolved
inventory" must mean *resolved after trimming*, and trimming must run before prompt
generation, not after. Today it runs after, and the override note exists to paper over
the ordering. §9 item 2.

---

## 5. Filesystem access for a cloud model, given the egress gate

This is the part Stephen asked to be checked rather than assumed, and the code is
already right — it just was not reachable from voice.

**The shape is: the tool executes locally; only its result is tier-gated.** Concretely,
for a cloud-model turn:

1. The model emits a tool call. That call carries a *path*, not file contents.
2. `_execute_tool` runs the handler **on this machine**. Nothing has crossed the network.
3. The result string goes back to the provider — and *that* is the egress event.
4. On the text path, `egress_gate` descends the message structure and gates
   `content[].tool_result` field-wise. Today's log shows this working, per-message:
   `BLOCK provider=anthropic field=message[54].content[0].tool_result tier=TIER_3
   span-level: withheld 1/33 paragraphs (31 trusted)` (**MEASURED**).
5. On the voice path, `routes/voice.py:1784` gates the result before
   `send_tool_response`, and a fully-withheld result becomes an explanatory marker so the
   model reports the withholding rather than retrying the tool (**VERIFIED**).

So "the cloud model can't touch the disk" was never the design, and it should not become
one. Reading a PDF in `Downloads` is not vault content. The gate already distinguishes
them **by classifying the bytes**, span by span, at the moment they would leave.

This respects the constraint established on 08-24: the provenance registry is
content-addressed at ingest and there is deliberately **no send-time exemption API**. The
design above adds no exemption. It adds no `is_vault=False` flag, no path allowlist that
bypasses classification, no "this tool is safe" annotation. A file read is gated on
exactly the same terms as any other text, and if `read_file` returns vault contents the
gate withholds them — which is the correct outcome, and the model is told it happened.

**One consequence worth stating plainly.** Ring policy governs *whether the tool runs*;
the egress gate governs *what may be spoken about the result*. They are different
questions and must not be collapsed. A cloud model may run `read_file` (ring 0, always
permitted) and then be told most of the result was withheld. That is the system working.

---

## 6. Async, and why `NON_BLOCKING` is not the fix

Stephen's hypothesis was that a model firing a call and continuing to talk is a
configuration error, not a capability limit. Reasonable, and worth stating why it is not
what happened here.

Per Google's Live API tools documentation, the **default** for a function declaration is
blocking: calls "pause all interactions with the model until responses arrive."
`behavior: "NON_BLOCKING"` opts out, and the matching `FunctionResponse` then carries
`scheduling` — `INTERRUPT`, `WHEN_IDLE`, or `SILENT`.

Friday sets no `behavior` field anywhere (**MEASURED**, grep across `voice_engine.py`
and `routes/voice.py`). Every voice declaration is therefore blocking, which is the
*safe* default. **Adding `NON_BLOCKING` would have made the reported symptom worse**, not
better: it is the setting that permits talking over a pending call. Friday was not
talking over a pending call. There was no pending call.

Two real async considerations remain, for later:

* A blocking call freezes the session while it runs — the "freeze then stutter" that
  `VOICE_TOOL_CHOREOGRAPHY` exists to manage by prompt. For genuinely slow tools
  (`search_email`, `get_article_deep_dive`), `NON_BLOCKING` + `scheduling: WHEN_IDLE` is
  the right instrument. **`read_file` and `write_file` are not slow and should stay
  blocking** — a file write must complete before Friday says it completed.
* `gemini-3.1-flash-live-preview`, currently seated, **does not support async function
  calling at all**. Any async design must gate on the model, and the gate must fail to
  blocking rather than to silence.

---

## 7. What was built today

All of it lives in the working tree and is now running (§8).

### 7.1 The voice surface resolves out of the registry (R1, R2, R3)

`services/voice_engine.py` gains `_VOICE_SHARED_TOOLS` — **a tuple of five strings**, no
declarations:

```python
_VOICE_SHARED_TOOLS = ("read_file", "write_file", "open_path",
                       "search_email", "screenshot")
```

`_voice_shared_tool_specs()` resolves each name against `CLAUDE_TOOLS` and
`CLAUDE_TOOL_HANDLERS`, taking the description and JSON schema **verbatim**. A name that
does not resolve — never registered, or dropped by §7.4 — is skipped with a log and is
therefore never declared and never named in the prompt.

`run_command` is deliberately excluded. Shell execution driven by a speech recogniser is
a different risk class from reading a file, and nothing in the reported failure needs it.

Result (**MEASURED**): the voice surface is now **15 tools**, and all 15 render as valid
`FunctionDeclaration`s against the live `google.genai` types —
`write_file` correctly carries `required: ['path', 'content']` and
`mode: enum ['write', 'append']`; `open_path` carries `in_browser: BOOLEAN`.

`_json_schema_to_genai` is deliberately narrow (object / string / integer / number /
boolean / array-of-those, plus `enum` and `description`). Anything richer **raises, and
the caller drops the tool** rather than shipping a truncated declaration the handler
could not honour. That is R6.

### 7.2 The prompt is generated from the resolved surface (R4)

`_voice_tool_surface_note()` now builds from `_voice_tool_names()` — the native table
plus whatever actually resolved — instead of from `_VOICE_LIVE_TOOLS` alone. It cannot
name a tool the API was not handed. The note's hard-coded example sentence, which said
*"I can't read files from voice mode"*, became false the moment `read_file` was added,
and was replaced; the note now also states that the file tools are real, that writing
then showing a file is `write_file` + `open_path(in_browser=true)`, that `open_url` will
refuse a local path, and that `screenshot` may come back denied if Computer Control is
off.

### 7.3 Voice tool calls emit process orbs (R7)

`_voice_orb_start` / `_voice_orb_finish` in `routes/voice.py` register an orb per call,
classify the outcome with the **same** sentinels the text path uses (`_tool_call_status`,
so a *deny* reads as a deny, not a success), and tier-redact args and results through
`_tier_safe_summary` because `/api/processes` is readable without the vault. The orb
lingers six seconds so a sub-second call is still seen.

Stephen's instrument now measures what he believed it measured: **an announced action
with no orb is narration, provably.**

### 7.4 Missing dependency ⇒ absent from the registry (R5)

New `services/capability_preflight.py` holds the declared inventory: what Friday
*claims*, what breaks when the dependency is absent, which tools to withhold, and whether
the absence is a packaging bug (`optional=False`) or a documented trade-off
(`optional=True` — Presidio's 590 MB model, Layer 3's 4.4 GB of torch).

`services/agent.py` consults it at import and **removes** withheld tools from
`CLAUDE_TOOLS`, `CLAUDE_TOOL_HANDLERS` and `TOOL_RINGS`. Because every surface now
derives from that list, one removal covers all four at once.

`pdfplumber` added to `requirements.txt` (with the history, so it is not silently dropped
again) and to `AgentFriday.spec` — pinned explicitly, because the import sits inside a
`try/except` that `collect_submodules` cannot see, which is exactly how it went missing.
Installed: `pdfplumber-0.11.10`.

The `/api/analyze-file` PDF fallback previously conflated two different failures. It now
separates them: *pdfplumber missing* says so, names it as Friday's packaging gap rather
than the user's chore, and records an egress decision of `block` because nothing was sent
anywhere; *no text layer* says the PDF is probably a scan. New endpoint
`/api/health/capabilities`, and a `capabilities` block on `/api/health`.

### 7.5 Silent tool-loss is now loud

Both `anthropic_to_openai_tools` conversion sites in `model_router.py` logged nothing on
failure and shipped a tool-free turn. They now log at ERROR, naming the seat and the
count, and additionally catch the *empty-result* case where conversion succeeds but
returns nothing. See §2.1.

### 7.6 `open_url` names the tool that finishes the job

Given a local path or a `file://` URL, `_tool_open_url` now returns a message naming
`open_path` with the resolved path and the `in_browser=true` hint, and explicitly
forbids substituting an invented `http://` URL. `_looks_like_local_path` is deliberately
conservative — `file://`, drive-letter, UNC, `~/` only — so a mistyped domain is never
silently reinterpreted as a filename (**MEASURED**: `example.com` → `None`,
`file:///C:/…/b%20r.md` → `C:\…\b r.md`).

### 7.7 Verified end to end

Run against the real handlers (**MEASURED**):

| Call | Result |
|---|---|
| `write_file` to a temp path | `Wrote 28 chars to …` — file existed on disk |
| `read_file` on it | returned the exact bytes written |
| `screenshot` with CC off | `[GOVERNANCE DENY] ring-3 OS control denied: Computer control permission not granted.` |
| `make_todo_list` (undeclared) | `TOOL CALL FAILED — there is no tool called 'make_todo_list' in voice mode, so nothing ran and there is no result.` |

The screenshot row is the design working: the tool is declared, ring-3 governance denies
it honestly, and the orb records a *deny*. The last row is the honest dead-end that
replaces a bare "unknown tool".

---

## 8. What the 08:55:43 restart brought live, and what it invalidates

The app restarted at **08:55:43** today (**MEASURED**), 6 minutes after the last edit in
§7 was written at 08:49:32. So the running server contains **both** the backlog and
today's work: yesterday's `spawn_task` and `_voice_tool_surface_note`, `_surface_override`,
the settings-loader and seat-binding fixes, the five privacy commits, and §7 entire.

Verified live against the running process (**MEASURED**):

* `GET /api/health/capabilities` → `"missing_required": []`, `"tools_withheld": []`,
  `pdf_text` present. The §7.4 code is executing in the live server.
* `friday.local_call — local dispatch: gemma4:12b -> llama.cpp seat http://127.0.0.1:8090/v1`
  — the seat now **respawns at boot** (PID 5912, parented to the server), with
  `--cache-type-k q8_0 --cache-type-v q8_0`.
* `http://127.0.0.1:8090/v1/models` returns `gemma4:12b`, capabilities
  `["completion","multimodal"]`.

**What this invalidates:** one conclusion, and it is a conclusion about the *present*,
not about the transcript. "The running process does not contain yesterday's voice fixes,
so restart before redesigning" was true when I checked and is false now. It remains the
correct explanation of what Stephen experienced — the transcript was produced by the
stale process, and §1.3's four log lines are from that process.

**What survives unchanged:** everything else. §1.5 in particular — the wiki search never
ran — never depended on the seat's state, because `_tool_search_wiki` never touches a
seat. The nine-tool measurement is from `git show HEAD:` and the historical log, not from
live state.

**One caveat I owe Stephen.** He restarted for his own reasons; my edits went live as a
side effect, mid-verification. They are sound — §7.7 exercised the handlers directly and
the live `/api/health/capabilities` response proves the import path is clean — but they
reached production without his sign-off, which is not how I would have sequenced it.

---

## 9. Not built, and why

1. **Delete `== AVAILABLE TOOLS ==` from `FRIDAY_SYSTEM_PROMPT`.** The end state of R4.
   Not done today: the honesty golden fixtures
   (`tests/honesty/golden/01_zero_tool_click.json` and siblings) assert against that
   block, and rewriting them belongs in a change whose subject is that block.
2. **Trim before generating, not after.** Today `tool_budget` trims and *then* appends a
   correction. The right order resolves the inventory, trims it to the seat, and only
   then generates the prompt section — at which point `_surface_override` has nothing
   left to correct and can be deleted.
3. **A drift test.** One test asserting that every name in every surface's filter
   resolves in `CLAUDE_TOOLS`, and that no surface declares a tool outside it. This is
   the guard that would have caught the original nine-versus-thirty-five gap the day it
   opened. It is the highest-value item on this list.
4. **`NON_BLOCKING` for slow voice tools**, gated on model support (§6).
5. **Unify `_voice_tool_run` dispatch** so native and borrowed voice tools alike resolve
   from the registry and execute through `_execute_tool` with one session context built
   at connect time (§2.2). This subsumes the parallel session's fix and deletes the
   hand-written table outright. Blocked on both sessions being out of the file.
6. **Extend `_VOICE_SHARED_TOOLS`** once 7.1 has been used in anger — `browse_web` and
   `get_briefing` are the obvious next two. `run_command` stays off deliberately.

---

## 9a. Claims this investigation made and had to withdraw

The theme of 2026-08-24/25 has been claims propagating past the point where anyone
re-checks them: a docstring describing four privacy layers when two ran, a prompt
naming thirty-five tools when nine were handed over, a requirements file silently
missing a library the code assumed. It would be poor form to write that up without
recording where this investigation did the same thing on a smaller scale.

**"The full unit suite is hanging around 75%."** It was not. Two runs stopped at the
same visible point, which looked like a stall and was reported as one. Both had in fact
been truncated by their own timeouts, and the phase that appeared to hang
(`test_provider_descriptors` and neighbours, 66 + 13 tests) completes in well under
180 s when run standalone. Two coincident truncations were read as a signal. The
correct move was to let one run finish and read its summary line, which is what
eventually produced the real answer.

**"The 44% failure is `test_goal_verification_repair.py`."** Derived by mapping a
progress percentage onto the collection order — arithmetic over the right data,
answering a question the data could not answer, because a `-q` progress row spans many
files and the mark inside it is not positioned. The file passed in isolation, which
should have retired the method rather than prompting a second estimate. The actual
failures were in `test_residency_arbiter.py`, `test_ollama_manager.py`,
`test_gate_harness_integrity.py` and `test_workspace_aliases.py` — ten of them, all
pre-existing and all in other sessions' territory, and all named plainly by the summary
line that was already on its way.

Both errors share a shape with the bugs above: **a plausible inference presented with
more confidence than its evidence carried.** Neither changed a line of code, because
both were caught before anything was built on them. That is the only reason they are a
footnote rather than a fifth section.

---

## 10. Open questions for Stephen

- **Q1.** `run_command` on the voice surface: deliberately excluded. Shell execution from
  a speech recogniser, with homophones, is a different risk class. Agree, or do you want
  it behind a confirmation like `navigate_workspace`?
- **Q2.** `write_file` from voice currently writes without confirmation, matching text.
  Voice has no undo and no visible diff. Should writes outside `~/.friday` and the
  desktop require a spoken yes/no first?
- **Q3.** `voice_model` is `gemini-3.1-flash-live-preview`, not the
  `gemini-2.5-flash-native-audio-latest` default — which forecloses async function
  calling (§6) and is a half-cascade rather than native-audio model. Deliberate?
- **Q4.** §9 item 1 rewrites the honesty goldens. Confirm before I touch fixtures that
  encode a behavioural contract.
- **Q5.** The capability table currently holds four entries. Which other user-facing
  claims should be in it — ElevenLabs, ComfyUI, Higgsfield, the local voice tiers?
