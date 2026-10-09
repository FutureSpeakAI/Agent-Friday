# Calendar

The **Calendar** workspace shows your Google Calendar, and Agent Friday™ can
look up, create and change events for you. Reading is immediate; any change
waits for your approval.

## Connecting

Calendar uses the same Google account connection as mail: **Settings >
Connections > Google accounts**. You can connect more than one account, and
Friday reads every account that has calendar turned on. A standard connection
includes calendar read and write permission and Google Tasks. An account
connected with read-only calendar permission is reported when Friday tries to
make a change, and **Reconnect** fixes it.

## What Friday can do

| Action | Needs your approval? |
|---|---|
| Read today's and tomorrow's events, or find events by text or date | No |
| Find times that are free on every connected calendar | No |
| Read, create, update and complete Google Tasks | No |
| **Create an event** | Yes |
| **Change an event** (title, time, place, description) | Yes |
| **Add the same note to several events** | Yes |
| **Place tentative holds** on your calendar | Yes |
| **Book a time** and send the invitation | Yes |
| **Delete a task** | Yes |

In a conversation, Friday asks you in chat before it creates or changes an
event, and your yes covers that exact event. In a scheduled job it needs a
grant, or it waits on an approval card. See
[Approvals and receipts](approvals-and-receipts.md).

Friday does not blank a field of an existing event unless you ask for that
specifically.

## Offering meeting times

When someone writes "can we talk next week?", Friday can find times that are
free on every connected calendar, hold them while the other person chooses,
and draft a reply that offers them.

1. **Find times.** Friday searches within your working hours and working days,
   in your time zone, and keeps a notice period and a buffer around existing
   events. A calendar Friday could not read is named in the answer and is never
   treated as free.
2. **Hold times.** With your go-ahead, Friday places tentative events titled
   "Hold: ..." on one of your own calendars, up to ten at a time. A hold
   invites nobody and sends no notification. Colleagues who can see your
   free/busy may see the time as busy.
3. **Offer them.** Friday drafts the reply through the usual email approval
   card. Nothing is sent until you approve it.
4. **Book.** Once a time is picked, Friday turns that hold into the real event
   with the invitation and releases the other holds. Booking asks for
   approval.

Friday deletes a hold only when a fresh read of the event shows Friday's own
hold marker and no guests. A hold is never matched by its title.

The defaults are weekdays from 09:00 to 17:00, a 12-hour notice period and a
15-minute buffer, in this computer's time zone. They live in the `scheduling`
block of `settings.json`; see [Configuration](configuration.md).

## Dates and times

Every date in a result Friday reads carries its weekday computed by Friday,
not guessed by the model, and times are sent to Google with your time-zone
offset.

## Privacy

An event you approve is written to your Google Calendar with the details you
approved. Event content Friday reads is treated as read content: an address or
link in an invitation cannot direct an action without the card saying where it
came from.
