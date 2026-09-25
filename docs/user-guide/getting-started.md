# Getting started

This page takes you from download to your first conversation on Windows. The
full installer reference, including source installs, is in
[Installation](../getting-started/installation.md).

## What you need

- Windows 10 or 11, 64-bit.
- About 8 GB of free disk for the application, plus room for any local model
  you choose (a local model is typically 7 to 19 GB).
- **For cloud-only use:** 16 GB of RAM is comfortable, and one API key (see
  [One key is enough](#one-key-is-enough)).
- **For a local model:** an NVIDIA graphics card. Friday reads your card's
  memory and offers the largest model that fits:

| Graphics memory | Model Friday offers |
|---|---|
| Less than about 4.3 GB, or no NVIDIA card | None by default; cloud is recommended. With 16 GB of RAM or more, Friday can run the smallest model on the CPU; its speed has not been measured. |
| About 4.3 GB | Gemma 4 E2B |
| About 5.5 GB | Gemma 4 E4B |
| About 10 GB (a 12 GB card) | Gemma 4 12B |
| About 19.5 GB (a 24 GB card) | Gemma 4 26B |

AMD and Intel graphics cards are not detected. Local image generation needs an
NVIDIA card with about 8.5 GB or more.

### One key is enough

If Friday thinks in the cloud, she needs exactly one AI key. Either of these
works on its own; you do not need both.

- **Anthropic (recommended).** Claude, from the company that makes it. You pay
  Anthropic for what you use. Get a key at
  <https://console.anthropic.com/settings/keys>.
- **OpenRouter (the alternative).** One account that reaches Claude and many
  other models. You buy credit up front and pay per use. Get a key at
  <https://openrouter.ai/keys>.

With that one key, chat, tools, briefings, the front page, scheduled jobs and
research all work. With an OpenRouter key and no Anthropic key, Friday uses
the same Claude model through OpenRouter. If you have both, Anthropic is used.

Paste the key into the secure field on its card in the setup chat, or in
**Settings → Accounts & Keys**. It is stored encrypted on this PC and never
goes into a conversation. Friday checks the key with one tiny request as soon
as you save it and tells you whether she can think.

## Install

1. Download `AgentFriday-Setup-<version>.zip` from the
   [latest release](https://github.com/FutureSpeakAI/Agent-Friday/releases/latest).
2. Unzip it anywhere and double-click **Install Agent Friday.cmd**. It needs no
   administrator rights; if Windows asks for administrator access, say no. The
   scripts are not code-signed, so SmartScreen may warn you first.
3. Answer the installer's questions. The main one is **how Friday should
   think**: a cloud key only, or also a local model sized to your card.
   Friday installs into `%LOCALAPPDATA%\AgentFriday`, with its own private
   copy of Python. Ollama is installed only if you choose a local model.
4. The installer asks whether Friday should start when you sign in (default:
   no), then runs the setup questions in the terminal and offers to start
   Friday.

Your data never goes in the program folder. It lives in `.friday` in your user
folder (`%USERPROFILE%\.friday`), which the installer and updates never touch.

## First run

The first time you open Friday it asks a few questions before anything else.
The same questions appear in the terminal if you set up there.

1. **Before anything else.** What Friday writes down (a wiki, your
   conversations, a map of how things connect) and where: the `.friday`
   folder on this PC.
2. **A passphrase for the private part.** Finance, health, legal and family
   records live in the vault and are encrypted with this passphrase. You can
   skip it and set it later in Settings. **If you lose it, those files cannot
   be recovered.** Put it in your password manager.
3. **Where your words go.** Cloud, on this computer only, or both. Nothing is
   pre-selected. If you choose cloud, a further screen says plainly what the
   cloud provider will see and what Friday will refuse to send.
4. **The part about other people.** Friday will hold notes about people you
   mention, who did not agree to it. In cloud mode their details travel with
   yours. You can make Friday forget a person from Contacts.
5. **Checking for new versions.** Check once a week, or don't check. Nothing is
   pre-selected, and nothing downloads on its own either way.

Then the **setup chat**. Friday introduces herself and asks, one short message
at a time: what to call you and what to call her; who this Friday is for and a
starting profile, with a hardware check that can download a local model;
which of your accounts and keys to connect, each card saying exactly what it
asks for; whether to look you up on the public web (off unless you say yes);
a few questions about how you like to be spoken to; and the speaking style
that comes out of your answers, which you can tune with sliders. You can type
or tap an answer, skip anything, and leave with **Set up later** at any
point. It works with no model and no key at all.

If you close Friday partway through, it picks up where you left off. Anything
you skipped waits in **Settings → Accounts & Keys → Setup checklist**, and
**Settings → General → Your profile** can run the chat again. What each part
does and what is stored where: [The setup chat](setup-chat.md).

## Opening Friday

- The desktop shortcut **Agent Friday** starts Friday and opens
  `http://localhost:3000` in your browser.
- If Friday starts when you sign in, it runs quietly in the system tray. Use
  the tray menu's **Open Friday Desktop**.
- Friday listens only on this PC (`127.0.0.1`). Nothing on your network can
  reach it unless you change that deliberately.

### Open Friday at a local address

Instead of `localhost:3000`, you can open Friday at `https://agent.friday` (or
`agent.<your agent's name>`). It is off by default. In **Settings → General →
Local address**:

1. **Add agent.&lt;name&gt; to this PC.** Adds the name to the Windows hosts
   file, pointing at this PC only. Windows asks for permission.
2. **Turn on Friday's local address.** Friday starts listening on ports 443
   and 80 of this PC.
3. **Enable secure local address → Trust Friday's certificate.** Friday creates
   a certificate authority that can only vouch for that one name, and Windows
   shows a security warning with a thumbprint; compare it with the one on the
   card before you accept. This covers your Windows account only. Edge and
   Chrome use it; Firefox does not.

Until step 3 is done, the plain `http://agent.<name>` address works but the
microphone and camera do not; they keep working at `localhost`. Each step has
an undo button. Google sign-in always completes on `localhost`.

## A tour

The dock at the bottom opens workspaces, grouped as:

- **Life:** News, Messages, Calendar, Family, Health, Finance
- **Work:** Career, Contacts, Code, Sites, Draft, Content
- **System:** Knowledge, Trust, Studio, Marketplace, Workflows, System,
  Settings

Chat is always available from the chat panel, and any conversation can be
undocked into its own window. Every workspace except Settings can open in its
own browser tab. Make the window very small and the desktop becomes a floating
widget.

Every reply says which model answered it. Settings → Models shows, for each
job, which model does it and whether it runs on this PC or in the cloud.

## Next

- [Approvals and receipts](approvals-and-receipts.md): what Friday asks before
  it acts, and the record it keeps.
- [Privacy: local and cloud](privacy.md): what leaves your PC and when.
- [Mail](mail.md), [Calendar](calendar.md), [Voice](voice.md),
  [Documents](documents.md), [Scheduled jobs](scheduled-jobs.md),
  [Phone](phone.md).
- [Backup and restore](backup-and-restore.md) and
  [Updating and uninstalling](updating-and-uninstalling.md).
