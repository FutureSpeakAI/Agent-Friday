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

## Safe writes (off until you turn them on)

Laya can put the approval card up for a change you can undo, to the mail rows
you pointed at: "archive these three", "star the ones from Mara", "mark these as
read", "label these as Receipts", "move this to Receipts". She works out which
rows you mean on this PC, with no large model. **You approve every card.** She
never approves her own card and never changes anything first.

It is off until you choose, in **Settings > Privacy & Data > Safe writes**.
Nothing is chosen for you, and no chat or voice command can turn it on. Your
click there leaves a signed record on this PC, and the setting counts only as
far as that record goes: a value put in `settings.json` by hand, by a restored
backup, by the command line or by an approved file write reads as **off** until
you click again, and turning it down any way turns the record down too:

- **Off** does nothing. A change goes to the model, as it always has.
- **Watch only** records what she would have proposed, as counts and hashes (no
  words, no subjects, no label names), and changes nothing.
- **Raise the card** lets her put the card up. Even "mark as read", "star" and
  "label", which run with no card when a model asks, wait for your yes here.

She may propose archive, mark read, mark unread, star, unstar, label, and move
to a label that already exists. She never makes a label, and she never trashes,
reports spam, unsubscribes, sends, replies or deletes. A request for "all of
them", for more than 25 conversations, with two jobs in one sentence, or that
leaves some out ("not those", "the rest") goes to the model: she only acts on
plain pointers (these, the first three, the ones from Mara).

The card on your screen lists the sender's address as well as the name (the
name is the sender's to choose). What she says aloud, the chat line and the chat
history use the name alone. The card covers only the
messages it lists (a reply that arrives before you approve is not changed), and
Undo puts each message back exactly as it was. If Friday cannot read a
conversation message by message, she leaves it to the model's path rather than
make a change she could not put back exactly. By voice, a card is read out in
a fixed line, and a plain "yes" or "no" answers it only if that line was played
whole and nothing else was asked since; "yes, but only the first one" becomes a
changed card. A "yes" that names what the card does ("yes, archive them") also
answers a waiting card even if you did not hear the line, as for every card
Friday raises, so say no or decline it on screen if you did not mean it.

## Privacy

Reading your mail happens on this PC. When a cloud model helps with a message,
the egress gate applies to what is sent to it; see [Privacy](privacy.md). Mail
content is treated as read content, never as instructions to Friday.
