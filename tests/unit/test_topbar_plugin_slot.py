"""A documented top-bar plugin slot (field install report, Part 2 #12).

Add-ons and local overrides had nowhere to put a control in the top bar
except by patching index.html, which every update replaces. The registry
(static/js/friday_topbar_slot.js) is the contract; FridayTopBarPlugins in the
page renders what is registered, each control inside its own Safe boundary.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SLOT = ROOT / "static" / "js" / "friday_topbar_slot.js"
node = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const events = [];
global.window = { CustomEvent: class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } },
                  dispatchEvent: e => { events.push(e.type); return true; } };
new Function('window', fs.readFileSync(process.argv[2], 'utf8'))(global.window);
const s = global.window.fridayTopBarSlot;
const out = { installed: !!s, empty: s.size() };
const A = () => null, B = () => null;
const off = s.register('my-addon', A);
s.register('other', B);
s.register('my-addon', B);  // same id replaces
out.ids = s.list().map(i => i.id);
out.replaced = s.list().find(i => i.id === 'my-addon').component === B;
out.events = events.length;
out.bad_id = (() => { try { s.register('Bad Id!', A); return false; } catch (e) { return true; } })();
out.bad_component = (() => { try { s.register('ok', 'not a component'); return false; } catch (e) { return true; } })();
off();
out.after_off = s.list().map(i => i.id);
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not node, reason="node is not installed")
def test_the_registry_registers_replaces_and_unregisters(tmp_path):
    assert SLOT.is_file(), "the top-bar slot registry is missing"
    h = tmp_path / "h.js"
    h.write_text(HARNESS, encoding="utf-8")
    r = subprocess.run([node, str(h), str(SLOT)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["installed"] and out["empty"] == 0
    assert out["ids"] == ["other", "my-addon"]
    assert out["replaced"] is True
    assert out["events"] == 3
    assert out["bad_id"] is True and out["bad_component"] is True
    assert out["after_off"] == ["other"]


def test_the_top_bar_renders_the_slot_and_both_heads_load_the_registry():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    head = (ROOT / "ui_parts" / "head.html").read_text(encoding="utf-8")
    tag = '<script src="/static/js/friday_topbar_slot.js"></script>'
    assert tag in index and tag in head
    assert "function FridayTopBarPlugins()" in index and "function FridayTopBarPlugins()" in app
    assert 'id: "friday-topbar-slot"' in index and 'id="friday-topbar-slot"' in app
    bar = index[index.index("const shellTopBar"):index.index("const shellKillSwitch")]
    assert "React.createElement(FridayTopBarPlugins)" in bar
    assert "<FridayTopBarPlugins/>" in app
    # Each add-on is contained: one broken control cannot unmount the desktop.
    comp = index[index.index("function FridayTopBarPlugins()"):]
    comp = comp[:comp.index("\n}\n")]
    assert "React.createElement(Safe," in comp
