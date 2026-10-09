# Library

The Library is where Agent Friday™ keeps the documents you choose to give it.
Friday reads them on your PC, finds the passages that answer a question, and
cites each statement with a footnote that opens the exact page. The Library
workspace has two ways to look at your documents: a list and a 3D view called
Shelves.

## Open it

Choose **Library** in the **Work** group of the dock. You can also ask Friday to
show it ("open my Library", "show the Library as shelves"), or Shift-click a
footnote in a chat reply to open that source.

The header has **List** and **Shelves** buttons, **Ask**, **Add...** and
**Inspector**. The left side lists **Ask**, **All documents**, one entry per
folder you added, **Couldn't read**, **Vault shelf** and **Browse this PC**.

## Add documents

Nothing is in the Library until you add it.

1. Choose **Add...**, then **Add a folder**, or paste a folder or file location.
2. Choose **Add to Library**. The header says how many documents are read and
   how many are still being read.
3. After your first folder, Friday asks once whether its knowledge graph may
   learn the people and places in these documents. Neither answer is
   preselected. You can change it later in Settings.

On an empty Library you can instead turn on **Include everything Friday already
tracks**, which reads the files and folders you have already given Friday access
to, plus the folders Media is built from. Private files go only to the sealed vault shelf, and places Friday never reads stay excluded. Turning it off takes out of the Library
what only that switch covered.

Friday can also propose adding a path, but only through an approval card that
you accept. A model cannot add a path by itself.

What the Library reads: PDF, Word (.docx), Markdown, plain text, HTML, CSV and
TSV, Excel (.xlsx), source code and config files, and caption files (.vtt, .srt).
Audio and video are read through a transcript: a caption file next to the file,
or a transcript Media has already made (see [Media](media.md)). Scanned PDF
pages are read with text recognition when that component is installed.
A document that breaks a limit (200 MB, 2,000 pages) or cannot be read appears
under **Couldn't read** with the reason, and **Try again** re-reads it. The
Library never silently skips a file.

New and changed files are read when the PC is free. Reading waits while Friday
is off the record, and while the PC is on battery unless you allow it (see
Limits).

## Ask your documents

1. Choose **Ask** and type a question.
2. Friday finds passages first and lists them, with **How I looked** to show the
   steps.
3. Choose **Write an answer** if you want a written reply. It is written on
   this PC by your local model, and every statement carries a footnote.
4. Click a footnote to open the exact page, or the exact moment in a recording.

If nothing matches, Friday says so and does not guess. Ask Friday in chat in the
same way, for example "what does my lease say about notice?". A footnote in chat
names the document and page, and shows "source no longer in your Library" if you
have since removed it.

## Shelves (3D)

Choose **Shelves**. Each folder stands as a tall glass shelf, and each document
stands on its shelf as a card. Open a document and it moves forward with its
sections fanned out in reading order, with a reading plane in front. If the 3D view does not load, Friday says so and your documents stay listed on the left.
**Browse this PC** shows files that are not in your Library yet, in the same 3D
browser, and says so.

## The inspector and the vault shelf

Select a document and open **Inspector** to see its kind, pages, shelf, whether
it has a cloud permission, and how many chats cited it. Buttons:

- **Open** reads it in the Library reader.
- **Move to vault** or **Move to open shelf**. Moving off the vault asks you to
  confirm, because its text is then stored without the vault key and becomes
  searchable by keyword.
- **Remove from Library** stops indexing and deletes the index entries. The file
  on disk is untouched.
- **Forget everywhere** asks for confirmation, then deletes the index entries,
  rewrites every saved chat so footnotes that cited the document read "[forgotten
  source]" with its quotations removed, and stops a folder you added from bringing the document back. The file is untouched. If some copies could not
  be reached, Friday says which.

Documents that Friday's sensitivity check rates private or sensitive go on the
**vault shelf** automatically: their text is sealed with your vault key, kept out
of full-text search, and readable only while the vault is open. With no vault
key, such a document is not indexed and is listed as skipped. See
[Privacy](privacy.md).

## What always asks

- Adding a path Friday proposes needs your approval card. Adding with the
  workspace's own folder picker is your own act.
- Forgetting and moving a document off the vault shelf ask in a confirmation box.
- Deleting a file from disk is a separate action that goes through
  [approvals](approvals-and-receipts.md); the Library never deletes your files.

## What stays on your PC, and what can leave

The index is built and stored on this PC. The workspace says "Indexed on this PC, nothing sent". Whether the
index file is encrypted depends on an optional component, and the Library says
which of three states applies (encrypted under your Windows account, encrypted
under your vault key, or a plain file).

Library text goes to a cloud model only when all of these hold:

- the chat is using a cloud model,
- **Settings, Privacy & Data, Library, Let a cloud model write Library answers**
  is on (off by default),
- the document has its own cloud permission under File access (see
  [File grants](file-grants.md)), and
- it is not on the vault shelf.

Even then, one answer sends at most the number of characters you set
(default 6,000, about three pages); anything beyond is left out and Friday says
so. With the switch off and a cloud model in use, Friday tells you and offers to
switch the chat to the local model. An answer delivered by text message or a chat
service counts as cloud.

## Settings

Under **Settings, Privacy & Data, Library**:

- **Include everything Friday already tracks** (see above).
- **Library search**, on by default. Off, Friday cannot see your documents;
  nothing is deleted.
- **Let a cloud model write Library answers** and the character cap.
- **Read documents while on battery**, off by default.
- **Let the knowledge graph learn from Library documents**. Forgetting a document
  removes what the graph learned only from it.

If the permissions record cannot be verified, the Library pauses and shows
**Open File access** to fix it.

## Limits

- Reading is paused off the record, and on battery unless allowed.
- Search also uses a local encoder for meaning-based matches. The Library
  records whether that encoder is available, and the written answer comes
  from your local model.
- Vault-shelf documents cannot be searched while the vault is locked.
- Friday can tick and point at documents on screen; see
  [See and Touch](see-and-touch.md).
