"""Under reduced motion the scene's time-driven decoration holds as well
(NS-33.2-1; BD-21 AX22, on AX21).

AX21 stopped every spin and drift scaled by idleFactor. What was left moved
straight with the clock (`elapsed`): the grid ocean's waves, the Mandelbrot's
turn and ripple, the Mobius flow, the Eden player's weave, the cubes' breathing,
the line pulses and the post-pass wobble. They now read `ambientT`, which is
`elapsed` exactly while motion is allowed (so nothing changes for anyone else)
and holds where it was the moment reduced motion comes on. Sound timing
(lastSoundTime, isQuiet) and the task orbs (still-working indicators, which AX2
keeps moving slowly) keep the real clock.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ["index.html", "ui_parts/styles_and_scene.html"]

DECORATIVE = [
    "const idlePulse = Math.sin(",
    "const organicBreathe = Math.sin(",
    "positions[i*3+1] = baseYs[i] + Math.sin(positions[i*3] * 0.1 + ",
    "mandelbrotSystem.rotation.y = ",
    "const wave = Math.sin(mData[i].baseX*0.5 + ",
    "const flowOffset = ",
    "edenPlayer.position.x = ",
    "edenPlayer.position.z = ",
    "edenPlayer.position.y = ",
    "edenPlayer.rotation.y = ",
    "edenPlayer.rotation.z = ",
    "const pulse = (Math.sin(",
    "holoPass.uniforms.amount.value = ",
    "holoPass.uniforms.angle.value = ",
]


def _page(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _line(src, start):
    found = [ln.strip() for ln in src.splitlines() if start in ln]
    assert len(found) == 1, (start, len(found))
    return found[0]


@pytest.mark.parametrize("rel", PAGES)
def test_decorative_time_holds_under_reduced_motion_and_is_elapsed_otherwise(rel):
    src = _page(rel)
    assert re.search(r"let ambientHoldT = null;", src)
    hold = re.search(r"if \(SceneMotion\.reduced\(\)\) \{ if \(ambientHoldT === null\) ambientHoldT = elapsed; \} "
                     r"else ambientHoldT = null;\s*const ambientT = ambientHoldT === null \? elapsed : ambientHoldT;", src)
    assert hold, "no held clock for decorative motion"
    at = src.find("const elapsed = clock.getElapsedTime()")
    assert 0 <= at < hold.start()


@pytest.mark.parametrize("rel", PAGES)
def test_every_time_driven_decoration_reads_the_held_clock(rel):
    src = _page(rel)
    for start in DECORATIVE:
        ln = _line(src, start)
        assert "ambientT" in ln and not re.search(r"\belapsed\b", ln), "still moves under reduced motion: " + ln[:120]
    # Held, the Mobius flow must not be multiplied by the sound level (it would jump with every sound).
    assert "ambientT * (2 + (ambientHoldT === null ? audioData.mid * 4 : 0))" in src


@pytest.mark.parametrize("rel", PAGES)
def test_sound_timing_and_task_orbs_keep_the_real_clock(rel):
    src = _page(rel)
    assert "if (audioData.total > 0.02) lastSoundTime = elapsed;" in src
    assert "const isQuiet = (elapsed - lastSoundTime > 6.0)" in src
    assert "processOrbManager.update(delta, elapsed)" in src


@pytest.mark.parametrize("rel", PAGES)
def test_the_last_ambient_motion_holds_too(rel):
    """AX23: what AX21/AX22 left moving under reduced motion, now held as well."""
    src = _page(rel)
    for line in ("cloud.rotation.z += cloud.userData.speed * (ambient ? 1 : 0);",
                 "cloud.position.z += delta * 0.5 * (ambient ? 1 : 0);",
                 "p.addScaledVector(v, delta * 60 * (ambient ? 1 : 0));",
                 "d.position.z += ((d.userData.speed * moodLerpValues.particleSpeedScale) + (audioData.mid * 5)) * (ambient ? 1 : 0);",
                 "holoPass.uniforms.time.value = ambientT;",
                 "const heartbeat = SceneMotion.reduced() ? 0.5 :",
                 "(SceneMotion.reduced() ? 0 : (Math.random()-0.5)*0.1)"):
        assert line in src, "still moves under reduced motion: " + line
    # Before: the same lines without the factor.
    assert "cloud.rotation.z += cloud.userData.speed;" not in src
    assert "holoPass.uniforms.time.value = elapsed;" not in src
    assert "p.addScaledVector(v, delta * 60);" not in src
    assert "cloud.position.z += delta * 0.5;" not in src
