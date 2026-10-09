# File access: letting a cloud model read your files

Agent Friday™ keeps what it cannot confidently classify as public on this PC.
Ask Friday to read your résumé and reason about it with a cloud model, and
she declines, because a résumé is full of the contact details that rule
exists to hold back. A file grant is how you say "this document, on purpose".
The decision is recorded, scoped, expiring where it has to be, and
revocable.

The alternative is pasting the text into the chat by hand. That sends the
same content to the cloud with no record. A grant inside Friday keeps the
record.

File grants matter only when a cloud model is in use. With a local model
nothing leaves the PC, and no grant is needed.

## Granting access

Open **Settings > Privacy & Data > File access**.

1. Type the full path of a file or folder under **Add**.
2. Choose **File** or **Folder**. For a folder, choose how long it lasts: 1,
   7 or 30 days.
3. Press **Add**. The result names what the privacy scan found in the file.

The list shows each grant with its state: Valid, Paused, File changed, File
missing, Expired, Quarantined or Unverified. **Remove** revokes one. Nothing
in the list is deleted from history; a removal is recorded as a removal.

Friday can also ask for access. She raises an approval card naming the file
or folder, and you approve it with **Allow it** on screen. A typed or spoken
"yes" does not count, and Friday has no tool that creates a grant. By voice you
can ask "what files can the cloud see?" or "take that away", and a spoken
request for access puts a card on screen.

Paths must be full local paths. Network paths, web addresses, device paths
and relative paths are refused. A folder grant on a whole drive or on your
whole home folder is refused, and so is a file over 50 MB.

## The rules

### A file grant is pinned to the file

Granting a file records a SHA-256 of its contents at that moment. If the file
changes, the grant shows **File changed** and stops applying until you grant
it again. The permission covers the document you looked at, not whatever
someone puts at that name later. A file grant has no expiry because the pin
already limits it.

### A folder grant expires within 30 days

A folder cannot be pinned to its contents, because it covers files that do not
exist yet. A folder grant therefore needs an expiry, and the maximum is 30
days. The limit is enforced in code, not only on the screen.

### A deny mark beats any grant

A deny mark on a path always wins over a grant, however specific the grant is.
Your never-send list also still applies. Overriding it needs an explicit
acknowledgement of each match the scan found in the file, and exists for
single files only, never for folders, where you cannot have seen what you were
agreeing to.

### Only you can grant

No model, on any surface (chat, voice, background work), can create a grant.
Grants come only from the controls above and from an approval card that you
click. So a document that contains instructions cannot widen its own
permissions, and cannot script the consent screen, which shows the privacy
scan's own findings rather than text from the file.

### Content is sendable only when the real file is read

A grant does not switch the privacy gate off. When Friday reads a granted file
with the file-reading tool, the passages she just read are marked sendable. Text she only claims came from a granted file is not. Passages from
a content search of a granted file still go through the normal gate.

### A damaged record can only tighten

Grants are kept in an append-only record, `.friday\privacy\file_grants.jsonl`,
separate from `settings.json`. Every line is signed. A line that cannot be
verified is never trusted:

- While such a line is in the record, **every file permission is paused** and
  deny marks that verified are still enforced. A notice explains why.
- A grant made under a key Friday has since replaced is set aside unchanged
  and shown as **Quarantined**, with a **Re-grant** button. Re-granting raises
  a fresh approval card.
- **Start file access fresh** sets the old record aside and starts with no
  permissions. It also needs your approval on screen, and your never-send
  marks are kept.

A damaged record can remove permissions. It can never add one.

## Auditing your grants

The record is plain JSONL on your own disk, so you can read it with any text
editor:

```
%USERPROFILE%\.friday\privacy\file_grants.jsonl
```

Revoking appends a revocation instead of erasing the grant, so what Friday was
allowed to send, and when you allowed it, stays answerable afterwards.

## For developers

The local API behind the screen needs a signed-in session, so a bare `curl`
gets a 401.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/privacy/file-grants` | Grants, deny marks, notices and ledger status |
| `GET` | `/api/privacy/file-grants/scan?path=...` | Privacy scan of a candidate file. Read-only |
| `POST` | `/api/privacy/file-grants` | Create a grant: `{path, scope: file or folder or glob, expiry_days}`. `expiry_days` is required for folder and glob, maximum 30 |
| `POST` | `/api/privacy/deny-marks` | Create a deny mark: `{path, scope}` |
| `POST` | `/api/privacy/file-grants/<id>/revoke` | Revoke a grant or a deny mark |

Glob grants and deny marks are available through the API only. The screen
offers files and folders.
