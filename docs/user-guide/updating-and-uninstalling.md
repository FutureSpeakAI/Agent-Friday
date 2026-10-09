# Updating and uninstalling

Agent Friday™ Beta 1.0 installs for your Windows account only. The program
lives in `%LOCALAPPDATA%\AgentFriday`. Your notes, conversations, settings and
vault live in `%USERPROFILE%\.friday`. The installer is not code-signed, so
Windows SmartScreen warns the first time you run it.

## Knowing when there is a new version

The update check is off until you turn it on. Friday asks at first run, and you
can change the answer at any time in **Settings > About**, where you can also
choose **Check now**.

When it is on, Friday contacts `api.github.com` about once a week and asks
whether a newer release exists. If one does, you get a notification with a link
to the release page. Friday never downloads or installs anything itself. The
request carries nothing about you or your PC.

Releases are ordered by a build sequence number, not by version number. Beta
1.0 (`1.0.0b1`) has a lower version number than the 5.x releases it replaces,
but a higher build sequence, so Friday never offers you an older 5.x build as an
update. With the check off, watch the
[releases page](https://github.com/FutureSpeakAI/Agent-Friday/releases).

## Updating

1. Download the new `AgentFriday-Setup-<version>.exe` from the releases page.
2. Double-click it, exactly as for a first install.

You do not uninstall first. The same installer upgrades a 5.x install in place
and keeps your vault, memory and settings. Setup refuses to replace an install
with a later release and says so without changing anything.

Setup closes Friday if it is running. Before it changes any file, it copies
your data to a timestamped folder under `%USERPROFILE%\.friday-backups`. When
the install finishes it checks that the data folder still holds every file and
that the vault's key files are unchanged. If that check fails, setup says so and
tells you where the backup is. If the backup cannot be made, the update does
not start.

The installer replaces the program files and leaves everything under
`%USERPROFILE%\.friday` alone, along with your vault passphrase in Windows
Credential Manager. Running `Agent Friday.cmd update` in the install folder
prints these same steps.

## What setup creates

- A **Desktop** shortcut, placed on your real Desktop even when OneDrive has
  moved it to a OneDrive Desktop folder.
- A **Start menu** folder named Agent Friday, with the program, an uninstall
  entry and a **Start Friday when I sign in** switch.
- An entry in Windows **Settings > Apps**.
- Optionally, a start-at-sign-in entry. Setup offers it as a tick-box that is
  off by default. It starts Friday quietly in the background.

If a shortcut cannot be created, setup says so and tells you to start Friday
from `Agent Friday.cmd` in the install folder.

Setup downloads no models. If you ticked the consent box on the model page, the
chosen models download when Friday first starts, with progress, resume and a
checksum check. See [The setup chat](setup-chat.md).

## Uninstalling

Use **Start > Agent Friday > Uninstall Agent Friday**, or **Settings > Apps** in
Windows. The uninstaller asks whether to keep your notes, conversations and
settings, and the passphrase that opens them. **Keeping them is the
default.** Choosing to delete them asks a second time, with No as the default
button, and the deletion cannot be undone.

**Always removed:**

- the program folder, `%LOCALAPPDATA%\AgentFriday`;
- the shortcuts, the start-at-sign-in entry, the Start menu folder and the
  Apps entry;
- downloaded models and caches under `.friday` (`local_voice`, `models\nemo`,
  `runtime\models`, `cache`, `audio-cache`, `logs`), and the sentence-embedding
  model in the Hugging Face cache;
- any Ollama model that an earlier install pulled and recorded. Ollama itself
  is left in place.

**Kept unless you choose to delete your data:** the rest of
`%USERPROFILE%\.friday` and the Credential Manager entries for the vault
passphrase. The vault and its passphrase are kept or removed together, because a
vault without its passphrase cannot be opened. Deleting your data also removes
the `friday-creations` folder on your Desktop.

Keep a [backup](backup-and-restore.md) and your vault passphrase before
deleting your data.

**The local address.** If you turned on the
[local address](getting-started.md#open-friday-at-a-local-address), the
uninstaller offers to undo it:

- **The hosts-file entry.** When Friday's marked entry is present, the
  uninstaller asks. Removing it needs administrator permission, so Windows shows
  its permission prompt. Only the lines between Friday's markers are removed.
- **The trusted certificate.** The uninstaller removes the certificate authority
  Friday made from your user's trusted roots. Windows asks you to confirm.

Anything that could not be removed is reported at the end with how to remove it
by hand.

## Silent install and uninstall

For managed machines, run the installer with `/VERYSILENT /SUPPRESSMSGBOXES
/NORESTART`. A silent install uses a cloud model and downloads nothing unless
you pass the model options and `/ConsentDownload=1`. A silent uninstall keeps
your data unless you pass `/REMOVEDATA=1`, and leaves the hosts-file entry in
place.
