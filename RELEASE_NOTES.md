# Agent Friday 5.14.0

*Draft. Date to be set at release. FutureSpeak.AI*

This release is about trust. Friday now asks before every outward action, from
every surface, and keeps a signed receipt of each decision. It also gains a
proper mail client, a phone line you control, real Office documents, dictation
anywhere in Windows, and a Settings screen reorganised around what you are
trying to do. Full detail is in the [CHANGELOG](CHANGELOG.md); what is not
right yet is in [KNOWN_ISSUES](KNOWN_ISSUES.md).

---

## What's new

**Friday asks before it acts.** Reading, searching and drafting run on their
own. Sending, publishing, calendar changes, installing, overwriting your files
and most commands now wait for you, whether the request came from chat, voice,
a scheduled job or a text message. In a conversation you answer yes or no in
the chat, and your yes covers exactly that action. Otherwise an approval card
shows exactly what will happen. Every decision is written to a signed receipt
file, and if the receipt cannot be written, the action does not run.
[Approvals and receipts](docs/user-guide/approvals-and-receipts.md)

**Cards say where a detail came from.** Emails and web pages can contain
instructions written by someone else. When a recipient, link, account number
or command came from something Friday read rather than from you, the card says
so, and a quick yes in chat is not enough.

**Grants for scheduled jobs.** A scheduled job can take an outward action on
its own only with a grant you create in Settings → Privacy & Approvals: which
job, which actions, for how long, how many times. Every email still gets its
own card.

**Messages, with Gmail parity.** Search, threads, labels, drafts, scheduled
send, undo send, Delete to Gmail's Trash with undo, keyboard shortcuts and 3D
triage. Friday drafts; nothing is sent until you approve that exact message.
[Mail](docs/user-guide/mail.md)

**Knowledge.** Your wiki pages and the knowledge galaxy are now one workspace,
with graph, split and page views.

**Dictate anywhere with Alt+T.** Hold Alt+T in any Windows app, speak, and let
go: the words are transcribed on your PC and typed where you were. Local voice
also uses your GPU when it can and is ready faster.
[Voice](docs/user-guide/voice.md)

**Real Office documents.** Friday makes Word, Excel and PowerPoint files on
your PC and looks at each one before calling it done.
[Documents](docs/user-guide/documents.md)

**A phone line (off by default).** Texts, voicemail and approvals by text
through your own Twilio number. Friday may contact only your verified cell on
its own. [Phone](docs/user-guide/phone.md)

**A local address.** Open Friday at `https://agent.friday` instead of
`localhost:3000`, if you want to. [Getting started](docs/user-guide/getting-started.md#open-friday-at-a-local-address)

**Reasoning traces.** See how Friday reasoned through a turn, with its tool
calls, nested under the conversation; archived encrypted on your PC.

**Also:** workspaces in their own browser tabs, a chat sidebar with projects,
a stop button for a running reply, a floating widget when the window is small,
task resume after a crash, keyless local web search, an optional hard spending
cap, and Claude Opus 5.5 in the model picker.

## What changed

- **Settings has eight tabs** organised by task: General, Models, Accounts &
  Keys, Privacy & Approvals, Appearance & 3D, Voice & Tracking, Spending,
  Advanced, plus About.
- **Scheduled jobs run on your local model by default.** The built-in
  briefings, heartbeat, news front page and daily creation never fall back to a
  paid cloud model. On a cloud-only install they are skipped until you add a
  local model or allow a job the cloud; see
  [Scheduled jobs](docs/user-guide/scheduled-jobs.md).
- **Daily creation runs while you are away**, once a day, between 09:00 and
  23:00, when the GPU is free.
- **Local models get the same working budget as cloud models**, and the token
  ceiling now warns instead of stopping a reply mid-way.
- **Every reply says which model answered.**
- **The Friday Edition and the Home workspace are gone.** The desktop is the
  landing screen, and the briefings carry the morning read.
- **Requests through a tunnel or proxy are never treated as you.** They must
  log in.
- **Sonnet 5 costs are metered at the published $2 / $10 per million tokens**
  (they were overstated by half), so reported spend drops.

## Upgrade notes

- **Your data and your vault passphrase are preserved.** Run the new installer
  over the old one. It replaces the program in `%LOCALAPPDATA%\AgentFriday` and
  does not touch `%USERPROFILE%\.friday` or the passphrase in Windows
  Credential Manager. [Updating](docs/user-guide/updating-and-uninstalling.md)
- **The update check now asks.** New installs are asked at first run whether
  Friday may check GitHub for a new version once a week; unanswered means off.
  An existing install keeps its current setting, and earlier versions had the
  check on by default, so if you upgrade and do not want it, turn it off in
  Settings → About. Nothing ever downloads or installs on its own.
- **The Lessac voice is no longer offered.** Its training data is licensed for
  non-commercial research only. If you already use it, it keeps working; new
  installs use Amy.
- **The phone stays off until you configure it**, and needs your own Twilio
  account.
- **Scheduled jobs may pause on approval cards.** A job that used to send or
  change something on its own now waits on a card until you give it a grant.
- **Documents need OfficeCLI**, which the installer does not yet provide; see
  [Documents](docs/user-guide/documents.md).

## Known issues

The most likely to affect you: the tray assumes port 3000; the first-run
routing screen describes "On this computer only" as falling back to the cloud,
when it actually refuses and offers the cloud; and moving your `.friday` folder
to another PC holds outward actions until you re-pin. All of them, with
workarounds, are in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
