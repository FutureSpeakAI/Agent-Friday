# Agent Friday 5.14.2

*26 September 2026. FutureSpeak.AI*

**Things you approve now actually happen.** In 5.14.0 and 5.14.1, approving an
approval card recorded your decision but never carried out the action. This
release fixes that, and fixes a gap in the privacy check that let some text
reach the cloud with less checking than it should have had. Please read the
next section. Voice conversations are also much steadier.

Full detail is in the [CHANGELOG](CHANGELOG.md); what is not right yet is in
[KNOWN_ISSUES](KNOWN_ISSUES.md).

---

## Two things 5.14.0 and 5.14.1 got wrong

**Approved cards never ran.** Friday asks before she does anything outward in
one of two ways. Sometimes she asks in the chat, and when you say yes she goes
ahead; that worked. Other times she raises an approval card instead: for
example when the request came from something she read, such as an email or a
web page, or from a scheduled job. When you approved one of those cards, your
approval was recorded and nothing else happened. The event was not added to
the calendar, and the email or the file was not sent or written. Worse, Friday's
own record of the attempt said it had succeeded, before you had even decided.

If you approved cards in 5.14.0 or 5.14.1, check whether what you approved
actually happened, and ask Friday again if it did not. Nothing was done that
you did not approve: the failure was that approved things were not done.

Now, when you approve a card, the action runs, exactly once, even if you
approve it in two tabs at the same moment. For a card raised in a chat, the
result appears in that chat without reloading the page, and so does one raised
by a background task you started from a chat. A card that is waiting is recorded
as waiting, not as a success, and a failed action records why it failed.

**The privacy check could fall back to pattern filters alone.** Before text
goes to a cloud model, Friday checks it for personal information in layers.
The first layers look for patterns: addresses, account numbers, health words
and so on. A later layer, the semantic check, catches personal information
that has no such pattern in it. When that semantic check failed to start,
Friday went on sending cloud-bound text with only the pattern filters checking
it, and said nothing. This could happen on any install with the memory
component, which the Windows installer includes by default, when the check
failed to load at startup.

Now, if the semantic check is installed but not running, Friday holds the
message and tells you, in the chat or out loud in a voice call. You choose:
send it anyway with the pattern filters only, or wait until the full check is
ready. A startup problem that could stop the check from loading is also fixed.
Settings › Privacy & Approvals › Privacy check shows, in plain words, which
layers are running on your PC.

## What's new

**Approval cards appear wherever you are.** A card that is waiting for you now
pops up in every open Friday tab, not only in the System workspace, and
disappears from all of them as soon as it is decided anywhere: in another tab,
by voice, or by a reply.

**Friday can open things for you, and check what is going on.** Ask her to
open a particular email, file, wiki page, calendar day, contact or Settings
section, and she opens exactly that. She tells you only what the screen
actually shows. Ask how things are going, and she can tell you which
workspaces are open, how busy the PC is, which models are loaded, what is
running, and what you have spent today.

**"Who is talking" for voice.** With the Cloud (Gemini Live) voice engine,
Settings › Voice & Tracking › Listening lets you tell Friday there are several
people in the room. She then answers only when she is spoken to by name, or
when someone is replying to her.

## What changed

- **Voice news moves forward.** A spoken news rundown covers different topics
  and does not repeat stories Friday has already told you in the same call.
  When there is nothing new, she says so.
- **No more long silences.** Every tool Friday uses during a voice call
  answers within 20 seconds, or she tells you nothing came back.
- **Friday stays in English.** The voice session's language is fixed (English
  unless you set another), so a misheard phrase no longer makes her switch
  languages.
- **Fewer interruptions.** A cough or a single word no longer cuts her off;
  speech has to last a moment first.
- **Nothing lost on reconnect.** A voice call quietly reconnects from time to
  time. What you said during a reconnect is now passed on, so you get an
  answer.
- **Her voice stays hers.** The personality you saved holds for the whole
  call, instead of drifting towards a generic tone.
- **Answers sized to the moment.** Short replies for quick back-and-forth,
  more room for the news, an explanation or a story, and she follows "keep it
  short" or "tell me more".
- **Evidence-first news.** When Friday reads the news she gives sources, says
  what is confirmed and what is only alleged, labels analysis as analysis, and
  leaves the judgement to you.
- **Honest about your notes.** In a voice call Friday uses your notes when
  your vault settings allow it, and says they are private only when they are.
- **Public business details are not your personal data.** A restaurant's
  published address, read from its own website, can now go into a calendar
  event. Your own addresses and records are still protected, and so are ID
  numbers, card numbers and health details wherever they came from.
- **Card labels describe the action.** A calendar entry is no longer labelled
  as spending because its notes mention buying tickets.
- **Local tasks use your local model.** A job meant for a model on your PC no
  longer picks a cloud model and then refuses with a confusing message.
- **Honest about which build you are running.** The health summary called an
  installed copy a "source checkout", and reported a privacy layer that is
  switched off on purpose as though something were broken. Both now say what is
  actually true; a layer that really is down is still reported as such.
- **Steadier startup.** A race while Friday loads could stop the privacy check
  or the Kokoro voice from starting. It no longer can.

## Security

The open dependency advisories are unchanged. None is reachable in a default
install; each one is explained in
[docs/security/dependency-advisories.md](docs/security/dependency-advisories.md).

## Upgrade notes

- **Your data and your vault passphrase are preserved.** Run the new installer
  over the old one. [Updating](docs/user-guide/updating-and-uninstalling.md)
- **Reload any Friday tab that was open during the upgrade**, so it receives
  approval pop-ups.

## Known issues

The most likely to affect you: moving your `.friday` folder to another PC
holds outward actions until you re-confirm the rules in Settings. All known
issues, with workarounds, are in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
