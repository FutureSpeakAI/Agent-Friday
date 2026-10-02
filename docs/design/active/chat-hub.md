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

## M3. The hub layout (in design)

Left rail: projects with their conversations inside. Centre: the chat. Right: a canvas that
opens when there is something to work on (a document, a page preview, media), or the Build
panel by a "Build" switch: the salon's editor, live preview and terminal beside the chat,
talking to the same Friday, working on the project's codebases. The salon's safety rules
hold: Friday edits a copy of herself, never the live checkout; execution and writes go through
the existing gates, batched per task, with no new bypass. Media's retirement of Draft, Content
and Studio stays. Voice: "open my Friday project", "show me the preview", "build mode". The
hub's targets register with the hand-cursor target registry when it lands.
