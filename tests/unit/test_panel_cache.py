"""The browser-side panel cache in index.html, run under node.

The block between the panel-cache markers wraps window.fetch. These tests
execute that exact block against a stubbed network and clock, and check
the promises it makes: a reopened panel is answered without a network wait,
a stale answer is still served but refreshed behind it, an action (any
non-GET) is never followed by a pre-action answer, and anything not on the
list passes straight through. The JSX mirror must carry the same block.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
START = "// ── Panel data cache (stale-while-revalidate)"
END = "// ── end panel data cache"

node = shutil.which("node")


def _block(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    a = text.index(START)
    b = text.index(END, a)
    return text[a:b]


def test_mirror_carries_the_same_block():
    assert _block("index.html") == _block("ui_parts/app.html")


HARNESS = r"""
let now = 1_000_000;
Date.now = () => now;
const calls = [];
let reply = (url) => ({status: 200, body: JSON.stringify({url, n: calls.length})});
let hold = null;   // when set, the next network read waits on this promise
globalThis.window = {
  location: {href: 'http://localhost:3000/', origin: 'http://localhost:3000'},
  fetch: (url, init) => {
    calls.push([(init && init.method) || 'GET', url]);
    const r = reply(url);
    const res = new Response(r.body, {status: r.status, headers: {'Content-Type': 'application/json'}});
    const h = hold; hold = null;
    return h ? h.then(() => res) : Promise.resolve(res);
  },
};
const apiFetch = (u, o) => window.fetch(u, o);
BLOCK
const f = (u, o) => window.fetch(u, o).then(r => r.json().then(j => ({status: r.status, j})));
const tick = () => new Promise(r => setTimeout(r, 0));
(async () => {
  const out = {};
  // 1. cold then warm: one network read, both answers readable
  await f('/api/repos/scan'); await f('/api/repos/scan');
  out.warm_calls = calls.filter(c => c[1] === '/api/repos/scan').length;
  // 2. concurrent cold callers share one read; each body is readable
  const both = await Promise.all([f('/api/system'), f('/api/system')]);
  out.concurrent_calls = calls.filter(c => c[1] === '/api/system').length;
  out.concurrent_bodies = both.map(b => b.j.url);
  // 3. a git action forgets the linked repo scan
  await window.fetch('/api/git/commit', {method: 'POST'});
  await f('/api/repos/scan');
  out.after_action_calls = calls.filter(c => c[1] === '/api/repos/scan').length;
  // 4. stale: served at once from cache, one background refresh
  now += 25_000;
  const stale = await f('/api/repos/scan');
  await tick(); await tick();
  out.stale_served_n = stale.j.n;
  out.stale_calls = calls.filter(c => c[1] === '/api/repos/scan').length;
  const refreshed = await f('/api/repos/scan');
  out.refreshed_n = refreshed.j.n;
  // 5. unlisted endpoints and query variants pass straight through
  await f('/api/tasks'); await f('/api/tasks');
  await f('/api/repos/scan?fresh=1');
  out.tasks_calls = calls.filter(c => c[1] === '/api/tasks').length;
  out.fresh_param_calls = calls.filter(c => c[1] === '/api/repos/scan?fresh=1').length;
  // 6. errors are not remembered
  reply = (url) => ({status: 500, body: '{"status":"error"}'});
  await f('/api/memory/stats'); await f('/api/memory/stats');
  out.error_calls = calls.filter(c => c[1] === '/api/memory/stats').length;
  reply = (url) => ({status: 200, body: JSON.stringify({url, n: calls.length})});
  // 7. past the max age the cache is not painted; the caller waits for the network
  await f('/api/countdowns');
  now += 31 * 60 * 1000;
  await f('/api/countdowns');
  out.max_age_calls = calls.filter(c => c[1] === '/api/countdowns').length;
  // 8. an action mid-refresh: the pre-action answer is not written back
  reply = (url) => ({status: 200, body: JSON.stringify({url, n: 'pre-action'})});
  await f('/api/trust');
  now += 61_000;
  let release;
  hold = new Promise(r => { release = r; });
  out.pre = (await f('/api/trust')).j.n;           // stale hit; its refresh is held open
  await window.fetch('/api/trust/score', {method: 'POST'});
  reply = (url) => ({status: 200, body: JSON.stringify({url, n: 'post-action'})});
  release();                                       // the pre-action refresh lands now
  await tick(); await tick(); await tick();
  out.after_action_trust = (await f('/api/trust')).j.n;
  out.stats = window.__fridayPanelCache.stats;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    if not node:
        pytest.skip("node is not on PATH")
    script = tmp_path_factory.mktemp("panel_cache") / "run.js"
    script.write_text(HARNESS.replace("BLOCK", _block("index.html")), encoding="utf-8")
    cp = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    return json.loads(cp.stdout.strip().splitlines()[-1])


def test_reopen_is_answered_from_cache(results):
    assert results["warm_calls"] == 1


def test_concurrent_cold_reads_share_one_request_and_both_parse(results):
    assert results["concurrent_calls"] == 1
    assert results["concurrent_bodies"] == ["/api/system", "/api/system"]


def test_an_action_is_never_followed_by_a_pre_action_answer(results):
    assert results["after_action_calls"] == 2
    assert results["after_action_trust"] == "post-action"


def test_stale_answer_is_served_and_refreshed_behind_it(results):
    assert results["stale_calls"] == results["after_action_calls"] + 1
    assert results["refreshed_n"] != results["stale_served_n"]


def test_unlisted_and_query_variants_pass_through(results):
    assert results["tasks_calls"] == 2
    assert results["fresh_param_calls"] == 1


def test_errors_are_not_cached(results):
    assert results["error_calls"] == 2


def test_nothing_older_than_max_age_is_painted(results):
    assert results["max_age_calls"] == 2
