# Frequently asked questions

## The basics

**What is Agent Friday™?**
An AI agent you install on your own Windows PC. It chats, listens and talks,
reads and drafts mail, manages a calendar, makes documents, and keeps notes
about your life in files you own. It asks before it sends, spends or deletes
anything on your behalf, unless you pre-approved that kind of send.

**Does it run entirely on my PC?**
Only if you choose that. Out of the box Friday is set to think in the cloud,
and the setup chat asks where she may think, with nothing picked for you. To
keep everything on your PC, choose **Only on this PC** and have a local model
that fits your PC. See [Privacy](privacy.md).

**What does it cost?**
Agent Friday is free software under the MIT license. If you use a cloud model,
you pay that provider for what you use. A local model costs nothing beyond
your own electricity. The Spending view in Settings shows what the cloud models you
use have cost.

**Do I need an account?**
No. There is no Friday server and no Friday account. A cloud model needs a key
from the provider you choose, and Google services need you to sign in to Google
yourself.

**Which Windows versions work?**
Windows 10 and 11, 64-bit. macOS and Linux can run the server from source
without the tray, graphics-card planning or Windows credential protection. They
are not supported platforms. See [CONTRIBUTING](../../CONTRIBUTING.md).

**Is this the same product as the Asimov's Mind plugin for Claude Code?**
No. That plugin is a separate product built for the Claude Code environment.

## Privacy

**Does Friday send anything about me to FutureSpeak.AI?**
No. Friday has no telemetry: no analytics, no crash reports, no license check.
Outbound connections happen only for things you use, such as the model
providers you configure, model downloads you approve, and the news or weather
you open. If you turn on the update check, Friday contacts `api.github.com`
once a week and sends nothing that identifies your install. The complete list
is in [Background network activity](background-network.md).

**Where is my data?**
In `%USERPROFILE%\.friday` on your PC, and nowhere else. Private records
(finance, health, legal, family) are in a vault encrypted with your passphrase.
If you lose that passphrase, those files cannot be recovered.

**What reaches a cloud model?**
Only what a cloud model needs to answer you, and only when a cloud model
answers. Choosing **Cloud for everything** is your consent to that. Your
never-send list is refused for every cloud model, always, and a key you paste
into chat is withheld. With the safeguards on (**Cloud for public things**, or
before you answer), private and sensitive content, including card and account
numbers, is held back too. The checks match patterns, so
sensitive meaning in ordinary words can get through. See
[Privacy](privacy.md).

**Why did Friday refuse something my settings allow?**
That is a bug. Friday reads your privacy settings at the start of every turn,
and a refusal that goes against them should be reported. Please
[tell us](https://github.com/FutureSpeakAI/Agent-Friday/issues).

**I pasted a key into the chat. Is it saved?**
No. Friday withholds it before anything keeps it and offers the secure field
instead. Keys pasted before Beta 1.2 may still be in old history; see
[Updating and uninstalling](updating-and-uninstalling.md#after-you-upgrade-to-beta-12).

**Can it work offline?**
With local models downloaded, chat and local voice work without the internet.
Features that need the outside world (mail, calendar, news, cloud models) need
a connection.

## Models

**What do "fast responder" and "deep thinker" mean?**
They are the two model seats chosen in setup. The fast responder answers voice
conversations and quick replies (Ternary Bonsai 1.7B by default, or Qwen3 4B
or 1.7B). The deep thinker
does the harder work (a Bonsai model). You can change both later in **Settings
> Models**.

**Why does setup recommend a model but not select it?**
Because you choose. One option in each job is labelled Recommended, and nothing
is ticked for you. Local models download only if you tick the consent box.

**How much memory do I need?**
See [Troubleshooting](troubleshooting.md#friday-is-very-slow-or-windows-runs-out-of-memory)
for a table. In short, 16 GB lets Friday run the model it is tuned for on the
processor, and a 12 GB NVIDIA card runs it at about 45 tokens per second.

**Can I use only the cloud?**
Yes. Tick **Use a cloud model instead** in setup, or choose one later, and add
a key on the **Connect your services** screen (**Settings > Connections**).
Nothing is downloaded. See [Connect your services](connections.md).

**Can I use only my own PC, with no keys?**
Yes. Choose local models in setup, choose **Only on this PC** in the setup
chat, and press **I'll just use local AI** on the Connect screen so Friday
stops suggesting cloud keys. That button alone does not change where Friday
thinks; the privacy choice does.

**Does it work with AMD or Intel graphics?**
Setup reads memory only from NVIDIA cards. A machine with another card is
planned as if it had no card, so models run on the processor.

## Control

**What will Friday do without asking?**
Read, search, draft (email drafts are saved to your Gmail Drafts; nothing is
sent), generate, and work in its own folders. Sending mail or texts, changing
your Google calendar, publishing, installing, overwriting your files and most
commands wait for your yes on a card. The exception is an email to people you
pre-approved yourself in **Settings > Privacy & Data > Pre-approved sends**,
which goes without a card and with an Undo. When Friday says she did
something, she must have a receipt for it from that turn. See
[Approvals and receipts](approvals-and-receipts.md).

**Can Friday see my screen?**
Friday sees what an open Friday workspace shows, as counts and kinds of items,
and never the details of a private workspace. It does not watch the rest of
your desktop. See [See & Touch](see-and-touch.md).

**How do I stop it?**
Quit from the tray icon, or press **I need my machine** in **Settings > Models**
to release the graphics card and pause background jobs.

## Updates and removal

**How do I update?**
Download the new setup program and run it over the old one. Your data is kept.
Setup backs it up first and checks it afterwards. You can turn on a weekly check
that tells you when a new release exists, at the first-run question or later in
Settings. The check only notifies; it never downloads anything.

**Will it offer to install an old 5.x build?**
No. Releases are ordered by a build sequence, and every 5.x build is older than
every Beta.

**Does uninstalling delete my notes?**
Not unless you say so. Uninstall asks, and the default keeps your data. See
[Updating and uninstalling](updating-and-uninstalling.md).

**Why is the installer unsigned?**
Code signing has not been set up yet, by decision for this beta. Windows
SmartScreen therefore shows **Unknown publisher**. Check the file's SHA-256
against the one on the release page, then choose **More info** and **Run
anyway**. See
[Getting started](../getting-started/README.md#3-the-smartscreen-warning).

## Reporting problems

**Where do I report a bug?**
Open an issue at
[github.com/FutureSpeakAI/Agent-Friday/issues](https://github.com/FutureSpeakAI/Agent-Friday/issues).
Security problems go through [SECURITY.md](../../SECURITY.md), never a public
issue.
