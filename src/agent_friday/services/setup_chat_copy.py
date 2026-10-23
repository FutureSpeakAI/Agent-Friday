"""setup_chat_copy -- every sentence the first-run setup chat says, in one place.

The setup chat is a conversation, but it is a scripted one: a fresh install may
have no API key and no local model, and the chat has to work anyway. So the
words live here, the state machine in services/setup_chat.py chooses which to
say, and nothing else composes onboarding text. A model is used only to read
the personality answers (see services/setup_profile.py); it never writes this
copy and it never decides what the chat asks next.

Promises made here are held by tests: the research explanation is checked
against the query generator in services/setup_research.py, and the question
list's shape (its length, its skippability, the exact final question) is
pinned in tests/unit/test_setup_chat_copy.py.
"""
from __future__ import annotations

# ── Stage names, in order ────────────────────────────────────────────────────
#
# The order is the first-run spec's (A0.2): how to talk, the offer, her
# identity, who you are, where she may think, then connections and the rest.
# The lock (the vault passphrase) is on the consent screens, before this chat,
# so nothing personal is asked before it exists. `reader` comes after
# `connect` on purpose: a key connected in the checklist can be the thing that
# makes a model available to read the answers.
STAGES = ("welcome", "offer", "agent_name", "voice", "pronouns", "framing",
          "about_name", "about_city", "about_tz", "basics", "posture", "never_send",
          "connect", "knowledge", "reader",
          "research_ask", "research_seeds", "questions", "style",
          "research_review", "money", "scheduled_cloud", "routines",
          "memory_rules", "household", "room_voice", "brain_warm",
          "finish", "done")

#: What the progress rail shows. Several internal stages share a label.
STAGE_GROUPS = (
    ("hello", "Hello", ("welcome", "offer")),
    ("me", "Me", ("agent_name", "voice", "pronouns", "framing")),
    ("you", "You", ("about_name", "about_city", "about_tz", "basics")),
    ("privacy", "Privacy", ("posture", "never_send")),
    ("connect", "Connect", ("connect",)),
    ("knowledge", "Your files", ("knowledge",)),
    ("research", "Research", ("reader", "research_ask", "research_seeds")),
    ("about", "Style", ("questions", "style", "research_review")),
    ("routine", "Money & routines", ("money", "scheduled_cloud", "routines")),
    ("house", "House rules", ("memory_rules", "household", "room_voice", "brain_warm")),
    ("finish", "Finish", ("finish", "done")),
)

SET_UP_LATER = "Set up later"
SKIP = "Skip"
RATHER_NOT = "Rather not say"
#: Shown only on a step that has a recommendation; it answers that step
#: (and no other) with the recommended choice, and says so.
USE_RECOMMENDED = "Use the recommended answer"
RECOMMENDED_SUFFIX = " (recommended)"

# Words that, typed on their own, mean "skip this one".
SKIP_WORDS = ("skip", "pass", "next", "rather not say", "rather not",
              "no comment", "prefer not to say", "n/a")


# ── Stage 1: hello, the contract, talk or type ───────────────────────────────

#: Said first, before any question (first-run spec A14).
CONTRACT = (
    "1. I'm a program on this PC. Nothing about you goes to the people who made me. Ever.\n"
    "2. You decide where I think: only here, or the cloud for some things. I'll ask in a minute.\n"
    "3. I ask before I send or delete anything, unless you've pre-approved it. "
    "Cloud models cost money as I use them; a daily spending cap limits that, and you set it.\n"
    "4. You can see everything I remember, and change or delete it.\n"
    "5. You can stop setup any time; I'll remember where we were.\n"
    "6. I'm not a lawyer or a financial adviser. When I help with your rights "
    "or your money, I'll show you my sources.")

WELCOME = (
    "Hi. I'm Friday. Six promises before anything else:\n\n" + CONTRACT + "\n\n"
    "Setup takes about 20 minutes. You can stop at any point and I'll pick up "
    "where we left off. Whatever you skip uses the safe default and can be "
    "changed later.\n\n"
    "Would you rather talk to me or type? You can switch any time.")

WELCOME_BACK = (
    "Welcome back. Nothing you connected or taught me is lost by running "
    "this again; we'll just revisit each part. The same promises hold:\n\n"
    + CONTRACT + "\n\nWould you rather talk to me or type?")

TALK_CHIPS = (("talk", "Talk"), ("type", "Type"),
              ("screen_reader", "I use a screen reader"))

TALK_OK = (
    "Talking it is. I'll read each line aloud with this computer's own "
    "built-in voice until my voice is installed; nothing I say leaves this "
    "PC. To answer by voice, hold Alt+T while you speak if dictation is set up "
    "here, or just type. Say \"type\" any time to stop me talking.")
TYPE_OK = "Typing it is. Say \"talk\" any time if you'd rather hear me."
SCREEN_READER_OK = (
    "Got it. I won't speak over your screen reader. Every step works with the "
    "keyboard, nothing here has a time limit, and each new line is announced "
    "once.")

RESUME = ("We got as far as {where}. Want to carry on? Just answer the "
          "question above, or use \"Set up later\".")


# ── Stage 1b: the offer (first-run spec B0; slice 1 offers the inbox) ─────────

OFFER = (
    "While we set things up, I can start on something useful. In this "
    "version that's cleaning up your inbox: I find the newsletters and "
    "mailing lists you never open, you tick which ones go, and I unsubscribe "
    "from those the standard one-click way. Finding you money and taking back "
    "your data are coming in a later update.\n\n"
    "Want me to clean up your inbox? Or none for now.")

OFFER_CHIPS = (("inbox", "Clean up my inbox"), ("none", "None for now"))

OFFER_INBOX = (
    "Good. For that I need to read your email, and only read it. When we get "
    "to connecting things, connect your email and I'll start in the "
    "background; the list is usually ready 5 to 15 minutes later. Nothing is "
    "unsubscribed until you tick it.")
OFFER_NONE = "No problem. I won't ask again. It's in Settings if you change your mind."


# ── Stage 2: her identity ────────────────────────────────────────────────────

AGENT_NAME = "First, me. What would you like to call me? Most people keep Friday."

VOICE_ASK = (
    "{agent} it is. Now pick a voice. These run on this computer: press "
    "\"Hear it\" beside one to listen, then choose.")
VOICE_NONE = (
    "{agent} it is. My own voice isn't installed on this computer yet, so "
    "there's nothing to listen to right now. You can pick one in Settings > "
    "Voice once it is.")
VOICE_LATER = "Choose later"
VOICE_SET = "{voice} it is."
VOICE_LATER_OK = "Fine; it waits in Settings > Voice."

PRONOUNS_ASK = (
    "How should I refer to myself: she, he, they, or something else? If you'd "
    "rather not choose, I'll just use my name.")
PRONOUNS_SET = "Thanks. I'll use {p}."
PRONOUNS_NAME_ONLY = "Then I'll use my name: \"{agent} will look into it.\""
PRONOUNS_OTHER = (
    "I can't use those properly yet, so for now I'll use my name instead of a "
    "pronoun. You can change it in Settings.")

FRAMING_ASK = (
    "And how do you want to think of me: as an assistant, a colleague, or "
    "more like family? It changes how I talk about our work, nothing else.")
FRAMING_SET = "Noted."


# ── Stage 3: who you are ─────────────────────────────────────────────────────

ABOUT_NAME = "Now you. What should I call you? A nickname is fine."
ABOUT_CITY = (
    "Nice to meet you, {name}. Which city are you in? It helps with weather "
    "and local news. You can add the state, like \"Springfield, IL\". Skip if "
    "you'd rather not say.")
ABOUT_CITY_NO_NAME = (
    "That's fine. Which city are you in? It helps with weather and local "
    "news. Skip if you'd rather not say.")
ABOUT_TZ = "Your time zone looks like {zone} from this computer. Is that right?"
ABOUT_TZ_ASK = (
    "Which time zone are you in? For example America/New_York or "
    "Europe/London.")
ABOUT_TZ_BAD = (
    "I don't know that one. Try a name like America/Chicago or Europe/Paris, "
    "or skip and I'll use this computer's.")
ABOUT_TZ_CHIPS = (("yes", "Yes"), ("no", "No, it's different"))
ABOUT_SAVED = (
    "I've written that on your \"About you\" card, beside this conversation. "
    "You can change or delete any line. I'll keep your name out of anything I "
    "send to a cloud model.")
ABOUT_SAVED_NO_NAME = (
    "I've written that on your \"About you\" card, beside this conversation. "
    "You can change or delete any line.")
ABOUT_NOTHING = "Nothing written about you. You can add it later in Settings."

BASICS = (
    "A few quick basics: who this is for, a starting profile for how you "
    "work, and what this computer can run on its own. You can change any of "
    "it later in Settings.")


# ── Stage 4: where I may think (first-run spec A3) ───────────────────────────

POSTURE_ASK = (
    "Last big question before we connect anything. Where am I allowed to "
    "think? There are three choices and nothing is picked for you; the card "
    "beside this says what each one means and costs. You can change it later "
    "in Settings.")

POSTURE_LABELS = {"local": "Only on this PC",
                  "public": "Cloud for public things",
                  "everything": "Cloud for everything"}

POSTURE_MEANS = {
    "local": ("Every model runs here, so nothing you say goes to an AI company. "
              "Services you connect, like Gmail or web search, still talk to "
              "their own servers. News and podcasts are written by the model on "
              "this PC."),
    "public": ("Your own words, files and mail stay on this PC. When I use the "
               "cloud for something public, like news or a web search, the "
               "safeguards stay on: personal details are not sent quietly."),
    "everything": ("Chat and routines may use the cloud, and choosing this is "
                   "your consent: everything except your never-send list may "
                   "go. Every call is still checked against that list and "
                   "logged, and your raw records (mail archives, bank "
                   "statements) only go if you say yes for that source."),
}
#: "Cloud for public things" on a PC that cannot think on its own: what it
#: really means there, said before the person chooses it.
POSTURE_MEANS_PUBLIC_NO_LOCAL = (
    "This PC can't run my thinking model, so our conversations go to the "
    "cloud with the safeguards on. Money, health, legal or identity details "
    "are not sent quietly: a card asks you first, showing exactly what would "
    "go and where, and you choose Send or keep it on this PC. Your never-send "
    "list is never sent, and I say why. Public things (news, web searches, "
    "briefings) use the cloud too.")
POSTURE_COSTS = {
    "local": "No cost. Needs a capable PC, and it's slower.",
    "public": "Your own cloud bill, for what uses the cloud.",
    "everything": "Your own cloud bill.",
}
POSTURE_LOCAL_PENDING = (
    "I'm still measuring this computer, so I can't promise that yet. "
    "Choose another for now, or skip and answer in Settings once it's measured")
POSTURE_LOCAL_CANT = "This computer can't run {what} well enough on its own"
POSTURE_SET = {
    "local": "Done: I think only on this PC.",
    "public": "Done: your own words stay here; public things may use the cloud, with the safeguards on.",
    "everything": "Done: I may use the cloud for chat and routines. Only your never-send list is refused.",
}
POSTURE_UNAVAILABLE = "That one isn't available on this computer: {why}. Pick another, or skip."
POSTURE_SKIPPED = (
    "Skipping it for now. Until you choose, every cloud call keeps all the "
    "safeguards on, and the question waits for you when setup ends.")

NEVER_SEND_ASK = (
    "Is there anything that must never leave this PC, whatever you chose? "
    "Names, topics, a client, a project. One per line, or separated by "
    "commas.\n\n"
    "Already on it: card numbers and bank or account numbers (you can "
    "remove them in Settings > Privacy & Data > Locks). Always kept back "
    "too: a key or password you paste, and your vault's finance, legal and "
    "family records.")
NEVER_SEND_SET = (
    "Added {n} to your never-send list. If a message to a cloud model "
    "contains one, I stop it rather than send it.")
NEVER_SEND_NONE = "Nothing added. The built-in protections still apply."


# ── Stage 2: connect everything ──────────────────────────────────────────────

CONNECT = (
    "Now the useful part: the things you already use. Each card says what "
    "connecting it unlocks and exactly what it asks for permission to do, "
    "before you connect anything.\n\n"
    "Keys go into the secure field on each card, straight into encrypted "
    "storage on this computer. They never go into this chat, and I will "
    "never ask for a password here. Skip whatever you like; everything stays "
    "in Settings > Connections.")

CONNECT_DONE = "Done for now"

# ── Stage 2b: start with your knowledge ──────────────────────────────────────
#
# The panel under this message (services/knowledge_folders.COPY) holds the two
# kinds of access in plain words: reading and indexing on this PC, and, as a
# separate choice, letting cloud models read a folder. This message only
# frames it; the choices are made on the panel and nothing is chosen for you.

KNOWLEDGE = (
    "Do you already keep notes or documents somewhere? If you point me at "
    "them, I can find things in them for you. This is optional, and you "
    "choose every folder yourself; nothing is picked for you.\n\n"
    "There are two different kinds of yes here. One lets me read and index a "
    "folder on this PC, and nothing leaves it. The other, separately and "
    "folder by folder, lets a cloud model read it for 30 days; it is off "
    "unless you turn it on, and I never suggest it for a vault or a private "
    "folder.\n\n"
    "\"Not now\" skips all of this. You can come back to it in Settings > "
    "Privacy & Data > Your knowledge.")

KNOWLEDGE_SKIP = "Not now"
KNOWLEDGE_DONE = "Saved my choices"
KNOWLEDGE_SKIPPED = "No problem. It will be in Settings > Privacy & Data > Your knowledge whenever you want it."
KNOWLEDGE_SAVED_LOCAL = "I'll read {n} here on this PC; nothing leaves it."
KNOWLEDGE_SAVED_CLOUD = "Cloud models may read {n} for the next 30 days."
KNOWLEDGE_SAVED_TAIL = ("Nothing else was touched, and you can change any of "
                        "it in Settings > Privacy & Data > Your knowledge.")
KNOWLEDGE_SAVED_NOTHING = ("You didn't choose anything, so nothing changed. "
                           "It will be in Settings > Privacy & Data > Your "
                           "knowledge whenever you want it.")

# ── One key is enough (cloud mode) ───────────────────────────────────────────
#
# Said at the connect stage when the user chose the cloud and there is no
# model on this computer. Anthropic first, OpenRouter as the alternative;
# either key alone is enough (services/one_key.py holds that promise).

CONNECT_ONE_KEY = (
    "You chose the cloud, so I need one AI key to think with. One key is "
    "enough, and either of these works on its own:\n\n"
    "Anthropic (recommended): Claude, from the company that makes it. You "
    "pay Anthropic only for what you use. Get a key at "
    "https://platform.claude.com/settings/keys\n\n"
    "OpenRouter (the alternative): one account that reaches Claude and many "
    "other models. You buy credit up front and pay per use. Get a key at "
    "https://openrouter.ai/settings/keys\n\n"
    "Paste the key into the API key field on the Anthropic or OpenRouter "
    "card, not into this chat. I'll check it works as soon as you save it.")

CONNECT_ONE_KEY_HAVE = (
    "You already have an {label} key, and that one key is enough: I can "
    "think with it. You don't need the other one.")

#: After a key is saved: the verdict sentence, then one of these.
KEY_CAN_THINK = "That one key is enough. I can think now."
KEY_CANNOT_THINK = ("I still can't think. Try the key again, or use the other "
                    "provider instead: one key is enough.")
KEY_UNSURE = ("I'll try it for real the first time you talk to me; if it "
              "fails, Settings > Connections is where to fix it.")

#: Leaving the connect stage in the cloud with no key: what will not work.
NO_KEY_YET = (
    "No AI key yet, so for now I can't think. Until you add one, chat, "
    "briefings, the front page, scheduled jobs and research won't work, and "
    "I'll use simple rules for the rest of setup. Add an Anthropic or an "
    "OpenRouter key any time in Settings > Connections; one is enough, "
    "and nothing else needs redoing.")

#: The one-key line on the checklist (setup chat and Settings > Connections).
ONE_KEY_IN_USE = ("Thinking with your {label} key. One key is enough; you "
                  "don't need another.")
ONE_KEY_NONE = ("One key is enough to think with: Anthropic, or OpenRouter "
                "instead. Nothing that needs a model will work until one is "
                "added.")
#: No cloud key, but a local mode with a model on this PC: chat works without one.
ONE_KEY_NONE_LOCAL = ("You don't need a key: I'm thinking on this computer. Add "
                      "Anthropic or OpenRouter only if you want the cloud.")
ONE_KEY_LINK_LABELS = {"anthropic": "Get an Anthropic key",
                       "openrouter": "Get an OpenRouter key"}

#: What each of the two cards says it unlocks, in plain words.
PROVIDER_UNLOCKS = {
    "anthropic": ("Claude, from the company that makes it. Pay as you go. "
                  "This one key is enough for everything Friday thinks about."),
    "openrouter": ("The alternative to Anthropic: one account for Claude and "
                   "many other models, paid from prepaid credit. This one key "
                   "is enough on its own."),
}

#: The guard's reply when something key-shaped is typed into the chat box.
#: The value itself is never repeated.
KEY_IN_CHAT = (
    "That looks like a key ({label}). I didn't send it anywhere, and it isn't in "
    "this conversation. Keys belong in the secure field on the matching "
    "card, which stores them encrypted on this computer.")

#: When text is typed at a step that is answered with the options or a card.
USE_OPTIONS = "Tap one of the options, or use the card beside this conversation."


# ── Who reads your answers ───────────────────────────────────────────────────

READER_LOCAL = (
    "Before the more personal part: when I read your answers, I'll use "
    "{model}, which runs on this computer. Nothing you tell me here leaves it.")

READER_CLOUD_OFFER = (
    "Before the more personal part: there is no model running on this "
    "computer yet. You chose the cloud when we talked about where your words "
    "go, so I could read your answers with {model} at {provider}. That would "
    "send your answers to {provider}. Or I can keep everything here and use "
    "simple rules instead, which work fine and can be refined later.")

READER_RULES = (
    "Before the more personal part: there's no model connected yet, so I'll "
    "turn your answers into a style with simple rules on this computer. It "
    "works without any model, and you can adjust everything afterwards.")

READER_CHIPS_CLOUD = (("cloud", "Use {model}"),
                      ("rules", "Keep it on this computer"))
READER_CHIP_OK = (("ok", "Sounds good"),)


# ── Stage 3: research on you (strictly opt-in) ───────────────────────────────

RESEARCH_ASK = (
    "Optional, and only if you want it: I can look you up on the public web, "
    "so I start out knowing your work instead of asking about it.\n\n"
    "What I would search: only the name, handles, websites and employer you "
    "type in next, and nothing else. I look at public pages: published work, "
    "talks, public profiles, your own sites. What I never look for, and throw "
    "away if I stumble on it: health, sexuality, finances, anything private "
    "about your family, and your home address.\n\n"
    "Pages I read are treated as untrusted text: nothing on them can give me "
    "instructions.\n\n"
    "What leaves this computer: the searches themselves go to a web search "
    "service, and the pages are fetched from the sites that host them. If "
    "you chose the cloud model a moment ago, the text of those pages is sent "
    "to it to be read; if you kept it on this computer, they are read here. "
    "Nothing is kept until you have approved it line by line.")

RESEARCH_CHIPS = (("yes", "Yes, look me up"), ("no", "No thanks"))

RESEARCH_NO_MODEL = (
    "I'd need a language model to read what I find, and there isn't one "
    "available to me yet. You can run this later from Settings > General > "
    "Your profile once one is connected.")

RESEARCH_DECLINED = "No problem. I won't look."

RESEARCH_SEEDS = (
    "Tell me what to search for. Only these, nothing else: your name as it "
    "appears in public, any handles, your own websites, and an employer if "
    "you want it included.")

RESEARCH_STARTED = (
    "Started. It runs in the background as a task, so you can watch it in "
    "the tray while we carry on. When it's done you'll review every finding "
    "before any of it is kept.")

RESEARCH_REFUSED_SEEDS = (
    "I left out {n} of those because they would steer the search toward "
    "things I don't look for.")

RESEARCH_REVIEW = (
    "Here is what I found on the public web. Each line says where it came "
    "from and when I read it. Accept what's right, fix what's close, reject "
    "the rest. Only what you accept is kept.")

RESEARCH_STILL_RUNNING = (
    "The search is still running. You can review what it finds from "
    "Settings > General > Your profile whenever it's done; nothing is kept "
    "until you do.")

RESEARCH_NOTHING = (
    "The search finished and found nothing I could stand behind, so there is "
    "nothing to review.")


# ── Stage 4: personality questions ───────────────────────────────────────────

QUESTIONS_INTRO = (
    "Now a few questions about how you like to be spoken to. There are no "
    "right answers, tap or type whatever fits, and skip anything. This is "
    "about communication style, nothing more.")

MOTHER_LEAD_IN = (
    "Last one. Every good onboarding ends on the classic question, so I'd be "
    "letting Freud down if I didn't ask. Entirely optional, and \"rather not "
    "say\" is a perfectly good answer.")

MOTHER_QUESTION = "What was the relationship with your mother like?"

#: The recommended quiet-hours choice. It is offered, never preselected.
QUIET_RECOMMENDED = "22:00 to 07:00 (Recommended)"

#: The questions, in order. Each: (id, question, chips, placeholder).
#: Every question also accepts free text and the Skip / Rather not say chips.
QUESTIONS = (
    ("address",
     "How do you like to be spoken to: more formal, or casual?",
     ("Formal", "Friendly but professional", "Casual", "Whatever fits the moment"),
     "Or describe it in your own words"),
    ("humor",
     "How much humour do you want from me?",
     ("None, please", "A little, dry", "Plenty"),
     "Or tell me what makes you laugh"),
    ("directness",
     "When you ask me something, do you want the blunt answer or a gentler "
     "lead-in?",
     ("Blunt", "Direct but kind", "Gentle"),
     "Or say how you like it"),
    ("challenge",
     "When you're wrong about something, how should I tell you?",
     ("Straight out", "Point it out gently", "Ask me a question that shows it"),
     "Or describe it"),
    ("detail",
     "How much detail do you usually want?",
     ("Just the answer", "The answer and why", "Everything, with options"),
     "Or tell me"),
    ("rhythm",
     "When do you tend to work, and when do you need to focus without "
     "interruptions?",
     ("Early mornings", "Normal office hours", "Late nights", "It varies"),
     "e.g. deep work before lunch, meetings after"),
    ("quiet",
     "Would you like quiet hours? In them only the urgent things interrupt me, and the rest waits "
     "for the morning. You can change this any time in Settings.",
     ("Not now", QUIET_RECOMMENDED),
     "Or tell me when, and I'll point you to Settings"),
    ("values",
     "What matters most to you in your work?",
     ("Getting it right", "Moving fast", "Craft", "The people"),
     "In your own words"),
    ("want",
     "What do you most want from an assistant like me?",
     ("Keep me organised", "A second brain", "Someone to argue with",
      "Take work off my plate"),
     "In your own words"),
    ("peeves",
     "Any pet peeves in how people communicate with you?",
     ("Waffle", "Jargon", "Over-apologising", "Exclamation marks"),
     "Anything that grates"),
    ("mother", MOTHER_QUESTION, (), "Only if you feel like it"),
)

#: Acknowledgements between questions, used in rotation. Short on purpose.
ACKS = ("Got it.", "Noted.", "Good to know.", "Thanks.", "Makes sense.",
        "Understood.", "Okay.", "Helpful, thanks.", "Right.")

SKIPPED_ACK = "Skipping that one."


# ── Stage 5: Friday for you ──────────────────────────────────────────────────

STYLE_INTRO = (
    "Here's how I'll talk to you, based on that. Move any slider and the "
    "example changes with it. Save it and it becomes part of my personality; "
    "it keeps evolving as we work together.")

STYLE_INTRO_SKIPPED = (
    "You skipped the questions, so here's a sensible middle to start from. "
    "Move any slider and the example changes with it.")

STYLE_SAVED = "Saved. That's how I'll talk to you from now on."
STYLE_SKIPPED = "Leaving my default style in place. You can tune it any time in Settings."

#: The fixed prompt the sample reply answers, so a slider's effect is visible.
EXAMPLE_PROMPT = ("I'm thinking of rewriting our whole app in a new framework "
                  "this weekend. Good idea?")

SLIDERS = (
    ("tone", "Warmth", "Cool", "Warm"),
    ("formality", "Formality", "Casual", "Formal"),
    ("humor", "Humour", "None", "Plenty"),
    ("directness", "Directness", "Gentle", "Blunt"),
    ("verbosity", "Detail", "Brief", "Thorough"),
    ("pushback", "Pushback", "Gentle", "Firm"),
)

#: The lowest the pushback slider goes. Friday still says when the user is
#: wrong at the floor; it only says it more softly.
PUSHBACK_FLOOR = 20


# ── Scheduled jobs on a cloud model (only on a PC with no local model) ───────

SCHEDULED_CLOUD_ASK = (
    "One more question. Two jobs run on my own schedule: a daily creation, "
    "and a heartbeat that checks your mail and calendar. They are meant to "
    "run on a model on this computer, which costs nothing, but this computer "
    "doesn't have one. Until it does, they are paused. Your News (the front "
    "pages and briefings) always runs on this computer and waits for it; it "
    "never uses a cloud model.\n\n"
    "May they use a cloud model instead? This is the model each would use, "
    "and roughly what it would cost:\n{lines}\n\n"
    "About {total} a month in total, billed to your account with the "
    "provider. The estimate errs on the high side. The heartbeat would run "
    "every {every}, between {start} and {end}. If you add a local model "
    "later, they move back to it. You can change this any time in "
    "Settings > Spending.")

#: One line per job in the question above.
SCHEDULED_CLOUD_LINE = "- {name}: {model}, about {usd} a month"

# ── Spoken approvals when other people are in the room ───────────────────────
#
# Yours to set, so you are asked rather than told. It is not a limit on what
# voice can do: it decides WHOSE "yes" counts when I cannot tell one voice
# from another. Skipping keeps the careful answer.

ROOM_VOICE_ASK = (
    "On the same subject: when you talk to me with someone "
    "else in the room, I hear everyone, and I cannot yet tell your voice from "
    "theirs.\n\n"
    "That matters for the approvals I ask you for out loud — sending a "
    "message, spending money, sharing something private. I can require that "
    "the approval says my name, so \"Friday, send it\" counts and a bare "
    "\"yeah, send it\" from across the room does not.\n\n"
    "It is one extra word from you, and without it anyone within earshot can "
    "approve. Which would you like? You can change it any time in "
    "Settings > Voice.")

ROOM_VOICE_CHIPS = (("name", "Require my name"),
                    ("anyone", "Anyone in the room may approve"),
                    ("skip", SKIP))

ROOM_VOICE_NAME = (
    "Set: in a room, a spoken approval has to say my name. On your own, "
    "nothing changes — \"send it\" is enough.")

ROOM_VOICE_ANYONE = (
    "Set: in a room, anyone I can hear may approve out loud. The cards "
    "themselves are unchanged, so you can still see every one on screen.")

#: Letting anyone in the room approve loosens who may say yes, so it takes a press on screen.
ROOM_VOICE_NEEDS_CLICK = (
    "That one needs a press on the screen: tap \"Anyone in the room may "
    "approve\" beside this chat. Until then, in a room, a spoken approval "
    "has to say my name.")

ROOM_VOICE_SKIPPED = (
    "Left as it is: in a room, a spoken approval says my name. It is in "
    "Settings > Voice whenever you want it.")

# ── The local brain between sessions ─────────────────────────────────────────
#
# His to set, so he is asked. Handing it back is the default: the machine is
# his again the moment he has closed Friday, and a seat holding most of the
# graphics card starves whatever he opens next. A planned restart keeps the
# seat either way, so this is only about quitting.

BRAIN_WARM_ASK = (
    "One last thing, and it is about your computer rather than about me. To "
    "think privately I load a large model, and it stays in memory the whole "
    "time I am open: about 14 GB of memory and most of your graphics card.\n\n"
    "When you quit me, should I hand that back?\n\n"
    "Handing it back keeps the rest of your computer fast, and costs me about "
    "a minute to load again next time. Keeping it warm means I can think "
    "privately the instant you return, and that memory stays with me while I "
    "am closed. Either way I keep it through a restart. Ask me to change it "
    "whenever you like, or run this setup again.")

BRAIN_WARM_CHIPS = (("release", "Hand it back when I quit"),
                    ("keep", "Keep it warm for me"),
                    ("skip", SKIP))

BRAIN_WARM_RELEASE = (
    "Set: quitting me takes the model out of memory and gives it back to your "
    "computer. I will take about a minute to think privately the next time "
    "you open me.")

BRAIN_WARM_KEEP = (
    "Set: I keep the model loaded after you quit, so I am ready straight "
    "away. If something else ever needs that memory, this is the setting to "
    "change.")

BRAIN_WARM_SKIPPED = (
    "Left as it is: quitting me hands the memory back to your computer. Ask "
    "me to keep it warm whenever you want that instead.")

SCHEDULED_CLOUD_CHIPS = (("yes", "Yes, use the cloud"),
                         ("no", "No, keep them paused"),
                         ("skip", "Skip"))

SCHEDULED_CLOUD_YES = (
    "Done. They'll run on the cloud while this computer has no local model, "
    "and what they cost shows in Settings > Spending.")

SCHEDULED_CLOUD_NO = (
    "Understood. They stay paused until this computer has a local model. You "
    "can change your mind in Settings > Spending.")

SCHEDULED_CLOUD_SKIPPED = (
    "Skipping it. They stay paused for now, and the question waits in "
    "Settings > Spending.")


# ── Money (first-run spec A6; asked only when the cloud is in play) ──────────

MONEY_ASK = (
    "Some things I can do cost money when they use a cloud model, billed to "
    "your own account with the provider. {now} Keep it, pick another amount, "
    "or have no daily cap.{estimate}")
MONEY_NOW = ("Right now I stop spending on cloud AI after ${usd:.2f} a day. "
             "Work that reaches it pauses and asks you; models on this PC keep working.")
MONEY_NOW_NONE = "Right now there is no daily cap on what I spend on cloud AI."
MONEY_ESTIMATE = ("\n\nWith what you've chosen so far, my scheduled jobs would "
                  "cost about {usd} a month. That's an estimate, and it errs high.")
MONEY_ESTIMATE_OVER_CAP = (" That is more than ${cap:.2f} a day allows, so on a busy day "
                           "they would pause at the cap.")
MONEY_OTHER_HINT = "Or type an amount a day"
MONEY_SET = ("Set: I stop spending on cloud AI after ${usd:.2f} a day. "
             "You can change it in Settings > Spending.")
MONEY_NONE = "No daily cap, then. You can add one any time in Settings > Spending."
MONEY_SKIPPED = ("Skipping it: the daily cap stays at ${usd:.2f}. "
                 "It's in Settings > Spending whenever you want to change it.")
MONEY_SKIPPED_NONE = "Skipping it: there is no daily cap. It's in Settings > Spending whenever you want one."
MONEY_NOT_ASKED = "Nothing I do on this PC costs money, so there's nothing to cap."


# ── Routines (first-run spec A7) ─────────────────────────────────────────────

ROUTINES_ASK = (
    "I can make you a few things on a schedule. Pick any, or none: nothing "
    "runs unless you tick it. Each one says when it runs and where, and you "
    "can change the time.")
ROUTINES_DONE = "Done"
ROUTINES_NONE = "None for now"
ROUTINES_SET = "Scheduled: {names}. Everything else is off. You can change any of them in Workflows."
ROUTINES_SET_NONE = "Nothing is scheduled. You can turn any of these on in Workflows."
ROUTINE_PODCAST_WHAT = "a short spoken episode of each edition you tick, in my voice"
ROUTINE_WHERE = {
    "local": "runs on this PC",
    "public": "may use the cloud, with the safeguards on",
    "everything": "may use the cloud",
    "": "runs on this PC, or waits for it",
}


# ── Memory and approval rules (first-run spec A9) ────────────────────────────

MEMORY_RULES = (
    "Here's what I remember on my own, and what I always ask about. This is "
    "how I work today; the card beside this lists both sides.")
MEMORY_SAVE_WITHOUT_ASKING = (
    "Things you tell me about yourself, onto your \"About you\" card, which you can see and edit",
    "Preferences you state, like \"shorter replies\"",
    "Facts from your sources go to a review queue first, not straight into memory",
    "My own working notes about how we work together",
)
MEMORY_ALWAYS_ASK = (
    "Anything I'd send: emails, messages, posts, requests, forms (unless you pre-approve a recipient)",
    "Anything that spends money, beyond the cloud models you chose (they stay under your daily spending cap)",
    "Anything that deletes or overwrites your files or data",
    "Any memory change after I've read outside content, like an email or a web page",
    "Changing a setting, a permission or a rule",
)
MEMORY_RULES_CHIPS = (("ok", "Looks right"),)
MEMORY_RULES_OK = "Good. You can see and change everything I remember in Settings > Privacy & Data."


# ── Household (first-run spec A10) ───────────────────────────────────────────

HOUSEHOLD_ASK = "Will anyone else use this PC?"
HOUSEHOLD_CHIPS = (("no", "No, just me"), ("yes", "Yes"))
HOUSEHOLD_NO = "Just you, then."
HOUSEHOLD_YES = (
    "For now I work for one person per Windows account. The safest way to "
    "share this PC is for each person to have their own Windows sign-in; each "
    "gets their own Friday with their own locked data. I can't keep two "
    "people's things apart inside one account yet. The button beside this "
    "opens Windows' own page for adding someone. I don't put anything on the "
    "Windows lock screen.")


# ── The receipt (first-run spec A13) ─────────────────────────────────────────

RECEIPT_TITLE = "What we set up"
RECEIPT_NOTE = ("Each line says who decided: you, the recommended answer you "
                "accepted, or the safe default because it was skipped. This "
                "record is kept on this PC and holds no answers, only what "
                "kind of answer each was.")


# ── Stage 6: finish ──────────────────────────────────────────────────────────

FINISH = (
    "That's everything. Here's what we set up. Anything you skipped is waiting "
    "in Settings, and you can run this conversation again from Settings > "
    "General > Your profile.")

FINISH_BUTTON = "Open my desktop"


# ── Connection checklist: plain words ────────────────────────────────────────

#: Google scopes in plain words. The checklist shows each one before connect.
GOOGLE_SCOPE_WORDS = {
    "https://www.googleapis.com/auth/gmail.readonly": "Read your email",
    "https://www.googleapis.com/auth/calendar": "See and edit your calendar events",
    "https://www.googleapis.com/auth/drive.readonly": "Read your Drive files",
    "https://www.googleapis.com/auth/documents.readonly": "Read your Google Docs",
    "https://www.googleapis.com/auth/spreadsheets.readonly": "Read your Google Sheets",
    "https://www.googleapis.com/auth/tasks": "See and edit your Google Tasks",
    "https://www.googleapis.com/auth/contacts.readonly": "Read your contacts",
    "https://www.googleapis.com/auth/userinfo.email": "See your email address",
    "https://www.googleapis.com/auth/gmail.send":
        "Send email as you. Every message still waits for your approval card.",
    "https://www.googleapis.com/auth/gmail.modify":
        "Change your mailbox: labels, archive, star, read/unread and drafts. "
        "Never permanent delete.",
    "https://www.googleapis.com/auth/youtube.upload": "Upload videos to your channel",
    "https://www.googleapis.com/auth/youtube": "Manage your YouTube account",
    "https://www.googleapis.com/auth/yt-analytics.readonly": "Read your channel analytics",
}

#: Social-platform scopes in plain words, where the vendor's name is opaque.
PLATFORM_SCOPE_WORDS = {
    "w_member_social": "Post on your behalf",
    "openid": "Confirm who you are",
    "profile": "Read your basic profile",
    "tweet.read": "Read posts",
    "tweet.write": "Post on your behalf",
    "users.read": "Read your profile",
    "offline.access": "Stay connected without asking again",
    "write:statuses": "Post on your behalf",
    "write:media": "Upload images",
    "read:statuses": "Read posts",
    "identity": "Confirm who you are",
    "submit": "Post on your behalf",
    "flair": "Set post flair",
    "read": "Read posts",
    "user.info.basic": "Read your basic profile",
    "user.info.stats": "Read your follower counts",
    "video.publish": "Publish videos",
    "video.upload": "Upload drafts",
    "video.list": "List your videos",
    "instagram_business_basic": "Read your business profile",
    "instagram_business_content_publish": "Publish posts",
    "app_password": "An app password you create (not your real password)",
    "basicProfile": "Read your basic profile",
    "publishPost": "Publish posts",
}

#: One line each: what connecting a service unlocks.
UNLOCKS = {
    "google": "Mail, calendar, Drive, Docs, Sheets, contacts and tasks in your briefings and workspaces.",
    "twilio": "A phone number Friday can answer, take voicemail on, and text you from.",
    "connector:github": "Issues, pull requests and repository search; PRs awaiting your review.",
    "connector:slack": "Read channels, search messages, and post updates you approve.",
    "connector:discord": "Read and post to your Discord servers.",
    "connector:linear": "Your assigned issues, projects and cycles.",
    "connector:notion": "Search and read the Notion pages you choose.",
    "connector:higgsfield": "Image, video and audio generation on your Higgsfield account.",
    "channel:telegram": "Talk to Friday from Telegram.",
    "channel:discord": "Talk to Friday from a Discord bot.",
    "provider:brave": "Web search through Brave's API.",
    "provider:firecrawl": "Web search and page reading through Firecrawl.",
    "provider:elevenlabs": "ElevenLabs voices, when you choose a cloud voice.",
    "provider:inworld": "Inworld voices, when you choose a cloud voice.",
    "cloudflare": "A public address for the phone line or remote access.",
    "local": "Chat that runs entirely on this computer, with no key.",
}

PROVIDER_PERMISSION = (
    "Uses your API key to send the prompts you route to {label}; usage is "
    "billed to your {label} account.")

CLOUDFLARE_STEPS = (
    "Friday holds no Cloudflare credential. A tunnel is set up outside Friday "
    "with Cloudflare's own `cloudflared` tool, signed in to your Cloudflare "
    "account in your browser.",
    "For the phone line, point a named tunnel at the phone's local address "
    "(shown in Settings > Connections > Phone) and put the public address "
    "into the Phone settings.",
    "For remote access to Friday itself, set FRIDAY_REMOTE_KEY first: without "
    "it Friday refuses every request that does not come from this computer.",
)
