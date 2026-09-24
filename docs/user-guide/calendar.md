# Calendar

The **Calendar** workspace shows your Google Calendar, and Friday can look up,
create and change events for you. Reading is immediate; any change waits for
your approval.

## Connecting

Calendar uses the same Google account connection as mail: **Settings →
Accounts & Keys → Google**. A standard connection includes calendar read and
write permission and Google Tasks. An account connected by an older version
may have calendar read-only permission; Friday says so when it tries to make a
change, and **Reconnect** fixes it.

## What Friday can do

| Action | Needs your approval? |
|---|---|
| Read today's and tomorrow's events, or find events by text or date | No |
| Read, create, update and complete Google Tasks | No |
| **Create an event** | Yes |
| **Change an event** (title, time, place, description) | Yes |
| **Add the same note to several events** | Yes |
| **Delete a task** | Yes |

In a conversation, Friday asks you in chat before it creates or changes an
event, and your yes covers that exact event. In a scheduled job it needs a
grant, or it waits on an approval card. See
[Approvals and receipts](approvals-and-receipts.md).

Friday will not blank a field of an existing event unless you asked for that
specifically.

## Dates and times

Every date in a result Friday reads carries its weekday computed by Friday, not
guessed by the model, and times are sent to Google with your time-zone offset.

## Privacy

An event you approve is written to your Google Calendar with the details you
approved.
Event content Friday reads is treated
as read content: an address or link in an invitation cannot direct an action
without the card saying where it came from.
