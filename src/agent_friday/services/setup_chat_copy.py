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
# `reader` comes after `connect` on purpose: a key connected in the checklist
# can be the thing that makes a model available to read the answers.
STAGES = ("welcome", "agent_name", "basics", "connect", "reader",
          "research_ask", "research_seeds", "questions", "style",
          "research_review", "scheduled_cloud", "finish", "done")

#: What the progress rail shows. Several internal stages share a label.
STAGE_GROUPS = (
    ("hello", "Hello", ("welcome", "agent_name", "basics")),
    ("connect", "Connect", ("connect",)),
    ("research", "Research", ("reader", "research_ask", "research_seeds")),
    ("about", "About you", ("questions",)),
    ("style", "Your Friday", ("style", "research_review")),
    ("finish", "Finish", ("scheduled_cloud", "finish", "done")),
)

SET_UP_LATER = "Set up later"
SKIP = "Skip"
RATHER_NOT = "Rather not say"

# Words that, typed on their own, mean "skip this one".
SKIP_WORDS = ("skip", "pass", "next", "rather not say", "rather not",
              "no comment", "prefer not to say", "n/a")


# ── Stage 1: hello ───────────────────────────────────────────────────────────

WELCOME = (
    "Hi. I'm Friday. Let's get me set up, which takes a few minutes and is "
    "entirely optional: you can type, tap an answer, skip anything, or leave "
    "with \"Set up later\" whenever you like.\n\n"
    "First things first: what should I call you?")

WELCOME_BACK = (
    "Welcome back. Nothing you connected or taught me is lost by running "
    "this again; we'll just revisit each part. What should I call you?")

AGENT_NAME = (
    "Nice to meet you, {name}. And what would you like to call me? Most "
    "people keep Friday.")

AGENT_NAME_NO_NAME = (
    "That's fine, I don't need a name to be useful. What would you like to "
    "call me? Most people keep Friday.")

BASICS = (
    "{agent} it is. A few quick basics: who this is for, a starting profile "
    "for how you work, and what this computer can run on its own. You can "
    "change any of it later in Settings.")


# ── Stage 2: connect everything ──────────────────────────────────────────────

CONNECT = (
    "Now the useful part: the things you already use. Each card says what "
    "connecting it unlocks and exactly what it asks for permission to do, "
    "before you connect anything.\n\n"
    "Keys go into the secure field on each card, straight into encrypted "
    "storage on this computer. They never go into this chat, and I will "
    "never ask for a password here. Skip whatever you like; everything stays "
    "in Settings > Accounts & Keys.")

CONNECT_DONE = "Done for now"

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
    "https://console.anthropic.com/settings/keys\n\n"
    "OpenRouter (the alternative): one account that reaches Claude and many "
    "other models. You buy credit up front and pay per use. Get a key at "
    "https://openrouter.ai/keys\n\n"
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
              "fails, Settings > Accounts & Keys is where to fix it.")

#: Leaving the connect stage in the cloud with no key: what will not work.
NO_KEY_YET = (
    "No AI key yet, so for now I can't think. Until you add one, chat, "
    "briefings, the front page, scheduled jobs and research won't work, and "
    "I'll use simple rules for the rest of setup. Add an Anthropic or an "
    "OpenRouter key any time in Settings > Accounts & Keys; one is enough, "
    "and nothing else needs redoing.")

#: The one-key line on the checklist (setup chat and Settings > Accounts & Keys).
ONE_KEY_IN_USE = ("Thinking with your {label} key. One key is enough; you "
                  "don't need another.")
ONE_KEY_NONE = ("One key is enough to think with: Anthropic, or OpenRouter "
                "instead. Nothing that needs a model will work until one is "
                "added.")
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
    "instructions. Everything stays on this computer, and nothing is kept "
    "until you have approved it line by line.")

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
    "One more question. I do a few jobs on my own schedule: the morning news, "
    "the evening front page, an afternoon briefing, a daily creation, and a "
    "heartbeat that checks your mail and calendar. They are meant to run on a "
    "model on this computer, which costs nothing, but this computer doesn't "
    "have one. Until it does, they are paused.\n\n"
    "May they use a cloud model instead? This is the model each would use, "
    "and roughly what it would cost:\n{lines}\n\n"
    "About {total} a month in total, billed to your account with the "
    "provider. The estimate errs on the high side. The heartbeat would run "
    "every {every}, between {start} and {end}. If you add a local model "
    "later, they move back to it. You can change this any time in "
    "Settings > Spending.")

#: One line per job in the question above.
SCHEDULED_CLOUD_LINE = "- {name}: {model}, about {usd} a month"

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
    "(shown in Settings > Accounts & Keys > Phone) and put the public address "
    "into the Phone settings.",
    "For remote access to Friday itself, set FRIDAY_REMOTE_KEY first: without "
    "it Friday refuses every request that does not come from this computer.",
)
