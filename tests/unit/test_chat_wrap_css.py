"""FR-5 — chat bubble text-wrap fix (docs: toolcall-integrity-v5).

Regression guard against a horizontal scrollbar reappearing in the chat
panel: a 300-char unbroken token (long URL, hash, run-on word) must wrap
instead of forcing the panel wider than its container. Verified live in a
browser (360px and 1200px, prose + code block, zero horizontal overflow) —
this test guards the CSS/inline-style source so a future edit can't silently
drop the fix. Checks ui_parts/*.html (source) and index.html (the build_ui.py
output) so drift between the two is caught too.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path):
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["ui_parts/head.html", "index.html"])
class TestFridayDocCssWraps:
    def test_base_class_wraps(self, path):
        css = _read(path)
        block = css.split(".friday-doc {", 1)[1].split("}", 1)[0]
        assert "overflow-wrap: anywhere" in block
        assert "word-break: break-word" in block

    def test_pre_block_wraps_instead_of_relying_only_on_scroll(self, path):
        css = _read(path)
        block = css.split(".friday-doc pre {", 1)[1].split("}", 1)[0]
        assert "white-space: pre-wrap" in block
        assert "overflow-wrap: anywhere" in block

    def test_pre_code_wraps(self, path):
        css = _read(path)
        block = css.split(".friday-doc pre code {", 1)[1].split("}", 1)[0]
        assert "white-space: pre-wrap" in block
        assert "overflow-wrap: anywhere" in block
        # Must not still be the old non-wrapping value.
        assert "white-space: pre;" not in block


class TestChatBubbleInlineStyles:
    def test_source_jsx_has_wrap_and_minwidth(self):
        app = _read("ui_parts/app.html")
        # app.html has TWO chatMsgs.map(...) renderers: a small 280px-wide
        # "Discuss Briefing" sidebar chat, and the main FRIDAY CHAT panel
        # (the "You ·" / "Friday ·" + VOICE-tag window Stephen screenshotted)
        # — rindex targets the latter, which is this fix's actual scope.
        assert "overflowX:'hidden'" in app
        anchor = app.rindex("chatMsgs.map((m,i)=>")
        window = app[anchor:anchor + 1200]
        assert "minWidth:0" in window
        assert "overflowWrap:'anywhere'" in window
        assert "wordBreak:'break-word'" in window

    def test_built_index_html_has_the_compiled_equivalent(self):
        # index.html is build_ui.py's JSX-precompiled output — the same
        # properties should appear as React.createElement style objects.
        built = _read("index.html")
        assert "overflowWrap: 'anywhere'" in built
        assert "wordBreak: 'break-word'" in built
