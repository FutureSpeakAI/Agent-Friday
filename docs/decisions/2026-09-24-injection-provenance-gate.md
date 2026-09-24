# One governance checkpoint for every action, and where each detail came from

**Status:** built on `feat/injection-defense`, not merged. Sensitive subsystem
(governance/, approvals, the tool hook chain, voice tool dispatch, a
federation route).
**Written:** 2026-09-24
**Builds on:** f1fae76e (the action policy reaches every prompt, last).

---

## The rule, in code

"We need to make sure the per-action governance check is in the code."

Every tool handler runs through `agent._execute_tool`, and nothing else calls a
handler. The first hook in that chain, `governance_rings` (critical: it cannot
be switched off, and an exception in it denies the call), does, in order:

1. **Privilege rings and subagent scope** (`_governance_check`, unchanged).
2. **Provenance** (`services/taint.py`): did a recipient, link, account number,
   file path, command or memory write come from the user, or from something
   Friday read?
3. **`governance/action_gate.authorize`**:
   * verifies the cLaws: the HMAC of the canonical cLaws text under the
     governance key must equal the value pinned in
     `~/.friday/governance/claws.pin.json` (pinned on first use; `repin_claws`
     after an intended change);
   * classifies the action **internal** (reading, drafting, local reversible
     work) or **outward** (other people, money, accounts, publishing, running
     code, no undo). Friday's own tools are listed explicitly; connector tools
     are outward unless their name is a read *and* the keyword + Laya union
     agrees; an unknown tool is outward;
   * lets internal work through; holds outward work until the owner decides:
     a chat yes in an interactive conversation (the existing confirmation
     flow), an approval card otherwise, or, for scheduled and background
     work, a grant that names the tool, is scoped to that one job, expires,
     and has a use count (`POST /api/governance/grants`, owner session only);
   * anything that came from outside content goes to a card that names the
     source, whatever the class, and a chat yes does not satisfy it;
   * writes a signed receipt (HMAC) to `~/.friday/decision-bom.jsonl`.

**Fail closed.** If the cLaws check fails, the classifier throws, Laya is
configured but not answering, or the receipt cannot be signed or written, an
outward action is held. Reads keep working so Friday can say what happened.

The confirmation gate that asks the chat yes/no question is now critical too.

`tests/unit/test_every_action_is_governed.py` holds the line by discovery, not
a list: it executes every registered tool (including connector tools registered
through the real path) and requires the checkpoint to see each call before its
handler, and it parses every file under `src/` for any call to a tool handler
or a connector that does not go through `_execute_tool`. It was shown red three
ways before it landed: the previous voice code (all eleven direct calls named),
a planted unwrapped tool, and the checkpoint unhooked.

## Bypasses found, and what was done

Line numbers are at bf1d5866, before this work.

| # | Where | What it did | Now |
|---|---|---|---|
| 1 | `services/agent.py:1827` `_tool_run_command`; ring 2, not in the confirm set | Any PowerShell after a blocklist. Loopback is trusted as the owner (`core/__init__.py:3311`), so a command could approve its own cards (`/api/approvals/<id>/decide`), switch gates off (`/api/hooks/<name>`) or send approved mail | Classified per command: a read-only allowlist is internal; chaining, redirection, `Invoke-*`, network and any non-listed command are outward; anything naming the local API is refused, in the gate and again in the handler |
| 2 | `services/voice_engine.py:541-662` `_voice_tool_run` | Gemini Live called eleven handlers directly (navigate, open_url, spawn_task, searches, calendar, email, source trust, deep dive, ask_friday); `spawn_task` started a background agent with no check | All eleven go through `_execute_tool` (a `handler=` argument for the voice-only helpers) with a voice ledger key |
| 3 | `routes/compute.py:39-75` → `python_script_adapter.py:136` | A peer's job ran its own Python on a self-reported trust score | `authorize_external`: each job waits for the owner's card; the peer resubmits once approved |
| 4 | `services/agent.py:1107/1120` → `calendar_write.py:307` | Calendar create/update/annotate sent invites with no question (the docstring said a card covered it; no code did) | Outward |
| 5 | `agent.py:5467` `content_create_post` / `content_schedule_post` → `publisher.py:682` | Scheduling a post to any of the platforms asked nothing; the publisher then posts whatever is scheduled | Scheduling is outward; drafting stays internal |
| 6 | `agent.py:7545-7553` connector tools | Every MCP write ran with ring 2 alone | Outward unless a read by name and by the union classifier |
| 7 | `agent.py:3185` background tasks, `scheduler.py:593` | Scheduled jobs held every outward power (`is_background_task` counted as authenticated and skipped the question) | Outward actions need a scoped, expiring grant; schedule id now reaches the task context |
| 8 | `agent.py:5313-5316` create/run_workflow (ring 1) from unauthenticated contexts (channels `manager.py:267`, creative pipeline `:511`) | Ring escalation: steps ran as authenticated background tasks | Steps are background work, so every outward step needs a grant or a card |
| 9 | `agent.py:4271-4306` `correct_wiki` | Text replace across every `~/.friday/*.json`, immediately: settings, approvals, connector commands, schedules, credentials | Only fact files (`CORRECTABLE_JSON`) |
| 10 | `write_file` into `~/.friday` or a file Friday loads as instructions | Could overwrite settings, approvals, SOUL.md, skills | Outward |
| 11 | `tool_hooks.py:167-176` | `confirmation_gate` could be turned off in settings and failed open on an exception | Critical |
| 12 | `routes/chat.py:1131-1157`, `2133-2159` | Both chat endpoints built their own prompt: no action policy, no override strip on memory, continuity, user model or heuristics | `seal_system_prompt`: strip, then the policy exactly once, last. Background prompts put their suffix before it |
| 13 | `agent.py:5959` `_get_governance_key` | An unavailable key became a throwaway random key, so receipts were signed with nothing checkable | The checkpoint takes the key from `proof_of_integrity` directly and holds outward actions when it cannot |
| 14 | the checkpoint's own first draft (found by the replay's harmless pass, fixed in bda86641) | A connector tool counted as a read if any word of its name was a read verb, so `update_user_info` ran unasked | The first word after the server name must be the read verb |

Deliberately outside the checkpoint, with the reason:

* **The owner's own REST actions** (Studio buttons, `/api/flow`, calendar
  insert from the UI, the vibe-coding terminals, which launch
  `claude --dangerously-skip-permissions`). They are the owner acting, not
  Friday. Friday could reach them only through loopback: `run_command` now
  refuses that, and `browse_web` already blocks private addresses.
* **Deterministic open/navigate intents** (`chat.py:598/627`): the user's own
  words, same turn, local and reversible.
* **Higgsfield's direct connector call** (`higgsfield_generate.py:99`,
  `higgsfield_catalog.py:100`): inside the governed generation tools or owner
  clicks. The discovery test names it as a reviewed exception, so a new direct
  connector call fails until someone reviews it.
* **Paid generation** (image, video, music, speech) is internal to the
  checkpoint; spend is governed by the budget guard, not a card.
* **OfficeCLI and Twilio** have no code yet. Their tools will be found by the
  discovery test and must be classified before it passes.

## Provenance on the card

Matching follows taintgate: values compared after lower-casing and dropping
spaces and punctuation, so an IBAN with spaces in a PDF still matches; long
text (commands, skills, instructions) by overlap of five-word runs. Structured
results are split per item, so the card can name the email a value came from,
and a value in a sender or participant field is labelled as such
("information", not a warning): replying to a sender or inviting someone
already on the meeting is the ordinary case. Short names ("general",
"Charlie") are not evidence either way.

Following a link exactly as a page or search result gave it is reading. A URL
that appears nowhere but whose site was named in outside content (the
exfiltration shape) asks; one pointing at this machine is refused.

A memory write (skill, wiki proposal, correction, or a file Friday loads as
instructions) asks when its text came from read content, and also when outside
content was read in the last 30 minutes of the same conversation, because
paraphrase defeats matching and a planted rule is exactly what gets
paraphrased.

## Measured

AgentDojo v1.1.2, the 629 attack cases buried-injections used, plus the 97
harmless user tasks. Tools mapped onto Friday's own where it has them
(send_email → draft_email, create_calendar_event, get_webpage → browse_web,
create_file → write_file); the rest (bank, Slack, travel, deleting mail) as
connector tools, which is how they would reach Friday.

**Replay** (`tools/injection_bench/replay_gate.py`): the real `_execute_tool`
and every hook, fake handlers, and the attacker's ground-truth calls, i.e. a
model that obeys every injection. Keyword classifier for both runs.

| | before (bf1d5866) | after |
|---|---|---|
| attacks with a harmful action | 588 | 588 |
| ran with no human decision | **368** | **0** |
| stopped at an approval card | 220 (email only) | 568 |
| card named where the detail came from | 0 | 567 |
| stopped at a chat yes/no question | 0 | 20 |
| harmless actions (82) that ran without asking | 71 | 0 |
| harmless actions stopped at a card | 7 | 17 |
| of those, carrying a warning | 0 | 14 |
| harmless actions asked about in chat | 4 | 65 |

The 41 cases with no harmful action are injections that only try to change what
Friday says (e.g. "tell the user this hotel is best"). Provenance does not
address those.

The 14 harmless cards with a warning are the price, and each is defensible:
paying a bill to an account number read from a file, an address change taken
from a document, inviting someone whose address came from a web page, a
deletion right after reading a document.

The 65 chat questions are the action policy doing what Stephen asked of it on
2026-09-24: outward actions ask first.

With Laya configured but not answering (as during its first minute after a
restart, or on an install without the model), connector reads are held too:
0 attacks run, but 204 are stopped by a question rather than a card that
names the source, because the reads that would have recorded the injected text
never ran.

**Live** (`run_live.py`): a real model in Friday's real loop, against
AgentDojo's own environment and scoring. One user task per suite against every
injection task (27 attack episodes), plus 12 harmless tasks that end in an
action. A shadow copy of the environment receives every call the model tried,
so "the model was fooled" and "the attack happened" are separate numbers.

| model, code | attack episodes | fooled | attack happened | harmless tasks finished in one turn |
|---|---|---|---|---|
| Claude Haiku 4.5, before | 27 | 0 | 0 | 1 of 12 |
| Claude Haiku 4.5, after | 27 | 0 | 0 | 0 of 12 (1 warning card) |
| Claude Sonnet 5, before | 27 | 0 | 0 | in progress |
| bonsai2:27b (local), after | 10 clean of 27 | 0 of 10 | 0 | in progress |

Status on 2026-09-24: the Sonnet 5 and Opus 5.5 runs (before and after) and
the bonsai2 before-run were still going. 17 of bonsai2's 27 attack episodes
failed because Friday's llama-server on :8090 went away mid-run and came back
as a new process at 09:56 (connection refused, then "503 Loading model"); they
need re-running before bonsai2 can be scored. No model in any finished episode
followed an injection, so the live runs do not yet exercise the gate: the
replay above is the measure of what the gate does when a model is fooled.

Harmless tasks rarely "finish in one turn" before or after, because Friday's
action policy tells the model to ask before sending or paying, and a one-turn
benchmark counts a question as unfinished. One harmless Haiku episode fails on
both versions with the Anthropic API rejecting an empty text block, a
pre-existing defect in the Claude loop, tracked separately.

**Text detection, for comparison** (`detector_compare.py`): Friday's own
override-phrase patterns caught 0 of 629 (they look for grants of authority,
which these attacks never make). A generic keyword detector caught 629 of 629
with AgentDojo's single attack template around the instruction, and 40 of 629
(6%) without it. That is the buried-injections result in one line: a detector
tuned to the text it has seen.

## Not done

* Voice prompts split at `VOLATILE_MARKER`; whether the policy lands in the
  system text or the user turn there was not checked.
* LLM summaries of read content that feed the prompt later (session summaries,
  briefings, research reports) are not in the provenance ledger.
* Short identifiers (a Slack user "Fred") and re-encoded or paraphrased values
  are not traceable.
* The ledger lives in memory; a restart forgets what was read.
* Grants have an API and no UI.
* `send_to_session` (typing into an already-open shell) is outward, so each
  message asks; relaxing it would reopen a path around per-command review.
