"""The Studio 3D file browser's browser-side pieces, run under node, and its
wiring into both UI files.

The thumbnail slot pool is the part a bug in hides well: tiles look fine
until two items share a slot and one card shows another file's picture.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "static" / "studio_files3d.js"
node = shutil.which("node")

HARNESS = r"""
globalThis.window = {};
globalThis.React = { createElement() {}, useState() {}, useEffect() {}, useRef() {}, useCallback() {} };
SCRIPT
const { createSlotPool, buildItems } = window.__files3dInternals;
const out = {};

// Several tiles landing in one frame each get their own slot.
{
  const p = createSlotPool(4, 10), wanted = new Uint8Array(10).fill(1);
  const got = [0, 1, 2, 3].map(i => p.claim(i, wanted, 1).slot);
  out.batch_slots = got;
  out.batch_consistent = [0, 1, 2, 3].every(i => p.itemIn[p.slotOf[i]] === i);
  // Full, and everyone still wanted: nothing is evicted.
  out.full_claim = p.claim(4, wanted, 2).slot;
  out.full_owners = Array.from(p.itemIn);
}
// Only unwanted items are evicted, least recently wanted first.
{
  const p = createSlotPool(3, 10), wanted = new Uint8Array(10).fill(1);
  p.claim(0, wanted, 1); p.claim(1, wanted, 2); p.claim(2, wanted, 3);
  wanted[0] = 0; wanted[2] = 0; p.touch(0, 10);
  const r = p.claim(5, wanted, 11);
  out.evicted = r.evicted;
  out.evicted_slot_cleared = p.slotOf[2];
  out.claim_again = p.claim(5, wanted, 12);
  out.used = p.used();
}
// A refresh hands a surviving file its old slot; a taken slot is refused.
{
  const p = createSlotPool(4, 10), wanted = new Uint8Array(10).fill(1);
  out.adopt_ok = p.adopt(3, 2, 1);
  out.adopt_taken = p.adopt(4, 2, 1);
  out.adopt_twice = p.adopt(3, 1, 1);
  out.adopt_next_claim = p.claim(5, wanted, 2).slot;
  out.adopt_owner = p.itemIn[2];
}
// The item table links folders to their files and skips the scanned folder itself.
{
  const items = buildItems({ path: 'base', entries: [
    ['base/a', 1, 30, 1], ['base/a/x.png', 0, 10, 2], ['base/a/b', 1, 20, 3],
    ['base/a/b/y.py', 0, 20, 4], ['base/z.mp4', 0, 5, 5]] });
  out.items = items.map(it => [it.name, it.parent, it.depth, it.cat, it.kids]);
}
console.log(JSON.stringify(out));
"""


def _run():
    src = HARNESS.replace("SCRIPT", SCRIPT.read_text(encoding="utf-8"))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
def test_slot_pool_gives_each_tile_its_own_slot():
    out = _run()
    assert sorted(out["batch_slots"]) == [0, 1, 2, 3]
    assert out["batch_consistent"] is True
    assert out["full_claim"] == -1
    assert out["full_owners"] == [0, 1, 2, 3]


@pytest.mark.skipif(not node, reason="node is not installed")
def test_slot_pool_evicts_only_unwanted_least_recent():
    out = _run()
    assert out["evicted"] == 2
    assert out["evicted_slot_cleared"] == -1
    assert out["claim_again"]["evicted"] == -1
    assert out["used"] == 3


@pytest.mark.skipif(not node, reason="node is not installed")
def test_slot_pool_adopts_a_kept_tile_only_into_a_free_slot():
    out = _run()
    assert out["adopt_ok"] is True and out["adopt_owner"] == 3
    assert out["adopt_taken"] is False and out["adopt_twice"] is False
    assert out["adopt_next_claim"] != 2


@pytest.mark.skipif(not node, reason="node is not installed")
def test_item_table_links_folders():
    items = _run()["items"]
    assert items == [
        ["a", -1, 1, "folder", [1, 2]],
        ["x.png", 0, 2, "image", []],
        ["b", 0, 2, "folder", [3]],
        ["y.py", 2, 3, "code", []],
        ["z.mp4", -1, 1, "video", []],
    ]


@pytest.mark.parametrize("path", ["index.html", "ui_parts/app.html"])
def test_studio_offers_the_3d_browser(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "setView('files')" in text
    assert "window.Files3DPanel" in text
    assert "view !== 'files'" in text or "view!=='files'" in text


@pytest.mark.parametrize("path", ["index.html", "ui_parts/styles_and_scene.html"])
def test_page_loads_the_browser_and_honours_the_backdrop_hold(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert '<script src="/static/studio_files3d.js"></script>' in text
    # The GUARD is the invariant: a large foreground 3D view holds the
    # backdrop's drawing because both share one GPU. What sits inside it is
    # not — the condensed widget added a second, cheaper draw path in there,
    # and pinning the whole line to one formatting made that read as a
    # regression when the hold was never weakened.
    assert "if (composer && !(window.__fridayBackdropHold > 0))" in text
    assert "composer.render()" in text


@pytest.mark.parametrize("path", ["index.html", "ui_parts/app.html"])
def test_settings_offers_the_dazzle_slider(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "3D dazzle" in text
    assert "studio_dazzle" in text and "friday-dazzle" in text


def test_dazzle_defaults_to_full():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["studio_dazzle"] == "full"


RECORDS = ROOT / "static" / "friday3d_records.js"


@pytest.mark.skipif(not node, reason="node is not installed")
def test_every_workspace_source_maps_a_record_to_a_card():
    src = r"""
globalThis.window = {};
globalThis.document = undefined;
globalThis.React = { createElement() {}, useState() {}, useEffect() {}, useRef() {}, useCallback() {}, Fragment: 'f' };
ENGINE
RECORDS
const S = window.__friday3dSources, out = {};
const samples = {
  models: { id: 'm', label: 'Model', provider: 'p', provider_label: 'P', local: true, available: true, context_window: 128000, modalities: ['text'] },
  news: { id: 'n', title: 'Headline', snippet: 's', source: 'x.test', category: 'Science', published_at: '2026-09-20T10:00:00Z', relevance_score: 3 },
  people: { name: 'Ada Example', overall: 0.8, evidence_count: 2, aliases: ['Ada'], domains: ['work'], last_interaction: '2026-09-01T10:00:00' },
  tasks: { kind: 'todo', id: '1', title: 'Task', priority: 'high', status: 'approved', created: '2026-09-20T10:00:00' },
  calendar: { id: 'e', title: 'Standup', start_time: '2026-09-22T09:00', end_time: '2026-09-22T09:30', day: '2026-09-22' },
  messages: { id: 'm1', thread_id: 't1', subject: 'Hi', sender: 'Ada', unread: true, lane: 'career', timestamp: 'Wed, 16 Sep 2026 10:00:00 +0000' }
};
for (const k of Object.keys(samples)) {
  const s = S[k], it = s.toItem(samples[k]);
  const other = Object.assign({}, samples[k], { id: 'z', title: 'Zed', name: 'Zed Other', label: 'Zed' });
  out[k] = { title: it.title, time: it.time > 0 || k === 'models', groups: Object.keys(s.groupings).map(g => s.groupings[g].key(samples[k])),
             views: s.views, detail: s.detail(samples[k]).length > 0, intro: s.intro,
             sorts: Object.keys(s.sorts || {}).map(q => typeof s.sorts[q].cmp(samples[k], other)),
             filters: (s.filters || []).map(f => typeof f.test(samples[k])),
             zones: (s.zones || []).length, undoable: !s.zones || (typeof s.act === 'function' && typeof s.undo === 'function') };
}
console.log(JSON.stringify(out));
"""
    src = src.replace("ENGINE", SCRIPT.read_text(encoding="utf-8")).replace("RECORDS", RECORDS.read_text(encoding="utf-8"))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert set(out) == {"models", "news", "people", "tasks", "calendar", "messages"}
    for k, v in out.items():
        assert v["title"] and v["time"] and v["detail"], k
        assert all(isinstance(g, str) and g for g in v["groups"]), k
    assert out["people"]["groups"][0] == "Inner circle"
    assert out["tasks"]["groups"][0] == "High priority"
    assert out["calendar"]["views"][0] == "week" and out["messages"]["views"][0] == "stack"
    # each workspace arrives its own way and brings its own sorts and filters
    intros = {k: v["intro"] for k, v in out.items()}
    assert intros == {"models": "center", "news": "rain", "people": "spiral", "tasks": "rise", "calendar": "sweep", "messages": "deal"}
    for k, v in out.items():
        assert v["sorts"] and all(t == "number" for t in v["sorts"]), k
        assert all(t == "boolean" for t in v["filters"]), k
        assert v["undoable"], k          # anything that can act on a card can undo it
    assert out["messages"]["zones"] >= 5 and out["tasks"]["zones"] == 1 and out["news"]["zones"] == 1
    assert out["people"]["zones"] == 0 and out["calendar"]["zones"] == 0 and out["models"]["zones"] == 0


@pytest.mark.parametrize("path", ["index.html", "ui_parts/app.html"])
def test_workspaces_get_the_3d_bar(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    # ("home", "tasks") was here until 2026-09-24. The Home workspace is gone --
    # the desktop hero is the landing screen -- so there is no window for it to
    # decorate. Every remaining pairing is unchanged.
    for ws, src in [("trust", "people"), ("code", "code"), ("news", "news"),
                    ("contacts", "people"), ("messages", "messages"), ("calendar", "calendar")]:
        assert f"with3D('{ws}', '{src}'" in text or f"with3D('{ws}','{src}'" in text, ws
    assert "with3DModels(" in text
    assert "useNavTarget('contacts'" in text


@pytest.mark.parametrize("path", ["index.html", "ui_parts/app.html"])
def test_windows_fold_away_instead_of_vanishing(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "closingWins" in text and "cancelClose" in text
    assert "'fwin' + (closing ? ' closing' : '')" in text or "'fwin'+(closing?' closing':'')" in text


@pytest.mark.parametrize("path", ["index.html", "ui_parts/head.html"])
def test_window_animation_css_respects_reduced_motion_and_dazzle_off(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "@keyframes fwinMaterialize" in text and "@keyframes fwinFold" in text
    assert ':root[data-dazzle="off"] .fwin' in text
    assert "prefers-reduced-motion: reduce" in text and "fwinFade" in text


@pytest.mark.skipif(not node, reason="node is not installed")
def test_stacks_never_leave_a_pile_alone_on_a_row():
    src = HARNESS.split("const { createSlotPool")[0].replace("SCRIPT", SCRIPT.read_text(encoding="utf-8")) + r"""
const { stackSlots } = window.__files3dInternals;
const out = {};
for (let n = 1; n <= 24; n++) {
  const s = stackSlots(n), rows = {};
  s.forEach(p => { (rows[p.row] = rows[p.row] || []).push(p); });
  const sizes = Object.values(rows).map(r => r.length);
  out[n] = { rows: sizes, centred: Object.values(rows).every(r => Math.abs(r.reduce((a, p) => a + p.x, 0) / r.length) < 1e-9),
             span: Math.max(...s.map(p => Math.abs(p.yaw))) * 2 * 180 / Math.PI };
}
console.log(JSON.stringify(out));
"""
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for n in range(1, 8):                       # 1-7 piles: one arc, no wrap
        assert out[str(n)]["rows"] == [n], n
        assert out[str(n)]["span"] <= 101, n
    for n in range(8, 25):                      # more: balanced, centred rows
        rows = out[str(n)]["rows"]
        assert sum(rows) == n and max(rows) - min(rows) <= 1 and max(rows) <= 7, (n, rows)
    assert all(v["centred"] for v in out.values())
