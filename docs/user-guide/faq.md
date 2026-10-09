# Frequently asked questions

## The basics

**What is Agent Friday™?**
A private AI agent that runs on your own Windows PC. It chats, listens and
talks, reads and drafts mail, manages a calendar, makes documents, and keeps
notes about your life in files you own. It asks before it does anything outward
on your behalf.

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
answers. Before anything is sent, an egress gate on your PC withholds private
and sensitive content, and if the gate fails nothing is sent. The gate matches
patterns, so sensitive meaning in ordinary words can get through. See
[Privacy](privacy.md).

**Can it work offline?**
With local models downloaded, chat and local voice work without the internet.
Features that need the outside world (mail, calendar, news, cloud models) need
a connection.

## Models

**What do "fast responder" and "deep thinker" mean?**
They are the two model seats chosen in setup. The fast responder answers voice
conversations and quick replies (a Qwen3 4B or 1.7B model). The deep thinker
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
a key under **Settings > Connections**. Nothing is downloaded.

**Does it work with AMD or Intel graphics?**
Setup reads memory only from NVIDIA cards. A machine with another card is
planned as if it had no card, so models run on the processor.

## Control

**What will Friday do without asking?**
Read, search, draft, generate, and work in its own folders. Sending mail or
texts, changing the calendar, publishing, installing, overwriting your files and
most commands wait for you. See
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
Beta 1.0.x.

**Does uninstalling delete my notes?**
Not unless you say so. Uninstall asks, and the default keeps your data. See
[Updating and uninstalling](updating-and-uninstalling.md).

**Why is the installer unsigned?**
Code signing has not been set up yet and is the next priority. Check the file's
SHA-256 against the one on the release page. See
[Getting started](../getting-started/README.md#3-the-smartscreen-warning).

## Reporting problems

**Where do I report a bug?**
Open an issue at
[github.com/FutureSpeakAI/Agent-Friday/issues](https://github.com/FutureSpeakAI/Agent-Friday/issues).
Security problems go through [SECURITY.md](../../SECURITY.md), never a public
issue.
