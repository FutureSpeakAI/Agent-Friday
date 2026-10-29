"""Under reduced motion the holographic scene stops its ambient spin and drift
(NS-33.2-1, "all ambient, holographic, avatar and graph motion respects
reduced-motion"; BD-21 AX21; WCAG 2.2.2 and 2.3.3).

Before: with the system's reduce-motion setting on, the scene dropped its flash
and halved speech-driven motion, but every structure kept spinning, the cube
and ring clusters kept turning, the particles kept drifting and the stars kept
rotating, for as long as Friday was open. Now each of those ambient moves is
multiplied by `ambient`, which is 0 under reduced motion; glow and opacity keep
using idleFactor, so the scene is stilled, not dimmed.

Checked in the served page (index.html) and its scene mirror
(ui_parts/styles_and_scene.html).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ["index.html", "ui_parts/styles_and_scene.html"]

# The ambient moves of the animate loop (each line a continuous rotation or drift).
AMBIENT_MOVES = [
    r"activeGroup\.rotation\.y \+= moodLerpValues\.rotationSpeed \* (\w+);",
    r"activeGroup\.rotation\.z \+= moodLerpValues\.rotationSpeed \* 0\.5 \* (\w+);",
    r"c\.rotation\.x \+= \(0\.6 \* c\.userData\.speed \* (\w+) \+ a\.twist\) \* delta;",
    r"c\.rotation\.y \+= \(0\.72 \* c\.userData\.speed \* (\w+) - a\.twist \* 0\.6\) \* delta;",
    r"abyssParticles\.rotation\.y -= 0\.005 \* (\w+);",
    r"c\.rotation\.y \+= \(0\.12 \* \(i%2==0\?1:-1\) \* (\w+) \+ a\.twist\) \* delta;",
    r"tesseractLines\.userData\.angleXW \+= \(0\.5 \+ audioData\.low \* 3\.0 \+ a\.lift\) \* delta \* (\w+);",
    r"tesseractLines\.userData\.angleYW \+= \(0\.3 \+ audioData\.low \* 2\.0 \+ a\.twist\) \* delta \* (\w+);",
    r"particleSystem\.rotation\.y \+= \(0\.001 \+ audioData\.mid \* 0\.01\) \* (\w+);",
]


def _page(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", PAGES)
def test_the_ambient_factor_is_zero_under_reduced_motion(rel):
    src = _page(rel)
    m = re.search(r"const ambient = SceneMotion\.reduced\(\) \? 0 : idleFactor;", src)
    assert m, "no motion factor that reduced motion sets to 0"
    # It is set after idleFactor is updated for the frame, so it never lags a frame behind.
    lerp = src.find("idleFactor = THREE.MathUtils.lerp(idleFactor")
    assert 0 <= lerp < m.start()


@pytest.mark.parametrize("rel", PAGES)
def test_every_ambient_spin_and_drift_uses_it(rel):
    src = _page(rel)
    for pat in AMBIENT_MOVES:
        found = re.findall(pat, src)
        assert found, "ambient move not found (renamed?): " + pat
        assert set(found) == {"ambient"}, "still spins under reduced motion: %s uses %s" % (pat, found)
    assert re.search(r"starField\.rotation\.y \+= 0\.0001 \* \(ambient \? 1 : 0\);", src), "the stars still turn"
    rings = re.findall(r"ring\.rotation\.[xy] \+= ring\.userData\.r[xy]Speed[^;]*;", src)
    assert len(rings) == 2 and all(r.endswith("* (ambient ? 1 : 0);") for r in rings), rings


def test_the_camera_holds_under_reduced_motion():
    # The cinematic drift is a function of `ct`; reduced motion pins it at 0, as head tracking does.
    # (Served page only: the scene mirror has no camera-drift block.)
    assert "const ct = (windowMode || SceneMotion.reduced()) ? 0 : elapsed;" in _page("index.html")


@pytest.mark.parametrize("rel", PAGES)
def test_glow_and_opacity_still_follow_idle_so_the_scene_is_not_dimmed(rel):
    src = _page(rel)
    assert "const bloomIdle = 0.75 + idleFactor * 0.25;" in src
    assert re.search(r"line\.material\.opacity = \([^;]*\) \* idleFactor;", src)


def test_no_rotation_scaled_by_idle_factor_is_left():
    # Narrow guard: a continuous rotation scaled by idleFactor must use `ambient` instead.
    # (Motion driven straight by `elapsed` is not caught here; see the HANDOFF's AX21 gaps.)
    src = _page("index.html")
    offenders = re.findall(r"rotation\.[xyz]\s*[+-]=[^;\n]*\bidleFactor\b", src)
    assert offenders == [], offenders
