# Agent Friday™ Beta 1.0.2

*FutureSpeak.AI™*

Agent Friday™ is a private AI agent that runs on your own Windows PC. Beta 1.0.2
is a hotfix after Beta 1.0.1, which followed Beta 1.0, the first release of the
app. It is a **pre-release**: it is meant to be tried, and it has rough edges,
listed below.

## Background AI work waits its turn

Background jobs that use your local model now go through one queue. That covers
wiki notes from voice chats, scheduled briefings and background tasks.

- **One at a time, and behind you.** A job never starts while you are in a chat
  or a local voice turn. A job that is already running pauses before its next
  step and carries on when you are done.
- **Work that can wait, waits for you to be away.** Deferrable jobs, such as
  wiki notes, start only after the computer has been idle for 10 minutes. You
  can change that in Settings > Advanced: "Background AI work waits until
  you've been away for" (5, 10, 15, 30 or 60 minutes).
- **Work with a time still runs on time.** The 7:00 news, the 16:00 briefing and
  the 18:00 front page do not wait for idle. They still run one at a time and
  behind you.
- **Duplicates merge.** A second request for work that is already waiting
  joins the first.
- **You can see it.** A "Local AI: N queued" chip appears in the top bar while
  work waits or runs. It opens a list with what waits and why, and a **Run
  now** and a **Cancel** for each job. **Run now** skips only the idle wait. The
  tray tooltip shows the same. Ask by voice: "what's in your queue?"

## Also in 1.0.2

- **Settings** reads never wait on a stopped Ollama. A voice turn could stall
  for seconds on the refused connection.
- A timing check in the test suite no longer fails by a hair.

## Upgrading

Run this installer over an existing Beta 1.0.1 (v1.0.1-beta.1), Beta 1.0
(v1.0.0-beta.1) or 5.x install, including 5.14.3. It upgrades in place. Before it changes anything, setup stops
Friday and backs up your data folder to a dated folder under `.friday-backups`
in your user folder, and your vault is part of that backup. When it finishes, it
checks that your data folder has every file it had before and that your vault's
key files are unchanged. Upgrades from 5.14.3, Beta 1.0 and Beta 1.0.1 are tested before each release.
Details are in [Upgrading from 5.x](#upgrading-from-5x) below.

## Download and install

Download `AgentFriday-Setup-1.0.2-beta.1.exe` (about 640 MB) from the assets
below. The same hash is in the `.sha256` file beside it.

**SHA-256:** `<SHA256 PLACEHOLDER: filled in when the release is published>`

1. Check the file. In PowerShell:
   `Get-FileHash .\AgentFriday-Setup-1.0.2-beta.1.exe -Algorithm SHA256`.
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
- Mail, calendar, documents, the Library and Media workspaces, Workflows,
  scheduled jobs and podcasts.
- Settings grouped into General, Voice, Models, Privacy & Data, Connections,
  Appearance and Advanced, with About at the bottom.
- No telemetry. The update check is off until you turn it on.

## Upgrading from 5.x

Run this installer over any 5.x release, including 5.14.3. Before it changes
anything, setup stops Friday and copies your data to a dated folder under
`.friday-backups` in your user folder. When it finishes, it checks that your
data folder has every file it had before and that your vault's key files are
unchanged. Your notes, settings and passphrase are never deleted by an update.

Beta 1.0.2 (1.0.2b1) is numerically lower than 5.14.3 and is newer. Friday and
the setup program order releases by build sequence, so a 5.x build, Beta 1.0 or
Beta 1.0.1 is never offered to this beta as an upgrade.

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
- The first local voice call after a while takes about a minute to start,
  because the voice models load onto the graphics card. Cloud voice calls
  (Gemini Live) do not pause background local-model work; only local voice
  turns do.
- It has had little time on real computers. Please report what you find at
  [github.com/FutureSpeakAI/Agent-Friday/issues](https://github.com/FutureSpeakAI/Agent-Friday/issues).

Build sequence: 101000201
