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
  recording". Either way, recording starts only from the Start card in the Friday window: the request must come from that page with its session token. That proves a page, not a physical click, so a program on this PC that forges those browser headers could ask for a start.

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
you asked for. The tray icon shows a red dot and a tooltip with the same words; they drop after two polls in a row fail to reach Friday (about ten seconds), so a Friday that cannot answer is the one case where the tray goes quiet. The indicator in the Friday window has no dismiss control. Until the microphone delivers its first sound,
it reads "Starting microphone...".

A recording stops by itself at its maximum length, when the computer sleeps,
when the screen locks (where Windows lets Friday tell), and when the microphone
fails. The meeting says why it stopped.

## While you record

- Background work waits. The Local AI queue says "waits for your meeting to
  finish". See [Scheduled jobs](scheduled-jobs.md).
- Speech is turned into text on this PC, by the local voice's ear. That ear is the same one a live voice conversation uses, so a voice turn during a meeting can wait behind a piece being turned into text; it uses the graphics card when it is free and the processor otherwise.
- If the streaming speech recogniser is installed, live words appear as you
  talk. They are shown, never stored.
- Audio is cut into pieces of about thirty seconds. Once a piece is turned into
  text its audio is dropped, unless you ticked **Keep the audio**.

## Your notes pad

While you record, the workspace shows **Your notes pad**: a line to jot short
bullets in ("Priya: budget by Friday"), each stamped with how far into the call
you wrote it. Press Enter or **Add**; the **x** takes a bullet away. The pad is
also a field Friday can fill when you ask her to. Friday's on-demand `meeting` tool can also add a bullet without a card (it is the owner's own pad and stays on this PC); a bullet she added is marked "added by Friday". It is never treated as your own words: the notes model may not take names from it, and the archive files what comes from it as "From a bullet Friday added". Bullets stay with the meeting, encrypted like the transcript. You can
add more after the call too.

## Notes from your bullets

After the call, **Turn my bullets into notes** asks a model on this PC to read
the transcript and expand each bullet into a sentence or two. Every line comes
with the transcript line it rests on, shown as `line 12 · 00:03:20`; click it
to read that line. The model runs in the work queue as your own job, not as
background work. Friday refuses a model whose name is cloud-tagged (`name:cloud`, `name-cloud`), whatever your routing mode. That is a check of the name only: a local alias that you built from a cloud model under a plain name is not recognised.

A line is shown only if it cites a transcript line that exists and says nothing
that the transcript and your bullet do not. A name, number, date or place that
nobody said is checked by a program, not by the model. A line that fails is
left out, and Friday tells you how many. A bullet the transcript does not cover
gets no line. The check cannot catch a wrong claim made in ordinary words,
which is why every line shows the transcript line to read beside it.

## Notes

When the recording ends, a local model writes the notes, the decisions and the
action items. If no local model is running, the notes are skipped with the
reason, and **Write notes again** is on the meeting. Friday does not send the transcript to a model whose name is cloud-tagged (see above); the check is of the name.

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
(neither choice is recommended or chosen for you). If you say yes, each finished
meeting's transcript and notes are read into your Library, in a scope of their
own, on this PC. Then **Ask your meetings** answers "what did we decide about
the budget?" with footnotes that open the exact transcript line, and you can
ask the same by voice. Saying yes is a click on the screen in the Friday window: the
settings write ignores `meeting_archive`, and voice and Friday's tools cannot set it. Saying no is a click on the screen too. It
indexes nothing, and **Stop making them searchable** removes what was indexed, in the background as a visible job. It also removes the memory suggestions made from meetings and, from your saved chats, the footnotes and quoted lines of replies that cited a meeting (nothing else in your chats, and never your own messages). Both
ends take effect at once: the answer is read again on every search, so a meeting
stops being an answer the moment you say no, before the documents are cleared.
Deleting a meeting takes it out of search first and removes its Library documents
in the same step, and so does a retention choice that deletes it. Deleting only the
audio leaves the notes and the archive as they are. If you ask Friday to forget a
Library document or a person, the meeting's documents are included; forgetting a person also redacts the name in the meeting record itself and in its suggestions (a meeting that is still being recorded or transcribed is redacted when it is saved settled), and the transcript is not read back in with that name or that document.


Meetings are encrypted in their own store, so they are read into the Library
only when the Library's index is encrypted too (its optional encryption
component installed and keyed). Otherwise the box says "Not available on this
PC: the Library index isn't encrypted", and nothing is indexed. Meeting
text in the Library is never offered to a cloud model, even with cloud answers
for the Library switched on and a file permission in place, and it is not read out
on a cloud voice call or in an answer relayed to one: with a cloud voice, Friday
says the meetings stay on this PC. A meeting is never drafted into the wiki's
suggested notes either; the Library's learning from your files does not read your
meetings. The setting is
`meeting_archive`; see [Configuration](configuration.md).

## By voice

- "What did we decide about the budget?" and "What are my action items from the
  Marta call?" search your meetings through the Library, with citations, when
  the local voice and the local model are answering. On a cloud voice call Friday
  says your meetings stay on this PC.
- "Open my last meeting", "open the Marta call" and "open the second one" open
  that meeting in the workspace. "Open the second one" means the second in the
  list on screen.
- "Select these" and "tick the first two" tick meetings in the list, which only
  shows them. Friday sees meeting rows as a count and a position, and what Friday sees of the Library screen leaves a meeting document's title out (the list on your screen still shows it). A title is named only to a local model speaking on this PC.
- "Add those action items to my tasks", "draft a thank-you to everyone from that
  meeting" and "put the summary on the calendar event" go through the same
  tools, cards and approvals as everywhere else.
- "Start recording" opens the start card. You press start and confirm.

Turning bullets into notes, suggesting what to remember and jotting a bullet
are also one on-demand `meeting` tool: ask Friday ("turn my bullets from that
call into notes") and she loads it. It stays on this PC, never starts a
recording, and tells a cloud model counts, not titles. Deleting a meeting is a
button.

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
- **What stays on this PC, and what can leave.** Speech recognition and notes run on local models, and Friday holds the Library passages of a meeting, its titles and its notes back from a cloud model, from a cloud voice call and from a local model whose answer a cloud voice reads out. Meeting content can still leave in four ways you approve or choose: a follow-up email after you approve its card; the calendar summary, after you approve its card, to your calendar account; a suggestion you keep, which then joins what Friday knows about you and may be told to a cloud model like anything else she knows about you; and a local model that is really a cloud model under a plain name, which no name check can see. The tests cover the cloud-tag name check, the stage and the titles; they use a fake model.
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

## What it never does

- Start a recording on its own, or from a suggestion, or by voice alone.
- Hide the indicator.
- Send audio, a transcript or notes to a cloud model on purpose. See "What stays on this PC, and what can leave" above.
- Write straight into memory or the knowledge graph from a meeting (a suggestion waits for your click on the screen, and the graph does not read the archive), or write to
  your calendar without a card you approved.
- Post the consent line into another app.
- Delete a meeting unless you, or a retention choice you made, say so.
