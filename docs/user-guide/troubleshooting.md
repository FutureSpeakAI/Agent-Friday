# Troubleshooting

Problems people have met with Agent Friday™, with the cause and the fix. Some
entries say which beta fixed them. If yours is not here, find the logs
(see [Where the logs are](#where-the-logs-are)) and open an issue using the bug
report template.

## Installing

### Windows says "Windows protected your PC"

The setup program is not code-signed, by decision for this beta, so
SmartScreen shows **Unknown publisher**. This does not mean Windows found a
problem; it has no reputation record for the file. Check the file's SHA-256
against the one on the release page:

```powershell
Get-FileHash .\AgentFriday-Setup-<version>.exe -Algorithm SHA256
```

If the hash matches, choose **More info**, then **Run anyway**. If it does not
match, delete the file and download it again. The steps are in
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
newer build with an older one. Nothing was changed. A Beta version looks
numerically lower than 5.14.3 but is newer, so Beta 1.2 installs over Beta
1.1, Beta 1.0.2, Beta 1.0.1, Beta 1.0 and any 5.x release.

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

The optional larger listener (Whisper large-v3-turbo) is not offered yet,
because its download is not yet pinned to a verified version. The
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

When the model asks "shall I?" in its own words before calling a tool, your
yes is not yet the checkpoint's question, so the checkpoint asks once more for
that exact action, as a card. It is a known behaviour; see
[Known issues](../../KNOWN_ISSUES.md). The card itself is the same everywhere
(in the chat, the **Needs you** tab, the popup and **System > Approvals**), and
one decision settles it in all of them.

## Connections and keys

### A service says "not connected", or Test connection fails

"Not connected" in grey on the **Connect your services** screen (**Settings >
Connections**) means nothing is saved for that service yet. That is normal on
a fresh install, and Friday does not warn about it. Open the card, follow its
three steps, paste the key, press **Save securely**, then **Test connection**.

What the result means:

| Result | What to do |
|---|---|
| **Test passed** | Nothing. It works. |
| **Test failed** | Read the **How to fix** line on the card. Usually: copy a fresh key and paste it again; add credit or a payment method at the provider (a key with no credit is accepted but cannot answer); start Ollama or LM Studio; check an address ends in `/v1`; for Google, press **Connect** and approve on Google's screen again. For Brave, check the key is for the Search plan, not the AI plan. |
| **Not connected yet** | No key or sign-in is saved for it. Add one. |
| **Could not tell** | Friday could not reach the service. That is not a verdict on the key. Check the internet connection and try again in a minute. |

If you chose **I'll just use local AI**, cloud services are hidden from advice
but you can still connect any of them; **Show cloud options again** brings
them back. See [Connect your services](connections.md).

### Gmail says it needs permission to send

Sending, and saving drafts, need Google permissions that start off when you
connect an account. If Friday says one is missing, press **Reconnect with
sending** and approve on Google's screen. See
[Mail](mail.md).

### Friday said "That looked like a … I withheld it"

You pasted a key or password into the chat. Friday withheld it before anything
kept it: it is not in the conversation, her memory or any log, and the message
shows `[credential withheld]` in its place. Nothing is wrong. Paste the key into
the secure field instead: the **Store that key securely** form that opened, or
the service's card in **Settings > Connections**.

If you pasted keys into chat before Beta 1.2, see
[After you upgrade to Beta 1.2](updating-and-uninstalling.md#after-you-upgrade-to-beta-12).

### Friday refused something your settings allow

Every prompt tells Friday your privacy settings as they are now, and that a
refusal against them is a bug. If she refuses something your choice allows
(for example a public news search, or a cloud answer when you chose **Cloud
for everything**), she should say so and offer the local model. Either way,
please [open an issue](https://github.com/FutureSpeakAI/Agent-Friday/issues)
with what you asked and your setting from **Settings > Privacy & Data > Cloud
consent**. Leave out anything private.

Some refusals are expected under every setting: anything on your never-send
list, passwords, keys and account numbers never go to a cloud model, and a
mail archive or bank statement goes only after your yes for that file. See
[Privacy](privacy.md).

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
