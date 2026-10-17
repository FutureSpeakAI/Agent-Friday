# The setup chat

Setup has three parts. The installer asks which models to download. The first
run in the app shows a few screens, including the vault passphrase. Then Agent
Friday™ sets herself up with you in a conversation, the setup chat. It takes
about twenty minutes. You can stop at any point and she picks up where you
left off.

The chat needs no model and no key. Every line Friday says in it is written in
advance, and Friday's own code decides each step. A model is used for two
things only, and only when one is available and you have been told which:
reading your answers to the style questions, and reading public web pages if
you ask her to look you up.

## Buttons on every step

- **Set up later** finishes setup at once with safe defaults. Routines stay
  off. Everything you did not answer waits in Settings.
- **Use the recommended answer** appears only on a step that has a
  recommendation: where Friday may think, the monthly spending ceiling, and
  scheduled jobs on a cloud model. It answers **that step only**, never one you
  have not seen, and Friday says that she used it.
- **Skip** or typing "skip" leaves that one question. A skipped step keeps the
  safe default.

If you leave and come back after half an hour or more, Friday says how far you
got and asks whether to carry on. The progress rail at the top shows the
groups: Hello, Me, You, Privacy, Connect, Your files, Research, Style, Money &
routines, House rules, Finish. You can go back to a group you have finished.

## Choosing models in the installer

The installer's model page looks at your PC (memory, graphics card and free
disk) and offers two jobs, each with a choice of model:

- **Fast responder** answers voice and quick replies: Ternary Bonsai 1.7B
  (recommended), Qwen3 4B or Qwen3 1.7B. See
  [Voice](voice.md#the-fast-responder).
- **Deep thinker** does the heavier thinking, from the Bonsai family.

One option per job is labelled **Recommended**; nothing is ticked for you. To
fetch local models you tick **Download these models when Agent Friday first
starts**. Or tick **Use a cloud model instead** and nothing is downloaded.
Downloads happen the first time Friday starts, with progress and checksums. A
choice that would leave the drive with under 10 GB free is refused.

## The first-run screens

Before the chat, Friday shows what she writes down and where, asks for the
passphrase that protects the vault, explains the notes she keeps about people
you mention, asks what to do when you are on a video call, and asks whether to
check for new versions weekly (off until you turn it on). The vault passphrase
comes first so that nothing personal is asked before your lock exists. See
[Privacy](privacy.md).

## The steps

### Hello

Friday opens with six promises, then asks whether you would rather **Talk**,
**Type**, or tell her **I use a screen reader**. You can switch any time. With
a screen reader she does not speak over it, every step works with the
keyboard, and nothing has a time limit.

Then an offer: **Clean up my inbox** or **None for now**. If you say yes, she
starts once your email is connected (see [Mail](mail.md)). She only reads your
mail for this, and nothing is unsubscribed until you tick it.

### Me

- **Her name.** Most people keep Friday.
- **Her voice.** If a voice is installed on this PC, press **Hear it** beside
  one, then choose. Or **Choose later**; it waits in **Settings > Voice**.
- **Her pronouns:** she, he, they, or her name only.
- **How you think of her:** an assistant, a colleague, or more like family. It
  changes how she talks about your work, nothing else.

### You

Your name (a nickname is fine), your city (for weather and local news), and
your time zone, which she reads from this PC and asks you to confirm. Each is
optional. What you give is written on the **About you** card beside the
conversation, where you can delete any line. Your name is kept out of anything
sent to a cloud model.

Then the basics card: who this Friday is for (you, or your child, with
age-appropriate filtering; nothing is picked for you, and **Continue** waits
for your answer), a starting profile for the dock and her tone (the receipt
says "default" if you leave it as it is), and
what this computer can run on its own, with a button to download a local model
if a runtime is installed.

### Privacy: where she may think

Three cards. **Nothing is picked for you.** Each card says what it means and
what it costs on this PC.

| Card | What it means | What it sets |
|---|---|---|
| **Only on this PC** | Every model runs here, so your conversations are answered on this PC. Needs a capable PC, and it is slower. | Routing: only on this PC. Scheduled jobs: never the cloud. |
| **Cloud for public things** | Your own words, files and mail stay on this PC; public things such as news and web searches may use the cloud, with the safeguards on. On a PC that cannot run Friday's thinking model, your conversations also go to the cloud with the safeguards on: money, health, legal or identity details are not sent quietly; Friday keeps them on this PC and tells you, and where a card is offered (mail, private records) it shows exactly what would go. Your never-send list is never sent. | Routing: only on this PC if this PC can run a local model, otherwise cloud. Scheduled jobs may use the cloud. |
| **Cloud for everything** | Chat and routines may use the cloud, and choosing it is your consent: only your never-send list is refused. It is still applied, every call is still checked and logged, and raw records (mail archives, bank statements) go only with your yes for each file. | Routing: cloud. Scheduled jobs may use the cloud. |

The recommended card is **Only on this PC** when this PC can run a local
model, and **Cloud for public things** when it cannot. **Only on this PC** is
not offered on a PC that cannot run the local model; Friday says why.

Your one answer is recorded everywhere it is read: your cloud consent, the
routing mode, and whether scheduled jobs may use a cloud model. Choosing a
cloud card is your consent for cloud models to see what that card says (see
[Privacy](privacy.md)). If you skip the question, every cloud call keeps all
the safeguards on, and the question waits for you when setup ends. You can
change it later in **Settings > Privacy & Data > Cloud consent** and
**Settings > Models**.

### Privacy: the never-send list

"Is there anything that must never leave this PC, whatever you chose?" Type
names, topics, a client or a project, one per line or separated by commas (up
to 50). A message to a cloud model that contains one is stopped, not sent,
under every choice above.

Card numbers and bank or account numbers are on the list from the start. Your
vault's finance, legal and family records never go either, and a key or
password you paste is withheld before anything keeps it. Your own name and
contact details are kept out of what goes to a cloud model while the
safeguards are on.

**Settings > Privacy & Data > Locks** shows the list and removes entries; to
add more later, run the setup chat again.

### Connect

The **Connect your services** screen: AI model providers, creative and media
tools, search, your accounts, and any MCP server. Every card is optional and
nothing is preselected. Keys go into the secure field on each card, never into
the chat. **I'll just use local AI** moves on without asking for any key; it
changes no privacy or routing setting. Everything here is also at the top of
**Settings > Connections**. See [Connect your services](connections.md).

If you chose the cloud and this PC has no local model, Friday says she needs
one AI key to think with: Anthropic (recommended) or OpenRouter, either one on
its own. If you leave with no key, she says what will not work until you add
one (chat, briefings, the front page, scheduled jobs and research), and uses
simple rules for the rest of setup.

If you paste something that looks like a key into the chat box, it is not
sent anywhere and is not kept; Friday points you to the secure field instead.

### Your files

"Do you already keep notes or documents somewhere?" **Not now** skips it.
**Choose folders…** picks folders; Friday also lists places that usually hold
notes, with a size and file count, and chooses none for you. There are two
separate yeses per folder: **read and index it on this PC** (nothing leaves),
and, off unless you turn it on, **also let cloud models read it** for 30 days.
**Saved my choices** finishes. The same choices are in **Settings > Privacy &
Data > Your knowledge**. See [Your knowledge](your-knowledge.md) and
[File grants](file-grants.md).

### Research (optional)

First Friday tells you which model would read your answers: a model on this
PC if there is one; otherwise, if you chose a cloud card, she names the cloud
model and company and asks (**Keep it on this computer** uses simple rules
instead); with no model at all, simple rules on this PC.

Then: **Yes, look me up** or **No thanks**. Off unless you say yes. If you do,
you type what to search for: your name as it appears in public, handles, your
own websites, and an employer if you want it. She searches only for those
words, reads public pages as untrusted text, never looks for health,
sexuality, finances, family or your home address (a finding that touches one
is thrown away), and keeps nothing until you accept it line by line. It runs
as a background task you can watch. You review the findings after the style
step, or later in **Settings > General > Your profile**.

### Style

About ten short questions on how you like to be spoken to (formal or casual,
humour, how blunt, how to tell you that you are wrong, how much detail, when
you work, quiet hours, what you value, what you want from an assistant, pet
peeves), and a last, optional, classic one. **Skip** or **Rather not say**
works on every one. This is a communication-style profile, nothing more;
Friday never diagnoses or labels anything from it.

Your answers become six sliders (warmth, formality, humour, directness, detail
and pushback) with a sample reply that changes as you move them. **Save this
style** makes it part of her personality. Honesty and asking before acting
cannot be tuned away: pushback has a floor, and a style cannot override her
rules.

### Money and routines

- **A monthly ceiling**, asked only if the cloud is in play: **No cap**, **$5
  a month**, **$10 a month** (recommended) or **$25 a month**, or type an
  amount. When the ceiling is reached, cloud work stops and Friday tells you
  once; models on this PC keep working. It sets the monthly hard stop in
  **Settings > Spending**. Skipping leaves nothing capped.
- **Scheduled jobs on a cloud model**, asked only if you skipped the privacy
  question, this PC has no local model, and you have not answered it before.
  It names the model each job would use and its estimated monthly cost.
  **Yes, use the cloud**, **No, keep them paused** or **Skip**. Change it in
  **Settings > Spending > Scheduled jobs without a local model**.
- **Routines.** Nothing is ticked. Pick any of: Morning front page, Evening
  front page, Afternoon briefing, Weekly editorial, Hourly heartbeat (checks
  mail and calendar, and says nothing when nothing changed), and a Podcast of
  each edition you tick. Each says when it runs and where. Change them later in
  Workflows.

### House rules

- **Memory and approval rules.** What Friday remembers on her own, and what she
  always asks about, as a card. **Looks right** moves on. See
  [Memory](memory.md) and [Approvals and receipts](approvals-and-receipts.md).
- **Will anyone else use this PC?** **No, just me**, or **Yes**. Friday works
  for one person per Windows account; the safe way to share a PC is for each
  person to have their own Windows sign-in, and a button opens Windows' own
  page for adding someone.
- **Spoken approvals in a room.** When someone else is in the room, Friday
  cannot tell your voice from theirs. **Require my name** ("Friday, send it"),
  **Anyone in the room may approve**, or **Skip**, which keeps requiring the
  name. On your own, "send it" is enough. Change it in **Settings > Voice**.
- **The local model when you quit.** **Hand it back when I quit** (the
  default) frees its memory and costs about a minute to load next time. **Keep
  it warm for me** keeps it loaded. Either way it stays loaded through a
  restart.

### Finish

A receipt, **What we set up**, lists every choice. Each line says who decided:
you, the recommended answer you accepted, or the safe default because it was
skipped. **Open my desktop** completes setup.

## What is stored where

| What | Where | How |
|---|---|---|
| Which step you are on, your non-personal choices, what you skipped | `.friday/setup_chat.json` | Plain file, no secrets and no answers |
| The setup record: one line per decision, with who decided | `.friday/setup/ledger.jsonl` | Labels and counts only, never your answers; each line is chained to the one before, so an edit is detected. The Locks page checks it. |
| Your privacy answer | `.friday/settings.json` (cloud consent, routing mode, scheduled jobs) | Plain file |
| Your never-send list | `.friday/privacy_shield.json` | Plain file |
| The About you lines from setup | Friday's list of facts about you | Deletable from the card beside the setup conversation |
| Your name | The watchlist in `.friday/privacy_shield.json` | Kept out of anything sent to a cloud model |
| "I'll just use local AI" | `.friday/connections_choice.json` | Plain file |
| Your answers to the style questions, the derived style | `.friday/profile/setup_profile.bin` | Encrypted |
| The conversation so far | `.friday/profile/setup_transcript.bin` | Encrypted |
| Research findings waiting for review | `.friday/profile/research/` | Encrypted |
| Accepted findings | Friday's knowledge graph | With "research" as their source and the page address |
| Keys and account tokens | Friday's credential store | Encrypted |
| The style Friday uses | A marked section in `.friday/SOUL.md` | The six values, a summary and short notes, never your answers |

Nothing here is sent anywhere unless you chose a cloud model to read your
answers, and then only your answers go, to the model you were shown.

## Running it again, changing or deleting your profile

**Settings > General > Your profile** shows each question and your answer. You
can change answers, choose **Save and rebuild my style**, run **Research on
you**, and **Run the setup chat again**. Running it again revisits each step
and keeps everything already connected, everything Friday has learned, and
your privacy choice.

**Delete my profile** removes your answers, the setup conversation and the
style section from Friday's personality. Findings you accepted from research
stay in the knowledge graph, where you can remove them like any other entry.
