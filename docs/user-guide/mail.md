# Mail

The **Messages** workspace is a Gmail client with Agent Friday™ alongside.
Friday can read, search, sort and draft your mail. A draft goes straight into
your Gmail Drafts with no card. A message is sent only when you ask Friday to
send it, and then it waits on an approval card unless you pre-approved that
send yourself (see [Pre-approved sends](#pre-approved-sends)).

## Connecting Gmail

Open **Settings > Connections > Google accounts** and add an account. Google's own
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
- Ask Friday to read a whole email conversation, typed or by voice ("read me
  the whole thread with Dana"). She reads every message in it, leaves out the
  quoted text each reply repeats, and can include a short excerpt of a small
  attachment.
- Unsubscribe using the list's own unsubscribe link, after you confirm. Friday
  asks first because unsubscribing tells the sender your address is read.
- Triage in 3D: drag conversations onto the Trash, snooze and label zones.

## Drafts

When you ask Friday to write an email, she saves it in your Gmail Drafts.
There is no card for a draft, because nothing is sent. She tells you where it
went, for example: "Saved as a draft in dana@example.com's Gmail Drafts.
Nothing was sent. Open it: (link)". By voice she says the short version.

Which account gets the draft:

1. The account you named ("draft it from my work account").
2. For a reply, the account the conversation is in. The draft is a proper
   reply in that conversation, with the subject and threading Gmail expects.
3. Otherwise the account in **Settings > Connections > Google accounts >
   Default account for drafts**. It starts on **Ask me**.
4. With **Ask me** and more than one account, Friday asks which one. She never
   guesses.

Saving a draft needs permission to change your mailbox. If an account does
not have it, Friday says the draft was **not** saved, names the account, and
points you to **Reconnect with sending** on that account.

## How sending is approved

Sending is a separate request: you ask Friday to send. Reply, reply all,
forward and compose each create an **approval card** showing the exact From,
To, Subject and full text. The message goes out only when you approve that
card, and only as written: editing the message afterwards needs a new
approval. One approval sends one message, and only from an account that
granted sending. A reply is sent in its conversation.

- If a recipient or link in the message came from something Friday read (for
  example, an address that appeared only in an incoming email), the card says
  so.
- If the message was written by a cloud model and contains private details,
  the card shows "exactly what would be sent", with **Send** or **Don't send**.
- If a paragraph is about money, and **Settings > Privacy & Data > Money
  paragraphs in email** is set to ask, a card shows those paragraphs before
  they go to a cloud model (**Send** or **Keep on this PC**). There is no card
  when the text stays on the local model.
- After you approve, the message waits 10 seconds (or until its scheduled
  time) so you can choose **Undo send**. A scheduled message that fell due
  while Friday was off, and is more than a short time late, is not sent. Friday
  tells you and asks again.
- The grants in **Settings > Privacy & Data > Scheduled jobs: what they may do
  on their own** never cover email. Only a pre-approved send (below) lets a
  message go without its own card.

The same rule covers mailbox changes that Friday proposes on its own (deleting,
archiving, marking spam, unsubscribing). A change you click happens at once with
undo. A change Friday proposes waits on a card.

See [Approvals and receipts](approvals-and-receipts.md).

## Pre-approved sends

Some email should go without a click, such as a weekly report to a colleague.
You can allow that in **Settings > Privacy & Data > Pre-approved sends**.

- **Who.** Name each address, or everyone at one domain (`@example.org`).
  There is no "anyone". A free-mail domain such as `@gmail.com` or
  `@outlook.com` is refused, and so is a shared ending such as `@co.uk`,
  because anyone can get an address there.
- **For which work.** **For any job or chat**, or **Only for one scheduled
  job**. A skill that declares who it sends to shows **Approve what it
  needs**; the declaration alone grants nothing.
- **When it still asks.** A pre-approval is skipped, and you get the normal
  card, when: a recipient is not named in it; the work read outside content
  (an email, a web page, recalled memory); the send was asked for by voice;
  the message looks personal or sensitive; or it was written by a cloud model
  and contains private details.
- **Voice.** **Voice may use pre-approvals** is off by default. While it is
  off, a send you ask for by voice always gets its card.
- **Edits.** Editing a pre-approved job (or a skill's recipient list) puts the
  pre-approval out of use: "Not in use: ... Sends ask until you approve it
  again." Press **Approve again as it is now** to renew it.
- **Undo.** Every pre-approved send tells you at once, with **Undo**. A send
  from a scheduled job or background task waits 120 seconds before it goes
  (you can set 10 to 600 seconds with the `unattended_send_undo_s` setting; see
  [Configuration](configuration.md)). A send you asked for in person keeps the
  usual 10 seconds.
- **Revoke.** **Revoke** (here, or on **System > Approvals**) takes the
  pre-approval back, including sends still waiting under it. Every grant, use
  and revoke is in the log on that page.

## Clean up your inbox

**Messages > Clean up** (also offered in the setup chat) finds mailing lists
you never open, so you can leave them.

- It reads message headers from a connected Gmail account, or from a Google
  Takeout `.mbox` file you choose. The scan is a visible job you can watch,
  and if you close Friday it continues where it stopped.
- It says what it saw ("you haven't opened any of the last 14"), never "you
  never read these".
- **One click** is offered only where the sender's own domain vouches for the
  unsubscribe link (the message is signed by the sender's own organisation
  and supports one-click unsubscribe). Nothing is ticked for you; **Tick all**
  is there if you want it. Other senders are listed under **These need you**
  with their own page or email address, and some under **Probably spam**.
- 14 days after you ask, Friday checks whether the sender is still sending
  and shows it under **So far** ("Confirmed: stopped sending" or "Asked, not
  checked yet").

## Safe writes (off until you turn them on)

Friday's small on-PC helper (called Laya in Settings) can put the approval
card up for a change you can undo, to the mail rows you pointed at: "archive
these three", "star the ones from Dana", "mark these as read", "label these
as Receipts", "move this to Receipts". She works out which rows you mean on
this PC, with no large model. **You approve every card.** She
never approves her own card and never changes anything first.

It is off until you choose, in **Settings > Laya > Safe writes**.
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
to a label that already exists, named in full as the last words you said (a name
she could only read with its symbols or a word changed, or with words after it,
goes to the model). She never makes a label, and she never trashes,
reports spam, unsubscribes, sends, replies or deletes. A request for "all of
them", for more than 25 conversations, with two jobs in one sentence, or that
leaves some out ("not those", "the rest") goes to the model: she only acts on
plain pointers (these, the first three, the ones from Dana).

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
"These" that would mean only one row is asked too. With her numbers on screen
she asks as above; with none, say "this one" if that is what you mean, or tick
the rows you want.

## Changes Friday makes without a card (a rule you set)

When a model asks, star, unstar, label (and take a label off), mark read and
mark unread run at once with no card. That is your rule for changes that are
instantly undoable and take nothing out of your inbox; archive, move to a label,
Trash, Spam and restore always raise one card per batch. A test pins that list so
no other change can join it without being noticed.

Each card-free change is recorded: its receipt lists the tool, the action, the
count and the message ids (never a subject), and it appears in the Undo list on
every path. It also appears in a turn receipt: for a typed chat turn, that turn's
receipt; for a spoken turn on the local voice, the receipt named `voice-…` (its id
is in that turn's route frame); for a Gemini Live tool call, a receipt of its own
named `live-…`; for a delegated or background task, `task-<id>`.

Undo puts back exactly the labels the change added or took off, message by
message, and the receipt says whether that is still possible. To keep that true,
a conversation Friday cannot read message by message is refused: it is not
changed, and the reply says so. When "label these as X" names a label that does
not exist, Friday creates it; Undo then removes the new label too if nothing is
left in it, and if it still holds mail it stays and Undo says so.

## Privacy

Friday fetches your mail from Google to this PC. Whether a cloud model may see
it follows the privacy choice you made in the setup chat. When a cloud model
helps with a message, your never-send list and the other checks apply to what
is sent to it; see [Privacy](privacy.md). Mail
content is treated as read content, never as instructions to Friday.
