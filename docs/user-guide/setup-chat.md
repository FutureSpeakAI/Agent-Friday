# The setup chat

After the first-run consent screens (what Friday writes down, the vault
passphrase, where your words go, other people, update checks), Friday sets
herself up with you in a short conversation. You answer by typing or by
tapping a suggested answer. Every step can be skipped, and **Set up later**
in the top bar finishes setup with defaults at any point.

The chat needs no model and no API key: every line Friday says in it is
written in advance, and every step is decided by Friday's own code. A model
is used for two things only, and only when one is available and you have been
told which: reading your answers to the style questions, and reading public
web pages if you ask for research on yourself.

## The steps

### 1. Hello

What should Friday call you, and what would you like to call her (most people
keep Friday). Then the basics:

- **Who this Friday is for:** you, or your child (family mode, with
  age-appropriate filtering).
- **A starting profile:** which workspaces the dock offers and Friday's
  starting tone. You can switch in Settings.
- **What this computer can run:** your graphics card and memory, and, if a
  local model runtime is installed, a button to download the local model.
- **Friday's own address on this PC** (optional): the same card as
  Settings → General → Local address.

### 2. Connect everything

A checklist of every service Friday can use, grouped: AI models (Anthropic,
OpenRouter, OpenAI, Google Gemini, Hugging Face and the other supported
providers, plus local models), your Google account (Gmail, Calendar, Drive,
Docs, Sheets, Contacts, Tasks), the phone (Twilio), GitHub, social and
publishing accounts (YouTube, LinkedIn, Instagram, Reddit, Mastodon, X,
TikTok, Bluesky, Medium, Substack), messaging (Telegram and Discord bots,
Slack, Discord servers), tools (Linear, Notion, Higgsfield), web search
(Brave, Firecrawl), voices (ElevenLabs, Inworld) and Cloudflare.

Each card says what connecting it unlocks and **exactly what it asks for**:
for Google, every permission in plain words, with sending mail and changing
your mailbox as separate boxes that are off unless you tick them; for social
accounts, the permissions the platform will ask you to approve.

- Accounts that support it connect through the service's **own sign-in and
  consent screen** in your browser.
- Keys go into a **secure field** on the card. The value goes straight into
  Friday's encrypted credential store on this PC. It never appears in the
  conversation, is never shown again, and never reaches a model.
- **Cloudflare** is set up outside Friday, with Cloudflare's own tool; the
  card lists the steps. Friday holds no Cloudflare credential.

Friday never reads your browser's saved passwords or cookies, never opens
other programs' credential files, and never asks for a password in the chat.
If you paste something that looks like a key into the chat box, it is not
sent anywhere; Friday offers the secure field for it instead.

Anything you skip waits in **Settings → Accounts & Keys → Setup checklist**,
with the same cards.

#### One key is enough

If you chose the cloud, and no model is installed on this computer, Friday
says so at this step: she needs one AI key to think with, and one is enough.

- **Anthropic** comes first: Claude, from the company that makes it, paid as
  you go. Get a key at <https://console.anthropic.com/settings/keys>.
- **OpenRouter** is the alternative: one account for Claude and many other
  models, paid from credit you buy up front. Get a key at
  <https://openrouter.ai/keys>.

Paste the key into the Anthropic or OpenRouter card, not the chat. When you
save it, Friday sends one tiny request with it and tells you plainly whether
it worked and whether she can think now. If the provider rejects the key, or
the account has no credit, she says which.

If you leave this step with no key, Friday tells you what will not work until
you add one: chat, briefings, the front page, scheduled jobs and research.
The rest of setup still works, using simple rules. The checklist, here and in
Settings, shows which key Friday is using and that it is enough.

### Who reads your answers

Before the personal part, Friday tells you which model will read your
answers:

- a model on this computer, if one is installed (nothing leaves the PC);
- otherwise, only if you chose a cloud option on the "Where your words go"
  screen, Friday offers a cloud model and names it and its company. It is used
  only if you say yes; "Keep it on this computer" uses simple rules instead;
- with no model at all, simple rules on this computer.

Whatever reads your answers is named next to everything it produced.

### 3. Research on you (optional)

Off unless you say yes. If you do, you type what to search for: your name as
it appears in public, any handles, your own websites, and an employer if you
want it included.

What it **will** do:

- Search only for what you typed. The searches are built from those words
  alone; no model writes a search, and nothing a page says can steer what is
  searched next.
- Look at public pages: published work, talks, public profiles, your own
  sites.
- Run in the background as a task you can watch in the tray, with a
  reasoning trace like any other task.

What it **will not** do:

- Search for, or keep, anything about health, sexuality, finances, family
  members or your home address. A search term that touches one of these is
  refused before anything is searched, and every finding is checked for them
  twice (by category and by Friday's sensitivity classifier). A finding that
  touches one is thrown away without being stored; you are told only how many.
- Take instructions from a page. Page text is treated as untrusted, lines that
  read like instructions are removed before a model sees it, and the model
  reading it has no tools, so nothing on a page can make Friday act.
- Keep anything you have not approved. Findings wait, encrypted, as short
  lines with their source address and the date they were read. You accept,
  edit or reject each one (or all at once). Only what you accept goes into
  Friday's knowledge graph, marked as coming from research with its source.
  Rejected findings are deleted.

With no model available, Friday says so and you can run it later from
**Settings → General → Your profile → Research on you**.

### 4. A few questions

About ten short questions on how you like to be spoken to: formal or casual,
humour, how blunt, how you want to hear that you are wrong, how much detail,
when you work and need to focus, what you value, what you want from an
assistant, and your pet peeves in how people communicate. Each has suggested
answers and room for your own words. **Skip** or **Rather not say** works on
every one, and so does typing "skip". The last question is the classic one,
and it is optional like the rest.

This is a communication-style profile, nothing more. Friday never diagnoses,
labels or guesses at anything clinical from it, and anything a model writes
that sounds like that is removed before you see it.

### 5. Friday for you

Your answers become six sliders (warmth, formality, humour, directness,
detail and pushback), a short summary, and a sample of how Friday would
answer an example question in that style. Move a slider and the sample
changes. **Save this style** adds it to Friday's personality; it keeps
evolving as you work together.

Two things cannot be tuned: honesty and asking before acting. Pushback has a
floor, so at its gentlest Friday still tells you when you are wrong, just
more softly. A style cannot switch off Friday's honesty rules or the rule that
she asks before any real-world action; those always come after the style in
her instructions, and text trying to override them is removed.

### Scheduled jobs on a PC with no local model

Friday does a few jobs on her own schedule: the morning news, the evening
front page, an afternoon briefing, a daily creation, and a heartbeat that
checks your mail and calendar. They run on a model on this computer, at no
cost. If this computer has no local model, they are paused, and the chat asks
once whether they may use a cloud model instead. The question lists the model
each job would use (Claude Haiku 4.5 by default) and its estimated monthly
cost, about $9 in total with the defaults.

- **Yes** lets them run on those models while there is no local model. The
  heartbeat then runs every 4 hours between 08:00 and 20:00.
- **No** keeps them paused.
- **Skip** leaves the question unanswered.

The question does not appear when a local model is installed, when you chose
"local only" on the "Where your words go" screen, or once you have answered
it. Change the answer any time in **Settings → Spending**. See
[Scheduled jobs](scheduled-jobs.md).

### 6. Finish

A summary of what is connected, where the research stands, and whether your
style was saved. **Open my desktop** completes setup.

## What is stored where

| What | Where | How |
|---|---|---|
| Which step you are on, your choices of name, profile and family mode, the routing choice, what you skipped | `.friday/setup_chat.json` | Plain file, no secrets and no answers |
| Whether scheduled jobs may use a cloud model | `.friday/settings.json`, `scheduled_cloud` | Plain file |
| Your name, your answers, the derived style | `.friday/profile/setup_profile.bin` | Encrypted |
| The conversation so far | `.friday/profile/setup_transcript.bin` | Encrypted |
| Research findings waiting for review | `.friday/profile/research/` | Encrypted |
| Accepted findings | Friday's knowledge graph | With "research" as their source and the page address |
| Keys and account tokens | Friday's credential store | Encrypted |
| The style Friday uses | A marked section in `.friday/SOUL.md` | Plain text: the six values, a summary and short notes, never your answers |

Nothing here is sent anywhere unless you chose a cloud model to read your
answers, and then only your answers go, to the model you were shown.

## Changing or deleting your profile

**Settings → General → Your profile** shows each question and your answer.
You can change answers, rebuild the style from them, move the sliders, and
**Run the setup chat again**. Running it again revisits each step and keeps
everything already connected and everything Friday has learned.

**Delete my profile** removes your answers, the setup conversation and the
style section from Friday's personality. Findings you accepted from research
stay in the knowledge graph, where you can remove them like any other entry.
