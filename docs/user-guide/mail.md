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
you click again, and turning it down any way turns the record down too: Friday
checks the file against your record when she starts and again the first time she
reads the setting after that, and if the record cannot be turned down it is
removed and the setting reads as off. Turning the setting down also expires every
card Laya raised that was still waiting:

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
name is the sender's to choose), for the messages it lists rather than the first
one (up to three senders per conversation). The chat line, the notification and
the saved conversation, typed or spoken, name no sender and no subject: they say
that a card was raised and how many conversations it covers, and the card on
your screen has the rest. The same neutral line goes into the end-of-call summary
of a voice call. What Friday says aloud, outside a room, does name the subjects
and the senders' names (never the addresses), because you are there to hear it;
in a room she gives the count only. The card covers only the messages it lists. A
message that arrived after your screen last showed the list is left out and the
card says so ("1 newer message not included"); a reply that arrives after the
card is raised is not changed either. Undo puts back, message by message, the
labels Friday changed; a label that a Gmail filter or you changed in between is
not restored. If Friday cannot read a conversation message by message she
refuses it: no card is raised for it and nothing is handed to the model, because
she will not make a change she could not put back exactly.

**How you approve it.** On the page, a card Laya raised is approved with its
**Approve** button; a typed "yes", "ok" or "fine" does not approve it (typing
"no" can decline it). A text message does not approve it either, even with
Approve by text on: Laya's cards are not texted to you, and a reply of YES with a
code is refused (a NO still declines). By voice, the card is read out in a fixed line, and a plain
"yes" or "no" answers it only if that line was played whole and nothing else was
asked since; "yes, but only the first one" becomes a changed card. For a card
Laya raised, nothing else substitutes: a "yes" that names the action ("yes,
archive them") approves it only if Friday actually read that card's line aloud in
this conversation, and a line read in another conversation, or cut off, does not
count. (For the cards a model raises, naming the action still answers an unheard
card, as before.)

When Friday has put her own numbers on rows, "the second one", and "these" when it
would mean the row you have open or a different number of rows than she
numbered, could mean either, so Laya asks you to say it again with a number
counted from the top of your list, or to tick the rows, instead of guessing. With
no numbers on screen she counts from the top of your list and the card says so.

## Changes Friday makes without a card (a rule you set)

When a model asks, star, unstar, label (and take a label off), mark read and
mark unread run at once with no card. That is your rule for changes that are
instantly undoable and take nothing out of your inbox; archive, move to a label,
Trash, Spam and restore always raise one card per batch. Each card-free change is
still recorded: the receipt lists the tool, the action, the count and the message
ids (never a subject) for that turn, and the change appears under Undo, which puts
back exactly the labels it changed, message by message. A test pins that list so
no other change can join it without being noticed.

## Privacy

Reading your mail happens on this PC. When a cloud model helps with a message,
the egress gate applies to what is sent to it; see [Privacy](privacy.md). Mail
content is treated as read content, never as instructions to Friday.
