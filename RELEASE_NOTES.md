# Agent Friday™ Beta 1.2

*FutureSpeak.AI™*

Agent Friday™ is a private AI agent that runs on your own Windows PC. Beta 1.2
follows Beta 1.1. It is a **pre-release**: it is meant to be tried, and it has
rough edges, listed below.

## Setup (first run)

- **First run is a conversation.** Friday sets herself up by talking you
  through it, and says what each choice means before you make it.
- **Friday's name and voice are yours to choose.** Nothing is assumed.
- **Your standing choices are settings.** Each has a default you can see and
  check, and first run asks the questions that need your answer.
- **First run says what is still downloading**, and model names show what is
  actually running, not what was planned.
- **Models fit this PC.** Local models are sized to its memory, feature models
  report their real download sizes, and the graphics card is used only when
  its libraries load.
- **The local model runtime installs from a pinned, known-good release.**
- **Your own changes to Friday's files survive updates.**
- **Spending caps start off**, so nothing is limited before you choose.
- The Desktop and Start menu shortcuts open Friday through the tray.

## First-run connections screen

- **Connect your services, in setup or later in Settings.** One screen lists
  what Friday can actually use: AI model providers (Anthropic, OpenRouter,
  Google Gemini, Groq, Mistral, xAI, DeepSeek, Together, a local Ollama, LM
  Studio or any OpenAI-compatible address), creative and media services,
  search and web tools, your accounts, and any MCP server.
- **Each card says how to get the key, what it costs where the vendor says
  so, and what it can reach.** The key goes into a masked field, and **Test
  connection** tells you it works, what to fix, or that it could not tell. A
  key is never shown back or written to a log.
- **Skippable.** Choose **I'll just use local AI** and setup moves on without
  asking for keys; no privacy or routing setting changes.
- **You can ask by voice** ("connect OpenRouter") and Settings opens on that
  card, with the field ready. Voice never types or submits a key.
- **A fresh install is quiet.** Services you have not connected say "not
  connected" and stay silent, instead of warning on start-up.

## Chat

- **A message you sent is kept even when the reply fails**, and you can send
  it again.
- **Friday can offer to switch a chat's model, or hand one task to the local
  model**, on a single card you approve.
- **The Models card shows the model that actually answered.**
- **A tool call is never left as words.** If a model writes a tool call into
  its reply as text, Friday runs it or says it could not.
- **Past conversations can be searched** by what was said.
- **Mail:** Friday can read a whole email thread to you, by text or voice;
  drafted emails can be saved to Gmail Drafts instead of raising a send card;
  and inbox clean-up offers one-click unsubscribe only where the sender's own
  domain vouches for the link.
- **Scheduled jobs and workflows:** saved jobs run on a repeating timetable on
  the model you pinned; scheduled email jobs keep sending; a workflow you run
  on request has a drafts-or-send switch.
- **Plans:** a plan with several stops gets a rough travel-time check.
- **Podcasts:** hosts take their names from Settings, links in pasted research
  become sources, and sentences no longer break at an initial or an
  abbreviation.
- You can ask Friday to show, hide or list the workspaces in the dock.
- The routing mode says what actually answers.

## Privacy and security

- **A key pasted into chat is withheld before anything keeps it**, and so is a
  key pasted into topic keywords, outside-chat requests or schedule updates.
  The Gemini key now travels in a request header, not the address.
- **One vault, one passphrase.** A stray value in the Windows keychain can no
  longer hide your vault or split it in two.
- **Every confirmation is a card from the first ask.** A typed "yes" approves
  that one card once, a typed "no" declines only the card it answers, and only
  your own page decides a card.
- **A claim that something was done needs a receipt.** Friday does not say she
  saved, sent or changed something unless the write happened in that turn.
- **Searches show what they send.** A search card shows everything that would
  leave this PC and is approved only on screen; cloud voice asks too. A search
  that would share private text from the conversation asks first, with the
  exact query and the search engine.
- **The vault asks for your cloud consent**, and never gates a tool that sends
  nothing off this PC. A task meant for this PC stays here, and so does its
  result.
- **The Locks page shows what Friday may read**, and raw records ask per file.
- **Release files are signed and checked.** The public lists and the release
  manifest carry signatures; with no certificate configured, signing does
  nothing.
- Friday will not run a program that walks a folder named through your home
  folder or an environment variable, and checks encoded commands inside
  encoded commands.
- Every background prompt carries only what its job needs, and every system
  prompt states your privacy settings as they are.
- Conversation memory and the knowledge graph never use a default embedder
  you did not choose.

## Calendar and memory

- **Calendar shows Day, Week and Month**, every calendar, in Google's own
  colours. Turn any calendar on or off in Friday, or narrow a view by voice;
  a chip shows the filter and names only calendars you have switched on.
  Free/busy is shown too.
- **Quick add shows the event before it writes it**, and dragging an event
  moves it only after you confirm.
- **Invitations can be answered** going, maybe or not going, after a
  confirmation that names who will hear your answer.
- **Events can be deleted from the event itself**, always after a confirmation
  that names it and its time.
- **Reminders come when Google's do**, once, and point to the preparation for
  events that matter.
- **Core memory is a card you can see.** Only your own typed words save to it
  without asking; pasted or reported words never do.
- **Memory review** lets you see, confirm or undo what Friday learned. Saves
  skip duplicates and keep fenced content out.
- Friday's memory stays fixed for the length of a conversation, and the memory
  ledger names a fork when it finds one.
- Wiki changes apply only as previewed, never over a page you edited; additions
  land at the end of the right section, and a page keeps one title.

## Fixes

- An open Friday tab reloads itself after an update.
- Saving one setting no longer drops the settings nested beside it.
- The desktop sky shows success stars only; failures stay in Activity.
- Knowledge-graph recovery restores only what a save dropped for want of a key.
- Source trust scores each article once, whatever tracking tags its link
  carries.
- The key doctor groups vault homes by passphrase without storing any hash of
  it.

The full list is in the [CHANGELOG](CHANGELOG.md).

## Upgrading

Run this installer over an existing Beta 1.1 (v1.1.0-beta.1), Beta 1.0.2,
Beta 1.0.1, Beta 1.0 or 5.x install, including 5.14.3. It upgrades in place.
Before it changes anything, setup stops Friday and backs up your data folder
to a dated folder under `.friday-backups` in your user folder, and your vault
is part of that backup. When it finishes, it checks that your data folder has
every file it had before and that your vault's key files are unchanged.
Upgrades from 5.14.3, Beta 1.0, Beta 1.0.1 and Beta 1.1 are tested before each
release.

## Download and install

Download `AgentFriday-Setup-1.2.0-beta.1.exe` (<PLACEHOLDER: size, about N MB>)
from the assets below. The same hash is in the `.sha256` file beside it.

**SHA-256:** `<SHA256 PLACEHOLDER: filled in when the release is published>`

1. Check the file. In PowerShell:
   `Get-FileHash .\AgentFriday-Setup-1.2.0-beta.1.exe -Algorithm SHA256`.
   The `Hash` must match the line above.
2. Double-click the file. It installs for your Windows account only, needs no
   administrator rights, and brings its own Python and every package.
3. Windows SmartScreen may show **Windows protected your PC** and **Unknown
   publisher**, because the setup program is not code-signed yet. After
   checking the hash, choose **More info**, then **Run anyway**.
4. Finish setup, then Friday's first run takes it from there.

Step by step: [Getting started](docs/getting-started/README.md).

## What is not in this release

- **The installer is not code-signed.** SmartScreen warns, as described above.
- **There is no signed update channel.** Friday checks GitHub at most once a
  week, only if you turn the check on, and never downloads or installs a
  release itself.
- **Phone pairing is not in this release.**

## Known issues

The full list is in [KNOWN_ISSUES.md](KNOWN_ISSUES.md). Please report what you
find at
[github.com/FutureSpeakAI/Agent-Friday/issues](https://github.com/FutureSpeakAI/Agent-Friday/issues).

Build sequence: 101020001
