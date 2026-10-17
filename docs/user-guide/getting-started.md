# Getting started

This page takes you from download to your first conversation with Agent
Friday™ Beta 1.2 on Windows. The full installer reference, including source
installs, is in [Installation](../getting-started/installation.md).

## What you need

- Windows 10 or 11, 64-bit.
- About 8 GB of free disk for the program (about 5 GB if you skip the memory
  tier). Setup stops with a plain message if the drive is too full.
- **For cloud use:** any computer that runs Windows 10 or 11, and one API key
  (see [One key is enough](#one-key-is-enough)).
- **For a local model:** 16 GB of RAM and a processor with AVX2. A graphics
  card with enough memory makes local models faster. After the download, 10 GB
  of disk must still be free.

The setup program reads your memory, graphics card and free disk, and lists
only the models that fit. If nothing fits, it says so and offers a cloud model.

### One key is enough

If Friday thinks in the cloud, she needs exactly one AI key. Either of these
works on its own.

- **Anthropic (recommended).** Claude, from the company that makes it. You pay
  Anthropic for what you use. Get a key at
  <https://console.anthropic.com/settings/keys>.
- **OpenRouter (the alternative).** One account that reaches Claude and many
  other models. You buy credit up front and pay per use. Get a key at
  <https://openrouter.ai/keys>.

With that one key, chat, tools, briefings, the front page, scheduled jobs and
research all work. With an OpenRouter key and no Anthropic key, Friday uses
the same Claude model through OpenRouter. If you have both, Anthropic is used.

Other providers work too (Google Gemini, Groq, Mistral, xAI, DeepSeek,
Together, or any OpenAI-compatible address); see
[Connect your services](connections.md). You need no key at all if you use
only a local model.

Paste the key into the secure field on its card on the **Connect your
services** screen, in the setup chat or in **Settings > Connections**. It is
stored encrypted on this PC and never goes into a conversation. Press **Test
connection** to check it works. A key pasted into the chat box instead is
withheld before anything keeps it.

## Install

1. Download `AgentFriday-Setup-1.2.0-beta.1.exe` from the
   [latest release](https://github.com/FutureSpeakAI/Agent-Friday/releases/latest).
2. Double-click it. It installs for your Windows user only and needs no
   administrator rights. The setup program is not code-signed, so Windows
   SmartScreen may warn you first.
3. On the model page, choose **how Friday should think**. Local use needs two
   models, one for each job:
   - **Deep thinker.** A Bonsai model that does the reasoning. The best one
     that fits your computer is labelled **Recommended**.
   - **Fast responder.** A voice model that answers quick questions and
     voice: Ternary Bonsai 1.7B (recommended), Qwen3 4B or Qwen3 1.7B. Its
     size includes the speech recogniser.

   The recommended choice is labelled but never pre-selected; you pick. Or
   tick **Use a cloud model instead** and add a key later.
4. For local models, tick **Download these models when Agent Friday first
   starts**. Setup itself downloads no model. Friday fetches your choices the
   first time she starts, shows the size and progress, checks every file
   against the publisher's checksum, and continues where it stopped if the
   download is interrupted.
5. Optionally tick **Start Agent Friday quietly when I sign in** (off by
   default), and finish. You get a shortcut on the Desktop and in the Start
   menu, and the last page offers **Start Agent Friday now**.

The setup program is not code-signed. If Windows shows **Windows protected
your PC** and **Unknown publisher**, check the file's SHA-256 against the
release page, then choose **More info** and **Run anyway**. See
[Getting started](../getting-started/README.md#3-the-smartscreen-warning).

Friday installs into `%LOCALAPPDATA%\AgentFriday`, with its own private copy of
Python. Your data lives in `.friday` in your user folder
(`%USERPROFILE%\.friday`), not in the program folder.

### Upgrading from 5.x or an earlier beta

Run the same installer. It stops Friday, copies your data to a dated folder
under `%USERPROFILE%\.friday-backups`, installs, and then checks the data is
unchanged. Your vault, memory and settings are kept. A newer installed release
is never replaced by an older installer. More, including what changes for you
in Beta 1.2, in [Updating and uninstalling](updating-and-uninstalling.md).

## First run

The first time you open Friday it shows a few screens before anything else.
Nothing on them is pre-selected.

1. **Before anything else.** What Friday writes down (a wiki, your
   conversations, a map of how things connect) and where: the `.friday` folder
   on this PC.
2. **A passphrase for the private part.** Finance, health, legal and family
   records live in the vault and are encrypted with this passphrase. You can
   skip it and set it later in Settings. **If you lose it, those files cannot
   be recovered.** Put it in your password manager.
3. **The part about other people.** Friday will hold notes about people you
   mention, who did not agree to it. If you let her use the cloud, their
   details travel with yours. You can make Friday forget a person from
   Contacts.
4. **When you're on a call.** Whether Friday steps back from the camera,
   microphone and memory when another app such as Zoom or Teams takes them.
   Standing back on her own is labelled recommended.
5. **Checking for new versions.** Check once a week, or don't check. Nothing
   downloads on its own either way. The check stays off until you turn it on.

Then the **setup chat**, a conversation of about twenty minutes. In order:
how you want to talk to her (talk, type, or "I use a screen reader"); an offer
to start cleaning up your inbox; her name, voice and pronouns; your name, city
and time zone; **where she may think** (**Only on this PC**, **Cloud for
public things** or **Cloud for everything**, nothing picked for you); a
never-send list; **Connect your services**; your folders; an optional public
web look-up of your work; how you like to be spoken to; a monthly spending
ceiling (only if the cloud is in play); routines (none ticked); memory and
approval rules; whether anyone else uses this PC; spoken approvals with other
people in the room; and what to do with a large local model when you quit.

You can type or tap an answer, skip anything, and leave with **Set up later**
at any point. Where a step has one, **Use the recommended answer** answers
that step only. It works with no model and no key at all. At the end, **What
we set up** lists every choice and who made it.

If you close Friday partway through, it picks up where you left off. Anything
you skipped waits in **Settings > General > Setup checklist**, and
**Settings > General > Your profile > Run the setup chat again** runs it
again. What each step does and what is stored where:
[The setup chat](setup-chat.md).

## Opening Friday

- The desktop shortcut **Agent Friday** starts Friday and opens
  `http://localhost:3000` in your browser.
- If Friday starts when you sign in, it runs quietly in the system tray. The
  tray menu's first item, **Open Agent Friday™**, opens it. The menu also has
  **Restart Server** and **Quit**.
- Friday listens only on this PC (`127.0.0.1`). Nothing on your network can
  reach it unless you change that deliberately.

### Open Friday at a local address

Instead of `localhost:3000`, you can open Friday at `https://agent.friday` (or
`agent.<your agent's name>`, or an address you choose). It is off by default.
In **Settings > General > Local address**:

1. **Add agent.&lt;name&gt; to this PC.** Adds the name to the Windows hosts
   file, pointing at this PC only. Windows asks for permission.
2. **Turn on Friday's local address.** Friday starts listening on ports 443 and
   80 of this PC. No system setting changes.
3. **Enable secure local address, then Trust Friday's certificate.** Friday
   creates a certificate authority that can vouch for that one name only, and
   Windows shows a security warning with a thumbprint; compare it with the one
   on the card before you accept. This covers your Windows account only. Edge
   and Chrome use it; Firefox does not.

Until step 3 is done, the plain `http://agent.<name>` address works but the
microphone and camera do not; they keep working at `localhost`. Each step has
an undo. Google sign-in always completes on `localhost`.

## A tour

The dock at the bottom opens workspaces, grouped as:

- **Life:** News, Messages, Calendar, Family, Health, Finance
- **Work:** Career, People, Crew, Code, Sites, Media, Library, Meetings
- **System:** Knowledge, Trust, Workflows, System, Settings

A fresh install may show only the core set of workspaces. **Settings >
General > Workspaces** and **Settings > Appearance > Dock** control which the
dock offers and in what order. Chat is always
available from the chat panel. Any workspace can also open in its own browser
tab at `/w/<name>`.

Settings has these sections, in order: **General**, **Voice**, **Models**,
**Spending**, **Privacy & Data**, **Connections**, **Appearance**,
**Advanced**, and **About** at the bottom. **Settings > Models** shows, for
each job, which model does it and whether it runs on this PC or in the cloud.
See [Settings](settings.md).

## Next

- [Approvals and receipts](approvals-and-receipts.md): what Friday asks before
  it acts, and the record it keeps.
- [Privacy: local and cloud](privacy.md): what leaves your PC and when.
- [Connect your services](connections.md), [Memory](memory.md) and
  [Locks](locks.md).
- [Mail](mail.md), [Calendar](calendar.md), [Voice](voice.md),
  [Documents](documents.md), [Scheduled jobs](scheduled-jobs.md),
  [Phone](phone.md).
- [Backup and restore](backup-and-restore.md) and
  [Updating and uninstalling](updating-and-uninstalling.md).
