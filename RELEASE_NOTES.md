# Agent Friday 5.14.1

*25 September 2026. FutureSpeak.AI*

**Cloud-only installs now fully work.** You can install Friday on an ordinary
Windows laptop, with no graphics card and no local AI model, give her one API
key, and everything works: chat, the morning news, the evening front page,
the briefings, daily creation and the heartbeat.

**This release also fixes a privacy leak in 5.14.0.** Please read the next
section if you ran 5.14.0 with a cloud key.

Full detail is in the [CHANGELOG](CHANGELOG.md); what is not right yet is in
[KNOWN_ISSUES](KNOWN_ISSUES.md).

---

## The privacy leak in 5.14.0

**What happened.** Friday shortens a conversation when it grows too long for
the model, by summarising the middle of it. It also writes a summary of the
day's conversations at 23:30 each night. In 5.14.0, both of those summaries
could be written by a cloud model even when the conversation was meant to stay
on your PC. The text sent to be summarised could include local-only chats,
what Friday read from your vault, and the output of tools that are meant to
stay local. Specifically:

- **A long conversation on your PC's own model** was summarised by a cloud
  model whenever the local model did not answer or the router chose the
  cloud.
- **The 23:30 day summary** sent the whole day's conversation, local-only
  chats included, through the ordinary router, which could choose a cloud
  model.
- **A scheduled prompt you set to local-only** did its work on a separate
  thread that the local-only rule did not reach, so it could use a cloud
  model. Friday's built-in scheduled jobs were not affected.
- **An Ollama model whose name ends in `-cloud` or `:cloud`**, which Ollama
  runs on ollama.com, was treated as local.

**Who could be affected.** Anyone who ran 5.14.0 with a cloud key set up and
used local-only chats, long conversations on a local model, or local-only
scheduled prompts, or that picked an Ollama cloud model. A PC with no cloud key
and no Ollama cloud model sent nothing, because there was nowhere to send it.

**Where it went.** Only to the cloud providers you had set up, under your own
key, or to ollama.com for an Ollama cloud model. Nothing went to FutureSpeak.AI
or to anyone else. Your provider's usage page lists the requests made with
your key, and its data policy says how long it keeps them.

**What 5.14.1 does.** It fixes each of them:

- A local conversation is summarised by the model that is already holding it.
  If no local model is available, the conversation is not summarised; it is
  never sent to the cloud instead.
- The 23:30 day summary is written on your PC or not at all.
- A local-only scheduled prompt stays local-only on every thread it uses,
  and after a restart.
- Ollama `-cloud` and `:cloud` models count as cloud, so local-only work
  refuses them.

**What you should do.** Install 5.14.1. Nothing else is needed.

## What's new

**One key is enough.** An Anthropic key, or an OpenRouter key if you prefer,
is all Friday needs to think in the cloud. The setup chat links to both key
pages, stores the key encrypted on your PC, checks it, and tells you plainly
whether Friday can now think.
[Getting started](docs/user-guide/getting-started.md)

**Scheduled jobs on a laptop without a local model.** Friday's scheduled jobs
used to run only on a model on your own PC, so on a laptop they never ran. The
setup chat now asks once whether they may use a cloud model, and shows what
that would cost: about $9 a month with the defaults (Claude Haiku 4.5, and the
heartbeat every four hours during the day rather than every hour). Say no, or
skip it, and they stay paused with one notice telling you where to change your
mind: Settings → Spending, which also shows the models and the estimate. On a
PC with a local model nothing changes; the jobs run there.
[Scheduled jobs](docs/user-guide/scheduled-jobs.md)

**A new Workflows screen.** Everything Friday does on her own is now one list,
in plain words: what each workflow does, when it runs ("Every weekday at
7:30 AM"), how the last run went and when the next one is, with a switch to
pause it. To make a new one, say what you want and when in your own words;
Friday writes a draft for you to check, and nothing is saved until you do.
Workflows with several steps can now run on a schedule too. Friday still asks
you before a workflow sends, posts, buys or deletes anything, and the screen
says so and shows when something is waiting for your OK.
[Scheduled jobs](docs/user-guide/scheduled-jobs.md)

**Long tasks keep going, and pick up after a restart.** A background task now
keeps an encrypted working record of what it has done and learned. When it
reaches the end of a turn it carries on by itself from that record, and if
Friday restarts or your PC crashes it resumes from where it was. Anything that
would send, post, buy or delete still waits for you. Numbers such as account,
card or phone numbers are masked in the record's step list.

**No limits you did not set.** Friday used to stop a turn after a built-in
number of rounds, minutes or tokens, and held every cloud answer to about
3,000 words. Those limits are gone. The only limits now are the ones you set
in Settings → Spending, plus the Stop button and the check that stops a model
repeating itself.

**Office documents out of the box.** The installer now sets up OfficeCLI, the
engine Friday uses for Word, Excel and PowerPoint files, and checks it against
a fingerprint built into the installer before using it.

**No font requests.** Friday's typefaces now come with Friday, as exactly the
files Google Fonts served, so the app looks the same and no longer asks Google
for them on every page load.

## What changed

- **Faster voice conversations.** Gemini 3.8 Live is the default Live model:
  Friday starts speaking in about 0.9 seconds rather than 2.2. When she needs
  a tool mid-conversation she runs them together and keeps listening, so a
  news or email lookup is no longer 40 seconds of silence.
- **Long answers from a local reasoning model no longer come back empty.** The
  model now has room to think and to answer; if it still runs out, Friday
  retries without the thinking, and failing that says so and offers to
  continue.
- **"Email this address" works with the safeguards on.** With the safeguards
  on, a cloud model sees addresses and numbers as tags. Tools now receive the
  real value on your PC, and the approval card shows it.
- **Context compression that actually compresses.** Headroom, the library
  Friday uses to shrink long tool results, is pinned to version 0.38.0. Earlier
  installs could end up with a version that returned everything unchanged.
  Its usage reporting and its anonymous upload beacon are both switched off.
  Friday uses Headroom as a library, a path that does not send the beacon;
  it is off regardless.
- **The heartbeat no longer runs in the cloud behind your back.** A fresh
  install used to run it every hour on a cloud model. It now follows the same
  rule as the other scheduled jobs.
- **A slow laptop never holds up an approval.** If Friday's second opinion
  takes more than a couple of seconds on your PC's processor, the simpler check
  decides, and Settings says how often that happened.
- **Dictation on a laptop** uses a smaller speech model on the processor, so
  Alt+T stays quick without a graphics card. Alt+T also works as soon as the
  tray starts, even while Friday is still loading, and the tray tells you if it
  cannot register the key.
- **Friday's voice no longer hangs on a busy graphics card.** One spoken reply
  is limited in time and length, and Friday recovers by herself.
- **A new install is never held for "re-confirm the rules"**; that only
  applies when a `.friday` folder is moved from another PC.
- **Menus and screens.** The top bar's menus open fully on a narrow window,
  the model menu no longer fails when two screens load it at once, and the
  Knowledge screen has the wiki on the left with the app's own scrollbars.
- **The file-picker button and the approval cards look as they did before
  5.14.0.**

## Security

- **Parts of a Gemini key were written to Friday's log.** Starting a Gemini
  Live conversation wrote the first ten characters of the Gemini key to the
  log file on your PC, and the key lookup wrote eight more. The log stays on
  your PC, but if you have shared a log file (for example with a bug report),
  consider replacing your Gemini key. Logs now say only whether a key is
  present.
- **Text an email planted in a task's notes stays flagged.** Friday remembers
  which text came from content it read (an email, a web page) rather than
  from you, and treats instructions in it with suspicion. That memory could
  lapse for text carried forward in a long task's notes or a summary. It no
  longer lapses.
- **The GPU voice tier loads only NVIDIA's own models.** Some libraries that
  come with the optional GPU voice tier have published vulnerabilities with no
  fix available. Friday now refuses any voice model name outside NVIDIA's own
  collection, which keeps those vulnerabilities out of reach. The open
  advisories and why none affects a default install are listed in
  [dependency-advisories.md](docs/security/dependency-advisories.md).

## Upgrade notes

- **Your data and your vault passphrase are preserved.** Run the new installer
  over the old one. [Updating](docs/user-guide/updating-and-uninstalling.md)
- **On a PC without a local model** you are asked once about scheduled jobs in
  the cloud; until you answer they stay paused, as before.

## Known issues

The most likely to affect you: moving your `.friday` folder to another PC
holds outward actions until you re-confirm the rules in Settings. All known
issues, with workarounds, are in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
