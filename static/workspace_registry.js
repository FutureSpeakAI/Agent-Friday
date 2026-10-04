/* FRIDAY'S WORKSPACES: the one list every surface reads.
   The dock, window and tab titles, deep-link titles, the command palette and
   notifications read it in the page; the navigate tools, voice and Friday's
   own prompts read it on the server (services/workspace_registry.py parses
   the JSON between the BEGIN and END markers, so it stays strict JSON:
   double quotes, no trailing commas, no comments inside).
   - id: stable. Deep links, saved layouts and settings key on it.
   - label: the name. It is also what people SAY, so it is short, easy to say
     and hard to mishear against every other label.
   - aliases: other words people use for it, spoken or typed. No alias may
     point at two workspaces, and none may be another workspace's label.
   - icon: assets/icons/<icon>.svg; glyph is the emoji shown where an image
     cannot be (and while it loads).
   - accent: a brand accent token (--ws-accent-<token>). Cyan is Friday's
     identity; the status hues (green, amber, red) are never an accent.
   - blurb: one plain line saying what it is for.
   - held: optional. Names a switch in settings.held_features; while that
     switch is off, nothing offers the workspace (dock, palette, navigate
     tools, voice) and it does not open. Its code and data stay.
   The brand system owns the values; this file is its front door.
 * `boundary` is what "this workspace" is when it is improved (salon spec §4.9.1):
 * for a native workspace the declared set of React components in index.html (and any
 * static script that renders it); an installed bundle workspace's boundary is the
 * bundle itself. Improving a native workspace means Friday's own source, never in place.
 */
window.FRIDAY_WORKSPACE_REGISTRY = /*BEGIN JSON*/{
  "groups": [
    {"id": "life", "label": "Life"},
    {"id": "work", "label": "Work"},
    {"id": "system", "label": "System"}
  ],
  "workspaces": [
    {"id": "news", "boundary": {"kind": "native", "components": ["NewsWS"]}, "label": "News", "group": "life", "core": true, "icon": "news", "glyph": "📰", "accent": "cyan",
     "blurb": "Your front page, feed and briefings, every source trust-scored.",
     "aliases": ["headlines", "feed", "newsfeed", "front page", "frontpage", "top stories", "breaking news", "newspaper", "the news"]},
    {"id": "messages", "boundary": {"kind": "native", "components": ["MessagesWS"], "scripts": ["static/friday_mail.js"]}, "label": "Messages", "group": "life", "core": true, "icon": "messages", "glyph": "💬", "accent": "cyan",
     "blurb": "All your mail in one inbox, sorted by what needs you.",
     "aliases": ["mail", "email", "emails", "gmail", "inbox", "dms", "chats", "texts", "messaging"]},
    {"id": "calendar", "boundary": {"kind": "native", "components": ["CalendarWS"]}, "label": "Calendar", "group": "life", "core": true, "icon": "calendar", "glyph": "📅", "accent": "cyan",
     "blurb": "Your day, your week and your meetings.",
     "aliases": ["schedule", "agenda", "events", "cal", "meetings"]},
    {"id": "family", "boundary": {"kind": "native", "components": ["FamilyWS"]}, "label": "Family", "group": "life", "core": false, "icon": "family", "glyph": "👪", "accent": "cyan",
     "blurb": "Family plans, dates and countdowns.",
     "aliases": ["household"]},
    {"id": "health", "boundary": {"kind": "native", "components": ["HealthWS"]}, "label": "Health", "group": "life", "core": false, "icon": "health", "glyph": "❤️", "accent": "cyan",
     "blurb": "Medications, appointments, insurance and vehicles.",
     "aliases": ["wellness", "fitness", "medical", "medications"]},
    {"id": "finance", "boundary": {"kind": "native", "components": ["FinanceWS"]}, "label": "Finance", "group": "life", "core": false, "icon": "finance", "glyph": "💰", "accent": "cyan",
     "blurb": "Your portfolio, card perks and money at a glance.",
     "aliases": ["money", "budget", "finances", "banking", "spending", "portfolio"]},
    {"id": "career", "boundary": {"kind": "native", "components": ["CareerWS"]}, "label": "Career", "group": "work", "core": true, "icon": "career", "glyph": "💼", "accent": "cyan",
     "blurb": "Your job search: roles evaluated, applications tracked.",
     "aliases": ["jobs", "job search", "job pipeline", "careers", "job"]},
    {"id": "contacts", "boundary": {"kind": "native", "components": ["ContactsWS"]}, "label": "People", "group": "work", "core": true, "icon": "contacts", "glyph": "👥", "accent": "cyan",
     "blurb": "Everyone you deal with, and what Friday remembers about them.",
     "aliases": ["contacts", "contact", "people graph", "address book", "relationships"]},
    {"id": "code", "boundary": {"kind": "native", "components": ["CodeWS"]}, "label": "Code", "group": "work", "core": true, "icon": "code", "glyph": "💻", "accent": "cyan",
     "blurb": "Your repos, git, files and running processes.",
     "aliases": ["coding", "editor", "ide", "code editor", "repos", "repositories"]},
    {"id": "futurespeak", "boundary": {"kind": "native", "components": ["FuturespeakWS", "SitesWS"]}, "label": "Sites", "group": "work", "core": true, "icon": "futurespeak", "glyph": "🌐", "accent": "cyan",
     "blurb": "Your websites: status, deploys and new projects.",
     "aliases": ["futurespeak", "future speak", "website", "websites", "web"]},
    {"id": "media", "label": "Media", "group": "work", "core": true, "icon": "media", "glyph": "🗂️", "accent": "cyan",
     "blurb": "Everything you make: one card per piece of work, three views, one editor.",
     "aliases": ["media workspace", "my work", "cards", "pipeline", "draft", "drafts", "writing", "writer", "content", "content studio", "posts", "publishing", "studio", "creations", "gallery", "create", "art", "creative"],
     "boundary": {"kind": "native", "components": ["MediaWS"], "scripts": ["static/media_ws.js"]}},
    {"id": "library", "label": "Library", "group": "work", "core": true, "icon": "library", "glyph": "📚", "accent": "cyan",
     "blurb": "The documents you gave Friday to read, answered with footnotes you can click.",
     "aliases": ["my documents", "document library", "my files", "shelves", "documents", "reading list", "file library"],
     "boundary": {"kind": "native", "components": ["LibraryWS"], "scripts": ["static/library_ws.js", "static/library_reader.js", "static/library_shelves.js"]}},
    {"id": "knowledge", "boundary": {"kind": "native", "components": ["KnowledgeWS"]}, "label": "Knowledge", "group": "system", "core": true, "icon": "knowledge", "glyph": "🌌", "accent": "violet",
     "blurb": "Your wiki's pages and the knowledge graph that links them.",
     "aliases": ["wiki", "pages", "notes", "knowledge base", "knowledgebase", "second brain", "knowledge graph", "galaxy", "wiki pages"]},
    {"id": "trust", "boundary": {"kind": "native", "components": ["TrustWS"]}, "label": "Trust", "group": "system", "core": false, "icon": "trust", "glyph": "🔗", "accent": "cyan",
     "blurb": "How far Friday trusts each person and source, and why.",
     "aliases": ["trust graph", "reputation", "trust score"]},
    {"id": "marketplace", "boundary": {"kind": "native", "components": ["MarketplaceWS"]}, "label": "Marketplace", "group": "system", "core": false, "icon": "marketplace", "glyph": "🛒", "accent": "cyan", "held": "federation",
     "blurb": "Buy, sell and share creations with other Friday agents.",
     "aliases": ["market", "store", "shop", "skill store"]},
    {"id": "workflows", "boundary": {"kind": "native", "components": ["WorkflowsWS"]}, "label": "Workflows", "group": "system", "core": false, "icon": "workflows", "glyph": "🧩", "accent": "cyan",
     "blurb": "Routines that run on their own and ask before anything outward.",
     "aliases": ["workflow", "routines", "scheduled tasks", "automations", "pipelines"]},
    {"id": "system", "boundary": {"kind": "native", "components": ["SystemWS"]}, "label": "System", "group": "system", "core": true, "icon": "system", "glyph": "🖥️", "accent": "cyan",
     "blurb": "Friday's health, her self-review, and approvals waiting on you.",
     "aliases": ["system health", "health check", "approvals"]},
    {"id": "settings", "boundary": {"kind": "native", "components": ["SettingsWS"]}, "label": "Settings", "group": "system", "core": true, "icon": "settings", "glyph": "⚙️", "accent": "cyan", "tab": false,
     "blurb": "Her name, models, accounts, privacy, look and voice.",
     "aliases": ["setting", "settings menu", "system settings", "preferences", "options", "config", "configuration"]}
  ]
}/*END JSON*/;
