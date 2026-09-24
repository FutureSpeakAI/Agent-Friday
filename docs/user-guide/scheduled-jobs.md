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
and never fall back to a paid cloud model.

- Hourly heartbeat
- Morning news and the evening front page
- Afternoon briefing
- Daily creation

If no local model is running when one of them is due, the run is **skipped**,
with the reason recorded, rather than sent to the cloud. On a cloud-only
install these jobs therefore do not run until you either add a local model
(Settings → Models) or allow the job to use the cloud. There is no switch for
that in Workflows yet: with Friday stopped, set `"local_only": false` in the
job's `task` in `schedules.json`. A job you have edited keeps your setting.

**Daily creation** runs once a day while you are away from the computer: after
10 minutes without activity, between 09:00 and 23:00, and only when the GPU is
not busy with other work. The window is the `idle_work` setting.

A scheduled run may take up to 300 rounds of model calls (a chat turn may take
999). See `turn_budget` in the [configuration reference](configuration.md).

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
