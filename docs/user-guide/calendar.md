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

## Day, Week and Month

The switch at the top of the Calendar shows one day (the timeline, with gaps,
prep cards and tomorrow's preview), a week (seven columns on the same hours,
all-day entries above) or a month (each day with its first events and a count
of the rest). The arrows and **Today** move through weeks and months; clicking
a day opens it in the Day view. Friday remembers the view you used last.

Every calendar of every connected account is read, and each event wears the
colour it has in Google Calendar: its own colour if you gave it one, otherwise
its calendar's. Calendars you have unticked in Google stay hidden until you
switch them on. If one calendar cannot be read, the view names it; it is never
shown as an empty day.

By voice: "show me my calendar by month", "switch to the week view",
"show my free busy time".

An account connected before the full calendar permission existed shows only
its main calendar and says so; **Settings > Connections > Reconnect** adds the
others.

## Which calendars, and free/busy

**Calendars** in the toolbar lists every calendar of every connected account
with its colour. Tick or untick one to show it in Friday or not; that changes
Friday only, never Google. A calendar you have hidden in Google starts
unticked and says so.

By voice, "show only my family calendar" narrows the view without changing
your ticks: a chip says "Only Family · by Friday", and its × (or "show all my
calendars") brings the rest back. Friday never switches a calendar off by
herself.

**Free/busy** shows the week as time taken and time open. Busy blocks name
their calendars, never the titles of events, so it is safe to show on a shared
screen; free time is counted inside your working hours on working days (the
same `scheduling` settings Friday uses to offer meeting times). It is worked
out on this computer from the events already read; nothing is asked of Google.

## Adding and moving events yourself

**Quick add.** Type a sentence ("lunch with Dana Tuesday 12pm") and press
Enter. Friday reads it, by code first and with a small model only for what
code could not find, and shows the event: its title, when, and a list of the
calendars you can add to. Nothing is written until you press **Add to ...**.
Guests named in the sentence are added without an email from Google.

**Drag to reschedule.** In the Day and Week views, drag an event to a new
time (in Week, to another day too), or focus it and press Alt with an arrow
key. Friday then asks "Move ... ?" with the old and new times; **Cancel** is
the button in focus, and nothing changes until you press **Move**. A drag of
less than a quarter of an hour, Esc during a drag, or an event you can't
change (one someone else organises) asks nothing and changes nothing. After a
move, **Undo** puts the event back.

**Replying to an invitation.** An event someone else invited you to shows
your reply and three buttons: **Going**, **Maybe**, **Not going**. Friday
asks "Reply ... ?" and says Google will let the organiser know; nothing is
sent until you press **Send reply**. You can change a reply any time. Replies
are a click, not something Friday sends by voice, because they reach another
person.

If you are moving several things, tick **Move without asking for the next 30
minutes** in the confirmation. That permission lives only in this window and
runs out by itself; every move still gets its Undo.

## Reminders and prep cards

Friday uses the reminders you already have in Google Calendar: an event's own
("30 minutes before"), or its calendar's default. The Day view shows each
event's reminder, and when one comes due Friday puts it in her tray: "Dentist
in 30 minutes". Each reminder comes once. Friday adds no reminder Google does
not have, says nothing for an invitation you declined, and leaves email
reminders to Google. Quiet hours hold reminders like any other notice.

An event with other people in it (or about your career) is one that matters:
its reminder says the prep card is one click away, and clicking the reminder
opens the event with its prep card (the "Meeting prep" job, which you can see
in Activity). Friday makes no prep card by herself.

To stop Friday's reminders, switch off **Calendar reminders** in the
Workflows workspace, under Friday's built-in routines
([Scheduled jobs](scheduled-jobs.md)); Google's own reminders are unchanged.

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
