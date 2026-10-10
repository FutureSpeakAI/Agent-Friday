# Troubleshooting

Problems people have met with Agent Friday™, with the cause and the fix. Each
entry says whether it is fixed in Beta 1.0. If yours is not here, find the logs
(see [Where the logs are](#where-the-logs-are)) and open an issue using the bug
report template.

## Installing

### Windows says "Windows protected your PC"

The setup program is not code-signed, so SmartScreen shows **Unknown
publisher**. Check the file's SHA-256 against the one on the release page, then
choose **More info** and **Run anyway**. The steps are in
[Getting started](../getting-started/README.md#3-the-smartscreen-warning).

### Setup stops and says Friday needs more free space

Setup needs about 8 GB of free disk for the program (about 5 GB if the memory
tier is skipped), before any model files. The model page also hides any model
that would leave your drive with less than 10 GB free. Empty the Recycle Bin or
move files to another drive, then run setup again. Nothing is changed when
setup stops for this reason.

### There is no Agent Friday icon on my Desktop

Fixed in Beta 1.0 for a Desktop that OneDrive keeps. Setup now asks Windows
where your Desktop is, wherever it has been moved to, and creates the shortcut
there and in the Start menu. If an icon is still missing, open the Start menu
and look under **Agent Friday**, or run `Agent Friday.cmd` from
`%LOCALAPPDATA%\AgentFriday`. If setup cannot create a shortcut, it shows a message
that names the file to start.

### Setup says a newer version is installed

Setup compares build sequence, not the version number, and it never replaces a
newer build with an older one. Nothing was changed. Beta 1.0.1 (1.0.1b1) looks
numerically lower than 5.14.3 but is newer, so it installs over Beta 1.0.1, Beta 1.0 and any
5.x release.

## Models and memory

### Friday is very slow, or Windows runs out of memory

A local model has to fit in memory beside Windows and your browser. Setup lists
only models that fit, but other programs also use memory.

| Your computer | What setup offers | What to expect |
|---|---|---|
| Under about 10 GB of memory | No local deep thinker | Use a cloud model. |
| About 12 GB of memory | The older, lighter Ternary Bonsai 4B as the deep thinker, and the Ternary Bonsai 1.7B or Qwen3 voice models | Lighter and less capable than Bonsai 2 27B. |
| 16 GB of memory, no usable graphics card | Bonsai 2 27B on the processor, 8,192 tokens of context | Slow. An estimate is 3 to 6 tokens per second. Close other programs, or use a cloud model for the deep thinker. |
| 16 GB, NVIDIA card with 6 or 8 GB | Bonsai 2 27B split between the card and the processor | Faster than the processor alone. |
| NVIDIA card with 10 GB | The whole model on the card, 16,384 tokens of context | Fast for short work. |
| NVIDIA card with 12 GB | The whole model on the card, 131,072 tokens of context | About 45 tokens per second, measured on a 12 GB card. |
| NVIDIA card with 16 GB or more | The whole model on the card with more context | Faster. |

Graphics cards from AMD and Intel are not read by setup; such a machine is
treated as having no card.

If Friday slows down while you do something else on the PC, open **Settings >
Models** and choose **I need my machine**. It unloads the local models from
the graphics card and pauses background and scheduled jobs until you press
Resume. Chat keeps working through a cloud model. On a 12 GB card, a second
model or local image generation may fail to find room while the 27B model is
loaded, and this button is the fix.

### A model download did not finish

Downloads continue where they stopped. Close and reopen Friday, or restart your
computer, and the download resumes from its partial file. Every file is checked
against its publisher's checksum; a file that does not match is deleted and the
model is not used. A download also needs the 10 GB free-disk margin, so a full
disk stops it.

### Voice downloads are not offered when voice is set to the cloud

Fixed in Beta 1.0. **Settings > Voice > Local voice models** now appears in
every voice mode. Each part shows its size and asks before it downloads.

### Local voice says "The local voice context is full"

Fixed in Beta 1.0. The fast voice model has a limited window, and its standing
instructions used to fill it. They are now cut to a budget so that every turn
leaves room for history and an answer. If you still see this message, you have
a very long conversation: start a fresh one, or hand the work to the main agent.

### The faster, more accurate speech listener is not offered

The optional larger listener (Whisper large-v3-turbo) is not offered in Beta
1.0, because its download is not yet pinned to a verified version. The
streaming listener that is offered is the one Friday uses.

## Opening and using Friday

### Friday does not open

Friday listens only on your own PC at `http://localhost:3000`. Use the Desktop
shortcut or the tray icon's **Open Agent Friday™**. If nothing answers, choose
**Restart Server** in the tray menu, then read the end of
`%USERPROFILE%\.friday\friday.log`.

### The microphone does not work

The browser allows the microphone at `http://localhost:3000` and at the secure
local address, but not at a plain `http://agent.<name>` address. Use
`localhost`, or finish the certificate step under **Settings > General**.

### After moving my data to a new PC, outward actions wait

The key that signs Friday's rules lives in Windows Credential Manager and does
not move with your data. On a new PC or Windows account, outward actions (sending
mail, publishing, installing, changing files) are held until you confirm the
rules again. Reading and drafting keep working. See
[Backup and restore](backup-and-restore.md).

### Friday asked me twice to confirm an action

When the model asks "shall I?" before calling a tool, your yes is not yet the
checkpoint's question, so the checkpoint asks once more for that exact action.
It is a known behaviour; see [Known issues](../../KNOWN_ISSUES.md).

## Where the logs are

| File | What it holds |
|---|---|
| `%USERPROFILE%\.friday\friday.log` | The server's log. It rolls at 10 MB and keeps three older files. |
| `%USERPROFILE%\.friday\logs\` | Crash records (`crashes.log`) and hang dumps (`hang-dump-*.log`), when they happen. |
| `%LOCALAPPDATA%\AgentFriday\logs\setup.log` | What setup did, step by step. |

The tray menu's **Voice Debug Log** opens the voice log. Logs can contain file
names and the text of your requests. Read them before you attach them to an
issue, and remove anything private.

## Backing up and restoring

Everything Friday knows is in `%USERPROFILE%\.friday`. To back it up, quit
Friday and copy that folder, or run the built-in export:

```
"%LOCALAPPDATA%\AgentFriday\Agent Friday.cmd" export
```

That writes a zip of your data to your Documents folder and leaves out every
key and credential. Add `--full` for a backup that includes them, encrypted
with a passphrase you type. Your vault passphrase is the one thing that cannot
be recovered: keep it in a password manager. Restoring, and what a restore
cannot bring back, are in [Backup and restore](backup-and-restore.md).

## Uninstalling cleanly

1. Open **Apps > Installed apps** in Windows Settings, find Agent Friday, and
   choose **Uninstall** (or use **Uninstall Agent Friday** in the Start menu
   folder).
2. When asked whether to keep your notes, conversations and settings, choose
   **Yes** to keep them (the default) or **No** to delete them. Deleting also
   removes the passphrase that opens your vault, and it cannot be undone.
3. If you set up the local address, uninstall offers to remove its line from the
   Windows hosts file. Windows asks for permission.
4. To remove model files too, delete `%USERPROFILE%\.friday` after uninstalling
   (only if you chose to keep it and no longer want it).

See [Updating and uninstalling](updating-and-uninstalling.md).

## Still stuck

Open an issue at
[github.com/FutureSpeakAI/Agent-Friday/issues](https://github.com/FutureSpeakAI/Agent-Friday/issues)
with your Friday version (**Settings > About**), your Windows build, your
memory and graphics card, and the relevant lines from the log. For a security
problem, follow [SECURITY.md](../../SECURITY.md) instead.
