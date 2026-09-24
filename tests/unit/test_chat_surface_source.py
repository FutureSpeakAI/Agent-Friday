"""One chat component, one key rule: source guards.

The docked chat and the in-desktop chat windows render the same ChatSurface.
The window used to be a separate, smaller copy that lost voice, sources,
attachments, the model switcher and markdown, and a single-line <input> sent on
Shift+Enter. The behaviour is tested in a browser by tests/chat_surface.spec.ts;
these checks keep the structure from drifting back without a browser.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _function(src, name):
    start = src.index("function " + name + "(")
    nxt = re.compile(r"\n(?:async )?function [A-Za-z_]\w*\(").search(src, start + 10)
    return src[start:nxt.start() if nxt else len(src)]


def test_panel_and_window_render_the_same_chat_surface():
    src = _read("index.html")
    assert src.count("function ChatSurface(") == 1
    app = _function(src, "App")
    assert "React.createElement(ChatSurface, {\n    mode: 'panel'" in app
    win = _function(src, "ConversationWindow")
    assert "React.createElement(ChatSurface, {" in win
    assert "mode: 'window'" in win
    # the window no longer carries its own transcript or composer markup
    assert '"Message this thread' not in win
    assert "/api/chat/send" not in win
    # and it streams through the same turn function as the panel
    assert "fridayChatTurn(" in win


def test_the_surface_has_the_whole_toolbar():
    surface = _function(_read("index.html"), "ChatSurface")
    for needle in ("ConversationBar", "toggleVoice", "data-audio-device-toggle",
                   "Cite Sources", "Show Sources", "chatFileRef", "renderFridayMarkdown",
                   "FridayChatInput", "SendTo", "PauseWarning"):
        assert needle in surface, needle


def test_enter_rule_is_ime_safe():
    src = _read("index.html")
    fn = _function(src, "fridayIsComposing")
    assert "isComposing" in fn and "229" in fn
    send = _function(src, "fridayEnterSends")
    assert "shiftKey" in send and "fridayIsComposing(e)" in send


def test_no_chat_box_sends_on_every_enter():
    """`e.key === 'Enter' && send()` fires on Shift+Enter too."""
    src = _read("index.html")
    for send in ("sendChat()", "sendPrompt()", "sendBriefingChat()"):
        assert f"e.key === 'Enter' && {send}" not in src, send
    app = _read("ui_parts/app.html")
    for send in ("sendChat()", "sendPrompt()", "sendBriefingChat()"):
        assert f"e.key==='Enter'&&{send}" not in app, send


def test_windows_resize_from_every_edge():
    src = _read("index.html")
    assert "const FRIDAY_EDGES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];" in src
    assert "fridayEdgeHandles(startResize)" in _function(src, "FWin")
    assert "fridayEdgeHandles(startResize)" in _function(src, "ConversationWindow")
    assert "friday_chatwin_size" in _function(src, "ConversationWindow")


def test_mirror_has_the_same_pieces():
    app = _read("ui_parts/app.html")
    assert app.count("function ChatSurface(") == 1
    assert '<ChatSurface mode="panel"' in app
    assert "function fridayEnterSends(" in app
    assert "fridayEdgeHandles(startResize)" in app
    css = _read("ui_parts/styles_and_scene.html")
    assert ".win-edge{" in css
