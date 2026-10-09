# Chat and projects

Agent Friday™ keeps every conversation as its own thread, and you can group
threads into projects. A project carries a default model, standing instructions
and files that every chat inside it inherits. A chat can sit in a small panel, a
floating window, or a full browser tab with a sidebar of all your chats.

## Three places a chat can live

| Place | How to open it | What it is for |
|---|---|---|
| **Panel** (the chat tray) | Always available at the edge of the screen | The conversation you are in while you use a workspace |
| **Window** | **Window** in the chat header | A chat as a floating window in the workspace, so you can work in it beside another workspace |
| **Tab** | **New tab** in the chat header | A chat in its own browser tab, with the sidebar of conversations and projects |

The panel's layout button (**Where the chat goes**) puts the chat on the left or
right with the workspace in the rest of the screen, or hides it. Each window or
tab can use a different model at the same time, because every conversation has
its own model setting. If the browser blocks the new tab, Friday tells you to
allow pop-ups for it.

## The sidebar

The sidebar appears in a chat tab only. The floating window and the panel have
no sidebar.

At the top: **+ New chat**, **+ Project** and **+ Codebase**. Below that, a
**Search chats** box that filters by title. Then your projects as collapsible
folders, each with its chats, and the chats that are not in any project under
**Not in a project** (or **Chats**, when you have no projects).

Chats are ordered with pinned chats first, then by when you last worked in
them. Filing, pinning or renaming a chat does not reorder it by itself.

Each chat has a **More** menu with these actions:

- **Rename**, with a prompt for the new title.
- **Pin**, which keeps it at the top of its list.
- **Move to**, which files it in a project, or takes it out with **No project**.
  If you move a chat to a project that no longer exists, Friday leaves it where
  it was and says so.
- **Archive**, which removes it from the sidebar.

A chat shows which model it will answer with. "Bound to" means you chose that
model for this chat. "Inherited from this chat's project" means the project's
default applies.

## Projects

Choose **+ Project** and give it a name. Use the gear beside a project, **Settings
for** that project, to set:

- **Default model.** Chats in the project use it unless a chat is bound to
  another model. A model your PC cannot seat is refused when you set it, with the
  reason, rather than failing on the first turn. On a PC with limited graphics memory only one local model fits at a time, so
  a project default also keeps its chats on the same model.
- **Standing instructions**, up to 4,000 characters, sent with every turn in the
  project. On a cloud model they are paid for on every turn.
- **Files the chats in this project can read.** Add a file (up to 2 MB each and
  20 MB per project) or remove one. For each turn, text files are quoted to Friday
  in short excerpts (600 characters each, about 1,500 in all) and other files
  are named with their size. A file that looks like key material is listed but
  never quoted.
- **Codebases the Build panel works on**, connected from the codebases you have
  started with **+ Codebase**.
- **Delete.** This deletes the folder only. The chats inside are kept and move to
  **Not in a project**.

A project does not make Friday forget anything outside it. Conversations are
kept apart, but Friday's memory, wiki and knowledge map are shared by all chats.

## The model chip

The conversation menu in the chat header shows which model a chat uses. Choose
**Model for this conversation** to pick one for this chat only. Your other chats
and your default stay as they are. Choose **Follow my default model instead** to
remove the binding. Every reply also says which model answered it, and Settings
shows which jobs run on this PC and which in the cloud (see
[Settings](settings.md)).

## Build mode

**+ Codebase** starts a chat bound to a codebase, from a small HTML page, a React
app, a new dock workspace, a study copy of a repository, or a folder already on
your PC. When a chat is bound, its panel becomes the Build panel, with Preview,
Files and Changes. In a project that has connected codebases, a **Build** switch
in the chat offers the same thing. For a folder on your PC, Friday works on a
separate branch, never directly on yours. Pages and apps run in a sandboxed
frame that cannot reach Friday's data. A larger request starts with a written
plan, and nothing is built until you approve the plan in the panel.

## By voice or chat

- "Open my Friday project" brings the project's latest chat forward, and makes a
  new one if the project has none.
- "Show me the preview" opens the preview beside the chat, or says that there is
  nothing to preview.
- "Build mode" or "build mode with the rent tracker", and "leave build mode".

These only move your own screen and change which of your records a chat is
bound to. They do not need approval.

## What always asks

Any action a chat takes outward (sending mail, changing files, publishing) asks
the same way in every place, panel, window or tab. See
[Approvals and receipts](approvals-and-receipts.md). Rearranging chats, renaming,
pinning, archiving and moving them are your own organising and do not ask.

## What stays on your PC, and what can leave

Conversations, projects and project files are stored in the `.friday` folder on
your PC. What leaves is what the model needs to answer, and only when the chat's
model runs in the cloud. That includes the project's standing instructions and
the excerpts of its files. See [Privacy](privacy.md). Off the record, nothing is
written.

## Limits

- The sidebar exists only in a chat tab.
- A project holds up to 20 MB of files and 4,000 characters of instructions.
- Friday can tick, point at and filter the chat list on screen; see
  [See and Touch](see-and-touch.md).
- Podcasts can be made from a conversation; see [Podcasts](podcasts.md).
