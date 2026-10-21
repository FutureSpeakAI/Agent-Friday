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
permissions are paused, and sends you to **Privacy & Data > File access**.
Saying "connect OpenRouter" (or another service) opens **Connections** on that
service's card.

## General

- **Setup checklist.** What you skipped in the setup chat, waiting for you.
- **Identity.** The name Friday calls herself in chat and voice, and a
  free-text description of her personality. The personality saves when you click
  away.
- **Workspaces.** **Show all workspaces** offers every workspace in the dock.
  Off shows the everyday set. If you arrange the dock yourself in Appearance,
  your arrangement wins.
- **Your profile.** What you told Friday during setup. You can edit it and
  choose **Save and rebuild my style**, **Run the setup chat again** (it keeps
  your connections, what Friday has learned and your privacy choice), or
  **Delete my profile**. See [The setup chat](setup-chat.md). A key pasted here
  is refused, with a pointer to Connections.
- **Research on you (optional).** Runs only on what you type, and shows
  findings for line-by-line review before anything is kept.
- **Interruptions.** How often something may interrupt you (6 an hour is the recommended choice), quiet hours (off until you turn them on; 22:00 to 07:00 is offered), and whether to follow Windows Focus Assist. See [Notifications](notifications.md).
- **Scheduled jobs.** The defaults for new jobs. **A run missed while Friday
  was closed**: **Run it once at the next start (recommended)** or **Skip it
  until its next time**. **A run held because its pinned model was
  unavailable**: **Run it once when the model is back (recommended)** or **Wait
  for its next scheduled time**. Each job can choose its own on its card in
  Workflows. See [Scheduled jobs](scheduled-jobs.md).
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
  people are in the room, whose spoken yes counts on an approval when others
  can be heard (**Spoken approvals in a room**: **Require my name** or
  **Anyone in the room**), how long a silence ends your turn, and
  push-to-transcribe (the shortcut and how long to hold it).
- **What voice can't do, and why.**
- **Podcasts: Episode length.** How long each show runs when spoken, set
  separately for The Front Page, The Briefing, The Week, The Editorial and the
  episodes you ask for: Short (about 2 minutes), Standard (about 5 minutes, the
  recommended default) or Long (about 10 minutes). Standard tells three or four
  stories in depth. A longer episode takes longer to write and speak on this
  computer; a shorter one tells fewer stories. It applies from the next
  episode. See [Podcasts](podcasts.md#how-long-an-episode-runs).
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
  says whether it is ready and why if not. Nothing is picked for you: on a new
  install no routing mode is pressed until you choose one (here or in setup),
  and the card says "Not chosen yet" and how Friday works until you do. An
  install that already had a mode keeps it.
- **Model for each job.** Click a job to choose its model. The list shows only
  models that can do that job. Voice is chosen in Voice.
- **Your stack**, **Find a model**, **Local models** (what your PC can run,
  installed or not, with a fetch button that confirms the size first) and a
  **Model browser** that searches every connected provider. Keys are added in
  Connections.

## Spending

What cloud models have cost: **Spend**, then breakdowns by model, prompt cache,
provider, kind, workspace, key and codebase, and scheduled jobs this month.

- **Spending caps.** A new install starts with a **daily cap of $2.00** on
  everything sent to paid cloud services; the section says it in one sentence,
  for example "Friday stops spending on cloud AI after $2.00 a day". Change the
  amount, switch it off, or add a **monthly cap** (off until you set one). When
  a cap is reached, paid cloud work pauses and a card says what paused, how
  much is spent against the cap, and that it can go again tomorrow (or next
  month) or as soon as you raise the cap; **Change my spending cap** opens this
  section. Nothing done so far is lost, and models on this PC keep working.
  Every pause is logged. The daily cap you pick in the setup chat is this one.
  An install from before the daily cap shipped on keeps the caps it had and
  shows one card, **New: a daily spending cap**, with a press to set it.
- **Budget alerts** are off until you switch them on. They warn at 80 percent of
  a limit and alert at 100 percent, and never pause anything.
- **Scheduled jobs without a local model.** Whether the daily creation and the
  heartbeat may use a cloud model while this PC has no local model, which
  model, and roughly what it costs.
- The stuck-model guard, which stops a model that repeats the same call without
  the conversation moving on. It looks at the pattern, never at an amount.

## Privacy & Data

- **Locks**, at the top. How your things are locked on this PC, in plain words,
  what that cannot stop, and the raw records (mail archives and statements)
  held from the cloud. See [Locks](locks.md).
- **Vault.** **Keep vault content off the cloud** strips vault-tier notes from
  cloud prompts and runs questions that touch them on the local model. If no
  local model is serving, such questions are refused instead of leaking.
- **Cloud consent**: your privacy choice from the setup chat. Here it is shown
  as "local, private" (Only on this PC), "cloud, safeguards on" (Cloud for
  public things) or "unrestricted" (Cloud for everything); "unrestricted (you
  chose Cloud only)" when the consent came from picking Cloud only in Models
  or setup. **Re-answer**
  checks this PC again and lets you change it; it changes the consent, not the
  routing mode in Models. See [Privacy](privacy.md).
- **Wiki sections kept off the cloud**, **What cloud models are sent**, and
  **Privacy check**, which shows which privacy layers are running now.
- **Meeting recordings.** See [Meetings](meetings.md).
- **File access.** Which files and folders cloud models may read. See
  [File grants](file-grants.md).
- **Your knowledge.** Start Friday on notes and documents you already have: pick
  folders in the Windows folder window or choose from places she found, with a
  size and file count for each. Reading a folder on this PC and letting cloud
  models read it are two separate choices, and the cloud one is off unless you
  turn it on. See [The setup chat](setup-chat.md) and [Library](library.md).
- **Library.** Library search, cloud answers (off by default) with a character
  cap, reading on battery, and whether the knowledge graph learns from your
  documents. See [Library](library.md).
- **Memory card.** The two short notes Friday reads at the start of every
  conversation: **About you** and **Working with you**, each with how full it
  is. **Edit**, **Delete** or **Add a line**; suggestions **Waiting for your
  review** (**Keep** or **Discard**); and **What Friday changed**, each with
  **Undo**. Your changes apply at once; hers from the next conversation. The
  switch **Review our conversations for the card** (on) lets a model on this
  PC, never a cloud model, suggest lines every 10 turns. See
  [Memory](memory.md).
- **Search your conversations.** Search everything you and Friday have said,
  typed or spoken, on this PC. Keys and passwords you pasted are withheld, and
  nothing said off the record is there. **Delete…** on a result, then **Yes,
  delete it**, removes that message from its conversation and from every index
  at once. It cannot be undone.
- **What needs your sign-off**, the Laya sections (**Laya chat pilot**, **What
  Laya learns about you**, **Instant answers (no model)**, **Safe writes (off
  until you turn them on)**) and **Scheduled jobs: what they may do on their
  own**. See [Scheduled jobs](scheduled-jobs.md).
- **Pre-approved sends.** Emails that may go without a card: to named
  addresses or an `@domain` (never "anyone", and never a free-mail domain such
  as `@gmail.com`), **For any job or chat** or **Only for one scheduled job**.
  **Voice may use pre-approvals** is off: a send asked for by voice always gets
  its card. **Undo window for emails sent with nobody watching** (120 seconds
  unless you change it, 10 to 600). **Revoke** takes a pre-approval back. See
  [Mail](mail.md).
- **Money paragraphs in email.** When an email paragraph about money would go
  to a cloud model: **Ask me each time, showing the text (recommended)** or
  **Always keep them on this PC**, with the threads and senders you said yes to
  and a **Forget** for each.
- **Deleting calendar events.** Whether **Notify guests** starts ticked when
  you delete an event (off). See [Calendar](calendar.md).
- **Friday's rules on this PC**, **Stored keys**, **Agent workspaces** and
  **Computer control** (**Allow computer control**). See
  [Backup and restore](backup-and-restore.md).
- **Knowledge indexing.** See [Your knowledge](your-knowledge.md).
- **Conversation logging.** **Log conversations and tool use**, **Go off the
  record** (nothing about your conversations is written to disk until you turn it
  off), the artifact panel beside the chat, and how long the log is kept.
- **Your data.** Export the conversation log, export all your data as a ZIP, or
  erase everything. Erasing asks you to type a confirmation and cannot be undone.
  See [Backup and restore](backup-and-restore.md) and [Privacy](privacy.md).

## Connections

- **Connect your services**, at the top: AI models, creative tools, search,
  your accounts and any MCP server, with **Test connection** on each card and
  **I'll just use local AI**. The same screen setup shows. See
  [Connect your services](connections.md).
- **Publishing channels** for Media posts. See [Media](media.md).
- **Model providers.** The keys for cloud models.
- **Google accounts.** Mail, calendar and files. Tokens are encrypted on your PC
  and never sent to a cloud model. **Default account for drafts** chooses where
  a new email draft goes when you name no account and it is not a reply. It
  starts on **Ask me**: with more than one account, Friday asks which. See [Mail](mail.md) and [Calendar](calendar.md).
- **Phone.** Twilio account, your cell number, who Friday may contact, behaviour
  and costs. See [Phone](phone.md).
- **Signing PDFs**: a signature image and a signing certificate. See
  [PDF documents](pdf-documents.md).
- **Friday's browser**: its window, and sign-ins and cookies.
- **Codebase settings** for chats started with **+ Codebase**: which model does
  small and large edits, and whose key pays. See [Chat and projects](chat-hub.md).

## Appearance

- **Display style** for your desktop presentation.
  - **Simple** keeps Home's cards beside Friday. **Hide cards**, on Friday's half
    of the screen or under **Scene & depth**, closes them: Friday takes one half
    of the screen and the chat the other. **Show cards** brings them back and puts
    the chat back as it was. The choice stays in this browser.
    Say "hide the cards" or "show the cards" to do it by voice or in chat.
    On a narrow window the cards stay where they are. While a workspace is
    open the screen is laid out for that workspace, and your choice is
    waiting when you are back on Home.
  - **Classic** keeps Friday centred in the part of the desktop nothing covers:
    what is left beside the chat tray, a task tray, notifications, or a
    workspace docked to one side. She glides there when that changes, and keeps
    moving, only more gently, when your system asks for reduced motion.
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
- **Local AI queue.** "Background AI work waits until you've been away for"
  sets how long the computer must be idle (5, 10, 15, 30 or 60 minutes;
  default 10) before background work that can wait starts. See
  [Scheduled jobs](scheduled-jobs.md#background-ai-work-waits-its-turn).
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
