# Your knowledge

If you already keep notes or documents somewhere (an Obsidian vault, a Documents
folder, a Notion export), you can point Agent Friday™ at them. She reads them on
this PC and can then find things in them and answer from them, with citations.
It is optional. You choose every folder.

## Adding folders

You add folders in the [setup chat](setup-chat.md), in **Settings > Privacy &
Data > Your knowledge**, in the [Library](library.md), or by voice ("add my
Obsidian vault"). Two separate choices apply to each folder:

- **Let Friday read and index it on this PC.** Your files stay here. This adds
  the folder to the Library, exactly as **Add to Library** does.
- **Also let cloud models read it.** A different permission, off unless you turn
  it on, folder by folder, and it ends after 30 days (see
  [File grants](file-grants.md)). Reading a folder here never lets a cloud model
  read it.

## What happens after you add folders

Each folder you add becomes **one job on the Local AI queue**. Folders are read
one at a time, and only while the PC has been idle: the wait is the same
"Background AI work waits until you've been away for" choice in Settings >
Advanced (10 minutes unless you changed it). Nothing is read while you work, and
nothing is read while you are in a meeting.

- **You can see every job.** Each pass shows in **Activity** as a task of its
  own: it names the reading model (the small model the Library already uses, on
  your processor, no language model), its status, a log of counts, a link to its
  reasoning trace and its place in the queue. The top-bar chip reads, for
  example, "Indexing your Obsidian vault · 1,240 of 3,100 · paused while you're
  here". **Run now** starts the folder without the idle wait (it still gives way
  to a chat), **Pause** holds it before the next file, **Resume** carries on, and
  **Cancel** stops that folder. You can also say "pause the indexing" and
  "how's the indexing going?".
- **Pause and resume lose nothing.** Every file is saved as it is read, so the
  count survives a restart, a crash, or you coming back to the keyboard. The
  folder picks up at the next unread file. It also pauses by itself when free
  memory is under the floor a model load keeps (`local_scheduler_ram_floor_gb`,
  4 GB unless you changed it), on battery (unless you allowed that), while you
  are off the record and during a meeting. A pause that will last (yours,
  battery, off the record) hands the queue back, so a model job is not held up
  behind it.
- **Your first answer comes early.** The Library reads text first, so Friday can
  answer from a folder, with citations, as soon as its first files are in. The
  rest of the folder keeps filling in behind it.
- **Friday notes what each document is.** On the processor, with the same small
  reading model (no extra model is loaded): its type (note, contract, invoice,
  paper, receipt, CV or letter, or "other" when she is not sure), how sensitive
  it is (the same local classifier that decides which shelf a document goes on),
  and whether it is a copy of another document. A near copy needs the words
  themselves to agree, since two documents on one topic look alike to the reading
  model. These notes sit beside the document and change nothing: no file is
  moved, merged or deleted, and a document never moves to a less private shelf.
- **Drafts wait for your review.** When a local model is installed, Friday also
  reads the documents that are rated public and drafts a short summary and a few
  facts for each, as a second job (Activity names the local model). They go to a
  staging folder, `.friday\wiki\_staging`, not into your wiki or the knowledge
  graph. Every draft carries the sentence it came from, copied from your
  document and checked against it, and a note of which model wrote it; a fact
  without a matching sentence is dropped. Nothing there is published until you
  review it. Vault-shelf and private documents, and copies, are skipped. With no
  local model the drafts wait; with learning from the Library turned off in
  Settings there are none. Removing or forgetting a document, forgetting a
  person who is named in a draft, and changing the file's text all delete the
  drafts made from it, and a draft that was still being written when you forgot
  the document is thrown away. Rejecting a draft keeps only a note that you said
  no, with none of its text.
- **New and changed files are found while the PC is idle.** Every 15 minutes of
  idle time Friday compares each folder with what she read (size and modified
  time, no reading). If anything is new or changed she queues **one** job for
  that folder, and it runs the same idle-gated way, never at the moment you save
  a file. A file whose contents did not change is not read again. A folder you
  paused or stopped is left alone until you resume it. Files you delete from
  disk leave the Library every 15 minutes whether or not reading is paused or the
  PC is idle (it only looks, it reads nothing). Friday checks your consent before
  every file, so removing a folder stops its reading at the next file. **Resume**
  undoes only **Pause**: a folder you cancelled stays cancelled until you add it
  again.

Everything in this section happens on this PC. The queue and Activity show
counts and labels, never what is in a document.
