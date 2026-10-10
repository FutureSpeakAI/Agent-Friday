# Installation reference

This page is the detail behind [Getting started](README.md): what the Agent
Friday™ setup program does, where it puts things, and the options for
installing without the wizard. Install from source is in
[CONTRIBUTING](../../CONTRIBUTING.md).

## What setup does

`AgentFriday-Setup-1.0.2-beta.1.exe` is one file of about 640 MB. It installs
for the current Windows account, needs no administrator rights, and sends
nothing anywhere. It carries:

- its own copy of Python, so nothing has to be installed first;
- Friday's files;
- a pre-built wheel for every Python package, which it installs without the
  internet.

It does not carry model weights. The model page records your choice and your
consent in `first-run.json` in the install folder, and Friday downloads the
files the first time she starts. See [Getting started](README.md#4-run-setup).

Two parts are still fetched while setup runs: the document engine (OfficeCLI)
and the checkpoint for the judgment model (about 800 MB, and only when the
memory tier is installed). The sentence-embedding model is the one other
optional download. The embedding model is lazy and announced:
`all-MiniLM-L6-v2` arrives on first use, with a notification saying what is
being fetched and from where, unless setup's memory tier already fetched it.

Setup needs about 8 GB of free disk for the program (about 5 GB if you skip the
memory tier), before any model files.

## Where things go

| | Location |
|---|---|
| Program, Python, packages | `%LOCALAPPDATA%\AgentFriday` |
| Your data (wiki, conversations, settings, vault, receipts) | `%USERPROFILE%\.friday` |
| Setup log | `%LOCALAPPDATA%\AgentFriday\logs\setup.log` |
| Server log | `%USERPROFILE%\.friday\friday.log` |
| Backup made before an upgrade | `%USERPROFILE%\.friday-backups` |
| Desktop and Start menu shortcuts | **Agent Friday**, with the rocket icon |
| Start-at-sign-in switch | Start menu, **Agent Friday**, **Start Friday when I sign in** |

The Desktop shortcut is created wherever Windows keeps your Desktop, including
under OneDrive.

## Upgrading

Run the new installer over the old one. Setup compares builds by their build
sequence, so a 5.x, Beta 1.0 or Beta 1.0.1 installation is always treated as older than Beta 1.0.2, and
Beta 1.0.2 never offers to "update" you back to an older build. Before it changes
anything, setup stops Friday and copies the data folder to a dated folder under
`.friday-backups`. When it finishes, it checks that the data folder has every
file it had before and that the vault's key files are unchanged. A newer
version already installed is never replaced by an older setup program.

## Silent install

For managed machines and test runs, the setup program accepts these options:

```
AgentFriday-Setup-1.0.2-beta.1.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=setup.log
```

| Option | Meaning |
|---|---|
| `/ModelsCloud=1` | Use a cloud model and download nothing. This is the default. |
| `/ModelFast=<id>` and optionally `/ModelFastPacking=<p>` | Choose the fast responder. |
| `/ModelDeep=<id>` and optionally `/ModelDeepPacking=<p>` | Choose the deep thinker. |
| `/ConsentDownload=1` | Required with `/ModelFast` and `/ModelDeep`. |
| `/SkipMemory=1`, `/SkipJudgment=1` | Skip the large optional parts. |
| `/AllowNetwork=1` | Let pip use the internet as well as the carried wheels. |

A silent uninstall keeps your data unless `/REMOVEDATA=1` is given.

## The SmartScreen warning

The setup program is not code-signed, so Windows SmartScreen shows **Windows
protected your PC** and **Unknown publisher**. Check the file against the
published SHA-256, then choose **More info** and **Run anyway**. The steps are
in [Getting started](README.md#2-check-the-file-optional-and-worth-doing).

## Uninstalling

Use **Apps > Installed apps** in Windows Settings, or **Uninstall Agent Friday**
in the Start menu folder. Uninstall asks whether to keep your notes,
conversations and settings, and the passphrase that opens them. The default
is to keep them. See [Updating and uninstalling](../user-guide/updating-and-uninstalling.md).
