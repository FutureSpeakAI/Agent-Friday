"""Gauntlet finding F28: MCPServerProcess._read_loop() and _drain_stderr()
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
discards (rather than tries to parse) a frame that hits the cap. Both
_read_loop (stdout) and _drain_stderr (stderr) have the identical
discard-and-resync shape.

CORRECTION (2026-09-04, caught by an independent cold re-verification of
this fix): the original version of this probe (a) pinned that readline()
was CALLED with a bounded size argument, which is a proxy for the real
property and can pass even if a caller ignored what readline() actually
returned; and (b) only covered _read_loop (stdout) -- _drain_stderr, the
second function this same finding names, had no test at all. Rewritten
below to assert directly on the length of every string _read_loop/
_drain_stderr actually hold in memory at once (the real property: bounded
memory, not bounded call arguments), and to cover both functions.
"""
from __future__ import annotations

from agent_friday.mcp_client import MCPServerProcess

# Deliberately NOT imported from mcp_client -- if the fix is reverted, the
# module-level cap this test pins doesn't exist at all, and importing it
# would turn the no-op-revert check into a collection ERROR instead of a
# clean test FAILURE. A generous, self-contained expectation (any bound at
# or under 64 MiB counts as "genuinely bounded") is what we actually want
# to prove regardless of the exact constant the fix picks.
_MAX_LINE_CHARS = 64 * 1024 * 1024


class _TrackingStream:
    """Simulates a pipe that never emits a newline -- exactly the DoS shape
    this fix defends against -- and records the length of every string it
    hands back, so the test can assert on actual memory held, not on what
    argument the caller passed.

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
        self.returned_lengths: list[int] = []

    def readline(self, size=-1):
        if self._remaining <= 0:
            out = ""
        else:
            n = self._remaining if size is None or size < 0 else min(size, self._remaining)
            self._remaining -= n
            out = "x" * n  # never contains "\n"
        self.returned_lengths.append(len(out))
        return out

    def __iter__(self):
        while True:
            chunk = self.readline()
            if not chunk:
                return
            yield chunk


def _make_process(total_stdout_chars: int = 0, total_stderr_chars: int = 0) -> MCPServerProcess:
    sp = MCPServerProcess(name="test-server", command="node", args=["server.js"])

    class _FakeProc:
        stdout = _TrackingStream(total_stdout_chars)
        stderr = _TrackingStream(total_stderr_chars)

    sp.proc = _FakeProc()
    return sp


class TestMcpStdioLineSizeBounded:
    def test_read_loop_never_holds_more_than_the_cap_in_memory_at_once(self):
        """The real property: no single string _read_loop() receives from
        readline() may ever exceed the cap, for a stdout stream that emits
        far more than the cap's worth of data with no newline anywhere.
        Kept just over the cap (not a large multiple) so the red-state
        allocation this test performs when run against reverted code
        (which would return everything in one uncapped call) stays modest."""
        huge = int(_MAX_LINE_CHARS * 1.2)
        sp = _make_process(total_stdout_chars=huge)

        sp._read_loop()

        lengths = sp.proc.stdout.returned_lengths
        assert lengths, "_read_loop() never called readline() at all"
        assert all(n <= _MAX_LINE_CHARS for n in lengths), (
            "_read_loop() received a string longer than _MAX_LINE_CHARS from "
            "readline() -- a server that never emits a newline can still "
            "make it materialize an unboundedly large string in memory, "
            "defeating the fix (this is the actual vulnerability; merely "
            "checking what size argument was passed to readline() is not "
            "enough, since a caller could pass a bound and then still "
            "concatenate/hold the unbounded result)"
        )

    def test_read_loop_terminates_on_a_newline_free_flood_without_hanging(self):
        """No-op-shaped sanity/liveness check: the loop must actually
        finish (reach EOF) rather than loop forever re-reading the same
        oversized frame -- proves the discard-and-resync logic advances
        past a too-long frame instead of getting stuck on it."""
        sp = _make_process(total_stdout_chars=int(_MAX_LINE_CHARS * 1.5))
        sp._read_loop()  # must return; a hang here fails the test via timeout

    def test_drain_stderr_never_holds_more_than_the_cap_in_memory_at_once(self):
        """Same property as the stdout test above, for _drain_stderr() --
        the finding names both functions (they share the identical
        discard-and-resync shape), but only stdout had a probe before this
        correction."""
        huge = int(_MAX_LINE_CHARS * 1.2)
        sp = _make_process(total_stderr_chars=huge)

        sp._drain_stderr()

        lengths = sp.proc.stderr.returned_lengths
        assert lengths, "_drain_stderr() never called readline() at all"
        assert all(n <= _MAX_LINE_CHARS for n in lengths), (
            "_drain_stderr() received a string longer than _MAX_LINE_CHARS "
            "from readline() -- an MCP server that floods stderr with a "
            "single newline-free frame can still make this loop materialize "
            "an unboundedly large string, even though _stderr_tail itself "
            "(a bounded deque) looks safe from the outside"
        )

    def test_drain_stderr_terminates_on_a_newline_free_flood_without_hanging(self):
        """No-op-shaped sanity/liveness check, mirroring the stdout test."""
        sp = _make_process(total_stderr_chars=int(_MAX_LINE_CHARS * 1.5))
        sp._drain_stderr()  # must return; a hang here fails the test via timeout
