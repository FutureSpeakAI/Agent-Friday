"""The chat tab wears the one top bar (docs/design/active/unified-shell.md §4).

A chat in its own tab (`?chrome=chat`) hides the React layer and shows the
chat again through an allow-list. The top bar is part of that allow-list: the
wordmark (the product, written with its mark), the model selector and the
clock are the same bar the desktop and every workspace tab wear, and the chat
sits under it. These tests read both UI files, so half an edit fails here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
MIRROR = ROOT / "ui_parts" / "app.html"
HEAD = ROOT / "ui_parts" / "head.html"          # the mirror's stylesheet
UI = pytest.mark.parametrize("path", [INDEX, MIRROR], ids=["index.html", "app.html"])
SHEETS = pytest.mark.parametrize("path", [INDEX, HEAD], ids=["index.html", "head.html"])


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector + " has no rule"
    return m.group(1)


@SHEETS
def test_the_chat_tab_shows_the_top_bar_and_sits_under_it(path):
    css = _read(path)
    # the bar is the one element of the hidden layer shown again
    assert "visibility: visible" in _rule(css, "body.chrome-chat .top-bar")
    # the sidebar and the panel start below the bar, by its own height
    chat_rules = re.findall(r"body\.chrome-chat \.chat-panel(?:,\s*body\.chrome-chat \.chat-panel\.open)?\s*\{([^}]*)\}", css)
    assert any("var(--fr-topbar-h" in r and "top:" in r for r in chat_rules), "the panel does not drop under the bar"
    assert "var(--fr-topbar-h" in _rule(css, "body.chrome-chat .chat-sidebar")


@UI
def test_the_bar_knows_it_is_in_a_chat_tab(path):
    s = _read(path)
    bar = s[s.index("const shellTopBar"):]
    bar = bar[:bar.index("const shellKillSwitch")]
    assert "shellChat" in bar, "the bar has no chat kind"
    assert "data-shell-kind" in bar
    # the lockup leads back to the desktop with THIS chat open
    assert "/?conversation=" in bar
    # the context slot names the chat, not a workspace or the scene
    assert "ShellChatName" in bar
    assert "function ShellChatName" in s


@UI
def test_a_chat_tab_hides_the_controls_whose_panels_it_cannot_show(path):
    s = _read(path)
    bar = s[s.index("const shellTopBar"):]
    bar = bar[:bar.index("const shellKillSwitch")]
    for label in ("Open settings", "Open Quick Draft", "Open chat with", "Open notifications", "Fullscreen with chat"):
        i = bar.index(label)
        assert "!shellChat" in bar[max(0, i - 900):i], label + " is offered in a chat tab, where its panel is hidden"
