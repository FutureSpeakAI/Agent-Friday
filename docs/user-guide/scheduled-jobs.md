# Scheduled jobs

Agent Friday™ runs some work on a schedule: the morning news, the evening front
page, an afternoon briefing, weekly digests, an hourly heartbeat, daily
creation, and anything you schedule yourself. This page says where jobs run,
what they cost, and what they may do without you.

## Where to find them

Open the **Workflows** workspace. Your own workflows are one list: each says
what it does, when it runs ("Every weekday at 7:30 AM"), how its last run went
and when the next one is, with a switch to pause it and buttons to run it now,
edit it or delete it. **Details** shows its steps and its last result. Friday's
built-in jobs are in a separate list below, **Friday's built-in routines**,
where they can be switched on or off.

To make a new one, type what you want and when in your own words ("every
weekday at 8, summarise my unread mail") and choose **Draft it**. Friday writes
a draft: a name, the timing and the steps. Check and edit it; nothing is saved
until you choose **Save workflow**. The timing is read by fixed rules, so the
sentence under **When** is exactly what the schedule will do. A workflow may
have several steps, and each step gets what the one before it found. Leave the
timing as **Only when I run it** for a workflow you start by hand. You can also
ask Friday in chat.

Jobs are stored in `%USERPROFILE%\.friday\schedules.json`, and each run is
recorded in `schedule_runs.jsonl`.

See [Workflows](workflows.md) for project destinations, checked outputs,
change-only notices and the same controls in chat and voice.

## Built-in jobs run on your PC by default

These built-in jobs are set to **local only**: they run on a model on your PC
and never fall back to a paid cloud model on their own.

- Hourly heartbeat
- Morning news, the evening front page, the afternoon briefing and the weekly
  digest and editorial
- Daily creation

When a local model is running, they run there, whatever you chose below.

**Daily creation** runs while you are away from the computer: after 10 minutes
without activity, between 09:00 and 23:00. This window is the `idle_work`
setting.

## Background AI work waits its turn

Work that uses your local model in the background goes through one queue: wiki
notes from voice chats, the built-in scheduled jobs and background tasks. The
rules:

- Jobs run one at a time. A job never starts while you are in a chat or a local
  voice turn, and a job that is running pauses before its next step until the
  turn ends.
- Work that can wait, such as wiki notes and daily creation, starts only after
  the computer has been idle for a set time. Set it in **Settings > Advanced >
  Local AI queue**: "Background AI work waits until you've been away for"
  (5, 10, 15, 30 or 60 minutes; default 10).
- Jobs with a time still run on time: the morning news (7:00), the afternoon
  briefing (16:00) and the evening front page (18:00), and the weekly digest
  and editorial. They run one at a time and behind your chats, but they do not
  wait for idle.
- A second request for work that is already waiting joins it.

While work waits or runs, a **Local AI: N queued** chip appears in the top bar.
It is hidden when the queue is empty. Choose it to see each job, why it waits,
and two buttons: **Run now**, which skips only the idle wait, and **Cancel**.
The tray icon's tooltip shows the same ("Local AI: 3 tasks queued · next: ... ·
waits for idle"). Only labels appear, never what a job contains.

Cloud models are not queued. Cloud voice calls (Gemini Live) do not pause
background local-model work; an open call does keep work that can wait
waiting.

## On a PC with no local model

If no local model is running when one of these jobs is due, the run is
**skipped** with the reason recorded, rather than sent to a paid cloud model
nobody chose. The news jobs wait for the local model and catch up the same day.
Friday keeps one entry in the notifications panel saying the jobs are paused
and where to change that. It is updated in place, so a skip never adds a row or
a failure notice.

You can let the **heartbeat** and **daily creation** use a cloud model instead.
News never uses a cloud model.

- The [setup chat](setup-chat.md) asks once, on a PC with no local model, and
  shows which model each job would use and its estimated monthly cost. The
  answers are Yes, No or Skip, and Skip leaves the question open.
- **Settings > Models > Scheduled jobs without a local model** shows your
  answer, each job's model and its estimated monthly cost, and lets you change
  the answer and how often the heartbeat runs.

When allowed, each run uses only the model you allowed, Claude Haiku 4.5 by
default. A provider that cannot serve that model is refused rather than swapped
for another. In the cloud the heartbeat runs every 4 hours between 08:00 and
20:00, not hourly. If you add a local model later, the jobs move back to it on
their own.

The estimate is worked out from each job's schedule, the size of what each run
sends (Friday's system prompt is about 13,550 tokens and every job sends it),
and the model's published price. It counts every input token at the full rate,
so actual spend is usually lower. Actual spend is metered like any other cloud
call and counts toward your spending limits.

A job you schedule yourself is not covered by this answer. To let one of your
own `local_only` jobs use the cloud, set `"local_only": false` in its `task` in
`schedules.json` with Friday stopped.

## What a job may do on its own

Scheduled jobs run when nobody is watching, so they cannot ask you in chat.

- **Internal work** (reading, searching, drafting, generating) runs.
- **Outward actions** (sending, creating calendar events, publishing,
  installing, commands that change things) wait on an **approval card**, unless
  you gave that job a **grant**.

Create a grant in **Settings > Privacy & Data > Scheduled jobs: what they may
do on their own**: choose the job, tick the actions, and set an expiry and a
number of uses. A grant is tied to that job's own runs and cannot be used by
anything else. Email is never covered by a grant; each message gets its own
card. A grant does not reach the steps of a workflow saved on the Workflows
screen: their outward actions always wait on a card, even when a grant names
that workflow's schedule. See
[Approvals and receipts](approvals-and-receipts.md).

The Workflows screen marks a workflow whose steps look likely to ask ("Asks you
before it can send email") and shows a button when cards are waiting. The mark
is a guess from the wording of the steps; the approval card is what actually
holds the action.

## Cost

Local jobs cost nothing in API fees. A job you allow to use the cloud is
metered like any other cloud call and counts toward your spending caps in
**Settings > Spending**.

## Weekly update check

The update check is also a scheduled job, and it is off until you turn it on at
first run or in **Settings > About**. See
[Background network activity](background-network.md).
