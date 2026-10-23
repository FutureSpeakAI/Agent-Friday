"""Reduced motion (north star NS-33.1-9): no endless decorative motion.

With the system's "reduce motion" setting on, every looping animation is
either stopped or, for an indicator that says work is still going on, slowed.
Checked rule by rule rather than with one blanket rule: a blanket
`animation-duration: 0.01ms` on everything broke the window open and close
animations that other code waits for.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "head.html"]
SCRIPTS = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]
INLINE_RULE = '[style*="infinite"][style*="pulse" i]'


def _uncomment(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _styles(page: Path) -> str:
    """The page's stylesheets only, comments removed."""
    text = page.read_text(encoding="utf-8")
    found = re.findall(r"<style[^>]*>(.*?)</style>", text, flags=re.S | re.I)
    return _uncomment("\n".join(found) if found else text)


def _blocks(css: str, query: str) -> str:
    out = []
    for m in re.finditer(r"@media\s*\(prefers-reduced-motion:\s*%s\)\s*\{" % query, css):
        i, depth = m.end(), 1
        while depth and i < len(css):
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append(css[m.end():i - 1])
    return "\n".join(out)


def _selectors(block: str) -> set:
    """Every selector named by a rule inside `block`, exactly."""
    out = set()
    for m in re.finditer(r"([^{}]+)\{[^{}]*\}", block):
        out.update(s.strip() for s in m.group(1).split(",") if s.strip())
    return out


def _endless_rules(css: str):
    """(selector, animation name) for each CSS rule with an infinite animation."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        if sel.startswith("@") or re.fullmatch(r"(from|to|[\d.]+%)(\s*,\s*(from|to|[\d.]+%))*", sel):
            continue
        for a in re.finditer(r"animation\s*:\s*([^;]+)", body):
            if "infinite" in a.group(1):
                name = re.match(r"\s*([\w-]+)", a.group(1)).group(1)
                yield sel, name


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_endless_css_animation_stops_or_slows_when_motion_is_reduced(page):
    css = _styles(page)
    covered = _selectors(_blocks(css, "reduce")) | _selectors(_blocks(css, "no-preference"))
    missing = sorted({"%s (%s)" % (one, name)
                      for sel, name in _endless_rules(css)
                      for one in (s.strip() for s in sel.split(","))
                      if one and one not in covered})
    assert not missing, "loops with no reduced-motion rule:\n  " + "\n  ".join(missing)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_inline_pulses_and_the_privacy_lights_stop(page):
    reduce = _blocks(_styles(page), "reduce")
    sels = _selectors(reduce)
    for sel in (INLINE_RULE, ".camera-pip .cam-active .pulse", ".camera-privacy-banner .pulse",
                ".dock", ".dock-btn"):
        assert sel in sels, sel
    # The privacy lights stop moving but are never hidden.
    assert not re.search(r"camera-(pip|privacy)[^{}]*\{[^}]*(display\s*:\s*none|opacity\s*:\s*0)", reduce)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_inline_loop_is_one_the_inline_rule_stops(script):
    """An inline style the app sets from script ('... infinite') is stopped by
    the attribute rule only when it is a pulse; a new kind needs its own rule."""
    text = script.read_text(encoding="utf-8")
    loops = re.findall(r"""['"]([\w-]+)\s+[\d.]+m?s\b[^'"]*\binfinite\b[^'"]*['"]""", text)
    others = sorted({n for n in loops if "pulse" not in n.lower()})
    assert not others, "inline loops the reduced-motion rule does not stop: %s" % others


def test_no_blanket_rule_that_breaks_the_window_animations():
    reduce = _blocks(_styles(ROOT / "index.html"), "reduce")
    assert "*" not in {s.split(":")[0] for s in _selectors(reduce)}, \
        "a blanket animation rule broke fwin open/close (messages_friday_select.spec)"
