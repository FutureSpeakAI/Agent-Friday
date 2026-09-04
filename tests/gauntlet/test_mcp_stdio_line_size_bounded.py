"""Gauntlet finding: MCPServerProcess._read_loop() and _drain_stderr()
(src/agent_friday/mcp_client.py) iterated their subprocess's stdout/stderr
with a plain `for raw in proc.stdout:` -- Python's line iteration has no
per-line size cap, so a malicious or simply buggy MCP server that never
emits a newline (or sends one enormous JSON-RPC line) gets read into a
single, unboundedly large Python string before anything else runs. The
existing `deque(maxlen=40)` on `_stderr_tail` only bounds how many
*completed* lines are retained -- it does nothing for the single line
currently being assembled.

This is independent of and not mitigated by extension_security.
gate_mcp_config()'s destructive-command scan, which checks what the
launch COMMAND is, not what the running process later WRITES.

The fix bounds each read via `readline(_MAX_LINE_CHARS)`, which never
returns more than that many characters even with no newline in sight, and
discards (rather than tries to parse) a frame that hits the cap.
"""
from __future__ import annotations

import io

from agent_friday.mcp_client import MCPServerProcess

# Deliberately NOT imported from mcp_client -- if the fix is reverted, the
# module-level cap this test pins doesn't exist at all, and importing it
# would turn the no-op-revert check into a collection ERROR instead of a
# clean test FAILURE. A generous, self-contained expectation (any bound at
# or under 64 MiB counts as "genuinely bounded") is what we actually want
# to prove regardless of the exact constant the fix picks.
_MAX_LINE_CHARS = 64 * 1024 * 1024


class _FakeStdout:
    """Simulates a pipe that never emits a newline -- exactly the DoS shape
    this fix defends against.

    `readline(size)` mirrors a real TextIOWrapper precisely: with an
    explicit, non-negative `size` it returns at most that many characters.
    With NO size argument (or a negative one) it returns the ENTIRE
    remaining buffer in one call, unbounded -- exactly what a real file
    object does when a caller reads until a newline or EOF with no cap.
    `__iter__` calls `readline()` with no argument, matching a real
    TextIOWrapper's `for raw in stream:` protocol -- so this fake correctly
    reproduces the vulnerability for code that still says
    `for raw in proc.stdout:`, and correctly bounds it for code that calls
    `proc.stdout.readline(_MAX_LINE_CHARS)` explicitly.
    """

    def __init__(self, total_chars: int):
        self._remaining = total_chars

    def readline(self, size=-1):
        if self._remaining <= 0:
            return ""
        n = self._remaining if size is None or size < 0 else min(size, self._remaining)
        self._remaining -= n
        return "x" * n  # never contains "\n"

    def __iter__(self):
        while True:
            chunk = self.readline()
            if not chunk:
                return
            yield chunk


def _make_process_with_fake_stdout(total_chars: int) -> MCPServerProcess:
    sp = MCPServerProcess(name="test-server", command="node", args=["server.js"])

    class _FakeProc:
        stdout = _FakeStdout(total_chars)
        stderr = None

    sp.proc = _FakeProc()
    return sp


class TestMcpStdioLineSizeBounded:
    def test_read_loop_never_materializes_more_than_the_cap_per_readline_call(self):
        """A server that emits more than _MAX_LINE_CHARS worth of data with
        no newline anywhere must be handled via bounded readline() calls,
        not a single unbounded read -- proven by checking the fake
        stream's readline is invoked with a bounded size, not called via
        bare iteration (`for raw in proc.stdout`), which passes no size
        at all. Kept just over the cap (not a large multiple) so the
        red-state allocation this test performs when run against reverted
        code stays modest."""
        huge = int(_MAX_LINE_CHARS * 1.2)
        sp = _make_process_with_fake_stdout(huge)
        sizes_requested = []
        orig_readline = sp.proc.stdout.readline

        def _tracking_readline(size=-1):
            sizes_requested.append(size)
            return orig_readline(size)
        sp.proc.stdout.readline = _tracking_readline

        sp._read_loop()

        assert sizes_requested, (
            "_read_loop() never called readline() at all -- it must read "
            "via a size-bounded readline(), not bare iteration"
        )
        assert all(s not in (-1, None) and s <= _MAX_LINE_CHARS
                   for s in sizes_requested), (
            "_read_loop() called readline() with no size bound (or a bound "
            "larger than _MAX_LINE_CHARS) -- a server that never emits a "
            "newline can still make it materialize an unboundedly large "
            "string in one call, defeating the fix"
        )

    def test_read_loop_terminates_on_a_newline_free_flood_without_hanging(self):
        """No-op-shaped sanity/liveness check: the loop must actually
        finish (reach EOF) rather than loop forever re-reading the same
        oversized frame -- proves the discard-and-resync logic advances
        past a too-long frame instead of getting stuck on it."""
        sp = _make_process_with_fake_stdout(int(_MAX_LINE_CHARS * 1.5))
        sp._read_loop()  # must return; a hang here fails the test via timeout
