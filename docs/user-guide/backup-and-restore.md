# Backup and restore

Everything Friday knows is in one folder, `%USERPROFILE%\.friday`. There is no
cloud copy, so if this PC's disk fails, a backup you made is the only copy.
This page covers what to back up, the one thing that cannot be recovered, and
how to restore.

## The vault passphrase: the one thing that cannot be recovered

If you set a vault passphrase, your finance, health, legal and family records
(and any wiki sections you chose to encrypt) are encrypted with a key derived
from it. **Without the passphrase those files cannot be decrypted by anyone.
There is no reset.** A backup of the folder does not help if the passphrase is
gone.

Friday keeps the passphrase in Windows Credential Manager and in a
DPAPI-protected file, so you do not type it every day. Both copies are tied to
this Windows account on this PC and **do not move with a backup**. Keep your
own copy in a password manager.

## What to back up

Back up the whole `%USERPROFILE%\.friday` folder. It holds your wiki,
conversations, settings, the vault, keys, receipts, traces and scheduled jobs.
The largest parts are downloaded model files, which you can leave out and
download again:

- `runtime\`, `local_voice\`, `models\`, `cache\` (model weights and caches)

The program folder, `%LOCALAPPDATA%\AgentFriday`, does not need a backup; the
installer recreates it.

**Treat the backup as sensitive.** The wiki, conversations and settings are
readable files, and `security\keystore.json` holds the key that decrypts your
stored API keys and account tokens. Anyone with the backup can use those
credentials. Keep it on encrypted storage.

### Making a backup

1. Quit Friday: tray icon → Quit, or close the Friday console window.
2. Copy `%USERPROFILE%\.friday` to your backup drive.

Or use the built-in export. It has two forms.

**Data export** (the default) zips your data and leaves out every key and
credential file: the keystore root key (`security\`), stored API keys,
account sign-in tokens, the web session secret, signing keys and private TLS
keys. It also leaves out downloaded model files and caches.

```
"%LOCALAPPDATA%\AgentFriday\Agent Friday.cmd" export
```

The zip, `friday-data-export-<date>.zip`, is written to your Documents folder,
or wherever you name with `--out <folder>`. Friday refuses to write it into
its own data folder or the program folder. If Documents is synced by
OneDrive, Friday says so, because the zip will then be uploaded. Vault files
stay encrypted inside the zip.

A data export is enough to keep your wiki, conversations, settings and vault,
but **it cannot restore your stored API keys or account sign-ins**. After
restoring one, enter your keys again in Settings → Accounts & Keys and
reconnect your accounts.

**Full backup** also includes the keys and credentials, inside one file
encrypted with a passphrase you type (at least 12 characters; AES-256-GCM
with an Argon2id key):

```
"%LOCALAPPDATA%\AgentFriday\Agent Friday.cmd" export --full
```

It writes `friday-backup-<date>.fbak`. **Without that passphrase the backup
cannot be opened by anyone**, including you. It is separate from your vault
passphrase; you need both to read vault files from the backup.

## Restoring

A folder copy or a data export zip is restored by putting its `.friday`
folder back as `%USERPROFILE%\.friday`. A full backup is first turned back
into a zip:

```
"%LOCALAPPDATA%\AgentFriday\Agent Friday.cmd" decrypt-backup <file>.fbak
```

That writes `<file>.zip` next to the backup (or to `--out <folder>`). The zip
holds your keys in readable form: restore from it, then delete it.

What each kind of backup brings back:

| Backup | Data, settings, vault | Stored API keys and account sign-ins |
|---|---|---|
| Folder copy | Yes | Yes |
| Full backup (`export --full`) | Yes | Yes |
| Data export (`export`) | Yes | No: enter keys again and reconnect accounts |

### On the same PC and Windows account

Quit Friday, replace `%USERPROFILE%\.friday` with your copy, and start Friday.
The passphrase copies in Credential Manager still work.

### On a new PC, or a different Windows account

1. Install Friday with the Windows installer.
2. Before starting Friday, copy your backup to `%USERPROFILE%\.friday`.
3. Store your vault passphrase for this account:

   ```
   "%LOCALAPPDATA%\AgentFriday\Agent Friday.cmd" vault-setup
   ```

4. Start Friday.

From a folder copy or a full backup, stored API keys and account tokens come
across with the keystore. From a data export they do not. If a connected
account asks you to sign in again, do so in Settings → Accounts & Keys.

**After a move, outward actions may be held.** Friday's governance signing key
is kept in Windows Credential Manager, so a new PC or account gets a new key,
and the pinned signature of Friday's rules no longer matches. Friday then
holds outward actions (reads keep working). To accept the new key, quit Friday,
delete `%USERPROFILE%\.friday\governance\claws.pin.json`, and start Friday; it
pins the rules again under the new key. Receipts signed before the move can
only be verified with the old key. This is listed in
[KNOWN_ISSUES.md](../../KNOWN_ISSUES.md).

## Erasing everything

To delete all of Friday's data from this PC:

```
"%LOCALAPPDATA%\AgentFriday\Agent Friday.cmd" erase
```

It lists what will be removed and asks you to confirm. The uninstaller can
also remove your data; see [Updating and uninstalling](updating-and-uninstalling.md).
