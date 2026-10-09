"""The browser-side poll guard in index.html, run under node.

Several components poll /api/processes, /api/tasks and /api/meetings/status on
timers of their own, and each timer starts the next request whether or not the
last one came back. Against a server that answers slowly those requests pile
up, and each is work the server does for a page that has stopped waiting.

The block between the poll-guard markers is executed against a stubbed network
and clock. It promises: one request per path at a time (callers that arrive
meanwhile share the answer, each with a body of its own to read); a path rests
after a slow answer for as long as that answer took, serving the previous
answer marked stale for a while and failing after that; and everything that is
not one of the three paths, or is not a plain GET, passes straight through.
The JSX mirror must carry the same block.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
START = "// ── Poll guard"
END = "// ── end poll guard"

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
let hold = null;      // when set, the next network read waits on this promise
let took = 0;         // how long the next network read "takes", on the stub clock
globalThis.window = {
  location: {href: 'http://localhost:3000/', origin: 'http://localhost:3000'},
  fetch: (url, init) => {
    calls.push([(init && init.method) || 'GET', url]);
    const r = reply(url);
    const res = new Response(r.body, {status: r.status, headers: {'Content-Type': 'application/json'}});
    const h = hold; hold = null;
    now += took; took = 0;
    return h ? h.then(() => res) : Promise.resolve(res);
  },
};
BLOCK
const n = (u) => calls.filter(c => c[1] === u).length;
const f = (u, o) => window.fetch(u, o).then(r => r.json().then(j => ({status: r.status, j, stale: r.headers.get('X-Friday-Stale')})));
const tick = () => new Promise(r => setTimeout(r, 0));
(async () => {
  const out = {};
  // 1. five overlapping polls of one path are one request, and all five parse
  let release;
  hold = new Promise(r => { release = r; });
  const five = [1, 2, 3, 4, 5].map(() => f('/api/processes'));
  await tick(); release();
  const got = await Promise.all(five);
  out.overlap_calls = n('/api/processes');
  out.overlap_bodies = got.map(g => g.j.url);
  // 2. sequential polls are each a request
  await f('/api/tasks'); await f('/api/tasks');
  out.sequential_calls = n('/api/tasks');
  // 3. a slow answer makes the path rest; the previous answer is served, marked
  took = 4000;
  await f('/api/meetings/status');
  const during = await f('/api/meetings/status');
  out.rest_calls = n('/api/meetings/status');
  out.rest_stale = during.stale;
  // 4. the rest ends after as long as the slow answer took
  now += 4001;
  const after = await f('/api/meetings/status');
  out.after_rest_calls = n('/api/meetings/status');
  out.after_rest_stale = after.stale;
  // 5. a rest never outlives the stale answer: past 20 s the poll fails, as a dead server would
  took = 30000;
  await f('/api/tasks');
  now += 21_000;
  out.too_old = await window.fetch('/api/tasks').then(() => 'answered', e => e.name);
  now += 30_000;
  out.rest_ends = (await f('/api/tasks')).stale;
  // 6. other paths, query strings, non-GETs and aborted callers pass straight through
  await f('/api/tasks/abc'); await f('/api/tasks/abc');
  await f('/api/processes?x=1'); await f('/api/processes?x=1');
  await window.fetch('/api/processes', {method: 'POST'});
  await window.fetch('/api/processes', {signal: {}});
  out.through = [n('/api/tasks/abc'), n('/api/processes?x=1')];
  out.post_and_signal_reached_network = calls.filter(c => c[0] === 'POST').length === 1;
  // 7. an error is passed on and does not start a rest
  reply = () => ({status: 503, body: '{"status":"busy"}'});
  now += 60_000;
  const bad = await f('/api/meetings/status');
  reply = (url) => ({status: 200, body: JSON.stringify({url})});
  const ok = await f('/api/meetings/status');
  out.error_then_ok = [bad.status, ok.status, ok.stale];
  out.stats = window.__fridayPollGuard.stats;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    if not node:
        pytest.skip("node is not on PATH")
    script = tmp_path_factory.mktemp("poll_guard") / "run.js"
    script.write_text(HARNESS.replace("BLOCK", _block("index.html")), encoding="utf-8")
    cp = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    return json.loads(cp.stdout.strip().splitlines()[-1])


def test_overlapping_polls_are_one_request_and_every_caller_can_read_it(results):
    assert results["overlap_calls"] == 1
    assert results["overlap_bodies"] == ["/api/processes"] * 5


def test_sequential_polls_are_not_held_back(results):
    assert results["sequential_calls"] == 2


def test_a_slow_answer_rests_the_path_and_the_stale_answer_says_so(results):
    assert results["rest_calls"] == 1
    assert results["rest_stale"] == "1"
    assert results["after_rest_calls"] == 2
    assert results["after_rest_stale"] is None


def test_a_rest_never_serves_an_answer_older_than_the_limit(results):
    assert results["too_old"] == "TypeError"
    assert results["rest_ends"] is None


def test_everything_else_passes_straight_through(results):
    assert results["through"] == [2, 2]
    assert results["post_and_signal_reached_network"] is True


def test_an_error_is_passed_on_and_does_not_start_a_rest(results):
    assert results["error_then_ok"] == [503, 200, None]
