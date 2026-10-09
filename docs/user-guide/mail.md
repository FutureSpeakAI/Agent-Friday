# Mail

The **Messages** workspace is a Gmail client with Agent Friday™ alongside.
Friday can read, search, sort and draft your mail. It sends a message only
after you approve that exact message on a card.

## Connecting Gmail

Open **Settings > Connections > Google** and add an account. Google's own
sign-in page opens in your browser, and the permissions Friday asks for are
listed before you continue. You can connect more than one account.

- **Read only** (the default): Friday can read and search. Archive, star and
  read state change in Friday's view only. Gmail-only actions (Trash, spam,
  importance and labels) are refused with an explanation.
- **Let Friday send mail from this account**: tick it before you add the
  account, or use **Reconnect with sending** on an existing one. This grants
  Gmail's send permission and the permission to change your mailbox (labels,
  archive, Trash). Google decides permissions at its consent screen, so sending
  cannot be switched on later without reconnecting. Friday never asks for
  permission to delete mail permanently.

Tokens are encrypted on this PC. Sign-in always completes on `localhost`.

## What you can do

- Read threads, open attachments, view the original source, print, and search
  with Gmail's own operators (`from:`, `is:unread`, `newer_than:7d` and so on).
  HTML mail opens in a sandboxed frame with no scripts and remote images off
  until you ask for them. It is re-coloured for the dark theme, and **Show
  original colours** shows a sender's mail as it was sent and remembers that
  choice for the sender.
- Archive, star, mark read or unread, mark important, snooze, mute, label, and
  **Delete**, which moves the conversation to Gmail's Trash (Gmail keeps it for
  30 days). Your own clicks happen at once and can be undone (press Z).
  Nothing here deletes mail permanently. Snooze and mute are Friday's own and
  do not change Gmail.
- Use Gmail's keyboard shortcuts, hover actions on each row, a right-click
  menu, select-all and bulk actions.
- Save drafts to Gmail Drafts, schedule a send, and take a send back for 10
  seconds after approving it.
- Unsubscribe using the list's own unsubscribe link, after you confirm. Friday
  asks first because unsubscribing tells the sender your address is read.
- Triage in 3D: drag conversations onto the Trash, snooze and label zones.

## How sending is approved

Friday has no tool that sends mail. Reply, reply all, forward and compose each
create an **approval card** showing the exact From, To, Subject and full text.
The message goes out only when you approve that card, and only as written:
editing the message afterwards needs a new approval. One approval sends one
message, and only from an account that granted sending.

- If a recipient or link in the message came from something Friday read (for
  example, an address that appeared only in an incoming email), the card says
  so.
- After you approve, the message waits 10 seconds (or until its scheduled
  time) so you can choose **Undo send**. A scheduled message that fell due
  while Friday was off, and is more than a short time late, is not sent. Friday
  tells you and asks again.
- Grants for scheduled jobs never cover email. Every message gets its own card.

The same rule covers mailbox changes that Friday proposes on its own (deleting,
archiving, marking spam, unsubscribing). A change you click happens at once with
undo. A change Friday proposes waits on a card.

See [Approvals and receipts](approvals-and-receipts.md).

## Privacy

Reading your mail happens on this PC. When a cloud model helps with a message,
the egress gate applies to what is sent to it; see [Privacy](privacy.md). Mail
content is treated as read content, never as instructions to Friday.
