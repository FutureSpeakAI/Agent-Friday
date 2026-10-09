# Approvals and receipts

Agent Friday™ does work on its own, but it does not act on the world for you
without your say-so. This page explains what it asks about, the three ways you answer,
and the record it keeps.

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

## Three ways you answer

### 1. A yes in chat

When you are in a conversation and Friday wants to do something outward, it
asks a plain yes/no question in the chat and stops. Your yes covers **that
exact action with those exact details**, and nothing else. If Friday asks the
same thing again, the question moves to an approval card instead of looping.

### 2. An approval card

Friday raises a card when nobody is chatting (a background task or a
scheduled job without a grant), when the action is an email, and whenever a
detail came from something Friday read rather than from you.

Cards appear in a panel at the top right of the main window, in **System >
Approvals**, and, in a workspace opened in its own browser tab, behind a
button in the header that reads "N waiting for you". Each card shows exactly what will
happen: for an email, the From, To, Subject and full text. Choose **Approve**
or **Deny**. An approved card lets that one action through once. Cards expire
after 24 hours by default.

If you turned on **Approve by text** for the [phone](phone.md), Friday also
texts you a one-time code; reply `YES <code>` or `NO <code>`. A bare "yes"
does nothing.

### When a detail came from something Friday read

Emails, web pages and documents can contain instructions written by someone
else. Friday records where every value came from. If a recipient, link,
account number, file path, command or memory note came from content it read,
the card says so, for example: "The recipient came from an email from
someone@example.com, not from you." A quick yes in chat does not satisfy that
card; you decide on the card itself.

### 3. A grant, for scheduled jobs

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
time from the same screen. Emails are not offered: every message keeps its own
card. A grant never covers an action whose details came from content Friday
read.

Without a grant, a scheduled job's outward action waits on a card.

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

There is no receipt viewer in the app yet; see
[KNOWN_ISSUES.md](../../KNOWN_ISSUES.md).

## Computer control

Mouse and keyboard control is off by default. It needs two things in
**Settings > Privacy & Data > Computer control**: the **Allow computer
control** switch, and a grant that names the apps Friday may control. The
grant lasts across restarts until you revoke it.
