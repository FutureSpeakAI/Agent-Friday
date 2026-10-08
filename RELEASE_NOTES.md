# Agent Friday™ Beta 1.0

*FutureSpeak.AI*

This is the first beta of Agent Friday™ on its new numbering. It is a
**pre-release**: it is meant to be tried, and it will have rough edges.

## One file to install

Download `AgentFriday-Setup-1.0.0-beta.1.exe` below and double-click it.

- It installs for you alone. It does not ask for administrator rights, and it
  sends nothing anywhere.
- It brings its own Python and everything Agent Friday needs to start. You do not
  need Python, git, or a terminal.
- It is **not code-signed**, so Windows SmartScreen may ask you to confirm.
  Choose *More info*, then *Run anyway*, after checking the file against the
  `.sha256` file published beside it.

## Choose how Agent Friday thinks

Setup looks at your computer (memory, graphics card, free disk space; it stays on
your computer) and shows only the Bonsai models that fit it, for two jobs:

- a **fast responder**, for voice and quick replies; and
- a **deep thinker**, for the harder work.

One choice is marked *Recommended*. Nothing is chosen for you. Or choose a cloud
model and add a key later in Settings.

Setup downloads **no** model. If you tick the box, Agent Friday fetches your
choices the first time she starts, shows the progress, checks every file against
the publisher's checksum, and picks up where she stopped if the connection drops.

## Updating from 5.x

You can install this over 5.14.3 or any earlier 5.x release. Before changing
anything, setup stops Agent Friday and copies your data to a dated folder under
`.friday-backups` in your user folder. When it finishes it checks that your data
folder has every file it had before and that your vault's key files are
unchanged. Your notes, settings and passphrase are never deleted by an update.

Beta 1.0 is numerically lower than 5.14.3 (1.0.0b1 against 5.14.3), but it is
newer. Agent Friday and the setup program order releases by a build sequence, so
5.14.3 is never offered to this beta as an upgrade.

## Uninstalling

Uninstall asks whether to keep your notes, conversations and settings. Keeping
them is the default.

## Known limits of this beta

- The fast responder you choose is recorded and downloaded, but spoken
  conversation still runs on its own small model for now. See
  [KNOWN_ISSUES](KNOWN_ISSUES.md).
- Before this release is published, an automated run installs it on a clean
  Windows machine, uninstalls it keeping the data, reinstalls it, and upgrades a
  5.14.3 install in place. It has had little time on real computers.

Build sequence: 101000001
