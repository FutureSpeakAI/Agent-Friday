"""In-app text says what the code does (docs audit 2026-10-17, in-app findings 1-31).

Every sentence below was on screen, spoken, or read by the model, and the code did something else:
"your private information never leaves your device" over a cloud default, a "Smart" mode that was
removed, "I ask before I send, spend or delete anything" beside pre-approved sends, and
self-description files that listed workspaces that do not exist and the Marketplace as live.
Each test names the sentence that came back if it fails.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "agent_friday"


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


UI = ("index.html", "ui_parts/app.html")

# (file, sentence that must not come back, why)
GONE = [
    ("src/agent_friday/setup_wizard.py", "private information never", "the welcome screen promised it over a cloud default"),
    ("src/agent_friday/setup_wizard.py", "nothing leaves this computer", "the local-AI choice changes no routing"),
    ("src/agent_friday/setup_wizard.py", "coming in v5", "OpenAI-compatible providers and Ollama ship"),
    ("src/agent_friday/setup_wizard.py", "(v5)", "Slack and Notion are cards on Connect your services"),
    ("src/agent_friday/setup_wizard.py", "brew install", "Windows only"),
    ("src/agent_friday/setup_wizard.py", "console.anthropic.com", "the verified key page is platform.claude.com"),
    ("src/agent_friday/setup_wizard.py", "works offline; faster-whisper", "deeper voice requests use the chat model"),
    ("src/agent_friday/setup_brain.py", "console.anthropic.com", "the verified key page is platform.claude.com"),
    ("src/agent_friday/routes/chat.py", "**Smart**", "there is no Smart mode"),
    ("src/agent_friday/routes/chat.py", "console.anthropic.com", "the verified key page is platform.claude.com"),
    ("src/agent_friday/routes/chat.py", "https://openrouter.ai/keys)", "the verified key page is openrouter.ai/settings/keys"),
    ("src/agent_friday/services/setup_chat_copy.py", "console.anthropic.com", "the verified key page"),
    ("src/agent_friday/services/setup_chat_copy.py", "https://openrouter.ai/keys\\n", "the verified key page"),
    ("src/agent_friday/services/setup_chat_copy.py", "I ask before I send, spend or delete anything",
     "pre-approved sends go without a card and cloud models spend within the ceiling"),
    ("src/agent_friday/services/setup_chat_copy.py", "1. I run on this PC.", "heard as 'I think on this PC' before the posture question"),
    ("src/agent_friday/services/setup_chat_copy.py", "Nothing about you leaves this PC", "connected services talk to their vendors"),
    ("src/agent_friday/services/setup_chat_copy.py", "identity details \"\n    \"and your never-send list are held back",
     "the cautious setting keeps such details here and tells you; it is not a topic block"),
    ("src/agent_friday/services/setup_chat_copy.py", "Already covered, always: passwords and keys, ID and account numbers",
     "your own name and ID numbers are not refused under Cloud for everything; card and account numbers are list entries"),
    ("src/agent_friday/services/onboarding_copy.py", "Ask her about a bank balance and she will decline",
     "no blanket topic refusals: the never-send list is the floor"),
    ("src/agent_friday/services/onboarding_copy.py", "the cloud when it would clearly help",
     "under Local preferred the cloud is used only when you ask"),
    ("src/agent_friday/services/agent.py", "Brave (BRAVE_API_KEY)", "no reader for BRAVE_API_KEY exists"),
    ("packaging/windows/install.ps1", "private notes stay on this laptop and are never sent",
     "with no local model a vault question goes to the cloud redacted"),
    ("packaging/windows/install.ps1", "Lets her work with no internet,", "downloading a model does not change routing"),
    ("src/agent_friday/VOICE_DEMO.md", "the cloud never sees it, period", "Local-only records can be turned off"),
    ("src/agent_friday/VOICE_DEMO.md", "you leaves your machine unless you connect a service", "the default routing is the cloud"),
    ("src/agent_friday/VOICE_DEMO.md", "I'm offline-first", "under cloud only a local model never answers in the cloud's place"),
    ("src/agent_friday/VOICE_DEMO.md", "the federation marketplace", "the Marketplace is held"),
    ("src/agent_friday/VOICE_DEMO.md", "Positrons", "the economy is held with the Federation"),
    ("src/agent_friday/SELF.md", "vault data never reaches the cloud even in this mode", "true only while Local-only records is on"),
    ("src/agent_friday/SELF.md", "**smart**: Task-type-aware routing", "smart was removed"),
    ("src/agent_friday/SELF.md", "federation listings sell for real ψ", "the Marketplace is held"),
]
for _f in UI:
    GONE += [
        (_f, "nothing leaves this machine", "the voice button: deeper requests and cloud voices leave"),
        (_f, "Ears, reasoning and voice on this machine. Nothing leaves it.", "reasoning follows the chat model"),
        (_f, "hears and thinks here", "thinking follows the chat model"),
        (_f, "fully offline with no cloud key", "installing Ollama does not change routing"),
        (_f, "chat works offline, zero keys", "only when the routing mode lets the local model answer"),
        (_f, "chats fully on-device", "only when the routing mode lets the local model answer"),
        (_f, "data stays on this machine.'", "a LAN address is another machine"),
        (_f, "indexed on this PC, and nothing is sent anywhere.", "Library cloud answers can send text"),
        (_f, "Sending email asks you first, every time.", "pre-approved sends"),
    ]


@pytest.mark.parametrize("rel, sentence, why", GONE, ids=["%s:%d" % (g[0].rsplit("/", 1)[-1], i) for i, g in enumerate(GONE)])
def test_the_wrong_sentence_is_gone(rel, sentence, why):
    assert sentence not in _read(rel), "%r is back in %s: %s" % (sentence, rel, why)


# ── what the text says now, against the code that keeps it ──────────────────────

def test_key_links_are_the_verified_ones_everywhere():
    from agent_friday.services import one_key, setup_chat_copy as copy
    assert one_key.KEY_URLS["anthropic"] in copy.CONNECT_ONE_KEY
    assert one_key.KEY_URLS["openrouter"] in copy.CONNECT_ONE_KEY
    chat = _read("src/agent_friday/routes/chat.py")
    assert one_key.KEY_URLS["anthropic"] in chat and one_key.KEY_URLS["openrouter"] in chat
    assert "**Local preferred** or **Local only**" in chat


def test_the_brave_key_named_to_the_model_is_one_the_search_reads():
    from agent_friday.services import agent
    desc = next(t["description"] for t in agent.CLAUDE_TOOLS if t["name"] == "search_web")
    assert "BRAVE_SEARCH_API_KEY" in desc
    assert "BRAVE_SEARCH_API_KEY" in _read("src/agent_friday/services/web_search.py")


def test_the_promise_about_asking_carves_out_pre_approvals_and_spend():
    from agent_friday.services import setup_chat_copy as copy
    assert "I ask before I send or delete anything, unless you've pre-approved it." in copy.CONTRACT
    assert "a daily spending cap is on from the start" in copy.CONTRACT   # north-star A17
    assert any("pre-approve" in line for line in copy.MEMORY_ALWAYS_ASK)


def test_the_two_setup_screens_say_what_the_cautious_and_the_open_settings_do():
    from agent_friday.services import onboarding_copy as oc, setup_chat_copy as copy
    cautious = copy.POSTURE_MEANS_PUBLIC_NO_LOCAL
    assert "not sent quietly" in cautious and "card" in cautious and "never-send list" in cautious
    assert "held back" not in cautious
    refuse = re.sub(r"\s+", " ", oc.CLOUD_ACK_REFUSE)
    assert "consent for everything except your never-send list" in refuse
    assert "decline" not in refuse
    assert "Only your never-send list is refused" in copy.POSTURE_SET["everything"]


def test_local_preferred_text_matches_the_choke_point():
    from agent_friday.services import onboarding_copy as oc
    both = dict((c[0], c[2]) for c in oc.ROUTING_CHOICES)["local_preferred"]
    assert "only when you ask" in both
    assert "called only with a recorded INTENT" in _read("src/agent_friday/services/cloud_intent.py")


def test_one_key_line_does_not_say_nothing_works_when_chat_runs_locally(monkeypatch):
    from agent_friday.services import one_key, setup_chat_copy as copy
    from agent_friday.services import cloud_intent, local_seats
    monkeypatch.setattr(one_key, "key_in_use", lambda: None)
    monkeypatch.setattr(cloud_intent, "routing_mode", lambda: "local_only")
    monkeypatch.setattr(local_seats, "installed", lambda *a, **k: [("gemma4:e2b", 1.0)])
    assert one_key.status()["line"] == copy.ONE_KEY_NONE_LOCAL
    monkeypatch.setattr(cloud_intent, "routing_mode", lambda: "cloud_only")
    assert one_key.status()["line"] == copy.ONE_KEY_NONE
    monkeypatch.setattr(cloud_intent, "routing_mode", lambda: "local_only")
    monkeypatch.setattr(local_seats, "installed", lambda *a, **k: [])
    assert one_key.status()["line"] == copy.ONE_KEY_NONE


# ── Settings > Voice has the room-approval control the setup chat points to ──

def test_the_room_approval_choice_lives_where_setup_says():
    from agent_friday.services import setup_chat_copy as copy
    assert "Settings > Voice" in copy.ROOM_VOICE_ASK
    for f in UI:
        src = _read(f)
        assert "Spoken approvals in a room" in src, f
        assert "voice_room_approvals_require_name" in src, f
    rows = json.loads(_read("tests/unit/settings_controls.json"))
    assert any(r["keys"] == ["voice_room_approvals_require_name"] and r["section"] == "Voice" for r in rows)


# ── the basics card picks nothing for the person ─────────────────────────────

def test_the_basics_card_waits_for_an_answer_in_both_ui_sources():
    assert "disabled: busy || minor === null" in _read("index.html")
    assert "disabled={busy || minor === null}" in _read("ui_parts/app.html")
    for f in UI:
        src = _read(f)
        start = src.index("function SetupBasicsCard(")
        body = src[start:src.index("\n}\n", start)]
        assert "radio(!minor," not in body, "'For me' is drawn as chosen before anyone chose it (%s)" % f
        assert "profile_picked" in body, f


def test_the_receipt_says_the_profile_was_the_default_when_nobody_picked_it(monkeypatch):
    from agent_friday.services import setup_chat as sc
    rows = []
    monkeypatch.setattr(sc.record, "append", lambda *a, **k: rows.append((a, k)))
    monkeypatch.setattr(sc, "_enter", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_heard", lambda *a, **k: None)
    st = {}
    sc._on_basics(st, [], {"minor_mode": False, "distribution": "default"}, "")
    assert rows[-1][1]["profile_by"] == "default"
    sc._on_basics(st, [], {"minor_mode": False, "distribution": "default", "profile_picked": True}, "")
    assert rows[-1][1]["profile_by"] == "you"


# ── the self-description files list the dock that exists ─────────────────────

def _registry():
    text = _read("static/workspace_registry.js")
    rows = re.findall(r'\{"id": "([a-z_]+)"[^\n]*?"label": "([^"]+)"([^\n]*)', text)
    live = {label for _id, label, rest in rows if '"held"' not in rest}
    held = {label for _id, label, rest in rows if '"held"' in rest}
    return live, held


def _bold_workspaces(md: str, start: str, end: str) -> set:
    block = md[md.index(start):md.index(end, md.index(start))]
    names = set()
    for b in re.findall(r"\*\*([^*]+)\*\*", block):
        names.update(n.strip() for n in b.split("/"))
    return names


def test_voice_demo_lists_only_workspaces_that_exist():
    live, held = _registry()
    md = _read("src/agent_friday/VOICE_DEMO.md")
    names = _bold_workspaces(md, "## What I can do — the workspaces", "Workspaces are configurable")
    assert names, "the workspace list moved"
    assert names <= live, "not in the dock: %s" % sorted(names - live)
    assert not (names & held)


def test_self_md_dock_names_only_workspaces_that_exist():
    live, held = _registry()
    md = _read("src/agent_friday/SELF.md")
    block = md[md.index("## 9. Workspaces: The Dock"):md.index("## 10.")]
    listed = set()
    for line in re.findall(r"^- \*\*(?:Life|Work|System)\*\* — (.+?)(?=^- |\n\n)", block, re.S | re.M):
        listed.update(re.findall(r"(?:^|, |and )([A-Z][A-Za-z]+)(?: / [A-Z][A-Za-z]+)* \(", line))
    assert {"Media", "Library", "Meetings", "Crew"} <= listed, listed
    assert not ({"Draft", "Content", "Studio", "Marketplace"} & listed), listed
    assert listed <= live | {"Finance", "Health", "Family"}, sorted(listed - live)
    assert "The Marketplace is held" in block
