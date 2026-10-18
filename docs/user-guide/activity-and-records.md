# What Friday is doing, and what she did

Agent Friday™ keeps a record of her work. This page shows how to follow a
task while it runs, how to steer or stop it, and where to read what she did
afterwards. Everything on this page is kept on this computer. A read-only
token you mint (below) lets another program read task records.

## Opening a task

In the **Simple** display style, the desktop's **Activity** view lists tasks
on three tabs: **Now**, **Needs you** and **Recent**. Click a task to open its
card in the **System** workspace. An approval on **Needs you** opens
**System > Approvals** instead. The Activity panel in the top bar has its own
tabs; see [Notifications](notifications.md).

## The task card

### Status

The card shows one status word:

| Status | What it means |
|---|---|
| **EXECUTING** | The task is running now. |
| **STALLED** | The task says it is running, but it has not checked in for more than 30 seconds. The card says when it was last seen. |
| **WAITING** | The task is queued and has not started yet. |
| **INTERRUPTED** | The task was running or waiting when Friday restarted. It does not carry on by itself; where it can, the card offers **Resume** (or **Resume from step N**). |
| **STOPPED** | The task was cancelled. |
| **COMPLETE** | The task finished. A grade shows if its result was checked. |
| **UNVERIFIED** | The task finished, but the check of its result did not pass. Look at the result before you rely on it. |
| **FAILED** | The task ended with an error. |

Once a task has started steps, the card shows "step N", or "step N of M" when
the total is known. There is no percentage, because one would be a guess.

### The log

Click the task's title, or ▸, to open its log. The header counts the events.
Tick **show reasoning** to add Friday's reasoning to the log. If part
of the record is missing, the log says the timeline is incomplete.

### While it runs

- **Steer** sends the task a message without stopping it. The box says
  "Steer: tell it something without stopping it". The task gets your message
  after its current pass, and the log records it.
- **Stop after step** lets the current step finish, then stops ("Finish the
  current step, then stop. The record stays complete."). The button then reads
  **Stopping after this step…**.

### When it has finished

- **Re-run** starts a new task from the same prompt. The old record stays as
  it was. Nothing resumes on its own, after a restart or otherwise.
- **Delete record** deletes the task's record at once. It does not ask first.
- **✕** dismisses the card. The record and its journal are kept.

## In the System workspace

### 🧾 What Friday did

This card lists every action the checkpoint decided on, from the signed
receipts. Choose the window: **Since last visit** (the default), **Last 24
hours** or **A day…**. The top line counts actions by decision and says how
many signatures verified.

Each row ends with **✓ signature verified** or **✗ not verified**. If
Friday's signing key cannot be found, the card says "signing key unavailable:
nothing can be verified". The first 40 rows show; **Show all N** shows the
rest. Under the list, **Tasks finished** links to each task that ended in the
window.

What the receipts contain, and where they are kept, is explained in
[Approvals and receipts](approvals-and-receipts.md#receipts).

### 📓 Task records

Every background task writes a journal of what it did, decided, called and
spent. The journal survives restarts.

**Keep records for [N] days** sets how long journals are kept. 0, the
default, keeps everything until you delete it.

**Mint read-only token** creates one credential that lets another program
read task records but not steer, stop or delete them; **Revoke** cancels it.
See [Task observation](../reference/task-observation.md).

### ↩ Changes Friday made

"Mail, files and wiki pages Friday organized for you. Undo puts each one
back; nothing was ever deleted."

The card shows the latest 15 changes. **↩ Undo** appears only where the
change can still be put back. A row can carry a tag:

- **approved on a card**: you approved this change.
- **undone**: you already put it back. The summary is struck through.
- **off the record**: it happened while Friday was off the record.

## The Context Log

The **Context Log** is on the main page of the **System** workspace. It is an
append-only record, one file per day. Search can filter by these types (the
log also holds others, such as web browsing, file writes and voice turns):

| Type | What it is |
|---|---|
| **chat_user** | A message you sent |
| **chat_agent** | A reply from Friday |
| **tool_call** | A tool Friday used |
| **wiki_edit** | A change to a wiki page |
| **wiki_delete** | A wiki page deleted |
| **file_read** | A file Friday read |
| **task_spawn** | A task Friday started |

The stats show how many days and entries are kept, the size, the average per
day, and the first and last dates.

- **⏸ Pause Logging** stops new entries; **▶ Resume Logging** starts them
  again.
- **⤓ Export** downloads the whole log as a ZIP file.
- **🔍 Search** finds entries by words, between two dates, and by type
  (**All types** or one of those above). It shows up to 200 results.
- **🗑 Delete Range** deletes whole days between the dates you set. You
  confirm by typing **DELETE**.

When you are off the record, the heading adds "(off the record: nothing is
being saved)".

The same switch is in **Settings > Privacy & Data > Conversation logging >
Log conversations and tool use**: "An append-only record of chats, tool calls
and file reads, kept on this machine." It is on by default. The same section
has **Go off the record** and **Keep the log for** (from **Forever** to **30
days**).

## Learning that waits for you

Friday does not change how she works without you.

- **Learned habits.** When Friday finds a pattern that helps a kind of task,
  she proposes it on an approval card: "Add a learned heuristic for [type]
  tasks" (or "Remove…"). The card shows the evidence. **Approve** adds it,
  **Deny** rejects it, and a card you leave waiting changes nothing.
- **Settings by sentence.** When you ask in a sentence to change a setting,
  Friday shows the change first. "Nothing changes until you approve." A
  maybe is not a yes. After your yes, the **Undo** beside that setting puts
  it back for 30 days. Only the newest change to a setting can be undone.
- **Tone in a conversation.** Friday adjusts how formal and how detailed she
  is as you talk. That adjustment lasts only for the session and is never
  saved. Only traits you set or accepted are kept.

Records of what Friday learned are described in
[Skills](skills.md#learning-records). How Friday saves memory is in
[Memory](memory.md).
