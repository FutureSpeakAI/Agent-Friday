"""The plain-language guide behind "Connect your services".

`services/setup_connections.checklist()` knows WHAT can be connected and
whether it is: it reads the credential store, the provider registry, the
Google account index and the connectors. This module adds what a person needs
to decide and to do it: which category a service belongs to, how to get a key
in three steps, what it costs where that is known, whether Friday recommends
it, and exactly what the connection can reach.

RULES THIS DATA KEEPS
* Only services the code can use are connectable. Anything else is listed in
  COMING_SOON with no input and no link to sign up, so nobody pays for a key
  Friday cannot use.
* Every sign-up URL was opened and checked against the vendor's own page or
  documentation (2026-10-16). A URL changes here and nowhere else.
* Cost notes say only what the vendor states (free tier, prepaid, per use).
  Where that is not known, they say "see their pricing page" rather than
  guess a number.
* "Recommended" is a label with a reason. Nothing here is ever preselected:
  the screen ticks, fills and connects nothing on its own.
"""
from __future__ import annotations

#: (id, label, one-line description). The screen shows them in this order.
CATEGORIES = (
    ("models", "AI model providers",
     "The brains Friday can think with. One is enough; local AI needs none."),
    ("creative", "Creative and media",
     "Images, video, music and voices."),
    ("search", "Search and web",
     "Better web search and page reading. Without these Friday uses a free "
     "search that works, more roughly."),
    ("accounts", "Accounts",
     "Your own accounts, through each service's own sign-in screen or a token "
     "you create there."),
    ("mcp", "Add any MCP server",
     "MCP is an open standard for plugging tools into AI assistants. Paste a "
     "server's address or command, or pick one below."),
    ("more", "More",
     "Phone, social and publishing, messaging apps and network."),
)

#: The checklist's older groups, folded into the categories above.
GROUP_TO_CATEGORY = {
    "ai": "models", "voice": "creative", "search": "search", "google": "accounts",
    "developer": "accounts", "tools": "accounts", "messaging": "more",
    "phone": "more", "social": "more", "network": "more",
}

#: Item ids that sit in a different category from their old group.
ITEM_CATEGORY = {
    "connector:higgsfield": "creative",
    "provider:kie": "creative",
    "connector:slack": "accounts",
    "connector:discord": "accounts",
}

# ── What a connection can reach (the transparency lines) ─────────────────────

ACCESS_MODEL = (
    "Gets: the messages and context Friday sends it for the turns that run "
    "there. Your never-send list is checked first, every time.",
    "Can't: open your files, mail or anything else on this computer by itself.",
)
ACCESS_LOCAL = (
    "Nothing leaves this computer.",
)
ACCESS_SEARCH = (
    "Gets: the search words and web addresses Friday looks up. Your "
    "never-send list is checked first.",
    "Can't: see your conversations, files or accounts.",
)
ACCESS_VOICE = (
    "Gets: only the text you choose to have spoken in its voice.",
)
ACCESS_CREATIVE = (
    "Gets: the prompts and any images you hand it for a generation.",
    "Paid generations show their cost and ask you first.",
)
ACCESS_MCP = (
    "Gets: only what Friday passes to this server's tools.",
    "Anything from it that is not a plain read (sending, posting, changing or "
    "deleting) asks you first on a card.",
    "A server you run by command runs on this computer as you; add only "
    "servers you trust.",
)

# ── Per-service guide ────────────────────────────────────────────────────────
#
# signup: the official page where the key (or account) is made. how: three
# short steps, appended to the generic ones for its kind. cost: what the vendor
# says. recommended: the reason, or "" for none.

_KEY_STEPS = (
    "Open {label}'s page with the button below and sign in, or sign up.",
    "Create a new API key and copy it.",
    "Paste it into the secure field here, press Save securely, then Test "
    "connection.",
)

GUIDE: dict = {
    # Local
    "local": {
        "cost": "Free. Runs on this computer.",
        "recommended": "Most private: nothing leaves this computer.",
        "how": ("Friday's own local models are offered on the hardware card "
                "during setup and in Settings > Models.",
                "No account and no key."),
    },
    "local:ollama": {
        "signup": "https://ollama.com/download", "signup_label": "Get Ollama",
        "cost": "Free. Runs on this computer.",
        "how": ("Install Ollama from its download page and start it.",
                "Pull a model in Ollama (it lists the commands).",
                "Leave the address as it is unless Ollama runs elsewhere, then "
                "press Test connection."),
    },
    "local:lmstudio": {
        "signup": "https://lmstudio.ai/download", "signup_label": "Get LM Studio",
        "cost": "Free. Runs on this computer.",
        "how": ("Install LM Studio and download a model inside it.",
                "In LM Studio, start the local server (Developer tab).",
                "Leave the address as it is unless you changed the port, press "
                "Save, then Test connection."),
    },
    "custom:openai": {
        "cost": "Depends on the service.",
        "how": ("Use this for any service that speaks the OpenAI API "
                "(vLLM, a company gateway, another provider).",
                "Give it a short name and its base address, ending in /v1.",
                "If it needs a key, paste it into the secure field that "
                "appears after saving."),
    },
    # Model providers with a key
    "provider:anthropic": {
        "signup": "https://platform.claude.com/settings/keys",
        "cost": "Paid per use, from prepaid credits you add under Billing.",
        "recommended": "Friday's tools work best with Claude.",
        "extra": ("Add a little credit under Billing first: a key with no "
                  "credit is accepted but cannot answer.",),
    },
    "provider:openrouter": {
        "signup": "https://openrouter.ai/settings/keys",
        "cost": "Paid per use from prepaid credits; some models are free, "
                "with rate limits.",
        "extra": ("One OpenRouter key reaches many providers' models. You can "
                  "set a spending limit on the key itself.",),
    },
    "provider:openai": {
        "signup": "https://platform.openai.com/api-keys",
        "cost": "Paid per use.",
    },
    "provider:google-gemini": {
        "signup": "https://aistudio.google.com/app/apikey",
        "cost": "Has a free tier with rate limits; paid per use beyond it.",
        "extra": ("Gemini also powers the cloud voice and describing images.",),
    },
    "provider:groq": {
        "signup": "https://console.groq.com/keys",
        "cost": "Free tier, no card needed; rate-limited.",
    },
    "provider:mistral": {
        "signup": "https://console.mistral.ai",
        "cost": "Free mode with API access and no card; usage limits apply.",
        "extra": ("In the console, open API Keys in the sidebar.",),
    },
    "provider:xai": {
        "signup": "https://console.x.ai",
        "cost": "Paid per use; add credits first.",
    },
    "provider:deepseek": {
        "signup": "https://platform.deepseek.com/api_keys",
        "cost": "Paid per use; top up a balance first.",
    },
    "provider:together": {
        "signup": "https://api.together.ai/settings/projects/~current/api-keys",
        "cost": "Paid per use; set up billing first.",
    },
    "provider:huggingface": {
        "signup": "https://huggingface.co/settings/tokens",
        "cost": "Free account; hosted inference has monthly limits.",
    },
    "provider:fireworks": {
        "signup": "https://app.fireworks.ai/settings/users/api-keys",
        "cost": "Paid per use. See their pricing page.",
    },
    "provider:perplexity": {
        "signup": "https://console.perplexity.ai/",
        "cost": "Paid per use. See their pricing page.",
    },
    "provider:cohere": {
        "signup": "https://dashboard.cohere.com/api-keys",
        "cost": "Free trial keys with rate limits; production keys are paid.",
    },
    # Creative and media
    "provider:kie": {
        "signup": "https://kie.ai/api-key",
        "cost": "Paid per generation.",
    },
    "provider:elevenlabs": {
        "signup": "https://elevenlabs.io/app/settings/api-keys",
        "cost": "Free plan with a monthly character allowance; paid plans for more.",
    },
    "provider:inworld": {
        "signup": "https://platform.inworld.ai",
        "cost": "See their pricing page.",
    },
    "connector:higgsfield": {
        "signup": "https://higgsfield.ai", "signup_label": "Sign up",
        "cost": "Paid credits. Each generation shows its cost and asks first.",
        "how": ("Sign up on Higgsfield's site if you have no account.",
                "Press Connect: Higgsfield's own sign-in opens in your browser.",
                "Approve access there, then come back and press Test connection."),
    },
    # Search and web
    "provider:brave": {
        "signup": "https://api-dashboard.search.brave.com/app/keys",
        "cost": "Has a free plan; Brave may ask for a card when you sign up.",
        "extra": ("Choose the Search plan, not the AI plan.",),
    },
    "provider:firecrawl": {
        "signup": "https://www.firecrawl.dev/app/api-keys",
        "cost": "Free plan with limited credits; paid plans for more.",
    },
    # Accounts
    "google": {
        "signup": "https://accounts.google.com/signup", "signup_label": "Sign up",
        "cost": "Free.",
        "how": ("Tick any extra permission you want (sending, mailbox changes); "
                "both start off.",
                "Press Connect: Google's own consent screen opens in your browser.",
                "Approve there, come back, and press Test connection."),
    },
    "connector:github": {
        "signup": "https://github.com/settings/personal-access-tokens/new",
        "signup_label": "Make a token",
        "cost": "Free.",
        "how": ("Open GitHub's token page with the button below.",
                "Create a fine-grained token for the repositories you choose, "
                "and copy it.",
                "Paste it into the secure field, Save securely, then Test "
                "connection. GitHub's tools run in Docker, which must be "
                "installed."),
    },
    "connector:slack": {
        "signup": "https://api.slack.com/apps", "signup_label": "Create a Slack app",
        "cost": "Free.",
        "how": ("Create a Slack app for your workspace and install it.",
                "Copy its Bot token (xoxb-...) and your Team ID.",
                "Paste both here, Save securely, then Test connection."),
    },
    "connector:discord": {
        "signup": "https://discord.com/developers/applications",
        "signup_label": "Create a Discord app",
        "cost": "Free.",
        "how": ("Create an application with a bot and invite it to your server.",
                "Copy the bot token.",
                "Paste it here, Save securely, then Test connection."),
    },
    "connector:notion": {
        "signup": "https://www.notion.so/signup", "signup_label": "Sign up",
        "cost": "Free plan available.",
        "how": ("Press Connect: Notion's own sign-in opens in your browser.",
                "Pick the pages Friday may read, and approve.",
                "Come back and press Test connection."),
    },
    "connector:linear": {
        "signup": "https://linear.app/signup", "signup_label": "Sign up",
        "cost": "Free plan available.",
        "how": ("Press Connect: Linear's own sign-in opens in your browser.",
                "Approve access to your workspace.",
                "Come back and press Test connection."),
    },
}

#: The kinds whose access lines come from the tables above.
def access_for(item_id: str, category: str) -> tuple:
    if item_id.startswith(("local", "local:")):
        return ACCESS_LOCAL
    if item_id in ("provider:elevenlabs", "provider:inworld"):
        return ACCESS_VOICE
    if category == "creative":
        return ACCESS_CREATIVE
    if category == "search":
        return ACCESS_SEARCH
    if category == "mcp":
        return ACCESS_MCP
    if category == "models":
        return ACCESS_MODEL
    return ()


def how_for(item_id: str, label: str, kind: str) -> list:
    g = GUIDE.get(item_id) or {}
    if g.get("how"):
        steps = list(g["how"])
    elif kind == "secret":
        steps = [s.format(label=label) for s in _KEY_STEPS]
    else:
        steps = []
    steps.extend(g.get("extra") or ())
    return steps


# ── Not built yet ────────────────────────────────────────────────────────────
#
# Asked for, and not something Friday's code can use today. Listed so the
# person sees the answer instead of looking for it; no key field, no sign-up
# link (a key bought for these would sit unused).

COMING_SOON = (
    {"id": "soon:replicate", "category": "creative", "label": "Replicate",
     "note": "Not built in yet."},
    {"id": "soon:fal", "category": "creative", "label": "fal",
     "note": "Not built in yet."},
    {"id": "soon:stability", "category": "creative", "label": "Stability AI",
     "note": "Not built in yet."},
    {"id": "soon:runway", "category": "creative", "label": "Runway",
     "note": "Not built in yet."},
    {"id": "soon:tavily", "category": "search", "label": "Tavily",
     "note": "Not built in yet."},
    {"id": "soon:exa", "category": "search", "label": "Exa",
     "note": "Not built in as search yet. Exa's own MCP server can be added "
             "under Add any MCP server."},
    {"id": "soon:microsoft", "category": "accounts",
     "label": "Microsoft (Outlook, Calendar, OneDrive)",
     "note": "Not built in yet."},
    {"id": "soon:spotify", "category": "accounts", "label": "Spotify",
     "note": "Not built in yet."},
)

# ── Curated MCP servers ──────────────────────────────────────────────────────
#
# Remote servers over Streamable HTTP whose address is on the vendor's own
# documentation (checked 2026-10-16). Picking one fills the form; nothing is
# added until the person presses Add.

CURATED_MCP = (
    {"name": "context7", "label": "Context7 (code library docs)",
     "url": "https://mcp.context7.com/mcp",
     "what": "Up-to-date documentation for programming libraries.",
     "auth": "Works without an account; a free key raises its limits.",
     "docs": "https://github.com/upstash/context7"},
    {"name": "exa", "label": "Exa (web search)",
     "url": "https://mcp.exa.ai/mcp",
     "what": "Web search and page reading.",
     "auth": "Works without an account at a limited rate.",
     "docs": "https://exa.ai/docs/get-started/exa-mcp"},
    {"name": "huggingface", "label": "Hugging Face",
     "url": "https://huggingface.co/mcp",
     "what": "Search models, datasets, papers and Spaces.",
     "auth": "Sign in to Hugging Face for your own settings.",
     "docs": "https://huggingface.co/docs/hub/hf-mcp-server"},
    {"name": "cloudflare-docs", "label": "Cloudflare documentation",
     "url": "https://docs.mcp.cloudflare.com/mcp",
     "what": "Search Cloudflare's developer documentation.",
     "auth": "No account needed.",
     "docs": "https://developers.cloudflare.com/agents/model-context-protocol/"},
)

MCP_SPEC_URL = "https://modelcontextprotocol.io"

#: What people call each service out loud -> its checklist id. "Connect
#: OpenRouter" by voice opens that card on Settings > Connections with its
#: secure field focused; the key itself is only ever typed into that field.
SPOKEN_NAMES = {
    "anthropic": "provider:anthropic", "claude": "provider:anthropic",
    "openrouter": "provider:openrouter", "open router": "provider:openrouter",
    "openai": "provider:openai", "open ai": "provider:openai",
    "gemini": "provider:google-gemini", "google gemini": "provider:google-gemini",
    "groq": "provider:groq", "mistral": "provider:mistral",
    "xai": "provider:xai", "x ai": "provider:xai",
    "deepseek": "provider:deepseek", "deep seek": "provider:deepseek",
    "together": "provider:together", "together ai": "provider:together",
    "hugging face": "provider:huggingface", "huggingface": "provider:huggingface",
    "fireworks": "provider:fireworks", "perplexity": "provider:perplexity",
    "cohere": "provider:cohere", "kie": "provider:kie",
    "ollama": "local:ollama", "lm studio": "local:lmstudio", "lmstudio": "local:lmstudio",
    "elevenlabs": "provider:elevenlabs", "eleven labs": "provider:elevenlabs",
    "inworld": "provider:inworld", "higgsfield": "connector:higgsfield",
    "brave": "provider:brave", "brave search": "provider:brave",
    "firecrawl": "provider:firecrawl", "fire crawl": "provider:firecrawl",
    "google": "google", "gmail": "google", "google calendar": "google",
    "google drive": "google", "github": "connector:github",
    "slack": "connector:slack", "discord": "connector:discord",
    "notion": "connector:notion", "linear": "connector:linear",
    "an mcp server": "mcp:add", "mcp server": "mcp:add", "mcp": "mcp:add",
    "local ai": "local", "a custom endpoint": "custom:openai",
}


def spoken_connection(phrase: str) -> str:
    """The checklist id a spoken or typed phrase names, or "".

    Accepts "OpenRouter", "connect OpenRouter", "my OpenRouter key", "set up
    the Notion account". The whole remainder must be a service name: a
    sentence that merely mentions one ("search notion for my notes") is not a
    request to connect it.
    """
    import re
    t = re.sub(r"[^a-z0-9 ]+", " ", (phrase or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(?:please |friday )*(?:connect|hook up|link|set up|add|"
               r"sign in to|log in to|open)\s+(?:to )?", "", t)
    t = re.sub(r"^(?:my|the|a|an) ", "", t) if t not in SPOKEN_NAMES else t
    t = re.sub(r" (?:api key|key|keys|account|token|connection|settings|"
               r"card|form)$", "", t)
    return SPOKEN_NAMES.get(t.strip(), "")


def guide_fields(item_id: str, label: str, kind: str) -> dict:
    """The guide's fields for one checklist item: how, cost, recommended,
    signup_url/label. Empty strings where the guide has nothing to say."""
    g = GUIDE.get(item_id) or {}
    return {"how": how_for(item_id, label, kind),
            "cost": g.get("cost", ""),
            "recommended": g.get("recommended", ""),
            "signup_url": g.get("signup", ""),
            "signup_label": g.get("signup_label", "Get a key" if kind == "secret"
                                  else ("Sign up" if g.get("signup") else ""))}
