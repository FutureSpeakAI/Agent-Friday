# Notifications, activity and updates

Agent Friday™ tells you things in four places. Each place has one rule, so you
can tell at a glance whether something needs you or only happened.

| Place | What is in it | How it reaches you |
|---|---|---|
| **Needs you** | Only what waits for your decision: approvals, a failure you have to fix, a money or credential problem. | The badge, and a popup for approvals, within the limits below. |
| **Activity** | Every task that is running or waiting, whoever started it. | You open it. The count never pops up. |
| **Updates** | Results and things for your information: a briefing ready, a task done. | You open it. A quiet dot, no sound, no popup. |
| **Receipts & traces** | Why Friday did something, step by step. | You ask. They are never pushed. |

All four are tabs of the **Activity** panel, which opens from the chip in the
top bar (the same chip shows the Local AI queue). When something needs you,
the chip carries an amber badge, for example "1 needs you" or "3 need you",
and it stays in the top bar while anything is waiting. Amber only ever means
that. Every approval card also shows in the chat and in **System >
Approvals**; see [Approvals and receipts](approvals-and-receipts.md).

A new install is quiet. Services you never connected show as "not
connected" in grey, with no warnings at start-up, and until you finish the
setup chat Friday holds the notices from scheduled jobs (the runs are still
recorded).

## Needs you

The first tab lists what is waiting for you, and says how many ("4 approvals
waiting").

- **Approvals of one kind and source are one card.** Only a few kinds batch
  (right now the media-diet and career-tracker changes), and every action is
  shown in full, the same as on its own card. Actions that use details from
  outside content, money, connector actions and anything only a click on screen
  may approve are never batched. **Approve these 3** approves exactly those
  three and nothing else. **Decline these 3** declines exactly those three. **Review each** shows
  the single cards, which are the same approvals.
- **Nothing about the checkpoint changes.** Each action is still its own approval
  underneath, decided through the same checkpoint, once. If another action
  arrives, or one of the three is decided somewhere else, while you look, the
  batch is refused and you are shown the new list instead. A batch never includes
  an approval that spends money or one that only a click on screen can approve.
- **A card that offers a fix says so.** An error card has a button for the fix
  ("Open schedules"). If there is nothing to fix from there, it says so.
- **Why?** on any item says how it reached you, in plain words, and opens its
  trace if it has one.

## Activity

The second tab lists every task, whoever started it: chat, voice, a button in a
workspace, a schedule, background work, or Friday's small on-PC helper
(called Laya in Settings). Each row shows:

- the **model** answering it (or that one will be chosen when it starts);
- its **status**, and its **place in line** while it waits;
- **Trace**, which opens its reasoning trace;
- **Stop** (or **Cancel**, for a job still waiting) only where the job says it
  can be stopped. Stop asks it to stop after its current step and never deletes
  its record. A job that cannot be stopped partway shows no Stop.

Background work on your local model still runs one job at a time, and your chats
and voice still go first. **Run now** skips only the idle wait.

## Updates

The third tab is a digest of what happened today, grouped by kind, newest first.
Nothing here ever interrupts you. Muting a kind from the tray still works, and
approvals can never be muted.

## Receipts & traces

The fourth tab lists recent reasoning traces. You reach one from the item that
made it (**Why?**, **Trace**) or from here, and from the Trust workspace. Traces
and receipts are pulled, never pushed: none of them creates a card or a toast.

## How often Friday may interrupt you

The setup chat asks once, and you can skip it, whether you want quiet hours;
22:00 to 07:00 is offered as the recommended answer and nothing is chosen for you.
**Settings > General > Interruptions** has three choices. Nothing is switched on
for you beyond the defaults.

- **How often something may interrupt you.** The default, and the recommended
  choice, is **6 an hour (Recommended)**. Over the limit, things that need you wait in the badge
  without popping up, and the badge says how many. You can choose 3, 6, 12 an
  hour, or no limit.
- **Quiet hours.** Off until you turn them on. 22:00 to 07:00 is offered as the
  recommended window, or you can choose your own times. In quiet hours only
  safety-critical things interrupt you. Everything else waits in the badge and
  in Updates. An approval held this way keeps the expiry its kind was given: holding
  stops a popup, not time, so a card whose time runs out while it waits expires
  and a notice in Needs you says so. A hold never gives a card an expiry: a kind with no
  expiry stays without one, and a hold that lasts a day ends then, with the card moving into
  Needs you quietly (no popup, no text) and still waiting for you.
  When the hold ends the card pops open and the text-message offer goes out. A
  scheduled job that would ask for approvals and comes due in quiet hours runs
  once when they end, passing the same checks as any run, and its summary says it
  ran late; the job keeps its usual schedule. A one-time job is different: it
  does not run under its old terms, you get an approval card asking whether to
  run it now.
- **Follow Windows Focus Assist.** Off until you choose it. When Windows Do Not
  Disturb is on, only safety-critical things interrupt.

**Safety-critical things always interrupt**, and do not use up the limit: a spend
halt, a credential problem, a budget alert, a change of model seat, and a block
by one of the cLaws.

Quiet hours and the limit only decide whether something *interrupts*. They never
hide it, approve it, or let it past a checkpoint.

A burst of the same kind of notice from the same source within five minutes
becomes one card with a count.

## By voice

- "Quiet until morning" turns quiet on until your quiet hours end (07:00 if you
  have not set any), and confirms aloud. By voice it lasts at most 12 hours, and
  a line in Updates says who turned it on ("Quiet until 07:00, turned on by you
  (voice)"). It needs no yes, because it can only hold things back, and only
  your own spoken words or the button on the page count: if a model asks for
  quiet while working, it gets a card like any other change. Ending quiet,
  muting and clearing still ask first.

Yes or no to a spoken approval works one card at a time. A spoken yes never
approves a whole batch.

## Where this is recorded

Every notice writes one line to the activity record on your PC: its kind, its
place, and whether it interrupted you or waited. It holds counts only, no words
from the notice, and it never leaves your PC.
