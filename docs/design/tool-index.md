# The tool index — groups are metadata, not a maze

**Date:** 2026-08-19
**Status:** design. Spec only; no implementation exists and none is proposed here.
**Scope:** the *shape of the tool index* underneath
[`context-assembly.md`](context-assembly.md) §3.1. That document already decided
**that** tool schemas defer. This one decides **how the deferred tail is indexed**, and
answers a specific proposal: make the index two-layer, groups on top, a normal index
underneath, loaded by the instruction *"if the task is media, open the media index, then
search inside it."*

**Relationship to existing work — read this before anything else.** This document does
**not** re-decide deferred tool loading, the CORE/DEFERRED split, `open_toolbox`,
the seat arithmetic, or the drop order. All of that is
[`context-assembly.md`](context-assembly.md) §3.1–§3.7 and rules CA1–CA16, and it
stands unamended. Nor does it re-measure the turn; that is
[`prompt-token-audit.md`](../audits/prompt-token-audit.md) (cited **AUDIT**). What is
new here is a measured inventory of the registry *by group*, the arithmetic of flat
versus grouped indexing across a **growing** registry, and one structural correction to
how the grouping is allowed to work. Where this document and `context-assembly.md`
touch, `context-assembly.md` wins and this one supplies detail.

**Branch note:** doc-only. Two other sessions hold ~274 uncommitted files in this tree;
this commit touches only this file. No code was read into and no code was written.

**Evidence registers:** **MEASURED** / **VERIFIED** / **INFERRED** / **UNKNOWN**.

---

## 0. The recommendation, up front

**Do not build the two-layer index as a navigation protocol.** Build the grouping as
**index metadata plus a group-aware matcher**, keep retrieval flat-search-first, and
promote a rendered group layer only when a measured trigger fires.

Three sentences of why:

1. The flat index already fixes tonight's defect, with room to spare (§3).
2. The two-layer index's real prize is **not** today's ~2,600 tokens; it is that the
   flat index's cost scales linearly with a registry that is visibly growing, and the
   grouped one does not (§3.3). That prize is worth designing *for* and wrong to
   *pay for* now.
3. Making the group a **required hop** would put a second model-made decision on the
   most fragile path in the system, and the practitioner caveat says that hop gets
   *less* reliable as the seat gets *better* (§5.4). So the group must never be a hop.
   Groups rank and filter; they never gate, and nothing is only reachable through one.

The design that follows is the version of Stephen's idea that survives its own
strongest objection: **you get the grouping, and the model never has to know it
exists.**

**Two decisions govern everything below** (Stephen, 2026-08-19):

- **§7.0 — never silently drop tools; disclose and offer, but do not block.** Proceed
  with the best subset, disclose visibly which groups were withheld, offer the upgrade in
  one step, and never turn any of it into a prompt that halts the turn. Lands as GT9/GT10
  (§6.1); it is why §6.3's ladder answers at every rung but the last. It also,
  unexpectedly, strengthens the case for the taxonomy: *"which groups are unavailable"*
  is a sentence only a grouped index can say.
- **§7.0b — the disclosure is an annotation on one answer, not a status indicator.** It
  renders in the transcript beside the reply it qualifies, with the upgrade control
  inline, and it scrolls away with it. A disclosure separated from its referent asserts a
  standing fact about the system when what happened was a property of one turn.

---

## 1. Measurement first — what is actually in the registry

### 1.1 Method, and its honesty

Friday's registry was extracted **statically** from source with Python's `ast` — parsed,
never imported, never executed, no server touched (per the commission's constraint and
the live-server prohibition in `SEATS_AND_TRANSPARENCY_SPEC.md`). Every `CLAUDE_TOOLS`
list literal, `.append(...)` and `.extend([...])` in `services/agent.py`, plus the
`TOOLS` lists in `services/media_tools.py` and `services/elevenlabs_tools.py`, which
register through `register(CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS, TOOL_RINGS)`
(`agent.py:4241`, `agent.py:4250`).

**Token estimator.** No BPE vocabulary was reachable offline, so tokens are reported
with `len(json.dumps(tool)) // 4` — deliberately, because that is the estimator
`services/tool_budget.py:40-45` actually ships and budgets against, so a number computed
this way is the number the running system will act on. It is also **calibrated**: AUDIT
measured 58 tools at 9,603 tokens offline = **165.6 tok/tool**; this extraction gives 64
tools at 10,755 = **168.0 tok/tool**. Two independent methods, **1.4% apart**. That
agreement is why the figures below are registered MEASURED rather than INFERRED. A
second, structural estimator (cl100k pre-token regex with sub-splitting) ran ~30% higher
at 14,006; it is *not* used, and the disagreement is recorded here so nobody rediscovers
it as a contradiction.

### 1.2 The count — settling the 157/64 question

**MEASURED. The built-in registry is 64 tools.** Not 65, and not 157.

| source | tools |
|---|---:|
| `agent.py` `CLAUDE_TOOLS` literals | 60 |
| `media_tools.TOOLS` | 3 |
| `elevenlabs_tools.TOOLS` | 2 |
| raw extraction total | 65 |
| less the MCP registration **template** at `agent.py:5433` — `{"name": full, "description": desc, ...}` is the loop body that registers connector tools, not a tool | −1 |
| **built-in total** | **64** |

So the "64 in one registration path" figure is the whole built-in registry, correctly
counted. **The "~157" figure is not a built-in count and cannot be one** — it can only
be a live total including MCP connectors, which register at runtime from a background
thread as each server completes its handshake (`agent.py:5410-5443`), get the name
`mcp_{server}_{tool}` and ring 2 unconditionally (`agent.py:5436`), and therefore have
**no static existence to count**. The live total is a property of which servers are up,
not of the source tree.

The connector numbers that *are* on record, from `tool_budget.py:12-16` (MEASURED on
Stephen's machine, 2026-08-18): **Higgsfield 86 tools, GitHub 26**, together
"roughly 36k" tokens. And AUDIT measured tool schemas at **14,041 live** against 9,603
on a bare import with GitHub alone connected — a 4,438-token delta over 26 tools =
**171 tok/connector-tool**, within 2% of the built-in mean. Connector schemas are not
cheaper or dearer per tool; there are simply many more of them.

**The registry as observed, therefore:**

| configuration | tools | schema tokens |
|---|---:|---:|
| built-ins only | 64 | ~10,800 |
| + GitHub (the AUDIT live turns) | 90 | **14,041** (MEASURED, AUDIT §1.1) |
| + Higgsfield + GitHub (the 2026-08-18 failure) | **176** | **~46,800** (MEASURED, `tool_budget.py`) |
| + the full ElevenLabs audio surface (§4.4) | ~190 | ~49,000 |

### 1.3 The motivating defect, in arithmetic

`tool_budget.py:5` records the observed error verbatim:

> `request (46288 tokens) exceeds the available context size (32768 tokens)`

And `context-assembly.md` §1.2 (commit `ed10711`, MEASURED) fixes the seat that produced
it: on the RTX 4070's 12,282 MiB, `gemma4:12b` at 32,768 leaves 1,936 MiB free and at
65,536 leaves **551 MiB** — under the 1,024 MiB display reserve that has cost Stephen a
monitor twice. **The seat cannot grow. The payload must shrink.**

Against a 32,768 seat with a 4,096-token output reserve — **28,672 usable** — and
AUDIT's measured non-tool fixed cost of ~5,100 (system ~4,200 + recall ~900):

| registry state | tool tokens | fixed total | conversation room |
|---|---:|---:|---:|
| full schemas, Higgsfield + GitHub | 46,800 | 51,900 | **−23,228** — cannot be built |
| full schemas, GitHub only | 14,041 | 19,141 | 9,531 |

The first row is the hard fail. It is not a near miss and no fallback exists below it:
the request is rejected by llama-server with a 400, and the dispatch ladder answers from
Anthropic (`context-assembly.md` §1.3) — so a turn the sensitivity classifier
*forced local for privacy reasons* becomes a cloud turn. **That is the defect: the
privacy guarantee and the context budget are in direct conflict, and the context budget
wins silently.**

`fit_tools_to_seat` (landed 2026-08-19) blunts it by dropping connector tools
all-or-nothing, which converts a hard fail into a *survivable* turn — real progress, and
the reason this is now a design question rather than an outage.

**But the disclosure it promises does not reach the user, and this is verifiable.**
`tool_budget.py:72-77` says the note is *"suitable for the model's system prompt AND for
telling Stephen, because a capability that quietly is not there is the failure this
module exists to prevent."* In practice the note has exactly two destinations: a
`print()` to the console (`tool_budget.py:105`), and the **model's system prompt** at
three call sites — `chat.py:739`, `agent.py:218`, `model_router.py:789`, each appending
`"\n[SEAT] " + note`. **A grep for `[SEAT]` across `static/` and `ui_parts/` returns
nothing** (VERIFIED, this session). So 112 tools vanish, Friday tells *herself* about it,
and Stephen is told nothing where he is working.

That is the gatekeeping pattern in its quietest form — not a refusal, just a smaller
Friday arriving without comment. §6.1 is where this document stops accepting it, under a
decision Stephen has now made explicitly (§7.0).

### 1.4 Cost concentration — MEASURED

Schema cost is top-heavy, which matters for what a CORE set can afford:

| | share of built-in schema tokens |
|---|---:|
| top 5 tools | 19.7% |
| top 10 | 34.2% |
| top 20 | 54.6% |
| top 40 | 82.0% |

Mean 168 tok/tool, median 177, p90 440, max 594 (`content_create_post`), min 73
(`type_text`). The most expensive tools are the generative and pipeline ones —
`content_create_post` 594, `generate_image` 582, `generate_music` 577,
`compose_timeline` 531, `creative_project` 472, `speak_text` 440. **The expensive tools
are disproportionately the ones a given turn does not need**, which is the whole reason
deferral pays.

---

## 2. The proposed taxonomy, justified from what the tools do

### 2.1 The axis, and why not the obvious ones

Three candidate axes were considered against the real inventory:

- **By vendor** (higgsfield / elevenlabs / github / built-in). **Rejected.** It is the
  axis the *registry* is organised by and the one the *user* never thinks in. It also
  fails structurally: Higgsfield's 86 tools span image, video, audio and publishing from
  one server, so a vendor group is not a capability group, and `tool_budget.py:18-22`
  already argues the related point — a caller that can see half a connector will try the
  half that is missing. (That rule survives §7.0 and is *completed* by it: connectors
  still load all-or-nothing, and GT9 now requires saying **which** ones went, so the
  caller is not left inferring. All-or-nothing was the right half of a two-part answer.)
- **By verb alone** (generate / transform / analyze / read / write). **Rejected as the
  top level.** "Generate" would hold image, video, audio, music, decks, websites and
  social posts — 20+ heterogeneous tools, the single largest group, and the one a
  keyword query discriminates within worst. The verb is a good *second* axis and is used
  as one (§2.3).
- **By capability domain — what the user is trying to do.** **Adopted.** It is the axis
  a query arrives on ("make a voiceover", "what's on my calendar", "read that file"),
  which is the only axis that helps a matcher.

### 2.2 The groups — MEASURED costing of all 64 built-ins

Every built-in tool is placed; there are no leftovers and no tool appears twice.

| group | n | schema tok | mean | what it is |
|---|---:|---:|---:|---|
| **publishing** | 7 | 1,970 | 281 | finished artifacts and their distribution: `compose_timeline`, `create_presentation`, `create_website`, `content_create_post`, `content_schedule_post`, `content_post_status`, `content_repurpose` |
| **comms** | 11 | 1,547 | 141 | other people's systems: calendar ×5, `search_email`, `draft_email`, `search_drive`, `read_doc`, `list_tasks`, `search_contacts` |
| **self** | 9 | 1,497 | 166 | Friday operating on Friday: `switch_model`, `navigate`, `revert_workspace`, `list_workspace_history`, `learn_skill`, `epistemic_score`, `personality_show`, `personality_check_sycophancy`, `spawn_task` |
| **vision** | 4 | 1,096 | 274 | image and video: `generate_image`, `generate_video`, `compare_image_takes`, `inspect_image` |
| **audio** | 4 | 1,067 | 267 | `generate_music`, `speak_text`, `list_voices`, `inspect_audio` — see §4.4, this group is about to quadruple |
| **memory** | 10 | 1,032 | 103 | wiki, knowledge graph, trust graph, briefings |
| **filesystem** | 7 | 878 | 125 | `read_file`, `write_file`, `write_clipboard`, `run_command`, `open_path`, `install_package`, `save_output` |
| **research** | 4 | 597 | 149 | `search_web`, `browse_web`, `search_news`, `open_url` |
| **creative** | 2 | 591 | 296 | `creative_project`, `start_creative_pipeline` — the Series-Bible and pipeline orchestration layer |
| **desktop** | 6 | 480 | 80 | mouse, keyboard, screen |
| **total** | **64** | **10,755** | 168 | |

### 2.3 The second axis, and the ambiguity this does not pretend to solve

Groups are **not** a partition of a clean space, and the design must not depend on their
being one. Genuinely two-homed today: `screenshot` (desktop | vision), `generate_music`
(audio | creative), `compare_image_takes` (vision | creative), `create_website`
(publishing | creative), `knowledge_query` (memory | research), `search_drive`
(comms | filesystem), `open_url` (research | desktop), `run_command`
(filesystem | desktop | self).

Two rules absorb this, and they are the reason the ambiguity is survivable:

- **GT1 — membership is many-to-many.** A tool carries a `groups: [...]` list, not a
  group. `screenshot` is in both `desktop` and `vision`. There is no tie to break, so
  there is no wrong answer to get wrong, and no migration when opinion changes.
- **GT2 — a group is a retrieval hint, never a partition and never a permission.**
  Group membership has no effect on what is callable. That is `TOOL_RINGS`' job
  (`agent.py:3431`: ring 0 read / 1 write / 2 network / 3 computer-control), which is an
  **orthogonal** axis and is not touched, folded in, or reordered by anything here.
  Confusing a retrieval group with a permission ring would be the worst outcome
  available in this design space; they are named differently and stay that way.

The **second axis is the verb** — `generate` / `transform` / `analyze` / `read` /
`write` / `control` — carried as a facet on each tool, not as a level of nesting. It
exists so that a group too large to fetch in one call can be fetched by facet (§4.4),
and so `open_toolbox("transcribe")` can rank an analyze-tool above a generate-tool
without anyone having built a taxonomy branch for it.

---

## 3. The arithmetic — flat index versus grouped, across a growing registry

This is the section the recommendation rests on.

Index entry costs, from the measured serialization: a flat entry (`name — one-line
purpose`) averages **~17.5 tokens**; a group header (name, one-line, member count)
**~25 tokens**. CORE is taken at 16 tools per `context-assembly.md` §3.1's
ledger-derived estimate.

### 3.1 What each index costs

| registry size | flat index | grouped top level | difference |
|---|---:|---:|---:|
| 64 (built-ins only) | 840 | 250 | 590 |
| 90 (today, GitHub up) | 1,295 | 250 | 1,045 |
| **176 (Higgsfield + GitHub — the observed failure)** | **2,800** | **250** | **2,550** |
| ~190 (+ full ElevenLabs, §4.4) | 3,045 | 250 | 2,795 |
| 380 (2×) | 6,370 | 350 | 6,020 |
| 760 (4×) | 13,020 | 350 | 12,670 |

**A correction worth stating plainly:** `context-assembly.md` §3.1 estimates the flat
index at **~1,000–1,400 tokens**. That was computed against the ~58–64-tool era and is
**low by roughly 2× against the registry as actually observed** — at 176 tools the flat
index is ~2,800. This is not an error in that document so much as the same growth
problem showing up inside the fix for the growth problem. It is the single most
important number this document contributes, and it should be carried back into
`context-assembly.md` §3.3's table when that doc is next revised.

### 3.2 What each does to the seat

28,672 budget; 5,100 non-tool fixed; CORE ≈ 16 × 169 = 2,704.

| shape | tool tokens | fixed total | conversation room |
|---|---:|---:|---:|
| full schemas, Higgsfield + GitHub (today) | 46,800 | 51,900 | **−23,228** |
| full schemas, GitHub only | 14,041 | 19,141 | 9,531 |
| **flat index @ 176 tools** | **5,504** | **10,604** | **18,068** |
| **grouped @ 176 tools** | **2,954** | **8,054** | **20,618** |
| flat index @ 760 tools | 15,724 | 20,824 | **7,848** |
| grouped @ 760 tools | 3,054 | 8,154 | **20,518** |

**Read the two rows that decide this.** At today's registry the flat index leaves
**18,068 tokens of conversation room** — comfortably above `context-assembly.md` §6's
live-acceptance bar of ≥15,000, and nearly double today's 9,531. **The flat index solves
the motivating defect.** The grouped index adds 2,550 tokens on top, a **14% improvement
in conversation room** — real, and nowhere near sufficient justification for a new
mechanism on this subsystem.

At 4× growth the picture inverts completely: the flat index alone costs 13,020 tokens
and conversation room collapses to 7,848 — *worse than doing nothing today*. The grouped
index is flat at ~20,500 regardless. **The two-layer index is not a fix for the current
defect. It is the fix for the defect the current fix will have.**

### 3.3 The trigger, derived rather than chosen

The threshold should not be a taste judgement. The natural one: **the flat index may
occupy up to 10% of the seat's tool budget.** Above that it stops being an index and
starts being the problem it was built to solve.

| flat index share of the 28,672 budget | registry size |
|---|---:|
| 5% (1,434 tok) | 98 tools |
| **10% (2,867 tok)** | **180 tools** |
| 15% (4,301 tok) | 262 tools |
| 20% (5,734 tok) | 344 tools |

**The registry is at 176 observed and ~190 with the ElevenLabs surface.** The trigger
fires essentially now — which is why the grouping is specified here and built as data
now, and why the *rendered* group layer is nonetheless left switched off until the
counter says so rather than until someone feels it is time.

---

## 4. The design

### 4.1 What is built

Three artifacts, in dependency order. None of them is a required step for the model.

1. **Group metadata on every tool** — `groups: [...]`, `verb: ...`, and the same
   one-line purpose the flat index already needs. For built-ins this is a static table.
   For MCP tools it is computed at registration by a deterministic rule (§4.3). It costs
   **zero prompt tokens** until something renders it.
2. **A group-aware matcher inside `open_toolbox(query)`** — the existing lookup, still
   string-matching, still no model call in the hot path (CA5). Order: exact name →
   group-name match → verb match → keyword over one-liners. `open_toolbox("audio")` now
   returns the audio group; `open_toolbox("make a voiceover")` returns `speak_text`
   first. **The same call site, better ranking, no new protocol.**
3. **A rendered group layer, behind a counter-driven flag (default off)** — when the
   flat index exceeds 10% of the tool budget (§3.3), the index renders as group headers
   with member counts *plus the names of the top-N tools by ledger frequency within each
   group*, instead of every name. §4.2 is the rule that keeps this safe.

### 4.2 GT3 — the rule that makes the whole thing safe

> **Groups rank and compress. They never route. `open_toolbox(query)` always searches
> the entire registry regardless of group, and no tool is reachable only via its
> group.**

Everything follows from this one sentence, and it is the direct answer to the
practitioner caveat (§5.4). There is no instruction of the form *"open the media index,
then search inside it"* anywhere in the prompt, because there is no state in which that
instruction is necessary. A model that thinks in groups gets a fast path
(`open_toolbox("audio")` → the group). A model that ignores groups entirely gets the
identical answer in one call (`open_toolbox("voiceover")` → `speak_text`). A model that
guesses a tool name directly gets `context-assembly.md` §3.1's auto-inject-and-retry.

**Correctness therefore never depends on the model following a rote instruction,
because there is no rote instruction to follow.** The 30%-vs-100% reliability signal
becomes a question about which *fast path* a model takes, not about whether it succeeds.
That is the difference between a design that degrades and one that fails.

This also keeps CA8 intact. CA8 says *a lookup that must be looked up is a lockout*.
A two-layer index in its literal form makes tool **names** something you must look up
before the lookup is usable — CA8's prohibition one level up. GT3 is what stops that:
the names remain reachable in one call, always.

### 4.3 Adding a vendor without rewriting the taxonomy

This is Stephen's second question, and it has a mechanical answer.

**A new connector's tools get group labels from a deterministic rule at registration
time, and are fully usable if the rule returns nothing.** In precedence order:

1. **A server-level default** in `mcp_servers.json` — one optional field,
   `"groups": ["audio"]`, set once per server. ElevenLabs → `audio`. GitHub →
   `filesystem, self`. A one-line edit, not a taxonomy change.
2. **Keyword match** of the tool's own name and description against the group and verb
   vocabularies — the same matcher `open_toolbox` already runs, applied once at
   registration instead of per query. Higgsfield's 86 tools shatter across
   vision/audio/publishing on their own descriptions, which is the correct outcome and
   requires nobody to have pre-classified them.
3. **Nothing.** An unlabelled tool sits in a `ungrouped` bucket, appears in the flat
   portion of the index, and is **fully reachable by keyword search like every other
   tool**. An unclassified tool is a slightly less well-ranked tool. It is never a
   missing tool.

Rule 3 is the load-bearing one: **the taxonomy is allowed to be wrong or absent without
any capability being lost.** That is what makes a growing registry safe, and it is why
the maintenance burden of the taxonomy is bounded — a stale group label costs ranking
quality, never reach.

### 4.4 Audio — the group Stephen is right about

Audio today is 4 tools / 1,067 tokens. The ElevenLabs surface named in the addendum —
text-to-speech, speech-to-speech, voice cloning, voice design, dubbing, translation,
sound-effect generation, transcription, audio isolation, voice library search — is
**10–14 more**, and `docs/design/elevenlabs-voice.md` already treats "audio Friday
produces" as a distinct thing from "audio Friday speaks." Add Higgsfield's audio and
music routes and the local whisper path behind `inspect_audio`, and audio lands at
**~18–22 tools**, making it the **largest or second-largest group** in the registry,
comparable to publishing and ahead of comms.

So: **audio is a top-level group, not a facet of media, and the vendor is not the
grouping.** `speak_text` (ElevenLabs), `generate_music` (Higgsfield-bound) and
`inspect_audio` (local whisper) belong together because a user asking about audio does
not know or care which of three backends answers.

**Within audio, the cut is by capability, exactly as Stephen proposes** — the §2.3 verb
facet doing its job:

- **generate** — TTS, SFX generation, music generation
- **transform** — speech-to-speech, dubbing, translation, isolation, voice cloning and design
- **analyze** — transcription, `inspect_audio`
- **read** — `list_voices`, voice library search

**GT4 — the group review trigger, derived not chosen.** `context-assembly.md` §3.1 caps
`open_toolbox` at **≤10 schemas per call**. Therefore: **when a group's membership
exceeds the fetch cap, the group must expose its verb facets as separately fetchable,
because a group that cannot be fetched in one call is a group that cannot be delivered.**
Audio at ~20 crosses this immediately and gains facets on arrival; publishing at 7 and
comms at 11 are watched (comms is already over and should be facet-split by the same
rule). The trigger needs no judgement call and no meeting — it is a counter against a
constant that already exists for another reason.

---

## 5. STORM — five perspectives, and the disagreements left standing

Each position is stated as its holder would state it, then adjudicated. Where a position
survived, it changed the design; where it did not, the reason is recorded.

### 5.1 The context engineer: "ship it, the arithmetic is obvious"

*Tool schemas are the largest fixed cost in the turn — 14,041 of a 19,752-token turn at
GitHub-only, and 46,800 with Higgsfield up. Nothing in the system selects a subset
(AUDIT §3). The grouped index is flat at ~250 tokens no matter how big the registry
gets. The flat index is linear. Linear loses to constant. Build the constant one.*

**Adjudication: right about the asymptote, wrong about the urgency.** §3.2 shows the
flat index already clears the acceptance bar at today's size with 18,068 tokens of
conversation room. Building for 760 tools while sitting at 176 is the kind of
anticipation that ships a mechanism nobody has a failing test for. The asymptotic
argument survives as §3.3's **trigger**, not as today's build order.

### 5.2 The reliability engineer: "this is the fragile path, do not put a decision on it"

*The tool path has broken three times in three days and every time it presented as model
incapacity.* `docs/audits/symphony-live-2026-08-15.md:42-62` — `json.loads()` on an
already-parsed dict silently emptied every local tool call's arguments; **0/5 became
15/15** once fixed. `residency-live-2026-08-15.md:47` — the `/api/chat` fallback dropped
`tools` entirely, guaranteeing a false tool-calling failure. `residency-live-2026-08-15.md:54`
— the seat was sized below the tool registry. `context-assembly.md:241` records the
standing rule: *"a model 'failing at tools' after any registry change is our plumbing
until proven otherwise. Three-for-three so far."*

*And the base rates are bad.* `model-suite-determination.md:65-93`: stock `gemma4:12b`,
`qwen3.5:9b` and `gemma4:e2b` each **1/3** on tool use, and the failures are *refusals to
use a tool the model was handed a schema for*, not malformed JSON.
`residency-live-2026-08-15.md:64-77`: the same e2b gate scored **10/10, 8/10, 8/10,
9/10** on one machine on one day — *"the gate is not reproducible, and that is a
finding."* If a single tool decision on e2b is p≈0.8–0.9, **two sequential mandatory
decisions are 0.64–0.81.** A required group hop converts one draw into two on the worst
seat.

**Adjudication: decisive, and it is why GT3 exists.** This objection is fatal to the
two-layer index *as a required hop* and has no force at all against groups as ranking
metadata, because GT3 adds **zero** mandatory decisions — the number of calls needed to
reach any tool is unchanged. This perspective did not lose the argument; it rewrote the
design.

### 5.3 The caching engineer: "you are optimising the cheapest region of the prompt"

*`context-assembly.md` CA14 puts the index in region 3 of the stable prefix —
byte-identical across turns* and *across conversations. llama-server's prefix reuse is
already on (§1.4), so on a warm prefix the index costs zero prefill; on Anthropic it
bills at ~0.1× under the §3.7 breakpoint. You are spending a new mechanism to shrink the
single most cacheable thing in the prompt. Worse: a grouped index makes the rare-tool
turn a two-stage tool-region change — sub-index in, then schemas in — so it costs* **two
cold prefills instead of one** *(§3.7 invalidator 4).*

**Adjudication: correct on latency, and it does not touch the argument that matters.**
`context-assembly.md` §3.1 already settled this shape: *deferral's latency argument is
conditional; its headroom argument is not* — **schemas occupy the context window and the
KV cache whether or not they were cached, and headroom is what makes 32,768 livable**
(CA16). The index competes for the same 28,672 tokens cached or cold. The two-cold-
prefill objection is real and is neutralised by GT3, which does not add a stage: a
grouped fetch is still one `open_toolbox` call returning schemas.

### 5.4 The practitioner's caveat, taken seriously and not as fact

The reported signal: **stronger reasoning models followed the mechanical
"open the group index, then search inside it" instruction *less* reliably than weaker
ones — 30% vs 100%, with no denominator given.**

**This is treated as an unverified signal, not a finding.** No denominator, no seat, no
task distribution, no report of what "followed" was scored as, single source, not
reproduced here. It could be two runs of five. It is not evidence of a mechanism.

**But it is a coherent hypothesis with a plausible mechanism, and that is enough to
design against.** A capable model that already believes it knows the tool will route
around a rote procedural instruction — which is *correct* behaviour under a flat index
(the name was in front of it) and a *miss* under a mandatory two-layer one. If true, the
consequence is the one that should alarm: **reliability anti-correlated with model
quality inverts every upgrade.** Friday's ladder runs `e2b` → `e4b` → `12b` → `26b` →
`claude-sonnet-5`, and a shadow week (`context-assembly.md` §3.1) runs on **local** seats
— exactly the seats where compliance would be *highest*. A shadow test would green-light
a mechanism that fails worst on the cloud seat it never measured. That is a
sharper objection than the caveat itself.

**Adjudication: designed against, at the structural level rather than the mitigation
level.** GT3 removes the instruction. There is no rote step whose compliance rate
matters, so a 30% compliance rate and a 100% compliance rate produce the same
*correctness* and differ only in how many tokens the fetch returns. The honest test of
whether this document took the caveat seriously is that **the caveat's truth or falsity
does not change the design's correctness** — only its efficiency. That was the goal.

### 5.5 The maintainer: "the taxonomy will rot"

*MCP tools register from a background thread at handshake (`agent.py:5410-5443`). Under
CA11 every one of them is deferred by default, present and future. Under a flat index
that is automatic —* `f"mcp_{server}_{tool}"` *and done. Under grouping, every newly
discovered tool needs a group assignment computed by something that has never seen it:
a hand-maintained map that goes stale silently, a heuristic that is wrong, or an LLM call
in a path that CA5 forbids. And twelve tools in the current registry have two plausible
homes.*

**Adjudication: correct about the failure mode, and §4.3 rule 3 is the answer.** An
unlabelled or mislabelled tool loses ranking quality and loses nothing else, because
keyword search over the full registry always runs (GT3). Many-to-many membership (GT1)
means the twelve ambiguous tools need no tie broken. **The taxonomy is permitted to
rot**, and the cost of rot is bounded to worse ordering in a result list. A design where
taxonomy rot costs reachability would deserve this objection; this one does not.

### 5.6 The case against doing this at all — the strongest form

Assembled deliberately as the position to beat, and it is not weak:

> The flat index already fixes the defect (§3.2, 18,068 tokens of conversation room
> against a 15,000 bar). `context-assembly.md` step 1 says the index **has never been
> measured** on the live seat. The prize is a fraction of the *most cacheable* region of
> the prompt. The mechanism adds a model decision to the subsystem this repo calls "the
> most fragile thing in the system all day," on models that refuse tools they hold
> schemas for one time in three. Twelve tools have ambiguous homes and a new connector
> needs classification machinery that does not exist. Cheaper alternatives are
> unexplored: twelve-token one-liners instead of seventeen-token ones would recover
> ~400–500 tokens for a `[:N]` and no new failure mode; ranking the flat index by ledger
> frequency and truncating with *"…and 143 more — call open_toolbox('keyword')"* is a
> **deterministic assembler decision** rather than a model decision, and captures most of
> the same tokens. **Ship the flat index, measure it, tighten the one-liners, and do
> nothing else.**

**This position wins on build order and loses on build target, and both halves are kept.**

It wins on order: nothing in §4.1 should be *rendered* before the flat index is measured
on the live 12b seat, and §6.2 sequences accordingly. Its cheaper alternatives are not
alternatives at all — they are **adopted outright** as §6.2 steps 1–2, and the
twelve-token cap and frequency-ranked truncation are strictly better than what
`context-assembly.md` currently specifies.

It loses on target for one reason: **it prices the prize using a stale number.** Its
"~1,100 tokens, 3.8% of budget" derives from `context-assembly.md`'s ~1,200-token index
estimate, which §3.1 shows is **low by roughly 2× against the 176-tool registry as
actually observed** — the real figure today is ~2,800 and rising. And its own preferred
fix, frequency-ranked truncation, has a failure mode grouping does not: **a truncated
flat index silently omits names**, so the tail becomes reachable only by guessing a
keyword, whereas a grouped index omits nothing at the top level — it lists every group,
and every group is enumerable. Truncation is the mechanism that quietly makes a
capability harder to reach. Grouping, under GT3, is not.

**Net:** the case against correctly identifies that this should not be built *first*,
and correctly identifies that the two-layer index *as proposed* should not be built at
all. It does not survive as an argument for never building the grouping, because the
registry it is arguing about is 176 tools and growing by a vendor suite at a time.

### 5.7 What remains genuinely unresolved

Stated rather than papered over:

- **The flat index has never been measured live.** Every index figure here is computed
  from serialized schemas, not observed in a running prompt. `context-assembly.md`
  step 1 is still the gate. **UNKNOWN.**
- **Whether group-name queries actually rank better than keyword queries** is an
  assumption. It is cheap to test offline and has not been tested. **UNKNOWN.**
- **The 30/100 caveat is unverified** and this document deliberately does not resolve it
  (§5.4). It should not be cited downstream as though it were measured.
- **The ledger-derived CORE set** (16 tools, ≥95% coverage) is INFERRED in
  `context-assembly.md` and inherited here unexamined. If CORE is really 30 tools, §3.2's
  arithmetic moves ~2,400 tokens and the trigger in §3.3 fires sooner.

---

## 6. Never gate, degrade gracefully, and how we would know

### 6.1 The escape hatch — explicit, and cheap

Stephen's standing rule, from `roles-and-model-identity.md` §5: *"This is advice, not a
gate... Do not refuse, and do not silently substitute."* Applied to tools:

| rule | statement |
|---|---|
| **GT5** | Loading a subset is an **optimization, never a restriction**. Every tool in the registry is reachable on demand from every seat, on every turn, in **one call**. |
| **GT6** | `open_toolbox(query)` searches the **entire** registry — every group, every connector, ungrouped tools included. Group membership never filters what a query can find, only how results are ordered. |
| **GT7** | `open_toolbox("*")` — or any query that matches nothing — returns the **full name list**, not an error. The list is the cheapest object in the system; it is never withheld. |
| **GT8** | A tool named directly, in any group, loaded or not, is **auto-injected and retried** (CA8). The model never needs to know a group exists to reach anything inside it. |
| **GT9** | **Never silently drop tools. Disclose and offer, but do not block.** When the full surface will not fit, Friday **proceeds** with the best available subset, **discloses visibly** what was withheld and why, and **offers the upgrade path in one step**. Three parts; the third is not optional. |
| **GT10** | Disclosure is **ambient, never modal**. The turn is never halted to ask a question about tool loading. The choice is *available*, not *demanded*. |

**GT9 — the three parts, and why each is load-bearing** (Stephen's decision, §7.0):

1. **Proceed.** The user asked for something. Answer it with the best subset available.
   Do not halt the turn to ask a question first. A withheld tool is a degraded answer;
   a blocked turn is no answer, and no answer is worse.
2. **Disclose visibly.** The user must be able to see *that* tools were withheld,
   *which ones* — or at minimum which **groups**, which is the first concrete thing the
   §2.2 taxonomy buys that a flat index cannot express — and *why*. Surfaced where he is
   working, not in a log and not in the model's system prompt. This is the specific
   correction to §1.3's finding: today's note goes to the console and to Friday, and
   the one destination it never reaches is the screen.
3. **Offer the upgrade in one step.** The larger seat or the cloud, reachable without
   re-explaining the request. Taking it **re-runs the same request with the full
   surface** — it is a re-dispatch, not a new conversation. An offer that costs the user
   a retype is not an offer.

**GT10 — the failure mode this design is defending against.** Stephen has already
objected to Friday asking *"do you want to wait for it?"* on every message after a model
switch. **A modal on each turn would be worse than the silent drop, not better** — it
converts a quiet capability loss into a loud tax on every single turn, and it is
gatekeeping wearing a politeness costume: the turn still does not proceed until the user
answers. So the amber line renders and the turn continues in the same breath. Nothing
waits on acknowledgement.

**Ambient does not mean persistent** (§7.0b). An earlier draft of this rule reasoned
that the *notification* should fire once per seat decision — following
`tool_budget.py`'s existing once-per-`(model, decision)` announce guard
(`tool_budget.py:36-37, 102-104`) — while the *state* stayed continuously visible
somewhere in chrome. **That is wrong and is retracted.** Both halves of it are wrong:
the line is **per-answer, not per-seat-decision**, and it **scrolls away with the answer
it qualifies**. The announce guard is the right instinct in the wrong place — it exists
to keep the *console* from repeating itself on every turn, and a console log has no
answer to sit next to. Suppressing the user-visible line on turn two would mean the
second degraded answer arrives looking like a complete one, which is the silent drop
returning by way of a deduplication rule. **Per-answer, every time, no dedup.**

This is also why GT9 sits apart from CA3's **refuse-with-choice**. Those are different
events and must not be conflated:

| event | response |
|---|---|
| the full tool surface will not fit | **GT9** — proceed on a subset, disclose, offer. The turn completes. |
| the *turn itself* cannot be assembled at any rung (§6.3 rung 5) | **CA3 refuse-with-choice** — there is no answer to give, so the choice is the response. |

Refusing is correct only when proceeding is impossible. Withholding tools never makes
answering impossible; it makes the answer smaller, and a smaller answer plus an honest
line beats a question every time.

### 6.2 Build order — the cheap and certain first

Nothing here is a code change today; this is the order a builder should take.

1. **Measure the flat index live** — `context-assembly.md` step 1, unchanged, still the
   gate. Nothing below proceeds without it.
2. **Twelve-token one-liners and a hard per-entry cap at serialization.** Recovers
   ~400–500 tokens, no mechanism, no new failure mode. Does **not** violate CA6: the
   index one-liner is a generated field, distinct from the schema `description` that
   carries behavioral law. *(Adopted wholesale from §5.6.)*
3. **Group metadata as data** — `groups`, `verb`, the server-level default, the
   registration-time keyword rule, the `ungrouped` bucket. **Zero prompt tokens, zero
   behavior change.** Purely an offline-testable table.
4. **Group-aware ranking inside `open_toolbox`** — same call site, better ordering, no
   protocol change. Offline-testable against a fixture of real queries.
5. **Audio facets** (§4.4) when the ElevenLabs surface lands and the group crosses the
   fetch cap under GT4.
6. **The rendered group layer, flag-off, shadow-counted** — and **only** if step 1's
   measured index crosses 10% of the tool budget (§3.3). Enable on the counter, not on
   the calendar.

### 6.3 Graceful degradation — the ladder, and what is never allowed

The failure this replaces hard-failed with no fallback. This one has five rungs and the
bottom of the ladder is a sentence to Stephen, not a 400.

**When the payload will not fit, in order. Rungs 1–4 all proceed and answer; only rung 5
cannot.**

| rung | shape | cost | disclosure |
|---|---|---:|---|
| 1 | **Full schemas** — small registry, big seat. Nothing defers. | — | none needed |
| 2 | **CORE + flat index** — the `context-assembly.md` §3.1 shape. | ~5,500 @176 | none needed: nothing is withheld, only deferred, and every name is visible to the model |
| 3 | **CORE + grouped index** — flat index over 10% of budget. | ~2,950 | none needed: same reason. Groups compress the *listing*; `open_toolbox` still reaches everything (GT6) |
| 4 | **CORE + capability sentence** — even group headers will not fit: one sentence naming that other tools exist and that `open_toolbox` reaches them. | ~40 | **GT9 fires.** Amber line in the transcript beside the answer, naming the unavailable groups, with the upgrade control inline (§7.0b) |
| 5 | **`open_toolbox` alone** — CA8's floor; the lookup is never droppable. | ~60 | **CA3 refuse-with-choice.** No answer is possible: *"this seat cannot hold this turn; the 131,072 seat or the cloud can."* |

**A rung is never skipped downward to save trouble.** Today's `fit_tools_to_seat` jumps
from rung 1 straight past 2 and 3 to a connector-less registry — which is why 112 tools
disappear when a *grouped index of all 176* would have cost ~2,950 tokens and withheld
nothing. Rungs 2 and 3 are not degradations at all; they are the design, and they are why
GT9's disclosure should be **rare in practice** rather than a line Stephen learns to
ignore. A disclosure that fires on every turn has already failed.

**Never allowed, at any rung:** a tool that exists but cannot be reached; a subset served
without saying it is a subset; a disclosure that reaches the model or the console but not
the screen (§1.3); a modal that halts the turn (GT10); silence.

**When the model asks for something outside the loaded set:**

| situation | response |
|---|---|
| names a real tool, not loaded | auto-inject that schema, retry the call — one extra iteration, logged `toolbox_miss` (CA8). Never an error. |
| names a real tool in another group | **identical** — the group is not consulted. GT3/GT8. |
| names a tool that does not exist | today's error text plus the nearest names by edit distance **across the whole registry**, so a miss teaches. |
| asks for a group that will not fit in one fetch | returns the verb facets and their counts, then the requested facet (GT4). Never a truncated list presented as complete. |
| asks for everything | GT7 returns the full name list; GT9 handles the seat if the schemas will not fit — proceed on what fits, disclose the rest, offer the upgrade. |
| asks for a tool withheld at rung 4 | the tool is named in the amber line as unavailable **on this seat**, with the one-step upgrade. Never "no such tool" — a tool that exists must never be reported as one that does not. |

### 6.4 Verification in live use — including the regression that hides

Unit tests cannot see the failure that matters here, which is **the model quietly doing
something worse because a tool it would have used was one call away and it did not make
the call.** That failure produces a plausible answer, no error, and no log line. It has
to be hunted specifically.

**Instruments that catch the loud failures:**

- **`toolbox_miss` rate**, already specified (CA4). Shadow baseline first; two
  consecutive weeks above baseline auto-disables the rendered group layer with a notice.
- **The dependent-chain battery**, extended per `context-assembly.md` §3.1 — 15/15 per
  local seat, including a leg that requires fetch-then-call. Below 15/15, revert. This
  is the instrument that caught the argument-dropping bug; it is a score, not a vibe.
- **Fixed-cost regression** (CA13) — >10% week-over-week growth in CORE + index notifies
  Stephen with the source named. This is how the next vendor's tax becomes visible the
  week it lands.

**The silent-substitution detector — the one that matters, three independent methods:**

1. **Shadow-diff on the CORE decision.** With the flag off, every turn records the tool
   set it *would* have loaded alongside the tools it *actually called*. A call to a tool
   that would have been deferred is a would-be fetch; the rate should match the ledger's
   ~5%. A rate near **zero** is the alarm, not the success — it means the model stopped
   reaching for deferred tools rather than that it never needed them, which is exactly
   the regression, wearing the costume of a good result.
2. **Answer-shape drift on a fixed probe set.** Twenty frozen prompts, each of which
   *should* provoke a specific deferred tool (one per group; several for audio),
   replayed weekly per seat. Score the **tool actually called**, not the answer quality.
   A probe that used to call `speak_text` and now answers in prose is the regression,
   caught by name. This is the only method that detects "did something worse" directly,
   because it fixes the correct behaviour in advance instead of judging the output after.
3. **The prose-substitution seam.** `services/tool_integrity.py:46` already exists
   because a model narrated fake tool calls in prose rather than making them
   (`inference-discovery.md:113`). That detector is repointed: a turn whose text asserts
   a capability (*"I've generated…"*, *"I've scheduled…"*) with **no matching successful
   tool receipt in-turn** is fabrication under `SEATS_AND_TRANSPARENCY_SPEC.md` A7 — and
   under the index it is *also* a candidate silent substitution. Cross-referencing A7
   fires against `toolbox_miss` records turns the existing honesty instrument into a
   regression detector for free.

**Live acceptance, before the rendered group layer is enabled:**

- A real turn on the 32,768 seat with **≥15,000 tokens of conversation room** and zero
  misses across a day of ordinary use (inherited from `context-assembly.md` §6).
- The 20-probe battery at **20/20 correct tool selection**, per seat, on both the flat
  and grouped index shapes. **If grouping costs a single probe, it does not ship** —
  that is the operational form of "an optimization, never a restriction."
- A turn that the sensitivity classifier forces local, with every connector up,
  completing **on the local seat** — which is the defect in §1.3 closing, stated as a
  test.

**GT9/GT10 acceptance — the disclosure tests, which are UI tests and cannot be unit
tests:**

- **Forced rung 4** (seat shrunk until the group index will not fit): the turn
  **completes with an answer**, an amber line naming the withheld **groups** appears
  **in the transcript, adjacent to that answer**, and a one-step upgrade control appears
  **inline with the line**. Failing any of the three fails the test — a completed turn
  with no line is the silent drop; a line with no control is a complaint; a line or
  control **in chrome rather than the transcript** is a §7.0b violation even though the
  user can see it.
- **Placement, not just presence.** The test asserts the line is a sibling of the message
  it qualifies. A disclosure that renders correctly but in the wrong place has lost its
  referent, which is the specific failure §7.0b exists to prevent — so "visible somewhere"
  is not a pass.
- **No dedup across turns.** Two consecutive degraded answers produce **two** lines. A
  second degraded answer arriving without a line is the silent drop returning by way of a
  deduplication rule (§6.1).
- **The upgrade re-runs the request.** Taking the offer re-dispatches **the same user
  message** on the larger seat with the full surface. If the user has to retype, GT9
  part 3 is not implemented.
- **Zero modals.** Across a day of ordinary use at rung 4, the count of turns blocked
  awaiting a tool-loading answer is **0**. Any nonzero value is a GT10 violation and
  reverts the flag.
- **The line is rare.** GT9 firing on more than ~1 turn in 20 during ordinary use means
  the rungs above it are misconfigured (§6.3), not that the disclosure is working.
- **Grep as a standing test.** `[SEAT]`-class notices must appear in a user-visible
  surface, not only in `system_prompt` concatenations. The §1.3 grep — three system-prompt
  call sites, zero UI hits — is the current failing state and should be re-run as a
  regression check, not just cited once.

---

## 7. Decisions and open questions

### 7.0 DECIDED — withheld tools are disclosed and offered, never blocked and never silent

**Stephen, 2026-08-19.** Asked whether `fit_tools_to_seat`'s current behaviour (drop 112
connector tools, note it internally) was acceptable, or whether the choice should be his:

> **Never silently drop tools. Disclose and offer, but do not block.**
>
> 1. **Proceed.** Do not halt the turn to ask a question. The user asked for something;
>    answer it with the best subset available.
> 2. **Disclose visibly.** The user must be able to see that tools were withheld, which
>    ones or at least which groups, and why. Not buried in a log — surfaced where they're
>    working.
> 3. **Offer the upgrade path in one step.** The bigger seat, or the cloud, reachable
>    without re-explaining themselves. If they take it, the same request re-runs with the
>    full surface.
>
> The failure mode to design against is turning this into a prompt. […] A modal that
> interrupts each turn would be worse than the silent drop, not better. **Disclosure is
> ambient; the choice is available, not demanded.**

**Binding, and normative for the whole document.** It lands as **GT9** and **GT10**
(§6.1), sets rung 4's behaviour in the degradation ladder (§6.3), separates GT9 from
CA3's refuse-with-choice (§6.1), and adds an acceptance test (§6.4). §1.3 records the
gap it closes: today the note reaches the console and the model's system prompt at three
call sites, and `[SEAT]` appears nowhere in `static/` or `ui_parts/` — the one place it
never reaches is the screen.

Two second-order consequences worth stating, because they were not in the question:

- **It raises the value of the taxonomy.** *"Which ones, or at least which groups"* is
  only sayable if groups exist. A flat index can disclose *"112 tools withheld"*, which
  is a number. A grouped one discloses *"audio and publishing are unavailable on this
  seat"*, which is a fact he can act on. Disclosure quality is now a second argument for
  §2.2, independent of tokens.
- **It makes rungs 2–3 the real fix and GT9 the safety net.** If the grouped index ships,
  a 176-tool registry costs ~2,950 tokens and withholds **nothing** — so GT9 should
  almost never fire. A disclosure that fires constantly becomes wallpaper, which would
  defeat its own purpose. **Rarity is a design requirement of GT9, not a happy accident**
  (§6.3).

### 7.0b DECIDED — the line goes in the conversation, and the upgrade control goes with it

**Stephen, 2026-08-19**, answering whether the disclosure belongs in the transcript or on
the orb:

> **The disclosure line goes in the conversation, not on the orb.**
>
> The disclosure qualifies one specific answer. It belongs next to the answer it
> qualifies, where the user can see what was missing from *that* reply. On the orb it
> becomes ambient state that has lost its referent — the user sees a persistent marker
> and can't tell which request it applies to, or whether it's still true.
>
> **Scrolling away is a feature, not a limitation.** If the condition matters again, it
> says so again. A persistent warning becomes wallpaper faster than a recurring one […]
> just expressed spatially instead of temporally.
>
> This also means the upgrade control lives **inline with the line, in the transcript**,
> rather than as a global control somewhere in chrome. One step from where the user is
> already looking.

**The generalization, because it is not only about this line.** A disclosure is an
**annotation on a specific answer**, not a **status indicator**. That distinction decides
placement everywhere it comes up:

| | annotation | status indicator |
|---|---|---|
| answers the question | *"what was missing from **this** reply?"* | *"what is true right now?"* |
| lifetime | the answer's | until the condition clears |
| lives in | the transcript, adjacent to what it qualifies | chrome / the orb |
| failure when misplaced | — | **loses its referent**: a persistent marker the user cannot map to a request, or tell is still current |

A tool-loading disclosure is unambiguously the first kind. **The referent is the whole
content of the message** — *these* tools were missing from *this* answer — so separating
the two destroys the information. A marker on the orb reading *"audio unavailable"* is
not a weaker version of the line; it is a different and worse claim, because it asserts a
standing fact about the system when what actually happened was a property of one turn.

**Why this is the same rule as §6.3's rarity requirement, not a second one.** Both are
guards against the disclosure becoming wallpaper — §6.3 temporally (fire rarely), §7.0b
spatially (do not persist). A persistent marker fails *faster* than a recurring line,
because a recurring line at least re-earns attention by appearing beside new content,
while a static one is unchanged by definition and therefore stops being read. The two
guards compose: rare, and gone when its answer is gone.

**Consequences recorded in the design:** GT10's "ambient" is now explicitly
*per-answer, not persistent*, and the once-per-`(model, decision)` dedup that governs the
console does **not** govern the line (§6.1) — suppressing it on turn two would let the
second degraded answer arrive looking complete. The upgrade control is **inline in the
transcript**, not global chrome, and §6.4's acceptance test checks placement, not just
presence.

**Q2 is therefore closed.** The `context-assembly.md` §3.5 amber transcript line is the
right surface, and its orb-detail counterpart carries the full assembly report as it
already does — the report is diagnostic depth on demand, which is a different object from
the disclosure and is correctly not in the transcript.

### 7.1 Still open

Each answerable in a sentence.

**Q1 — The trigger.** Is 10% of the tool budget the right point to render the group
layer, or would you rather it never render until a turn actually fails to fit?

**Q3 — Audio facets.** Does the generate / transform / analyze / read cut match how you
think about audio work, or would you rather audio split by *what it is for* — narration,
music, production?

**Q4 — The probe set.** Twenty frozen prompts, one per group and several for audio, is
the regression detector. Do you want to write them, or should they be drawn from your
actual usage in the ledger?

**Q5 — Ungrouped tools.** A new connector whose tools match no group sits in
`ungrouped` — fully reachable, just less well ranked. Is silent-but-reachable the right
default, or do you want to be told when a connector arrives unclassified?

---

## 8. Sources

- **Measurement.** Static `ast` extraction of `services/agent.py`,
  `services/media_tools.py`, `services/elevenlabs_tools.py` (this session, 2026-08-19,
  read-only, no import, no server contact). Estimator `len(json.dumps())//4` per
  `services/tool_budget.py:40-45`; calibrated against AUDIT's 58-tool/9,603-token
  offline figure to within 1.4% per tool.
- [`docs/design/context-assembly.md`](context-assembly.md) — §3.1 deferred tool loading
  and `open_toolbox`; §1.2 the display-reserve collision and the 32,768 cap; §1.3 the
  llama-server 400 and the cloud bounce; §3.3 the seat arithmetic; §3.7 caching;
  rules CA1–CA16. **The parent document. Not amended here.**
- [`docs/audits/prompt-token-audit.md`](../audits/prompt-token-audit.md) — the live turn
  table, 14,041 tool tokens, the 65,799 overhead, the transcript ceiling. *Note: not
  present on this branch; lives on `model-suite-determination` at commit `9504c8d`.
  Figures quoted here are second-hand via `context-assembly.md` and are registered
  accordingly.*
- [`docs/contracts/roles-and-model-identity.md`](../contracts/roles-and-model-identity.md)
  §5 — *"This is advice, not a gate... Do not refuse, and do not silently substitute."*
  The source of GT5–GT10. §6a's `TOOL_SEAT_NUM_CTX = 65,536` is stale; `context-assembly.md`
  §1.2's 32,768 cap supersedes it, and build step 8 there schedules the re-derivation.
- `services/tool_budget.py` — the 2026-08-18 failure verbatim, the Higgsfield-86 /
  GitHub-26 counts, the all-or-nothing connector rule and its reasoning. **Its drop
  behaviour is superseded by §7.0/GT9**: the module's own docstring already states the
  right principle (*"a capability that quietly is not there is the failure this module
  exists to prevent"*) and its note never reaches a user-visible surface (§1.3, VERIFIED).
  The reasoning that survives intact is the *all-or-nothing per connector* rule — a half-
  loaded connector invites calls to the missing half. GT9 is what makes that safe rather
  than merely quiet: all-or-nothing **plus disclosure of which groups went**.
- `docs/audits/symphony-live-2026-08-15.md:42-62`, `docs/audits/handoff-2026-08-16.md:240-242`
  — the argument-dropping bug, 0/5 → 15/15. `docs/audits/residency-live-2026-08-15.md:47,54,64-77`
  — the dropped `tools` retry, the seat sized below its registry, the non-reproducible
  gate (10/10, 8/10, 8/10, 9/10). `docs/audits/model-suite-determination.md:65-93` — the
  1-in-3 refusal rate. `docs/audits/inference-discovery.md:113` and
  `services/tool_integrity.py:46` — prose-narrated fake tool calls. **The evidence base
  for §5.2 and §6.4.**
- [`docs/design/elevenlabs-voice.md`](elevenlabs-voice.md),
  [`docs/design/higgsfield-integration.md`](higgsfield-integration.md) — the audio and
  media surfaces §4.4 sizes against.
- `docs/SEATS_AND_TRANSPARENCY_SPEC.md` A7 (completion-receipt law), B2 (no silent
  changes), A4(6) (connection state must be freshly checked, never asserted from memory)
  — the honesty invariants §6.4's detector repoints.
- The two-layer index proposal itself: a practitioner report, relayed by Stephen,
  including the 30%-vs-100% reliability caveat with no denominator. Treated throughout
  as an unverified signal designed against, never as a measurement (§5.4).
