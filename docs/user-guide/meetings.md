# Meetings

Agent Friday™ can record a meeting or a call, write down what was said, and take
notes. All of it happens on this PC. Nothing is recorded unless you press start
on screen and confirm. Friday never starts a recording by herself.

## Where to find it

Open **Meetings** in the dock (Work). It holds **Record a meeting**, your notes
pad, **Past meetings** and the box that asks your meetings a question. Calendar
keeps a short link to it. If you say "start recording", Friday opens the same
start card and waits for you. She does not start anything by voice.

## Starting a recording

**Record a meeting** opens the start card. It always shows:

- The consent statement, and a note on the law: some places require everyone's
  consent to record a call, and some only the person recording. Friday can't
  decide the law for you, so the default is to ask everyone.
- **What to tell everyone**, an editable line. The default is: "Heads up: I'm
  recording this call so I can take notes. The recording stays on my computer.
  Tell me now if you'd rather I didn't." Edit it and choose **Save my
  wording**, or choose **Copy the line** and paste it into the call chat.
  Friday does not post into other apps. **Say it aloud when recording starts**
  is off unless you tick it.
- A name for the meeting. If a calendar event is on now, its title is filled in.
- **Keep the audio (encrypted)**, **Also record the other side of the call**,
  and **Stop after** 1 to 4 hours (the default is 2).
- The start button. It reads "I've told everyone, start recording". If you
  changed **Consent mode** to **owner decides** (the setting
  `meeting_consent_mode`), it reads "I've checked what applies, start
  recording". Either way, recording starts only when you press it.

"Your call is starting. Take notes?" may appear for a calendar event. It shows
at most once per event, and switches itself off after you say **Not now** twice
(**Turn them back on** is in Meetings). Choosing **Open the start card** opens
the card and starts nothing. Turn it off in the `meeting_suggestions` setting.

## Both sides of a call

The microphone is always recorded and labelled **You**. Tick **Also record the
other side of the call** and Friday also records what your speakers play,
labelled **Them**, from any call app. The two are kept apart and never mixed, so
each is transcribed and labelled on its own.

A microphone alone, in a room, is not split into people: its lines carry no
speaker names.

If this PC can't record system audio, the tick box is unavailable and says why
(for example, a missing Windows component). If it stops partway, Friday says so
and keeps recording your microphone.

## The indicator

While Friday records, a red dot, **REC**, the elapsed time, and **Stop** are on
screen, in every workspace. It says "both sides" when the other side is being
captured, and what the indicator says is what is really being recorded, not what
you asked for. The tray icon's tooltip says the same. It has no dismiss
control and nothing can hide it. Until the microphone delivers its first sound,
it reads "Starting microphone...".

A recording stops by itself at its maximum length, when the computer sleeps,
when the screen locks (where Windows lets Friday tell), and when the microphone
fails. The meeting says why it stopped.

## While you record

- Background work waits. The Local AI queue says "waits for your meeting to
  finish". See [Scheduled jobs](scheduled-jobs.md).
- Speech is turned into text on this PC, by the local voice, on the graphics
  card when it is free and on the processor otherwise.
- If the streaming speech recogniser is installed, live words appear as you
  talk. They are shown, never stored.
- Audio is cut into pieces of about thirty seconds. Once a piece is turned into
  text its audio is dropped, unless you ticked **Keep the audio**.

## Your notes pad

While you record, the workspace shows **Your notes pad**: a line to jot short
bullets in ("Priya: budget by Friday"), each stamped with how far into the call
you wrote it. Press Enter or **Add**; the **x** takes a bullet away. The pad is
also a field Friday can fill when you ask her to, and she never adds the bullet
for you. Bullets stay with the meeting, encrypted like the transcript. You can
add more after the call too.

## Notes from your bullets

After the call, **Turn my bullets into notes** asks a model on this PC to read
the transcript and expand each bullet into a sentence or two. Every line comes
with the transcript line it rests on, shown as `line 12 · 00:03:20`; click it
to read that line. The model runs in the work queue as your own job, not as
background work, and Friday does not use a cloud model for it, whatever your
routing mode.

A line is shown only if it cites a transcript line that exists and says nothing
that the transcript and your bullet do not. A name, number, date or place that
nobody said is checked by a program, not by the model. A line that fails is
left out, and Friday tells you how many. A bullet the transcript does not cover
gets no line. The check cannot catch a wrong claim made in ordinary words,
which is why every line shows the transcript line to read beside it.

## Notes

When the recording ends, a local model writes the notes, the decisions and the
action items. If no local model is running, the notes are skipped with the
reason, and **Write notes again** is on the meeting. Notes are never written by
a cloud model.

- **Add selected to Tasks** turns the action items you tick into tasks. Nothing
  is added unless you tick it. If an action item cannot be found in the
  transcript, Friday says so under the notes ("Check before you add these to
  Tasks") so you can look before you add it.
- **Suggest what to remember** offers the people, projects and decisions of the
  meeting to Friday's memory. They are only suggestions: each shows the
  transcript line it came from and the model that read it, with **Keep** and
  **Not now**. Nothing goes into memory or the knowledge graph unless you press
  **Keep**.
- **Add the summary to the calendar event** (for a meeting that started from a
  calendar event) raises an approval card showing the text, which is the
  decisions and action items and never the transcript. Nothing is added to your
  calendar unless you approve it, and it goes to that one event only.
- **Copy the notes** copies the notes, and your bullets' notes with their
  citations, to the clipboard.
- **Draft a thank-you email** and **Draft a follow-up** write a draft. Sending
  it is an approval card, and nothing is sent until you approve it. See
  [Approvals and receipts](approvals-and-receipts.md).

## Ask your meetings

The first time, Meetings asks whether to **Make my meetings searchable**
(labelled Recommended, and not chosen for you). If you say yes, each finished
meeting's transcript and notes are read into your Library, in a scope of their
own, on this PC. Then **Ask your meetings** answers "what did we decide about
the budget?" with footnotes that open the exact transcript line, and you can
ask the same by voice. Saying no indexes nothing, and **Stop making them
searchable** removes what was indexed. Deleting a meeting removes its Library
documents in the same step, and so does a retention choice that deletes it.

The Library's index is encrypted when its optional encryption component is
installed and a plain file on this PC when it is not; the box says which. Meeting
text in the Library is never offered to a cloud model, even with cloud answers
for the Library switched on and a file permission in place. The setting is
`meeting_archive`; see [Configuration](configuration.md).

## By voice

- "What did we decide about the budget?" and "What are my action items from the
  Marta call?" search your meetings through the Library, with citations.
- "Open my last meeting", "open the Marta call" and "open the second one" open
  that meeting in the workspace. "Open the second one" means the second in the
  list on screen.
- "Select these" and "tick the first two" tick meetings in the list, which only
  shows them.
- "Add those action items to my tasks", "draft a thank-you to everyone from that
  meeting" and "put the summary on the calendar event" go through the same
  tools, cards and approvals as everywhere else.
- "Start recording" opens the start card. You press start and confirm.

Turning bullets into notes, suggesting what to remember and deleting a meeting
are buttons for now.

## Keeping and deleting

Nothing is chosen for you. Until you choose, nothing is deleted on a timer. Set
your choices in **Settings > Privacy & Data > Meeting recordings**, or under
**Keeping and deleting** in Meetings.

| Choice | Options |
|---|---|
| Audio (`meeting_audio_retention`) | Delete the audio once the notes are made (labelled Recommended, never preselected), delete it after 30 days, or keep it until you delete it. |
| Transcripts and notes (`meeting_text_retention`) | Keep them, or delete the whole meeting, audio included, after a number of days (`meeting_text_retention_days`, 1 to 3650; 90 is offered). |

The sweep that applies these runs in the work queue, when you have been away,
about once a day. It never touches a meeting that is recording or still being
finished.

On any meeting:

- **Delete audio only** removes the audio from this PC. The transcript and notes
  stay.
- **Delete meeting** removes the transcript, the notes and any kept audio.

Both are real deletions, and each asks you to confirm. The activity ledger
records only a count of what was deleted.

## Privacy

- **Where it is kept.** One folder for each meeting under
  `%USERPROFILE%\.friday\meetings`. The transcript and notes are encrypted with
  Friday's keystore, and so is any audio you keep.
- **Nothing leaves this PC.** Speech recognition and notes run on local models.
  Meeting content has no route to a cloud model in this release: a meeting
  cannot be sent to the cloud, even in Cloud only mode, and a test holds that
  line. The only thing that can leave is a follow-up email, and only after you
  approve its card.
- **Other people.** A transcript holds what other people said. That is why the
  default is to ask everyone, why you see the consent line every time, and why
  it is your press that starts the recording. Friday does not tell anyone on
  your behalf.
- **Only you.** Meetings can be opened only by the person at this PC. A remote
  session is refused.

## What it does not do yet

- Help during the call. Nothing answers questions in a meeting while it runs;
  the pad is yours to write.
- Name the people in a room. A microphone alone in a room is not split into
  speakers, and a call's other side is labelled "Them".
- Write notes without a local model. Notes and the expansion of your bullets
  need one running; without it they are skipped with the reason, and **Write
  notes again** is on the meeting.
- Take Friday's voice for the buttons that change things. Turning bullets into
  notes, suggesting what to remember and deleting a meeting are on screen.

## What it never does

- Start a recording on its own, or from a suggestion, or by voice alone.
- Hide the indicator.
- Send audio, a transcript or notes to a cloud model.
- Write straight into memory or the knowledge graph from a meeting, or write to
  your calendar without a card you approved.
- Post the consent line into another app.
- Delete a meeting unless you, or a retention choice you made, say so.
