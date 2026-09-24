"""Push-to-transcribe inside the Friday tab.

The tray's system-wide hook SUPPRESSES the hotkey before any window sees it,
so this handler runs only when the system-wide one is off or absent. That is
the entire coordination between them, which makes the in-page chord matching
the thing worth pinning: a binding that fires on the wrong chord would steal
a key from the app it is embedded in, and one that never fires leaves someone
with no dictation at all when the tray is not running.

Driven through node against the real file, because a rule about keyboard
events is only true in a JavaScript engine.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "static" / "js" / "friday_push_to_transcribe.js"

node = shutil.which("node")
pytestmark = pytest.mark.skipif(not node, reason="node is not on PATH")

HARNESS = r"""
const fs = require('fs');
// Enough of a browser for the module to install itself.
const listeners = {};
global.window = {
  addEventListener: (k, f) => { (listeners[k] = listeners[k] || []).push(f); },
};
global.document = {
  activeElement: null,
  querySelector: () => null,
  createElement: () => ({ style: {}, appendChild() {}, setAttribute() {} }),
  body: { appendChild() {} },
};
global.navigator = { mediaDevices: { getUserMedia: () => Promise.reject(new Error('no mic')) } };
global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ settings: {} }) });
global.btoa = s => Buffer.from(s, 'binary').toString('base64');
global.Event = class { constructor(t) { this.type = t; } };
global.AudioContext = class {};

eval(fs.readFileSync(process.argv[2], 'utf8'));

const api = global.window.fridayPushToTranscribe;
const ev = (key, mods) => Object.assign(
  { key, code: 'Key' + key.toUpperCase(),
    altKey: false, ctrlKey: false, shiftKey: false, metaKey: false }, mods || {});

const out = {
  installed: !!api,
  altT_matches_altT: api.matches(ev('t', { altKey: true }), api.parseHotkey('alt+t')),
  bare_t_does_not: api.matches(ev('t'), api.parseHotkey('alt+t')),
  ctrl_alt_t_does_not: api.matches(
      ev('t', { altKey: true, ctrlKey: true }), api.parseHotkey('alt+t')),
  shift_alt_t_does_not: api.matches(
      ev('t', { altKey: true, shiftKey: true }), api.parseHotkey('alt+t')),
  other_letter_does_not: api.matches(ev('y', { altKey: true }), api.parseHotkey('alt+t')),
  rebind_ctrl_shift_space: api.matches(
      Object.assign(ev(' '), { ctrlKey: true, shiftKey: true, code: 'Space' }),
      api.parseHotkey('ctrl+shift+space')),
  rebind_rejects_bare_modifier: api.parseHotkey('alt') === null ||
      api.parseHotkey('alt').mods.length === 0,
  bad_modifier_is_null: api.parseHotkey('meta+t') === null,
  listens_for_keydown: (listeners.keydown || []).length > 0,
  listens_for_keyup: (listeners.keyup || []).length > 0,
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    h = tmp_path_factory.mktemp("ptt") / "harness.js"
    h.write_text(HARNESS, encoding="utf-8")
    r = subprocess.run([node, str(h), str(SRC)], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_controller_installs_itself(probe):
    assert probe["installed"]
    assert probe["listens_for_keydown"] and probe["listens_for_keyup"], (
        "hold-to-talk needs both halves of the keystroke")


def test_the_bound_chord_fires(probe):
    assert probe["altT_matches_altT"]
    assert probe["rebind_ctrl_shift_space"], "rebinding must actually rebind"


def test_a_near_miss_does_not_fire(probe):
    """A chord with extra modifiers is a different chord.

    Without this, an Alt+T binding swallows Ctrl+Alt+T and Shift+Alt+T too,
    taking two more shortcuts away from whatever application is in front.
    """
    assert not probe["bare_t_does_not"], "plain T is typing, not dictation"
    assert not probe["ctrl_alt_t_does_not"]
    assert not probe["shift_alt_t_does_not"]
    assert not probe["other_letter_does_not"]


def test_an_unusable_hotkey_is_rejected_rather_than_approximated(probe):
    assert probe["bad_modifier_is_null"]


def test_it_is_served_and_referenced_by_both_uis():
    """A file nothing loads is not a feature."""
    assert SRC.exists()
    for path in (ROOT / "index.html", ROOT / "ui_parts" / "head.html"):
        assert "friday_push_to_transcribe.js" in path.read_text(encoding="utf-8"), (
            "%s does not load the in-page dictation handler" % path.name)


def test_the_audio_goes_only_to_the_local_route():
    src = SRC.read_text(encoding="utf-8")
    assert "/api/voice/transcribe" in src
    for host in ("https://", "http://", "googleapis", "openai"):
        assert host not in src, (
            "push-to-transcribe audio must not reach a remote host: %r" % host)
