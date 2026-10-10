# Getting started with Agent Friday™ Beta 1.0.1

This page takes you from the download to your first conversation, and then to
voice. It assumes a Windows 10 or 11 PC and about twenty minutes. Nothing here
needs a terminal, Python or administrator rights.

## Before you start

| | Minimum | Recommended |
|---|---|---|
| Windows | 10 or 11, 64-bit | 11 |
| Memory | 16 GB for the local model Friday is tuned for. With less, setup offers a cloud model or older, lighter local models. | 32 GB |
| Free disk | About 8 GB for the program, plus the model files you choose (a deep thinker is about 6 GB) | More, so a second model fits |
| Processor | 4 physical cores with AVX2 | |
| Graphics card | None. The model then runs on the processor, at roughly 3 to 6 tokens per second (an estimate; it has not been measured on this class of machine). | NVIDIA with 12 GB of video memory or more. A 12 GB card measured about 45 tokens per second. |

Setup reads your computer and lists only the models that fit it, so you do not
need to work these out yourself. The model page also keeps at least 10 GB of
your disk free after the download and hides any model that would use more.

## 1. Download

Open the [latest release](https://github.com/FutureSpeakAI/Agent-Friday/releases/latest)
and download `AgentFriday-Setup-1.0.1-beta.1.exe` (about 640 MB). The file
carries its own copy of Python and every package Friday uses, so nothing else
has to be installed first.

## 2. Check the file (optional, and worth doing)

The release page publishes the file's SHA-256 checksum. In PowerShell, in the
folder you downloaded to:

```powershell
Get-FileHash .\AgentFriday-Setup-1.0.1-beta.1.exe -Algorithm SHA256
```

The `Hash` it prints must match the checksum on the release page, character for
character. If it does not, delete the file and download it again.

## 3. The SmartScreen warning

The first time you run the file, Windows may show a blue box titled **Windows
protected your PC**, with the publisher shown as **Unknown publisher**.

The box appears because the setup program is not code-signed. A signature is a
certificate that Windows can trace to a company it knows, and Beta 1.0.1 does not
have one yet. Getting one is the next priority. The warning does not mean
Windows found a problem in the file. It means Windows has no reputation record
for it. That is why the checksum in step 2 matters: it confirms the file is the
one that was published.

To continue, choose **More info**, then **Run anyway**.

## 4. Run setup

Setup installs for your Windows account only and does not ask for
administrator access. There are four decisions.

1. **Your models.** The page begins with what setup found on this computer:
   memory, graphics card and free disk. It then shows two jobs, each with the
   models that fit.
   - **Fast responder.** Answers voice conversations and quick replies. The
     recommended choice is Ternary Bonsai 1.7B (about 442 MB; it stays loaded
     beside the deep thinker). The alternatives are Qwen3 4B (better answers;
     Friday pauses the deep thinker while you talk) and Qwen3 1.7B (smaller
     than the 4B; it also stays loaded beside the deep thinker). Each choice
     includes the speech listener, and the size shown counts it.
   - **Deep thinker.** Does the harder work. It is a Bonsai model. The one
     Friday is tuned for is Bonsai 2 27B, about 6 GB. A computer with less
     memory is offered an older, lighter Ternary Bonsai model instead.
   - One choice in each job is labelled **Recommended**. Nothing is selected
     for you.
   - Or tick **Use a cloud model instead** and add a key after install. Nothing
     is downloaded in that case.
2. **Consent to the download.** If you chose local models, tick **Download
   these models when Agent Friday first starts**. The page shows the total.
   Setup itself downloads no model. Friday fetches them the first time she
   starts, shows progress, checks each file against its publisher's checksum,
   and continues where it stopped if the connection drops.
3. **Start when you sign in.** Optional, and off by default. It starts Friday
   quietly in the tray.
4. **Install.** This takes several minutes, because it sets up Friday's own
   Python and packages from the files inside the installer, with no internet
   needed for them. Two parts are still fetched while setup runs: the document
   engine (OfficeCLI) and the checkpoint for the judgment model.

Setup puts shortcuts, with the rocket icon, on your Desktop (also when OneDrive
holds your Desktop) and in the Start menu. The program is in
`%LOCALAPPDATA%\AgentFriday`. Your data is in `%USERPROFILE%\.friday`, which
installing and updating never touch.

If you already run a 5.x version, run this installer over it. Setup stops
Friday, copies your data to a dated folder under `.friday-backups` in your user
folder, installs, and then checks that your data folder has every file it had
before. Your vault, memory and settings are kept. See
[Updating and uninstalling](../user-guide/updating-and-uninstalling.md).

## 5. First start

Open Friday from the Desktop shortcut or the tray icon's **Open Agent
Friday™**. It opens in your browser at `http://localhost:3000`. If you chose
local models, the downloads begin now and a progress indicator shows them.

Friday asks a few questions before anything else: what she writes down and
where, a passphrase for the vault, where your words go (the cloud, this
computer only, or both), what it means that she holds notes about people you
mention, and whether she may check for new versions once a week. Nothing is
pre-selected. The vault passphrase protects finance, health, legal and family
records, and **if you lose it those files cannot be recovered**. Keep it in a
password manager. The [first-run page](../user-guide/getting-started.md) covers
each question.

## 6. Your first conversation

After the questions, Friday starts the **setup chat**. She asks what to call
you, checks your hardware, lists the accounts and keys she can use (each card
says exactly what it asks for), and asks a few questions about how you like to
be spoken to. Every step can be skipped, and **Set up later** is always there.
It works with no model and no key. See
[The setup chat](../user-guide/setup-chat.md).

When it ends, type in the chat panel. Start with something that does not touch
the outside world, such as asking what she can do for you, or pasting in a
paragraph and asking for a summary. Every reply says which model answered it.
Anything that would send, publish or change something waits for your approval
on a card. Read [Approvals and receipts](../user-guide/approvals-and-receipts.md)
before you connect mail or calendar.

## 7. Set up voice

Open **Settings > Voice**. There are two ways to talk to Friday.

**Cloud voice.** Choose Gemini Live and add a Gemini key under **Settings >
Connections**. Audio goes to Google while you talk.

**Local voice.** Choose Local CPU or Local GPU (NVIDIA). Listening and speaking
happen on this PC. Local voice needs a few downloaded parts. **Settings > Voice
> Local voice models** appears whichever voice mode you chose. Each part is
listed with its size and a **Download** button, which shows the size and asks
before it fetches anything. Each download is pinned to an exact version and
checked after it arrives.

The parts are the streaming speech listener and its helpers, the fast reply
model (the one you chose in setup, or another), and a few
optional pronunciation helpers. The larger, more accurate listener is not
offered yet.

Hold **Alt+T** anywhere in Windows to dictate into any app. The speech is
transcribed on your PC. See [Voice](../user-guide/voice.md).

## If something goes wrong

[Troubleshooting and FAQ](../user-guide/troubleshooting.md) covers the
SmartScreen box, a missing Desktop shortcut, slow or out-of-memory behaviour on
small machines, where the logs are, and how to back up, restore and uninstall.

## Where to go next

- [Settings](../user-guide/settings.md), section by section
- [See & Touch](../user-guide/see-and-touch.md), pointing at what you mean
- [Privacy: local and cloud](../user-guide/privacy.md)
- [The whole documentation index](../README.md)
