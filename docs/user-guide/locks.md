# Locks

The Locks page says, line by line and in plain words, how your things are
locked on this PC, what you chose, and what that cannot stop. Each line is read
from the part of Agent Friday™ that actually decides it, not from a setting
that only claims it. When Friday cannot read a line's truth, it says "I
couldn't tell" instead of guessing. Something that is not built yet says so.

Open it at **Settings > Privacy & Data > Locks**. It is the first section on
that tab. It works only on Friday's own page on this PC; it cannot be read
from another computer or through a tunnel.

The page only reads. Nothing on it changes a setting by itself, and nothing on
it leaves the PC.

## The lines

| Line | What it tells you |
|---|---|
| **Your lock** | Whether your private records (finance, health, legal, family) are encrypted with your vault passphrase. If no passphrase is set, it says they are readable on this disk and points to **Privacy & Data > Vault**. |
| **Recovery** | There is no recovery kit yet. If you lose your passphrase, the vault cannot be opened. Keep it in a password manager. |
| **Everything else** | Your conversations, memory and most of Friday's databases are not encrypted yet. Until they are, your Windows account protects them. |
| **Your disk** | Whether Windows' own disk encryption is on. Friday cannot yet read this reliably without administrator rights, so today it says "I couldn't tell" and offers to open Windows' settings page so you can see for yourself. |
| **Where I think** | Your privacy choice (**Only on this PC**, **Cloud for public things** or **Cloud for everything**, or "not chosen yet"), your never-send list, each entry with a **Remove** button (a click on this page only), and how many requests left this PC today, by destination. |
| **Keys you've given me** | Which provider keys are stored, encrypted, on this PC. Stored is not the same as working; each key is checked when it is used. |
| **My records** | Whether Friday's own records were changed after they were written: the setup record and the inbox clean-up receipts. |
| **Held from the cloud** | Raw records (below) that Friday opened without your yes, held back from every cloud model. |
| **Updates** | Whether the weekly update check is on, when it last ran, and a reminder that the installer is not code-signed, so check its SHA-256. |
| **Forget** | What you can forget today: a person (from Contacts) and all of Friday's data (**Privacy & Data > Your data**). Forgetting one source with every copy made from it is not built yet. |
| **The honest limit** | Always shown, at the bottom. |

## The honest limit

Your files are locked on disk where Friday has a lock for them. While Friday
is running, any program you run on this PC under your own Windows account
could read what Friday can read, just as it could open your documents. Keep
Windows updated, use antivirus, and do not run programs you do not trust.

## Raw records

A **raw record** is a whole mail archive or a bank or card statement:

- **Mail archives**: files ending `.mbox`, `.mbx`, `.eml`, `.emlx`, `.msg`,
  `.pst` or `.ost`, or a file with no extension that looks like a mailbox.
- **Statements**: files ending `.ofx`, `.qfx`, `.qif` or `.qbo`; a PDF or
  spreadsheet whose name says statement, transactions, account history or
  bank; or a spreadsheet named like a bank export (activity, checking, savings,
  credit card, ledger, register).

Friday recognises them by the file's name and type, never by what a model says
the file is.

**Under every privacy choice, even Cloud for everything, a raw record reaches
a cloud model only after you say yes for that one file.** A folder you let
cloud models read does not cover it. When it matters, a card names the file,
what it is and which provider would read it, and says "Until then it stays on
this PC." Your never-send list still applies after a yes. The card asks at
most once a day for the same version of a file.

### Files held from the cloud

If Friday opens a raw record you have not said yes to, she holds it: nothing
from that file goes to a cloud model. The **Held from the cloud** line lists
each held file with an **Ask me about this file** button, which puts the yes
or no card on screen now.

**Start the list fresh…** puts a card on screen that says exactly what starting
fresh lets go: lines from the held files could then reach a cloud model when
something quotes them. Nothing changes until you approve that card on screen.
A held file that is read again is held again. Starting fresh does not change
your never-send list.

If Friday cannot keep the held list in full, it says so on this line and stops
**every** cloud message until you start the list fresh.

### The known limit

A model that deliberately builds a file's path from pieces can read around
this check. If it does, the never-send floor still stops what it read from
going to the cloud when it quotes the held file or matches your never-send
list.

## Related

- [Privacy: local and cloud](privacy.md)
- [File grants](file-grants.md)
- [Backup and restore](backup-and-restore.md)
