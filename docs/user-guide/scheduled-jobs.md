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

## How often

The **How often** list in the editor has these choices:

- **Only when I run it** (nothing runs by itself)
- **Every day**, at a time
- **Every weekday (Mon–Fri)**, at a time
- **On certain days**, at a time
- **Every few hours**
- **Every month**, on a day from **Day 1** to **Day 31**, or **Last day**
- **Once**, on a date and time

Times are your local time and stay the same when the clocks change for
daylight saving.

## Which model a job uses

A job either follows your normal routing or is pinned to one model. The card
in Workflows says which: "Model: ..." (pinned: every run uses this model, and
is held if it is unavailable) or "Model (not pinned): ...".

A pinned job never moves to another model. If that model cannot answer when
the job is due, the run is **held**, and the card can show "Waiting for its
model to come back, then it runs once." What happens to a held run is set in
**Settings > General > Scheduled jobs**:

- **A run held because its pinned model was unavailable**: **Run it once when
  the model is back (recommended)** or **Wait for its next scheduled time**.
- **A run missed while Friday was closed**: **Run it once at the next start
  (recommended)** or **Skip it until its next time**.

These are defaults for your jobs. Each job's card in Workflows has its own
**Missed while closed** and (for a pinned job) **Held by its model** lists.
Their first choice, **follow my default (now: ...)**, puts the job back on
the setting above and says what it currently is.

## Jobs that write email

Each job's card has **Its emails:** with two choices:

- **save as drafts in Gmail** (what a new job does)
- **send (asks first unless pre-approved)**

A saved workflow that you run by hand has the same switch on its card. With
**send**, each message still waits on an approval card unless a
[pre-approved send](mail.md#pre-approved-sends) covers it.

When drafting became the default, jobs that were already made to send email
were kept sending, once, so nothing you relied on stopped. If you had any,
one notice in Activity lists them: **Your email jobs keep sending**. You can
switch any of them to drafts on its card.

## Friday's built-in routines

Under **Friday's built-in routines** in Workflows you can switch each built-in
job on or off. One of them is **Calendar reminders**, which checks every 2
minutes for reminders that are due (see [Calendar](calendar.md#reminders-and-prep-cards)).
The routines offered in the setup chat (morning and evening front page,
afternoon briefing, weekly editorial, hourly heartbeat, podcast) start
unticked; nothing runs until you choose it.

Until you finish the setup chat, jobs still run and are still recorded, but
Friday holds their notices so a new install stays quiet.

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

That holds in every routing mode but one. If you **chose Cloud only** (in
Settings or during setup), that choice is your consent for the News routines
(front pages, briefings, the weekly digest and editorial), podcast scripts and
the heartbeat to use the cloud: they run on the scheduled-jobs cloud model
below (Claude Haiku 4.5 by default), whether or not a local model is running,
and the heartbeat keeps its cloud cadence. Daily creation is not part of that
consent; it follows your answer below. A podcast made from your own private
material is still written on this PC, and the Front Page read-aloud still uses
this PC's voice. To keep these jobs local even in Cloud only, set
`scheduled_cloud.routine_follows_cloud_mode` to `false`.

Cloud only is also what a new install starts on. That starting value is not a
choice: until you pick a routing mode yourself (picking Cloud only again
counts), these jobs follow the other rows of the table. Cloud preferred is not
consent either.

| Routing mode | Local model running | You allowed a cloud model (heartbeat, daily creation) | Where they run |
|---|---|---|---|
| Cloud only, chosen by you (News, podcasts, heartbeat) | either | either | the cloud model |
| any other case | yes | either | this PC |
| any other case | no | yes | the cloud model |
| any other case | no | no | skipped (News waits and catches up) |

A job allowed onto the cloud with no cloud model set for it is skipped, and
the skip says so.

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
- Every call to a local model waits its turn, in this order: a voice turn, a
  chat, a Local API request, a job you asked for (a button in a workspace, a
  tool inside your own chat), then background work. Inside a class it is first
  come, first served. A job you asked for never waits for the chat it was
  started from, and never for idle.
- A background call that nothing claims, such as a summary written after a chat,
  joins the queue as a job that can wait. It shows in the chip, and in
  Activity with a card that names the model.
- Jobs take their model together. A model that is already loaded finishes its
  waiting jobs before anything asks the machine to load another, and when
  another one is loaded, all of its waiting jobs go together.
- A model does not load if it would leave less than 4 GB of memory free
  (`local_scheduler_ram_floor_gb`). The job waits, and the chip says why.
- A second GPU job (an image, a heavy model) that finds the card in use waits its
  turn instead of being refused, for up to two minutes. A voice call waits 20
  seconds, and it stands the card down for work of a lower class.

While work waits or runs, a **Local AI: N queued** chip appears in the top bar.
It is the one place this shows. It is hidden when the queue is empty. Choose it
to see each job, why it waits, and two buttons: **Run now**, which skips only
the idle wait, and **Cancel**. The tray icon's tooltip shows the same ("Local
AI: 3 tasks queued · next: ... · waits for idle"). Only labels appear, never what
a job contains. The scheduler also keeps a few numbers on this PC and sends none
of them anywhere: model swaps and the lowest free memory each hour, how long
each class waited, and how many background jobs finished
(`GET /api/work/local/metrics`).

Cloud models are not queued. A local voice call keeps work that can wait
waiting until the call ends. Cloud voice calls (Gemini Live) do not pause
background local-model work: during one, work that can wait still waits
while you use the keyboard or mouse, but talking alone does not count.

## On a PC with no local model

If no local model is running when one of these jobs is due, the run is
**skipped** with the reason recorded, rather than sent to a paid cloud model
nobody chose. The news jobs wait for the local model and catch up the same day.
Friday keeps one entry in the notifications panel saying the jobs are paused
and where to change that. It is updated in place, so a skip never adds a row or
a failure notice.

You can let the **heartbeat** and **daily creation** use a cloud model instead.
In a local routing mode News never uses a cloud model.

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
anything else. Email is never covered by these grants; an email send gets
its own card unless you made a
[pre-approved send](mail.md#pre-approved-sends) for it. A grant does not reach the steps of a workflow saved on the Workflows
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
