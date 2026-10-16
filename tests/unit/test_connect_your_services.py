"""Connect your services: the guide, the voice route, the screen, the terminal.

The screen itself is served by GET /api/setup/connections (its HTTP behaviour
is in tests/api/test_connect_your_services_routes.py). These tests hold the
rules that do not need a server: every entry Friday shows is one its code can
use, every link is one that was checked, recommended is a label and never a
default, "I'll just use local AI" is an answer, and "connect OpenRouter" by
voice opens that card's secure field and nothing else.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import connections_guide as cg

ROOT = Path(__file__).resolve().parents[2]

#: Opened and checked against each vendor's own page or documentation on
#: 2026-10-16. A new link is checked the same way before it is added here.
VERIFIED_URLS = frozenset({
    "https://platform.claude.com/settings/keys",
    "https://openrouter.ai/settings/keys",
    "https://platform.openai.com/api-keys",
    "https://aistudio.google.com/app/apikey",
    "https://console.groq.com/keys",
    "https://console.mistral.ai",
    "https://console.x.ai",
    "https://platform.deepseek.com/api_keys",
    "https://api.together.ai/settings/projects/~current/api-keys",
    "https://huggingface.co/settings/tokens",
    "https://app.fireworks.ai/settings/users/api-keys",
    "https://console.perplexity.ai/",
    "https://dashboard.cohere.com/api-keys",
    "https://kie.ai/api-key",
    "https://ollama.com/download",
    "https://lmstudio.ai/download",
    "https://higgsfield.ai",
    "https://elevenlabs.io/app/settings/api-keys",
    "https://platform.inworld.ai",
    "https://api-dashboard.search.brave.com/app/keys",
    "https://www.firecrawl.dev/app/api-keys",
    "https://accounts.google.com/signup",
    "https://github.com/settings/personal-access-tokens/new",
    "https://api.slack.com/apps",
    "https://discord.com/developers/applications",
    "https://www.notion.so/signup",
    "https://linear.app/signup",
    "https://mcp.context7.com/mcp", "https://github.com/upstash/context7",
    "https://mcp.exa.ai/mcp", "https://exa.ai/docs/get-started/exa-mcp",
    "https://huggingface.co/mcp", "https://huggingface.co/docs/hub/hf-mcp-server",
    "https://docs.mcp.cloudflare.com/mcp",
    "https://developers.cloudflare.com/agents/model-context-protocol/",
    "https://modelcontextprotocol.io",
})


# ── The guide: categories, links, honesty ────────────────────────────────────

def test_the_five_asked_for_categories_come_first_in_order():
    ids = [c[0] for c in cg.CATEGORIES]
    assert ids[:5] == ["models", "creative", "search", "accounts", "mcp"]
    assert all(c[1] and c[2] for c in cg.CATEGORIES)


def test_every_link_in_the_guide_is_a_verified_one():
    urls = [g["signup"] for g in cg.GUIDE.values() if g.get("signup")]
    urls += [m["url"] for m in cg.CURATED_MCP] + [m["docs"] for m in cg.CURATED_MCP]
    urls.append(cg.MCP_SPEC_URL)
    unverified = sorted(set(urls) - VERIFIED_URLS)
    assert not unverified, unverified


def test_the_registry_sign_up_links_are_the_verified_ones():
    from agent_friday.routing.provider_descriptors import SIGNUP_URLS
    from agent_friday.services import one_key
    for url in list(SIGNUP_URLS.values()) + list(one_key.KEY_URLS.values()):
        assert url in VERIFIED_URLS, url


def test_coming_soon_entries_offer_nothing_to_buy():
    """Asked for, not usable by Friday's code: listed, with no key field and no
    sign-up link, so nobody pays for a key that would sit unused."""
    labels = " ".join(s["label"] for s in cg.COMING_SOON).lower()
    for name in ("replicate", "fal", "stability", "runway", "tavily", "exa",
                 "microsoft", "spotify"):
        assert name in labels, name
    for s in cg.COMING_SOON:
        assert set(s) == {"id", "category", "label", "note"}, s
        assert s["id"] not in cg.GUIDE


def test_cost_notes_never_invent_a_price():
    import re
    for iid, g in cg.GUIDE.items():
        assert not re.search(r"[$£€]\s?\d", g.get("cost", "")), iid


def test_recommended_carries_a_reason():
    recs = {k: g["recommended"] for k, g in cg.GUIDE.items() if g.get("recommended")}
    assert set(recs) == {"local", "provider:anthropic"}
    assert all(len(r) > 10 for r in recs.values())


def test_every_key_entry_has_three_steps_and_a_link():
    for iid, g in cg.GUIDE.items():
        if not iid.startswith("provider:"):
            continue
        steps = cg.how_for(iid, iid, "secret")
        assert len(steps) >= 3 and g.get("signup"), iid


def test_every_category_says_what_a_connection_can_reach():
    for cat in ("models", "creative", "search", "mcp"):
        assert cg.access_for("x:" + cat, cat), cat
    assert cg.access_for("local", "models") == cg.ACCESS_LOCAL
    assert any("never-send" in line for line in cg.ACCESS_MODEL)
    assert any("asks you first" in line for line in cg.ACCESS_MCP)


# ── Voice parity: "connect OpenRouter" opens its secure card ─────────────────

@pytest.mark.parametrize("said,item", [
    ("connect OpenRouter", "provider:openrouter"),
    ("set up my Notion account", "connector:notion"),
    ("connect my Brave Search key", "provider:brave"),
    ("hook up Higgsfield", "connector:higgsfield"),
    ("add an MCP server", "mcp:add"),
])
def test_connect_openrouter_by_voice_opens_its_secure_card(said, item):
    from agent_friday.services import laya_router as lr
    from agent_friday.services import desktop_targets as dt
    r = lr._tier0(said)
    assert r is not None and r.tool == "navigate_to", (said, r)
    assert r.args.get("kind") == "settings", r.args
    out = dt.resolve_settings(r.args.get("query", ""))
    assert out["ok"], out
    assert out["target"] == {"workspace": "settings", "tab": "connections", "connect": item}
    assert out["verify"] == {"workspace": "settings", "key": "connect", "value": item}


@pytest.mark.parametrize("said", [
    "set up a meeting with the team", "add milk to the shopping list",
    "connect the dots for me", "search notion for my notes",
])
def test_ordinary_sentences_are_not_connect_requests(said):
    from agent_friday.services import laya_router as lr
    r = lr._tier0(said)
    assert not (r is not None and r.tool == "navigate_to"
                and (r.args or {}).get("kind") == "settings"), (said, r)
    assert cg.spoken_connection(said) == ""


def test_a_spoken_key_is_never_part_of_the_route():
    """The route carries a service name only; the key is typed into the card."""
    from agent_friday.services import laya_router as lr
    r = lr._tier0("connect OpenRouter")
    assert set(r.args) == {"kind", "query"}


# ── The screen (both UI files) ───────────────────────────────────────────────

@pytest.fixture(params=["index.html", "ui_parts/app.html"])
def ui(request):
    return (ROOT / request.param).read_text(encoding="utf-8")


def _between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


def test_the_screen_offers_local_ai_tests_and_access(ui):
    panel = _between(ui, "function SetupConnectionsPanel", "// The secure key form")
    assert "I'll just use local AI" in panel
    assert "/api/setup/connections/local-only" in panel
    item = _between(ui, "function SetupConnectionItem", "function SetupConnectionsPanel")
    for words in ("Test connection", "What it can access", "How to connect",
                  "How to fix: ", "Recommended", "Cost:"):
        assert words in item, words
    assert "Coming soon" in panel


def test_nothing_on_the_screen_is_preselected(ui):
    block = _between(ui, "const SC_STATUS", "// The secure key form")
    assert "defaultChecked" not in block
    assert "checked: true" not in block and "checked={true}" not in block
    # The only ticks are the optional extra permissions, read from state that starts empty.
    assert block.count("type: \"checkbox\"") + block.count('type="checkbox"') == 1


def test_settings_connections_reuses_the_same_screen(ui):
    tail = ui[ui.index("tab === 'connections'"):][:900]
    assert "Connect your services" in tail and "SetupConnectionsPanel" in tail
    assert "highlight: connectFocus" in tail
    assert "connect: connectFocus" in ui and "setConnectFocus(String(t.connect))" in ui


def test_the_setup_card_is_the_same_screen_and_local_ai_answers_it(ui):
    assert "Connect your services" in ui
    assert "action: 'local_only'" in ui


# ── The terminal setup (the installer's flow) ────────────────────────────────

def test_the_terminal_offers_local_ai_first_and_preselects_nothing():
    from agent_friday import setup_wizard as w
    choices = w.connect_choices(True)
    assert choices[0][1].startswith("I'll just use local AI")
    assert [c[0] for c in choices] == ["1", "2", "3"]
    src = (ROOT / "src" / "agent_friday" / "setup_wizard.py").read_text(encoding="utf-8")
    body = _between(src, "def step_brain(", "def _enable_provider")
    assert 'Prompt.ask("  Which?", choices=["1", "2", "3"])' in body   # no default
    assert "CONNECT YOUR SERVICES" in body
    for name in w.CLI_PROVIDERS:
        assert ("provider:" + name) in cg.GUIDE, name
