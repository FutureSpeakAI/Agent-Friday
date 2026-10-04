"""The Chat Hub's Build switch (docs/design/active/chat-hub.md, M3a), pinned in
the files that ship it: the panel script offers "Build" to a chat whose project
has connected codebases, binds the chat to the one chosen, and the "Beside the
chat" offer stays out of a chat tab, where tray placement means nothing. The
sidebar's new-codebase path opens the chat it made, and a project row says
what it holds.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
PANEL_JS = ROOT / "static" / "friday_artifacts.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_the_host_offers_build_for_the_projects_codebases():
    js = _read(PANEL_JS)
    host = js[js.index("function FridayArtifactHost("):]
    assert "data-build-switch" in host, "no Build switch"
    assert "/api/projects/" in host, "the host does not read the chat's project"
    assert re.search(r"/api/conversations/'\s*\+\s*encodeURIComponent\(convId\)\s*\+\s*'/codebase'", host), "Build does not bind through the conversation's codebase route"


def test_beside_the_chat_is_not_offered_inside_a_chat_tab():
    js = _read(PANEL_JS)
    gate = re.search(r"const canWiden = (.+)", js)
    assert gate and "__FRIDAY_CHROME__ !== 'chat'" in gate.group(1)


def test_the_sidebar_opens_the_codebase_chat_it_just_made():
    s = _read(INDEX)
    sb = s[s.index("function ChatSidebar("):]
    fn = sb[sb.index("const newCodebase ="):sb.index("const onAct")]
    assert "api('/api/codebases'" in fn
    assert ".then(r => r.json())" not in fn, "api() already returns JSON; a second .json() throws and the chat never opens"
    assert "onOpenConversation(d.conversation.id)" in fn


def test_a_project_row_says_what_it_holds():
    s = _read(INDEX)
    sb = s[s.index("function ChatSidebar("):]
    assert "data-project-counts" in sb
    row = sb[sb.index("data-project-counts"):][:600]
    assert "p.files" in row and "p.codebases" in row
