"""Friday's scrollbars: one rule set, sized by one token, on every surface.

The owner asked for scrollbars twice as thick, because 4px ones were hard to
click and drag. What keeps that true is structure rather than a number:
- one <style id="friday-scrollbars"> element holds every scrollbar rule, with
  its size in one brand token, --scrollbar-size; the page and its mirror carry
  the same element;
- nothing else sizes or colours a scrollbar, so no surface drifts back to a
  sliver;
- no rule anywhere Chromium reads sets scrollbar-width or scrollbar-color: from
  Chrome 121 either one switches that element to the standard properties and
  silently ignores every ::-webkit-scrollbar size, so a change like this one
  would look done and not be. Firefox gets them in a block Chromium skips;
- frames Friday builds copy the rule set in, since a frame inherits nothing.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
HEAD = REPO / "ui_parts" / "head.html"
APP = REPO / "ui_parts" / "app.html"
MAIL = REPO / "static" / "friday_mail.js"

BLOCK = re.compile(r'^[ \t]*<style id="friday-scrollbars">(.*?)^[ \t]*</style>', re.S | re.M)
FIREFOX = re.compile(r"@supports \(-moz-appearance: none\) \{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.S)


def _block(path):
    found = BLOCK.findall(path.read_text(encoding="utf-8"))
    assert len(found) == 1, "%s must hold exactly one friday-scrollbars element" % path.name
    return found[0]


def _served_sources():
    """Everything a Friday page loads that could style a scrollbar."""
    out = [INDEX, APP, HEAD] + sorted((REPO / "ui_parts").glob("*.html"))
    out += [p for p in sorted((REPO / "static").glob("*.js"))]
    out += sorted((REPO / "static").glob("**/*.css")) + sorted((REPO / "assets").glob("**/*.css"))
    return [p for p in dict.fromkeys(out) if "vendor" not in p.parts]


def test_one_element_one_token_in_the_page_and_its_mirror():
    page, mirror = _block(INDEX), _block(HEAD)
    assert page == mirror, "the mirror's scrollbar rules drifted from the page's"
    assert len(re.findall(r"--scrollbar-size\s*:\s*\d", page)) == 1, "one declaration of the token"
    assert re.search(r"::-webkit-scrollbar\s*\{\s*width:\s*var\(--scrollbar-size\);\s*"
                     r"height:\s*var\(--scrollbar-size\);\s*\}", page)
    assert re.search(r"::-webkit-scrollbar-thumb:vertical\s*\{\s*min-height:\s*var\(--scrollbar-thumb-min\)", page)
    assert re.search(r"::-webkit-scrollbar-thumb:horizontal\s*\{\s*min-width:\s*var\(--scrollbar-thumb-min\)", page)
    assert re.search(r"::-webkit-scrollbar-thumb:hover\s*\{[^}]*--scrollbar-thumb-hover", page)
    assert re.search(r"::-webkit-scrollbar-thumb:active\s*\{[^}]*--scrollbar-thumb-active", page)


def test_the_size_is_double_the_old_four_pixels_or_more():
    size = re.search(r"--scrollbar-size\s*:\s*(\d+)px", _block(INDEX))
    assert size and int(size.group(1)) >= 8


@pytest.mark.parametrize("path", _served_sources(), ids=lambda p: str(p.relative_to(REPO)))
def test_nothing_else_sizes_a_scrollbar_or_sets_a_standard_property(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    text = BLOCK.sub("", text)                              # the one element itself
    for m in re.finditer(r"::-webkit-scrollbar[\w:-]*\s*\{([^}]*)\}", text):
        body = m.group(1)
        assert re.fullmatch(r"\s*display:\s*none;?\s*", body), (
            "%s sizes or colours a scrollbar outside the friday-scrollbars element: %s"
            % (path.name, m.group(0)[:120]))
    for prop in ("scrollbar-width", "scrollbar-color", "scrollbarWidth", "scrollbarColor"):
        for m in re.finditer(prop + r"\s*:", text):
            line = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.start())]
            if line.strip().startswith(("/*", "*", "//")):
                continue
            pytest.fail("%s sets %s, which makes Chrome 121+ ignore every "
                        "::-webkit-scrollbar size: %s" % (path.name, prop, line.strip()[:120]))


def test_the_standard_properties_live_only_in_the_firefox_block():
    block = _block(INDEX)
    outside = FIREFOX.sub("", block)
    assert not re.search(r"scrollbar-(width|color)\s*:", outside)
    inside = "".join(FIREFOX.findall(block))
    assert "scrollbar-width: auto" in inside, "Firefox's widest bar is 'auto'"


def test_frames_friday_builds_carry_the_rule_set():
    mail = MAIL.read_text(encoding="utf-8")
    assert "const doc = (window.fridayFrameScrollbars || (s => s))('<!doctype html>" in mail
    for path in (INDEX, APP):
        src = path.read_text(encoding="utf-8")
        assert "function fridayFrameScrollbars(html)" in src, path.name
        assert "fridayFrameScrollbars(htmlContent)" in src, path.name + ": the briefing reader"
        assert "return fridayFrameScrollbars([" in src, path.name + ": the draft export"
