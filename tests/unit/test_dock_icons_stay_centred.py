"""Two things the owner asked for by name, pinned so they cannot come back.

1. A dock icon must sit dead centre in its tile.

   Under a shared CSS perspective, a layer with its own Z is displaced from its
   parent by (z / perspective) x (distance from the perspective origin). The
   icon carried a translateZ and its tile did not, so the two came apart — and
   because the displacement grows with distance from the dock's centre, the
   outermost icons left their tiles entirely. Measured before the fix: 14.6px
   at the shipped depth with the head centred, 19.4px with the head turned,
   53.7px at maximum depth, on tiles 56px wide.

   The invariant that makes it impossible is that NOTHING in the dock button
   subtree carries Z. Then no perspective, origin, depth setting, hover state
   or head angle can separate an icon from its tile, because there is no
   transform that could. That is what these tests check, rather than checking
   a rendered position that only holds for the values in the file today.

2. The desktop must not shimmer.

   A screen-blended band swept across the whole desktop, driven off head
   angle. It is gone, and so are the variables that fed it. The rim light
   stays: it is a vignette, not a band.
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"


def stylesheet() -> str:
    m = re.search(r"<style>(.*?)</style>", INDEX.read_text(encoding="utf-8"), re.S)
    assert m, "index.html has no <style> block"
    return m.group(1)


def rules(css: str):
    """(selector, body) for every rule, comments stripped."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def dock_button_rules():
    """Rules that style a dock button or anything inside one."""
    out = []
    for sel, body in rules(stylesheet()):
        sel = sel.strip()
        if ".dock-btn" in sel and not sel.startswith("@"):
            out.append((sel, body))
    assert out, "no .dock-btn rules found at all — the selector or file moved"
    return out


# Deliberately blunt. An earlier version tried to allow translate3d with a
# zero Z and could not cross the nested parens of a var() fallback, so it
# passed against the very code it was written to catch. The dock has no use
# for a 3D translate at all now, so anything that could carry Z is a failure.
Z = re.compile(r"translateZ\s*\(|translate3d\s*\(|perspective\s*\(|--mag-z")


def test_nothing_in_a_dock_button_carries_z():
    offenders = []
    for sel, body in dock_button_rules():
        for decl in re.findall(r"transform\s*:\s*([^;]+)", body):
            if Z.search(decl):
                offenders.append((sel, decl.strip()))
    assert not offenders, (
        "a dock button or one of its layers carries Z again, which is what "
        "pulled icons out of their tiles:\n"
        + "\n".join("  %s -> %s" % o for o in offenders)
    )


def test_the_icon_itself_has_no_transform():
    """The exact rule that broke it. A transform here separates icon from tile."""
    for sel, body in dock_button_rules():
        if re.fullmatch(r"\.dock-btn\s+\.ico", sel.strip()):
            assert "transform" not in body, (
                ".dock-btn .ico declares a transform (%r). The icon moves with "
                "its tile only while it has none of its own." % body.strip()
            )


def test_dock_keyframes_do_not_reintroduce_z():
    css = re.sub(r"/\*.*?\*/", "", stylesheet(), flags=re.S)
    m = re.search(r"@keyframes\s+dockPress3d\s*\{(.*?)\n\s*\}\s*\n", css, re.S)
    assert m, "the dock press keyframes are gone or renamed"
    assert not Z.search(m.group(1)), (
        "the press animation moves the button in Z again: %r" % m.group(1)
    )


@pytest.mark.parametrize("var", ["--holo-sheen", "--holo-sheen-a"])
def test_the_desktop_sheen_variables_have_no_readers(var):
    assert var not in INDEX.read_text(encoding="utf-8"), (
        "%s is back. It only ever fed the sweeping band over the desktop "
        "surface, which the owner asked to have removed." % var
    )


def test_the_hud_overlay_draws_no_sweeping_band():
    for sel, body in rules(stylesheet()):
        if sel.strip() == "#hud-overlay::after":
            pytest.fail(
                "#hud-overlay::after is back (%r). That pseudo-element was the "
                "full-screen sheen; a rim light belongs on ::before, which is a "
                "vignette rather than a band." % body.strip()
            )


def test_the_dock_surface_does_not_animate_on_a_clock():
    for sel, body in rules(stylesheet()):
        if sel.strip() == ".dock" and "animation" in body:
            pytest.fail(
                ".dock animates its own background again (%r). A cue on a clock "
                "is decoration; the dock's depth answers the head and the "
                "pointer instead." % body.strip()
            )
