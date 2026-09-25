# Privacy: local and cloud

Friday keeps what it knows on your PC. What can leave is what a model needs to
answer you, and only when that model runs in the cloud. This page says exactly
what goes where.

## What stays on your PC

Everything Friday writes down lives in the `.friday` folder in your user
folder: your wiki, your conversations, the knowledge map, your settings, the
vault, the receipts. Friday has no server and no account; there is no copy
anywhere else. You can open the folder, back it up or delete it.

Not all of it is encrypted:

- **The vault** (finance, health, legal and family records) is encrypted with
  your vault passphrase (AES-256-GCM). Without a passphrase it is readable.
- **Keys and connected-account tokens** are encrypted under Friday's keystore.
- **Traces and the task journal** are encrypted by default.
- **Your wiki, conversations and the knowledge map** are ordinary files, so
  you can read and edit them. You can encrypt chosen wiki sections with the
  vault key (`wiki_encrypted_sections`).

Anyone who can sign in to your Windows account can read the plain files. Use
Windows sign-in and BitLocker.

## Where your words go

First-run setup asks, and Settings → Models lets you change it per job.

| Choice | What happens |
|---|---|
| **Cloud** (`cloud_only`) | Friday thinks at the cloud provider you chose. Your messages go there over an encrypted connection, after the egress gate. |
| **On this computer only** (`local_only`) | A model on this PC answers. If no local model is running, Friday refuses the turn and offers to answer it in the cloud; nothing is sent until you choose. |
| **Both** (`local_preferred`) | This PC by default; the cloud when it would clearly help. |

Every reply says which model answered it.

## The egress gate

Before anything goes to a cloud model, Friday's egress gate inspects the whole
payload on this PC:

- **Private** vault content (contacts, family, personal notes) becomes a
  placeholder.
- **Sensitive** content (financial, medical, legal, identity numbers) is
  withheld, and Friday tells you it held a message back.
- A watchlist in `.friday\privacy_shield.json` redacts any extra names or
  numbers you add.
- If the gate itself fails, the message is not sent.

**What the gate cannot promise.** It recognises patterns: the shape of an
account number, a phone number or an address, and the vocabulary of finance,
medicine and law. It does not understand meaning. "She started sertraline last
month" contains nothing that looks like a medical record and would be sent. In
cloud mode, assume the provider can read what you type.

**Unrestricted cloud.** On a PC that cannot run local models, first-run setup
may ask you to accept unrestricted cloud explicitly. Only that recorded choice
turns the gate's safeguards off; nothing else does.

**File grants.** To send one specific document to a cloud model on purpose,
create a file grant: it is pinned to that file's content and expires. See
[File grants](file-grants.md).

## What leaves your PC on its own

No telemetry, analytics or crash reports, and nothing to FutureSpeak.AI. The
few background connections Friday does make (news feeds, connector health
checks, Google Fonts until you turn it off or the font files are installed,
the opt-in update check, and one-time model downloads when a feature first
needs them) are listed, with how to turn each off, in
[Background network activity](background-network.md).

## Other people

Friday will hold notes about people you mention or who write to you. In cloud
mode, what you write about them travels under the same rules as what you write
about yourself. To make Friday forget someone, open Contacts, choose the
person, and use **Forget**: it removes Friday's own record and map entries for
them. Your own pages and conversations are left as you wrote them.

## Remote access

Friday listens only on this PC. A request that arrives through a tunnel or a
reverse proxy is never treated as you: it must log in. Do not publish Friday's
port through a tunnel. The phone feature uses its own, separate listener; see
[Phone](phone.md).
