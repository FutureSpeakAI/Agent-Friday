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

## Recording a meeting

Meetings records, transcribes and takes notes on this computer. It has its own
workspace in the dock, and Calendar keeps a link to it. The full page is
[Meetings](meetings.md); this is the short version.
Nothing is recorded unless you press start on screen and confirm; Friday never
starts a recording by herself. If you say "start recording", she opens the same
start card and waits for you.

- **Both sides of a call.** The microphone is always recorded ("You"). Tick
  "Also record the other side of the call" to record what your speakers play
  ("Them") from any call app. The two stay separate in the transcript. Friday
  tells you what is really being captured, in the top bar, the tray tooltip and
  the Meetings view; the red indicator is always shown while she records and
  can't be hidden.
- **Ask everyone.** Some places require everyone's consent to record. Friday
  can't decide the law for you, so the start card says so and the default is to
  ask everyone. The consent line is yours to edit; Copy puts it on the
  clipboard to paste into the call chat. Friday doesn't post into other apps.
  You can also have her say it aloud when recording starts (off by default).
- **"Your call is starting. Take notes?"** shows at most once per event and
  switches itself off after you decline twice. Accepting only opens the start
  card.
- **While you record,** Friday keeps background work out of the way and
  transcribes with the local voice (the GPU when it is free, the CPU
  otherwise). Live words appear as you talk when the streaming ear is
  installed.
- **Keeping and deleting.** Nothing is chosen for you. For audio: delete it once
  the notes are made (Recommended), after 30 days, or keep it. For transcripts
  and notes: keep them, or delete after a number of days. Audio can be deleted
  without touching the notes; deleting a meeting removes everything. Both are
  real deletions, and the activity ledger records only a count. The settings
  are `meeting_*` in [Configuration](configuration.md).
- **Meeting content stays on this computer.** Notes are written by a local model
  only, and a follow-up email is only ever an approval card. Friday sends
  nothing from a meeting to a cloud service.

## Dates and times

Every date in a result Friday reads carries its weekday computed by Friday,
not guessed by the model, and times are sent to Google with your time-zone
offset.

## Privacy

An event you approve is written to your Google Calendar with the details you
approved. Event content Friday reads is treated as read content: an address or
link in an invitation cannot direct an action without the card saying where it
came from.
