"""The avatar reacts to speech the moment it changes, and only to a real one
(avatar-visual-genome.md §13.1: no motion without a real event).

- When Friday is interrupted (the user talks over her, or presses Escape),
  the scene stops showing her speaking at once: the speaking signals clear,
  the analyser's fading tail is ignored for a quarter of a second, and the
  scene shows it is listening. Before, it kept "speaking" for up to about a
  second.
- Stopping a read-aloud clears the speaking signal: before, the scene stayed
  in the speaking mood, with its faster motion, after the audio stopped.
- When voice stops, the user's mic level the scene reads goes to zero:
  before, the last level stayed, so the listening ripple and "Listening"
  went on with nobody talking.
- With a voice session's mic open the scene can take the LISTENING mood: it
  read only the scene's own microphone, which is never started.
- Friday's own voice, heard back through the mic, is not the user talking:
  the listening ripple follows the mic only while Friday is not speaking.

Both copies of the UI are checked: index.html (served) and its mirrors.
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
APP = ROOT / "ui_parts" / "app.html"
SCENE = ROOT / "ui_parts" / "styles_and_scene.html"


def _squash(text):
    return "".join(text.split())


@pytest.mark.parametrize("path", [INDEX, APP], ids=lambda p: p.name)
def test_an_interruption_stops_the_avatar_speaking_at_once(path):
    t = _squash(path.read_text(encoding="utf-8"))
    flush = t.split("flushPlaybackRef.current=", 1)[1][:2600]
    assert "window._fridayHoloAmplitude=0;" in flush
    assert "window._fridayMoodSignals.ttsActive=false;" in flush
    assert "v._ampQuietUntil=performance.now()+250;" in flush
    assert "setSystemMood('LISTENING')" in flush
    loop = t.split("const_voiceAmpLoop=()=>{", 1)[1][:900]
    assert "performance.now()<(v._ampQuietUntil||0)?0:" in loop


@pytest.mark.parametrize("path", [INDEX, APP], ids=lambda p: p.name)
def test_stopping_a_read_aloud_stops_the_speaking_mood(path):
    t = _squash(path.read_text(encoding="utf-8"))
    stop = t.split("conststopReading=()=>{", 1)[1].split("};", 1)[0]     # its own body only
    assert "window._fridayMoodSignals.ttsActive=false" in stop


@pytest.mark.parametrize("path", [INDEX, APP], ids=lambda p: p.name)
def test_voice_stopping_clears_the_mic_level_the_scene_reads(path):
    t = _squash(path.read_text(encoding="utf-8"))
    # the voice-stop teardown and the socket-close teardown
    assert t.count("window._fridayMicLevel=0;window.__fridayVoiceMicOpen=false;") == 2
    assert t.count("window.__fridayVoiceMicOpen=true;") == 1


@pytest.mark.parametrize("path", [INDEX, SCENE], ids=lambda p: p.name)
def test_an_open_voice_mic_lets_the_scene_listen_and_its_own_echo_does_not(path):
    t = _squash(path.read_text(encoding="utf-8"))
    mic = t.split("isMicActive:function(){", 1)[1][:200]
    assert "window.__fridayVoiceMicOpen" in mic
    assert ("FridayGestures.setMic(window._fridayMoodSignals&&window._fridayMoodSignals.ttsActive?0"
            ":(window._fridayMicLevel||0));") in t
