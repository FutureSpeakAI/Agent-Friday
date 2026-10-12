# Agent Friday™ Beta 1.1

*FutureSpeak.AI™*

Agent Friday™ is a private AI agent that runs on your own Windows PC. Beta 1.1
follows Beta 1.0.1 and Beta 1.0, the first release of the app. It
is a **pre-release**: it is meant to be tried, and it has rough edges, listed
below.

## What's new

- **One scheduler for your local models.** Every call to a local model waits
  its turn in the order voice, chat, Local API, jobs you asked for, background.
  No model loads if it would leave less than 4 GB of memory free; the job waits
  and the Local AI chip says why. A second GPU job waits for the card instead of
  being refused. Say "cancel the podcast job" to take a waiting job out of the
  line.
- **Four places, one rule each.** The top-bar chip opens **Needs you** (only
  what waits for your decision), **Activity** (every task, whoever started it),
  **Updates** (a digest you pull) and **Receipts & traces** (pulled from an
  item's **Why?**). At most 6 things an hour may pop up, speak or text
  (`interruption_budget_per_hour`), quiet hours are there when you turn them on,
  and approvals of one kind can show as one batch card.
- **Meetings.** The recorder and past meetings have their own workspace. A
  recording starts only from the start card, after your consent and a confirm,
  and can record both sides of a call. A notes pad takes your bullets during the
  call, and afterwards every note line cites the transcript. The tray icon
  carries a red dot while a meeting records. Meetings become searchable only
  after you say yes.
- **Your folders, read while you are away.** A folder you add is read as one
  Local AI queue job, only while the PC is idle, and picked up where it stopped
  after a restart. Friday notes each document's type, how sensitive it is and
  whether it is a copy, and a local model drafts summaries and facts for your
  review. Nothing is published without you.
- **What Laya learns about you** (on by default; Settings > Privacy & Data).
  When you correct her in a local voice call, or in typed chat while she picks
  the tool, she keeps your words sealed in your vault on this PC and learns your
  phrasing. A report card shows what she learned and what she turned down, with
  undo, forget, reset and roll back.
- **Laya's safe writes** (off until you turn them on; Settings > Privacy & Data >
  Safe writes). Laya can raise the approval card for a mail change you can undo
  (archive, mark read or unread, star, unstar, label, move to an existing label)
  on the rows you pointed at. You approve every card, and Undo puts back the
  labels Friday changed.
- **Cloud voice is asked for, and offered back once** (Settings > Voice).
  Moving voice to the cloud asks "just this call, or always?". After an always,
  when local voice is measured and ready again, Friday offers once to switch
  back. Nothing switches silently in either direction. Settings > Voice also says
  whether local voice can run on this PC, with **Measure now**.
- **Local voice starts faster.** The ear, the voice and the fast reply model
  load at the same time, and the page shows an estimate from this PC's recent
  starts. In a call, "open the second one" or "select these" are
  settled against the rows on your screen.
- **Exact mail Undo.** Undo now puts back only the labels each message really
  gained or lost, on only those messages, and says so when it cannot.

## Also in 1.1

- A plain "yes" typed on Friday's page runs the action Friday is waiting on
  (`reflex_approval_replies`, on by default).
- "Open the X" without a model (`reflex_open_enabled`, off until you choose).
- Laya can pick the tool for a typed lookup (`chat_tool_routing`, unchosen by
  default).
- Local modes call the cloud only when you asked. When a local answer fails,
  Friday offers to try again locally, give a shorter version or send it to
  Claude, with nothing preselected.
- Podcast lengths are spoken time: Short about 2 minutes, Standard about 5 (the
  default) and Long about 10, each story written from its full article where it
  can be read.
- One media player for episodes and recordings; the avatar follows the audio.
- The avatar stays centred in the visible part of the desktop, and Simple has a
  **Hide the cards** control.
- Mail reads time out with an error and **Retry** instead of showing an empty
  inbox.
- Settings survive a read that meets a save; a failed read can never be saved
  over your settings.
- Ollama models that run in Ollama's cloud (`:cloud` or `-cloud`) are checked
  like any other cloud model and are sent no images.
- Library search stays fast with thousands of documents.

The full list is in the [CHANGELOG](CHANGELOG.md).

## Upgrading

Run this installer over an existing Beta 1.0.2 (v1.0.2-beta.1), Beta 1.0.1
(v1.0.1-beta.1), Beta 1.0 (v1.0.0-beta.1) or 5.x install, including 5.14.3. It
upgrades in place. Before it changes anything, setup stops Friday and backs up
your data folder to a dated folder under `.friday-backups` in your user folder,
and your vault is part of that backup. When it finishes, it checks that your
data folder has every file it had before and that your vault's key files are
unchanged. Upgrades from 5.14.3, Beta 1.0 and Beta 1.0.1 are tested
before each release. Details are in [Upgrading from 5.x](#upgrading-from-5x)
below.

## Download and install

Download `AgentFriday-Setup-1.1.0-beta.1.exe` (about ⟨SIZE⟩ MB) from the assets
below. The same hash is in the `.sha256` file beside it.

**SHA-256:** `<SHA256 PLACEHOLDER: filled in when the release is published>`

1. Check the file. In PowerShell:
   `Get-FileHash .\AgentFriday-Setup-1.1.0-beta.1.exe -Algorithm SHA256`.
   The `Hash` must match the line above.
2. Double-click the file. It installs for your Windows account only, needs no
   administrator rights, and brings its own Python and every package, so you
   need nothing else.
3. Windows SmartScreen may show **Windows protected your PC** and **Unknown
   publisher**, because the setup program is not code-signed yet. Code signing
   is the next priority. After checking the hash, choose **More info**, then
   **Run anyway**.
4. On the model page, choose how Friday thinks (below), then finish. You get a
   shortcut on the Desktop and in the Start menu.

Step by step: [Getting started](docs/getting-started/README.md).

## Choose how Friday thinks

Setup reads your memory, graphics card and free disk, which stay on your
computer, and shows only the models that fit, for two jobs:

- a **fast responder** for voice and quick replies, Ternary Bonsai 1.7B by
  default (Qwen3 4B or 1.7B as alternatives), which comes with its speech
  listener; and
- a **deep thinker** for the harder work, from the Bonsai family.

One choice in each job is labelled *Recommended*. Nothing is chosen for you.
You can choose a cloud model instead and add its key later in Settings.

Setup downloads no model. If you tick the consent box, Friday fetches your
choices the first time she starts, shows the size and progress, checks every
file against the publisher's checksum, and resumes if the connection drops.

## What is in this release

- Chat with local and cloud models, with every reply labelled by the model that
  answered, and a sidebar of conversations grouped into projects.
- Voice on your own PC or in the cloud, with downloadable, pinned and verified
  local voice models under Settings > Voice, and Alt+T dictation in any app.
- See & Touch: Friday sees what your open workspace shows, points at rows with
  numbered badges, ticks or filters what you mean, and fills in a field you can
  see. Acting on what is ticked still needs your approval, and trash and
  archive always ask.
- Mail, calendar, meetings, documents, the Library and Media workspaces,
  Workflows, scheduled jobs and podcasts.
- Settings grouped into General, Voice, Models, Privacy & Data, Connections,
  Appearance and Advanced, with About at the bottom.
- No telemetry. The update check is off until you turn it on.

## What is not in this release

- **The installer is not code-signed.** SmartScreen warns, as described above.
  Check the SHA-256 before you run it.
- **There is no signed update channel.** Friday checks GitHub at most once a
  week and tells you when a newer release exists; it never downloads or
  installs one itself. Download a new release from the releases page and
  check its hash.
- **Phone pairing is not in this release.**

## Upgrading from 5.x

Run this installer over any 5.x release, including 5.14.3. Before it changes
anything, setup stops Friday and copies your data to a dated folder under
`.friday-backups` in your user folder. When it finishes, it checks that your
data folder has every file it had before and that your vault's key files are
unchanged. Your notes, settings and passphrase are never deleted by an update.

Beta 1.1 (1.1.0b1) is numerically lower than 5.14.3 and is newer. Friday and
the setup program order releases by build sequence, so a 5.x build, Beta 1.0,
Beta 1.0.1 or Beta 1.0.2 is never offered to this beta as an upgrade.

## Uninstalling

Uninstall asks whether to keep your notes, conversations and settings. Keeping
them is the default.

## Known issues

The full list is in [KNOWN_ISSUES.md](KNOWN_ISSUES.md). The ones most likely to
matter:

- The setup program is **not code-signed**, so SmartScreen warns.
- A few parts are still fetched during setup: the document engine (OfficeCLI)
  and the judgment model's checkpoint. Model weights download on first start.
- A large local model needs a lot of memory. The 27B Bonsai model needs 16 GB of
  RAM to run on the processor, where it is slow (an estimate of 3 to 6 tokens
  per second), and a 12 GB NVIDIA card for fast, long-context use.
- The optional, more accurate "turbo" speech listener is not offered yet.
- The first local voice call after a while is slow to start,
  because the voice models load first. Cloud voice calls (Gemini Live) do not
  pause background local-model work; only local voice turns do.
- Laya's safe writes are off and await their separate safety review.
- It has had little time on real computers. Please report what you find at
  [github.com/FutureSpeakAI/Agent-Friday/issues](https://github.com/FutureSpeakAI/Agent-Friday/issues).

Build sequence: 101010001
