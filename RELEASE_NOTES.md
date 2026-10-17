# Agent Friday™ Beta 1.2 (update 2)

*FutureSpeak.AI™*

Agent Friday™ is a private AI agent that runs on your own Windows PC. This is
an update to Beta 1.2. It is a **pre-release**: it is meant to be tried, and it
has rough edges.

## What changed

- **Calendar and Connect your services say what went wrong in a plain
  sentence.** When Google or a connected service refuses an action, the screen
  shows a short sentence about what failed, never the technical error. The
  technical detail is kept in Friday's local log on this PC.

Everything else is as in
[Beta 1.2](https://github.com/FutureSpeakAI/Agent-Friday/releases/tag/v1.2.0-beta.1).
The full list is in the [CHANGELOG](CHANGELOG.md).

## Upgrading

Run this installer over an existing Beta 1.2, Beta 1.1, Beta 1.0.2, Beta 1.0.1,
Beta 1.0 or 5.x install, including 5.14.3. It upgrades in place, and backs up
your data folder, vault included, to a dated folder under `.friday-backups`
before it changes anything.

## Download and install

Download `AgentFriday-Setup-1.2.0-beta.2.exe` (about 653 MB)
from the assets below. The same hash is in the `.sha256` file beside it.

**SHA-256:** `<SHA256 PLACEHOLDER: filled in when the release is published>`

1. Check the file. In PowerShell:
   `Get-FileHash .\AgentFriday-Setup-1.2.0-beta.2.exe -Algorithm SHA256`.
   The `Hash` must match the line above.
2. Double-click the file. It installs for your Windows account only, needs no
   administrator rights, and brings its own Python and every package.
3. Windows SmartScreen may show **Windows protected your PC** and **Unknown
   publisher**, because the setup program is not code-signed yet. After
   checking the hash, choose **More info**, then **Run anyway**.
4. Finish setup, then Friday's first run takes it from there.

Step by step: [Getting started](docs/getting-started/README.md).

## Known issues

The full list is in [KNOWN_ISSUES.md](KNOWN_ISSUES.md). Please report what you
find at
[github.com/FutureSpeakAI/Agent-Friday/issues](https://github.com/FutureSpeakAI/Agent-Friday/issues).

Build sequence: 101020002
