"""Keys for the top bar's model pill (static/friday_model_shortcut.js), run under node with a small
stand-in page: no browser.

Stephen, 2026-11-13: "model switching ... fast and easy". The pill and its Quick Switch list exist
(HIG: the model is one of the top bar's eight controls); the keys are what was missing. Guarded:

* Ctrl+Alt+M opens the list with the current model focused, and closes it again; no other chord does;
* while it is open, 1 to 9 pick that line of Quick Switch (the pill's own button is clicked, so the pick
  is the pill's own save), arrows move, Esc closes and returns focus to the pill;
* nothing fires while the list is closed, in a text field, or with a modifier held;
* the page carries the script and lists the key on its shortcuts sheet and in the user guide.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "static" / "friday_model_shortcut.js"
node = shutil.which("node")

HARNESS = r"""
const api = require(process.argv[2]);
const log = [];
const btn = (title, name) => ({ tagName: 'BUTTON', className: 'model-option-cloud', title, textContent: name,
  click() { log.push('pick:' + name); }, focus() { doc.activeElement = this; log.push('focus:' + name); } });
const tier = t => ({ tagName: 'DIV', className: 'model-selector-tier', textContent: t });
let open = false;
const quick = [btn('Current orchestrator', 'Sonnet'), btn('Switch orchestrator to Bonsai', 'Bonsai'), btn('Switch orchestrator to Opus', 'Opus')];
const other = btn('Switch orchestrator to Elsewhere', 'Elsewhere');
const menu = { children: [tier('Quick Switch'), ...quick, tier('Local Models'), other] };
const pill = { click() { open = !open; log.push('pill'); }, focus() { doc.activeElement = pill; log.push('focus:pill'); } };
const listeners = {};
const doc = { activeElement: null, addEventListener(t, f) { listeners[t] = f; },
  querySelector(sel) { return sel.includes('Model selection') ? pill : (open ? menu : null); } };
globalThis.setTimeout = f => f();
api.wire(doc);
const key = (k, extra) => { const e = Object.assign({ key: k, preventDefault() { log.push('prevent'); }, target: { tagName: 'DIV' } }, extra || {}); listeners.keydown(e); };
const out = {};
out.pure = { open: api.isOpenKey({ ctrlKey: true, altKey: true, key: 'm' }), shift: api.isOpenKey({ ctrlKey: true, altKey: true, shiftKey: true, key: 'm' }),
  noAlt: api.isOpenKey({ ctrlKey: true, key: 'm' }), meta: api.isOpenKey({ ctrlKey: true, altKey: true, metaKey: true, key: 'm' }),
  digit: api.digitOf({ key: '3' }), digitMod: api.digitOf({ key: '3', ctrlKey: true }), zero: api.digitOf({ key: '0' }),
  quick: api.quickButtons(menu).map(b => b.textContent), current: api.currentOf(api.quickButtons(menu)).textContent };
key('2'); key('ArrowDown'); key('Escape'); out.closedIgnored = log.splice(0);          // list closed: nothing happens
key('m', { ctrlKey: true, altKey: true }); out.opened = log.splice(0); out.isOpen = open;
key('2'); out.pickedSecond = log.splice(0);
key('9'); out.pickedMissing = log.splice(0);
doc.activeElement = quick[0]; key('ArrowDown'); out.down = log.splice(0);
doc.activeElement = quick[0]; key('ArrowUp'); out.upWraps = log.splice(0);
key('1', { target: { tagName: 'INPUT' } }); out.inField = log.splice(0);
key('1', { ctrlKey: true }); out.withModifier = log.splice(0);
key('Escape'); out.escaped = log.splice(0); out.afterEsc = open;
open = true; key('M', { ctrlKey: true, altKey: true }); out.toggledClosed = log.splice(0); out.afterToggle = open;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def r():
    if not node:
        pytest.skip("node is not installed")
    run = subprocess.run([node, "-", str(SCRIPT)], input=HARNESS, text=True, capture_output=True,
                         encoding="utf-8", timeout=30)
    assert run.returncode == 0, run.stderr[-2000:]
    return json.loads(run.stdout)


def test_only_ctrl_alt_m_opens_it_and_one_to_nine_are_the_lines(r):
    p = r["pure"]
    assert p["open"] is True and p["shift"] is False and p["noAlt"] is False and p["meta"] is False
    assert p["digit"] == 3 and p["digitMod"] == 0 and p["zero"] == 0


def test_the_lines_are_the_quick_switch_list_and_the_current_model_comes_first(r):
    assert r["pure"]["quick"] == ["Sonnet", "Bonsai", "Opus"]            # not the next section's "Elsewhere"
    assert r["pure"]["current"] == "Sonnet"


def test_nothing_happens_while_the_list_is_closed(r):
    assert r["closedIgnored"] == []


def test_the_chord_opens_the_pill_and_focuses_the_current_model(r):
    assert r["isOpen"] is True and r["opened"] == ["prevent", "pill", "focus:Sonnet"]


def test_a_digit_clicks_that_line_and_a_missing_line_does_nothing(r):
    assert r["pickedSecond"] == ["prevent", "pick:Bonsai"] and r["pickedMissing"] == []


def test_arrows_move_and_wrap(r):
    assert r["down"] == ["prevent", "focus:Bonsai"] and r["upWraps"] == ["prevent", "focus:Opus"]


def test_a_text_field_or_a_modifier_never_picks(r):
    assert r["inField"] == [] and r["withModifier"] == []


def test_escape_closes_and_returns_focus_to_the_pill_and_the_chord_toggles(r):
    assert r["escaped"] == ["prevent", "pill", "focus:pill"] and r["afterEsc"] is False
    assert r["toggledClosed"] == ["prevent", "pill", "focus:pill"] and r["afterToggle"] is False


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/head.html"])
def test_the_page_carries_the_script(rel):
    assert '<script src="/static/friday_model_shortcut.js" defer></script>' in (ROOT / rel).read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_shortcuts_sheet_lists_the_key(rel):
    assert re.search(r"keys: \['Ctrl\+Alt\+M'\], what: 'Switch the model", (ROOT / rel).read_text(encoding="utf-8-sig"))


def test_the_user_guide_lists_the_key():
    assert "**Ctrl+Alt+M**" in (ROOT / "docs/user-guide/keyboard-shortcuts.md").read_text(encoding="utf-8")
