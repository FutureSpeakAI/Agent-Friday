"""One dead turn is one notice.

The chat's liveness poller runs inside a setInterval whose callback is async.
setInterval does not wait for that callback, so when a poll takes longer than
the 15-second tick -- routine while the server is busy -- ticks overlap. The
staleness guard sat entirely BEFORE the await, so every overlapping tick had
already passed it by the time its answer arrived, and every one of them
released the chat and appended its own notice.

Measured on the reference machine, 2026-09-22. "Please start my day" ran
15:12:16 -> 15:18:19 on bonsai2:27b, streamed its full 3,043-character reply,
called search_email and open_url throughout, and finished normally. The turn
was never dead. Stephen saw about a dozen identical "that turn ended without
producing a reply" notices stacked in the thread.

The same overlap broke the four-miss rule: `misses` was incremented by
CONCURRENT polls, so four in-flight failures inside a single window counted as
four consecutive ones and tripped the hang message a dropped packet was never
supposed to trigger.

These tests run the real block out of index.html under node with a fake clock
and a fake fetch, rather than asserting on its text.
"""

import json
import shutil
import subprocess

import pytest

UI = "index.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                               reason="node is not installed")


def _block():
    """The liveness effect, located by content rather than by line number.

    An earlier UI check in this repo used a hard-coded line offset, drifted off
    the script it meant to inspect, and silently stopped checking anything.
    """
    src = open(UI, encoding="utf-8").read()
    # Anchor on the POLLER, not on the endpoint.
    #
    # `'/api/chat/turn/'` alone stopped identifying this effect the moment a
    # second caller of the same endpoint appeared earlier in the file
    # (`fridayResumeTurn`, which reads liveness before resuming a turn). The
    # locator then spanned from a useEffect thousands of lines above to this
    # effect's closing line, node refused to parse the result, and these four
    # tests failed as a harness error rather than as a finding -- the drift this
    # function's own comment warns about, in a new disguise.
    #
    # The poller is the only interval that awaits inside its own tick -- that
    # overlap is the whole subject of this file -- so its own construction is
    # the anchor, and the locator checks that it is unique rather than trusting
    # that it still is.
    NEEDLE = "const iv = setInterval(async () => {"
    assert src.count(NEEDLE) == 1, (
        "%d candidates for the liveness poller; the locator can no longer "
        "identify it" % src.count(NEEDLE))
    marker = src.index(NEEDLE)
    start = src.rindex("useEffect(() => {", 0, marker)
    end = src.index("}, [chatLoad]);", marker)
    block = src[start:end + len("}, [chatLoad]);")]
    assert "setInterval(async () =>" in block, "the poller was not located"
    assert block.count("useEffect(() => {") == 1, (
        "the located block spans more than one effect: %d found"
        % block.count("useEffect(() => {"))
    return block


HARNESS = """
'use strict';
let chatLoad = true;
let msgs = [];
let statuses = [];
const inFlightSince = { current: Date.now() - 200000 };  // well past 90s
const inFlightTurn = { current: 'turn-under-test' };
const setChatLoad = (v) => { chatLoad = v; };
const setTurnStatus = (s) => { statuses.push(s); };
const setChatMsgs = (fn) => { msgs = fn(msgs); };
const useEffect = (fn) => { fn(); };
let tick = null;
const setInterval = (fn, ms) => { tick = fn; return 1; };
const clearInterval = () => {};
const pending = [];
const fetch = (url) => new Promise((resolve) => pending.push(resolve));

__BLOCK__

(async () => {
  // Fire the tick many times over. Every one of them starts while the
  // previous answer is still in flight -- exactly what a slow server does.
  for (let i = 0; i < 12; i++) { tick(); await null; }
  // Now let every outstanding request answer at once.
  for (const resolve of pending) { resolve(__ANSWER__); }
  // Drain the microtask queue so each callback runs to completion.
  for (let i = 0; i < 200; i++) { await null; }
  console.log(JSON.stringify({
    polls: pending.length,
    stalled: msgs.filter((m) => m.kind === 'stalled').length,
    texts: msgs.filter((m) => m.kind === 'stalled').map((m) => m.text.slice(0, 40)),
    chatLoad: chatLoad,
  }));
})();
"""

GONE = ("{ ok: true, json: () => Promise.resolve("
        "{ state: 'gone', alive: false, hang: { stalled: false } }) }")
WORKING = ("{ ok: true, json: () => Promise.resolve("
           "{ state: 'working', alive: true, elapsed_s: 200, "
           "hang: { stalled: false } }) }")
FAILED = "{ ok: false, json: () => Promise.resolve({}) }"


def _run(tmp_path, answer):
    js = HARNESS.replace("__BLOCK__", _block()).replace("__ANSWER__", answer)
    f = tmp_path / "poller.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, timeout=60)
    out = r.stdout.decode("utf-8", "replace").strip()
    assert out, "harness produced nothing: " + r.stderr.decode("utf-8", "replace")
    return json.loads(out.splitlines()[-1])


def test_a_dead_turn_is_announced_exactly_once(tmp_path):
    """Twelve overlapping polls, one dead turn, one notice."""
    got = _run(tmp_path, GONE)
    assert got["stalled"] == 1, (
        "the poller appended %d notices for one dead turn (%r)"
        % (got["stalled"], got["texts"]))
    assert got["chatLoad"] is False, "a dead turn must still release the chat"


def test_only_one_poll_is_outstanding_at_a_time(tmp_path):
    """Overlapping polls are what let twelve verdicts be reached. They also
    hammer a server that is already struggling to answer."""
    got = _run(tmp_path, GONE)
    assert got["polls"] == 1, (
        "%d liveness requests were in flight at once" % got["polls"])


def test_a_working_turn_is_never_released(tmp_path):
    """The invariant the whole endpoint exists for."""
    got = _run(tmp_path, WORKING)
    assert got["stalled"] == 0
    assert got["chatLoad"] is True, "a live turn was released"


def test_concurrent_failures_do_not_satisfy_the_four_in_a_row_rule(tmp_path):
    """Four misses means four consecutive polls. Twelve simultaneous failures
    are one failure observed twelve times, and must not read as a hang."""
    got = _run(tmp_path, FAILED)
    assert got["stalled"] == 0, (
        "concurrent misses tripped the hang message: %r" % (got["texts"],))
