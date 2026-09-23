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


# A button's OWN transform must stay two-dimensional. Its children may carry
# z — that is where the dock's depth comes from, and each button gives its own
# raised layers their own vanishing point through a perspective PROPERTY.
# What a button must never do is carry perspective() or a z translate in its
# own transform: an element that does paints in one place and hit-tests in
# another. Measured on the dock this replaced, a click on the centre of the
# Studio icon landed on the document body, and wiki, trust and marketplace
# went the same way at the default depth.
BUTTON_3D = re.compile(r"perspective\s*\(|translateZ\s*\(|translate3d\s*\(")

BUTTON_SELECTORS = (".dock-btn", ".dock-btn:hover", ".dock-btn.active")


def test_a_dock_button_keeps_its_own_transform_flat():
    offenders = []
    for sel, body in dock_button_rules():
        if sel.strip() not in BUTTON_SELECTORS:
            continue
        for decl in re.findall(r"transform\s*:\s*([^;]+)", body):
            if BUTTON_3D.search(decl):
                offenders.append((sel.strip(), decl.strip()))
    assert not offenders, (
        "a dock button carries 3D in its own transform again, which makes it "
        "hit-test somewhere other than where it is drawn:\n"
        + "\n".join("  %s -> %s" % o for o in offenders)
    )


def test_the_press_animation_keeps_the_button_flat():
    css = re.sub(r"/\*.*?\*/", "", stylesheet(), flags=re.S)
    m = re.search(r"@keyframes\s+dockPress3d\s*\{(.*?)\n\s*\}\s*\n", css, re.S)
    assert m, "the dock press keyframes are gone or renamed"
    assert not BUTTON_3D.search(m.group(1)), (
        "the press animation puts the button back into 3D, so it cannot be "
        "clicked while the animation runs: %r" % m.group(1)
    )


@pytest.mark.parametrize("var", ["--holo-sheen", "--holo-sheen-a",
                                 "--dock-spec", "--dock-spec-a"])
def test_the_desktop_sheen_variables_have_no_readers(var):
    assert var not in INDEX.read_text(encoding="utf-8"), (
        "%s is back. It only ever fed the sweeping band over the desktop "
        "surface, which the owner asked to have removed." % var
    )


def test_the_mouse_does_not_stand_in_for_the_head():
    """Nothing on the desktop may follow the pointer.

    The head-coupled scene shears and slides to answer where you are sitting.
    While the camera was not tracking a face, the POINTER was fed in as a
    stand-in head, so the whole scene — and the glass in front of it — swam
    around after the mouse. Measured before the removal, moving the pointer
    across the desktop accounted for 3.2 grey levels of change per pixel;
    after, it accounts for none, within noise.

    A window answers to a head. When nothing knows where the head is, it sits
    still.
    """
    src = INDEX.read_text(encoding="utf-8")
    assert "pushMouseFallback" not in src, (
        "the mouse is being pushed in as a head position again, which makes "
        "the whole scene follow the pointer"
    )
    assert "relaxToNeutral" in src, (
        "the no-face path is gone; without it the head freezes at its last "
        "value instead of easing back to centre"
    )


def test_the_dock_has_no_light_that_follows_the_pointer():
    """The dock's floor and lip are lit evenly, not where the pointer is."""
    css = stylesheet()
    for sel, body in rules(css):
        if sel.strip() in (".dock::before", ".dock::after"):
            assert "--dock-spec" not in body, (
                "%s puts a highlight where the pointer is again, which drags "
                "a bright smear along the dock: %r" % (sel.strip(), body.strip())
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
