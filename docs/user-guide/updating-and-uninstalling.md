# Updating and uninstalling

## Knowing when there is a new version

If you said yes to the weekly update check at first run, Friday asks GitHub
about once a week whether a newer release exists and, if so, shows a
notification with a link. It never downloads or installs anything itself. You
can turn the check on or off, or check now, in **Settings → About**. Without
it, watch the
[releases page](https://github.com/FutureSpeakAI/Agent-Friday/releases).

## Updating

1. Download the new `AgentFriday-Setup-<version>.zip` from the releases page.
2. Quit Friday (tray icon → Quit, or close its console window).
3. Unzip it and double-click **Install Agent Friday.cmd**, exactly as for a
   first install.

The installer replaces the program in `%LOCALAPPDATA%\AgentFriday` and keeps:

- everything in `%USERPROFILE%\.friday`: your data, settings, vault, keys,
  receipts and scheduled jobs, which no installer touches;
- your vault passphrase in Windows Credential Manager;
- launch files that may hold your settings (`start.bat`, `config.yaml` and
  similar) in the application folder.

You do not need to uninstall first. Read the release notes before updating;
they list anything that changes behaviour. Running `Agent Friday.cmd update`
prints these same steps.

## Uninstalling

Use **Start → Agent Friday → Uninstall Agent Friday**, or **Settings → Apps**
in Windows. The uninstaller shows what it will remove and what it will keep,
then asks you to confirm.

**Always removed:**

- the program folder, `%LOCALAPPDATA%\AgentFriday`;
- the shortcuts, the start-at-sign-in entry, and the Apps & features entry;
- caches and downloaded models under `.friday` (`local_voice`, `models\nemo`,
  `runtime\models`, `cache`, `audio-cache`, `logs`);
- the Ollama models this installer downloaded, and Ollama itself only if this
  installer installed it and you agree.

**Kept unless you ask:** the rest of `%USERPROFILE%\.friday` (your notes,
conversations, vault and settings) and the Credential Manager entries for the
vault passphrase and the governance key. The uninstaller asks separately
whether to delete your data as well; you must type `DELETE` to confirm. That
also removes the `friday-creations` folder on your desktop.

Keep a [backup](backup-and-restore.md) and your vault passphrase before
deleting your data. Deleted data cannot be recovered.

If you turned on the [local address](getting-started.md#open-friday-at-a-local-address),
undo it in Settings → General before uninstalling, so the hosts-file entry and
the trusted certificate are removed.
