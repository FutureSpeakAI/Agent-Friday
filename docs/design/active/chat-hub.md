# The Chat Hub: vibe, chat and build from one place

**Status:** owner-approved direction (2026-10-02). M1 and M2 built on `feat/salon-phase1`;
M3 in design. This document records what is built and the decisions behind it; the
salon's own rules stay in `vibe-coding-salon.md`, the shell's in `unified-shell.md`.

**The ask.** The chat tab had none of the universal branding and none of the vibe-coding
salon, and "projects" answered from two different stores. The owner's direction: put it
all into the Chat workspace, so the user can vibe, chat and build from one interface.

## M1. Every chat surface wears the one top bar (built)

A chat in its own tab (`?chrome=chat`, `body.chrome-chat`) hides the whole React layer and
shows the chat again through an allow-list. The top bar is part of that allow-list: the
same bar the desktop and every workspace tab wear (`unified-shell.md` §4).

| In a chat tab | Does |
|---|---|
| Lockup (Agent Friday™ by FutureSpeak.AI™) | back to the desktop with this chat open (`/?conversation=<id>`) |
| Context slot | a chip naming the chat (💬 + its title; at phone width the glyph alone) |
| Model selector | the same menu, same effect |
| Clock, resource strip, connection light | as on the desktop |
| Notifications, Quick Draft, the chat button, fullscreen with chat, settings, camera | not offered: their panels live in the hidden layer; Settings is reached from the desktop |

The sidebar and the chat start under the bar by its own height (`--fr-topbar-h`). The
rule is held by `tests/unit/test_chat_tab_shell.py`, in both UI files and the mirror's
stylesheet (`ui_parts/head.html`).

## M2. One project store (built)

**Before.** Two stores wrote under `~/.friday/projects/`: the chat sidebar's projects
(`<proj-xxxxxxxx>/project.json`: name, seat, instructions) and the creative projects with
their Series Bible (`<slug-xxxxxx>/bible.json`, plus `active.json`). Each listing skipped the
other's folders by file name; the creative delete removed any folder in the root; the chat
project's instructions were stored and shown but never reached the model; the Bible reached
only background prompts, never a chat turn.

**Now.** One record, `project.json`, owned by `services/projects`:

| Field | What it is | Who reads it |
|---|---|---|
| `name`, `type`, `color`, `archived` | the folder | the sidebar, the creative panel |
| `seat` | the default model for chats inside; a chat's own pick wins, then the project, then the global default; a codebase's seat first of all | every chat turn (`conversations.effective_seat`) |
| `instructions` | standing instructions, capped at 4,000 characters | every chat turn in the project |
| `files[]` + `files/<name>` | knowledge the chats can read: 2 MB per file, 20 MB per project; text files are quoted (600 characters each, 1,500 in all), the rest named | every chat turn; `GET/POST/DELETE /api/projects/<id>/files` |
| `codebases[]` | the codebases the Build panel works on; the codebase record names its project too, written together | every chat turn; the Build panel (M3) |
| `bible{}` | characters, locations, continuity, style guide, assets, pipeline status | `services/creative_memory`, now an adapter over this record; the creative routes and tools are unchanged |

**What a chat inherits.** `projects.context_block(conversation_id)` is appended to the
system prompt in both chat paths (`/api/chat`, `/api/chat/send`), beside the codebase block:
the project's name and type, its instructions, its files, its codebases and its Bible. A chat
outside any project gets nothing. Background and voice prompts take the conversation's project
when one is known, else the globally active creative project (pipelines keep working).

**Migration.** A legacy `bible.json` folder gets a `project.json` beside it on first sight
(`projects.migrate_legacy`, idempotent, lossless: every Bible field reads back identically
through the creative view). The legacy file is left exactly as it was; a manifest under
`projects/_migrations/` lists what was written, and `projects.rollback(manifest)` removes only
that. Off the record, no file is written. Deleting a project removes its own folder and
detaches its chats; the chats are kept.

Tests: `tests/unit/test_projects_one_store.py`, `tests/api/test_project_prompt_and_files.py`,
plus the existing project, creative and codebase suites.

**Known gap.** `ui_parts/app.html` never carried the chat sidebar or the project editor; the
editor's Files and Codebases sections live in `index.html` with the rest of it.

## M3. The hub layout (M3a and M3b built; voice to come)

Left rail: projects with their conversations inside (the sidebar, with what each project holds).
Centre: the chat. Right: the canvas, which is the artifact panel (a document, a page preview, a
diff, an image) or, by the **Build** switch, the Build panel.

**M3a, the Build switch.** A chat filed in a project whose project connects codebases is offered
Build above the chat: one codebase binds on the click, several open a short list.
`POST /api/conversations/<cid>/codebase` binds the chat (both sides written together) or
unbinds it with `{codebase: null}`; "Chat" returns the canvas. The Build panel is the codebase
panel: Preview, Files (the editor), Changes (steps, receipts, undo), Terminal.

**M3b, the Terminal and the gate.** `codebases.run` runs one command in the codebase's own
folder (PowerShell, bounded, output redacted and kept as a run, newest first, announced on the
bus); the Terminal tab lists the runs and asks Friday in the chat to run a typed command, so
every command goes through the one gate. There is no route that runs a command from the page.

| Rule | Where |
|---|---|
| A command or Claude's agent in a codebase is outward and self-gated | `governance/action_gate.py`: `codebase_run`, `codebase_agent` in `SELF_GATED`, not `INTERNAL` |
| One card per task, not per command | `services/codebase_tasks.py`: the first `codebase_run` or `codebase_agent` of a task raises one card; approval mints a grant `codebase:<id>` (30 minutes, 40 uses), runs the first action, posts the result; later actions spend the grant (`action_gate.consume_grant`) |
| Denied means nothing | a denied or expired card runs nothing and mints nothing; a later ask raises a new card |
| The same refusals as run_command | key material, the blocklist, Friday's own API, before anything runs |
| Friday edits a copy of herself | `codebases.is_friday_checkout`: a codebase is never a checkout of Friday's source, in `create(existing_path)` or in `run`; the copy is Phase 7 |

Tests: `tests/unit/test_codebase_run.py`, `tests/unit/test_codebase_run_gate.py`,
`tests/unit/test_chat_hub_build_switch.py`, `tests/unit/test_chat_hub_terminal_ui_files.py`,
`tests/api/test_conversation_codebase_bind.py`.

**The hub's tools are on demand.** The always-on tool catalogue keeps its ceilings
(`tests/unit/test_latency_budget.py`: 23,000 tokens with the index off, 4,500 for what a turn
sends, 9,000 standing), and measured, the always-on tools alone sit within two hundred tokens of
the first. So the hub's twenty tools (`agent.HUB_TOOL_NAMES`) live in the workspace-tools pool
(`agent.WORKSPACE_TOOLS["hub"]`), as Media's do: a chat in the hub (bound to a codebase, or filed
in a project) gets them in its turn's catalogue as index lines, with `codebase_edit` resident;
every other chat sees the three ways in (`artifact_put`, `improve_workspace`, `open_project`) on
the loader's by-name line and loads any of them with `load_tools`. Voice declares its own. A hub
chat's turn costs about 500 tokens more than a plain one, which is the hub's own cost, not every
chat's. `tests/unit/test_hub_tools_on_demand.py` holds the rule.

**M3c, voice (built).** Three tools through the governed path, each with the layout tool's page
round trip (a chat-kind `hub` event; the page that applies it acks, and only that earns `HUB_OK`;
`HUB_SAVED` says what was remembered when no page showed it; `HUB_FAIL` says why not):
`open_project` ("open my Friday project": the project's latest chat comes to the front, a new one
when it has none), `show_preview` ("show me the preview": the panel opens on Preview, or says
there is nothing to preview), `build_mode` ("build mode", "build mode with the rent tracker",
"leave build mode": binds the chat to one of its project's codebases, or unbinds it). Tests:
`tests/unit/test_hub_voice_tools.py`. **M3d.** The hub's targets register with the hand-cursor
target registry when it lands.

**Known, pre-existing, out of scope here:** `/api/vibe-code/launch` opens Claude Code with
permissions skipped and no card (the salon spec records it); the mirror has no chat sidebar.
