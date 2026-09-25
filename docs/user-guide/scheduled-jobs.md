# Scheduled jobs

Friday runs some work on a schedule: briefings, the news front page, an hourly
heartbeat, daily creation, and anything you schedule yourself. This page says
where jobs run, what they cost, and what they may do without you.

## Where to find them

Open the **Workflows** workspace. Each job shows its schedule, its last run
and whether it is on, with buttons to run it now, pause it or delete it. You
can create your own jobs there, or ask Friday in chat ("every weekday at 8,
summarise my unread mail").

Jobs are stored in `%USERPROFILE%\.friday\schedules.json`, and each run is
recorded in `schedule_runs.jsonl`.

## Built-in jobs run on your PC by default

These built-in jobs are set to **local only**: they run on a model on your PC
and never fall back to a paid cloud model on their own.

- Hourly heartbeat
- Morning news and the evening front page
- Afternoon briefing
- Daily creation

When a local model is running, they always run there, whatever you chose
below.

**Daily creation** runs once a day while you are away from the computer: after
10 minutes without activity, between 09:00 and 23:00, and only when the GPU is
not busy with other work. The window is the `idle_work` setting.

A scheduled run may take up to 300 rounds of model calls (a chat turn may take
999). See `turn_budget` in the [configuration reference](configuration.md).

## On a PC with no local model

If no local model is running when one of these jobs is due, the run is
**skipped** with the reason recorded, rather than sent to a paid cloud model
nobody chose. Friday keeps one entry in the notifications panel saying the jobs
are paused and where to change that. It is updated in place, so an hourly skip
never adds a row or a failure notice.

You can let them use a cloud model instead:

- The **setup chat** asks once, on a PC with no local model, and shows which
  model each job would use and the estimated monthly cost. Yes, No or Skip;
  Skip leaves the question open.
- **Settings → Spending → Scheduled jobs without a local model** shows your
  answer, each job's model and its estimated monthly cost, and lets you
  change the answer and how often the heartbeat runs.

When allowed, each run uses only the model you allowed: Claude Haiku 4.5 by
default, for the heartbeat and for the other four jobs. If a job asks for a
different model, the run uses the allowed one instead. A provider that cannot
serve that model is refused rather than swapped for another. In the cloud the
heartbeat runs every 4 hours between 08:00 and 20:00, not hourly. If you add a
local model later, the jobs move back to it on their own.

The estimate is roughly $9 a month with the defaults, about half of it the
heartbeat. It is worked out from each job's schedule, the size of what each
run sends (Friday's system prompt is about 13,550 tokens and every job sends
it), and the model's published price. It counts every input token at the full
rate, so actual spend is usually lower. Actual spend is metered like any
other cloud call, shown in Settings → Spending, and counts toward your
spending limits.

A job you schedule yourself is not covered by this answer. To let one of your
own `local_only` jobs use the cloud, set `"local_only": false` in its `task`
in `schedules.json` with Friday stopped.

## What a job may do on its own

Scheduled jobs run when nobody is watching, so they cannot ask you in chat.

- **Internal work** (reading, searching, drafting, generating) runs.
- **Outward actions** (sending, creating calendar events, publishing,
  installing, commands that change things) wait on an **approval card**,
  unless you gave that job a **grant**.

Create a grant in **Settings → Privacy & Approvals → Scheduled jobs: what they
may do on their own**: choose the job, tick the actions, and set an expiry
(1, 7 or 30 days) and a number of uses. A grant is tied to that job's own runs
and cannot be used by anything else. Email is never covered by a grant; each
message gets its own card. See [Approvals and receipts](approvals-and-receipts.md).

## Cost

Local jobs cost nothing in API fees. A job you allow to use the cloud is
metered like any other cloud call and counts toward your spending caps in
**Settings → Spending**.

## Weekly update check

If you said yes to it at first run, the update check is also a scheduled job.
Turn it on or off in **Settings → About**. See
[Background network activity](background-network.md).
