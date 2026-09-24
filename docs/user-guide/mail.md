# Mail

The **Messages** workspace is a Gmail client with Friday alongside. Friday can
read, search, sort and draft your mail. It sends a message only after you
approve that exact message on a card.

## Connecting Gmail

Open **Settings → Accounts & Keys → Google** and add an account. Google's own
sign-in page opens in your browser, and the permissions Friday asks for are
listed before you continue. You can connect more than one account.

- **Read only** (the default): Friday can read and search. Archive, star and
  read state change in Friday's view only, and Gmail-only actions (Trash,
  spam, labels, drafts) are refused with an explanation.
- **Allow sending**: tick it when adding the account, or use **Reconnect with
  sending** on an existing one. This grants Gmail's send permission and the
  permission to change your mailbox (labels, archive, Trash). Friday never
  asks for permission to delete mail permanently.

Tokens are encrypted on this PC. Sign-in always completes on `localhost`.

## What you can do

- Read threads (HTML mail is shown in a sandboxed frame and re-coloured for
  the dark theme; "Show original colours" is one click), open attachments,
  search with Gmail's own operators (`from:`, `is:unread`, `newer_than:7d`, …).
- Archive, star, mark read or unread, snooze, mute, label, and **Delete**,
  which moves the conversation to Gmail's Trash (Gmail keeps it for 30 days).
  Every change can be undone.
- Use Gmail's keyboard shortcuts, hover actions on each row, a right-click
  menu, select-all and bulk actions.
- Save drafts to Gmail Drafts, schedule a send, and undo a send for 10
  seconds after approving it.
- Unsubscribe using the list's own unsubscribe link, after you confirm.
- Triage in 3D: drag conversations onto Trash, snooze and label zones, or deal
  unread mail one at a time with the arrow keys.

## How sending is approved

Friday has no tool that sends mail. When you ask it to write an email, it
creates an **approval card** showing the exact From, To, Subject and full text.
The message goes out only when you approve that card, and only as written:
editing the message afterwards needs a new approval. One approval sends one
message.

- If a recipient or link in the message came from something Friday read (for
  example, an address that appeared only in an incoming email), the card says
  so.
- After you approve, the message waits 10 seconds (or until its scheduled
  time) so you can undo it. A scheduled message that fell due while Friday was
  off is not sent late; Friday asks you again.
- Grants for scheduled jobs never cover email. Every message gets its own card.

The same rule covers mailbox changes that Friday proposes on its own
(deleting, archiving, marking spam, unsubscribing): a change you click happens
at once with undo; a change Friday proposes waits on a card.

See [Approvals and receipts](approvals-and-receipts.md).

## Privacy

Reading your mail happens on this PC. When a cloud model helps with a
message, the egress gate applies to what is sent to it; see
[Privacy](privacy.md). Mail content is treated as read content, never as
instructions to Friday.
