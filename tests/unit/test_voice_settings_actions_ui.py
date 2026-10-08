"""A voice-readiness action of kind 'settings' does something, and the local
voice models section is on the Voice tab, in index.html and its mirror.

The component source is cut out of each file and run under node with React
stubbed; no browser, no server.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILES = ("index.html", "ui_parts/app.html")
node = shutil.which("node")

HARNESS = r"""
const events = [], saved = [], fetched = [];
globalThis.window = {dispatchEvent: e => events.push({type: e.type, detail: e.detail})};
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } };
const React = {createElement: (t, p, ...c) => ({t, p: p || {}, c}), Fragment: 'F'};
const useState = i => [typeof i === 'function' ? i() : i, () => {}];
const useRef = () => ({current: null});
const useEffect = () => {};
const useCallback = f => f;
const StRow = 'StRow', VoiceStackRow = 'VoiceStackRow';
const fridayName = () => 'Friday';
const TTS_ENGINES_FALLBACK = [];
function apiFetch(u, o) { fetched.push([u, o && o.method]); return Promise.resolve({json: () => Promise.resolve({})}); }
SOURCE
function find(n, t) {
  if (!n || typeof n !== 'object') return null;
  if (Array.isArray(n)) { for (const x of n) { const r = find(x, t); if (r) return r; } return null; }
  if (n.t === t) return n;
  return find(n.c, t);
}
(async () => {
  const tree = VoiceStackCard({s: {voice_engine: 'local'}, save: o => { saved.push(o); return Promise.resolve(); }, mc: {}});
  const onProve = find(tree, VoiceStackRow).p.onProve;
  onProve({label: "Set GPU to 'if free'", kind: 'settings', set: {voice_mouth_gpu: 'if_free'}});
  onProve({label: 'Load the model', kind: 'settings', tab: 'intelligence'});
  onProve({label: 'Dead', kind: 'settings'});
  await new Promise(r => setTimeout(r, 20));
  console.log(JSON.stringify({saved, events, fetched}));
})();
"""


def _component(path, name):
    text = (ROOT / path).read_text(encoding="utf-8")
    m = re.search(r"^function %s\(.*?^}\n" % name, text, re.S | re.M)
    assert m, "%s is not defined in %s" % (name, path)
    return m.group(0)


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", FILES)
def test_a_settings_action_applies_its_setting_or_opens_its_tab(path):
    src = HARNESS.replace("SOURCE", _component(path, "VoiceStackCard"))
    out = subprocess.run([node, "-"], input=src, capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["saved"] == [{"voice_mouth_gpu": "if_free"}]
    assert got["events"] == [{"type": "friday:settings-tab",
                              "detail": {"tab": "intelligence"}}]
    # The saved setting is proved again; the action naming neither does nothing.
    assert got["fetched"] == [["/api/voice/prove", "POST"]]


@pytest.mark.parametrize("path", FILES)
def test_the_voice_tab_carries_the_local_voice_models_section(path):
    tab = _component(path, "SettingsTabVoice")
    assert "LocalVoiceModels" in tab and "Local voice models" in tab
    assert "VoiceStackCard" in tab and "VoiceModePicker" in tab
    for name in ("LocalVoiceModels", "VoiceStackCard", "VoiceModePicker", "VoiceStackRow"):
        _component(path, name)
    models = _component(path, "LocalVoiceModels")
    assert "consent: true" in models, "a download must carry the owner's confirmation"
    assert "'/api/voice/artifacts'" in models
