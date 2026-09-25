# Agent Friday 5.14.1

*25 September 2026. FutureSpeak.AI*

**Cloud-only installs now fully work.** You can install Friday on an ordinary
Windows laptop, with no graphics card and no local AI model, give her one API
key, and everything works: chat, the morning news, the evening front page,
the briefings, daily creation and the heartbeat. Full detail is in the
[CHANGELOG](CHANGELOG.md); what is not right yet is in
[KNOWN_ISSUES](KNOWN_ISSUES.md).

---

## What's new

**One key is enough.** An Anthropic key, or an OpenRouter key if you prefer,
is all Friday needs to think in the cloud. The setup chat links to both key
pages, stores the key encrypted on your PC, checks it, and tells you plainly
whether Friday can now think.
[Getting started](docs/user-guide/getting-started.md)

**Scheduled jobs on a laptop without a local model.** Friday's scheduled jobs
used to run only on a model on your own PC, so on a laptop they never ran. The
setup chat now asks once whether they may use a cloud model, and shows what
that would cost: about $9 a month with the defaults (Claude Haiku 4.5, and the
heartbeat every four hours during the day rather than every hour). Say no, or
skip it, and they stay paused with one notice telling you where to change your
mind: Settings → Spending, which also shows the models and the estimate. On a
PC with a local model nothing changes; the jobs run there.
[Scheduled jobs](docs/user-guide/scheduled-jobs.md)

**A new Workflows screen.** Everything Friday does on her own is now one list,
in plain words: what each workflow does, when it runs ("Every weekday at
7:30 AM"), how the last run went and when the next one is, with a switch to
pause it. To make a new one, say what you want and when in your own words;
Friday writes a draft for you to check, and nothing is saved until you do.
Workflows with several steps can now run on a schedule too. Friday still asks
you before a workflow sends, posts, buys or deletes anything, and the screen
says so and shows when something is waiting for your OK.
[Scheduled jobs](docs/user-guide/scheduled-jobs.md)

**Office documents out of the box.** The installer now sets up OfficeCLI, the
engine Friday uses for Word, Excel and PowerPoint files, and checks it against
a fingerprint built into the installer before using it.

**No font requests.** Friday's typefaces now come with Friday, as exactly the
files Google Fonts served, so the app looks the same and no longer asks Google
for them on every page load.

## What changed

- **The heartbeat no longer runs in the cloud behind your back.** A fresh
  install used to run it every hour on a cloud model. It now follows the same
  rule as the other scheduled jobs.
- **A slow laptop never holds up an approval.** If Friday's second opinion
  takes more than a couple of seconds on your PC's processor, the simpler check
  decides, and Settings says how often that happened.
- **Dictation on a laptop** uses a smaller speech model on the processor, so
  Alt+T stays quick without a graphics card.
- **A new install is never held for "re-confirm the rules"**; that only
  applies when a `.friday` folder is moved from another PC.
- **The file-picker button and the approval cards look as they did before
  5.14.0.**

## Upgrade notes

- **Your data and your vault passphrase are preserved.** Run the new installer
  over the old one. [Updating](docs/user-guide/updating-and-uninstalling.md)
- **On a PC without a local model** you are asked once about scheduled jobs in
  the cloud; until you answer they stay paused, as before.

## Known issues

The most likely to affect you: moving your `.friday` folder to another PC
holds outward actions until you re-confirm the rules in Settings. All known
issues, with workarounds, are in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
