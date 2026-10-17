# Privacy: local and cloud

Agent Friday™ keeps what it knows on your PC. What can leave is what a model needs to
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
- **Keys and connected-account tokens** are encrypted in Friday's credential store.
- **The task journal** is encrypted by default.
- **Your wiki, conversations and the knowledge map** are ordinary files, so
  you can read and edit them. In **Settings > Privacy & Data > Wiki sections
  kept off the cloud** you can choose wiki sections that are encrypted with the
  vault key and never enter the knowledge block sent with cloud prompts.

Anyone who can sign in to your Windows account can read the plain files. Use
Windows sign-in and BitLocker. **Settings > Privacy & Data > Locks** shows, line
by line, what is locked and what is not; see [Locks](locks.md).

## Where Friday thinks: your choice

**Out of the box, Friday is set to think in the cloud.** The setup chat then
asks you where she may think, with three cards and nothing picked for you:

| Your choice | What it means |
|---|---|
| **Only on this PC** | Every model runs on this PC, so your conversations are answered here. Needs a PC that can run a local model, and it is slower. |
| **Cloud for public things** | Your own words, files and mail stay on this PC; public things such as news and web searches may use the cloud, with the safeguards on. On a PC that cannot run a local model, your conversations also go to the cloud with the safeguards on: money, health, legal and identity details and your never-send list are held back. |
| **Cloud for everything** | Chat and routines may use the cloud. The never-send floor (below) still applies, every call is still checked and logged, and raw records go only with your yes for each file. |

**Choosing Cloud for everything is your consent.** It lets cloud models be
used for everything except the never-send floor (and, if you turned it on, the
vault records kept local). Friday tells you once what that means and then does
what you chose; she does not ask again or lecture.

**Cloud for public things** keeps the safeguards on. So does switching to
**Cloud only** in Settings > Models without answering the question: picking a
mode is not the same as answering it. Until you answer, the consent is "not
answered" and every safeguard stays on.
You can change the answer later in **Settings > Privacy & Data > Cloud
consent** (**Re-answer**), and the routing mode in **Settings > Models**.

The routing modes in Settings > Models:

| Mode | What happens |
|---|---|
| **Cloud only** | Friday thinks at the cloud provider you chose, over an encrypted connection, after the checks below. |
| **Local only** | A model on this PC answers. If no local model is running, Friday refuses the turn and offers to answer it in the cloud; nothing is sent until you choose, and the cloud choice covers that one turn after a confirm that names the mode. |
| **Local preferred** | This PC by default. The cloud only when you ask for it: a chat you bound to a cloud model, or "Send to Claude" on one turn. When the local model cannot answer, Friday says so and offers the choice; nothing is sent until you pick. |

Every reply says which model answered it. Friday can run fully on this PC:
choose **Only on this PC** and have a local model.

## No blanket topic blocks

A public web or news search is not treated as private because of its topic.
Searching the news for a health story, a court case or a bank's results is a
public search. Only the never-send floor and your personal identifiers (your
name, contact details and the like) are taken out of the search text.

A search that would share private text from your conversation is different:
a card shows the exact query and the search engine, with **Send** and **Don't
send**. Nothing is sent until you choose **Send**.

## The never-send floor

Some things are refused for **every** cloud model, in **every** mode, even
**Cloud for everything**:

- your never-send list (the names, topics, clients or projects you typed in
  the setup chat, or added later);
- files you marked never-send, and the finance, legal and family records in
  your vault;
- lines from a mail archive or statement you have not said yes to (see
  [Locks](locks.md)).

A message to a cloud model that contains one is stopped, not sent. If the
check itself cannot run, nothing is sent.

Two related protections work differently:

- **Keys and passwords you paste into chat** are withheld before anything
  keeps them (see [Keys you paste into chat are withheld](#keys-you-paste-into-chat-are-withheld)).
- **Card and account numbers in ordinary text** are masked when the safeguards
  are on (**Only on this PC** or **Cloud for public things**). Under **Cloud
  for everything** they are not masked. If there is a number that must never
  reach a cloud model, add it to your never-send list.

## Cards that show what would leave

- **An email written on a cloud model with private details in it.** Before it
  is sent, the send card says "this is exactly what would be sent", with
  **Send** or **Don't send**.
- **Money paragraphs in email.** With **Cloud for public things**, when an
  email paragraph about money (an invoice, a payment, a price) would go to a
  cloud model, a card shows exactly that text first: **Send** or **Keep on
  this PC**, with ticks to remember your answer for that thread or sender.
  By voice, a "send" counts only after Friday has read the text back to you
  (within 10 minutes). There is no card when the email stays on the local
  model. Choose **Always keep them on this PC** instead in **Settings > Privacy
  & Data > Money paragraphs in email**, where you can also forget what you
  said yes to.
- **Raw records.** A whole mail archive or a bank or card statement reaches a
  cloud model only after your yes for that one file, under every choice. See
  [Locks](locks.md#raw-records).
- **Vault records.** The vault asks for your cloud consent. It never blocks a
  tool that sends nothing off this PC. When a task needs a model that may read
  vault records, Friday can offer a card to switch the chat's model, or to hand
  that one task to the local model. A model marked as cloud is still refused.

## Keys you paste into chat are withheld

If you paste a key or a password into the chat, Friday withholds it before
anything keeps it. It is replaced with `[credential withheld]`, and she says:
"That looked like a … I withheld it: it isn't in this conversation, my memory
or any log. Paste it in the secure field instead; it is stored encrypted on
this computer." A form, **Store that key securely**, opens for it. The chat box
may catch the key before you even send it.

The same happens in topic keywords, schedule requests, task instructions,
workflows and workspace chats, and a withheld key is never in conversation
search. Keys pasted **before Beta 1.2** may still be in your old history; see
[Updating and uninstalling](updating-and-uninstalling.md#after-you-upgrade-to-beta-12)
for how to delete such a message.

## What Friday reads about your settings

Every prompt Friday sends to a model (chat, background jobs, agents and cloud
voice) starts with a short summary of your privacy settings as they are right
now: the mode, your consent, whether vault records are local-only, how many
items your never-send list has (never the items), and whether spending caps
are on. It is read fresh every turn, so a change in Settings shows on the next
one.

That summary also tells her the rule: **a refusal that goes against your
settings is a bug.** If Friday refuses something your settings allow, she
should say so plainly and offer the local model. If she does not, or refuses
when she should not, please
[tell us](https://github.com/FutureSpeakAI/Agent-Friday/issues).

## The egress gate, and its honest limits

Before anything goes to a cloud model, Friday's egress gate inspects the whole
payload on this PC. With the safeguards on (**Cloud for public things**, or
no answer yet):

- **Private** vault content (contacts, family, personal notes) becomes a
  placeholder.
- **Sensitive** content (financial, medical, legal, identity numbers) is
  withheld, and Friday tells you it held a message back.
- A watchlist in `.friday\privacy_shield.json` redacts any extra names or
  numbers you add.
- If the gate itself fails, the message is not sent.
- Ollama models that run in Ollama's cloud are treated as cloud: the privacy
  gate checks what they're sent.

**What the gate cannot promise.** It recognises patterns: the shape of an
account number, a phone number or an address, and the vocabulary of finance,
medicine and law. It does not understand meaning. "She started sertraline last
month" contains nothing that looks like a medical record and would be sent. In
cloud mode, assume the provider can read what you type.

**Cloud for everything.** Only a recorded "Cloud for everything" (shown in
Settings as "unrestricted") turns the gate's safeguards off; nothing else
does. The never-send floor stays on even then. You can change the answer in
**Settings > Privacy & Data > Cloud consent**.

**File grants.** To send one specific document to a cloud model on purpose,
create a file grant: it is pinned to that file's content and expires. See
[File grants](file-grants.md).

## Sending a cloud model only what a turn needs

By default a cloud model gets the same context a local one does, after the
egress gate has run. The first time a turn goes to a cloud model, Friday asks
once, with nothing preselected: **Send only what each turn needs
(Recommended)** or **Send everything as before**.

With the first choice, Friday reads each message on this PC and leaves out the
parts of your personal context that the message does not need: notes, people,
tasks, calendar, career, memory, what is on your screen. Her identity, your
rules and her safety rules are never left out. If she is unsure about a private
part she sends a fixed one-line note saying some personal context was held back
(it names kinds, never your data), and the model can ask for it. The ask goes
to your own computer, which answers, hides names and identifiers, and shows you
the exact text on a card unless you already allowed sharing in that
conversation. If the turn has read anything from the web or an email, you are
always asked, even when you allowed it before. The choice is in **Settings >
Privacy & Data > What cloud models are sent**.

What was kept and what was held back is on every turn's receipt, and you can
ask "what did you send to the cloud for that?". Local models are never
affected, and with the setting off nothing changes.

## What leaves your PC on its own

No telemetry, analytics or crash reports, and nothing to FutureSpeak.AI. The
few background connections Friday does make (news feeds, connector health
checks, Google Fonts only if Friday's font files are missing (they ship with the app),
the opt-in update check, and one-time model downloads when a feature first
needs them) are listed, with how to turn each off, in
[Background network activity](background-network.md).

## What Laya learns about you

Laya, the fast part of Friday that picks a tool for a simple request, learns from
you on this PC in two small ways. It is **on by default**, with a notice, a
report card and controls you can reach by screen or by voice. Turn it off any
time in **Settings > Privacy & Data > What Laya learns about you**
(`laya_personal_learning`).

**What teaches her.** Words spoken in a local voice call on this PC, and words you
type on Friday's own page when you have chosen Laya to pick the tool for typed
chat (Settings, `chat_tool_routing`) and the chat model runs on this PC. For the
voice call she goes by the voice connection, not by who is speaking: if other
people or a TV are heard in the call, they can be heard as you, so turn on room
mode for those calls (nothing is learned in room mode). Nothing said on a paired
phone, through a relay or tunnel, or in a cloud voice call, and nothing another
agent or a background task says or does, ever teaches her as a correction, and
nothing you type is learned while typed chat is left to the chat model or runs on
a cloud model. The one thing a background task can do is the deeper-mind choice
below, which stays a suggestion until you click. A request she answers from what
is on your screen ("open the second one") is not itself kept as a phrasing; if she
asked you a question first, your choice from her options still counts as your
answer to that question.

| You do this | She treats it as |
|---|---|
| Correct her ("no, I meant my work calendar") | A firm label: you meant that. |
| Answer a question she asks by choosing one of its options, or "something else" (every question offers it). A fresh request is not an answer. | A firm label. |
| Approve or decline a card soon after a request she routed | A light signal: it tells her whether an action was wanted. |
| Say something in a private (local-only) voice call that she hands to the deeper mind, and the background task it starts runs one of the lookup tools she can choose herself (calendar, mail search, notes, news and the like) | A choice **waiting for your OK**, from the first such tool that task runs (a write, a layout or status call, a tool run for another request, or any tool after the task read untrusted content makes none; neither does a request she handed over that you did not say in that call). It measures her, and it teaches her nothing unless you click **Yes, that was right** on the report card. **No, drop it** forgets it. A typed request, or a hand-over that waits for the end of the call, makes no such choice. |
| Say nothing | Nothing. |

**What she does with it.** A phrasing you confirmed becomes one more example for
the route you meant, after two checks: if it sounds closer to a different route
than to yours she uses it for that one request only, and she re-scores her own
shipped test questions and a set of your own earlier answers she holds back, and
turns the phrasing down, with the reason on the report card, if anything she got
right would turn wrong. From your labels she may also tune how sure she must be
before she acts. **She can only make herself more careful on her own.** She never
acts on less than her shipped score unless you approve one card, for one head,
from the report card, and only a click on the screen approves it, never a spoken
"yes". A learned phrasing can let her act without asking in one case only: a
read-only, local route that you have confirmed at least three times, for a
near-exact repeat of one of those phrasings. Anything that reaches outside this
PC (the web, search, past conversations) or is sensitive still asks first.

**What she never keeps.** Anything she can tell is sensitive (money, health,
legal, identity), and anything she cannot tell about; anything while you are off
the record; anything said in a call with several people in the room; anything for
a minor profile; anything when the vault has no key. Retuning runs as a **Local AI
queue** job ("Learning from your corrections"), only while you are signed in and
here, with the vault open and the PC on AC power, on the processor: it uses no
graphics card and no model seat.

**Where it lives.** Sealed in your vault, with the same key as the rest of the
vault, on this PC, and never sent anywhere. While the vault is locked, new
corrections wait in an encrypted buffer that only the vault can open. Nothing of
yours is ever part of Friday's shipped models. Corrections are kept for 180 days
(`laya_personal_retention_days`), then deleted with every saved version built
from them.

**Your controls.** The report card lists what she learned, each item with **Undo**
and **Forget** (which deletes it and every saved version trained on it), what she
turned down and why, and the routes she learns less about because of
sensitivity. She keeps her last three versions plus how she shipped: **Go back to
this**, **Reset** (sets everything aside) and **Wipe everything** (deletes it, after
a second click; by voice it only raises a card that a click on the screen
approves). In a local voice call or in typed chat on this PC (not in a cloud voice
call), once, with your whole sentence: "what have you learned?", "forget that",
"reset", "go back to how you were last week". Say it as the whole sentence: if the
same sentence also asks for a change ("forget what you just learned and archive
these"), she does the change as an ordinary request and does not run the control, so
say the control on its own. Forgetting works even when learning
is off. **Forget** on a person in Contacts deletes every correction that names them
and the saved versions built from them; while the vault has no key the sealed ones
are removed the next time it opens, and nothing from before is used until then.

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
