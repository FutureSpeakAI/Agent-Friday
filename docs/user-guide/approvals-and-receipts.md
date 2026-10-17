# Approvals and receipts

Agent Friday™ does work on its own, but it does not act on the world for you
without your say-so. This page explains what it asks about, how you answer, and the
record it keeps.

## What Friday asks about

Every action Friday takes, from any surface (chat, voice, a scheduled job, a
background task, a text message), passes one checkpoint first.

- **Internal work runs without asking.** Reading, searching, drafting,
  summarising, generating images, and writing inside Friday's own output
  folders.
- **Outward actions wait for you.** Anything that leaves your PC or changes
  something you own: sending an email or a text, creating or changing a
  calendar event, scheduling a social post, installing software, overwriting a
  document Friday did not make, running a command that is not on a short
  read-only list, and any connector action that is not a plain read.
- **Unknown means outward.** A tool the checkpoint does not recognise is
  treated as outward.
- **Reads at services you connected usually run without a card.** A plain
  read at a connected service, such as a lookup in a project tracker, runs
  without a card and is still receipted. It becomes a card when its words say
  send, pay or delete, when a detail came from content Friday read, or when
  its arguments would carry private data out. The setting `outward_reads` set
  to `card` makes every such read ask.
- **Some things are refused outright.** For example, a command that tries to
  call Friday's own local API.

If anything in the checkpoint fails (for example, the record of the decision
cannot be written), outward actions are held and reading keeps working.

## Approval cards

Every confirmation is an **approval card**, from the first time Friday asks.
Each card shows exactly what will happen: for an email, the From, To, Subject
and full text. Choose **Approve** or **Deny** (on an email card, **Send** or
**Don't send**). An approved card lets that one action through once, with
those exact details. Cards expire after 24 hours by default.

### Where cards show

The same card shows in several places at once, and one decision answers it
everywhere:

- in the chat, as a card under Friday's message;
- in the **Activity** panel, on the **Needs you** tab. The Activity button
  stays in the top bar with a badge ("1 needs you", "3 need you") while
  anything is waiting;
- in a popup on every page, whatever you have open. **Later** folds the popup
  away on that page until the next card arrives; the card keeps waiting. In a
  workspace opened in its own browser tab, it folds to a "waiting for you"
  button in the header;
- in **System > Approvals**.

### Answering by typing

On your own Friday page you can also type an answer in the chat:

- A whole "yes" approves the one card it answers, once.
- A bare "no" declines only that card.
- "No, I meant ..." cancels only the question in the chat and leaves the
  card alone, so you can correct what you asked for.

A card that the small on-PC helper raised for a change to your mail is the
exception: a typed "yes" does not approve it; use its **Approve** button. See
[Mail](mail.md).

### Answering by voice

By voice, Friday reads the card out and a plain "yes" or "no" answers it.
When there are other people in the room, a spoken approval has to say
Friday's name ("Friday, send it"), so a "yeah" from across the room does not
count. You choose this in the setup chat (**Require my name** or **Anyone in
the room may approve**); skipping keeps the name required. Voice never types
a key or a password for you. See [Voice](voice.md).

### Answering by text message

If you turned on **Approve by text** for the [phone](phone.md), Friday also
texts you a one-time code; reply `YES <code>` or `NO <code>`. A bare "yes"
does nothing.

### Cards you may meet

- **Send an email.** Shows the whole message. See [Mail](mail.md).
- **Exactly what would be sent.** An email written on a cloud model that
  contains private details shows "this is exactly what would be sent", with
  **Send** or **Don't send**.
- **Money paragraphs.** With **Settings > Privacy & Data > Money paragraphs in
  email** set to ask, paragraphs about money are shown before they go to a
  cloud model: **Send** or **Keep on this PC**. You can tick **Remember for
  this thread** or **Remember for this sender**. By voice, a Send counts only
  after Friday has read the paragraphs back to you (within 10 minutes). There
  is no card when the text stays on the local model.
- **Switch this chat to ...?** When a private record cannot go to the chat's
  cloud model, Friday can offer to switch the chat's model, or to hand just
  that one task to the local model on this PC. Nothing changes until you
  approve. A model marked as cloud is never offered as "local".
- **A raw record.** A whole mail archive or a bank statement reaches a cloud
  model only after a yes for that one file, under every privacy setting. See
  [Privacy](privacy.md).

### When a detail came from something Friday read

Emails, web pages and documents can contain instructions written by someone
else. Friday records where every value came from. If a recipient, link,
account number, file path, command or memory note came from content it read,
the card says so, for example: "The recipient came from an email from
someone@example.com, not from you." A quick yes in chat does not satisfy that
card; you decide on the card itself.

## Grants, for scheduled jobs

A scheduled job runs when nobody is watching, so it cannot ask in chat. To let
a job take an outward action on its own, create a grant in
**Settings > Privacy & Data > Scheduled jobs: what they may do on their
own**:

1. Pick the scheduled job.
2. Tick the actions it may take.
3. Choose how long the grant lasts (1, 7 or 30 days) and how many times it may
   be used.

A grant belongs to that one job and nothing else. It stops at its expiry or
when its uses run out, whichever comes first, and you can revoke it at any
time from the same screen. Emails are not offered here. A grant never covers
an action whose details came from content Friday read.

Without a grant, a scheduled job's outward action waits on a card.

## Pre-approved sends, for email

Email has its own standing yes: **Settings > Privacy & Data > Pre-approved
sends**. You name the addresses (or one `@domain`; free-mail domains such as
`@gmail.com` are refused) and choose **For any job or chat** or **Only for one
scheduled job**. A send still gets its card when a recipient is not named,
when the work read outside content, when you asked by voice (unless **Voice
may use pre-approvals** is on; it is off by default), when the message looks
personal or sensitive, or when it was written on a cloud model with private
details. Editing the job puts the pre-approval out of use until you choose
**Approve again as it is now**. Every pre-approved send tells you at once with
**Undo**, and **Revoke** (in Settings or on **System > Approvals**) takes it
back along with anything still waiting under it. The full rules are on the
[Mail](mail.md#pre-approved-sends) page.

## A claim needs a receipt

When Friday says she did something outward ("sent", "moved", "deleted"), that
claim has to be backed by a receipt from the same turn. If there is no
receipt, she does not say it was done.

## The optional second opinion

Deciding what counts as "ambiguous" starts with keyword rules. An optional
local model (Laya) can add a card the rules would have missed; it can only add
a card, never remove one. **Settings > Privacy & Data > What needs your
sign-off** offers Off, Shadow and On. Shadow is the default: Laya scores
decisions alongside the keyword rules, both answers are logged, and no
decision changes. On lets either one raise a card.

Laya runs on the CPU and loads in the background after Friday starts (about a
minute from a cold start). Nothing waits for it: until it is ready, and
whenever it takes longer than a couple of seconds to answer (as it can on a
laptop), the keyword rules decide alone. The status line in that Settings
section says which is happening and how often Laya was too slow.

## Receipts

Every decision the checkpoint makes is appended to a receipt file:

```
%USERPROFILE%\.friday\decision-bom.jsonl
```

Each line is one JSON record: the action, how it was classified, the decision
and why, and a UTC timestamp, signed with Friday's governance key (HMAC-SHA256).
Open it with any text editor. A receipt is written before an outward action
runs; if it cannot be written, the action does not run.

Other records you may want:

| File | What it records |
|---|---|
| `.friday\vault\decision-bom.jsonl` | Older receipts only. Current ones, including privilege-ring checks, go to `.friday\decision-bom.jsonl` |
| `.friday\decisions.jsonl` | What the approval scanner decided for ambiguous actions |
| `.friday\vault\egress-log.jsonl` | What the egress gate withheld or redacted from cloud calls |
| `.friday\traces\ledger.jsonl` | Reasoning traces (encrypted; open them from the Ledger) |

You can read them in the app too. In **System**, under the approvals, the
**What Friday did** card lists every action the checkpoint decided on, from
the signed receipts, and says if a signature does not check out. The card
below it lists the changes Friday made to your mail, files and notes, each
with **Undo** where that is still possible. The **Activity** panel's
**Receipts & traces** tab shows recent traces.

## Computer control

Mouse and keyboard control is off by default. It needs two things in
**Settings > Privacy & Data > Computer control**: the **Allow computer
control** switch, and a grant that names the apps Friday may control. The
grant lasts across restarts until you revoke it.
