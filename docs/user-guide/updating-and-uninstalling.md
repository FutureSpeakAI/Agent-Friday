# Updating and uninstalling

Agent Friday™ installs for your Windows account only. The program
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

Releases are ordered by a build sequence number, not by version number. The
Beta releases have lower version numbers than the 5.x releases they replace,
but a higher build sequence, so Friday never offers you an older 5.x build as an
update. There is no signed update channel: Friday never fetches or installs a
release itself. With the check off, watch the
[releases page](https://github.com/FutureSpeakAI/Agent-Friday/releases).

## Updating

1. Download the new `AgentFriday-Setup-<version>.exe` from the releases page.
2. Double-click it, exactly as for a first install.

You do not uninstall first. The same installer upgrades Beta 1.1, Beta 1.0.2,
Beta 1.0.1, Beta 1.0 and 5.x installs (including 5.14.3) in place and keeps
your vault, memory and settings. Setup refuses to replace an install with a
later release and says so without changing anything. The setup program is not
code-signed, so SmartScreen may warn; check the SHA-256 first.

Setup closes Friday if it is running. Before it changes any file, it copies
your data, vault included, to a dated folder under
`%USERPROFILE%\.friday-backups` (if the whole folder would not fit, it copies
the vault and settings). When the install finishes it checks that the
data folder still holds every file and that the vault's key files are
unchanged. If that check fails, setup says so and tells you where the backup
is. If the backup cannot be made, the update does not start.

The installer replaces the program files and leaves everything under
`%USERPROFILE%\.friday` alone, along with your vault passphrase in Windows
Credential Manager. A Friday tab you left open in your browser reloads itself
after the update. Running `Agent Friday.cmd update` in the install folder
prints these same steps.

## After you upgrade to Beta 1.2

**What is kept:** your vault and its passphrase, your wiki, conversations,
memory, settings, connected accounts and keys, and your scheduled jobs. The
vault needs nothing from you: one vault, one passphrase, even if older copies
of the passphrase disagreed.

**What you will notice:**

- **Your spending caps are kept.** New installs start with a daily spending
  cap of $2.00 on paid cloud services; an update never switches one on. If
  yours has no daily cap, one card, **New: a daily spending cap**, explains it
  and opens **Settings > Spending** to set one or keep none. A daily creation
  ceiling of $0.50 you never set, and workspace budgets left at the old
  defaults, are switched off once; any other figure you set is kept.
- **Email jobs.** Emails Friday writes are now saved as drafts in your Gmail
  Drafts unless you ask to send. Scheduled jobs and workflows you made before
  Beta 1.2 that send email **keep sending** (each send still asks you first,
  unless you pre-approved it). One notice in Activity, "Your email jobs keep
  sending", lists them. New jobs save drafts. Change a job on its card in
  Workflows: **Its emails: save as drafts in Gmail** or **send (asks first
  unless pre-approved)**.
- **The setup chat** can be run again from **Settings > General > Your
  profile > Run the setup chat again**, to answer the new questions (where
  Friday may think, your never-send list). It keeps your connections, what
  Friday has learned and your privacy choice.
- **The memory card.** The facts Friday kept about you move once onto the
  Memory card: the ones you entered or approved go on the card, and the ones
  she noted without asking wait for your review in **Settings > Privacy & Data
  > Memory card**.

**Keys pasted into chat before Beta 1.2.** From Beta 1.2, a key or password
pasted into the chat is withheld before anything keeps it. A key you pasted
into a chat **before** Beta 1.2 may still be in your old conversation history.
To delete such a message:

1. Open **Settings > Privacy & Data > Search your conversations**.
2. Search for words you typed with the key, such as the service's name or
   "key". In the results a key shows as `[credential withheld]`.
3. On the message, choose **Delete…**, then **Yes, delete it**. That removes the
   message from its conversation and from every index at once. It cannot be
   undone.

Then replace the key itself at the provider (make a new one and delete the
old one there), and paste the new one into the secure field on its card in
**Settings > Connections**. The backup the installer made under
`.friday-backups` still holds the old history; delete that backup folder once
you are happy with the upgrade. Older logs, such as the context log in
`.friday\vault\context-log\`, may also hold a key pasted before Beta 1.2;
Friday has no tool yet that cleans those, which is why replacing the key at
the provider matters most.

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
