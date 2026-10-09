# Settings

Settings is where you change how Agent Friday™ behaves. Open it from the dock.
A rail down the left lists the groups. The heading above each group says what
it holds. Settings is the one workspace that does not open in its own browser
tab.

The groups, top to bottom:

1. [General](#general)
2. [Voice](#voice)
3. [Models](#models)
4. [Spending](#spending)
5. [Privacy & Data](#privacy--data)
6. [Connections](#connections)
7. [Appearance](#appearance)
8. [Advanced](#advanced)
9. [About](#about), set apart at the bottom of the rail

A control that spends money or lets data leave your PC is never turned on for
you. Where a setting needs your explicit yes, its description says what leaves.
Changes to some settings made by Friday on your behalf ask first on a card of
their own; see [Approvals and receipts](approvals-and-receipts.md).

Other pages link here: a banner at the top of every group appears when file
permissions are paused, and sends you to **Privacy & Data, File access**.

## General

- **Setup checklist.** Everything Friday can connect to, with what each one asks
  for. Anything you skipped in the setup chat waits here.
- **Identity.** The name Friday calls herself in chat and voice, and a
  free-text description of her personality. The personality saves when you click
  away.
- **Workspaces.** **Show all workspaces** offers every workspace in the dock.
  Off shows the everyday set. If you arrange the dock yourself in Appearance,
  your arrangement wins.
- **Your profile.** What you told Friday during setup. You can edit it and
  choose **Save and rebuild my style**, or run the setup chat again (see
  [Setup chat](setup-chat.md)). A key pasted here is refused, with a pointer to
  Connections. **Research on you** is optional, runs only on what you type, and
  shows findings for line-by-line review before anything is kept.
- **Muted notifications.** The kinds of notice you have muted.

## Voice

- **Voice engine.** Choose how Friday listens and speaks, and the Gemini Live
  model, the Gemini voice and the on-device Piper voice. See [Voice](voice.md).
- **Local voice models.** Every downloadable piece of on-device voice, whichever
  mode is selected. Nothing downloads until you confirm its size. See
  [Local voice models and the GPU option](local-voice-gpu-tier.md).
- **Conversation style.** **Answer depth** and **Speaking pace**, applied from
  the next voice session.
- **Listening.** What happens when you talk over her, who is talking when several
  people are in the room, how long a silence ends your turn, and
  push-to-transcribe (the shortcut and how long to hold it).
- **What voice can't do, and why.**
- **Gemini Live options**, shown for the Gemini engine only: expressive speech,
  keeping long sessions going, using tools while talking, speaking style and
  language.
- **Calls.** Video-call mode, which needs the camera, the microphone and most of
  the computer's memory.

## Models

- **What answers you.** The model that will answer your next message, where it
  runs, what it costs, and the routing mode: **Local only**, **Local preferred**
  or **Cloud only**. The rows show your local model, your deep model and your
  cloud model. These correspond to the **Fast responder** and **Deep thinker**
  you picked in setup (see [Getting started](getting-started.md)), and each row
  says whether it is ready and why if not.
- **Model for each job.** Click a job to choose its model. The list shows only
  models that can do that job. Voice is chosen in Voice.
- **Your stack**, **Find a model**, **Local models** (what your PC can run,
  installed or not, with a fetch button that confirms the size first) and a
  **Model browser** that searches every connected provider. Keys are added in
  Connections.

## Spending

What cloud models have cost: **Spend**, then breakdowns by model, prompt cache,
provider, kind, workspace, key and codebase, and scheduled jobs this month.

- **Budget alerts** are off until you switch them on. They warn at 80 percent of
  a limit and alert at 100 percent.
- **Hard stop** is also off by default. When on, and an enabled period's spend
  reaches its limit, Friday refuses every further cloud call until you raise the
  limit or switch it off. Local models keep working. Every halt is logged and
  notified.

## Privacy & Data

- **Vault.** **Keep vault content off the cloud** strips vault-tier notes from
  cloud prompts and runs questions that touch them on the local model. If no
  local model is serving, such questions are refused instead of leaking.
- **Cloud consent**, **Wiki sections kept off the cloud** and **Privacy check**,
  which shows which privacy layers are running now.
- **File access.** Which files and folders cloud models may read. See
  [File grants](file-grants.md).
- **Library.** Library search, cloud answers (off by default) with a character
  cap, reading on battery, and whether the knowledge graph learns from your
  documents. See [Library](library.md).
- **What needs your sign-off**, **Scheduled jobs: what they may do on their
  own**, **Stored keys**, **Agent workspaces** and **Computer control**
  (**Allow computer control**). See [Scheduled jobs](scheduled-jobs.md).
- **Conversation logging.** **Log conversations and tool use**, **Go off the
  record** (nothing about your conversations is written to disk until you turn it
  off), the artifact panel beside the chat, and how long the log is kept.
- **Your data.** Export the conversation log, export all your data as a ZIP, or
  erase everything. Erasing asks you to type a confirmation and cannot be undone.
  See [Backup and restore](backup-and-restore.md) and [Privacy](privacy.md).

## Connections

- **Publishing channels** for Media posts. See [Media](media.md).
- **Model providers.** The keys for cloud models.
- **Google accounts.** Mail, calendar and files. Tokens are encrypted on your PC
  and never sent to a cloud model. See [Mail](mail.md) and
  [Calendar](calendar.md).
- **Phone.** Twilio account, your cell number, who Friday may contact, behaviour
  and costs. See [Phone](phone.md).
- **Signing PDFs**: a signature image and a signing certificate. See
  [PDF documents](pdf-documents.md).
- **Friday's browser**: its window, and sign-ins and cookies.
- **Codebase settings** for chats started with **+ Codebase**: which model does
  small and large edits, and whose key pays. See [Chat and projects](chat-hub.md).

## Appearance

- **Display style** for your desktop presentation.
- **Dock.** Which workspaces the dock shows, and in what order.
- **Start screen.** **Show my day**, and when your day shows over the scene.
- **3D effects.** **3D dazzle** (full, subtle or off) controls glow, particles and
  motion in every 3D view. Off is the calmest and lightest on the graphics card.
- **Knowledge graph** display options.
- **Head and hand tracking**: parallax, viewing distance, depth, smoothing and
  the hand cursor's sensitivity, region and clicking. Both need the camera on.

## Advanced

Rarely needed controls and diagnostics. Nothing here is required for everyday
use.

- **Proposed work.** When Friday judges a job might be heavy, she lays out the
  steps here and asks how you want it run. **Queue** lists background work.
- **This machine** and **Provider activity** diagnostics, and **Turn limits**:
  rounds, time and a token ceiling per turn.
- **Knowledge graph indexing**: where indexing runs and nightly reindexing.
- **MCP servers** and **Platform connectors**: the tool servers Friday can call
  and the connectors this build knows.
- **Tracking diagnostics.**

## About

- **Version**, credits and links.
- **Updates.** **Check for new versions weekly** asks GitHub once a week whether
  a newer Agent Friday™ has been released. It is off unless you answered yes at
  first run or turn it on here. The check sends nothing about you or your PC, and
  Friday never downloads or installs anything by itself. There is also a **Check
  now** button. See [Updating and uninstalling](updating-and-uninstalling.md) and
  [Background network use](background-network.md).
- **System**, **Links** and **Acknowledgments**.

## Limits

- Settings opens in the dock only, not in its own browser tab.
- Some controls are hidden unless a related feature is switched on, for example
  the Gemini Live options, which appear only with the Gemini engine.
- Friday can offer to change a setting by voice or chat, but the change waits for
  your yes on its own card.
